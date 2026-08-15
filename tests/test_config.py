"""Config layering: defaults, file/env/flag precedence, and matcher pack merging."""

import logging

import pytest
from pydantic import ValidationError

from ci_doctor.config.loader import _deep_merge, default_config, load_config
from ci_doctor.config.schema import Config


def test_defaults_load_and_validate():
    """The shipped config loads and validates with no user input at all."""
    cfg = load_config(environ={})
    assert isinstance(cfg, Config)
    assert cfg.ci == "github"
    assert cfg.scm is None and cfg.scm_vendor == "github"  # the note follows the CI system
    assert cfg.phases["step_script"] == "script"  # from defaults.yml
    assert cfg.github.timeout_seconds == 30  # scalar default from schema
    assert cfg.github.base_url == "https://api.github.com"  # official default, overridable
    assert cfg.gitlab.base_url == "https://gitlab.com"


def test_shipped_matchers_parse():
    """The matcher packs in defaults.yml parse into the schema."""
    cfg = load_config(environ={})
    ids = {m.id for m in cfg.extraction.matchers}
    assert {"pytest", "generic_error"} <= ids


def test_scm_defaults_to_the_ci_system(tmp_path):
    """Left unset, the git host is the CI system — right for GitLab and GitHub."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("ci: github\n")
    cfg = load_config(repo_config=p, environ={})
    assert cfg.scm is None and cfg.scm_vendor == "github"


def test_scm_is_independent_of_ci(tmp_path):
    """The mixed case the split exists for: Jenkins building a GitLab repo."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("ci: jenkins\nscm: gitlab\n")
    cfg = load_config(repo_config=p, environ={})
    assert (cfg.ci, cfg.scm_vendor) == ("jenkins", "gitlab")


def test_deprecated_provider_key_still_selects_the_ci(tmp_path, caplog):
    """An existing `.ci-doctor.yml` keeps working, loudly."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("provider: github\n")
    with caplog.at_level(logging.WARNING):
        cfg = load_config(repo_config=p, environ={})
    assert cfg.ci == "github"
    assert "deprecated" in caplog.text


def test_provider_contradicting_ci_is_an_error(tmp_path):
    """Both keys set to different systems: refuse rather than pick one."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("ci: gitlab\nprovider: github\n")
    with pytest.raises(ValidationError):
        load_config(repo_config=p, environ={})


def test_unknown_key_is_error(tmp_path):
    """A typo in a config file raises rather than being silently ignored."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("gitlab:\n  nonsense_key: 1\n")
    with pytest.raises(ValidationError):
        load_config(repo_config=p, environ={})


def test_repo_file_overrides_defaults(tmp_path):
    """The repo file wins over the shipped defaults, key by key."""
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("gitlab:\n  base_url: https://gl.internal\n  timeout_seconds: 99\n")
    cfg = load_config(repo_config=p, environ={})
    assert cfg.gitlab.base_url == "https://gl.internal"
    assert cfg.gitlab.timeout_seconds == 99


def test_missing_repo_file_raises(tmp_path):
    """An explicitly named config file that does not exist is an error."""
    with pytest.raises(FileNotFoundError):
        load_config(repo_config=tmp_path / "nope.yml", environ={})


def test_env_overlay_nested_and_token_collision():
    """Nested env vars apply, and secret-looking ones are ignored, not rejected."""
    env = {
        "CI_DOCTOR_LLM__TEMPERATURE": "0.7",
        "CI_DOCTOR_GITLAB_TOKEN": "supersecret",  # secret, not a config key -> ignored
        "CI_DOCTOR_LLM_KEY": "k",  # ditto
    }
    cfg = load_config(environ=env)  # must not raise on the secret vars
    assert cfg.llm.temperature == 0.7


def test_deep_merge_list_replaces():
    """Ordinary lists replace wholesale rather than concatenating."""
    base = {"a": {"x": [1, 2]}, "b": 1}
    over = {"a": {"x": [9]}}
    assert _deep_merge(base, over) == {"a": {"x": [9]}, "b": 1}


def test_deep_merge_id_keyed_list_merges_instead_of_replacing():
    """Lists whose entries all carry an id merge per id."""
    # The one exception to "lists replace": entries that all carry an id.
    base = {"m": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    over = {"m": [{"id": "b", "v": 99}, {"id": "c", "v": 3}]}
    assert _deep_merge(base, over) == {"m": [{"id": "a", "v": 1}, {"id": "b", "v": 99}, {"id": "c", "v": 3}]}


def _write(tmp_path, body):
    """Write a `.ci-doctor.yml` into tmp_path and return its path."""
    path = tmp_path / ".ci-doctor.yml"
    path.write_text(body)
    return path


_CUSTOM_MATCHERS = """
extraction:
  matchers:
    - id: pytest
      pattern: '^MY OWN ANCHOR'
      priority: 99
    - id: my_pack
      pattern: '^BOOM'
"""


def test_user_matcher_overrides_default_but_keeps_the_rest(tmp_path):
    """A user pack overrides its namesake field by field and leaves every other pack."""
    cfg = load_config(repo_config=_write(tmp_path, _CUSTOM_MATCHERS), environ={})
    by_id = {m.id: m for m in cfg.extraction.matchers}
    shipped = {m.id: m for m in default_config().extraction.matchers}

    assert by_id["pytest"].pattern == "^MY OWN ANCHOR"  # the fields the user set win
    assert by_id["pytest"].priority == 99
    assert by_id["pytest"].start == shipped["pytest"].start  # untouched fields survive
    assert "my_pack" in by_id  # a new id is added
    assert len(by_id) == len(default_config().extraction.matchers) + 1  # nothing was dropped


def test_retuning_one_matcher_field_keeps_the_shipped_regexes(tmp_path):
    """Setting only `priority` must not blank the regexes into a matcher that never fires."""
    text = "extraction:\n  matchers:\n    - id: pytest\n      priority: 95\n"
    cfg = load_config(repo_config=_write(tmp_path, text), environ={})
    pytest_pack = next(m for m in cfg.extraction.matchers if m.id == "pytest")
    shipped = next(m for m in default_config().extraction.matchers if m.id == "pytest")

    assert pytest_pack.priority == 95
    assert (pytest_pack.start, pytest_pack.end) == (shipped.start, shipped.end)


def test_overriding_a_default_matcher_warns_with_its_id(tmp_path, caplog):
    """Shadowing a shipped pack warns and names it; adding a new one stays quiet."""
    with caplog.at_level(logging.WARNING, logger="ci_doctor.config.loader"):
        load_config(repo_config=_write(tmp_path, _CUSTOM_MATCHERS), environ={})

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("pytest" in w for w in warnings), warnings
    assert not any("my_pack" in w for w in warnings), "a brand-new id is not an override"


def test_default_config_ignores_the_repo_file(tmp_path, monkeypatch):
    """`default_config()` is the diff baseline, so it must ignore user layers."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "llm:\n  enabled: false\n")
    assert load_config(environ={}).llm.enabled is False  # picked up as the repo layer
    assert default_config().llm.enabled is True  # baseline ignores it


def test_json_schema_documents_every_field():
    """Every config field carries a description — the schema is a published asset."""
    # The schema is a published release asset; an undescribed field ships an
    # undocumented knob, so require a description on all of them.
    schema = Config.model_json_schema()
    undocumented = [
        f"{name}.{prop}"
        for name, model in ({"Config": schema} | schema["$defs"]).items()
        for prop, spec in model.get("properties", {}).items()
        if not spec.get("description")
    ]
    assert not undocumented, f"config fields missing a description: {undocumented}"
