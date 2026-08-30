"""Pipeline orchestration: chronological ordering of the analyzed jobs."""

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import Job, Run
from ci_doctor.pipeline import _ordered, analyze_run
from tests import support


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


class _FakeProvider:
    """A CIProvider that serves one fixture log for every job, counting fetches."""

    def __init__(self, jobs, log):
        self.jobs = jobs
        self.log = log
        self.fetched = []

    def fetch_run(self, run_ref):
        """Return the canned run."""
        return Run(id=run_ref, jobs=self.jobs)

    def fetch_job_log(self, job):
        """Hand back the same log for any job, recording the order of fetches."""
        self.fetched.append(job.name)
        return self.log


def _wire_provider(monkeypatch, names):
    """Point analyze_run at a fake provider serving `names` as failed jobs."""
    log = support.read_log("github", "jest_test_failure")
    jobs = [
        Job(id=n, name=n, status="failed", started_at=f"2024-01-01T00:0{i}:00Z") for i, n in enumerate(names)
    ]
    provider = _FakeProvider(jobs, log)
    monkeypatch.setattr("ci_doctor.pipeline.make_ci_provider", lambda cfg: provider)
    return provider


def test_analyze_run_preserves_job_order_when_parallel(monkeypatch):
    """Concurrency must not reorder the report — the reader expects pipeline order."""
    names = [f"job{i}" for i in range(8)]
    _wire_provider(monkeypatch, names)
    cfg = load_config(environ={}, overrides={"analysis": {"max_parallel_jobs": 4}})
    _run, _provider, results = analyze_run("42", cfg)
    assert [r.job.name for r in results] == names


def test_analyze_run_analyzes_every_job_when_sequential(monkeypatch):
    """max_parallel_jobs: 1 is a real opt-out, and still covers every job."""
    names = [f"job{i}" for i in range(3)]
    _wire_provider(monkeypatch, names)
    cfg = load_config(environ={}, overrides={"analysis": {"max_parallel_jobs": 1}})
    _run, _provider, results = analyze_run("42", cfg)
    assert [r.job.name for r in results] == names


def test_analyze_run_builds_one_llm_client_for_the_whole_run(monkeypatch):
    """One client per run, not one per job — a pool per job was the old cost."""
    built = []

    class _Client:
        def complete_structured(self, prompt):
            """Answer with something the schema rejects, so the run degrades cleanly."""
            return {}

    def counting_make_client(cfg, environ=None):
        """Stand in for the real factory, counting constructions."""
        built.append(cfg)
        return _Client()

    monkeypatch.setattr("ci_doctor.llm.backends.make_client", counting_make_client)
    _wire_provider(monkeypatch, ["a", "b", "c", "d"])
    cfg = load_config(
        environ={},
        overrides={"llm": {"enabled": True, "model": "m", "api_base": "http://stub"}},
    )
    _run, _provider, results = analyze_run("42", cfg)
    assert len(results) == 4
    assert len(built) == 1
