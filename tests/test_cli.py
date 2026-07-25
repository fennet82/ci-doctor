import sys

import pytest

from ci_doctor import cli
from tests import support


def test_main_always_exits_zero_on_error(monkeypatch):
    # A bad --from-file path raises inside analyze; guardrail #3 forces exit 0.
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "--from-file", "/no/such/file"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_verbose_enables_debug_logging():
    import logging

    cli._configure_logging(True)
    assert logging.getLogger("ci_doctor").level == logging.DEBUG
    cli._configure_logging(False)  # default INFO when not verbose and no env override
    assert logging.getLogger("ci_doctor").level == logging.INFO


@pytest.mark.parametrize("provider", support.providers_with("sample"))
def test_from_file_replay_smoke(provider, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)  # report.md/report.json land here, not in the repo
    log = str(support.log_path(provider, "sample").resolve())
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "--no-color", "--from-file", log])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Root cause" in out
    assert "script" in out
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.json").exists()
