import pytest
from pydantic import ValidationError

from ci_doctor.config.loader import _deep_merge, load_config
from ci_doctor.config.schema import Config


def test_defaults_load_and_validate():
    cfg = load_config(environ={})
    assert isinstance(cfg, Config)
    assert cfg.provider == "gitlab"
    assert cfg.phases["step_script"] == "script"  # from defaults.yml
    assert cfg.gitlab.timeout_seconds == 30       # scalar default from schema
    assert cfg.gitlab.base_url == "https://gitlab.com"   # official default, overridable


def test_shipped_matchers_parse():
    cfg = load_config(environ={})
    ids = {m.id for m in cfg.extraction.matchers}
    assert {"pytest", "generic_error"} <= ids


def test_unknown_key_is_error(tmp_path):
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("gitlab:\n  nonsense_key: 1\n")
    with pytest.raises(ValidationError):
        load_config(repo_config=p, environ={})


def test_repo_file_overrides_defaults(tmp_path):
    p = tmp_path / ".ci-doctor.yml"
    p.write_text("gitlab:\n  base_url: https://gl.internal\n  timeout_seconds: 99\n")
    cfg = load_config(repo_config=p, environ={})
    assert cfg.gitlab.base_url == "https://gl.internal"
    assert cfg.gitlab.timeout_seconds == 99


def test_missing_repo_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(repo_config=tmp_path / "nope.yml", environ={})


def test_env_overlay_nested_and_token_collision():
    env = {
        "CI_DOCTOR_LLM__TEMPERATURE": "0.7",
        "CI_DOCTOR_GITLAB_TOKEN": "supersecret",  # secret, not a config key -> ignored
        "CI_DOCTOR_LLM_KEY": "k",                 # ditto
    }
    cfg = load_config(environ=env)  # must not raise on the secret vars
    assert cfg.llm.temperature == 0.7


def test_deep_merge_list_replaces():
    base = {"a": {"x": [1, 2]}, "b": 1}
    over = {"a": {"x": [9]}}
    assert _deep_merge(base, over) == {"a": {"x": [9]}, "b": 1}
