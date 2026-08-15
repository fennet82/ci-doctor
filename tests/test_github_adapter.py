"""GitHub adapter: segmenter, reasons, provider mapping, attribution end to end.

The full run through the GitHub path proves core needed no changes for it.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from ci_doctor.config.loader import load_config
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job, Phase
from ci_doctor.core.phases import assign_phases
from ci_doctor.core.select import select_failed_jobs
from ci_doctor.providers.github.provider import GitHubProvider
from ci_doctor.providers.github.reasons import to_failure_reason
from ci_doctor.providers.github.segmenter import GitHubSegmenter


def test_reason_mapping():
    """Conclusions map to reasons, and startup_failure wins over the conclusion."""
    assert to_failure_reason("failure") == FailureReason.SCRIPT_FAILURE
    assert to_failure_reason("timed_out") == FailureReason.TIMEOUT
    assert to_failure_reason("cancelled") == FailureReason.CANCELLED
    assert to_failure_reason(None) == FailureReason.UNKNOWN
    assert to_failure_reason("failure", startup_failure=True) == FailureReason.RUNNER_SYSTEM


def test_segmenter_groups_and_canonical_names():
    """Groups become sections with canonical names, originals kept as headers."""
    log = (
        "2024-01-01T00:00:00.0Z ##[group]Run actions/checkout@v4\n"
        "2024-01-01T00:00:01.0Z Syncing repository\n"
        "2024-01-01T00:00:02.0Z ##[endgroup]\n"
        "2024-01-01T00:00:03.0Z ##[group]Run pytest -q\n"
        "2024-01-01T00:00:04.0Z E   assert 1 == 2\n"
        "2024-01-01T00:00:05.0Z ##[endgroup]\n"
        "2024-01-01T00:00:06.0Z ##[error]Process completed with exit code 1\n"
    )
    secs = GitHubSegmenter().segment(log)
    names = [s.name for s in secs]
    assert names == ["checkout", "run", "__trailer__"]
    assert secs[0].header == "Run actions/checkout@v4"  # original name preserved
    assert secs[0].closed and secs[1].closed
    assert "exit code 1" in secs[2].lines[-1].text  # error annotation -> trailer


def test_full_attribution_through_github_path():
    """End to end on a GitHub log, with zero GitHub-specific code in core.

    The provider segmenter plus the shared phase map and classifier reach the
    right verdict — which is the whole point of the ports/adapters split.
    """
    log = (
        "##[group]Run actions/checkout@v4\n"
        "Syncing repository\n"
        "##[endgroup]\n"
        "##[group]Run pytest -q\n"
        "E   assert 1 == 2\n"
        "##[endgroup]\n"
        "##[error]Process completed with exit code 1\n"
    )
    job = Job(id="1", name="test", status="failed", failure_reason=FailureReason.SCRIPT_FAILURE, log=log)
    cfg = load_config(environ={})
    job.sections = GitHubSegmenter().segment(log)
    assign_phases(job.sections, cfg.phases)

    assert job.sections[0].phase == Phase.FETCH  # checkout -> fetch
    assert job.sections[1].phase == Phase.SCRIPT  # run -> script
    attr = attribute(job, job.sections)
    assert attr.phase == Phase.SCRIPT
    assert attr.reason == FailureReason.SCRIPT_FAILURE


def _job(**kw):
    """A stand-in for a PyGithub WorkflowJob, with every attribute we read."""
    return SimpleNamespace(
        **{
            "runner_name": None,
            "runner_id": None,
            "started_at": None,
            "completed_at": None,
            "html_url": "",
            **kw,
        }
    )


def _fake_client(jobs, *, pull_requests=()):
    """An in-memory stand-in for `github.Github`, down to the workflow run."""
    run = SimpleNamespace(
        id=999,
        head_branch="main",
        head_sha="deadbeef",
        html_url="http://gh/run/999",
        pull_requests=list(pull_requests),
        jobs=lambda: jobs,
    )
    return SimpleNamespace(get_repo=lambda name: SimpleNamespace(get_workflow_run=lambda rid: run))


def test_provider_maps_jobs_and_normalizes_status():
    """PyGithub jobs map to the domain model and failing conclusions become "failed"."""
    jobs = [
        _job(
            id=11,
            name="build",
            status="completed",
            conclusion="failure",
            runner_name="gh-runner-1",
            runner_id=7,
            html_url="http://gh/11",
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        _job(id=12, name="flaky", status="completed", conclusion="timed_out"),
        _job(id=13, name="ok", status="completed", conclusion="success"),
        _job(id=14, name="slow", status="completed", conclusion="cancelled"),
    ]
    provider = GitHubProvider(
        load_config(environ={}),
        client=_fake_client(jobs),
        environ={"GITHUB_REPOSITORY": "acme/app", "GITHUB_REF": "refs/pull/42/merge"},
    )
    run = provider.fetch_run("999")

    build = run.jobs[0]
    assert build.status == "failed"  # normalized from conclusion
    assert build.failure_reason == FailureReason.SCRIPT_FAILURE
    assert build.runner.description == "gh-runner-1"
    assert build.started_at == "2024-01-01T00:00:00+00:00"  # datetime -> ISO string
    assert run.jobs[1].failure_reason == FailureReason.TIMEOUT
    assert run.jobs[2].status == "completed"  # success conclusion stays non-failed
    # A cancellation keeps its own status rather than being flattened into
    # "failed" — the domain has a word for it, and GitLab reports the same one.
    assert run.jobs[3].status == "cancelled"
    assert run.jobs[3].failure_reason == FailureReason.CANCELLED
    assert [j.name for j in select_failed_jobs(run.jobs)] == ["build", "flaky", "slow"]
    assert run.ref == "main" and run.sha == "deadbeef"  # straight off the run, no env vars
    assert run.mr is not None and run.mr.iid == "42"  # PR resolved from GITHUB_REF


def test_pull_request_on_the_run_wins_over_the_ref():
    """The run's own PR list is authoritative; GITHUB_REF is only the fork fallback."""
    provider = GitHubProvider(
        load_config(environ={}),
        client=_fake_client([], pull_requests=[SimpleNamespace(number=7)]),
        environ={"GITHUB_REPOSITORY": "acme/app", "GITHUB_REF": "refs/pull/42/merge"},
    )
    assert provider.fetch_run("999").mr.iid == "7"


def test_repository_falls_back_to_git_origin(monkeypatch):
    """With no GITHUB_REPOSITORY, the local origin names the repo (and warns)."""
    from ci_doctor.providers import git_origin

    git_origin.origin_repo.cache_clear()
    monkeypatch.setattr(
        git_origin.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="git@github.com:acme/app.git\n")
    )
    seen = []
    provider = GitHubProvider(
        load_config(environ={}), client=SimpleNamespace(get_repo=lambda name: seen.append(name)), environ={}
    )
    provider._repo()
    assert seen == ["acme/app"]
    git_origin.origin_repo.cache_clear()


def test_origin_keeps_nested_gitlab_groups(monkeypatch):
    """A GitLab subgroup path survives; a bare regex on the tail would lose it."""
    from ci_doctor.providers import git_origin

    git_origin.origin_repo.cache_clear()
    monkeypatch.setattr(
        git_origin.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="https://gitlab.example.com/group/sub/project.git\n"),
    )
    assert git_origin.origin_repo("CI_PROJECT_ID") == "group/sub/project"
    git_origin.origin_repo.cache_clear()
