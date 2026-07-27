"""CLI surface: the exit-0 guardrail, log levels, replay, and `config`."""

import json
import sys

import pytest

from ci_doctor import cli
from tests import support


def _run(monkeypatch, capsys, *argv):
    """Invoke the CLI with argv and return stdout.

    Args:
        monkeypatch: pytest fixture, used to swap sys.argv.
        capsys: pytest fixture capturing stdout.
        *argv: Arguments after the program name.

    Returns:
        Captured stdout.
    """
    monkeypatch.setattr(sys, "argv", ["ci-doctor", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    return capsys.readouterr().out


def test_main_always_exits_zero_on_error(monkeypatch):
    """Guardrail #3: even a crash exits 0, so a pipeline outcome never changes."""
    # A missing --config raises FileNotFoundError out of the loader, before anything renders.
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "-f", "/no/such/config.yml", "123"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_verbose_enables_debug_logging():
    """`--verbose` turns on DEBUG; without it the level returns to INFO."""
    import logging

    cli._configure_logging(True)
    assert logging.getLogger("ci_doctor").level == logging.DEBUG
    cli._configure_logging(False)  # default INFO when not verbose and no env override
    assert logging.getLogger("ci_doctor").level == logging.INFO


@pytest.mark.parametrize("provider", support.providers_with("sample"))
def test_from_file_replay_smoke(provider, monkeypatch, capsys, tmp_path):
    """Offline replay renders a report and writes both artifacts."""
    monkeypatch.chdir(tmp_path)  # report.md/report.json land here, not in the repo
    log = str(support.log_path(provider, "sample").resolve())
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "--no-color", log])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Root cause" in out
    assert "script" in out
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.json").exists()


def test_config_schema_is_valid_json(monkeypatch, capsys, tmp_path):
    """`--schema` emits parseable JSON Schema carrying the published $id."""
    monkeypatch.chdir(tmp_path)  # no stray .ci-doctor.yml from the repo root
    schema = json.loads(_run(monkeypatch, capsys, "config", "--schema"))
    assert schema["$id"].endswith("ci-doctor.schema.json")
    assert "extraction" in schema["properties"]


def test_config_prints_the_effective_config(monkeypatch, capsys, tmp_path):
    """The bare command shows the user's layer merged onto the defaults."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ci-doctor.yml").write_text("llm:\n  model: my-model\n")
    out = _run(monkeypatch, capsys, "config", "--no-color")
    assert "my-model" in out  # the user layer
    assert "tail_lines" in out  # and the untouched defaults


def test_config_diff_shows_only_what_the_user_changed(monkeypatch, capsys, tmp_path):
    """`--diff` shows the replaced default and the new value, nothing untouched."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ci-doctor.yml").write_text("llm:\n  model: my-model\n")
    out = _run(monkeypatch, capsys, "config", "--diff", "--no-color")
    assert "-  model: null" in out
    assert "+  model: my-model" in out
    assert "tail_lines" not in out  # unchanged keys stay out of the diff


def test_config_diff_is_empty_on_stock_defaults(monkeypatch, capsys, tmp_path):
    """With no user config, `--diff` says so instead of printing an empty diff."""
    monkeypatch.chdir(tmp_path)
    assert "no differences" in _run(monkeypatch, capsys, "config", "--diff", "--no-color")


def test_repeated_config_flags_apply_left_to_right(monkeypatch, capsys, tmp_path):
    """Several -f files stack, and the rightmost wins on a key they both set."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.yml").write_text("llm:\n  model: from-a\n  temperature: 0.9\n")
    (tmp_path / "b.yml").write_text("llm:\n  model: from-b\n")
    out = _run(monkeypatch, capsys, "config", "-f", "a.yml", "-f", "b.yml", "--no-color")
    assert "model: from-b" in out  # the rightmost file wins
    assert "temperature: 0.9" in out  # keys only the earlier file set survive


def test_config_validate_reports_ok_and_failure(monkeypatch, capsys, tmp_path):
    """`--validate` names the bad key instead of raising, and confirms a good config."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.yml").write_text("llm:\n  nope: 1\n")
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "config", "--validate", "-f", "bad.yml"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "invalid config" in capsys.readouterr().err  # named, not raised

    assert "config ok" in _run(monkeypatch, capsys, "config", "--validate")
