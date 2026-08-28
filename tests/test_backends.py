"""Backend registry: selection, readiness, and the claude_code CLI path.

No network, no litellm installed — clients are lazy-importing, so
construction and selection are testable without the optional deps.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.llm.backends import (
    ClaudeCodeClient,
    LiteLLMClient,
    OpenAILLMClient,
    backend_ready,
    make_client,
)


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
    out = ClaudeCodeClient(_llm(backend="claude_code")).complete_structured("prompt")
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
        ClaudeCodeClient(_llm(backend="claude_code")).complete_structured("p")


def _reply(content):
    """A minimal stand-in for the SDK's ChatCompletion response object."""
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _bad_request():
    """A real openai.BadRequestError, built offline — no socket is opened."""
    import httpx
    from openai import BadRequestError

    request = httpx.Request("POST", "http://stub/v1/chat/completions")
    return BadRequestError(
        "response_format is unsupported",
        response=httpx.Response(400, request=request),
        body=None,
    )


class _FakeCompletions:
    """Scripted `chat.completions`, recording the kwargs of every call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        """Record the call and replay the next scripted outcome."""
        self.calls.append(kwargs)
        outcome = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _wire(client, script):
    """Give an OpenAILLMClient a scripted SDK client, bypassing the network."""
    completions = _FakeCompletions(script)
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client._client = lambda: sdk
    return completions


def test_a_transport_failure_is_not_retried_without_response_format():
    """Only a 400 means "this server rejects response_format" — everything else must propagate.

    A bare `except Exception` here re-issues the identical request that just burned
    the full timeout, doubling the wall time of every outage.
    """
    client = OpenAILLMClient(_llm(model="m", api_base="http://stub"))
    completions = _wire(client, [TimeoutError("read timed out")])
    with pytest.raises(TimeoutError):
        client.complete_structured("prompt")
    assert len(completions.calls) == 1


def test_a_400_retries_once_without_response_format():
    """A server that rejects response_format gets one retry without it."""
    client = OpenAILLMClient(_llm(model="m", api_base="http://stub"))
    completions = _wire(client, [_bad_request(), _reply('{"ok": true}')])
    assert client.complete_structured("prompt") == {"ok": True}
    assert len(completions.calls) == 2
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]


def test_a_rejected_response_format_is_remembered():
    """The failed probe costs one request per run, not one per job."""
    client = OpenAILLMClient(_llm(model="m", api_base="http://stub"))
    completions = _wire(client, [_bad_request(), _reply('{"ok": true}')])
    client.complete_structured("first")
    client.complete_structured("second")
    assert len(completions.calls) == 3  # probe + retry, then one call for the second job
    assert "response_format" not in completions.calls[2]


def test_the_sdk_client_is_built_once_and_reused(monkeypatch):
    """One connection pool per client, not one per call."""
    built = []

    def fake_openai(**kwargs):
        built.append(kwargs)
        completions = _FakeCompletions([_reply('{"ok": true}')])
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    monkeypatch.setattr("openai.OpenAI", fake_openai)
    client = OpenAILLMClient(_llm(model="m", api_base="http://stub"))
    client.complete_structured("first")
    client.complete_structured("second")
    assert len(built) == 1


def test_max_retries_reaches_the_sdk(monkeypatch):
    """The SDK's own retry count is set from config, not left at its default of 2."""
    built = []

    def fake_openai(**kwargs):
        built.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions([_reply('{"ok": true}')])))

    monkeypatch.setattr("openai.OpenAI", fake_openai)
    client = OpenAILLMClient(_llm(model="m", api_base="http://stub", max_retries=0))
    client.complete_structured("prompt")
    assert built[0]["max_retries"] == 0


def test_claude_code_cli_runs_isolated(monkeypatch):
    """The analyzer subprocess gets no tools, no MCP servers and no inherited settings.

    Without these the CLI loads the developer's own Claude Code environment on every
    call — measurably slower, and it hands a log-analysis subprocess write access to
    the repository it is analyzing (invariant #10 is read-only).
    """
    monkeypatch.setattr("ci_doctor.llm.backends.shutil.which", lambda _: "/usr/bin/claude")
    seen = {}

    def fake_run(cmd, **kwargs):
        """Capture the argv the backend built."""
        seen["cmd"] = cmd
        envelope = json.dumps({"result": json.dumps({"ok": True})})
        return subprocess.CompletedProcess(cmd, 0, stdout=envelope, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ClaudeCodeClient(_llm(backend="claude_code")).complete_structured("p")

    cmd = seen["cmd"]
    assert cmd[1:4] == ["-p", "--output-format", "json"]
    assert "--max-turns" in cmd and cmd[cmd.index("--max-turns") + 1] == "1"
    assert cmd[cmd.index("--disallowedTools") + 1] == "*"
    assert "--strict-mcp-config" in cmd
    assert json.loads(cmd[cmd.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in cmd
    # --bare is deliberately absent: it authenticates only via ANTHROPIC_API_KEY.
    assert "--bare" not in cmd
