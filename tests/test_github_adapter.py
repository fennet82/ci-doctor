"""GitHub adapter: segmenter, reasons, provider mapping, and a full end-to-end
attribution through the GitHub path — proving core needed no changes.
"""

from types import SimpleNamespace

from ci_doctor.config.loader import load_config
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job, Phase
from ci_doctor.core.phases import assign_phases
from ci_doctor.providers.github.provider import GitHubProvider
from ci_doctor.providers.github.reasons import to_failure_reason
from ci_doctor.providers.github.segmenter import GitHubSegmenter


def test_reason_mapping():
    assert to_failure_reason("failure") == FailureReason.SCRIPT_FAILURE
    assert to_failure_reason("timed_out") == FailureReason.TIMEOUT
    assert to_failure_reason("cancelled") == FailureReason.CANCELLED
    assert to_failure_reason(None) == FailureReason.UNKNOWN
    assert to_failure_reason("failure", startup_failure=True) == FailureReason.RUNNER_SYSTEM


def test_segmenter_groups_and_canonical_names():
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
    # The GitHub segmenter + shared phase-map + shared classifier => correct verdict,
    # with zero GitHub-specific code in core.
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

    assert job.sections[0].phase == Phase.FETCH   # checkout -> fetch
    assert job.sections[1].phase == Phase.SCRIPT  # run -> script
    attr = attribute(job, job.sections)
    assert attr.phase == Phase.SCRIPT
    assert attr.reason == FailureReason.SCRIPT_FAILURE


def _fake_client(jobs, logs):
    return SimpleNamespace(
        run_jobs=lambda repo, run_id: jobs,
        job_log=lambda repo, job_id: logs.get(job_id, ""),
    )


def test_provider_maps_jobs_and_normalizes_status():
    jobs = [
        {"id": 11, "name": "build", "status": "completed", "conclusion": "failure",
         "runner_name": "gh-runner-1", "html_url": "http://gh/11"},
        {"id": 12, "name": "flaky", "status": "completed", "conclusion": "timed_out"},
        {"id": 13, "name": "ok", "status": "completed", "conclusion": "success"},
    ]
    provider = GitHubProvider(load_config(environ={}), client=_fake_client(jobs, {11: b"boom".decode()}),
                              environ={"GITHUB_REPOSITORY": "acme/app", "GITHUB_REF": "refs/pull/42/merge"})
    run = provider.fetch_run("999")

    build = run.jobs[0]
    assert build.status == "failed"  # normalized from conclusion
    assert build.failure_reason == FailureReason.SCRIPT_FAILURE
    assert build.runner.description == "gh-runner-1"
    assert run.jobs[1].failure_reason == FailureReason.TIMEOUT
    assert run.jobs[2].status == "completed"  # success conclusion stays non-failed
    assert run.mr is not None and run.mr.iid == "42"  # PR resolved from GITHUB_REF
