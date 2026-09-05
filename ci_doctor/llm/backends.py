"""LLM backend registry.

Every backend builds one `PydanticAILLMClient` wrapping a different
`pydantic_ai.models.Model`; the config `llm.backend` selects one. Every backend
uses `PromptedOutput(Report)` (the schema is rendered into the prompt and
parsed from text, not enforced server-side), so correctness is enforced by
Pydantic AI's own validation + one retry (`Agent(retries=1)`) — not by the
backend, and not duplicated by a second hand-rolled retry loop here.

Every backend needs its own pip extra — there is no default SDK in the base
install (unlike the old `openai`-is-always-installed setup). `openai` covers
anything speaking the OpenAI Chat Completions shape, which is most of the
field — self-hosted Ollama/vLLM/LM Studio included, via a custom `api_base`.
`azure` rides the same extra (Azure OpenAI uses the same client under a
different provider). `anthropic` is its own extra. `litellm` reaches
everything those three don't — Bedrock, Vertex, Cohere, watsonx, custom
proxies, litellm's other ~100 providers — via the community
`pydantic-ai-litellm` bridge, using litellm's own model-string convention
unchanged (e.g. `model: bedrock/anthropic.claude-v2`). `openai`/`azure` and
`litellm` are mutually exclusive installs — litellm hard-pins `openai<3.0`,
which the `openai`/`azure` extra's `openai>=3.8` can never satisfy at once
(see pyproject.toml's `[tool.uv] conflicts`).

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
    """Resolve the configured API key.

    Args:
        cfg: LLM settings.
        environ: Environment to read the key from.

    Returns:
        The resolved key, or None when `api_key_env` is unset — a provider
        that needs one falls back to its own default env var (e.g.
        `ANTHROPIC_API_KEY`); a self-hosted server that ignores auth entirely
        is handled by the `openai` builder's own "no-key" placeholder, since
        the openai SDK (unlike the others) refuses an empty string.
    """
    return environ.get(cfg.api_key_env) if cfg.api_key_env else None


def _openai_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Any OpenAI-compatible chat-completions endpoint.

    Covers self-hosted Ollama/vLLM/LM Studio/llama.cpp via `api_base`, and any
    hosted OpenAI-compatible provider (Groq, Mistral, OpenAI itself) the same way.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    kwargs: dict[str, Any] = {
        "base_url": cfg.api_base,
        "api_key": _api_key(cfg, environ) or "no-key",  # local servers ignore it; the SDK requires non-empty
    }
    if cfg.ca_bundle:
        import httpx

        kwargs["http_client"] = httpx.AsyncClient(verify=cfg.ca_bundle)
    return OpenAIChatModel(cfg.model, provider=OpenAIProvider(**kwargs))


def _anthropic_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """The real Anthropic Messages API.

    Replaces the old `claude_code` CLI backend: same models, direct HTTPS
    instead of a subprocess, full tool-calling (no `--max-turns` ceiling).
    """
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    kwargs: dict[str, Any] = {"api_key": _api_key(cfg, environ)}
    if cfg.ca_bundle:
        import httpx

        kwargs["http_client"] = httpx.AsyncClient(verify=cfg.ca_bundle)
    return AnthropicModel(cfg.model, provider=AnthropicProvider(**kwargs))


def _azure_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Azure OpenAI — needs the resource endpoint, not just a base URL.

    Unlike `openai`'s "no-key" placeholder (for local servers that ignore auth
    entirely), Azure's provider validates eagerly and raises if it can't
    resolve a key at all — set `api_key_env`, there is no local-server case here.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.azure import AzureProvider

    provider = AzureProvider(
        azure_endpoint=cfg.azure_endpoint,
        api_version=cfg.azure_api_version,
        api_key=_api_key(cfg, environ),
    )
    return OpenAIChatModel(cfg.model, provider=provider)


def _litellm_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Any provider litellm can reach that the other backends can't.

    Bedrock, Vertex, Cohere, watsonx, custom proxies — litellm's own
    model-string convention (e.g. "bedrock/anthropic.claude-v2") is passed
    straight through, unchanged from what a litellm user configures today.
    """
    # Unresolved by design: resolving it would mean installing the optional extra.
    from pydantic_ai_litellm import LiteLLMModel  # ty: ignore[unresolved-import]

    return LiteLLMModel(cfg.model, api_key=_api_key(cfg, environ), api_base=cfg.api_base or None)


#: One builder per backend. A new backend is one function plus one dict entry
#: here (and a matching readiness rule below) — not a new `if`/`elif` branch.
_MODEL_BUILDERS: dict[str, Callable[[LLMConfig, Mapping[str, str]], "Model"]] = {
    "openai": _openai_model,
    "anthropic": _anthropic_model,
    "azure": _azure_model,
    "litellm": _litellm_model,
}

#: What each backend needs before a call is worth attempting.
_READY_CHECKS: dict[str, Callable[[LLMConfig], bool]] = {
    "openai": lambda cfg: bool(cfg.model and cfg.api_base),
    "anthropic": lambda cfg: bool(cfg.model),
    "azure": lambda cfg: bool(cfg.model and cfg.azure_endpoint),
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

    Checked *before* attempting a call so an unconfigured backend produces the
    clean deterministic report rather than a failed call and a degraded one.

    Args:
        cfg: LLM settings.

    Returns:
        True if the backend has everything it needs. Unknown backends are False.
    """
    check = _READY_CHECKS.get(cfg.backend)
    return check(cfg) if check else False


class PydanticAILLMClient(LLMClient):
    """Wraps one `pydantic_ai.models.Model` behind the `LLMClient` port.

    One `Agent` is built lazily and reused for the client's lifetime — one
    client per run (`llm/report.py::client_for_run`), so this is one
    connection pool and one schema/instruction setup per run, not per job.
    """

    def __init__(self, model: "Model", cfg: LLMConfig) -> None:
        """Store the model and config; no `Agent` is built until a call is made.

        Args:
            model: The backend-specific `Model`, from one of the builders above.
            cfg: LLM settings — only `temperature`/`timeout_seconds` are read here.
        """
        self._model = model
        self.cfg = cfg
        self._agent: Agent[None, Report] | None = None
        self._agent_lock = threading.Lock()

    def _agent_for(self) -> "Agent[None, Report]":
        """Build the `Agent` once, thread-safely.

        A run analyzes its jobs on a thread pool (`analysis.max_parallel_jobs`),
        so this is reached concurrently; calling `run_sync` concurrently on the
        already-built `Agent` itself is safe (verified directly against the
        pinned Pydantic AI version), but construction is still guarded as cheap
        insurance.

        Returns:
            The lazily-built `Agent`, using `PromptedOutput` (schema rendered
            into the prompt, not enforced server-side — matches the target
            population of self-hosted, unevenly tool-calling-capable endpoints)
            and one repair retry on a schema-invalid reply.
        """
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
            The reply, already schema-valid — Pydantic AI's own
            `PromptedOutput` validation (plus its one retry) ran before this
            returns. The caller still re-validates (`llm/report.py`), which is
            then a no-op in the common case, not a second retry layer.

        Raises:
            Exception: Any transport, API, or exhausted-retry failure from
                Pydantic AI or the underlying SDK.
        """
        agent = self._agent_for()
        result = agent.run_sync(prompt)
        return result.output.model_dump(mode="json")
