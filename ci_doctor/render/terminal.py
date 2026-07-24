"""Terminal rendering with rich. Readable inside the CI job log itself.

Respects NO_COLOR / non-TTY / --no-color (rich auto-detects; `no_color` forces
it). When running in GitLab CI the whole report is wrapped in a collapsible
section so it doesn't dominate the job log.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel

from ci_doctor.llm.schema import Report

_SECTION = "ci_doctor_report"


def render_terminal(report: Report, *, no_color: bool = False, wrap_section: bool = False, file=None) -> None:
    console = Console(no_color=no_color, file=file, highlight=False, soft_wrap=True)
    ts = int(time.time())
    if wrap_section:
        console.file.write(f"\x1b[0Ksection_start:{ts}:{_SECTION}[collapsed=true]\r\x1b[0K")

    infra = " · infra-not-code" if report.is_infra_not_code else ""
    flaky = " · likely-flaky" if report.likely_flaky else ""
    console.print(Panel(
        f"[bold]{report.summary}[/]\n"
        f"phase=[cyan]{report.failure_phase}[/] category={report.category} "
        f"confidence={report.confidence}{infra}{flaky}",
        title="ci-doctor", expand=False,
    ))
    console.print("[bold]Root cause[/]")
    console.print(report.root_cause)
    if report.contributing_factors:
        console.print("[bold]Contributing factors[/]")
        for factor in report.contributing_factors:
            console.print(f"  • {factor}")
    if report.evidence:
        console.print("[bold]Evidence[/]")
        for e in report.evidence:
            console.print(Panel(e.excerpt, title=e.section, subtitle=e.why_it_matters, expand=False))
    if report.remediation:
        console.print("[bold]Remediation[/]")
        for step in report.remediation:
            where = f" [dim]({step.where})[/]" if step.where else ""
            console.print(f"  [green]{step.order}.[/] {step.action}{where}")

    if wrap_section:
        console.file.write(f"\x1b[0Ksection_end:{ts}:{_SECTION}\r\x1b[0K\n")
