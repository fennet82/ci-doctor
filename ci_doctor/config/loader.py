"""Layered config loading: schema defaults < defaults.yml < repo file < env < flags.

Mappings deep-merge; lists/scalars replace. Unknown keys in a config *file* are a
hard error (schema uses extra="forbid"). Env vars are more permissive by
necessity (see `_env_overlay`).
"""

from __future__ import annotations

import os
from copy import deepcopy
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ci_doctor.config.schema import Config

_ENV_PREFIX = "CI_DOCTOR_"
_TOP_LEVEL = set(Config.model_fields)  # provider, gitlab, llm, ...
_DEFAULT_REPO_CONFIG = Path(".ci-doctor.yml")


def _deep_merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _load_yaml(text: str) -> dict:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")
    return data


def _shipped_defaults() -> dict:
    text = resources.files("ci_doctor.config").joinpath("defaults.yml").read_text()
    return _load_yaml(text)


def _coerce(raw: str) -> Any:
    # Env values are strings; parse via YAML so `true`, `0.7`, `[a, b]` work.
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _env_overlay(environ: dict[str, str]) -> dict:
    """CI_DOCTOR_LLM__MODEL=x -> {"llm": {"model": "x"}}. "__" nests.

    Only vars whose first segment is a known top-level config field are consumed.
    That deliberately skips secret env vars like CI_DOCTOR_GITLAB_TOKEN (referenced
    by *name* via `token_env`, not a config key) so their presence never turns into
    an extra-key validation error.
    """
    out: dict = {}
    for key, value in environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        path = key[len(_ENV_PREFIX):].lower().split("__")
        if path[0] not in _TOP_LEVEL:
            continue
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return out


def load_config(
    repo_config: str | Path | None = None,
    overrides: dict | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    """Assemble and validate config. `overrides` is the CLI-flag layer (highest)."""
    merged = _shipped_defaults()

    if repo_config is None and _DEFAULT_REPO_CONFIG.is_file():
        repo_config = _DEFAULT_REPO_CONFIG
    if repo_config is not None:
        path = Path(repo_config)
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
        merged = _deep_merge(merged, _load_yaml(path.read_text()))

    merged = _deep_merge(merged, _env_overlay(os.environ if environ is None else environ))
    if overrides:
        merged = _deep_merge(merged, overrides)

    return Config(**merged)  # extra="forbid" -> unknown keys raise ValidationError
