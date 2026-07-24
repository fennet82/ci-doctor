"""The structured-output contract: the report the tool emits.

This is the interface between ci-doctor and any downstream consumer (renderers,
the JSON artifact, a fixer agent reading `handoff_prompt`). Keep it stable.
It is defined here in M0 because the ports reference it; the LLM code that fills
it in lands in M4.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ci_doctor.core.models import Phase

Category = Literal[
    "build", "test", "dependency", "config", "infrastructure",
    "timeout", "permissions", "flaky", "unknown",
]


class Evidence(BaseModel):
    section: str
    excerpt: str  # <= 15 lines, verbatim from the log
    why_it_matters: str


class RemediationStep(BaseModel):
    order: int
    action: str
    rationale: str
    where: str | None = None  # file / line / config key if identifiable


class Report(BaseModel):
    summary: str = Field(max_length=140)   # one sentence
    failure_phase: Phase
    category: Category
    confidence: Literal["high", "medium", "low"]
    is_infra_not_code: bool                # "not your fault" signal
    likely_flaky: bool
    root_cause: str                        # 2-4 sentences
    contributing_factors: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    remediation: list[RemediationStep] = Field(default_factory=list)
    related_paths: list[str] = Field(default_factory=list)
    handoff_prompt: str                    # self-contained prompt for a coding agent
