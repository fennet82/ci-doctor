"""Produce the Report: one LLM call (plus one repair retry), or a deterministic
report when the LLM is disabled/unconfigured/unreachable.

The deterministic report is a first-class output (llm.enabled: false), the M2-grade
verdict, AND the degraded fallback — it always carries phase, reason, terminal
evidence, an excerpt, and templated remediation. Nothing here crashes; a broken
analyzer must never change the pipeline outcome.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from string import Template

from pydantic import ValidationError

from ci_doctor.config.schema import Config
from ci_doctor.core.analyze import EvidenceBundle
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.models import FailureReason, Job, Phase
from ci_doctor.core.redact import redact_report, redact_text
from ci_doctor.llm.schema import Evidence, RemediationStep, Report

log = logging.getLogger("ci_doctor.llm")

_REMEDIATION = {
    FailureReason.NO_RUNNER: "Check that a runner with the required tags is online and not saturated.",
    FailureReason.RUNNER_SYSTEM: "Retry the job; if it recurs, check runner/executor health and image availability.",
    FailureReason.TIMEOUT: "Increase the job timeout or optimize the slow step; look for a hang.",
    FailureReason.MISSING_DEPENDENCY: "Ensure the upstream job that produces the artifact succeeded and needs/dependencies are correct.",
    FailureReason.UNMET_PREREQUISITES: "Verify job prerequisites (environments, approvals, variables) are satisfied.",
    FailureReason.SCRIPT_FAILURE: "Inspect the terminal command output in the excerpt and fix the failing command.",
}
_CATEGORY = {
    FailureReason.NO_RUNNER: "infrastructure",
    FailureReason.RUNNER_SYSTEM: "infrastructure",
    FailureReason.TIMEOUT: "timeout",
    FailureReason.MISSING_DEPENDENCY: "dependency",
}


def produce_report(job, attr, bundle, cfg: Config, *, client=None, environ=None) -> Report:
    if not (cfg.llm.enabled and cfg.llm.model and cfg.llm.api_base):
        log.debug("LLM disabled/unconfigured -> deterministic report")
        return redact_report(deterministic_report(job, attr, bundle), cfg.redaction, environ)

    if client is None:
        from ci_doctor.llm.client import OpenAILLMClient

        client = OpenAILLMClient(cfg.llm, environ=environ)

    schema = Report.model_json_schema()
    prompt = redact_text(_render_prompt(job, attr, bundle, schema), cfg.redaction, environ)
    log.debug("calling LLM model=%s api_base=%s prompt=%d chars", cfg.llm.model, cfg.llm.api_base, len(prompt))
    report = _call_and_validate(client, prompt, schema)
    if report is None:
        log.info("no valid LLM report; using deterministic fallback")
        report = deterministic_report(job, attr, bundle, degraded=True)
    else:
        log.debug("LLM returned a valid report")
    return redact_report(report, cfg.redaction, environ)


def _call_and_validate(client, prompt: str, schema: dict) -> Report | None:
    hint = ""
    for _ in range(2):  # one call + one repair retry
        try:
            data = client.complete_structured(prompt + hint, schema)
            return Report.model_validate(data)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            log.debug("LLM reply invalid, repair retry: %s", exc)
            hint = f"\n\nYour previous reply was invalid ({exc}). Reply with ONLY a JSON object matching the schema."
        except Exception as exc:  # noqa: BLE001 - network/LLM failure -> deterministic fallback
            log.warning("LLM call failed, using deterministic fallback: %s", exc)
            return None
    return None


def deterministic_report(job: Job, attr: Attribution, bundle: EvidenceBundle, *, degraded: bool = False) -> Report:
    excerpt = "\n".join(bundle.blamed_lines[-15:]) if bundle.blamed_lines else (attr.terminal_evidence or "")
    is_infra = attr.phase in (Phase.PROVISION, Phase.PREPARE) or attr.reason in (FailureReason.RUNNER_SYSTEM, FailureReason.NO_RUNNER)
    remediation = _REMEDIATION.get(attr.reason, "Inspect the log excerpt and address the failing step.")
    if attr.rule_id == "oom_137":
        remediation = "Reduce memory use or raise the runner memory limit (exit 137 = OOM/SIGKILL)."

    factors = [f"warnings in the {p} phase (non-causal)" for p in attr.secondary_phases]
    if degraded:
        factors.append("ci-doctor could not reach the LLM; this is the deterministic fallback report.")

    return Report(
        summary=f"{job.name} failed in the {attr.phase} phase ({attr.reason})."[:140],
        failure_phase=attr.phase,
        category=_CATEGORY.get(attr.reason, "unknown"),
        confidence=attr.confidence,
        is_infra_not_code=is_infra,
        likely_flaky=False,
        root_cause=attr.terminal_evidence
        or f"Deterministic analysis attributes the failure to the {attr.phase} phase (rule '{attr.rule_id}').",
        contributing_factors=factors,
        evidence=[Evidence(section=str(attr.phase), excerpt=excerpt[:2000],
                           why_it_matters="Terminal evidence selected by deterministic attribution.")],
        remediation=[RemediationStep(order=1, action=remediation, rationale=f"reason={attr.reason}, rule={attr.rule_id}")],
        related_paths=[],
        handoff_prompt=_handoff(job, attr, excerpt),
    )


def _handoff(job: Job, attr: Attribution, excerpt: str) -> str:
    return (
        f'CI job "{job.name}" failed in the {attr.phase} phase '
        f"(reason: {attr.reason}, rule: {attr.rule_id}).\n"
        f"Terminal evidence:\n{attr.terminal_evidence or 'n/a'}\n\n"
        f"Relevant log excerpt:\n{excerpt}\n\n"
        "Diagnose the root cause and implement a fix. Do not change unrelated code."
    )


def _render_prompt(job: Job, attr: Attribution, bundle: EvidenceBundle, schema: dict) -> str:
    system = Template(_load("analyze.system.txt")).safe_substitute(
        phase=attr.phase, rule_id=attr.rule_id, confidence=attr.confidence, schema=json.dumps(schema),
    )
    user = Template(_load("analyze.user.txt")).safe_substitute(
        job=job.name, stage=job.stage or "?", failure_reason=str(job.failure_reason),
        raw_failure_reason=job.raw_failure_reason or "", duration=job.duration,
        needs=", ".join(job.needs) or "none", terminal=attr.terminal_evidence or "n/a",
        phase=attr.phase, secondary=", ".join(str(p) for p in attr.secondary_phases) or "none",
        excerpt="\n".join(bundle.blamed_lines),
    )
    return system + "\n\n---\n\n" + user


def _load(name: str) -> str:
    return resources.files("ci_doctor.llm.prompts").joinpath(name).read_text()
