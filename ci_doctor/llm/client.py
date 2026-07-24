"""LLMClient over any OpenAI-compatible endpoint (Ollama, vLLM, llama.cpp, LocalAI,
internal gateway). Pure HTTP via the openai SDK — no runtime downloads.

The endpoint's own CA bundle is honoured; proxies and SSL_CERT_FILE come from the
environment via httpx's trust_env. API key is optional (local servers usually have
none). openai/httpx are imported lazily so nothing here is pulled in unless a real
model is actually configured and called.
"""

from __future__ import annotations

import json
import os

from ci_doctor.config.schema import LLMConfig
from ci_doctor.core.ports import LLMClient


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


class OpenAILLMClient(LLMClient):
    def __init__(self, cfg: LLMConfig, environ: dict[str, str] | None = None):
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ

    def _api_key(self) -> str:
        if self.cfg.api_key_env:
            return self.environ.get(self.cfg.api_key_env) or "no-key"
        return "no-key"  # openai SDK requires a non-empty string even when the server ignores it

    def _client(self):
        from openai import OpenAI

        kwargs = {"base_url": self.cfg.api_base, "api_key": self._api_key(), "timeout": self.cfg.timeout_seconds}
        if self.cfg.ca_bundle:
            import httpx

            kwargs["http_client"] = httpx.Client(verify=self.cfg.ca_bundle)
        return OpenAI(**kwargs)

    def complete_structured(self, prompt: str, schema: dict) -> dict:
        client = self._client()
        messages = [
            {"role": "system", "content": "Respond with a single JSON object and nothing else."},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = client.chat.completions.create(
                model=self.cfg.model, messages=messages,
                temperature=self.cfg.temperature, response_format={"type": "json_object"},
            )
        except Exception:  # noqa: BLE001 - some OpenAI-compatible servers reject response_format
            resp = client.chat.completions.create(
                model=self.cfg.model, messages=messages, temperature=self.cfg.temperature,
            )
        content = resp.choices[0].message.content or ""
        return json.loads(_strip_fences(content))
