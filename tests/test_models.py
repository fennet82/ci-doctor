"""Domain model: construction, StrEnum behaviour, offline replay, and core purity."""

from pathlib import Path

from ci_doctor import cli
from ci_doctor.core.models import FailureReason, Job, LogLine, Phase, Run, Section
from ci_doctor.pipeline import run_from_file


def test_domain_model_instantiates():
    """The model nests correctly and its enums compare equal to their strings."""
    sec = Section(name="step_script", phase=Phase.SCRIPT, lines=[LogLine(1, "hi")])
    job = Job(
        id="1",
        name="build",
        status="failed",
        failure_reason=FailureReason.SCRIPT_FAILURE,
        sections=[sec],
        log="hi\n",
    )
    run = Run(id="p1", jobs=[job])
    assert run.jobs[0].sections[0].phase == Phase.SCRIPT
    assert Phase.SCRIPT == "script"  # StrEnum comparison
    assert f"{FailureReason.SCRIPT_FAILURE}" == "script_failure"


def test_run_from_file(tmp_path):
    """A raw log file becomes a single-job run named after the file."""
    f = tmp_path / "build.log"
    f.write_text("line1\nERROR: boom\n")
    run = run_from_file(f)
    assert run.jobs[0].name == "build"
    assert run.jobs[0].log == "line1\nERROR: boom\n"


def test_core_carries_no_vendor_name_in_its_code():
    """Invariant #2, enforced on the code rather than on the prose.

    CONTRIBUTING tells you to run `grep -ri gitlab ci_doctor/core/`, but that
    grep has never been empty: `ports.py` explains *why* the ports are split by
    naming the vendors that mix them, and `denoise.py` names whose framing the
    segmenter already stripped. Prose like that is the invariant being honoured,
    not broken.

    What must never appear is a vendor in the executable code — an import, an
    identifier, a string literal. `ast.unparse` drops comments outright and the
    docstrings are stripped below, so what is left is exactly that.
    """
    import ast
    import re

    vendors = re.compile(r"gitlab|github|jenkins|forgejo|gitea|bitbucket|woodpecker", re.IGNORECASE)
    core = Path(cli.__file__).parent / "core"
    offenders = {}
    for path in sorted(core.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast.get_docstring(node) is not None:
                    node.body = node.body[1:] or [ast.Pass()]
        hits = sorted({m.group(0) for m in vendors.finditer(ast.unparse(tree))})
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"provider names in core/ code: {offenders}"
