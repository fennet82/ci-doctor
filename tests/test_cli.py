import sys
from pathlib import Path

import pytest

from ci_doctor import cli


def test_main_always_exits_zero_on_error(monkeypatch):
    # A bad --from-file path raises inside analyze; guardrail #3 forces exit 0.
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "--from-file", "/no/such/file"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_from_file_replay_smoke(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)  # report.md/report.json land here, not in the repo
    log = str((Path(__file__).parent / "fixtures" / "sample.log").resolve())
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "--no-color", "--from-file", log])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Root cause" in out
    assert "script" in out
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.json").exists()
