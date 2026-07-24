import sys

import pytest

from ci_doctor import cli


def test_main_always_exits_zero_on_error(monkeypatch):
    # A bad --from-file path raises inside analyze; guardrail #3 forces exit 0.
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "--from-file", "/no/such/file"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_from_file_replay_smoke(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "--from-file", "tests/fixtures/sample.log"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "job=sample" in out
    assert "log_lines=" in out
