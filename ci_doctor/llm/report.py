"""Produce the Report: one LLM call plus one repair retry, or no call at all.

A deterministic report stands in whenever the LLM is disabled, unconfigured or
unreachable.

The deterministic report is a first-class output (`llm.enabled: false`) *and*
the degraded fallback — it always carries phase, reason, terminal evidence, an
excerpt, and templated remediation. Nothing here crashes; a broken analyzer must
never change the pipeline outcome.
"""

import json
import logging
from collections.abc import Mapping
from importlib import resources
from string import Template

from pydantic import ValidationError

from ci_doctor.config.schema import Config
from ci_doctor.core.analyze import EvidenceBundle
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.models import FailureReason, Job, Phase
from ci_doctor.core.ports import LLMClient
from ci_doctor.core.redact import redact_report, redact_text
from ci_doctor.llm.schema import Category, Evidence, RemediationStep, Report

log = logging.getLogger("ci_doctor.llm")

#: Templated fix advice per failure reason, used by the deterministic report.
_REMEDIATION = {
    FailureReason.NO_RUNNER: "Check that a runner with the required tags is online and not saturated.",
    FailureReason.RUNNER_SYSTEM: "Retry the job; if it recurs, check runner/executor health and image availability.",
    FailureReason.TIMEOUT: "Increase the job timeout or optimize the slow step; look for a hang.",
    FailureReason.MISSING_DEPENDENCY: "Ensure the upstream job that produces the artifact succeeded and needs/dependencies are correct.",
    FailureReason.UNMET_PREREQUISITES: "Verify job prerequisites (environments, approvals, variables) are satisfied.",
    FailureReason.SCRIPT_FAILURE: "Inspect the terminal command output in the excerpt and fix the failing command.",
}
#: Categories the provider reason settles on its own. A reason not here (notably
#: `script_failure`) leaves the category to the model — the deterministic report
#: does not guess an ecosystem from the log, which is the model's job.
_CATEGORY: dict[FailureReason, Category] = {
    FailureReason.NO_RUNNER: "infrastructure",
    FailureReason.RUNNER_SYSTEM: "infrastructure",
    FailureReason.TIMEOUT: "timeout",
    FailureReason.MISSING_DEPENDENCY: "dependency",
}


def _infer_category(reason: FailureReason) -> Category:
    """Categorise from the provider reason alone; "unknown" when it can't decide.

    Deliberately does not read the log: classifying a script failure as test/build/
    runtime means matching tool-specific regexes (pytest, tsc, go…) that duplicate the
    matcher catalogue and belong to the model. Without an LLM the category is honestly
    "unknown" — the phase still says where it broke.
    """
    return _CATEGORY.get(reason, "unknown")


def client_for_run(cfg: Config, environ: Mapping[str, str] | None = None) -> LLMClient | None:
    """Build the single LLM client a whole run shares.

    Built once per run rather than once per job: every backend holds a connection
    pool (and the `openai` one remembers whether the server accepts
    `response_format`), so a client per job threw both away 10 times over.

    Degrades to None rather than raising, exactly like `produce_report` — a broken
    backend must still leave the deterministic report standing (invariant #3).

    Args:
        cfg: The effective config.
        environ: Environment for API keys. Defaults to os.environ.

    Returns:
        The client, or None when the LLM is disabled, unconfigured, or failed to
        build. `produce_report` then takes its own deterministic path.
    """
    from ci_doctor.llm.backends import backend_ready, make_client

    if not (cfg.llm.enabled and backend_ready(cfg.llm)):
        log.debug("LLM disabled/not ready (backend=%s); no client for this run", cfg.llm.backend)
        return None
    try:
        return make_client(cfg.llm, environ=environ)
    except Exception as exc:  # noqa: BLE001 - never crash on a bad backend
        log.info("LLM backend %s unavailable (%s); deterministic reports", cfg.llm.backend, exc)
        return None


def produce_report(
    job: Job,
    attr: Attribution,
    bundle: EvidenceBundle,
    cfg: Config,
    *,
    client: LLMClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> Report:
    """Produce the final report, with or without an LLM.

    Degrades at every step rather than raising: LLM disabled, backend not
    configured, backend unavailable, and an invalid reply after one repair retry
    all end in the deterministic report. The output is always redacted.

    Args:
        job: The failed job.
        attr: The classifier's verdict. The LLM may not overturn its phase.
        bundle: The budgeted evidence.
        cfg: Config supplying the LLM and redaction settings.
        client: Pre-built LLM client, used by tests to avoid a real backend.
        environ: Environment for redaction and API keys. Defaults to os.environ.

    Returns:
        A validated, redacted report.
    """
    from ci_doctor.llm.backends import backend_ready, make_client

    if str(attr.reason) in cfg.analysis.skip_llm_for:
        # The reason already settles it (no runner, missing artifact, cancelled):
        # a model has nothing left to explain, so don't pay for the call.
        log.debug("reason %s is in analysis.skip_llm_for -> deterministic report", attr.reason)
        return redact_report(deterministic_report(job, attr, bundle), cfg.redaction, environ)

    if not (cfg.llm.enabled and (client is not None or backend_ready(cfg.llm))):
        log.debug("LLM disabled/not ready (backend=%s) -> deterministic report", cfg.llm.backend)
        return redact_report(deterministic_report(job, attr, bundle), cfg.redaction, environ)

    if client is None:
        try:
            client = make_client(cfg.llm, environ=environ)
        except Exception as exc:  # noqa: BLE001 - never crash on a bad backend
            log.info("LLM backend %s unavailable (%s); deterministic report", cfg.llm.backend, exc)
            return redact_report(deterministic_report(job, attr, bundle), cfg.redaction, environ)

    prompt = redact_text(_render_prompt(job, attr, bundle), cfg.redaction, environ)
    log.info(
        "job %s: calling LLM backend=%s model=%s (may take a while)", job.name, cfg.llm.backend, cfg.llm.model
    )
    log.debug("prompt=%d chars", len(prompt))
    report = _call_and_validate(client, prompt)
    if report is None:
        log.info("no valid LLM report; using deterministic fallback")
        report = deterministic_report(job, attr, bundle, degraded=True)
    else:
        log.debug("LLM returned a valid report")
        if report.failure_phase != attr.phase:
            # Invariant #1, enforced here and not only in the prompt: attribution
            # owns the phase. A model that argues with it gets overruled, silently
            # for the reader but loudly in the log.
            log.warning(
                "LLM answered phase=%s; overruled by attribution phase=%s (rule %s)",
                report.failure_phase,
                attr.phase,
                attr.rule_id,
            )
            report = report.model_copy(update={"failure_phase": attr.phase})
    return redact_report(report, cfg.redaction, environ)


def _call_and_validate(client: LLMClient, prompt: str) -> Report | None:
    """Call the LLM and validate its reply.

    No hand-rolled retry here: every backend already retries once internally
    on a schema-invalid reply (`Agent(retries=1)`); a second one would just
    duplicate it (see `149313e`).

    Args:
        client: The LLM client.
        prompt: The rendered prompt.

    Returns:
        The validated report, or None on any failure — the caller degrades to
        the deterministic report either way.
    """
    try:
        data = client.complete_structured(prompt)
        return Report.model_validate(data)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        log.info("LLM reply invalid; using deterministic fallback: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - network/LLM failure -> deterministic fallback
        log.warning("LLM call failed, using deterministic fallback: %s", exc)
        return None


def _capped(text: str, limit: int = 2000) -> str:
    """Cap the excerpt for the report field, announcing the cut.

    Keeps the tail, matching `budget.fit`: the failure lives at the end. The
    marker is the point — a silent cut is a lie about the evidence, and this is
    the last place the evidence can be lost before a human reads it.

    Args:
        text: The excerpt.
        limit: Maximum characters to keep.

    Returns:
        The text, or its last `limit` characters behind an elision marker.
    """
    if len(text) <= limit:
        return text
    return f"… [{len(text) - limit} characters elided] …\n{text[-limit:]}"


def deterministic_report(
    job: Job, attr: Attribution, bundle: EvidenceBundle, *, degraded: bool = False
) -> Report:
    """Build a full report from the deterministic pipeline alone.

    Not a stub: this is a first-class output (`llm.enabled: false`) *and* the
    degraded fallback. It always carries a phase, reason, terminal evidence, an
    excerpt and templated remediation.

    Args:
        job: The failed job.
        attr: The classifier's verdict.
        bundle: The budgeted evidence.
        degraded: Whether this stands in for a failed LLM call. When true, that
            fact is added to the contributing factors so the reader is never
            misled about which path produced the report.

    Returns:
        A valid report. Not yet redacted — callers do that.
    """
    # No second cut: `blamed_lines` is already denoised, windowed and budget-fitted.
    # Re-trimming discards that selection — see GUIDELINES §7.
    blamed = "\n".join(bundle.blamed_lines)
    excerpt = blamed if bundle.blamed_lines else (attr.terminal_evidence or "")
    is_infra = attr.phase in (Phase.PROVISION, Phase.PREPARE) or attr.reason in (
        FailureReason.RUNNER_SYSTEM,
        FailureReason.NO_RUNNER,
    )
    remediation = _REMEDIATION.get(attr.reason, "Inspect the log excerpt and address the failing step.")
    if attr.rule_id == "oom_137":
        remediation = "Reduce memory use or raise the runner memory limit (exit 137 = OOM/SIGKILL)."

    factors = [f"warnings in the {p} phase (non-causal)" for p in attr.secondary_phases]
    if degraded:
        factors.append("ci-doctor could not reach the LLM; this is the deterministic fallback report.")

    category = _infer_category(attr.reason)
    return Report(
        summary=f"{job.name} failed in the {attr.phase} phase ({attr.reason})."[:140],
        failure_phase=attr.phase,
        category=category,
        confidence=attr.confidence,
        is_infra_not_code=is_infra,
        likely_flaky=False,
        root_cause=attr.terminal_evidence
        or f"Deterministic analysis attributes the failure to the {attr.phase} phase (rule '{attr.rule_id}').",
        contributing_factors=factors,
        evidence=[
            Evidence(
                section=str(attr.phase),
                excerpt=_capped(excerpt),
                why_it_matters="Terminal evidence selected by deterministic attribution.",
            )
        ],
        remediation=[
            RemediationStep(
                order=1, action=remediation, rationale=f"reason={attr.reason}, rule={attr.rule_id}"
            )
        ],
        related_paths=[],
        handoff_prompt=_handoff(job, attr, excerpt),
    )


def _handoff(job: Job, attr: Attribution, excerpt: str) -> str:
    """Compose the prompt a developer can paste into their own coding agent.

    Args:
        job: The failed job.
        attr: The classifier's verdict.
        excerpt: The evidence lines.

    Returns:
        Plain prose with no markup, so copying it out of the terminal yields
        exactly what an agent should receive.
    """
    return (
        f'CI job "{job.name}" failed in the {attr.phase} phase '
        f"(reason: {attr.reason}, rule: {attr.rule_id}).\n"
        f"Terminal evidence:\n{attr.terminal_evidence or 'n/a'}\n\n"
        f"Relevant log excerpt:\n{excerpt}\n\n"
        "Diagnose the root cause and implement a fix. Do not change unrelated code."
    )


def _render_prompt(job: Job, attr: Attribution, bundle: EvidenceBundle) -> str:
    """Fill the system and user prompt templates.

    Invariant #8: prompt text lives in `llm/prompts/*.txt`, never inline here.
    The reply schema is no longer rendered into this text — `PromptedOutput`
    renders its own schema instructions when it builds the model request.

    Args:
        job: The failed job.
        attr: The classifier's verdict, handed to the model as *settled*.
        bundle: The budgeted evidence.

    Returns:
        The combined prompt. Still unredacted — the caller scrubs it before it
        leaves the process.
    """
    system = Template(_load("analyze.system.txt")).safe_substitute(
        phase=attr.phase,
        rule_id=attr.rule_id,
        confidence=attr.confidence,
    )
    user = Template(_load("analyze.user.txt")).safe_substitute(
        job=job.name,
        stage=job.stage or "?",
        failure_reason=str(job.failure_reason),
        raw_failure_reason=job.raw_failure_reason or "",
        duration=job.duration,
        needs=", ".join(job.needs) or "none",
        terminal=attr.terminal_evidence or "n/a",
        phase=attr.phase,
        secondary=", ".join(str(p) for p in attr.secondary_phases) or "none",
        excerpt="\n".join(bundle.blamed_lines),
    )
    return system + "\n\n---\n\n" + user


def _load(name: str) -> str:
    """Read a packaged prompt template.

    Args:
        name: File name under `llm/prompts/`, e.g. "analyze.system.txt".

    Returns:
        The template text.
    """
    return resources.files("ci_doctor.llm.prompts").joinpath(name).read_text()
