"""Abstract ports. Core imports only these; adapters implement them.

Kept import-light on purpose: annotations are strings (PEP 563) and concrete
types are only pulled in under TYPE_CHECKING so importing a port never drags a
provider or the LLM stack into core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ci_doctor.core.models import Job, MergeRequestRef, Run, Section
    from ci_doctor.llm.schema import Report


class CIProvider(ABC):
    @abstractmethod
    def fetch_run(self, run_ref: str) -> "Run": ...

    @abstractmethod
    def fetch_job_log(self, job: "Job") -> str | None: ...

    @abstractmethod
    def post_note(self, mr: "MergeRequestRef", body: str, marker: str) -> None:
        """Optional capability; adapters without MR support may no-op."""


class LogSegmenter(ABC):
    @abstractmethod
    def segment(self, raw_log: str) -> list["Section"]: ...


class LLMClient(ABC):
    @abstractmethod
    def complete_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class Renderer(ABC):
    @abstractmethod
    def render(self, report: "Report") -> str: ...
