"""Abstract ports. Core imports only these; adapters implement them.

Kept import-light on purpose: annotations are strings (PEP 563) and concrete
types are only pulled in under TYPE_CHECKING so importing a port never drags a
provider or the LLM stack into core.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ci_doctor.core.models import Job, MergeRequestRef, Run, Section
    from ci_doctor.llm.schema import Report


class CIProvider(ABC):
    """Read-only access to one CI system. Implemented per provider under `providers/`.

    Guardrail #1: every method here is a *read*, except :meth:`post_note`, which
    writes only to a discussion thread. Nothing may retry, cancel or restart a job.
    """

    @abstractmethod
    def fetch_run(self, run_ref: str) -> "Run":
        """Fetch a run and its jobs.

        Args:
            run_ref: The provider's run/pipeline id.

        Returns:
            The run, with every job attached but logs not yet fetched.
        """

    @abstractmethod
    def fetch_job_log(self, job: "Job") -> str | None:
        """Fetch one job's raw trace.

        Args:
            job: The job to fetch, already carrying its provider id.

        Returns:
            The raw log, or None when the job genuinely produced none. None is
            valid data, not an error — it identifies the "never got a runner" case.
        """

    @abstractmethod
    def post_note(self, mr: "MergeRequestRef", body: str, marker: str) -> None:
        """Post or update the report as an MR/PR note. Optional; may no-op.

        Implementations must be idempotent: find the existing note by `marker`
        and edit it rather than adding a second comment on every run.

        Args:
            mr: The merge/pull request to comment on.
            body: Rendered Markdown report.
            marker: Hidden HTML comment identifying this tool's note.
        """


class LogSegmenter(ABC):
    """Parse a provider's raw trace into the neutral :class:`Section` tree."""

    @abstractmethod
    def segment(self, raw_log: str) -> list["Section"]:
        """Split a raw log into sections.

        Args:
            raw_log: The provider's trace, ANSI codes included.

        Returns:
            Top-level sections in log order. Output outside any marker is wrapped
            in synthetic "__preamble__"/"__trailer__" sections so no line is lost.
        """


class LLMClient(ABC):
    """A backend that answers with JSON matching a supplied schema."""

    @abstractmethod
    def complete_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Run one completion and parse the response as JSON.

        Args:
            prompt: The fully rendered prompt.
            schema: JSON Schema the response must satisfy.

        Returns:
            The parsed response. Callers validate it — a backend may return
            something schema-invalid, and the repair retry lives in `llm/report.py`.

        Raises:
            Exception: Any backend failure. Callers degrade to the deterministic
                report rather than failing the run.
        """


class Renderer(ABC):
    """Turn a validated report into one output format."""

    @abstractmethod
    def render(self, report: "Report") -> str:
        """Render a report.

        Args:
            report: The validated, already-redacted report.

        Returns:
            The rendered text.
        """
