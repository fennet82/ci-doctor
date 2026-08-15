"""The blame classifier. Pure function: (Job, sections) -> Attribution.

No I/O, no network, no clock (invariant #7). This decides *where* the job failed;
the LLM never does. The precedence ladder below is first-match-wins and every rule
records a ``rule_id`` so the report can say exactly why it decided.

The load-bearing anti-noise rule: a line the *runner* marked as a warning is
non-fatal by definition (missing cache, missing artifact, failed cache extract all
warn and the job continues). A section whose only negative evidence is warning-level
cannot be selected as the blamed phase — see ``_is_warning_line`` for which prefixes
count, and ``_last_error_section`` for where it bites.
"""

import re
from dataclasses import dataclass, field

from ci_doctor.core.models import Confidence, FailureReason, Job, Phase, Section

_SYNTHETIC = {"__preamble__", "__trailer__"}

#: Fatality signals for the structural fallback, and the whole of it. This answers
#: one coarse question — "did anything in this section actually fail?" — to pick
#: which *section* to blame. Which *lines* are the evidence is a different question,
#: asked later, by the config matcher catalogue in `extraction.matchers`.
#:
#: So this list stays runner-level: the runner's own error annotations, and a
#: non-zero exit. No vendor tokens. A pytest/jest/npm signature added here would be
#: a second, hardcoded copy of the catalogue that no `.ci-doctor.yml` can tune, and
#: it would buy nothing — a tool that fails at all makes its runner say so.
_ERROR_RE = re.compile(r"^##\[error\]|\b(ERROR|FATAL)\b|exit code \d+")

#: How a runner marks its *own* line as a warning, which is not the same thing as a
#: tool printing the word. Two forms, because the two runners we read differ: a
#: literal `WARNING:` prefix, and the `##[warning]` annotation. A line carrying
#: either is non-fatal by definition, whatever alarming words follow it.
#:
#: Deliberately case-sensitive. `Warning:` and `warning:` are what apt, docker and
#: pip print from *inside* a step, and that is the step's output, not the runner
#: excusing it — swallowing those would let a real failure go unblamed.
#:
#: Deliberately warnings only, not every non-`error` annotation level. `##[notice]`
#: and `##[debug]` are also non-fatal, but this predicate does double duty: it also
#: decides which phases get reported as contributing factors. Widening it to the
#: whole syntax would surface a phase whose only annotation was a debug line as
#: "warnings present", which is a lie to the reader for no gain.
_WARNING_RE = re.compile(r"^(WARNING:|##\[warning\])")

#: Fallback reason when the phase was concluded from log structure rather than
#: reported by the provider. Keeps a structurally-derived verdict self-consistent.
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
    """The classifier's verdict on where and why a job failed.

    Attributes:
        phase: The blamed phase. Decided here and only here — never by the LLM.
        reason: The normalised failure reason backing that phase.
        confidence: "high", "medium" or "low". High means the provider told us or
            the trailer said so outright; low means we guessed from structure.
        terminal_evidence: The single most telling line, when one exists.
        rule_id: Which rung of the ladder fired, so the report can say *why* it
            decided rather than only what it decided.
        secondary_phases: Phases that emitted runner warnings but were not blamed.
            Surfaced as contributing factors — noise worth mentioning, not blaming.
    """

    phase: Phase
    reason: FailureReason
    confidence: Confidence
    terminal_evidence: str | None
    rule_id: str
    secondary_phases: list[Phase] = field(default_factory=list)


def attribute(job: Job, sections: list[Section]) -> Attribution:
    """Decide which phase a job failed in.

    A pure function — no I/O, no network, no clock (invariant #7) — so the same
    log always yields the same verdict. The rules are a first-match-wins ladder:
    an empty log, then the provider's own reason, then `script_failure` short-
    circuiting straight to SCRIPT, then structural fallbacks for an UNKNOWN reason.

    Args:
        job: The failed job, with its raw log attached.
        sections: The job's segmented sections, phases already assigned.

    Returns:
        The verdict, tagged with the `rule_id` that produced it.
    """
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
        return Attribution(
            _open_phase(sections) or Phase.UNKNOWN, reason, "high", _terminal(trailer), "reason_cancelled"
        )
    if reason == FailureReason.TIMEOUT:
        # Phase of whatever section was open when time ran out.
        return Attribution(
            _open_phase(sections) or Phase.PROVISION,
            reason,
            "high",
            _terminal(trailer),
            "reason_timeout_open_section",
        )
    if reason == FailureReason.API_FAILURE:
        return Attribution(Phase.UNKNOWN, reason, "medium", _terminal(trailer), "reason_api_failure")

    # Rule 3 — script_failure is SCRIPT, full stop. This is the rule that kills the
    # "blamed the cache" bug: the noisy fetch/prepare warnings are never consulted.
    if reason == FailureReason.SCRIPT_FAILURE:
        return Attribution(
            Phase.SCRIPT,
            reason,
            "high",
            _terminal_command(sections),
            "script_failure_is_script",
            _warning_phases(sections, Phase.SCRIPT),
        )

    # --- reason is UNKNOWN: fall back to log structure ---

    # Rule 4 — an unclosed section is where execution died (hard abort).
    open_sec = _last_open_section(sections)
    if open_sec is not None:
        return Attribution(
            open_sec.phase,
            _PHASE_REASON[open_sec.phase],
            "medium",
            _last_line(open_sec),
            "unclosed_section",
            _warning_phases(sections, open_sec.phase),
        )

    # Rule 5 — parse the terminal trailer line.
    parsed = _parse_trailer(trailer)
    if parsed is not None:
        phase, rsn, rule_id, evidence = parsed
        return Attribution(phase, rsn, "high", evidence, rule_id, _warning_phases(sections, phase))

    # Rule 6 — last section with a real (non-warning) error line.
    sec = _last_error_section(sections)
    if sec is not None:
        return Attribution(
            sec.phase,
            _PHASE_REASON[sec.phase],
            "low",
            _first_error_line(sec),
            "last_error_section",
            _warning_phases(sections, sec.phase),
        )

    return Attribution(Phase.UNKNOWN, reason or FailureReason.UNKNOWN, "low", None, "no_signal")


# --- helpers ----------------------------------------------------------------


def _walk(sections):
    """Yield every section depth-first, parents before their children.

    Args:
        sections: Top-level sections.

    Yields:
        Each section in the tree, in log order.
    """
    for sec in sections:
        yield sec
        yield from _walk(sec.children)


def _find(sections, name) -> Section | None:
    """Find the first section with a given name.

    Args:
        sections: Top-level sections.
        name: Section name to look for, e.g. "__trailer__".

    Returns:
        The section, or None when absent.
    """
    return next((s for s in _walk(sections) if s.name == name), None)


def _last_open_section(sections) -> Section | None:
    """Find the last section that never saw its end marker.

    An unclosed section means execution died inside it — the strongest structural
    signal available when the provider reported no reason.

    Args:
        sections: Top-level sections.

    Returns:
        The innermost/latest unclosed real section, or None when all are closed.
    """
    result = None
    for sec in _walk(sections):
        if sec.name not in _SYNTHETIC and not sec.closed:
            result = sec
    return result


def _open_phase(sections) -> Phase | None:
    """Phase of whatever section was still running.

    Args:
        sections: Top-level sections.

    Returns:
        The unclosed section's phase, or None when nothing was left open. Used
        for cancellations and timeouts, where "what was it doing" is the answer.
    """
    sec = _last_open_section(sections)
    return sec.phase if sec is not None else None


def _is_warning_line(text: str) -> bool:
    """Whether a line is the runner flagging something advisory.

    Args:
        text: One log line.

    Returns:
        True for a runner's own warning prefix — not for a tool that merely
        printed the word. Shared by the two rules that care — one to refuse to
        blame such a line, one to surface its phase as a contributing factor —
        so a form that fools one cannot fool the other.
    """
    return bool(_WARNING_RE.match(text.lstrip()))


def _is_error_line(text: str) -> bool:
    """Whether a line is evidence of an actual failure.

    The load-bearing anti-noise rule: a warning-prefixed runner line is non-fatal
    by definition — a missing cache, a missing artifact and a failed cache extract
    all warn and the job carries on. Treating those as errors is exactly the
    "blamed the cache" bug.

    Args:
        text: One log line.

    Returns:
        True if the line looks fatal.
    """
    if _is_warning_line(text):
        return False  # non-fatal by definition
    return bool(_ERROR_RE.search(text.lstrip()))


def _last_error_section(sections) -> Section | None:
    """Find the last section containing a genuinely fatal line.

    Args:
        sections: Top-level sections.

    Returns:
        The section, or None when only warnings were found. A section whose sole
        negative evidence is warning-level can never be selected here.
    """
    result = None
    for sec in _walk(sections):
        if sec.name in _SYNTHETIC:
            continue
        if any(_is_error_line(line.text) for line in sec.lines):
            result = sec
    return result


def _first_error_line(sec: Section) -> str | None:
    """The earliest fatal line in a section — the cause, not its cascade.

    Args:
        sec: The blamed section.

    Returns:
        The line text, or None when the section has no fatal line.
    """
    return next((line.text for line in sec.lines if _is_error_line(line.text)), None)


def _last_line(sec: Section) -> str | None:
    """The section's last non-blank line.

    Args:
        sec: Any section.

    Returns:
        The line text, or None when the section is empty or all blank.
    """
    return next((line.text for line in reversed(sec.lines) if line.text.strip()), None)


def _terminal(trailer: Section | None) -> str | None:
    """The job's final line — usually the runner's verdict.

    Args:
        trailer: The synthetic "__trailer__" section, or None when absent.

    Returns:
        The last non-blank line, or None.
    """
    return _last_line(trailer) if trailer is not None else None


def _terminal_command(sections) -> str | None:
    """The last command the user's script ran before it failed.

    Args:
        sections: Top-level sections.

    Returns:
        The last `$ `-prefixed echoed command, falling back to the script
        section's last line, or None when there is no script section.
    """
    step = _find(sections, "step_script")
    if step is None:
        return None
    cmds = [line.text for line in step.lines if line.text.lstrip().startswith("$ ")]
    return cmds[-1] if cmds else _last_line(step)


def _matching_line(sec: Section, pattern: str) -> str | None:
    """First line in a section matching a pattern.

    Args:
        sec: The section to search.
        pattern: Case-insensitive regex.

    Returns:
        The matching line, falling back to the section's last line so the
        evidence field is never empty when we know the section is at fault.
    """
    rx = re.compile(pattern, re.IGNORECASE)
    hit = next((line.text for line in sec.lines if rx.search(line.text)), None)
    return hit if hit is not None else _last_line(sec)


def _parse_trailer(trailer: Section | None):
    """Read the runner's closing verdict.

    Args:
        trailer: The synthetic "__trailer__" section, or None.

    Returns:
        A ``(phase, reason, rule_id, evidence)`` tuple, or None when the trailer
        says nothing recognisable. Exit code 137 is special-cased: SIGKILL is the
        OOM killer, so it is infrastructure rather than a script bug.
    """
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
        return (
            Phase.PREPARE,
            FailureReason.RUNNER_SYSTEM,
            "trailer_system_failure",
            _matching_line(trailer, r"system failure|prepare environment"),
        )
    if re.search(r"execution took longer than|timeout", text, re.IGNORECASE):
        return (
            Phase.PROVISION,
            FailureReason.TIMEOUT,
            "trailer_timeout",
            _matching_line(trailer, r"took longer|timeout"),
        )
    return None


def _warning_phases(sections, exclude: Phase) -> list[Phase]:
    """Phases that warned but were not blamed.

    Args:
        sections: Top-level sections.
        exclude: The blamed phase, left out of the result.

    Returns:
        Distinct phases carrying warning lines, in log order. These become
        contributing factors — worth mentioning to the reader, never the verdict.
    """
    out: list[Phase] = []
    for sec in _walk(sections):
        if sec.name in _SYNTHETIC or sec.phase == exclude:
            continue
        if any(_is_warning_line(line.text) for line in sec.lines) and sec.phase not in out:
            out.append(sec.phase)
    return out
