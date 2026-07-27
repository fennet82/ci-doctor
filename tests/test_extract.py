"""Windowed extraction: anchors, merging, and visible elision."""

from ci_doctor.config.schema import MatcherConfig
from ci_doctor.core.budget import estimate_tokens
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


# The reason matcher priorities exist. Without budget-aware selection the cut is
# positional (`budget.fit` keeps the tail), which is exactly backwards here: npm
# reports the child process failed *after* the compiler said what was wrong.
def test_budget_pressure_drops_low_priority_windows_not_the_tail():
    """Under budget the compiler errors survive and the npm epilogue is shed."""
    lines = [f"src/a{i}.ts:1:1 - error TS2345: bad argument" for i in range(40)]
    # Unmatched output between the blocks: windows only stay separable — and so
    # only become rankable — across a gap, since `_merge` fuses adjacent ones.
    lines += [f"  building bundle chunk {i}" for i in range(20)]
    lines += [f"npm ERR! code ELIFECYCLE {i}" for i in range(400)]
    matchers = [_m(id="tsc", pattern="error TS", priority=80), _m(id="npm", pattern="npm ERR!", priority=75)]

    budget = estimate_tokens("\n".join(lines)) // 2
    out = "\n".join(extract(lines, matchers, tail_lines=0, max_tokens=budget))

    assert "error TS2345" in out, "dropped the cause and kept the complaint"
    assert "npm ERR!" not in out, "kept the low-priority epilogue that blew the budget"


def test_one_oversized_window_is_never_dropped_to_nothing():
    """A single window over budget stays: cutting *inside* it is `budget.fit`'s job."""
    lines = [f"=== FAILURES ===   detail line {i}" for i in range(500)]
    out = extract(lines, [_m(pattern="FAILURES", priority=90)], tail_lines=0, max_tokens=10)
    assert any("detail line" in line for line in out)
