"""Render a Report as JSON (report.json artifact)."""

from ci_doctor.core.ports import Renderer
from ci_doctor.llm.schema import Report


class JsonRenderer(Renderer):
    """Emits the report as JSON — the machine-readable artifact."""

    def render(self, report: Report) -> str:
        """Serialise a report.

        Args:
            report: The validated, already-redacted report.

        Returns:
            Indented JSON matching the `Report` schema exactly, so downstream
            tooling can rely on it.
        """
        return report.model_dump_json(indent=2)
