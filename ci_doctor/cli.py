"""ci-doctor CLI (typer).

`analyze <pipeline_id>` against a live provider, or `analyze <path/to/log>` to replay
a raw log offline. Both run the same pipeline: segment -> classify -> evidence ->
report -> render/deliver.

Invariant #3: the analyzer must never change a pipeline's outcome, so `main()`
catches everything and always exits 0.
"""

import logging
import os
import sys
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.style import Style
from rich.syntax import Syntax
from rich.theme import Theme

from ci_doctor import __version__
from ci_doctor.config.loader import default_config, load_config
from ci_doctor.core.models import FailureReason, Job, Run

app = typer.Typer(add_completion=False, help="Explain why a CI pipeline failed (read-only postmortem).")

log = logging.getLogger("ci_doctor.cli")


class OutputFormat(StrEnum):
    """What `analyze` writes to stdout. The artifacts are written either way."""

    TERMINAL = "terminal"
    JSON = "json"


def _configure_logging(verbose: bool) -> None:
    """`--verbose` (or CI_DOCTOR_LOG_LEVEL=DEBUG) turns on debug logs across ci_doctor.*."""
    level_name = os.environ.get("CI_DOCTOR_LOG_LEVEL", "").upper()
    level = logging.DEBUG if verbose else getattr(logging, level_name, logging.INFO)
    logging.getLogger("ci_doctor").setLevel(level)
    if verbose:
        log.debug("verbose logging enabled")


def _version_callback(value: bool) -> None:
    """Print the version and exit, before any other option is processed.

    Args:
        value: Whether `--version` was passed.

    Raises:
        typer.Exit: Always, when `value` is true.
    """
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


_CONFIG_OPTION = typer.Option(
    None,
    "--config",
    "-f",
    help="Path to a .ci-doctor.yml. Repeatable — later files override earlier ones.",
)


@app.command()
def analyze(
    target: str = typer.Argument(
        None, help="Pipeline/run id, or the path to a raw job log to replay offline."
    ),
    job_id: str = typer.Option(None, "--job-id", help="Analyze a single job id."),
    config_path: list[Path] = _CONFIG_OPTION,
    output_format: OutputFormat = typer.Option(
        OutputFormat.TERMINAL,
        "--format",
        help="stdout format. `json` prints the report instead of the rendered panels.",
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable coloured terminal output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    """Explain why a CI run failed.

    Either replays a captured log offline (an existing path) or fetches a live run
    (anything else). Both take the same pipeline: segment, classify, extract
    evidence, report, render. Never changes the run's outcome.

    Args:
        target: A pipeline/run id, or a path to a raw job log.
        job_id: Restrict a live run to one job.
        config_path: Explicit `.ci-doctor.yml` files, lowest precedence first.
        output_format: What goes to stdout — the rendered panels, or the report
            as JSON for a script or a coding agent to read.
        no_color: Disable coloured terminal output.
        verbose: Enable debug logging.
    """
    _configure_logging(verbose)
    cfg = load_config(repo_config=config_path)
    log.debug("ci=%s scm=%s target=%s job_id=%s", cfg.ci, cfg.scm_vendor, target, job_id)

    run = provider = None
    if target is not None and Path(target).is_file():
        jobs = _run_from_file(Path(target)).jobs
        log.info("analyzing %d job(s) from %s", len(jobs), target)
        results = [_process(job, cfg) for job in jobs]
    elif target is not None and _looks_like_a_path(target):
        typer.echo(f"no such log file: {target}", err=True)
        return
    elif target is not None:
        run, provider, results = _analyze_live(target, cfg, job_id)
    else:
        typer.echo("nothing to do: pass a pipeline id, or the path to a log to replay.", err=True)
        return

    _deliver(results, cfg, no_color=no_color, output_format=output_format)
    _maybe_post_mr(provider, run, results, cfg)


@app.command()
def config(
    config_path: list[Path] = _CONFIG_OPTION,
    diff: bool = typer.Option(
        False, "--diff", help="Show only what your config changes against the shipped defaults."
    ),
    schema: bool = typer.Option(False, "--schema", help="Print the JSON Schema for .ci-doctor.yml and exit."),
    validate: bool = typer.Option(
        False, "--validate", help="Load and validate the merged config, then report what failed."
    ),
    less: bool = typer.Option(False, "--less", help="Force the scrollable pager, even when piped."),
    plain: bool = typer.Option(False, "--plain", help="Print straight to stdout instead of paging."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable coloured output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    """Show the effective config after every layer is merged.

    Useful for answering "why is it doing that" — the answer is almost always a
    layer you forgot was applied. Pages the output on a terminal; pipe it (or pass
    `--plain`) to get the raw text back.
    """
    _configure_logging(verbose)
    if validate:
        _validate(config_path)
        return
    if schema:
        text, lexer = _schema_json(), "json"
    elif not diff:
        text, lexer = _as_yaml(load_config(repo_config=config_path)), "yaml"
    else:
        lines = _diff_lines(_as_yaml(default_config()), _as_yaml(load_config(repo_config=config_path)))
        text, lexer = "\n".join(lines) or "no differences: running on the shipped defaults.", "diff"

    if plain or (not less and not sys.stdout.isatty()):
        # Raw echo, not rich: piped output is meant to be re-parsed, and a Console
        # would pad every line to the detected terminal width.
        typer.echo(text)
        return

    os.environ.setdefault("PAGER", "less -R")  # bare `less` prints the colour escapes literally
    console = Console(soft_wrap=True, no_color=no_color)  # soft_wrap: never fold long regexes
    with console.pager(styles=not no_color):
        console.print(Syntax(text, lexer, theme="ansi_dark", background_color="default"))


def _validate(paths: list[Path]) -> None:
    """Merge every config layer and report whether the result validates.

    Args:
        paths: The `--config/-f` files, lowest precedence first.

    Note:
        Reports to stderr and still exits 0 — invariant #3 applies to every
        subcommand, so grep the output rather than the exit code.
    """
    try:
        cfg = load_config(repo_config=paths)
    except Exception as exc:  # noqa: BLE001 - a bad config is a message, not a traceback
        typer.echo(f"invalid config: {exc}", err=True)
        return
    typer.echo(f"config ok: ci={cfg.ci}, scm={cfg.scm_vendor}, {len(cfg.extraction.matchers)} matchers.")


def _looks_like_a_path(target: str) -> bool:
    """Whether a missing `analyze` target was meant as a log file, not a run id.

    Args:
        target: The positional argument as typed.

    Returns:
        True for anything carrying a separator or a suffix, so a typo'd path
        reports "no such log file" instead of being fetched as a pipeline id.
    """
    return os.sep in target or "/" in target or bool(Path(target).suffix)


def _schema_json() -> str:
    """Render the config JSON Schema.

    Published as a release asset so editors can complete and validate a
    ``.ci-doctor.yml`` against the exact version in use.

    Returns:
        The pretty-printed JSON Schema document.
    """
    import json

    from ci_doctor.config.schema import Config

    return json.dumps(Config.model_json_schema(), indent=2)


def _as_yaml(cfg) -> str:
    """Dump a validated config back to YAML.

    Args:
        cfg: A :class:`~ci_doctor.config.schema.Config`.

    Returns:
        YAML text in schema field order, so two dumps diff cleanly.
    """
    import yaml

    return yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def _diff_lines(before: str, after: str) -> list[str]:
    """Unified diff of the shipped defaults against the effective config.

    Args:
        before: YAML dump of the shipped defaults.
        after: YAML dump of the effective config.

    Returns:
        Diff lines without the ``---``/``+++`` file headers (they carry no
        information here) and without trailing newlines; empty when identical.
    """
    import difflib

    diff = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=1)
    return [line.rstrip("\n") for line in diff][2:]


def _adapter(vendor: str, cfg):
    """Build the adapter for one vendor, whichever ports it implements.

    A vendor may be a CI system, a git host, or — for GitLab and GitHub — both
    over a single client. The caller checks which ports it got.

    Args:
        vendor: The vendor name from `ci` or `scm`.
        cfg: The effective config.

    Returns:
        The adapter, or None when no adapter exists for that name.
    """
    # Import adapters lazily so `core` and the offline path never pull a vendor in.
    if vendor == "gitlab":
        from ci_doctor.providers.gitlab.provider import GitLabProvider

        return GitLabProvider(cfg)
    if vendor == "github":
        from ci_doctor.providers.github.provider import GitHubProvider

        return GitHubProvider(cfg)
    return None


def _make_ci_provider(cfg):
    """Build the adapter that reads the run, named by `config.ci`.

    Args:
        cfg: The effective config.

    Returns:
        A :class:`~ci_doctor.core.ports.CIProvider`.

    Raises:
        ValueError: When no adapter reads that CI system.
    """
    from ci_doctor.core.ports import CIProvider

    adapter = _adapter(cfg.ci, cfg)
    if not isinstance(adapter, CIProvider):
        raise ValueError(f"unsupported CI system: {cfg.ci}")
    return adapter


def _make_scm_provider(cfg, ci_provider=None):
    """Build the adapter that posts the note, named by `config.scm`.

    Args:
        cfg: The effective config.
        ci_provider: The already-connected CI adapter. Reused when the same
            vendor serves both roles — GitLab's pipelines and merge requests are
            one API behind one client, and connecting twice would mean a second
            token read and a second version probe for nothing.

    Returns:
        An :class:`~ci_doctor.core.ports.SCMProvider`, or None when the note has
        nowhere to go — which is not an error, the report still lands in the
        terminal and the artifacts.
    """
    from ci_doctor.core.ports import SCMProvider

    vendor = cfg.scm_vendor
    if vendor == "none":
        return None
    if vendor == cfg.ci and isinstance(ci_provider, SCMProvider):
        return ci_provider
    adapter = _adapter(vendor, cfg)
    if not isinstance(adapter, SCMProvider):
        log.debug("no git-host adapter for %r; the note has nowhere to go", vendor)
        return None
    return adapter


def _make_segmenter(cfg):
    """Build the log segmenter for the configured CI system.

    Keyed on `ci`, never on the git host: log framing is whatever the *runner*
    printed. That is also why one segmenter can serve several CI systems — Forgejo
    and Gitea Actions run act_runner, which emits GitHub's `##[group]` markers.

    Args:
        cfg: The effective config.

    Returns:
        A :class:`~ci_doctor.core.ports.LogSegmenter`, defaulting to GitLab's —
        offline replay has no run metadata to go on.
    """
    if cfg.ci == "github":
        from ci_doctor.providers.github.segmenter import GitHubSegmenter

        return GitHubSegmenter()
    from ci_doctor.providers.gitlab.segmenter import GitLabSegmenter

    return GitLabSegmenter()


def _analyze_live(run_id: str, cfg, job_id: str | None):
    """Fetch a run from the provider and analyze its failed jobs.

    Args:
        run_id: The provider's run/pipeline id.
        cfg: The effective config.
        job_id: Restrict to a single job id, or None for all failed ones.

    Returns:
        A ``(run, provider, results)`` tuple. The provider is returned so the
        caller can post an MR note without reconnecting.
    """
    from ci_doctor.core.select import select_failed_jobs

    provider = _make_ci_provider(cfg)
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
    """Load a raw job log into the domain model for offline replay.

    Args:
        path: The log file.

    Returns:
        A synthetic single-job run. The failure reason is UNKNOWN — a bare log
        carries no provider metadata — which sends attribution down its
        structural fallback rules.

    Raises:
        OSError: If the file cannot be read.
    """
    log = path.read_text()
    job = Job(id="local", name=path.stem, status="failed", failure_reason=FailureReason.UNKNOWN, log=log)
    return Run(id="local", jobs=[job])


def _process(job: Job, cfg):
    """Segment, classify, assemble evidence and produce the report for one job.

    Deterministic when the LLM is disabled or unconfigured; one LLM call otherwise.

    Args:
        job: The failed job, log already attached.
        cfg: The effective config.

    Returns:
        A ``(job, attribution, report)`` tuple.
    """
    from ci_doctor.core.analyze import build_bundle
    from ci_doctor.core.attribution import attribute
    from ci_doctor.core.phases import assign_phases
    from ci_doctor.llm.report import produce_report

    job.sections = _make_segmenter(cfg).segment(job.log or "")
    log.debug("job %s: %d top-level sections", job.name, len(job.sections))
    assign_phases(job.sections, cfg.phases)
    attr = attribute(job, job.sections)
    log.debug(
        "job %s: attribution phase=%s reason=%s rule=%s confidence=%s",
        job.name,
        attr.phase,
        attr.reason,
        attr.rule_id,
        attr.confidence,
    )
    bundle = build_bundle(job, attr, job.sections, cfg)
    report = produce_report(job, attr, bundle, cfg)
    log.debug(
        "job %s: report category=%s confidence=%s infra=%s",
        job.name,
        report.category,
        report.confidence,
        report.is_infra_not_code,
    )
    return job, attr, report


def _deliver(results, cfg, *, no_color: bool, output_format: OutputFormat = OutputFormat.TERMINAL) -> None:
    """Render the reports to stdout and write the artifacts.

    Args:
        results: ``(job, attribution, report)`` tuples.
        cfg: The effective config, supplying the output paths.
        no_color: Disable coloured terminal output.
        output_format: JSON puts the report on stdout instead of the panels, for a
            script or an agent to read. The artifacts are written either way, and
            every human-facing line already goes to stderr, so stdout stays parseable.
    """
    import json
    import os

    from ci_doctor.render.markdown import MarkdownRenderer
    from ci_doctor.render.terminal import render_terminal

    if not results:
        typer.echo("no failed jobs to analyze.")
        return

    reports = [report for *_, report in results]
    payload = [r.model_dump(mode="json") for r in reports]
    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(payload, indent=2))
    elif cfg.output.terminal:
        wrap = os.environ.get("GITLAB_CI") == "true"  # collapsible only inside GitLab CI
        for report in reports:
            render_terminal(report, no_color=no_color, wrap_section=wrap)

    md = "\n\n---\n\n".join(MarkdownRenderer().render(r) for r in reports)
    Path(cfg.output.markdown_path).write_text(md)
    Path(cfg.output.json_path).write_text(json.dumps(payload, indent=2))
    typer.echo(f"wrote {cfg.output.markdown_path} and {cfg.output.json_path}", err=True)


def _maybe_post_mr(ci_provider, run, results, cfg) -> None:
    """Post the report as an MR/PR note, when configured and confident enough.

    Gated on medium-or-better confidence: a low-confidence guess posted on
    someone's MR is worse than no comment. Delivery failures are swallowed —
    invariant #3 means a broken note must not change the pipeline's outcome.

    Args:
        ci_provider: The CI adapter, or None for offline replay. Passed only so
            the git host can reuse its client when one vendor serves both roles.
        run: The analyzed run, or None.
        results: ``(job, attribution, report)`` tuples.
        cfg: The effective config.
    """
    if not cfg.output.mr_note or run is None or run.mr is None:
        return
    reports = [report for *_, report in results]
    if not any(r.confidence in ("medium", "high") for r in reports):  # user's gate
        typer.echo("MR note skipped: confidence below medium.", err=True)
        return

    scm = _make_scm_provider(cfg, ci_provider)
    if scm is None:
        typer.echo(f"MR note skipped: no git-host adapter for {cfg.scm_vendor!r}.", err=True)
        return

    from ci_doctor.render.markdown import MarkdownRenderer

    body = "\n\n---\n\n".join(MarkdownRenderer().render(r) for r in reports)
    marker = f"<!-- ci-doctor:pipeline:{run.id} -->"
    try:
        scm.post_note(run.mr, body, marker)
        typer.echo("posted/updated MR note.", err=True)
    except Exception as exc:  # noqa: BLE001 - delivery must never break the run
        typer.echo(f"MR note failed (ignored): {exc}", err=True)


_LEVEL_COLORS = Theme(
    {
        "logging.level.debug": Style(color="blue"),
        "logging.level.info": Style(color="green"),
        "logging.level.warning": Style(color="yellow"),
        "logging.level.error": Style(color="red"),
        "logging.level.critical": Style(color="red", bold=True),
    }
)


def main() -> None:
    """Console-script entry point.

    Invariant #3: catches everything and always exits 0, so a crash in the
    analyzer can never turn a passing pipeline red.
    """
    # NullHighlighter: rich otherwise repr-highlights the message body (numbers
    # cyan, words yellow/magenta), which drowns out the level colour. Passing
    # highlighter=None does NOT disable it — rich falls back to ReprHighlighter.
    #
    # stderr=True: stdout is the payload — the rendered report, or the JSON that
    # `--format json` exists to be piped into something. A log line there corrupts
    # it. Diagnostics belong on stderr, where `typer.echo(..., err=True)` already
    # puts every other human-facing message.
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            RichHandler(
                console=Console(theme=_LEVEL_COLORS, stderr=True),
                show_time=False,
                show_path=False,
                markup=False,
                highlighter=NullHighlighter(),
            )
        ],
    )
    try:
        app()
    except SystemExit:
        # typer/click signal both normal and usage exits via SystemExit; force 0.
        raise SystemExit(0) from None
    except BaseException as exc:  # noqa: BLE001 - analyzer must never alter pipeline outcome
        print(f"ci-doctor: internal error, exiting 0 to preserve pipeline status: {exc}", file=sys.stderr)
        raise SystemExit(0) from None


if __name__ == "__main__":
    main()
