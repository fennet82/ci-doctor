"""Token budgeting: fits under the cap, keeps the tail, never truncates silently."""

from ci_doctor.core.budget import estimate_tokens, fit


def test_fit_noop_when_under_budget():
    """Evidence already under budget is returned untouched."""
    lines = ["short line"] * 3
    out, truncated = fit(lines, max_tokens=1000)
    assert out == lines
    assert truncated is False


def test_fit_keeps_tail_and_marks_elision():
    """Over budget, the head goes, the tail stays, and the cut is announced."""
    lines = [f"line number {i} with some content here" for i in range(1000)]
    out, truncated = fit(lines, max_tokens=50)
    assert truncated is True
    assert out[0].startswith("… [") and "elided to fit token budget" in out[0]
    assert out[-1] == lines[-1]  # tail (where the failure lives) is kept
    assert estimate_tokens("\n".join(out[1:])) <= 50  # actually fits, no silent overflow
