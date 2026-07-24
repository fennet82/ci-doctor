"""ci-doctor CLI (typer).

M0: only `--from-file` offline replay is wired up. Segment/attribute/analyze come
online in later milestones and reuse the same `--from-file` path.

Guardrail #3: the analyzer must never change a pipeline's outcome, so `main()`
catches everything and always exits 0.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from ci_doctor import __version__
from ci_doctor.config.loader import load_config
from ci_doctor.core.models import FailureReason, Job, Run

app = typer.Typer(add_completion=False, help="Explain why a CI pipeline failed (read-only postmortem).")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """Explain why a CI pipeline failed (read-only postmortem)."""


@app.command()
def analyze(
    run_id: str = typer.Argument(None, help="Pipeline/run id. Omit when using --from-file."),
    from_file: Path = typer.Option(
        None, "--from-file", help="Replay a raw job log offline (no network, no LLM)."
    ),
    job_id: str = typer.Option(None, "--job-id", help="Analyze a single job id."),
    config_path: Path = typer.Option(None, "--config", help="Path to .ci-doctor.yml."),
) -> None:
    cfg = load_config(repo_config=config_path)

    if from_file is not None:
        _summarize(_run_from_file(from_file))
        return

    if run_id is not None:
        _analyze_live(run_id, cfg, job_id)
        return

    typer.echo("nothing to do: pass a pipeline id, or --from-file to replay a log.", err=True)


def _analyze_live(run_id: str, cfg, job_id: str | None) -> None:
    # Import the adapter lazily so `core` and the offline path never pull a provider in.
    from ci_doctor.core.select import select_failed_jobs
    from ci_doctor.providers.gitlab.provider import GitLabProvider

    if cfg.provider != "gitlab":
        raise ValueError(f"unsupported provider: {cfg.provider}")

    provider = GitLabProvider(cfg)
    run = provider.fetch_run(run_id)
    jobs = select_failed_jobs(run.jobs, cfg.analysis.include_allowed_failures)
    if job_id is not None:
        jobs = [j for j in jobs if j.id == job_id]
    if not jobs:
        typer.echo("no failed jobs to analyze.")
        return

    for job in jobs[: cfg.analysis.max_jobs_analyzed]:
        job.log = provider.fetch_job_log(job)
        _summarize_job(job)


def _run_from_file(path: Path) -> Run:
    """Load a raw job log into the domain model for offline replay."""
    log = path.read_text()
    job = Job(id="local", name=path.stem, status="failed",
              failure_reason=FailureReason.UNKNOWN, log=log)
    return Run(id="local", jobs=[job])


def _summarize(run: Run) -> None:
    for job in run.jobs:
        _summarize_job(job)


def _summarize_job(job: Job) -> None:
    lines = 0 if job.log is None else job.log.count("\n") + 1
    typer.echo(f"job={job.name} status={job.status} reason={job.failure_reason} log_lines={lines}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="ci-doctor: %(message)s")
    try:
        app()
    except SystemExit:
        # typer/click signal both normal and usage exits via SystemExit; force 0.
        raise SystemExit(0)
    except BaseException as exc:  # noqa: BLE001 - analyzer must never alter pipeline outcome
        print(f"ci-doctor: internal error, exiting 0 to preserve pipeline status: {exc}", file=sys.stderr)
        raise SystemExit(0)


if __name__ == "__main__":
    main()
