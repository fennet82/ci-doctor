"""Backend registry: selection, readiness, and the claude_code CLI path.

No network, no litellm installed — clients are lazy-importing, so
construction and selection are testable without the optional deps.
"""

import json
import subprocess

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.llm.backends import (
    ClaudeCodeClient,
    LiteLLMClient,
    backend_ready,
    make_client,
)
from ci_doctor.llm.client import OpenAILLMClient


def _llm(**over):
    """Build an LLMConfig from the shipped defaults, overridden by kwargs."""
    return load_config(environ={}, overrides={"llm": over}).llm


@pytest.mark.parametrize(
    "backend,cls",
    [
        ("openai", OpenAILLMClient),
        ("litellm", LiteLLMClient),
        ("claude_code", ClaudeCodeClient),
    ],
)
def test_make_client_selects_backend(backend, cls):
    """Each backend name builds its own client class."""
    assert isinstance(make_client(_llm(backend=backend)), cls)


def test_unknown_backend_raises():
    """The factory rejects an unknown backend.

    Config-level Literal validation blocks bad values earlier, so the factory is
    exercised directly here.
    """
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="unknown llm.backend"):
        make_client(SimpleNamespace(backend="nope"))


def test_backend_ready_rules(monkeypatch):
    """Each backend reports ready only when it has everything it needs."""
    assert backend_ready(_llm(backend="openai", model="m", api_base="http://x")) is True
    assert backend_ready(_llm(backend="openai", model="m")) is False  # needs api_base
    assert backend_ready(_llm(backend="litellm", model="m")) is True
    assert backend_ready(_llm(backend="litellm")) is False  # needs model

    monkeypatch.setattr("ci_doctor.llm.backends.shutil.which", lambda _: "/usr/bin/claude")
    assert backend_ready(_llm(backend="claude_code")) is True
    monkeypatch.setattr("ci_doctor.llm.backends.shutil.which", lambda _: None)
    assert backend_ready(_llm(backend="claude_code")) is False


def test_claude_code_client_parses_cli_envelope(monkeypatch):
    """The CLI's JSON envelope is unwrapped to the model's own JSON reply."""
    monkeypatch.setattr("ci_doctor.llm.backends.shutil.which", lambda _: "/usr/bin/claude")
    report = {"summary": "x", "failure_phase": "script"}  # inner JSON the model returned

    def fake_run(cmd, **kwargs):
        """Stand in for subprocess.run, returning a successful CLI envelope."""
        assert kwargs["input"], "prompt must be piped on stdin"
        envelope = json.dumps({"result": json.dumps(report), "session_id": "abc"})
        return subprocess.CompletedProcess(cmd, 0, stdout=envelope, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = ClaudeCodeClient(_llm(backend="claude_code")).complete_structured("prompt", {})
    assert out == report


def test_claude_code_client_raises_on_cli_failure(monkeypatch):
    """A non-zero CLI exit raises, so the caller can fall back deterministically."""
    monkeypatch.setattr("ci_doctor.llm.backends.shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"),
    )
    with pytest.raises(RuntimeError, match="claude CLI failed"):
        ClaudeCodeClient(_llm(backend="claude_code")).complete_structured("p", {})
