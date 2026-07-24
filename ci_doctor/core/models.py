"""Provider-neutral domain model.

Everything downstream of acquisition speaks *only* this model. No provider types
leak past an adapter boundary. Purity guardrail: a case-insensitive grep for any
provider name over core/ must return nothing (keep this file free of them too).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Phase(StrEnum):
    PROVISION = "provision"  # waiting for / assigning a runner; job never started
    PREPARE = "prepare"      # executor setup, secrets, image pull
    FETCH = "fetch"          # clone, cache restore, artifact download
    SCRIPT = "script"        # the user's actual commands  <- primary suspect
    POST = "post"            # after_script, cache archive, artifact upload
    UNKNOWN = "unknown"


class FailureReason(StrEnum):
    SCRIPT_FAILURE = "script_failure"
    NO_RUNNER = "no_runner"                     # stuck / never scheduled
    TIMEOUT = "timeout"
    RUNNER_SYSTEM = "runner_system"             # executor / infra died
    MISSING_DEPENDENCY = "missing_dependency"   # upstream job's artifact absent
    UNMET_PREREQUISITES = "unmet_prerequisites"
    API_FAILURE = "api_failure"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class LogLine:
    number: int
    text: str
    ts: int | None = None


@dataclass
class RunnerInfo:
    id: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class MergeRequestRef:
    iid: str
    project_id: str | None = None
    title: str | None = None
    description: str | None = None
    web_url: str | None = None


@dataclass
class Section:
    name: str                                  # raw provider name, e.g. "step_script"
    phase: Phase = Phase.UNKNOWN               # mapped via config
    header: str | None = None                  # human-readable header line
    start_ts: int | None = None
    end_ts: int | None = None
    closed: bool = False                       # saw an explicit end marker
    lines: list[LogLine] = field(default_factory=list)
    children: list["Section"] = field(default_factory=list)  # nesting is legal


@dataclass
class Job:
    id: str
    name: str
    status: str
    stage: str | None = None
    failure_reason: FailureReason = FailureReason.UNKNOWN
    raw_failure_reason: str = ""               # keep the provider's original string
    allow_failure: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    duration: float | None = None
    runner: RunnerInfo | None = None
    needs: list[str] = field(default_factory=list)  # for cascade detection
    web_url: str = ""
    log: str | None = None                     # None when there is genuinely no log
    sections: list[Section] = field(default_factory=list)  # filled by the segmenter


@dataclass
class Run:  # "pipeline" on one provider, "workflow run" on another
    id: str
    ref: str = ""
    sha: str = ""
    web_url: str = ""
    mr: MergeRequestRef | None = None
    jobs: list[Job] = field(default_factory=list)
