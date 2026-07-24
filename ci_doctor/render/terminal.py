"""Terminal rendering with rich. Readable inside the CI job log itself.

Respects NO_COLOR / non-TTY / --no-color (rich auto-detects; `no_color` forces
it). When running in GitLab CI the whole report is wrapped in a collapsible
section so it doesn't dominate the job log.

All log-derived text is rendered as rich `Text` (markup disabled). Log lines are
full of ``[...]`` (``[error]``, ``[gw0]``, ANSI remnants) which rich would
otherwise treat as style markup — mangling content or raising on malformed tags.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ci_doctor.llm.schema import Report

_SECTION = "ci_doctor_report"


def render_terminal(report: Report, *, no_color: bool = False, wrap_section: bool = False, file=None) -> None:
    console = Console(no_color=no_color, file=file, highlight=False, soft_wrap=True)
    ts = int(time.time())
    if wrap_section:
        console.file.write(f"\x1b[0Ksection_start:{ts}:{_SECTION}[collapsed=true]\r\x1b[0K")

    tags = ""
    if report.is_infra_not_code:
        tags += " · infra-not-code"
    if report.likely_flaky:
        tags += " · likely-flaky"

    header = Text()
    header.append(report.summary + "\n", style="bold")
    header.append(f"phase={report.failure_phase} category={report.category} confidence={report.confidence}{tags}")
    console.print(Panel(header, title="ci-doctor", expand=False))

    console.print(Text("Root cause", style="bold"))
    console.print(Text(report.root_cause))
    if report.contributing_factors:
        console.print(Text("Contributing factors", style="bold"))
        for factor in report.contributing_factors:
            console.print(Text(f"  • {factor}"))
    if report.evidence:
        console.print(Text("Evidence", style="bold"))
        for e in report.evidence:
            console.print(Panel(Text(e.excerpt), title=Text(e.section), subtitle=Text(e.why_it_matters), expand=False))
    if report.remediation:
        console.print(Text("Remediation", style="bold"))
        for step in report.remediation:
            line = Text.assemble((f"  {step.order}. ", "green"), report_step_text(step))
            console.print(line)

    if wrap_section:
        console.file.write(f"\x1b[0Ksection_end:{ts}:{_SECTION}\r\x1b[0K\n")


def report_step_text(step) -> Text:
    text = Text(step.action)
    if step.where:
        text.append(f" ({step.where})", style="dim")
    return text
