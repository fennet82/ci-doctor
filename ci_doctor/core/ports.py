"""Abstract ports. Core imports only these; adapters implement them.

Kept import-light on purpose: annotations are strings (PEP 563) and concrete
types are only pulled in under TYPE_CHECKING so importing a port never drags a
provider or the LLM stack into core.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ci_doctor.core.models import Job, MergeRequestRef, Run, Section, VectorHit, VectorRecord
    from ci_doctor.llm.schema import Report


class CIProvider(ABC):
    """Read-only access to one CI system — who ran the pipeline.

    Separate from :class:`SCMProvider` because the two are chosen independently:
    Jenkins builds GitLab repos, Woodpecker builds Forgejo ones. A vendor that is
    both (GitLab, GitHub) implements both ports over one client.

    Invariant #10: every method here is a *read*. Nothing may retry, cancel or
    restart a job.
    """

    @abstractmethod
    def fetch_run(self, run_ref: str) -> "Run":
        """Fetch a run and its jobs.

        Args:
            run_ref: The CI system's run/pipeline id.

        Returns:
            The run, with every job attached but logs not yet fetched.
        """

    @abstractmethod
    def fetch_job_log(self, job: "Job") -> str | None:
        """Fetch one job's raw trace.

        Args:
            job: The job to fetch, already carrying its CI-side id.

        Returns:
            The raw log, or None when the job genuinely produced none. None is
            valid data, not an error — it identifies the "never got a runner" case.
        """


class SCMProvider(ABC):
    """Access to one git host — where the code lives and the note goes.

    Invariant #10 holds here too: the only write in the whole tool is
    :meth:`post_note`, and it writes to a discussion thread. Nothing may push,
    merge, or change a repository.
    """

    @abstractmethod
    def post_note(self, mr: "MergeRequestRef", body: str, marker: str) -> None:
        """Post or update the report as an MR/PR note.

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
    """A backend that answers with JSON matching the schema embedded in the prompt.

    Every backend is prompt-and-parse: the reply schema is rendered into the
    prompt text by `llm/report.py`, not passed as an argument, because none of
    the endpoints we target enforce one server-side.
    """

    @abstractmethod
    def complete_structured(self, prompt: str) -> dict[str, Any]:
        """Run one completion and parse the response as JSON.

        Args:
            prompt: The fully rendered prompt, schema included.

        Returns:
            The parsed response. Callers validate it — a backend may return
            something schema-invalid, and the repair retry lives in `llm/report.py`.

        Raises:
            Exception: Any backend failure. Callers degrade to the deterministic
                report rather than failing the run.
        """


class VectorStore(ABC):
    """Store and similarity-search embedding vectors.

    Neutral by design (invariant #2): no vector-DB vendor name appears here.
    Concrete backends live in ``ci_doctor/memory/backends.py`` and lazy-import
    their SDKs, exactly as the LLM backends do for :class:`LLMClient`. The port
    speaks *vectors*, never text — embedding is the caller's concern, kept out so
    the store depends on no model.

    A hit reports the same ``id`` the record was stored under, whatever the backend
    uses internally as a key.
    """

    @abstractmethod
    def ensure_collection(self, dimension: int) -> None:
        """Create the collection/index/table if absent; a no-op if it exists.

        Args:
            dimension: Length every stored vector will have. Most backends fix this
                at creation, so it is set here rather than inferred per add.
        """

    @abstractmethod
    def add(self, records: "list[VectorRecord]") -> None:
        """Upsert records by id.

        Args:
            records: Vectors plus metadata. Re-adding an existing id overwrites it,
                so a re-analyzed run does not accumulate duplicates.
        """

    @abstractmethod
    def search(
        self, vector: "list[float]", k: int, filter: "dict[str, Any] | None" = None
    ) -> "list[VectorHit]":
        """Return the k nearest records to a query vector.

        Args:
            vector: The query embedding.
            k: Maximum number of hits to return.
            filter: Optional exact-match metadata constraints (e.g. ``{"repo": "x"}``);
                each backend translates it to its own filter language.

        Returns:
            Up to k hits, nearest first (highest score first).
        """

    @abstractmethod
    def delete(self, ids: "list[str]") -> None:
        """Delete records by id.

        Args:
            ids: Ids to remove. Ids that are absent are ignored, not an error.
        """

    def close(self) -> None:  # noqa: B027 - optional lifecycle hook; no-op default backends may override
        """Release any open connection. A no-op for backends that hold none.

        Safe to call more than once. A long-lived host must call this; a one-shot
        process may rely on exit to reclaim the connection instead.
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
