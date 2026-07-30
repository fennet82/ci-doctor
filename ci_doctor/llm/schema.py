"""The structured-output contract: the report the tool emits.

This is the interface between ci-doctor and any downstream consumer (renderers,
the JSON artifact, a fixer agent reading `handoff_prompt`). Keep it stable.
It is defined here in M0 because the ports reference it; the LLM code that fills
it in lands in M4.
"""

from typing import Literal

from pydantic import BaseModel, Field

from ci_doctor.core.models import Confidence, Phase

#: What kind of failure this was, orthogonal to :class:`Phase` (*where* it failed).
#: "runtime" means the application itself crashed, as opposed to its tests or build.
Category = Literal[
    "build",
    "test",
    "dependency",
    "config",
    "infrastructure",
    "timeout",
    "permissions",
    "flaky",
    "runtime",
    "unknown",
]


class Evidence(BaseModel):
    """A log excerpt, plus why it is the one worth reading.

    Attributes:
        section: Human-readable name of where the excerpt came from.
        excerpt: Verbatim log lines, 15 at most. Never paraphrased — a
            reworded log line is not evidence.
        why_it_matters: What this excerpt proves about the failure.
    """

    section: str
    excerpt: str  # <= 15 lines, verbatim from the log
    why_it_matters: str


class RemediationStep(BaseModel):
    """One concrete action to fix the failure.

    Attributes:
        order: 1-based position in the sequence.
        action: What to do.
        rationale: Why this addresses the cause.
        where: File, line or config key, when identifiable — the difference
            between advice and something actionable.
    """

    order: int
    action: str
    rationale: str
    where: str | None = None  # file / line / config key if identifiable


class Report(BaseModel):
    """The tool's output contract. Stable: downstream consumers depend on it.

    Attributes:
        summary: One sentence, 140 characters at most.
        failure_phase: Always from deterministic attribution. Guardrail #1 — the
            LLM may explain the failure but never re-decide where it happened.
        category: What kind of failure it was.
        confidence: How much to trust this verdict.
        is_infra_not_code: The "not your fault" signal, the single most useful bit
            for whoever is reading at 3am.
        likely_flaky: Whether a re-run would plausibly pass.
        root_cause: Two to four sentences.
        contributing_factors: Non-causal context worth knowing.
        evidence: Excerpts backing the root cause.
        remediation: Ordered fix steps.
        related_paths: Repo paths implicated in the failure.
        handoff_prompt: A self-contained prompt to paste into a coding agent.
    """

    summary: str = Field(max_length=140)  # one sentence
    failure_phase: Phase
    category: Category
    confidence: Confidence
    is_infra_not_code: bool  # "not your fault" signal
    likely_flaky: bool
    root_cause: str  # 2-4 sentences
    contributing_factors: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    remediation: list[RemediationStep] = Field(default_factory=list)
    related_paths: list[str] = Field(default_factory=list)
    handoff_prompt: str  # self-contained prompt for a coding agent
