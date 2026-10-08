"""LLM backend registry.

Every backend builds one `PydanticAILLMClient` wrapping a `pydantic_ai.models.Model`,
selected by `llm.backend`. Each uses `PromptedOutput(Report)` and one built-in repair
retry (`Agent(retries=1)`) — no hand-rolled JSON parsing or retry loop here.

Any Pydantic AI-native provider goes through one generic path: Pydantic AI's own
`infer_model` picks the `Model` class, and the provider is built from the constructor
arguments it actually accepts (`api_key`, `base_url`, `http_client`). Adding a provider
is a name in `LLMConfig.backend` and a pip extra. Only a provider with unusual
constructor arguments gets an entry in `_EXTRA_KWARGS`. `litellm` is the one real
override: it is the community `pydantic-ai-litellm` bridge, which routes in-process to
~100 providers by litellm's own model-string convention (`vertex_ai/gemini-1.5-pro`).

Every backend needs its own pip extra; there is no default SDK in the base install.
`openai`/`azure` and `litellm` are mutually exclusive — litellm pins `openai<3.0`,
Pydantic AI's OpenAI integration needs `openai>=3.8` (see `[tool.uv] conflicts`).

Every SDK is imported inside the builder that needs it, so importing this module
costs nothing on a run that never reaches a model.
"""

import asyncio
import contextlib
import inspect
import os
import ssl
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, get_args

from ci_doctor.config.schema import LLMConfig
from ci_doctor.core.ports import LLMClient
from ci_doctor.llm.schema import Report

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models import Model

#: Every name `llm.backend` accepts — the config Literal is the single source.
_KNOWN = frozenset(get_args(LLMConfig.model_fields["backend"].annotation))

#: Where our name differs from Pydantic AI's: its bare `openai` is the Responses API,
#: and self-hosted servers speak Chat Completions.
_KIND = {"openai": "openai-chat"}


def _api_key(cfg: LLMConfig, environ: Mapping[str, str]) -> str | None:
    """Resolve the configured API key, or None to let the provider use its own default env var."""
    return environ.get(cfg.api_key_env) if cfg.api_key_env else None


def _require_model(cfg: LLMConfig) -> str:
    """Narrow `cfg.model` to `str` — `backend_ready` checks this in practice, but an injected client skips it."""
    if not cfg.model:
        raise ValueError(f"llm.model is required for the {cfg.backend} backend")
    return cfg.model


def _provider_kwargs(provider_class: type, cfg: LLMConfig, environ: Mapping[str, str]) -> dict[str, Any]:
    """Build the constructor arguments the provider class actually accepts."""
    params = inspect.signature(provider_class.__init__).parameters
    kwargs: dict[str, Any] = {}
    if "api_key" in params:
        kwargs["api_key"] = _api_key(cfg, environ)
    if "base_url" in params and cfg.api_base:
        kwargs["base_url"] = cfg.api_base
    if "http_client" in params and cfg.ca_bundle:
        import httpx

        kwargs["http_client"] = httpx.AsyncClient(verify=ssl.create_default_context(cafile=cfg.ca_bundle))
    return kwargs


@contextlib.contextmanager
def _aws_ca_bundle(path: str | None) -> Iterator[None]:
    """Expose `llm.ca_bundle` to boto as `AWS_CA_BUNDLE` while the Bedrock provider builds its client.

    The provider has no `http_client`, and handing it a ready-made boto client would skip its
    own setup (bearer-token auth, 300s read timeout). boto reads the variable once, at client
    creation, so it is restored straight after.
    """
    if not path:
        yield
        return
    previous = os.environ.get("AWS_CA_BUNDLE")
    os.environ["AWS_CA_BUNDLE"] = path
    try:
        yield
    finally:
        if previous is None:
            del os.environ["AWS_CA_BUNDLE"]
        else:
            os.environ["AWS_CA_BUNDLE"] = previous


#: Arguments a provider needs beyond the generic three. Each takes the config and the
#: kwargs built so far, and returns what to add or replace.
_EXTRA_KWARGS: dict[str, Callable[[LLMConfig, dict[str, Any]], dict[str, Any]]] = {
    # The OpenAI SDK refuses an empty key; local servers ignore whatever they get.
    "openai": lambda cfg, kw: {"api_key": kw.get("api_key") or "no-key"},
    "azure": lambda cfg, kw: {"azure_endpoint": cfg.azure_endpoint, "api_version": cfg.azure_api_version},
    # AWS auth comes from the environment/IAM (boto3's chain), not an API key.
    "bedrock": lambda cfg, kw: {"region_name": cfg.aws_region},
    "bedrock-mantle": lambda cfg, kw: {"region_name": cfg.aws_region},
    "google-cloud": lambda cfg, kw: {
        k: v for k, v in {"project": cfg.gcp_project, "location": cfg.gcp_location}.items() if v
    },
}


def _apply_max_retries(provider: object, cfg: LLMConfig) -> None:
    """Hand `llm.max_retries` to the provider's SDK client, where that SDK has the knob.

    The openai/anthropic/groq SDKs retry 429s, 5xxs and connection errors themselves
    (default 2); leaving that alone would multiply with the Agent's own repair retry.
    Providers without a `max_retries` client attribute (bedrock via boto, litellm) keep
    their own retry behavior.
    """
    client = getattr(provider, "client", None)
    if client is not None and hasattr(client, "max_retries"):
        client.max_retries = cfg.max_retries


def _generic_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Any Pydantic AI-native provider: Anthropic, Google, Groq, Mistral, Cohere, xAI, Bedrock, ..."""
    from pydantic_ai.models import infer_model, infer_provider_class

    model_name = _require_model(cfg)
    kind = _KIND.get(cfg.backend, cfg.backend)
    provider_class = infer_provider_class(kind)
    kwargs = _provider_kwargs(provider_class, cfg, environ)
    if extra := _EXTRA_KWARGS.get(cfg.backend):
        kwargs.update(extra(cfg, kwargs))
    with _aws_ca_bundle(cfg.ca_bundle if cfg.backend == "bedrock" else None):
        provider = provider_class(**kwargs)
    _apply_max_retries(provider, cfg)
    return infer_model(f"{kind}:{model_name}", provider_factory=lambda _: provider)


def _litellm_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Anything litellm reaches that the native providers can't."""
    from pydantic_ai_litellm import LiteLLMModel  # ty: ignore[unresolved-import]

    model_name = _require_model(cfg)
    if cfg.ca_bundle:
        import litellm  # ty: ignore[unresolved-import]

        litellm.ssl_verify = (
            cfg.ca_bundle
        )  # litellm's only CA knob; a module global, but we are the only caller

    return LiteLLMModel(model_name, api_key=_api_key(cfg, environ), api_base=cfg.api_base or None)


_OVERRIDES: dict[str, Callable[[LLMConfig, Mapping[str, str]], "Model"]] = {"litellm": _litellm_model}

#: What a backend needs before a call is worth attempting; the default is just a model.
_NEEDS: dict[str, Callable[[LLMConfig], bool]] = {
    "openai": lambda cfg: bool(cfg.model and cfg.api_base),
    "azure": lambda cfg: bool(cfg.model and cfg.azure_endpoint),
}


#: The pip extra for a backend whose name differs from it.
_EXTRA = {"azure": "openai", "google-cloud": "google"}


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
    if cfg.backend not in _KNOWN:
        raise ValueError(f"unknown llm.backend: {cfg.backend}")
    build = _OVERRIDES.get(cfg.backend, _generic_model)
    resolved_environ = os.environ if environ is None else environ
    try:
        model = build(cfg, resolved_environ)
    except ImportError as exc:
        extra = _EXTRA.get(cfg.backend, cfg.backend)
        raise ImportError(
            f"llm.backend {cfg.backend!r} needs its SDK: pip install 'ci-doctorr[{extra}]' ({exc})"
        ) from exc
    return PydanticAILLMClient(model, cfg)


def backend_ready(cfg: LLMConfig) -> bool:
    """Check whether a backend can run as configured.

    Args:
        cfg: LLM settings.

    Returns:
        True if the backend has everything it needs. Unknown backends are False.
    """
    if cfg.backend not in _KNOWN:
        return False
    return _NEEDS.get(cfg.backend, lambda c: bool(c.model))(cfg)


class PydanticAILLMClient(LLMClient):
    """Wraps one `pydantic_ai.models.Model` behind the `LLMClient` port.

    One `Agent` is built lazily and reused for the client's lifetime (one client per
    run, per `llm/report.py::client_for_run`). Jobs call in from a thread pool, but every
    call runs on one shared event loop in a daemon thread: the SDK's async client pools
    keep-alive connections, and a pooled connection is bound to the loop that opened it,
    so `run_sync` (a fresh loop per call) drops some calls with "Connection error".
    """

    def __init__(self, model: "Model", cfg: LLMConfig) -> None:
        """Store the model and config; no Agent or loop is built until a call is made."""
        self._model = model
        self.cfg = cfg
        self._agent: Agent[None, Report] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _agent_for(self) -> "Agent[None, Report]":
        """Build the Agent once, thread-safely (jobs run on a thread pool)."""
        with self._lock:
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

    def _loop_for(self) -> asyncio.AbstractEventLoop:
        """Start the shared event loop once, thread-safely."""
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="ci-doctor-llm", daemon=True).start()
                self._loop = loop
        return self._loop

    def complete_structured(self, prompt: str) -> dict[str, Any]:
        """Run one completion and return the validated reply as a dict.

        Args:
            prompt: The rendered, already-redacted prompt.

        Returns:
            The reply, already schema-valid.

        Raises:
            Exception: Any transport, API, or exhausted-retry failure, or `TimeoutError`
                when the call — repair retry and SDK retries included — outlasts
                `llm.timeout_seconds`.
        """
        agent = self._agent_for()
        budget = asyncio.wait_for(agent.run(prompt), self.cfg.timeout_seconds)
        result = asyncio.run_coroutine_threadsafe(budget, self._loop_for()).result()
        return result.output.model_dump(mode="json")
