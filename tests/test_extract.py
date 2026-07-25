"""Windowed extraction: anchors, merging, and visible elision."""

from ci_doctor.config.schema import MatcherConfig
from ci_doctor.core.extract import extract


def _m(**kw):
    """Build a MatcherConfig with every field defaulted, overridden by kwargs."""
    base = dict(id="x", start=None, end=None, pattern=None, before=0, after=0, priority=50)
    base.update(kw)
    return MatcherConfig(**base)


def test_tail_window_only():
    """With no matchers, the tail window carries the evidence."""
    lines = [f"line{i}" for i in range(200)]
    out = extract(lines, [], tail_lines=10)
    assert out[0].startswith("… [190 lines elided]")
    assert out[-1] == "line199"
    assert "line195" in out


def test_anchored_window_context():
    """A pattern matcher keeps its `before`/`after` context lines."""
    lines = ["a", "b", "c", "BOOM error here", "d", "e", "f"]
    out = extract(lines, [_m(pattern="BOOM", before=1, after=1)], tail_lines=0)
    assert "c\nBOOM error here\nd" in "\n".join(out)
    assert "elided" in out[0]  # head before the window is elided visibly


def test_overlapping_windows_merge_to_contiguous():
    """Adjacent windows merge, so no elision marker splits contiguous output."""
    lines = ["l0", "hit1", "l2", "l3", "hit2", "l5"]
    out = extract(lines, [_m(pattern="hit", before=1, after=1)], tail_lines=0)
    assert out == lines  # two adjacent windows merged; nothing elided


def test_start_end_block():
    """A start/end matcher captures the whole block and elides both sides."""
    lines = ["pre", "=== FAILURES ===", "detail1", "detail2", "=== short test summary ===", "after"]
    out = extract(lines, [_m(id="pytest", start="=+ FAILURES", end="short test summary")], tail_lines=0)
    assert "=== FAILURES ===\ndetail1\ndetail2\n=== short test summary ===" in "\n".join(out)
    assert out[0].startswith("… [")  # "pre" elided
    assert out[-1].startswith("… [")  # "after" elided
