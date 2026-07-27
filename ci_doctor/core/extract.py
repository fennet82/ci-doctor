"""Select the lines worth showing: a tail window plus anchored context windows.

Anchored windows catch causes that print *before* a framework's summary (so a
tail-only view would miss them). Overlapping windows merge. Every gap between
selected windows is marked with an explicit elision count — never a silent cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ci_doctor.config.schema import MatcherConfig


@dataclass
class _Window:
    """A half-open slice of the log worth keeping.

    Attributes:
        start: First line index, inclusive.
        end: Last line index, exclusive.
        priority: From the matcher that produced it. Survives merging as the max
            of the merged windows, so a high-priority window never loses rank by
            touching a low-priority neighbour.
    """

    start: int
    end: int  # exclusive
    priority: int


def _windows_for(lines: list[str], matchers: list[MatcherConfig]) -> list[_Window]:
    """Run every matcher over the log and collect the windows they anchor.

    Args:
        lines: The denoised log lines.
        matchers: Matcher packs from `extraction.matchers`.

    Returns:
        One window per hit, unsorted and possibly overlapping. Empty when nothing
        matched — which is why a pack that never fires fails *silently*, and why
        each one needs a fixture in `test_matcher_packs.py`.
    """
    wins: list[_Window] = []
    for m in matchers:
        if m.pattern:
            rx = re.compile(m.pattern)
            for i, line in enumerate(lines):
                if rx.search(line):
                    wins.append(_Window(max(0, i - m.before), min(len(lines), i + m.after + 1), m.priority))
        elif m.start and m.end:
            srx, erx = re.compile(m.start), re.compile(m.end)
            i = 0
            while i < len(lines):
                if srx.search(lines[i]):
                    j = i + 1
                    while j < len(lines) and not erx.search(lines[j]):
                        j += 1
                    end = min(len(lines), j + 1)
                    wins.append(_Window(i, end, m.priority))
                    i = end
                else:
                    i += 1
    return wins


def _merge(wins: list[_Window]) -> list[_Window]:
    """Collapse overlapping or adjacent windows into contiguous ones.

    Args:
        wins: Windows in any order. Mutated in place while merging.

    Returns:
        Non-overlapping windows sorted by start, each carrying the highest
        priority among those it absorbed.
    """
    if not wins:
        return []
    wins = sorted(wins, key=lambda w: w.start)
    merged = [wins[0]]
    for w in wins[1:]:
        last = merged[-1]
        if w.start <= last.end:  # overlapping or adjacent
            last.end = max(last.end, w.end)
            last.priority = max(last.priority, w.priority)
        else:
            merged.append(w)
    return merged


def extract(lines: list[str], matchers: list[MatcherConfig], tail_lines: int) -> list[str]:
    """Reduce a log to the lines worth showing.

    Args:
        lines: The denoised log lines.
        matchers: Matcher packs from `extraction.matchers`.
        tail_lines: How many trailing lines to always keep, at lowest priority.
            Pass 0 to test a pack in isolation — otherwise the tail masks a
            matcher that never fired.

    Returns:
        The selected lines with "… [N lines elided] …" markers where content was
        dropped. Falls back to every line when nothing matched and there is no tail.
    """
    wins = _windows_for(lines, matchers)
    if tail_lines > 0 and lines:
        wins.append(_Window(max(0, len(lines) - tail_lines), len(lines), 0))  # tail, lowest priority
    merged = _merge(wins)
    if not merged:
        return list(lines)
    return _render(lines, merged)


def _render(lines: list[str], windows: list[_Window]) -> list[str]:
    """Emit the windowed lines, announcing every gap between them.

    Args:
        lines: The full log.
        windows: Merged, sorted, non-overlapping windows.

    Returns:
        Selected lines interleaved with explicit elision markers. Guardrail #8:
        no cut is ever silent.
    """
    out: list[str] = []
    prev_end = 0
    for w in windows:
        if w.start > prev_end:
            out.append(f"… [{w.start - prev_end} lines elided] …")
        out.extend(lines[w.start : w.end])
        prev_end = w.end
    if prev_end < len(lines):
        out.append(f"… [{len(lines) - prev_end} lines elided] …")
    return out
