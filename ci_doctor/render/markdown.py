"""Render a Report as Markdown (report.md artifact and MR-note body)."""

from ci_doctor.core.ports import Renderer
from ci_doctor.llm.schema import Report


class MarkdownRenderer(Renderer):
    """Emits the report as Markdown — the `report.md` artifact and MR-note body."""

    def render(self, report: Report) -> str:
        """Render a report.

        Args:
            report: The validated, already-redacted report.

        Returns:
            Markdown: a summary table, then root cause, contributing factors,
            evidence, remediation, related paths and the handoff prompt. Empty
            sections are omitted rather than rendered as headings with nothing
            under them.
        """
        yn = lambda b: "yes" if b else "no"  # noqa: E731
        out = [
            f"## ci-doctor — {report.summary}",
            "",
            "| Phase | Category | Confidence | Infra not code | Likely flaky |",
            "|---|---|---|---|---|",
            f"| {report.failure_phase} | {report.category} | {report.confidence} | "
            f"{yn(report.is_infra_not_code)} | {yn(report.likely_flaky)} |",
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
