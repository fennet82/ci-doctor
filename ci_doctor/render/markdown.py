"""Render a Report as Markdown (report.md artifact and MR-note body)."""

from ci_doctor.core.models import Run
from ci_doctor.core.ports import Renderer
from ci_doctor.llm.schema import Report
from ci_doctor.pipeline import JobResult
from ci_doctor.render.pipeline import header_line


def _yn(value: bool) -> str:
    """Render a boolean as the table cell a reader can scan."""
    return "yes" if value else "no"


class MarkdownRenderer(Renderer):
    """Emits the report as Markdown — the `report.md` artifact and MR-note body."""

    def render(self, report: Report, heading: str | None = None) -> str:
        """Render a report.

        Args:
            report: The validated, already-redacted report.
            heading: The section heading. Defaults to a standalone `## ci-doctor
                — <summary>`; a pipeline passes its own so each job is a section
                under the pipeline title.

        Returns:
            Markdown: a summary table, then root cause, contributing factors,
            evidence, remediation, related paths and the handoff prompt. Empty
            sections are omitted rather than rendered as headings with nothing
            under them.
        """
        out = [
            heading or f"## ci-doctor — {report.summary}",
            "",
            "| Phase | Category | Confidence | Infra not code | Likely flaky |",
            "|---|---|---|---|---|",
            f"| {report.failure_phase} | {report.category} | {report.confidence} | "
            f"{_yn(report.is_infra_not_code)} | {_yn(report.likely_flaky)} |",
            "",
            "### Root cause",
            report.root_cause,
            "",
        ]
        if report.contributing_factors:
            out += ["### Contributing factors", *[f"- {c}" for c in report.contributing_factors], ""]
        if report.evidence:
            out.append("### Evidence")
            for e in report.evidence:
                out += [f"**{e.section}** — {e.why_it_matters}", "", "```", e.excerpt, "```", ""]
        if report.remediation:
            out.append("### Remediation")
            for s in report.remediation:
                where = f" — `{s.where}`" if s.where else ""
                out.append(f"{s.order}. {s.action}{where}")
            out.append("")
        if report.related_paths:
            out += ["### Related paths", *[f"- `{p}`" for p in report.related_paths], ""]
        out += ["### Handoff prompt", "", "```", report.handoff_prompt, "```", ""]
        return "\n".join(out)


def render_pipeline_markdown(run: Run, results: list[JobResult]) -> str:
    """Render a whole pipeline as one Markdown document.

    A pipeline title and triage line, then each failed job as its own `##`
    section in pipeline order — so the `report.md` artifact and the MR/PR note
    read as one document, not a stack of unlabelled reports concatenated.

    Args:
        run: The analyzed run.
        results: The analyzed jobs, in pipeline order.

    Returns:
        The combined Markdown.
    """
    renderer = MarkdownRenderer()
    parts = [f"# ci-doctor — pipeline {run.id}", "", header_line(run.id, run.ref, run.sha, results), ""]
    if run.web_url:
        parts += [f"[View pipeline]({run.web_url})", ""]
    for jr in results:
        stage = f" · {jr.job.stage}" if jr.job.stage else ""
        heading = f"## {jr.job.name}{stage} — {jr.report.summary}"
        parts.append(renderer.render(jr.report, heading=heading))
    return "\n\n".join(parts)
