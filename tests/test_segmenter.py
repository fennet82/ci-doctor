"""Segmentation: preamble/trailer, nesting, ANSI markers, phase inheritance.

GitLab first, then the GitHub Actions step/sub-group structure.
"""

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import Phase
from ci_doctor.core.phases import assign_phases
from ci_doctor.providers.gitlab.segmenter import GitLabSegmenter
from tests import support


def _seg(text):
    """Segment a GitLab log."""
    return GitLabSegmenter().segment(text)


def test_preamble_sections_and_trailer():
    """Content outside markers lands in the synthetic preamble and trailer."""
    log = (
        "Running with gitlab-runner\n"
        "section_start:100:step_script\n"
        "$ pytest\n"
        "section_end:160:step_script\n"
        "ERROR: Job failed: exit code 1\n"
    )
    secs = _seg(log)
    names = [s.name for s in secs]
    assert names == ["__preamble__", "step_script", "__trailer__"]
    step = secs[1]
    assert step.closed is True and step.start_ts == 100 and step.end_ts == 160
    assert any("pytest" in line.text for line in step.lines)
    assert "exit code 1" in secs[2].lines[-1].text


def test_unclosed_section_flagged():
    """A section with no end marker stays open — the signal the job died inside."""
    log = "section_start:100:step_script\nworking...\nERROR: took too long\n"
    step = _seg(log)[0]
    assert step.name == "step_script"
    assert step.closed is False  # no section_end


def test_nesting():
    """Nested sections become children, and both close correctly."""
    log = (
        "section_start:1:outer\nsection_start:2:inner\ndeep line\nsection_end:3:inner\nsection_end:4:outer\n"
    )
    secs = _seg(log)
    assert secs[0].name == "outer"
    assert secs[0].children[0].name == "inner"
    assert secs[0].closed and secs[0].children[0].closed


def test_ansi_wrapped_markers_parse():
    """Real ANSI-wrapped markers parse, and the header line is captured."""
    log = "\x1b[0Ksection_start:100:step_script\r\x1b[0KRunning script\n$ go test\n\x1b[0Ksection_end:160:step_script\r\x1b[0K\n"
    secs = _seg(log)
    assert [s.name for s in secs] == ["step_script"]
    assert secs[0].header == "Running script"


def test_gitlab_com_line_metadata_is_stripped():
    """gitlab.com prefixes every line with a timestamp + `00O`/`01E` stream token.

    That prefix sits before the section markers too, so without stripping it the
    segmenter would see no sections and the timestamps would leak into the
    evidence. `00O+` is a continued line and carries no separating space.
    """
    log = (
        "2026-08-19T21:44:16.163313Z 00O section_start:1787175856:step_script\r\x1b[0K\n"
        "2026-08-19T21:44:47.218423Z 01O $ ruff check .\n"
        "2026-08-19T21:44:47.225115Z 01O F401 [*] `math` imported but unused\n"
        "2026-08-19T21:44:47.100393Z 01E [notice] A new release of pip is available\n"
        "2026-08-19T21:44:16.163318Z 00O+\x1b[0KPreparing executor\n"
        "2026-08-19T21:44:48.000000Z 00O section_end:1787175880:step_script\r\x1b[0K\n"
        "2026-08-19T21:44:48.100000Z 00O ERROR: Job failed: exit code 1\n"
    )
    secs = _seg(log)
    assert [s.name for s in secs] == ["step_script", "__trailer__"]  # markers were found
    step = secs[0]
    assert step.start_ts == 1787175856 and step.closed is True
    body = [line.text for line in step.lines]
    assert "F401 [*] `math` imported but unused" in body  # no timestamp/`01O` prefix
    assert "$ ruff check ." in body
    assert "\x1b[0KPreparing executor" in body  # `00O+` continuation stripped, ANSI kept
    assert not any("00O" in line or "2026-08-19T" in line for line in body)
    assert "exit code 1" in secs[1].lines[-1].text


def test_phase_assignment_and_inheritance():
    """An unknown nested section inherits its parent's phase."""
    log = (
        "section_start:1:step_script\n"
        "section_start:2:my_custom_step\n"  # unknown -> inherits enclosing SCRIPT
        "x\n"
        "section_end:3:my_custom_step\n"
        "section_end:4:step_script\n"
        "section_start:5:restore_cache\n"
        "y\n"
        "section_end:6:restore_cache\n"
    )
    secs = _seg(log)
    assign_phases(secs, {"step_script": "script", "restore_cache": "fetch"})
    assert secs[0].phase == Phase.SCRIPT
    assert secs[0].children[0].phase == Phase.SCRIPT  # inherited
    assert secs[1].phase == Phase.FETCH


# --- GitHub Actions -----------------------------------------------------------
#
# These pin the shape that broke the segmenter, which only shows up in a log long
# enough to have one: output *after* `##[endgroup]`, sub-groups inside a step, and a
# runner completion marker. The original hand-written GitHub fixture was five lines
# and had none of it, so every assertion passed while each step's output was being
# dropped on the floor.

CASE = "rust_test_failure"


def _gh():
    """Segment a GitHub Actions fixture and assign phases."""
    sections = support.segment("github", support.read_log("github", CASE))
    assign_phases(sections, load_config(environ={}).phases)
    return sections


def _by_header(sections, needle):
    """The step whose original group name contains `needle`."""
    return next(s for s in sections if needle in (s.header or ""))


def test_a_step_owns_the_output_it_printed():
    """A `##[group]` wraps a step's *header*; its output follows `##[endgroup]`.

    Reading the group as the step collects `with:`/`env:` and drops everything the
    command actually printed — the evidence the whole tool exists to find.
    """
    step = _by_header(_gh(), "Run cargo --version")
    text = "\n".join(line.text for line in step.lines)

    assert "shell: /usr/bin/bash -e {0}" in text  # the header block, inside the group
    assert "assertion `left == right` failed" in text  # the output, after ##[endgroup]


def test_output_does_not_pile_up_in_the_trailer():
    """The trailer is the runner's closing verdict, not most of the job."""
    sections = _gh()
    trailer = next(s for s in sections if s.name == "__trailer__")
    steps = [s for s in sections if s.name not in {"__preamble__", "__trailer__"}]

    assert len(trailer.lines) < sum(len(s.lines) for s in steps) / 5


def test_sub_groups_nest_into_their_step_and_inherit_its_phase():
    """Checkout's "Getting Git version info" is part of checkout, not a sibling step."""
    checkout = _by_header(_gh(), "Run actions/checkout@v4")

    assert checkout.phase == Phase.FETCH
    assert [c.header for c in checkout.children], "sub-groups were flattened to top level"
    assert all(c.phase == Phase.FETCH for c in checkout.children)  # inherited, not defaulted


def test_a_step_stays_open_until_the_runner_reports_it():
    """`##[endgroup]` ends a header, not a step — only the runner ends a step.

    That is what leaves a section open for a cancelled or timed-out job, which is
    the signal rule 4 blames.
    """
    finished = _gh()
    assert all(s.closed for s in finished), "a completed job left a step open"

    # Same shape, cut off mid-output the way a cancelled job is.
    cancelled = support.segment("github", support.read_log("github", "timeout_unclosed_script"))
    assign_phases(cancelled, load_config(environ={}).phases)
    open_steps = [s for s in cancelled if not s.closed]
    assert [s.name for s in open_steps] == ["run"]
