"""Backend registry: selection, readiness, provider kwargs, and the shared PydanticAILLMClient.

No network. Dispatch and argument-building are tested against a fake provider class, so
they need no backend SDK; the real-construction tests skip per test when their SDK isn't
installed. `PydanticAILLMClient` runs against a stub output model — the real `Report`
schema is covered end-to-end in tests/test_report.py.
"""

import asyncio
import os
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import certifi
import httpx
import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from ci_doctor.config.loader import load_config
from ci_doctor.llm import backends
from ci_doctor.llm.backends import _KNOWN, PydanticAILLMClient, backend_ready, make_client


class _StubReport(BaseModel):
    """A minimal output model, standing in for the real `Report` schema."""

    summary: str
    score: int


def _llm(**over):
    """Build an LLMConfig from the shipped defaults, overridden by kwargs."""
    return load_config(environ={}, overrides={"llm": over}).llm


@pytest.fixture(autouse=True)
def _use_stub_report_schema(monkeypatch):
    """Every PydanticAILLMClient in this file validates against `_StubReport`, not `Report`."""
    monkeypatch.setattr("ci_doctor.llm.backends.Report", _StubReport)


class _FakeProvider:
    """Records the kwargs it was built with. Accepts api_key and base_url, like most providers."""

    built: dict = {}

    def __init__(self, *, api_key=None, base_url=None):
        type(self).built = {"api_key": api_key, "base_url": base_url}


class _FakeClientProvider(_FakeProvider):
    """A provider that also takes an http_client."""

    def __init__(self, *, api_key=None, http_client=None):
        type(self).built = {"api_key": api_key, "http_client": http_client}


class _AnyProvider:
    """Accepts whatever it is given, for tests that only look at the model id."""

    def __init__(self, **kwargs):
        pass


class _RegionProvider:
    """A provider with no api_key at all, like Bedrock."""

    built: dict = {}

    def __init__(self, *, region_name=None):
        type(self).built = {"region_name": region_name}


@pytest.fixture
def fake_pydantic_ai(monkeypatch):
    """Swap Pydantic AI's provider lookup and model builder for recorders — no SDK needed.

    Returns:
        A dict with the provider class to hand out (`provider_class`) and the ids
        `infer_model` was called with (`ids`).
    """
    state = {"provider_class": _FakeProvider, "ids": [], "kinds": []}

    def infer_provider_class(kind):
        state["kinds"].append(kind)
        return state["provider_class"]

    def infer_model(model_id, provider_factory):
        state["ids"].append(model_id)
        provider_factory("ignored")
        return TestModel()

    monkeypatch.setattr("pydantic_ai.models.infer_provider_class", infer_provider_class)
    monkeypatch.setattr("pydantic_ai.models.infer_model", infer_model)
    return state


def test_generic_path_passes_only_the_kwargs_the_provider_accepts(fake_pydantic_ai):
    """api_key and base_url reach a provider that takes them; nothing else is invented."""
    env = {"MY_KEY": "secret"}
    backends._generic_model(_llm(backend="groq", model="m", api_key_env="MY_KEY", api_base="http://x"), env)
    assert _FakeProvider.built == {"api_key": "secret", "base_url": "http://x"}


def test_generic_path_leaves_an_unset_key_to_the_provider(fake_pydantic_ai):
    """No api_key_env -> None, so the provider reads its own default env var."""
    backends._generic_model(_llm(backend="groq", model="m"), {})
    assert _FakeProvider.built["api_key"] is None


class _SdkClient:
    """Stands in for an SDK client that exposes `max_retries` (openai, anthropic, groq)."""

    max_retries = 2


class _ClientProvider:
    """A provider holding an SDK client, like `OpenAIProvider.client`."""

    last: "_ClientProvider"

    def __init__(self, **kwargs):
        self.client = _SdkClient()
        type(self).last = self


def test_max_retries_reaches_the_sdk_client(fake_pydantic_ai):
    """`llm.max_retries` is applied to a provider's SDK client, not just declared in the schema."""
    fake_pydantic_ai["provider_class"] = _ClientProvider
    backends._generic_model(_llm(backend="groq", model="m", max_retries=0), {})
    assert _ClientProvider.last.client.max_retries == 0


def test_max_retries_skips_providers_without_the_knob():
    """A provider with no SDK client (bedrock via boto) is left alone, not crashed on."""
    backends._apply_max_retries(_AnyProvider(), _llm(model="m", max_retries=0))
    backends._apply_max_retries(SimpleNamespace(client=object()), _llm(model="m", max_retries=0))


def test_a_missing_sdk_names_the_extra_to_install(monkeypatch):
    """The ImportError points at ci-doctorr's extra, not at pydantic-ai-slim's."""

    def boom(cfg, environ):
        raise ImportError("No module named 'openai'")

    monkeypatch.setattr(backends, "_generic_model", boom)
    with pytest.raises(ImportError, match=r"ci-doctorr\[openai\]"):
        make_client(_llm(backend="azure", model="m", azure_endpoint="https://x"))


def test_ca_bundle_becomes_an_http_client_only_where_accepted(fake_pydantic_ai):
    """A provider with an http_client parameter gets the CA-bundle client; one without doesn't."""
    fake_pydantic_ai["provider_class"] = _FakeClientProvider
    backends._generic_model(_llm(backend="groq", model="m", ca_bundle=certifi.where()), {})
    assert isinstance(_FakeClientProvider.built["http_client"], httpx.AsyncClient)

    fake_pydantic_ai["provider_class"] = _FakeProvider
    backends._generic_model(_llm(backend="groq", model="m", ca_bundle=certifi.where()), {})
    assert "http_client" not in _FakeProvider.built


@pytest.mark.parametrize(
    "backend,kind",
    [("openai", "openai-chat"), ("groq", "groq"), ("google-cloud", "google-cloud"), ("bedrock", "bedrock")],
)
def test_model_id_uses_pydantic_ais_provider_kind(fake_pydantic_ai, backend, kind):
    """`openai` maps to Chat Completions; the rest keep their name. Colons in a model id survive."""
    fake_pydantic_ai["provider_class"] = _AnyProvider
    backends._generic_model(_llm(backend=backend, model="a.b-v1:0"), {})
    assert fake_pydantic_ai["kinds"] == [kind]
    assert fake_pydantic_ai["ids"] == [f"{kind}:a.b-v1:0"]


def test_openai_gets_a_placeholder_key_for_local_servers(fake_pydantic_ai):
    """The OpenAI SDK refuses an empty key; a keyless local server gets a placeholder."""
    backends._generic_model(_llm(backend="openai", model="m", api_base="http://stub"), {})
    assert _FakeProvider.built["api_key"] == "no-key"
    backends._generic_model(
        _llm(backend="openai", model="m", api_base="http://stub", api_key_env="K"), {"K": "real"}
    )
    assert _FakeProvider.built["api_key"] == "real"


def test_azure_extras(fake_pydantic_ai):
    """Azure is built from its endpoint and API version, not a base URL."""

    class _Azure:
        built: dict = {}

        def __init__(self, *, azure_endpoint=None, api_version=None, api_key=None):
            type(self).built = {"e": azure_endpoint, "v": api_version, "k": api_key}

    fake_pydantic_ai["provider_class"] = _Azure
    cfg = _llm(
        backend="azure", model="m", azure_endpoint="https://x", azure_api_version="v1", api_key_env="K"
    )
    backends._generic_model(cfg, {"K": "k"})
    assert _Azure.built == {"e": "https://x", "v": "v1", "k": "k"}


@pytest.mark.parametrize("backend", ["bedrock", "bedrock-mantle"])
def test_bedrock_family_gets_the_region(fake_pydantic_ai, backend):
    """Bedrock takes a region, no key; unset falls through to the AWS environment."""
    fake_pydantic_ai["provider_class"] = _RegionProvider
    backends._generic_model(_llm(backend=backend, model="m", aws_region="eu-west-1"), {})
    assert _RegionProvider.built == {"region_name": "eu-west-1"}
    backends._generic_model(_llm(backend=backend, model="m"), {})
    assert _RegionProvider.built == {"region_name": None}


def test_google_cloud_passes_project_and_location_only_when_set(fake_pydantic_ai):
    """Unset project/location are omitted, so Application Default Credentials can supply them."""

    class _GCloud:
        built: dict = {}

        def __init__(self, *, api_key=None, project=None, location=None):
            type(self).built = {"project": project, "location": location}

    fake_pydantic_ai["provider_class"] = _GCloud
    backends._generic_model(_llm(backend="google-cloud", model="m"), {})
    assert _GCloud.built == {"project": None, "location": None}
    backends._generic_model(
        _llm(backend="google-cloud", model="m", gcp_project="p", gcp_location="global"), {}
    )
    assert _GCloud.built == {"project": "p", "location": "global"}


@pytest.mark.parametrize("backend", sorted(_KNOWN))
def test_make_client_builds_a_client_for_every_backend(backend, monkeypatch, fake_pydantic_ai):
    """Every name the config accepts builds a PydanticAILLMClient (litellm's bridge faked too)."""
    monkeypatch.setitem(backends._OVERRIDES, "litellm", lambda cfg, environ: TestModel())
    fake_pydantic_ai["provider_class"] = _AnyProvider
    kwargs = {"backend": backend, "model": "m", "api_base": "http://stub", "azure_endpoint": "https://x"}
    assert isinstance(make_client(_llm(**kwargs)), PydanticAILLMClient)


def test_unknown_backend_raises():
    """The factory rejects an unknown backend."""
    with pytest.raises(ValueError, match="unknown llm.backend"):
        make_client(SimpleNamespace(backend="nope"))


def test_an_unknown_backend_is_never_ready():
    """`backend_ready` refuses a name the config would never accept."""
    assert backend_ready(SimpleNamespace(backend="nope", model="m")) is False


@pytest.mark.parametrize("backend", sorted(_KNOWN - {"openai", "azure"}))
def test_a_backend_needs_only_a_model(backend):
    """Most backends are ready as soon as they have a model; credentials come from the environment."""
    assert backend_ready(_llm(backend=backend, model="m")) is True
    assert backend_ready(_llm(backend=backend)) is False


def test_openai_and_azure_need_their_endpoint():
    """Openai needs api_base; azure needs azure_endpoint."""
    assert backend_ready(_llm(backend="openai", model="m", api_base="http://x")) is True
    assert backend_ready(_llm(backend="openai", model="m")) is False
    assert backend_ready(_llm(backend="azure", model="m", azure_endpoint="https://x")) is True
    assert backend_ready(_llm(backend="azure", model="m")) is False


def test_a_missing_model_is_a_clear_error(fake_pydantic_ai):
    """An injected config skips `backend_ready`, so the builder itself refuses."""
    with pytest.raises(ValueError, match="llm.model is required for the groq backend"):
        backends._generic_model(_llm(backend="groq"), {})


def _reply(json_text: str) -> ModelResponse:
    """A pydantic-ai ModelResponse carrying one text part."""
    return ModelResponse(parts=[TextPart(content=json_text)])


def test_complete_structured_returns_a_validated_dict():
    """One call, no repair needed: the reply is parsed and returned as a dict."""
    model = FunctionModel(lambda messages, info: _reply('{"summary": "ok", "score": 1}'))
    client = PydanticAILLMClient(model, _llm(model="m"))
    assert client.complete_structured("prompt") == {"summary": "ok", "score": 1}


def test_agent_is_built_once_and_reused():
    """One Agent per client, not one per call."""
    calls = {"n": 0}

    def fake_llm(messages, info):
        calls["n"] += 1
        return _reply('{"summary": "ok", "score": 1}')

    client = PydanticAILLMClient(FunctionModel(fake_llm), _llm(model="m"))
    first = client._agent_for()
    client.complete_structured("first")
    client.complete_structured("second")
    assert client._agent_for() is first
    assert calls["n"] == 2


def test_parallel_calls_share_one_event_loop():
    """Every thread-pool call lands on one loop; `run_sync` would use a fresh one each time.

    Pooled keep-alive connections are bound to the loop that opened them.
    """
    loops = set()

    async def fake_llm(messages, info):
        loops.add(id(asyncio.get_running_loop()))
        return _reply('{"summary": "ok", "score": 1}')

    client = PydanticAILLMClient(FunctionModel(fake_llm), _llm(model="m"))
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(client.complete_structured, ["p"] * 12))
    assert all(r == {"summary": "ok", "score": 1} for r in results)
    assert len(loops) == 1


class _ClosableModel(TestModel):
    """A model exposing an async-closable SDK client, as the real ones do."""

    closed = False

    @property
    def client(self):
        """Hand out an object whose `close` records the call."""
        model = self

        class _Sdk:
            async def close(self):
                model.closed = True

        return _Sdk()


def test_close_closes_the_sdk_client_and_stops_the_loop_thread():
    """After a call, `close()` shuts the SDK client and ends the loop thread; twice is fine."""
    model = _ClosableModel(custom_output_text='{"summary": "ok", "score": 1}')
    client = PydanticAILLMClient(model, _llm(model="m"))
    before = set(threading.enumerate())  # other tests leave their own loop threads running
    client.complete_structured("prompt")
    (thread,) = set(threading.enumerate()) - before
    client.close()
    thread.join(timeout=5)
    assert model.closed is True
    assert not thread.is_alive()
    client.close()


def test_close_before_any_call_is_a_no_op():
    """Nothing was opened, so there is nothing to close — and no loop thread is started for it."""
    client = PydanticAILLMClient(_ClosableModel(), _llm(model="m"))
    client.close()
    assert client._loop is None


def test_close_never_raises():
    """A client that fails to close must not fail the run whose report is already built."""

    class _Exploding(TestModel):
        @property
        def client(self):
            raise RuntimeError("boom")

    client = PydanticAILLMClient(
        _Exploding(custom_output_text='{"summary": "ok", "score": 1}'), _llm(model="m")
    )
    client.complete_structured("prompt")
    client.close()


def test_close_shuts_a_real_sdk_client_built_with_a_ca_bundle():
    """The `httpx` client `ca_bundle` creates is closed through the SDK client that owns it."""
    pytest.importorskip("openai")
    cfg = _llm(backend="openai", model="m", api_base="http://127.0.0.1:1/v1", ca_bundle=certifi.where())
    client = make_client(cfg)
    http_client = client._model.client._client
    assert not http_client.is_closed
    client._loop_for()  # close() is a no-op until a call has started the loop
    client.close()
    assert http_client.is_closed


def test_one_repair_retry_on_invalid_then_valid_reply():
    """PromptedOutput + Agent(retries=1) retries exactly once on a schema-invalid reply."""
    calls = {"n": 0}

    def fake_llm(messages, info):
        calls["n"] += 1
        return _reply("not json at all" if calls["n"] == 1 else '{"summary": "ok", "score": 1}')

    client = PydanticAILLMClient(FunctionModel(fake_llm), _llm(model="m"))
    assert client.complete_structured("prompt") == {"summary": "ok", "score": 1}
    assert calls["n"] == 2


def test_still_invalid_after_the_retry_raises():
    """A reply still invalid after the retry ceiling propagates, not silently degrades."""
    client = PydanticAILLMClient(
        FunctionModel(lambda messages, info: _reply("still not json")), _llm(model="m")
    )
    with pytest.raises(Exception):  # noqa: B017 - pydantic-ai's own exhausted-retries error type
        client.complete_structured("prompt")


def test_temperature_and_timeout_reach_the_agent():
    """Config values reach the Agent's model_settings, not left at framework defaults."""
    client = PydanticAILLMClient(
        FunctionModel(lambda messages, info: _reply('{"summary": "ok", "score": 1}')),
        _llm(model="m", temperature=0.7, timeout_seconds=42),
    )
    agent = client._agent_for()
    assert agent.model_settings["temperature"] == 0.7
    assert agent.model_settings["timeout"] == 42


def test_litellm_ca_bundle_sets_its_ssl_verify(monkeypatch):
    """Litellm's only CA knob is the module-level `ssl_verify`; an unset bundle leaves it alone."""
    fake_litellm = types.ModuleType("litellm")
    fake_litellm.ssl_verify = True
    fake_bridge = types.ModuleType("pydantic_ai_litellm")
    fake_bridge.LiteLLMModel = lambda *a, **k: TestModel()
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setitem(sys.modules, "pydantic_ai_litellm", fake_bridge)

    backends._litellm_model(_llm(backend="litellm", model="m"), {})
    assert fake_litellm.ssl_verify is True
    backends._litellm_model(_llm(backend="litellm", model="m", ca_bundle="/etc/ca.pem"), {})
    assert fake_litellm.ssl_verify == "/etc/ca.pem"


def test_timeout_is_a_wall_clock_budget_for_the_whole_call():
    """A slow reply is cut off at `timeout_seconds`, repair retry and SDK retries included."""

    async def slow(messages, info):
        await asyncio.sleep(10)
        return _reply('{"summary": "ok", "score": 1}')

    client = PydanticAILLMClient(FunctionModel(slow), _llm(model="m", timeout_seconds=1))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        client.complete_structured("prompt")
    assert time.monotonic() - started < 5


def test_litellm_model_string_reaches_litellm_model(monkeypatch):
    """Litellm's own model-string convention passes straight through (fake module: the real one conflicts)."""
    captured = {}

    class _FakeLiteLLMModel:
        def __init__(self, model_name, *, api_key=None, api_base=None):
            captured["model_name"] = model_name

    fake_module = types.ModuleType("pydantic_ai_litellm")
    fake_module.LiteLLMModel = _FakeLiteLLMModel
    monkeypatch.setitem(sys.modules, "pydantic_ai_litellm", fake_module)

    backends._litellm_model(_llm(backend="litellm", model="vertex_ai/gemini-1.5-pro"), {})
    assert captured["model_name"] == "vertex_ai/gemini-1.5-pro"


# Real construction: needs each provider's SDK, so each test skips when it is absent.
# (openai/azure/bedrock-mantle conflict with litellm — see pyproject.toml.)
_REAL = [
    ("openai", "openai", "OpenAIChatModel", {"api_base": "http://stub"}),
    (
        "azure",
        "openai",
        "OpenAIChatModel",
        {"azure_endpoint": "https://x.openai.azure.com", "azure_api_version": "v1"},
    ),
    ("anthropic", "anthropic", "AnthropicModel", {}),
    ("google", "google.genai", "GoogleModel", {}),
    ("google-cloud", "google.genai", "GoogleModel", {}),
    ("groq", "groq", "GroqModel", {}),
    ("mistral", "mistralai", "MistralModel", {}),
    ("cohere", "cohere", "CohereModel", {}),
    ("xai", "xai_sdk", "XaiModel", {}),
    ("huggingface", "huggingface_hub", "HuggingFaceModel", {}),
]


@pytest.mark.parametrize("backend,sdk,model_class,extra", _REAL, ids=[r[0] for r in _REAL])
def test_real_provider_builds_the_right_model(backend, sdk, model_class, extra):
    """The generic path constructs each provider's own Model class from a key in the environment."""
    pytest.importorskip(sdk)
    cfg = _llm(backend=backend, model="some-model", api_key_env="K", **extra)
    model = backends._generic_model(cfg, {"K": "fake"})
    assert type(model).__name__ == model_class
    assert model.model_name == "some-model"


def test_a_missing_key_fails_naming_the_providers_own_variable(monkeypatch):
    """No key anywhere -> the provider's clear error, which `client_for_run` degrades on."""
    pytest.importorskip("groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(Exception, match="GROQ_API_KEY"):
        backends._generic_model(_llm(backend="groq", model="m"), {})


@pytest.fixture
def aws_env(monkeypatch):
    """Fake AWS credentials.

    With none resolvable, boto3 falls through to a real network probe (EC2 instance
    metadata), which the socket-blocking test guard rightly rejects.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")


def test_bedrock_builds_with_only_a_region(aws_env):
    """Bedrock's auth is AWS IAM; a region is enough, and a colon in the model id survives."""
    pytest.importorskip("boto3")
    model = backends._generic_model(
        _llm(backend="bedrock", model="anthropic.claude-opus-4-6-v1:0", aws_region="us-east-1"), {}
    )
    assert type(model).__name__ == "BedrockConverseModel"
    assert model.model_name == "anthropic.claude-opus-4-6-v1:0"


def test_bedrock_ca_bundle_reaches_the_boto_client(aws_env, monkeypatch):
    """Bedrock has no `http_client`; the bundle reaches boto via AWS_CA_BUNDLE, then is removed again."""
    pytest.importorskip("boto3")
    monkeypatch.delenv("AWS_CA_BUNDLE", raising=False)
    model = backends._generic_model(
        _llm(backend="bedrock", model="m", aws_region="us-east-1", ca_bundle=certifi.where()), {}
    )
    assert model.client._endpoint.http_session._verify == certifi.where()
    assert "AWS_CA_BUNDLE" not in os.environ


def test_bedrock_ca_bundle_restores_a_preexisting_variable(monkeypatch):
    """A caller's own AWS_CA_BUNDLE survives, even when the build fails."""
    monkeypatch.setenv("AWS_CA_BUNDLE", "/mine.pem")
    with pytest.raises(RuntimeError), backends._aws_ca_bundle("/ours.pem"):
        assert os.environ["AWS_CA_BUNDLE"] == "/ours.pem"
        raise RuntimeError
    assert os.environ["AWS_CA_BUNDLE"] == "/mine.pem"


def test_bedrock_keeps_the_providers_own_setup_with_a_ca_bundle(aws_env, monkeypatch):
    """A bearer token still works alongside a CA bundle (a pre-built boto client would skip it)."""
    pytest.importorskip("boto3")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok")
    model = backends._generic_model(
        _llm(backend="bedrock", model="m", aws_region="us-east-1", ca_bundle=certifi.where()), {}
    )
    assert model.client.meta.config.signature_version == "bearer"
    assert model.client.meta.config.read_timeout == 300


def test_bedrock_falls_back_to_the_aws_region_env_var(aws_env, monkeypatch):
    """No aws_region configured -> AWS_DEFAULT_REGION is used."""
    pytest.importorskip("boto3")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    backends._generic_model(_llm(backend="bedrock", model="anthropic.claude-opus-4-6-v1:0"), {})


def test_bedrock_mantle_builds_an_openai_shaped_model(aws_env):
    """Bedrock Mantle serves OpenAI-shaped models over AWS auth."""
    pytest.importorskip("boto3")
    pytest.importorskip("openai")
    model = backends._generic_model(
        _llm(backend="bedrock-mantle", model="openai.gpt-oss-120b", aws_region="us-east-1"), {}
    )
    assert type(model).__name__.startswith("BedrockMantle")
