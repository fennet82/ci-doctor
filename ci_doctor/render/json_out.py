"""Render a Report as JSON (report.json artifact)."""

from __future__ import annotations

from ci_doctor.core.ports import Renderer
from ci_doctor.llm.schema import Report


class JsonRenderer(Renderer):
    def render(self, report: Report) -> str:
        return report.model_dump_json(indent=2)
