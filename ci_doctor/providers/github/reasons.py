"""Map a GitHub job conclusion/status to the normalized FailureReason.

GitHub has no failure_reason enum, so this is derived from the job conclusion plus
the startup-failure signal. The classifier (shared, in core) still does the real
disambiguation from the log structure.
"""

from ci_doctor.core.models import FailureReason

#: GitHub job conclusions -> the neutral enum.
_MAP = {
    "failure": FailureReason.SCRIPT_FAILURE,
    "timed_out": FailureReason.TIMEOUT,
    "cancelled": FailureReason.CANCELLED,
    "startup_failure": FailureReason.RUNNER_SYSTEM,
    "action_required": FailureReason.UNMET_PREREQUISITES,
}


def to_failure_reason(conclusion: str | None, *, startup_failure: bool = False) -> FailureReason:
    """Derive a failure reason from a GitHub job conclusion.

    GitHub has no `failure_reason` field, so the conclusion plus the
    startup-failure signal is all there is to go on.

    Args:
        conclusion: The job's conclusion, e.g. "failure", "timed_out".
        startup_failure: Whether the run failed before any job started. Wins over
            the conclusion — the workflow never got as far as running.

    Returns:
        The mapped reason, or UNKNOWN.
    """
    if startup_failure:
        return FailureReason.RUNNER_SYSTEM
    if not conclusion:
        return FailureReason.UNKNOWN
    return _MAP.get(conclusion.lower(), FailureReason.UNKNOWN)
