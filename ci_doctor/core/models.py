"""Provider-neutral domain model.

Everything downstream of acquisition speaks *only* this model. No provider types
leak past an adapter boundary. Purity guardrail: a case-insensitive grep for any
provider name over core/ must return nothing (keep this file free of them too).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Phase(StrEnum):
    """Where in a job's lifecycle the failure happened.

    Ordered as a job executes. The distinction that matters is SCRIPT (the user's
    own commands — their bug) versus everything else (infrastructure — not their
    bug). The phase is always decided deterministically; the LLM never picks it.
    """

    PROVISION = "provision"  # waiting for / assigning a runner; job never started
    PREPARE = "prepare"  # executor setup, secrets, image pull
    FETCH = "fetch"  # clone, cache restore, artifact download
    SCRIPT = "script"  # the user's actual commands  <- primary suspect
    POST = "post"  # after_script, cache archive, artifact upload
    UNKNOWN = "unknown"


class FailureReason(StrEnum):
    """The provider's own verdict, normalised across providers.

    Distinct from :class:`Phase`: this is what the CI system *reported*, while the
    phase is what the classifier *concluded* from the log. UNKNOWN is a normal
    value — offline replay has no provider metadata at all.
    """

    SCRIPT_FAILURE = "script_failure"
    NO_RUNNER = "no_runner"  # stuck / never scheduled
    TIMEOUT = "timeout"
    RUNNER_SYSTEM = "runner_system"  # executor / infra died
    MISSING_DEPENDENCY = "missing_dependency"  # upstream job's artifact absent
    UNMET_PREREQUISITES = "unmet_prerequisites"
    API_FAILURE = "api_failure"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class LogLine:
    """One line of a job log.

    Attributes:
        number: 1-based line number in the original log, kept so evidence can
            point back at the raw trace.
        text: The line, ANSI already stripped by the segmenter.
        ts: Epoch seconds from the section marker, when the provider emits one.
    """

    number: int
    text: str
    ts: int | None = None


@dataclass
class RunnerInfo:
    """The machine that executed the job.

    Attributes:
        id: Provider's runner id.
        description: Human-readable runner name.
        tags: Runner tags, the usual explanation for "why did this land here".
    """

    id: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class MergeRequestRef:
    """The merge/pull request a run belongs to, for posting the report back.

    Attributes:
        iid: Project-scoped MR/PR number (not the global id).
        project_id: Owning project, when the provider needs it to resolve `iid`.
        title: MR/PR title.
        description: MR/PR body.
        web_url: Browser URL.
    """

    iid: str
    project_id: str | None = None
    title: str | None = None
    description: str | None = None
    web_url: str | None = None


@dataclass
class Section:
    """A named, possibly nested block of a job log.

    Sections are what makes attribution possible: they turn a flat trace into a
    structure the classifier can blame a *part* of.

    Attributes:
        name: Raw provider name, e.g. "step_script". Two synthetic names exist,
            "__preamble__" and "__trailer__", for output outside any marker.
        phase: Filled in by :func:`~ci_doctor.core.phases.assign_phases`.
        header: Human-readable header line, when the provider emits one.
        start_ts: Epoch seconds from the opening marker.
        end_ts: Epoch seconds from the closing marker.
        closed: Whether an explicit end marker was seen. False is a signal in
            itself — an unclosed section usually means the job died inside it.
        lines: The section's own lines, excluding its children's.
        children: Nested sections; nesting is legal and providers do use it.
    """

    name: str  # raw provider name, e.g. "step_script"
    phase: Phase = Phase.UNKNOWN  # mapped via config
    header: str | None = None  # human-readable header line
    start_ts: int | None = None
    end_ts: int | None = None
    closed: bool = False  # saw an explicit end marker
    lines: list[LogLine] = field(default_factory=list)
    children: list["Section"] = field(default_factory=list)  # nesting is legal


@dataclass
class Job:
    """One unit of work in a run — the thing that actually fails.

    Attributes:
        id: Provider's job id.
        name: Job name as configured in the pipeline.
        status: Normalised status string, e.g. "failed", "cancelled".
        stage: Pipeline stage, when the provider has stages.
        failure_reason: The provider's verdict, normalised.
        raw_failure_reason: The provider's original string, kept so an unmapped
            reason is still visible rather than flattened into UNKNOWN.
        allow_failure: Whether the pipeline tolerates this job failing. These are
            noise by default (`analysis.include_allowed_failures`).
        started_at: ISO timestamp.
        finished_at: ISO timestamp.
        duration: Wall-clock seconds.
        runner: The executing machine, when known.
        needs: Upstream job names, used to spot cascade failures.
        web_url: Browser URL for the job.
        log: Raw trace. None means there genuinely is no log — itself the answer
            in the "never got a runner" case, not an error.
        sections: Filled by the provider's segmenter.
    """

    id: str
    name: str
    status: str
    stage: str | None = None
    failure_reason: FailureReason = FailureReason.UNKNOWN
    raw_failure_reason: str = ""  # keep the provider's original string
    allow_failure: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    duration: float | None = None
    runner: RunnerInfo | None = None
    needs: list[str] = field(default_factory=list)  # for cascade detection
    web_url: str = ""
    log: str | None = None  # None when there is genuinely no log
    sections: list[Section] = field(default_factory=list)  # filled by the segmenter


@dataclass
class Run:
    """A whole CI execution — a "pipeline" on one provider, a "workflow run" on another.

    Attributes:
        id: Provider's run id.
        ref: Branch or tag.
        sha: Commit analyzed.
        web_url: Browser URL for the run.
        mr: The MR/PR this run belongs to, when there is one.
        jobs: Every job in the run, not only the failed ones — selection happens
            later, in :mod:`ci_doctor.core.select`.
    """

    id: str
    ref: str = ""
    sha: str = ""
    web_url: str = ""
    mr: MergeRequestRef | None = None
    jobs: list[Job] = field(default_factory=list)
