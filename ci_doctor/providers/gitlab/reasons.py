"""Map a GitLab job `failure_reason` string to the normalized `FailureReason`.

Unknown / future GitLab reasons degrade to UNKNOWN rather than raising — older and
newer self-managed instances both drift from what we know. The classifier (M2)
does the real disambiguation from log structure; this is only the initial
normalization. The provider's original string is always kept in
`Job.raw_failure_reason`.
"""

from ci_doctor.core.models import FailureReason

#: GitLab's `failure_reason` strings -> the neutral enum. Several GitLab reasons
#: collapse onto one of ours; that is intended, the distinctions they draw are not
#: ones a developer acts on differently.
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
    """Normalise a GitLab failure reason.

    Args:
        raw: GitLab's string, or None when the API reported none.

    Returns:
        The mapped reason, or UNKNOWN. Unrecognised values degrade rather than
        raise — self-managed instances drift both older and newer than us, and
        the classifier does the real disambiguation from the log anyway.
    """
    if not raw:
        return FailureReason.UNKNOWN
    return _MAP.get(raw, FailureReason.UNKNOWN)
