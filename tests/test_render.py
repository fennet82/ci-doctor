"""Renderers: Markdown sections, JSON round-trip, and rich-markup safety."""

import json

from ci_doctor.llm.schema import Evidence, RemediationStep, Report
from ci_doctor.render.markdown import MarkdownRenderer


def _report():
    """Build a fully populated report so every renderer branch is exercised."""
    return Report(
        summary="unit tests failed",
        failure_phase="script",
        category="test",
        confidence="high",
        is_infra_not_code=False,
        likely_flaky=False,
        root_cause="test_add asserted 1 == 2",
        contributing_factors=["cache warning (non-causal)"],
        evidence=[
            Evidence(section="script", excerpt="E assert 1 == 2", why_it_matters="the failing assertion")
        ],
        remediation=[
            RemediationStep(
                order=1, action="fix the assertion", rationale="assert failed", where="tests/test_x.py"
            )
        ],
        related_paths=["tests/test_x.py"],
        handoff_prompt="fix test_add",
    )


def test_markdown_has_sections():
    """Every populated section appears in the Markdown output."""
    md = MarkdownRenderer().render(_report())
    assert "## ci-doctor" in md
    assert "### Root cause" in md
    assert "### Remediation" in md
    assert "fix the assertion" in md
    assert "Handoff prompt" in md


def test_json_roundtrips():
    """The JSON artifact parses and preserves nested fields."""
    data = json.loads(_report().model_dump_json(indent=2))
    assert data["failure_phase"] == "script"
    assert data["remediation"][0]["where"] == "tests/test_x.py"


def test_terminal_preserves_bracketed_log_content():
    """Log text full of `[...]` must not be eaten as rich markup.

    Real logs are full of `[error]`, `[gw0]` and ANSI remnants; rendering them as
    markup would mangle the evidence or raise on a malformed tag.
    """
    import io

    from ci_doctor.render.terminal import render_terminal

    r = _report().model_copy(
        update={
            "root_cause": "##[error]Process completed with exit code 1",
            "evidence": [Evidence(section="script", excerpt="[gw0] [ 50%] FAILED test", why_it_matters="w")],
        }
    )
    buf = io.StringIO()
    render_terminal(r, no_color=True, file=buf)
    out = buf.getvalue()
    assert "[error]" in out
    assert "[gw0]" in out
