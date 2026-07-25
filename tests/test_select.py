"""Job selection policy: failed jobs only, allow_failure excluded by default."""

from ci_doctor.core.models import Job
from ci_doctor.core.select import select_failed_jobs


def _job(name, status, allow_failure=False):
    """Build a minimal job with the given status."""
    return Job(id=name, name=name, status=status, allow_failure=allow_failure)


def test_selects_only_failed_non_allowed():
    """Only failed jobs are selected, and allow_failure ones are skipped."""
    jobs = [
        _job("ok", "success"),
        _job("build", "failed"),
        _job("flaky", "failed", allow_failure=True),
        _job("running", "running"),
    ]
    assert [j.name for j in select_failed_jobs(jobs)] == ["build"]


def test_include_allowed_failures():
    """The opt-in flag brings allow_failure jobs back in."""
    jobs = [_job("build", "failed"), _job("flaky", "failed", allow_failure=True)]
    assert [j.name for j in select_failed_jobs(jobs, include_allowed_failures=True)] == ["build", "flaky"]
