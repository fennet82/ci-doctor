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
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ci_doctor.config.schema import LLMConfig
from ci_doctor.core.ports import LLMClient

if TYPE_CHECKING:
    from openai import OpenAI
    from openai.types.chat import ChatCompletionMessageParam

#: System message shared by every backend. The reply schema lives in the user
#: prompt; this only pins the *shape* of the response.
_SYSTEM = "Respond with a single JSON object and nothing else. No markdown, no code fences, no prose."

#: Flags that cut the `claude` CLI down to "answer this one prompt".
#:
#: Without them it starts a full interactive-grade session per call: the developer's
#: MCP servers, settings and CLAUDE.md are loaded and prepended to every ci-doctor
#: prompt, and the subprocess inherits their tool permissions. Two reasons that is
#: wrong here — it measured 31s/job against 19s with these flags, and a subprocess
#: whose only job is to *read* a log must not be able to run Bash or edit the repo
#: it is analyzing (invariant #10). `--max-turns 1` also pins it to one answer
#: rather than an agent loop that runs until `timeout_seconds`.
_CLAUDE_ISOLATION = [
    "--max-turns",
    "1",
    "--allowed-tools",
    "",
    "--strict-mcp-config",
    "--mcp-config",
    '{"mcpServers":{}}',
    "--setting-sources",
    "",
]


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

    def __init__(self, cfg: LLMConfig, environ: Mapping[str, str] | None = None) -> None:
        """Store config; no connection is opened until a call is made.

        Args:
            cfg: LLM settings — model, api_base, key env var, CA bundle, timeout.
            environ: Environment to read the API key from. Defaults to os.environ.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ
        self._sdk: OpenAI | None = None
        self._sdk_lock = threading.Lock()
        #: Set once a server answers 400 to `response_format`, so the probe that
        #: discovers it costs one request per run rather than one per job.
        self._no_response_format = False

    def _api_key(self) -> str:
        """Resolve the API key.

        Returns:
            The configured key, or the literal "no-key" — local servers ignore it,
            but the openai SDK refuses an empty string.
        """
        if self.cfg.api_key_env:
            return self.environ.get(self.cfg.api_key_env) or "no-key"
        return "no-key"  # openai SDK requires a non-empty string even when the server ignores it

    def _client(self) -> "OpenAI":
        """Build the SDK client once, honouring a custom CA bundle.

        Memoized: one client means one connection pool for the whole run instead of
        a fresh TCP + TLS handshake per job. The lock is not paranoia — a run
        analyzes its jobs on a thread pool (`analysis.max_parallel_jobs`), so this
        is reached concurrently.

        Returns:
            A configured `openai.OpenAI`. Imported lazily so the SDK is never
            pulled in unless a model is actually configured and called.
        """
        import openai

        with self._sdk_lock:
            if self._sdk is None:
                kwargs: dict[str, Any] = {
                    "base_url": self.cfg.api_base,
                    "api_key": self._api_key(),
                    "timeout": self.cfg.timeout_seconds,
                    # Left unset, the SDK retries twice on its own. That multiplies
                    # with the repair retry in `llm/report.py` and turns one dead
                    # endpoint into six requests, each waiting out `timeout_seconds`.
                    "max_retries": self.cfg.max_retries,
                }
                if self.cfg.ca_bundle:
                    import httpx

                    kwargs["http_client"] = httpx.Client(verify=self.cfg.ca_bundle)
                self._sdk = openai.OpenAI(**kwargs)
        return self._sdk

    def complete_structured(self, prompt: str) -> dict[str, Any]:
        """Run one completion and parse the reply as JSON.

        Args:
            prompt: The rendered, already-redacted prompt, schema included.

        Returns:
            The parsed reply. Validation is the caller's job.

        Raises:
            json.JSONDecodeError: If the reply is not JSON.
            Exception: Any transport or API error from the SDK.
        """
        from openai import BadRequestError

        if not self.cfg.model:
            # `backend_ready` checks this, but an injected client skips that path.
            raise ValueError("llm.model is required for the openai backend")
        client = self._client()
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ]
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
        }
        if self._no_response_format:
            resp = client.chat.completions.create(**kwargs)
        else:
            try:
                resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
            except BadRequestError:
                # Only a 400 means the server rejects the parameter. Catching every
                # exception here re-sent the request after a timeout, a 429 or an
                # auth failure — doubling the wall time of every outage.
                self._no_response_format = True
                resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        return json.loads(strip_fences(content))


class LiteLLMClient(LLMClient):
    """A provider litellm can reach that the OpenAI shape cannot.

    Bedrock, Vertex, Azure. For anything OpenAI-compatible prefer `openai`: no
    extra dependency.
    """

    def __init__(self, cfg: LLMConfig, environ: Mapping[str, str] | None = None) -> None:
        """Store config; litellm is imported only when a call is made.

        Args:
            cfg: LLM settings.
            environ: Environment for the API key. Defaults to os.environ.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def complete_structured(self, prompt: str) -> dict[str, Any]:
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
        # Only a 400-shaped error means the provider rejects the parameter; retrying
        # a timeout or a 429 just waits out `timeout_seconds` twice. litellm re-exports
        # openai's exception types, and `UnsupportedParamsError` is not in every version.
        rejected = (litellm.exceptions.BadRequestError,)
        unsupported = getattr(litellm.exceptions, "UnsupportedParamsError", None)
        if unsupported is not None:
            rejected += (unsupported,)
        try:
            resp = litellm.completion(response_format={"type": "json_object"}, **kwargs)
        except rejected:
            resp = litellm.completion(**kwargs)
        return json.loads(strip_fences(resp.choices[0].message.content or ""))


class ClaudeCodeClient(LLMClient):
    """Shell out to the local `claude` CLI in headless print mode.

    Uses whatever auth Claude Code is configured with; no API key or endpoint is
    needed here.
    """

    def __init__(self, cfg: LLMConfig, environ: Mapping[str, str] | None = None) -> None:
        """Store config; the CLI is located at call time, not here.

        Only `model` and `timeout_seconds` reach the CLI. `llm.temperature` does
        **not** — `claude -p` exposes no temperature flag — so the reproducibility
        that knob promises elsewhere is not available on this backend. Documented
        rather than silently dropped, because the config says otherwise.

        Args:
            cfg: LLM settings; only `model` and `timeout_seconds` are used.
            environ: Environment passed through to the subprocess.
        """
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def complete_structured(self, prompt: str) -> dict[str, Any]:
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
        cmd = [binary, "-p", "--output-format", "json", *_CLAUDE_ISOLATION]
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
