"""Layered config loading: schema defaults < defaults.yml < repo file < env < flags.

Mappings deep-merge; plain lists/scalars replace. The one exception is a list whose
every entry carries an ``id`` (today: ``extraction.matchers``) — those merge per id,
so a user pack adds to the shipped language packs instead of wiping them, and reusing
a shipped id overrides only the fields the user wrote. Unknown
keys in a config *file* are a hard error (schema uses ``extra="forbid"``). Env vars
are more permissive by necessity (see :func:`_env_overlay`).
"""

import logging
import os
from collections.abc import Iterable, Mapping
from copy import deepcopy
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ci_doctor.config.schema import Config

log = logging.getLogger(__name__)

_ENV_PREFIX = "CI_DOCTOR_"
_TOP_LEVEL = set(Config.model_fields)  # provider, gitlab, llm, ...
_DEFAULT_REPO_CONFIG = Path(".ci-doctor.yml")


def _is_id_keyed(value: Any) -> bool:
    """Whether ``value`` is a non-empty list whose every entry is a dict with an ``id``.

    Args:
        value: Any merged config value.

    Returns:
        True if the list can be merged per ``id`` rather than replaced wholesale.
    """
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) and "id" in item for item in value)
    )


def _merge_by_id(base: list[dict], over: list[dict], where: str) -> list[dict]:
    """Merge two id-keyed lists: same ``id`` deep-merges field by field.

    Only the keys the user actually wrote are overridden, so retuning one field
    (``priority: 95`` on the shipped ``pytest`` pack) keeps the rest of that pack
    instead of blanking it into a matcher that can never fire.

    Args:
        base: Entries from the lower-precedence layer (the shipped defaults).
        over: Entries from the higher-precedence layer (the user's config).
        where: Dotted config path, used only for the override warning.

    Returns:
        The base order preserved, overridden entries merged in place, and
        genuinely new entries appended.
    """
    merged = {item["id"]: deepcopy(item) for item in base}
    for item in over:
        if item["id"] in merged:
            log.warning("%s: config overrides the default %r entry", where, item["id"])
            merged[item["id"]] = _deep_merge(merged[item["id"]], item, where)
        else:
            merged[item["id"]] = deepcopy(item)
    return list(merged.values())


def _deep_merge(base: dict, over: dict, path: str = "") -> dict:
    """Recursively overlay ``over`` onto ``base``.

    Args:
        base: The lower-precedence layer. Not mutated.
        over: The higher-precedence layer.
        path: Dotted prefix of the current node, for override warnings.

    Returns:
        A new dict: mappings deep-merged, id-keyed lists merged per id, everything
        else replaced by ``over``.
    """
    out = deepcopy(base)
    for key, value in over.items():
        here = f"{path}.{key}" if path else key
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value, here)
        elif _is_id_keyed(value) and _is_id_keyed(out.get(key)):
            out[key] = _merge_by_id(out[key], value, here)
        else:
            out[key] = deepcopy(value)
    return out


def _load_yaml(text: str) -> dict:
    """Parse a YAML config document.

    Args:
        text: Raw YAML source.

    Returns:
        The parsed mapping; an empty dict for empty input.

    Raises:
        ValueError: If the document's root is not a mapping.
    """
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")
    return data


def _shipped_defaults() -> dict:
    """Read the packaged ``defaults.yml`` (phase map, matcher packs, noise patterns).

    Returns:
        The baseline config layer, before any user input.
    """
    text = resources.files("ci_doctor.config").joinpath("defaults.yml").read_text()
    return _load_yaml(text)


def default_config() -> Config:
    """Build the config as shipped, ignoring the repo file, env and CLI flags.

    This is the baseline ``ci-doctor config --diff`` compares against.

    Returns:
        The validated schema-defaults + ``defaults.yml`` config.
    """
    return Config(**_shipped_defaults())


def _coerce(raw: str) -> Any:
    """Parse an env var's string value into a YAML scalar.

    Args:
        raw: The raw environment value.

    Returns:
        The parsed value so ``true``, ``0.7`` and ``[a, b]` arrive typed; the
        original string if it is not valid YAML.
    """
    # Env values are strings; parse via YAML so `true`, `0.7`, `[a, b]` work.
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _env_overlay(environ: Mapping[str, str]) -> dict:
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
        path = key[len(_ENV_PREFIX) :].lower().split("__")
        if path[0] not in _TOP_LEVEL:
            continue
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return out


def load_config(
    repo_config: str | Path | Iterable[str | Path] | None = None,
    overrides: dict | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Assemble and validate the effective config from every layer.

    Layers apply lowest to highest: schema defaults, ``defaults.yml``, the repo
    config file(s), ``CI_DOCTOR_*`` env vars, then ``overrides``.

    Args:
        repo_config: One config file path, or several applied left to right so the
            last one wins. Defaults to ``./.ci-doctor.yml`` when that exists and
            none were given; no repo layer at all when it does not.
        overrides: The CLI-flag layer, applied last and therefore winning.
        environ: Environment to read. Defaults to :data:`os.environ`; tests pass
            ``{}`` to get a hermetic load.

    Returns:
        The validated config.

    Raises:
        FileNotFoundError: If a given path does not exist.
        pydantic.ValidationError: On an unknown or ill-typed key.
    """
    merged = _shipped_defaults()

    if isinstance(repo_config, (str, Path)):
        repo_config = [repo_config]
    paths = [Path(p) for p in repo_config or []]
    if not paths and _DEFAULT_REPO_CONFIG.is_file():
        paths = [_DEFAULT_REPO_CONFIG]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
        merged = _deep_merge(merged, _load_yaml(path.read_text()))

    merged = _deep_merge(merged, _env_overlay(os.environ if environ is None else environ))
    if overrides:
        merged = _deep_merge(merged, overrides)

    return Config(**merged)  # extra="forbid" -> unknown keys raise ValidationError
