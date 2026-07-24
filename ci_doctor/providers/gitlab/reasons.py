"""Map a GitLab job `failure_reason` string to the normalized `FailureReason`.

Unknown / future GitLab reasons degrade to UNKNOWN rather than raising — older and
newer self-managed instances both drift from what we know. The classifier (M2)
does the real disambiguation from log structure; this is only the initial
normalization. The provider's original string is always kept in
`Job.raw_failure_reason`.
"""

from __future__ import annotations

from ci_doctor.core.models import FailureReason

_MAP: dict[str, FailureReason] = {
    "script_failure": FailureReason.SCRIPT_FAILURE,
    "api_failure": FailureReason.API_FAILURE,
    "stuck_or_timeout_failure": FailureReason.TIMEOUT,
    "job_execution_timeout": FailureReason.TIMEOUT,
    "runner_system_failure": FailureReason.RUNNER_SYSTEM,
    "scheduler_failure": FailureReason.RUNNER_SYSTEM,
    "runner_unsupported": FailureReason.RUNNER_SYSTEM,
    "missing_dependency_failure": FailureReason.MISSING_DEPENDENCY,
    "unmet_prerequisites": FailureReason.UNMET_PREREQUISITES,
    "no_matching_runner": FailureReason.NO_RUNNER,
}


def to_failure_reason(raw: str | None) -> FailureReason:
    if not raw:
        return FailureReason.UNKNOWN
    return _MAP.get(raw, FailureReason.UNKNOWN)
