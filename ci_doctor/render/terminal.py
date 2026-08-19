"""Terminal rendering with rich. Readable inside the CI job log itself.

Respects NO_COLOR / non-TTY / --no-color (rich auto-detects; `no_color` forces
it). When running in GitLab CI the whole report is wrapped in a collapsible
section so it doesn't dominate the job log.

All log-derived text is rendered as rich `Text` (markup disabled). Log lines are
full of ``[...]`` (``[error]``, ``[gw0]``, ANSI remnants) which rich would
otherwise treat as style markup — mangling content or raising on malformed tags.
Colour is deliberately restrained: section headers, a status line, and severity
cues only.
"""

import time
from typing import IO

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from ci_doctor.core.models import Run
from ci_doctor.llm.schema import RemediationStep, Report
from ci_doctor.pipeline import JobResult
from ci_doctor.render.pipeline import header_line, job_verdict

#: Name of the collapsible section wrapping the report inside a GitLab job log.
_SECTION = "ci_doctor_report"

#: Traffic-light cue on the confidence field, so a low-confidence verdict looks
#: like one at a glance.
_CONFIDENCE_STYLE = {"high": "green", "medium": "yellow", "low": "red"}

_LABEL = "bold cyan"
_SUBLABEL = "bold"  # headers nested inside the evidence panel


def render_terminal(
    report: Report,
    *,
    no_color: bool = False,
    wrap_section: bool = False,
    file: IO[str] | None = None,
) -> None:
    """Print a report to the terminal.

    Args:
        report: The validated, already-redacted report.
        no_color: Force plain text. Rich also auto-detects NO_COLOR and non-TTY.
        wrap_section: Emit GitLab collapsible-section markers around the output,
            so the report doesn't dominate the job log.
        file: Output stream. Defaults to stdout.
    """
    console = Console(no_color=no_color, file=file, highlight=False, soft_wrap=True)
    ts = int(time.time())
    if wrap_section:
        console.file.write(f"\x1b[0Ksection_start:{ts}:{_SECTION}[collapsed=true]\r\x1b[0K")

    # Border cues whether it's the code's fault (red) or infrastructure (yellow).
    border = "yellow" if report.is_infra_not_code else "red"
    header = Text()
    header.append(report.summary + "\n", style="bold")
    header.append("phase=")
    header.append(str(report.failure_phase), style="cyan")
    header.append("  category=")
    header.append(report.category, style="magenta")
    header.append("  confidence=")
    header.append(report.confidence, style=_CONFIDENCE_STYLE.get(report.confidence, "white"))
    if report.is_infra_not_code:
        header.append("  infra-not-code", style="bold yellow")
    if report.likely_flaky:
        header.append("  likely-flaky", style="bold magenta")
    console.print(
        Panel(header, title="ci-doctor", title_align="left", border_style=border, expand=True),
        end="\n\n",
        soft_wrap=False,
    )

    console.print(Text("Root cause", style=_LABEL))
    console.print(Text(report.root_cause), end="\n\n")
    if report.contributing_factors:
        console.print(Text("Contributing factors", style=_LABEL))
        _print_each(console, [Text(f"  • {f}") for f in report.contributing_factors])
    if report.evidence:
        console.print(Text("Evidence", style=_LABEL))
        for e in report.evidence:
            body = Text(overflow="fold")  # fold, so nothing is ever cropped
            body.append("Description\n", style=_SUBLABEL)
            body.append(e.why_it_matters + "\n\n")
            body.append("Error\n", style=_SUBLABEL)
            body.append(e.excerpt, style="red")  # the raw log lines backing the description
            console.print(Text(e.section, style=_SUBLABEL))
            # soft_wrap=False overrides the console default so the panel wraps
            # long lines instead of cropping them at the border.
            console.print(Panel(body, border_style="blue", expand=True), end="\n\n", soft_wrap=False)
    if report.remediation:
        console.print(Text("Remediation", style=_LABEL))
        _print_each(
            console,
            [
                Text.assemble((f"  {step.order}. ", "bold green"), _step_text(step))
                for step in report.remediation
            ],
        )

    console.print(Text("Handoff prompt (copy to your coding agent)", style=_LABEL))
    console.print(Text(report.handoff_prompt), end="\n\n")

    if wrap_section:
        console.file.write(f"\x1b[0Ksection_end:{ts}:{_SECTION}\r\x1b[0K\n")


def _job_rule(console: Console, jr: JobResult) -> None:
    """Print the heavy separator that opens one job's section.

    Args:
        console: The output console.
        jr: The analyzed job.

    The rule is the answer to "where does one job end and the next begin" — the
    complaint that started this. Red for a code fault, yellow for infrastructure,
    with the verdict inline so the header alone places the failure.
    """
    color = "yellow" if jr.report.is_infra_not_code else "red"
    title = Text()
    title.append(f" {jr.job.name} ", style=f"bold {color}")
    if jr.job.stage:
        title.append(f"({jr.job.stage}) ", style="dim")
    title.append(f"{job_verdict(jr)} ", style=color)
    console.print(Rule(title=title, characters="═", style=color, align="left"), end="\n\n")


def _pipeline_header(console: Console, run: Run, results: list[JobResult]) -> None:
    """Print the one-line pipeline header, the link, and the job index.

    Args:
        console: The output console.
        run: The analyzed run.
        results: The analyzed jobs, in pipeline order.
    """
    console.print(Text(header_line(run.id, run.ref, run.sha, results), style="bold"))
    if run.web_url:
        console.print(Text(run.web_url, style="dim"))
    console.print(Text("  " + " · ".join(jr.job.name for jr in results), style="dim"), end="\n\n")


def render_pipeline(
    run: Run,
    results: list[JobResult],
    *,
    no_color: bool = False,
    file: IO[str] | None = None,
) -> None:
    """Render every job of a pipeline, linearly, for CI logs and pipes.

    The non-interactive view: a pipeline header and a chronological index, then
    each job under its own heavy rule. No prompts — safe to pipe or read in a CI
    log, where the reader cannot answer anyway.

    Args:
        run: The analyzed run.
        results: The analyzed jobs, in pipeline order.
        no_color: Force plain text.
        file: Output stream. Defaults to stdout.
    """
    console = Console(no_color=no_color, file=file, highlight=False, soft_wrap=True)
    _pipeline_header(console, run, results)
    for i, jr in enumerate(results):
        if i:
            console.print("\n")  # blank lines between jobs, so each section stands apart
        _job_rule(console, jr)
        render_terminal(jr.report, no_color=no_color, wrap_section=False, file=file)


def _print_each(console: Console, items: list[Text]) -> None:
    """Print a list, leaving one blank line after the last item only.

    Args:
        console: The output console.
        items: Renderables. Must not be empty.
    """
    for item in items[:-1]:
        console.print(item)
    console.print(items[-1], end="\n\n")


def _step_text(step: RemediationStep) -> Text:
    """Format one remediation step.

    Args:
        step: A :class:`~ci_doctor.llm.schema.RemediationStep`.

    Returns:
        The action, with any `where` appended in colour — a file:line is the
        actionable part, not chrome.
    """
    text = Text(step.action)
    if step.where:
        text.append(f" ({step.where})", style="cyan")  # a file:line is actionable, not chrome
    return text
