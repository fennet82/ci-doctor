"""GitLab failure_reason normalisation, including graceful degradation."""

from ci_doctor.core.models import FailureReason
from ci_doctor.providers.gitlab.reasons import to_failure_reason


def test_known_reasons():
    """Documented GitLab reasons map to their neutral equivalents."""
    assert to_failure_reason("script_failure") == FailureReason.SCRIPT_FAILURE
    assert to_failure_reason("missing_dependency_failure") == FailureReason.MISSING_DEPENDENCY
    assert to_failure_reason("stuck_or_timeout_failure") == FailureReason.TIMEOUT
    assert to_failure_reason("runner_system_failure") == FailureReason.RUNNER_SYSTEM
    assert to_failure_reason("no_matching_runner") == FailureReason.NO_RUNNER


def test_unknown_and_empty_degrade():
    """Absent or future reasons degrade to UNKNOWN rather than raising."""
    assert to_failure_reason(None) == FailureReason.UNKNOWN
    assert to_failure_reason("") == FailureReason.UNKNOWN
    assert to_failure_reason("some_future_gitlab_reason") == FailureReason.UNKNOWN
