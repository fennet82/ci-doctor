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
    start: int
    end: int  # exclusive
    priority: int


def _windows_for(lines: list[str], matchers: list[MatcherConfig]) -> list[_Window]:
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
    wins = _windows_for(lines, matchers)
    if tail_lines > 0 and lines:
        wins.append(_Window(max(0, len(lines) - tail_lines), len(lines), 0))  # tail, lowest priority
    merged = _merge(wins)
    if not merged:
        return list(lines)
    return _render(lines, merged)


def _render(lines: list[str], windows: list[_Window]) -> list[str]:
    out: list[str] = []
    prev_end = 0
    for w in windows:
        if w.start > prev_end:
            out.append(f"… [{w.start - prev_end} lines elided] …")
        out.extend(lines[w.start:w.end])
        prev_end = w.end
    if prev_end < len(lines):
        out.append(f"… [{len(lines) - prev_end} lines elided] …")
    return out
