import json

from ci_doctor.llm.schema import Evidence, RemediationStep, Report
from ci_doctor.render.json_out import JsonRenderer
from ci_doctor.render.markdown import MarkdownRenderer


def _report():
    return Report(
        summary="unit tests failed", failure_phase="script", category="test",
        confidence="high", is_infra_not_code=False, likely_flaky=False,
        root_cause="test_add asserted 1 == 2",
        contributing_factors=["cache warning (non-causal)"],
        evidence=[Evidence(section="script", excerpt="E assert 1 == 2", why_it_matters="the failing assertion")],
        remediation=[RemediationStep(order=1, action="fix the assertion", rationale="assert failed", where="tests/test_x.py")],
        related_paths=["tests/test_x.py"], handoff_prompt="fix test_add",
    )


def test_markdown_has_sections():
    md = MarkdownRenderer().render(_report())
    assert "## ci-doctor" in md
    assert "### Root cause" in md
    assert "### Remediation" in md
    assert "fix the assertion" in md
    assert "Handoff prompt" in md


def test_json_roundtrips():
    data = json.loads(JsonRenderer().render(_report()))
    assert data["failure_phase"] == "script"
    assert data["remediation"][0]["where"] == "tests/test_x.py"
