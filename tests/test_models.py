"""Domain model: construction, StrEnum behaviour, and offline replay loading."""

from ci_doctor import cli
from ci_doctor.core.models import FailureReason, Job, LogLine, Phase, Run, Section


def test_domain_model_instantiates():
    """The model nests correctly and its enums compare equal to their strings."""
    sec = Section(name="step_script", phase=Phase.SCRIPT, lines=[LogLine(1, "hi")])
    job = Job(id="1", name="build", status="failed",
              failure_reason=FailureReason.SCRIPT_FAILURE, sections=[sec], log="hi\n")
    run = Run(id="p1", jobs=[job])
    assert run.jobs[0].sections[0].phase == Phase.SCRIPT
    assert Phase.SCRIPT == "script"                # StrEnum comparison
    assert f"{FailureReason.SCRIPT_FAILURE}" == "script_failure"


def test_run_from_file(tmp_path):
    """A raw log file becomes a single-job run named after the file."""
    f = tmp_path / "build.log"
    f.write_text("line1\nERROR: boom\n")
    run = cli._run_from_file(f)
    assert run.jobs[0].name == "build"
    assert run.jobs[0].log == "line1\nERROR: boom\n"
