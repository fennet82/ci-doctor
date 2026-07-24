"""ci-doctor CLI (typer).

M0: only `--from-file` offline replay is wired up. Segment/attribute/analyze come
online in later milestones and reuse the same `--from-file` path.

Guardrail #3: the analyzer must never change a pipeline's outcome, so `main()`
catches everything and always exits 0.
"""

from __future__ import annotations

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
    load_config(repo_config=config_path)  # validates config even when unused in M0

    if from_file is not None:
        _summarize(_run_from_file(from_file))
        return

    typer.echo(
        "ci-doctor M0 skeleton: only --from-file is wired up. GitLab acquisition lands in M1.",
        err=True,
    )


def _run_from_file(path: Path) -> Run:
    """Load a raw job log into the domain model for offline replay."""
    log = path.read_text()
    job = Job(id="local", name=path.stem, status="failed",
              failure_reason=FailureReason.UNKNOWN, log=log)
    return Run(id="local", jobs=[job])


def _summarize(run: Run) -> None:
    for job in run.jobs:
        lines = 0 if job.log is None else job.log.count("\n") + 1
        typer.echo(f"job={job.name} status={job.status} reason={job.failure_reason} log_lines={lines}")


def main() -> None:
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
