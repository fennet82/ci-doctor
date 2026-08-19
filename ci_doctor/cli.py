"""ci-doctor CLI (typer): argument parsing, then delivery.

`analyze <pipeline_id>` against a live provider, or `analyze <path/to/log>` to replay
a raw log offline. Both hand straight off to :mod:`ci_doctor.pipeline`, which is
where the analysis itself lives — a caller that is not a terminal (an editor, an
agent, a future `mcp` command) needs the same run without typer in the call stack.
What stays here is what only a CLI needs: options, rendering, artifacts, the note.

Invariant #3: the analyzer must never change a pipeline's outcome, so `main()`
catches everything and always exits 0.
"""

import difflib
import json
import logging
import os
import sys
from enum import StrEnum
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.style import Style
from rich.syntax import Syntax
from rich.theme import Theme

from ci_doctor import __version__
from ci_doctor.config.loader import default_config, load_config
from ci_doctor.config.schema import Config
from ci_doctor.core.models import Run
from ci_doctor.pipeline import JobResult, analyze_log, analyze_run
from ci_doctor.providers.registry import make_scm_provider

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
    interactive: bool | None = typer.Option(
        None,
        "--interactive/--no-interactive",
        help="Pick jobs from a menu (default: on when attached to a terminal outside CI).",
    ),
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
        interactive: Force the job menu on or off. Left unset, it is auto-detected:
            on for a real terminal outside CI, off in CI, a pipe, or `--format json`.
        verbose: Enable debug logging.
    """
    _configure_logging(verbose)
    cfg = load_config(repo_config=config_path)
    log.debug("ci=%s scm=%s target=%s job_id=%s", cfg.ci, cfg.scm_vendor, target, job_id)

    run = provider = None
    if target is not None and Path(target).is_file():
        results = analyze_log(Path(target), cfg)
    elif target is not None and _looks_like_a_path(target):
        typer.echo(f"no such log file: {target}", err=True)
        return
    elif target is not None:
        run, provider, results = analyze_run(target, cfg, job_id)
    else:
        typer.echo("nothing to do: pass a pipeline id, or the path to a log to replay.", err=True)
        return

    _deliver(
        results,
        cfg,
        run=run,
        no_color=no_color,
        output_format=output_format,
        interactive=_is_interactive(output_format, interactive),
    )
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
    return json.dumps(Config.model_json_schema(), indent=2)


def _as_yaml(cfg: Config) -> str:
    """Dump a validated config back to YAML.

    Args:
        cfg: A :class:`~ci_doctor.config.schema.Config`.

    Returns:
        YAML text in schema field order, so two dumps diff cleanly.
    """
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
    diff = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=1)
    return [line.rstrip("\n") for line in diff][2:]


_CI_ENV = ("CI", "GITHUB_ACTIONS", "GITLAB_CI")


def _in_ci() -> bool:
    """Whether we are running inside a CI system.

    Returns:
        True if any of the usual CI markers is set. `CI` alone covers GitHub and
        GitLab, which both export `CI=true`; the others are belt and braces.
    """
    return any(os.environ.get(v) for v in _CI_ENV)


def _is_interactive(output_format: OutputFormat, override: bool | None) -> bool:
    """Decide whether the job menu should run.

    Args:
        output_format: JSON is a payload, never interactive.
        override: `--interactive/--no-interactive`, or None to auto-detect.

    Returns:
        The override when given; otherwise True only for a real terminal outside
        CI. A pipe, a CI log, or `--format json` all read non-interactively —
        nobody is there to answer the prompt.
    """
    if output_format is OutputFormat.JSON:
        return False
    if override is not None:
        return override
    return sys.stdout.isatty() and not _in_ci()


def _pipeline_markdown(run: Run | None, results: list[JobResult]) -> str:
    """The Markdown for the artifact and the MR note.

    Args:
        run: The analyzed run, or None for offline replay.
        results: One :class:`~ci_doctor.pipeline.JobResult` per analyzed job.

    Returns:
        A pipeline document (header + a section per job) for a live run of more
        than one job; a single report otherwise, so replay and one-job runs stay
        unframed.
    """
    from ci_doctor.render.markdown import MarkdownRenderer, render_pipeline_markdown

    if run is not None and len(results) > 1:
        return render_pipeline_markdown(run, results)
    return "\n\n---\n\n".join(MarkdownRenderer().render(r.report) for r in results)


def _deliver(
    results: list[JobResult],
    cfg: Config,
    *,
    run: Run | None,
    no_color: bool,
    output_format: OutputFormat = OutputFormat.TERMINAL,
    interactive: bool = False,
) -> None:
    """Render the reports to stdout and write the artifacts.

    Args:
        results: One :class:`~ci_doctor.pipeline.JobResult` per analyzed job.
        cfg: The effective config, supplying the output paths.
        run: The analyzed run, or None for offline replay. Present unlocks the
            pipeline framing — a header and a job menu or per-job separators.
        no_color: Disable coloured terminal output.
        output_format: JSON puts the report on stdout instead of the panels, for a
            script or an agent to read. The artifacts are written either way, and
            every human-facing line already goes to stderr, so stdout stays parseable.
        interactive: Offer the job menu instead of printing every job linearly.
    """
    from ci_doctor.render.terminal import render_pipeline, render_terminal, select_and_show

    if not results:
        typer.echo("no failed jobs to analyze.")
        return

    payload = [r.report.model_dump(mode="json") for r in results]
    multi_job = run is not None and len(results) > 1
    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(payload, indent=2))
    elif cfg.output.terminal:
        wrap = os.environ.get("GITLAB_CI") == "true"  # collapsible only inside GitLab CI
        if interactive and multi_job:
            select_and_show(run, results, no_color=no_color)
        elif multi_job:
            render_pipeline(run, results, no_color=no_color, wrap_section=wrap)
        else:
            for r in results:
                render_terminal(r.report, no_color=no_color, wrap_section=wrap)

    Path(cfg.output.markdown_path).write_text(_pipeline_markdown(run, results))
    Path(cfg.output.json_path).write_text(json.dumps(payload, indent=2))
    typer.echo(f"wrote {cfg.output.markdown_path} and {cfg.output.json_path}", err=True)


def _maybe_post_mr(ci_provider: object, run: Run | None, results: list[JobResult], cfg: Config) -> None:
    """Post the report as an MR/PR note, when configured and confident enough.

    Gated on medium-or-better confidence: a low-confidence guess posted on
    someone's MR is worse than no comment. Delivery failures are swallowed —
    invariant #3 means a broken note must not change the pipeline's outcome.

    Args:
        ci_provider: The CI adapter, or None for offline replay. Passed only so
            the git host can reuse its client when one vendor serves both roles.
        run: The analyzed run, or None.
        results: One :class:`~ci_doctor.pipeline.JobResult` per analyzed job.
        cfg: The effective config.
    """
    if not cfg.output.mr_note or run is None or run.mr is None:
        return
    reports = [r.report for r in results]
    if not any(r.confidence in ("medium", "high") for r in reports):  # user's gate
        typer.echo("MR note skipped: confidence below medium.", err=True)
        return

    scm = make_scm_provider(cfg, ci_provider)
    if scm is None:
        typer.echo(f"MR note skipped: no git-host adapter for {cfg.scm_vendor!r}.", err=True)
        return

    body = _pipeline_markdown(run, results)
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
