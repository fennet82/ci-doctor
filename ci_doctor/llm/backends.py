"""LLM backend registry.

Every backend builds one `PydanticAILLMClient` wrapping a different
`pydantic_ai.models.Model`, selected by `llm.backend`. Each uses
`PromptedOutput(Report)` and one built-in repair retry (`Agent(retries=1)`) —
no hand-rolled JSON parsing or retry loop here.

Every backend needs its own pip extra; there is no default SDK in the base
install. `openai`/`azure` and `litellm` are mutually exclusive installs —
litellm pins `openai<3.0`, Pydantic AI's OpenAI integration needs `openai>=3.8`
(see pyproject.toml's `[tool.uv] conflicts`). `litellm` reaches everything the
other four don't (Vertex, Cohere, watsonx, ~100 providers) via the community
`pydantic-ai-litellm` bridge, using litellm's own model-string convention
(e.g. `model: vertex_ai/gemini-1.5-pro`).

Every SDK is imported inside the builder that needs it, so importing this
module costs nothing on a run that never reaches a model.
"""

import os
import threading
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from ci_doctor.config.schema import LLMConfig
from ci_doctor.core.ports import LLMClient
from ci_doctor.llm.schema import Report

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models import Model


def _api_key(cfg: LLMConfig, environ: Mapping[str, str]) -> str | None:
    """Resolve the configured API key, or None to let the provider use its own default env var."""
    return environ.get(cfg.api_key_env) if cfg.api_key_env else None


def _require_model(cfg: LLMConfig) -> str:
    """Narrow `cfg.model` to `str` — `backend_ready` checks this in practice, but an injected client skips it."""
    if not cfg.model:
        raise ValueError(f"llm.model is required for the {cfg.backend} backend")
    return cfg.model


def _openai_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Any OpenAI-compatible endpoint — self-hosted (Ollama, vLLM) or hosted."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    model_name = _require_model(cfg)
    kwargs: dict[str, Any] = {
        "base_url": cfg.api_base,
        "api_key": _api_key(cfg, environ) or "no-key",  # SDK requires non-empty; local servers ignore it
    }
    if cfg.ca_bundle:
        import httpx

        kwargs["http_client"] = httpx.AsyncClient(verify=cfg.ca_bundle)
    return OpenAIChatModel(model_name, provider=OpenAIProvider(**kwargs))


def _anthropic_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """The Anthropic Messages API directly."""
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    model_name = _require_model(cfg)
    kwargs: dict[str, Any] = {"api_key": _api_key(cfg, environ)}
    if cfg.ca_bundle:
        import httpx

        kwargs["http_client"] = httpx.AsyncClient(verify=cfg.ca_bundle)
    return AnthropicModel(model_name, provider=AnthropicProvider(**kwargs))


def _azure_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Azure OpenAI. Unlike `openai`, no "no-key" fallback: Azure always needs one."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.azure import AzureProvider

    model_name = _require_model(cfg)
    provider = AzureProvider(
        azure_endpoint=cfg.azure_endpoint,
        api_version=cfg.azure_api_version,
        api_key=_api_key(cfg, environ),
    )
    return OpenAIChatModel(model_name, provider=provider)


def _bedrock_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":  # noqa: ARG001 - AWS creds come from the environment/IAM, not `environ`
    """Amazon Bedrock. AWS IAM auth (env vars, profile, or instance role) — not an API key."""
    from pydantic_ai.models.bedrock import BedrockConverseModel
    from pydantic_ai.providers.bedrock import BedrockProvider

    model_name = _require_model(cfg)
    return BedrockConverseModel(model_name, provider=BedrockProvider(region_name=cfg.aws_region))


def _litellm_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Anything litellm reaches that the other backends can't."""
    from pydantic_ai_litellm import LiteLLMModel  # ty: ignore[unresolved-import]

    model_name = _require_model(cfg)
    return LiteLLMModel(model_name, api_key=_api_key(cfg, environ), api_base=cfg.api_base or None)


_MODEL_BUILDERS: dict[str, Callable[[LLMConfig, Mapping[str, str]], "Model"]] = {
    "openai": _openai_model,
    "anthropic": _anthropic_model,
    "azure": _azure_model,
    "bedrock": _bedrock_model,
    "litellm": _litellm_model,
}

_READY_CHECKS: dict[str, Callable[[LLMConfig], bool]] = {
    "openai": lambda cfg: bool(cfg.model and cfg.api_base),
    "anthropic": lambda cfg: bool(cfg.model),
    "azure": lambda cfg: bool(cfg.model and cfg.azure_endpoint),
    "bedrock": lambda cfg: bool(cfg.model),
    "litellm": lambda cfg: bool(cfg.model),
}


def make_client(cfg: LLMConfig, environ: Mapping[str, str] | None = None) -> LLMClient:
    """Build the client for the configured backend.

    Args:
        cfg: LLM settings; `cfg.backend` selects the implementation.
        environ: Environment for API keys. Defaults to os.environ.

    Returns:
        A client implementing :class:`~ci_doctor.core.ports.LLMClient`.

    Raises:
        ValueError: On an unknown backend name.
    """
    build = _MODEL_BUILDERS.get(cfg.backend)
    if build is None:
        raise ValueError(f"unknown llm.backend: {cfg.backend}")
    resolved_environ = os.environ if environ is None else environ
    return PydanticAILLMClient(build(cfg, resolved_environ), cfg)


def backend_ready(cfg: LLMConfig) -> bool:
    """Check whether a backend can run as configured.

    Args:
        cfg: LLM settings.

    Returns:
        True if the backend has everything it needs. Unknown backends are False.
    """
    check = _READY_CHECKS.get(cfg.backend)
    return check(cfg) if check else False


class PydanticAILLMClient(LLMClient):
    """Wraps one `pydantic_ai.models.Model` behind the `LLMClient` port.

    One `Agent` is built lazily and reused for the client's lifetime (one
    client per run, per `llm/report.py::client_for_run`).
    """

    def __init__(self, model: "Model", cfg: LLMConfig) -> None:
        """Store the model and config; no Agent is built until a call is made."""
        self._model = model
        self.cfg = cfg
        self._agent: Agent[None, Report] | None = None
        self._agent_lock = threading.Lock()

    def _agent_for(self) -> "Agent[None, Report]":
        """Build the Agent once, thread-safely (jobs run on a thread pool)."""
        with self._agent_lock:
            if self._agent is None:
                from pydantic_ai import Agent
                from pydantic_ai.output import PromptedOutput

                self._agent = Agent(
                    self._model,
                    output_type=PromptedOutput(Report),
                    retries=1,
                    model_settings={
                        "temperature": self.cfg.temperature,
                        "timeout": self.cfg.timeout_seconds,
                    },
                )
        return self._agent

    def complete_structured(self, prompt: str) -> dict[str, Any]:
        """Run one completion and return the validated reply as a dict.

        Args:
            prompt: The rendered, already-redacted prompt.

        Returns:
            The reply, already schema-valid.

        Raises:
            Exception: Any transport, API, or exhausted-retry failure.
        """
        agent = self._agent_for()
        result = agent.run_sync(prompt)
        return result.output.model_dump(mode="json")
