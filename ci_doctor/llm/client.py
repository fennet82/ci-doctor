"""LLMClient over any OpenAI-compatible endpoint (Ollama, vLLM, llama.cpp, LocalAI,
internal gateway). Pure HTTP via the openai SDK — no runtime downloads.

The endpoint's own CA bundle is honoured; proxies and SSL_CERT_FILE come from the
environment via httpx's trust_env. API key is optional (local servers usually have
none). openai/httpx are imported lazily so nothing here is pulled in unless a real
model is actually configured and called.
"""

import json
import os
from typing import Any

from ci_doctor.config.schema import LLMConfig
from ci_doctor.core.ports import LLMClient


def _strip_fences(text: str) -> str:
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
            {"role": "system", "content": "Respond with a single JSON object and nothing else."},
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
        return json.loads(_strip_fences(content))
