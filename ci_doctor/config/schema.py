"""Pydantic config model, and the source of truth for the published JSON Schema.

``extra="forbid"`` everywhere means an unknown key is an error, not a silent typo.
Scalar defaults live here; the data-heavy baseline (phase map, matcher packs, noise
patterns) ships in ``defaults.yml``.

Every field carries a ``description``: it is the text editors show when completing a
``.ci-doctor.yml`` against the schema emitted by ``ci-doctor config --schema``, so a
field added without one ships an undocumented knob.
"""

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger("ci_doctor.config")

#: Canonical URL of the published schema. A `.ci-doctor.yml` points its
#: `# yaml-language-server: $schema=` comment here to get editor completion.
SCHEMA_ID = "https://github.com/fennet82/ci-doctor/releases/latest/download/ci-doctor.schema.json"


class _Strict(BaseModel):
    """Base for every config section: unknown keys raise instead of being ignored."""

    model_config = ConfigDict(extra="forbid")


class GitLabConfig(_Strict):
    """How to reach GitLab. Used when GitLab is the ``ci`` or the ``scm``."""

    base_url: str = Field(
        "https://gitlab.com",
        description="GitLab base URL. Override for a self-hosted or internal instance.",
    )
    api_version: str = Field("v4", description="GitLab REST API version.")
    token_env: str = Field(
        "CI_DOCTOR_GITLAB_TOKEN",
        description="Name of the env var holding a read-only API token. The token itself is never a config value.",
    )
    token_file: str | None = Field(
        None,
        description="Path to a file holding the API token, e.g. /run/secrets/gitlab_token. Wins over token_env.",
    )
    ca_bundle: str | None = Field(None, description="Path to a custom CA bundle for the GitLab endpoint.")
    verify_ssl: bool = Field(True, description="Verify the GitLab endpoint's TLS certificate.")
    timeout_seconds: int = Field(30, description="Per-request timeout for GitLab API calls.")


class GitHubConfig(_Strict):
    """How to reach GitHub. Used when GitHub is the ``ci`` or the ``scm``."""

    base_url: str = Field(
        "https://api.github.com",
        description="GitHub API base URL. Override for GitHub Enterprise, e.g. https://ghe.internal/api/v3.",
    )
    token_env: str = Field(
        "CI_DOCTOR_GITHUB_TOKEN",
        description="Name of the env var holding a read-only API token. The token itself is never a config value.",
    )
    token_file: str | None = Field(
        None, description="Path to a file holding the API token. Wins over token_env."
    )
    ca_bundle: str | None = Field(None, description="Path to a custom CA bundle for the GitHub endpoint.")
    verify_ssl: bool = Field(True, description="Verify the GitHub endpoint's TLS certificate.")
    timeout_seconds: int = Field(30, description="Per-request timeout for GitHub API calls.")


class LLMConfig(_Strict):
    """The optional explanation step.

    Disabled or unreachable degrades to the deterministic report rather than
    failing the run.
    """

    enabled: bool = Field(True, description="Set false for a deterministic-only report with no LLM call.")
    backend: Literal["openai", "litellm", "azure", "anthropic"] = Field(
        "openai",
        description=(
            "openai: any OpenAI-compatible endpoint (needs api_base) — self-hosted Ollama/vLLM/"
            "LM Studio included. anthropic: the Anthropic Messages API directly. azure: Azure "
            "OpenAI (needs azure_endpoint). litellm: any of litellm's ~100 providers (Bedrock, "
            "Vertex, Cohere, watsonx, custom proxies, ...) via litellm's own model-string "
            'convention, e.g. model: "bedrock/anthropic.claude-v2".'
        ),
    )
    model: str | None = Field(None, description='Model identifier, e.g. "qwen2.5-coder:32b".')
    api_base: str | None = Field(None, description="Base URL of the OpenAI-compatible / litellm endpoint.")
    api_key_env: str | None = Field(
        None,
        description="Name of the env var holding the LLM API key. Often unset — local servers need no key, "
        "and a hosted backend without this set falls back to its own default env var (e.g. ANTHROPIC_API_KEY).",
    )
    ca_bundle: str | None = Field(
        None, description="CA bundle for the LLM endpoint, independent of the CI provider's."
    )
    azure_endpoint: str | None = Field(
        None, description="Azure OpenAI resource endpoint URL. Required for the azure backend."
    )
    azure_api_version: str | None = Field(
        None, description="Azure OpenAI API version, e.g. 2024-10-21. Required for the azure backend."
    )
    max_input_tokens: int = Field(
        12000,
        description="Evidence budget. Larger bundles are truncated, and every truncation is visible in the report.",
    )
    temperature: float = Field(
        0.1, description="Sampling temperature. Low keeps the postmortem reproducible."
    )
    timeout_seconds: int = Field(120, description="Timeout for the single LLM call.")
    max_retries: int = Field(
        1,
        ge=0,
        description="HTTP-transport retries per request. Kept low: Pydantic AI's own Agent "
        "already retries once on a schema-invalid reply, and the two multiply.",
    )


class AnalysisConfig(_Strict):
    """Which jobs get analyzed, and which skip the LLM."""

    include_allowed_failures: bool = Field(
        False, description="Analyze jobs marked allow_failure. They are noise by default."
    )
    max_jobs_analyzed: int = Field(10, description="Cap on failed jobs analyzed per run.")
    max_parallel_jobs: int = Field(
        4,
        ge=1,
        description=(
            "Jobs analyzed concurrently. The LLM call dominates a run and the calls are "
            "independent, so this is the main latency lever. Set 1 for a strictly "
            "sequential run against a rate-limited endpoint."
        ),
    )
    skip_llm_for: list[str] = Field(
        default_factory=lambda: ["no_runner", "missing_dependency", "cancelled"],
        description="Failure reasons already fully determined, so they get a templated report and no LLM call.",
    )


class MatcherConfig(_Strict):
    """One evidence matcher: the log window to pull around a recognised failure.

    Use either ``start``/``end`` (a bounded block) or ``pattern`` with
    ``before``/``after`` (a single anchor line plus context), not both.
    """

    id: str = Field(
        description="Unique matcher id. Reusing a shipped id overrides just the fields you set, and logs a warning."
    )
    start: str | None = Field(None, description="Regex opening a windowed matcher.")
    end: str | None = Field(None, description="Regex closing a windowed matcher.")
    pattern: str | None = Field(None, description="Regex anchoring a single-line matcher.")
    before: int = Field(0, description="Lines of context kept above a `pattern` hit.")
    after: int = Field(0, description="Lines of context kept below a `pattern` hit.")
    priority: int = Field(
        50, description="Higher priority survives budget pressure when the evidence must be trimmed."
    )


class ExtractionConfig(_Strict):
    """How the causal lines are pulled out of a job log."""

    tail_lines: int = Field(120, description="Lines kept from the end of the blamed section as a fallback.")
    matchers: list[MatcherConfig] = Field(
        default_factory=list,
        description=(
            "Language packs, merged by id onto the shipped ones. Reusing a shipped id overrides "
            "only the fields you set on it; a new id adds to them."
        ),
    )


class DenoiseConfig(_Strict):
    """Log cleanup applied before evidence extraction."""

    collapse_carriage_returns: bool = Field(
        True, description=r"Collapse \r progress bars to their final rendered state."
    )
    dedupe_repeats: bool = Field(True, description='Fold repeated lines into "<line>  (×47)".')
    noise_patterns: list[str] = Field(
        default_factory=list, description="Regexes whose matching lines are dropped outright."
    )


class OutputConfig(_Strict):
    """Where the report goes."""

    terminal: bool = Field(True, description="Render the report to the terminal.")
    markdown_path: str = Field("report.md", description="Path the Markdown report is written to.")
    json_path: str = Field("report.json", description="Path the JSON report is written to.")
    mr_note: bool = Field(
        False,
        description="Post or update a note on the MR/PR. Needs a live pipeline, not an offline log replay.",
    )


class RedactionConfig(_Strict):
    """Secret scrubbing. Applied before anything is rendered, written or sent."""

    enabled: bool = Field(
        True, description="Scrub tokens, credentials in URLs and env secrets from all output."
    )
    extra_patterns: list[str] = Field(
        default_factory=list, description="Additional regexes to scrub, on top of the built-in set."
    )


class Config(_Strict):
    """The full ``.ci-doctor.yml`` document. Every key is optional."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"$id": SCHEMA_ID},
    )

    ci: str = Field(
        "github",
        description=(
            "Which CI system to read the failed run from: github or gitlab. Only used for a live "
            "run — replaying a log file detects the format from the log itself."
        ),
    )
    scm: str | None = Field(
        None,
        description=(
            "Which git host to post the MR/PR note to. Defaults to `ci`, which is right whenever the "
            "CI system is also the git host. Set it for mixed setups — `ci: jenkins` with `scm: gitlab`. "
            'Use "none" to never post.'
        ),
    )
    provider: str | None = Field(
        None,
        description="Deprecated alias for `ci`, kept for one release. Set `ci` instead.",
        deprecated=True,
    )
    gitlab: GitLabConfig = Field(default_factory=GitLabConfig, description="GitLab connection settings.")
    github: GitHubConfig = Field(default_factory=GitHubConfig, description="GitHub connection settings.")
    llm: LLMConfig = Field(default_factory=LLMConfig, description="LLM explanation step.")
    analysis: AnalysisConfig = Field(
        default_factory=AnalysisConfig, description="Job selection and LLM skipping."
    )
    phases: dict[str, str] = Field(
        default_factory=dict,
        description="Section/step name -> phase (setup, script, after_script, ...). Deep-merged onto the shipped map.",
    )
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig, description="Evidence extraction.")
    denoise: DenoiseConfig = Field(default_factory=DenoiseConfig, description="Log cleanup.")
    output: OutputConfig = Field(default_factory=OutputConfig, description="Report destinations.")
    redaction: RedactionConfig = Field(default_factory=RedactionConfig, description="Secret scrubbing.")

    @model_validator(mode="after")
    def _fold_deprecated_provider(self) -> "Config":
        """Accept the old `provider` key as `ci`, and refuse a contradiction.

        Returns:
            Self, with `ci` carrying the old key's value when only it was set.

        Raises:
            ValueError: If both keys are set to different systems. Guessing which
                one the user meant would silently read the wrong CI.
        """
        # Read through __dict__: `deprecated=True` turns plain attribute access into
        # a DeprecationWarning, and this validator runs on every single config load.
        provider = self.__dict__.get("provider")
        if provider is None:
            return self
        if "ci" in self.model_fields_set and self.ci != provider:
            raise ValueError(f"config sets both ci={self.ci!r} and the deprecated provider={provider!r}")
        log.warning("`provider:` is deprecated and will be removed; rename it to `ci:`")
        self.ci = provider
        return self

    @property
    def scm_vendor(self) -> str:
        """The git host to post the note to.

        Returns:
            The configured `scm`, or `ci` when unset — a CI system that is also a
            git host is its own note target, which covers GitLab and GitHub.
        """
        return self.scm or self.ci
