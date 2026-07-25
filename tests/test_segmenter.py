"""GitLab segmentation: preamble/trailer, nesting, ANSI markers, phase inheritance."""

from ci_doctor.core.models import Phase
from ci_doctor.core.phases import assign_phases
from ci_doctor.providers.gitlab.segmenter import GitLabSegmenter


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
        "section_start:1:outer\n"
        "section_start:2:inner\n"
        "deep line\n"
        "section_end:3:inner\n"
        "section_end:4:outer\n"
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


def test_phase_assignment_and_inheritance():
    """An unknown nested section inherits its parent's phase."""
    log = (
        "section_start:1:step_script\n"
        "section_start:2:my_custom_step\n"     # unknown -> inherits enclosing SCRIPT
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
    assert secs[0].children[0].phase == Phase.SCRIPT   # inherited
    assert secs[1].phase == Phase.FETCH
