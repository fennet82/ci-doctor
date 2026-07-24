"""ci-doctor CLI (typer).

`analyze <pipeline_id>` against live GitLab, or `--from-file` to replay a raw log
offline. Both run the same pipeline: segment -> classify -> evidence -> report ->
render/deliver.

Guardrail #3: the analyzer must never change a pipeline's outcome, so `main()`
catches everything and always exits 0.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import typer

from ci_doctor import __version__
from ci_doctor.config.loader import load_config
from ci_doctor.core.models import FailureReason, Job, Run

app = typer.Typer(add_completion=False, help="Explain why a CI pipeline failed (read-only postmortem).")

log = logging.getLogger("ci_doctor.cli")


def _configure_logging(verbose: bool) -> None:
    """`--verbose` (or CI_DOCTOR_LOG_LEVEL=DEBUG) turns on debug logs across ci_doctor.*."""
    level_name = os.environ.get("CI_DOCTOR_LOG_LEVEL", "").upper()
    level = logging.DEBUG if verbose else getattr(logging, level_name, logging.INFO)
    logging.getLogger("ci_doctor").setLevel(level)
    if verbose:
        log.debug("verbose logging enabled")


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
    no_color: bool = typer.Option(False, "--no-color", help="Disable coloured terminal output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    _configure_logging(verbose)
    cfg = load_config(repo_config=config_path)
    log.debug("provider=%s from_file=%s run_id=%s job_id=%s", cfg.provider, from_file, run_id, job_id)

    run = provider = None
    if from_file is not None:
        results = [_process(job, cfg) for job in _run_from_file(from_file).jobs]
    elif run_id is not None:
        run, provider, results = _analyze_live(run_id, cfg, job_id)
    else:
        typer.echo("nothing to do: pass a pipeline id, or --from-file to replay a log.", err=True)
        return

    _deliver(results, cfg, no_color=no_color)
    _maybe_post_mr(provider, run, results, cfg)


def _make_provider(cfg):
    # Import adapters lazily so `core` and the offline path never pull a provider in.
    if cfg.provider == "gitlab":
        from ci_doctor.providers.gitlab.provider import GitLabProvider

        return GitLabProvider(cfg)
    if cfg.provider == "github":
        from ci_doctor.providers.github.provider import GitHubProvider

        return GitHubProvider(cfg)
    raise ValueError(f"unsupported provider: {cfg.provider}")


def _make_segmenter(cfg):
    if cfg.provider == "github":
        from ci_doctor.providers.github.segmenter import GitHubSegmenter

        return GitHubSegmenter()
    from ci_doctor.providers.gitlab.segmenter import GitLabSegmenter

    return GitLabSegmenter()


def _analyze_live(run_id: str, cfg, job_id: str | None):
    from ci_doctor.core.select import select_failed_jobs

    provider = _make_provider(cfg)
    run = provider.fetch_run(run_id)
    jobs = select_failed_jobs(run.jobs, cfg.analysis.include_allowed_failures)
    if job_id is not None:
        jobs = [j for j in jobs if j.id == job_id]
    log.debug("run %s: %d jobs, %d failed and selected", run_id, len(run.jobs), len(jobs))

    selected = jobs[: cfg.analysis.max_jobs_analyzed]
    log.info("analyzing %d failed job(s) from run %s", len(selected), run_id)
    results = []
    for job in selected:
        job.log = provider.fetch_job_log(job)
        results.append(_process(job, cfg))
    return run, provider, results


def _run_from_file(path: Path) -> Run:
    """Load a raw job log into the domain model for offline replay."""
    log = path.read_text()
    job = Job(id="local", name=path.stem, status="failed",
              failure_reason=FailureReason.UNKNOWN, log=log)
    return Run(id="local", jobs=[job])


def _process(job: Job, cfg):
    """Segment + classify + assemble evidence + produce the report for one job.

    Deterministic when the LLM is disabled/unconfigured; one LLM call otherwise.
    GitLab log format is assumed (the only provider today); a provider-driven
    segmenter arrives with M6.
    """
    from ci_doctor.core.analyze import build_bundle
    from ci_doctor.core.attribution import attribute
    from ci_doctor.core.phases import assign_phases
    from ci_doctor.llm.report import produce_report

    job.sections = _make_segmenter(cfg).segment(job.log or "")
    log.debug("job %s: %d top-level sections", job.name, len(job.sections))
    assign_phases(job.sections, cfg.phases)
    attr = attribute(job, job.sections)
    log.debug("job %s: attribution phase=%s reason=%s rule=%s confidence=%s",
              job.name, attr.phase, attr.reason, attr.rule_id, attr.confidence)
    bundle = build_bundle(job, attr, job.sections, cfg)
    report = produce_report(job, attr, bundle, cfg)
    log.debug("job %s: report category=%s confidence=%s infra=%s",
              job.name, report.category, report.confidence, report.is_infra_not_code)
    return job, attr, report


def _deliver(results, cfg, *, no_color: bool) -> None:
    import json
    import os

    from ci_doctor.render.markdown import MarkdownRenderer
    from ci_doctor.render.terminal import render_terminal

    if not results:
        typer.echo("no failed jobs to analyze.")
        return

    reports = [report for *_, report in results]
    if cfg.output.terminal:
        wrap = os.environ.get("GITLAB_CI") == "true"  # collapsible only inside GitLab CI
        for report in reports:
            render_terminal(report, no_color=no_color, wrap_section=wrap)

    md = "\n\n---\n\n".join(MarkdownRenderer().render(r) for r in reports)
    Path(cfg.output.markdown_path).write_text(md)
    Path(cfg.output.json_path).write_text(json.dumps([r.model_dump(mode="json") for r in reports], indent=2))
    typer.echo(f"wrote {cfg.output.markdown_path} and {cfg.output.json_path}", err=True)


def _maybe_post_mr(provider, run, results, cfg) -> None:
    if not cfg.output.mr_note or provider is None or run is None or run.mr is None:
        return
    reports = [report for *_, report in results]
    if not any(r.confidence in ("medium", "high") for r in reports):  # user's gate
        typer.echo("MR note skipped: confidence below medium.", err=True)
        return

    from ci_doctor.render.markdown import MarkdownRenderer

    body = "\n\n---\n\n".join(MarkdownRenderer().render(r) for r in reports)
    marker = f"<!-- ci-doctor:pipeline:{run.id} -->"
    try:
        provider.post_note(run.mr, body, marker)
        typer.echo("posted/updated MR note.", err=True)
    except Exception as exc:  # noqa: BLE001 - delivery must never break the run
        typer.echo(f"MR note failed (ignored): {exc}", err=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="ci-doctor [%(levelname)s] %(message)s")
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
