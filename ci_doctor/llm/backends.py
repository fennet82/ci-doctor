"""LLM backend registry. Every backend implements the LLMClient port; the config
`llm.backend` selects one. All use prompt-and-parse (the prompt already embeds the
schema and a JSON-only instruction), so correctness is enforced by the caller's
pydantic validation + repair retry — not by the backend.

Heavy/optional deps (litellm, anthropic) are imported lazily inside each client so
the base install stays lean and air-gap-clean; install via `ci-doctor[litellm]` /
`ci-doctor[anthropic]`. `claude_code` shells out to the local `claude` CLI (stdlib
subprocess, no dep, but the binary must be on PATH).
"""

from __future__ import annotations

import json
import os
import shutil

from ci_doctor.config.schema import LLMConfig
from ci_doctor.core.ports import LLMClient
from ci_doctor.llm.client import _strip_fences

#: System message shared by every backend. The reply schema lives in the user
#: prompt; this only pins the *shape* of the response.
_SYSTEM = "Respond with a single JSON object and nothing else. No markdown, no code fences, no prose."


def make_client(cfg: LLMConfig, environ: dict[str, str] | None = None) -> LLMClient:
    """Build the client for the configured backend.

    Args:
        cfg: LLM settings; `cfg.backend` selects the implementation.
        environ: Environment for API keys. Defaults to os.environ.

    Returns:
        A client implementing :class:`~ci_doctor.core.ports.LLMClient`.

    Raises:
        ValueError: On an unknown backend name.
    """
    if cfg.backend == "openai":
        from ci_doctor.llm.client import OpenAILLMClient

        return OpenAILLMClient(cfg, environ=environ)
    if cfg.backend == "litellm":
        return LiteLLMClient(cfg, environ=environ)
    if cfg.backend == "anthropic":
        return AnthropicLLMClient(cfg, environ=environ)
    if cfg.backend == "claude_code":
        return ClaudeCodeClient(cfg, environ=environ)
    raise ValueError(f"unknown llm.backend: {cfg.backend}")


def backend_ready(cfg: LLMConfig) -> bool:
    """Check whether a backend can run as configured.

    Checked *before* attempting a call so an unconfigured backend produces the
    clean deterministic report rather than a failed call and a degraded one.

    Args:
        cfg: LLM settings.

    Returns:
        True if the backend has everything it needs. Unknown backends are False.
    """
    if cfg.backend == "openai":
        return bool(cfg.model and cfg.api_base)
    if cfg.backend == "litellm":
        return bool(cfg.model)
    if cfg.backend == "anthropic":
        return True  # model defaults to claude-opus-4-8; auth from env / ant profile
    if cfg.backend == "claude_code":
        return shutil.which("claude") is not None
    return False


class LiteLLMClient(LLMClient):
    """Any litellm-supported provider (OpenAI, Gemini, Bedrock, Vertex, Ollama, …)."""

    def __init__(self, cfg: LLMConfig, environ: dict[str, str] | None = None):
        """Store config; litellm is imported only when a call is made.

        Args:
            cfg: LLM settings.
            environ: Environment for the API key. Defaults to os.environ.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def complete_structured(self, prompt: str, schema: dict) -> dict:
        """Run one completion through litellm.

        Args:
            prompt: The rendered, already-redacted prompt.
            schema: JSON Schema of the expected reply, already embedded in the prompt.

        Returns:
            The parsed reply.

        Raises:
            json.JSONDecodeError: If the reply is not JSON.
            Exception: Any provider error.
        """
        import litellm

        litellm.telemetry = False  # no phone-home
        api_key = self.environ.get(self.cfg.api_key_env) if self.cfg.api_key_env else None
        kwargs = dict(
            model=self.cfg.model,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
            api_base=self.cfg.api_base or None,
            api_key=api_key,
            temperature=self.cfg.temperature,
            timeout=self.cfg.timeout_seconds,
        )
        try:
            resp = litellm.completion(response_format={"type": "json_object"}, **kwargs)
        except Exception:  # noqa: BLE001 - provider may reject response_format; retry without
            resp = litellm.completion(**kwargs)
        return json.loads(_strip_fences(resp.choices[0].message.content or ""))


class AnthropicLLMClient(LLMClient):
    """Official Anthropic SDK. No `temperature` (rejected on claude-opus-4-8)."""

    def __init__(self, cfg: LLMConfig, environ: dict[str, str] | None = None):
        """Store config; the SDK is imported only when a call is made.

        Args:
            cfg: LLM settings.
            environ: Environment for the API key. Defaults to os.environ.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def _client(self):
        """Build the SDK client.

        Returns:
            A configured `anthropic.Anthropic`. Every argument is optional — with
            none set the SDK falls back to its own env/profile auth.
        """
        import anthropic

        kwargs: dict = {"timeout": self.cfg.timeout_seconds}
        if self.cfg.api_key_env and self.environ.get(self.cfg.api_key_env):
            kwargs["api_key"] = self.environ[self.cfg.api_key_env]
        if self.cfg.api_base:
            kwargs["base_url"] = self.cfg.api_base
        if self.cfg.ca_bundle:
            from anthropic import DefaultHttpxClient

            kwargs["http_client"] = DefaultHttpxClient(verify=self.cfg.ca_bundle)
        return anthropic.Anthropic(**kwargs)

    def complete_structured(self, prompt: str, schema: dict) -> dict:
        """Run one Messages API call.

        Args:
            prompt: The rendered, already-redacted prompt.
            schema: JSON Schema of the expected reply, already embedded in the prompt.

        Returns:
            The parsed reply, concatenated from every text block.

        Raises:
            json.JSONDecodeError: If the reply is not JSON.
            Exception: Any API error.
        """
        resp = self._client().messages.create(
            model=self.cfg.model or "claude-opus-4-8",
            max_tokens=8192,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return json.loads(_strip_fences(text))


class ClaudeCodeClient(LLMClient):
    """Shell out to the local `claude` CLI in headless print mode. Uses whatever
    auth Claude Code is configured with; no API key/endpoint needed here."""

    def __init__(self, cfg: LLMConfig, environ: dict[str, str] | None = None):
        """Store config; the CLI is located at call time, not here.

        Args:
            cfg: LLM settings; only `model` and `timeout_seconds` are used.
            environ: Environment passed through to the subprocess.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def complete_structured(self, prompt: str, schema: dict) -> dict:
        """Run one headless `claude -p` and unwrap its JSON envelope.

        Args:
            prompt: The rendered, already-redacted prompt. Passed on stdin, which
                avoids ARG_MAX on large evidence bundles.
            schema: JSON Schema of the expected reply, already embedded in the prompt.

        Returns:
            The parsed reply, taken from the envelope's `result` field.

        Raises:
            RuntimeError: If the CLI is missing from PATH or exits non-zero.
            json.JSONDecodeError: If the envelope or the reply is not JSON.
            subprocess.TimeoutExpired: If the CLI outruns `timeout_seconds`.
        """
        import subprocess

        binary = shutil.which("claude")
        if binary is None:
            raise RuntimeError("`claude` CLI not found on PATH")
        cmd = [binary, "-p", "--output-format", "json"]
        if self.cfg.model:
            cmd += ["--model", self.cfg.model]
        proc = subprocess.run(  # prompt on stdin avoids ARG_MAX on large prompts
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.cfg.timeout_seconds,
            env=self.environ,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed ({proc.returncode}): {proc.stderr[:500]}")
        envelope = json.loads(proc.stdout)  # {"result": "<model text>", ...}
        result = envelope.get("result", "") if isinstance(envelope, dict) else str(envelope)
        return json.loads(_strip_fences(result))
