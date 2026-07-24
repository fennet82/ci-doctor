from ci_doctor.core.models import Job
from ci_doctor.core.select import select_failed_jobs


def _job(name, status, allow_failure=False):
    return Job(id=name, name=name, status=status, allow_failure=allow_failure)


def test_selects_only_failed_non_allowed():
    jobs = [
        _job("ok", "success"),
        _job("build", "failed"),
        _job("flaky", "failed", allow_failure=True),
        _job("running", "running"),
    ]
    assert [j.name for j in select_failed_jobs(jobs)] == ["build"]


def test_include_allowed_failures():
    jobs = [_job("build", "failed"), _job("flaky", "failed", allow_failure=True)]
    assert [j.name for j in select_failed_jobs(jobs, include_allowed_failures=True)] == ["build", "flaky"]
