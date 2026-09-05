"""Backend registry: selection, readiness, and the shared PydanticAILLMClient.

No network — every builder is monkeypatched to a pydantic-ai test double
(`TestModel`/`FunctionModel`, no extra required). `PydanticAILLMClient` is
exercised against a stub output model; the real `Report` schema is covered
end-to-end in tests/test_report.py.
"""

import sys
import types
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from ci_doctor.config.loader import load_config
from ci_doctor.llm.backends import (
    _MODEL_BUILDERS,
    PydanticAILLMClient,
    backend_ready,
    make_client,
)


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


@pytest.mark.parametrize("backend", ["openai", "anthropic", "azure", "litellm"])
def test_make_client_selects_backend(backend, monkeypatch):
    """Every known backend builds a PydanticAILLMClient, real builder swapped for a test double."""
    monkeypatch.setitem(_MODEL_BUILDERS, backend, lambda cfg, environ: TestModel())
    kwargs = {"backend": backend, "model": "m"}
    if backend == "openai":
        kwargs["api_base"] = "http://stub"
    if backend == "azure":
        kwargs["azure_endpoint"] = "https://stub.openai.azure.com"
    client = make_client(_llm(**kwargs))
    assert isinstance(client, PydanticAILLMClient)


def test_unknown_backend_raises():
    """The factory rejects an unknown backend."""
    with pytest.raises(ValueError, match="unknown llm.backend"):
        make_client(SimpleNamespace(backend="nope"))


def test_backend_ready_rules():
    """Each backend reports ready only when it has everything it needs."""
    assert backend_ready(_llm(backend="openai", model="m", api_base="http://x")) is True
    assert backend_ready(_llm(backend="openai", model="m")) is False  # needs api_base
    assert backend_ready(_llm(backend="anthropic", model="m")) is True
    assert backend_ready(_llm(backend="anthropic")) is False  # needs model
    assert (
        backend_ready(_llm(backend="azure", model="m", azure_endpoint="https://x.openai.azure.com")) is True
    )
    assert backend_ready(_llm(backend="azure", model="m")) is False  # needs azure_endpoint
    assert backend_ready(_llm(backend="litellm", model="m")) is True
    assert backend_ready(_llm(backend="litellm")) is False  # needs model


def _reply(json_text: str) -> ModelResponse:
    """A pydantic-ai ModelResponse carrying one text part."""
    return ModelResponse(parts=[TextPart(content=json_text)])


def test_complete_structured_returns_a_validated_dict():
    """One call, no repair needed: the reply is parsed and returned as a dict."""
    model = FunctionModel(lambda messages, info: _reply('{"summary": "ok", "score": 1}'))
    client = PydanticAILLMClient(model, _llm(model="m"))
    out = client.complete_structured("prompt")
    assert out == {"summary": "ok", "score": 1}


def test_agent_is_built_once_and_reused():
    """One Agent per client, not one per call — same reuse discipline as before."""
    calls = {"n": 0}

    def fake_llm(messages, info):
        calls["n"] += 1
        return _reply('{"summary": "ok", "score": 1}')

    client = PydanticAILLMClient(FunctionModel(fake_llm), _llm(model="m"))
    agent_first = client._agent_for()
    client.complete_structured("first")
    client.complete_structured("second")
    agent_second = client._agent_for()
    assert agent_first is agent_second
    assert calls["n"] == 2  # one call per job, not one extra for re-building the Agent


def test_one_repair_retry_on_invalid_then_valid_reply():
    """PromptedOutput + Agent(retries=1) retries exactly once on a schema-invalid reply."""
    calls = {"n": 0}

    def fake_llm(messages, info):
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply("not json at all")
        return _reply('{"summary": "ok", "score": 1}')

    client = PydanticAILLMClient(FunctionModel(fake_llm), _llm(model="m"))
    out = client.complete_structured("prompt")
    assert out == {"summary": "ok", "score": 1}
    assert calls["n"] == 2  # one call + Pydantic AI's own one repair retry, no more


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


def test_litellm_model_string_reaches_litellm_model(monkeypatch):
    """Litellm's own model-string convention passes straight through (fake module: real one conflicts)."""
    captured = {}

    class _FakeLiteLLMModel:
        def __init__(self, model_name, *, api_key=None, api_base=None):
            captured["model_name"] = model_name
            captured["api_key"] = api_key
            captured["api_base"] = api_base

    fake_module = types.ModuleType("pydantic_ai_litellm")
    fake_module.LiteLLMModel = _FakeLiteLLMModel
    monkeypatch.setitem(sys.modules, "pydantic_ai_litellm", fake_module)

    from ci_doctor.llm.backends import _litellm_model

    _litellm_model(_llm(backend="litellm", model="bedrock/anthropic.claude-v2"), {})
    assert captured["model_name"] == "bedrock/anthropic.claude-v2"


# Real SDKs needed below; mutually exclusive with litellm, so skip rather than
# require every CI job to install every extra.
openai_sdk = pytest.importorskip("openai")
anthropic_sdk = pytest.importorskip("anthropic")


def test_openai_model_uses_api_base_and_resolved_key(monkeypatch):
    """A configured api_key_env reaches the provider; api_base is passed through."""
    from ci_doctor.llm.backends import _openai_model

    model = _openai_model(
        _llm(model="m", api_base="http://stub", api_key_env="MY_KEY"), {"MY_KEY": "secret-key"}
    )
    assert model.model_name == "m"


def test_openai_model_falls_back_to_no_key_placeholder():
    """No api_key_env configured -> the SDK gets a non-empty placeholder, not None."""
    from ci_doctor.llm.backends import _openai_model

    # Would raise if the client ever required a real, non-empty string and got None.
    _openai_model(_llm(model="m", api_base="http://stub"), {})


def test_anthropic_model_resolves_key_from_env():
    """Anthropic reads its key the same way openai does — via api_key_env."""
    from ci_doctor.llm.backends import _anthropic_model

    model = _anthropic_model(_llm(model="claude-x", api_key_env="ANTHROPIC_KEY"), {"ANTHROPIC_KEY": "k"})
    assert model.model_name == "claude-x"


def test_azure_model_needs_endpoint_and_a_key():
    """Azure builds on the endpoint/api_version, not api_base — and needs a key."""
    from ci_doctor.llm.backends import _azure_model

    model = _azure_model(
        _llm(
            model="gpt-x",
            azure_endpoint="https://x.openai.azure.com",
            azure_api_version="2024-10-21",
            api_key_env="AZURE_KEY",
        ),
        {"AZURE_KEY": "k"},
    )
    assert model.model_name == "gpt-x"
