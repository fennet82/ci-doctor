"""Renderers: Markdown, JSON round-trip, rich-markup safety, and pipeline framing.

The pipeline part covers ordering, per-job separators, the header, and the
interactive menu driven by a scripted picker.
"""

import io
import json

from ci_doctor.core.attribution import Attribution
from ci_doctor.core.models import Job, Run
from ci_doctor.llm.schema import Evidence, RemediationStep, Report
from ci_doctor.pipeline import JobResult
from ci_doctor.render.markdown import MarkdownRenderer, render_pipeline_markdown


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


def _jr(name, *, stage="build", infra=False, confidence="high"):
    """A JobResult carrying just the fields the pipeline framing reads."""
    report = _report().model_copy(
        update={"summary": f"{name} failed", "is_infra_not_code": infra, "confidence": confidence}
    )
    job = Job(id=name, name=name, status="failed", stage=stage)
    attr = Attribution(report.failure_phase, "script_failure", confidence, None, "rule")
    return JobResult(job, attr, report)


def _run(results):
    """A synthetic Run wrapping the given JobResults' jobs."""
    return Run(
        id="42", ref="main", sha="a1b2c3deadbeef", web_url="https://ci/42", jobs=[r.job for r in results]
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


# --- pipeline framing -------------------------------------------------------


def test_pipeline_helpers_triage_and_header():
    """The shared helpers count blame and summarise it in the header."""
    from ci_doctor.render.pipeline import header_line, triage

    results = [_jr("build", infra=True), _jr("test", infra=False, confidence="high")]
    assert triage(results) == (2, 1, 1)  # total, code, infra
    assert "2 failed · 1 your code · 1 infra" in header_line("42", "main", "a1b2c3d", results)


def test_render_pipeline_has_header_and_a_rule_per_job():
    """The non-interactive view frames the run and separates every job."""
    from ci_doctor.render.terminal import render_pipeline

    results = [_jr("build", stage="build"), _jr("deploy", stage="deploy", infra=True)]
    buf = io.StringIO()
    render_pipeline(_run(results), results, no_color=True, file=buf)
    out = buf.getvalue()
    assert "Pipeline 42 · main @ a1b2c3d" in out  # header, sha shortened
    assert "  build · deploy" in out  # the index line
    assert out.count("═") > 0  # heavy job rules present
    assert "build (build)" in out and "deploy (deploy)" in out  # each job's own separator


def test_render_pipeline_markdown_is_one_document_per_job():
    """The artifact/MR body is a titled pipeline with a section per job."""
    results = [_jr("build"), _jr("deploy", infra=True)]
    md = render_pipeline_markdown(_run(results), results)
    assert md.startswith("# ci-doctor — pipeline 42")
    job_headings = [ln for ln in md.splitlines() if ln.startswith("## ") and not ln.startswith("###")]
    assert len(job_headings) == 2  # one job section heading each, not the ### sub-headings
    assert "## build" in md and "## deploy" in md
    assert "[View pipeline](https://ci/42)" in md


def test_render_pipeline_spaces_jobs_apart():
    """Consecutive jobs are separated by blank lines, not run together."""
    from ci_doctor.render.terminal import render_pipeline

    results = [_jr("build", stage="build"), _jr("deploy", stage="deploy", infra=True)]
    buf = io.StringIO()
    render_pipeline(_run(results), results, no_color=True, file=buf)
    # The deploy separator is preceded by a run of blank lines that the first job's
    # body does not carry on its own — the extra spacing between sections.
    before_deploy = buf.getvalue().split("deploy (deploy)")[0]
    assert "\n\n\n" in before_deploy
