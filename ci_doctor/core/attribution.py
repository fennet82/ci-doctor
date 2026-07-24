"""The blame classifier. Pure function: (Job, sections) -> Attribution.

No I/O, no network, no clock (guardrail #7). This decides *where* the job failed;
the LLM never does. The precedence ladder below is first-match-wins and every rule
records a ``rule_id`` so the report can say exactly why it decided.

The load-bearing anti-noise rule: a ``WARNING:``-prefixed runner line is non-fatal
by definition (missing cache, missing artifact, failed cache extract all warn and
the job continues). A section whose only negative evidence is ``WARNING:``-level
cannot be selected as the blamed phase — see ``_last_error_section``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ci_doctor.core.models import FailureReason, Job, Phase, Section

_SYNTHETIC = {"__preamble__", "__trailer__"}

# Conservative error anchors for the structural fallback. The full matcher
# catalogue (jest, go, maven, ...) is M3's extractor, not the classifier's job.
_ERROR_RE = re.compile(r"\b(ERROR|FATAL)\b|Traceback \(most recent call last\)|exit code \d+|npm ERR!")

_PHASE_REASON = {
    Phase.PROVISION: FailureReason.NO_RUNNER,
    Phase.PREPARE: FailureReason.RUNNER_SYSTEM,
    Phase.FETCH: FailureReason.MISSING_DEPENDENCY,
    Phase.SCRIPT: FailureReason.SCRIPT_FAILURE,
    Phase.POST: FailureReason.SCRIPT_FAILURE,
    Phase.UNKNOWN: FailureReason.UNKNOWN,
}


@dataclass
class Attribution:
    phase: Phase
    reason: FailureReason
    confidence: str  # "high" | "medium" | "low"
    terminal_evidence: str | None
    rule_id: str
    secondary_phases: list[Phase] = field(default_factory=list)


def attribute(job: Job, sections: list[Section]) -> Attribution:
    trailer = _find(sections, "__trailer__")

    # Rule 1 — no / empty log => the job never really ran.
    if not job.log or not job.log.strip():
        return Attribution(Phase.PROVISION, FailureReason.NO_RUNNER, "high", None, "empty_log_no_runner")

    reason = job.failure_reason

    # Rule 2 — provider failure_reason is authoritative for everything but script_failure.
    if reason == FailureReason.NO_RUNNER:
        return Attribution(Phase.PROVISION, reason, "high", _terminal(trailer), "reason_no_runner")
    if reason == FailureReason.RUNNER_SYSTEM:
        return Attribution(Phase.PREPARE, reason, "high", _terminal(trailer), "reason_runner_system")
    if reason == FailureReason.MISSING_DEPENDENCY:
        return Attribution(Phase.FETCH, reason, "high", _terminal(trailer), "reason_missing_dependency")
    if reason == FailureReason.UNMET_PREREQUISITES:
        return Attribution(Phase.PREPARE, reason, "medium", _terminal(trailer), "reason_unmet_prerequisites")
    if reason == FailureReason.CANCELLED:
        return Attribution(_open_phase(sections) or Phase.UNKNOWN, reason, "high", _terminal(trailer), "reason_cancelled")
    if reason == FailureReason.TIMEOUT:
        # Phase of whatever section was open when time ran out.
        return Attribution(_open_phase(sections) or Phase.PROVISION, reason, "high", _terminal(trailer), "reason_timeout_open_section")
    if reason == FailureReason.API_FAILURE:
        return Attribution(Phase.UNKNOWN, reason, "medium", _terminal(trailer), "reason_api_failure")

    # Rule 3 — script_failure is SCRIPT, full stop. This is the rule that kills the
    # "blamed the cache" bug: the noisy fetch/prepare warnings are never consulted.
    if reason == FailureReason.SCRIPT_FAILURE:
        return Attribution(Phase.SCRIPT, reason, "high", _terminal_command(sections),
                           "script_failure_is_script", _warning_phases(sections, Phase.SCRIPT))

    # --- reason is UNKNOWN: fall back to log structure ---

    # Rule 4 — an unclosed section is where execution died (hard abort).
    open_sec = _last_open_section(sections)
    if open_sec is not None:
        return Attribution(open_sec.phase, _PHASE_REASON[open_sec.phase], "medium",
                           _last_line(open_sec), "unclosed_section", _warning_phases(sections, open_sec.phase))

    # Rule 5 — parse the terminal trailer line.
    parsed = _parse_trailer(trailer)
    if parsed is not None:
        phase, rsn, rule_id, evidence = parsed
        return Attribution(phase, rsn, "high", evidence, rule_id, _warning_phases(sections, phase))

    # Rule 6 — last section with a real (non-WARNING) error line.
    sec = _last_error_section(sections)
    if sec is not None:
        return Attribution(sec.phase, _PHASE_REASON[sec.phase], "low",
                           _first_error_line(sec), "last_error_section", _warning_phases(sections, sec.phase))

    return Attribution(Phase.UNKNOWN, reason or FailureReason.UNKNOWN, "low", None, "no_signal")


# --- helpers ----------------------------------------------------------------


def _walk(sections):
    for sec in sections:
        yield sec
        yield from _walk(sec.children)


def _find(sections, name) -> Section | None:
    return next((s for s in _walk(sections) if s.name == name), None)


def _last_open_section(sections) -> Section | None:
    result = None
    for sec in _walk(sections):
        if sec.name not in _SYNTHETIC and not sec.closed:
            result = sec
    return result


def _open_phase(sections) -> Phase | None:
    sec = _last_open_section(sections)
    return sec.phase if sec is not None else None


def _is_error_line(text: str) -> bool:
    if text.lstrip().startswith("WARNING:"):
        return False  # non-fatal by definition
    return bool(_ERROR_RE.search(text))


def _last_error_section(sections) -> Section | None:
    result = None
    for sec in _walk(sections):
        if sec.name in _SYNTHETIC:
            continue
        if any(_is_error_line(line.text) for line in sec.lines):
            result = sec
    return result


def _first_error_line(sec: Section) -> str | None:
    return next((line.text for line in sec.lines if _is_error_line(line.text)), None)


def _last_line(sec: Section) -> str | None:
    return next((line.text for line in reversed(sec.lines) if line.text.strip()), None)


def _terminal(trailer: Section | None) -> str | None:
    return _last_line(trailer) if trailer is not None else None


def _terminal_command(sections) -> str | None:
    step = _find(sections, "step_script")
    if step is None:
        return None
    cmds = [line.text for line in step.lines if line.text.lstrip().startswith("$ ")]
    return cmds[-1] if cmds else _last_line(step)


def _matching_line(sec: Section, pattern: str) -> str | None:
    rx = re.compile(pattern, re.IGNORECASE)
    hit = next((line.text for line in sec.lines if rx.search(line.text)), None)
    return hit if hit is not None else _last_line(sec)


def _parse_trailer(trailer: Section | None):
    if trailer is None:
        return None
    text = "\n".join(line.text for line in trailer.lines)
    m = re.search(r"exit code (\d+)", text)
    if m:
        line = _matching_line(trailer, r"exit code")
        if int(m.group(1)) == 137:  # SIGKILL, typically the OOM killer
            return (Phase.SCRIPT, FailureReason.RUNNER_SYSTEM, "oom_137", line)
        return (Phase.SCRIPT, FailureReason.SCRIPT_FAILURE, "trailer_exit_code", line)
    if re.search(r"system failure|prepare environment", text):
        return (Phase.PREPARE, FailureReason.RUNNER_SYSTEM, "trailer_system_failure",
                _matching_line(trailer, r"system failure|prepare environment"))
    if re.search(r"execution took longer than|timeout", text, re.IGNORECASE):
        return (Phase.PROVISION, FailureReason.TIMEOUT, "trailer_timeout",
                _matching_line(trailer, r"took longer|timeout"))
    return None


def _warning_phases(sections, exclude: Phase) -> list[Phase]:
    out: list[Phase] = []
    for sec in _walk(sections):
        if sec.name in _SYNTHETIC or sec.phase == exclude:
            continue
        if any(line.text.lstrip().startswith("WARNING:") for line in sec.lines) and sec.phase not in out:
            out.append(sec.phase)
    return out
