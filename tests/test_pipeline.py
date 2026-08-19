"""Pipeline orchestration: chronological ordering of the analyzed jobs."""

from ci_doctor.core.models import Job
from ci_doctor.pipeline import _ordered


def _job(name, started_at):
    """A minimal failed job with a start time (or None)."""
    return Job(id=name, name=name, status="failed", started_at=started_at)


def test_ordered_is_chronological_not_api_order():
    """Jobs sort by start time, so a report recaps build before deploy.

    The provider hands them back newest-first; the reader watched them run
    oldest-first.
    """
    deploy = _job("deploy", "2024-01-01T00:10:00Z")
    build = _job("build", "2024-01-01T00:01:00Z")
    assert [j.name for j in _ordered([deploy, build])] == ["build", "deploy"]


def test_ordered_puts_never_started_jobs_first():
    """A job with no runner never got a timestamp — it is earliest in the lifecycle."""
    ran = _job("build", "2024-01-01T00:05:00Z")
    stuck = _job("stuck", None)
    assert [j.name for j in _ordered([ran, stuck])] == ["stuck", "build"]


def test_ordered_is_stable_on_ties():
    """Same-instant jobs keep their incoming order rather than shuffling."""
    a = _job("a", "2024-01-01T00:00:00Z")
    b = _job("b", "2024-01-01T00:00:00Z")
    assert [j.name for j in _ordered([a, b])] == ["a", "b"]
    assert [j.name for j in _ordered([b, a])] == ["b", "a"]
