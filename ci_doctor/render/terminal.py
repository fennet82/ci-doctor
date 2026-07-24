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

from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ci_doctor.llm.schema import Report

_SECTION = "ci_doctor_report"
_CONFIDENCE_STYLE = {"high": "green", "medium": "yellow", "low": "red"}
_LABEL = "bold cyan"


def render_terminal(report: Report, *, no_color: bool = False, wrap_section: bool = False, file=None) -> None:
    console = Console(no_color=no_color, file=file, highlight=False, soft_wrap=True)
    ts = int(time.time())
    if wrap_section:
        console.file.write(f"\x1b[0Ksection_start:{ts}:{_SECTION}[collapsed=true]\r\x1b[0K")

    # Border cues whether it's the code's fault (red) or infrastructure (yellow).
    border = "yellow" if report.is_infra_not_code else "red"
    header = Text()
    header.append(report.summary + "\n", style="bold")
    header.append("phase=", style="dim")
    header.append(str(report.failure_phase), style="cyan")
    header.append("  category=", style="dim")
    header.append(report.category, style="magenta")
    header.append("  confidence=", style="dim")
    header.append(report.confidence, style=_CONFIDENCE_STYLE.get(report.confidence, "white"))
    if report.is_infra_not_code:
        header.append("  infra-not-code", style="bold yellow")
    if report.likely_flaky:
        header.append("  likely-flaky", style="bold magenta")
    console.print(Panel(header, title="ci-doctor", title_align="left", border_style=border, expand=False))

    console.print(Text("Root cause", style=_LABEL))
    console.print(Text(report.root_cause))
    if report.contributing_factors:
        console.print(Text("Contributing factors", style=_LABEL))
        for factor in report.contributing_factors:
            console.print(Text(f"  • {factor}", style="dim"))
    if report.evidence:
        console.print(Text("Evidence", style=_LABEL))
        for e in report.evidence:
            console.print(Panel(Text(e.excerpt), title=Text(e.section, style="cyan"),
                                subtitle=Text(e.why_it_matters, style="dim"), border_style="blue", expand=False))
    if report.remediation:
        console.print(Text("Remediation", style=_LABEL))
        for step in report.remediation:
            console.print(Text.assemble((f"  {step.order}. ", "bold green"), _step_text(step)))

    if wrap_section:
        console.file.write(f"\x1b[0Ksection_end:{ts}:{_SECTION}\r\x1b[0K\n")


def _step_text(step) -> Text:
    text = Text(step.action)
    if step.where:
        text.append(f" ({step.where})", style="dim")
    return text
