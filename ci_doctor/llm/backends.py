"""LLM backend registry.

Every backend implements the LLMClient port; the config `llm.backend` selects
one. All use prompt-and-parse (the prompt already embeds the schema and a
JSON-only instruction), so correctness is enforced by the caller's pydantic
validation + repair retry — not by the backend.

`openai` is the base install and covers anything speaking the OpenAI Chat Completions
shape, which is most of the field. `litellm` exists for the providers that do *not*
— Bedrock's SigV4, Vertex's GCP auth, Azure's URL scheme — and is imported lazily so
the base install stays lean and air-gap-clean (`ci-doctor[litellm]`); it can pull
tiktoken, which fetches vocab at runtime, so it is unfit for a strict air gap.
`claude_code` shells out to the local `claude` CLI (stdlib subprocess, no dep, but
the binary must be on PATH).

Every SDK is imported inside the call that needs it, so importing this module
costs nothing on a run that never reaches a model. The `openai` backend honours
the endpoint's own CA bundle, and picks up proxies and `SSL_CERT_FILE` from the
environment via httpx's trust_env.
"""

import json
import os
import shutil
from typing import Any

from ci_doctor.config.schema import LLMConfig
from ci_doctor.core.ports import LLMClient

#: System message shared by every backend. The reply schema lives in the user
#: prompt; this only pins the *shape* of the response.
_SYSTEM = "Respond with a single JSON object and nothing else. No markdown, no code fences, no prose."


def strip_fences(text: str) -> str:
    """Unwrap a ```-fenced code block.

    Models add fences even when told to reply with bare JSON, so this runs
    unconditionally rather than as an error path.

    Args:
        text: The model's raw reply.

    Returns:
        The reply with any surrounding fence removed.
    """
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


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
        return OpenAILLMClient(cfg, environ=environ)
    if cfg.backend == "litellm":
        return LiteLLMClient(cfg, environ=environ)
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
    if cfg.backend == "claude_code":
        return shutil.which("claude") is not None
    return False


class OpenAILLMClient(LLMClient):
    """Talks to any OpenAI-compatible chat-completions endpoint."""

    def __init__(self, cfg: LLMConfig, environ: dict[str, str] | None = None):
        """Store config; no connection is opened until a call is made.

        Args:
            cfg: LLM settings — model, api_base, key env var, CA bundle, timeout.
            environ: Environment to read the API key from. Defaults to os.environ.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def _api_key(self) -> str:
        """Resolve the API key.

        Returns:
            The configured key, or the literal "no-key" — local servers ignore it,
            but the openai SDK refuses an empty string.
        """
        if self.cfg.api_key_env:
            return self.environ.get(self.cfg.api_key_env) or "no-key"
        return "no-key"  # openai SDK requires a non-empty string even when the server ignores it

    def _client(self):
        """Build the SDK client, honouring a custom CA bundle.

        Returns:
            A configured `openai.OpenAI`. Imported lazily so the SDK is never
            pulled in unless a model is actually configured and called.
        """
        from openai import OpenAI

        kwargs: dict[str, Any] = {
            "base_url": self.cfg.api_base,
            "api_key": self._api_key(),
            "timeout": self.cfg.timeout_seconds,
        }
        if self.cfg.ca_bundle:
            import httpx

            kwargs["http_client"] = httpx.Client(verify=self.cfg.ca_bundle)
        return OpenAI(**kwargs)

    def complete_structured(self, prompt: str) -> dict:
        """Run one completion and parse the reply as JSON.

        Args:
            prompt: The rendered, already-redacted prompt, schema included.

        Returns:
            The parsed reply. Validation is the caller's job.

        Raises:
            json.JSONDecodeError: If the reply is not JSON.
            Exception: Any transport or API error from the SDK.
        """
        client = self._client()
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = client.chat.completions.create(
                model=self.cfg.model,
                messages=messages,
                temperature=self.cfg.temperature,
                response_format={"type": "json_object"},
            )
        except Exception:  # noqa: BLE001 - some OpenAI-compatible servers reject response_format
            resp = client.chat.completions.create(
                model=self.cfg.model,
                messages=messages,
                temperature=self.cfg.temperature,
            )
        content = resp.choices[0].message.content or ""
        return json.loads(strip_fences(content))


class LiteLLMClient(LLMClient):
    """A provider litellm can reach that the OpenAI shape cannot.

    Bedrock, Vertex, Azure. For anything OpenAI-compatible prefer `openai`: no
    extra dependency.
    """

    def __init__(self, cfg: LLMConfig, environ: dict[str, str] | None = None):
        """Store config; litellm is imported only when a call is made.

        Args:
            cfg: LLM settings.
            environ: Environment for the API key. Defaults to os.environ.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def complete_structured(self, prompt: str) -> dict:
        """Run one completion through litellm.

        Args:
            prompt: The rendered, already-redacted prompt, schema included.

        Returns:
            The parsed reply.

        Raises:
            json.JSONDecodeError: If the reply is not JSON.
            Exception: Any provider error.
        """
        # Unresolved by design: the `litellm` extra is optional (see the module
        # docstring). Making the type checker resolve it would mean installing the
        # dependency whose whole point is not being in the base install.
        import litellm  # ty: ignore[unresolved-import]

        litellm.telemetry = False  # no phone-home
        api_key = self.environ.get(self.cfg.api_key_env) if self.cfg.api_key_env else None
        kwargs = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
            "api_base": self.cfg.api_base or None,
            "api_key": api_key,
            "temperature": self.cfg.temperature,
            "timeout": self.cfg.timeout_seconds,
        }
        try:
            resp = litellm.completion(response_format={"type": "json_object"}, **kwargs)
        except Exception:  # noqa: BLE001 - provider may reject response_format; retry without
            resp = litellm.completion(**kwargs)
        return json.loads(strip_fences(resp.choices[0].message.content or ""))


class ClaudeCodeClient(LLMClient):
    """Shell out to the local `claude` CLI in headless print mode.

    Uses whatever auth Claude Code is configured with; no API key or endpoint is
    needed here.
    """

    def __init__(self, cfg: LLMConfig, environ: dict[str, str] | None = None):
        """Store config; the CLI is located at call time, not here.

        Args:
            cfg: LLM settings; only `model` and `timeout_seconds` are used.
            environ: Environment passed through to the subprocess.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def complete_structured(self, prompt: str) -> dict:
        """Run one headless `claude -p` and unwrap its JSON envelope.

        Args:
            prompt: The rendered, already-redacted prompt, schema included.
                Passed on stdin, which avoids ARG_MAX on large evidence bundles.

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
        proc = subprocess.run(  # noqa: S603 — argv list, no shell; binary resolved above
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.cfg.timeout_seconds,
            env=self.environ,
            check=False,  # the return code is read below, with the CLI's own stderr
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed ({proc.returncode}): {proc.stderr[:500]}")
        envelope = json.loads(proc.stdout)  # {"result": "<model text>", ...}
        result = envelope.get("result", "") if isinstance(envelope, dict) else str(envelope)
        return json.loads(strip_fences(result))
