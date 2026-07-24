"""Pydantic config model. `extra="forbid"` everywhere => unknown keys are an
error, not a silent typo. Scalar defaults live here; the data-heavy baseline
(phase map, matchers, noise patterns) ships in defaults.yml.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitLabConfig(_Strict):
    # Defaults to the public host; override for a self-hosted / internal instance.
    base_url: str = "https://gitlab.com"
    api_version: str = "v4"
    token_env: str = "CI_DOCTOR_GITLAB_TOKEN"
    token_file: str | None = None            # takes precedence over token_env if present
    ca_bundle: str | None = None
    verify_ssl: bool = True
    timeout_seconds: int = 30


class GitHubConfig(_Strict):
    # Defaults to public GitHub's API; override for GitHub Enterprise
    # (e.g. https://ghe.internal/api/v3).
    base_url: str = "https://api.github.com"
    token_env: str = "CI_DOCTOR_GITHUB_TOKEN"
    token_file: str | None = None
    ca_bundle: str | None = None
    verify_ssl: bool = True
    timeout_seconds: int = 30


class LLMConfig(_Strict):
    enabled: bool = True                     # false => deterministic-only report
    # openai: OpenAI-compatible endpoint (api_base). litellm: any litellm provider.
    # anthropic: official Anthropic SDK. claude_code: the local `claude` CLI.
    backend: Literal["openai", "litellm", "anthropic", "claude_code"] = "openai"
    model: str | None = None                 # model string (defaults to claude-opus-4-8 for anthropic)
    api_base: str | None = None              # OpenAI-compatible / litellm / anthropic base URL
    api_key_env: str | None = None           # optional; local servers often need none
    ca_bundle: str | None = None
    max_input_tokens: int = 12000
    temperature: float = 0.1
    timeout_seconds: int = 120


class AnalysisConfig(_Strict):
    include_allowed_failures: bool = False
    max_jobs_analyzed: int = 10
    skip_llm_for: list[str] = Field(
        default_factory=lambda: ["no_runner", "missing_dependency", "cancelled"]
    )
    known_flaky_tests: list[str] = Field(default_factory=list)


class MatcherConfig(_Strict):
    id: str
    start: str | None = None                 # start/end define a windowed matcher
    end: str | None = None
    pattern: str | None = None               # or a single-line anchor with context
    before: int = 0
    after: int = 0
    priority: int = 50


class ExtractionConfig(_Strict):
    tail_lines: int = 120
    matchers: list[MatcherConfig] = Field(default_factory=list)


class DenoiseConfig(_Strict):
    collapse_carriage_returns: bool = True
    dedupe_repeats: bool = True
    noise_patterns: list[str] = Field(default_factory=list)


class OutputConfig(_Strict):
    terminal: bool = True
    markdown_path: str = "report.md"
    json_path: str = "report.json"
    mr_note: bool = False


class RedactionConfig(_Strict):
    enabled: bool = True
    extra_patterns: list[str] = Field(default_factory=list)


class Config(_Strict):
    provider: str = "gitlab"
    gitlab: GitLabConfig = Field(default_factory=GitLabConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    phases: dict[str, str] = Field(default_factory=dict)  # section name -> phase
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    denoise: DenoiseConfig = Field(default_factory=DenoiseConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)
