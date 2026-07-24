"""Map a GitHub job conclusion/status to the normalized FailureReason.

GitHub has no failure_reason enum, so this is derived from the job conclusion plus
the startup-failure signal. The classifier (shared, in core) still does the real
disambiguation from the log structure.
"""

from __future__ import annotations

from ci_doctor.core.models import FailureReason

_MAP = {
    "failure": FailureReason.SCRIPT_FAILURE,
    "timed_out": FailureReason.TIMEOUT,
    "cancelled": FailureReason.CANCELLED,
    "startup_failure": FailureReason.RUNNER_SYSTEM,
    "action_required": FailureReason.UNMET_PREREQUISITES,
}


def to_failure_reason(conclusion: str | None, *, startup_failure: bool = False) -> FailureReason:
    if startup_failure:
        return FailureReason.RUNNER_SYSTEM
    if not conclusion:
        return FailureReason.UNKNOWN
    return _MAP.get(conclusion.lower(), FailureReason.UNKNOWN)
