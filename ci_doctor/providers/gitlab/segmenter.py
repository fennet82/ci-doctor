"""Parse GitLab collapsible-section markers into the Section tree.

Real markers look like::

    \\e[0Ksection_start:1735689600:step_script\\r\\e[0KRunning script
    ...
    \\e[0Ksection_end:1735689660:step_script\\r\\e[0K

with optional ``[collapsed=true]`` after the name and legal nesting. The ANSI
wrapper and the trailing ``\\r\\e[0K`` are optional in this parser so plain-text
fixtures parse the same way. Content outside any section becomes the synthetic
``__preamble__`` (before the first section) or ``__trailer__`` (after) — the
trailer carries the terminal ``ERROR: Job failed:`` line.
"""

from __future__ import annotations

import re

from ci_doctor.core.models import LogLine, Section
from ci_doctor.core.ports import LogSegmenter

PREAMBLE = "__preamble__"
TRAILER = "__trailer__"

_MARKER = re.compile(
    r"(?:\x1b\[0K)?"
    r"section_(?P<kind>start|end):(?P<ts>\d+):(?P<name>[A-Za-z0-9_.\-]+)"
    r"(?:\[[^\]]*\])?"          # options like [collapsed=true] — parsed but ignored
    r"(?:\r(?:\x1b\[0K)?)?"     # optional CR + line-clear
    r"\n?"                       # swallow the marker's own trailing newline so it
                                 # doesn't leak into a section as an empty line, or
                                 # split depth-0 content between back-to-back sections
)


class GitLabSegmenter(LogSegmenter):
    def segment(self, raw_log: str) -> list[Section]:
        return _assemble(_tokenize(raw_log))


def _tokenize(raw: str):
    tokens = []
    pos = 0
    for m in _MARKER.finditer(raw):
        if m.start() > pos:
            tokens.append(("content", raw[pos:m.start()]))
        tokens.append((m.group("kind"), m.group("name"), int(m.group("ts"))))
        pos = m.end()
    if pos < len(raw):
        tokens.append(("content", raw[pos:]))
    return tokens


def _pop_matching(stack: list[Section], name: str) -> Section | None:
    for i in range(len(stack) - 1, -1, -1):
        if stack[i].name == name:
            sec = stack[i]
            del stack[i:]  # also drop any unclosed nested sections above it (malformed)
            return sec
    return None


def _assemble(tokens) -> list[Section]:
    top: list[Section] = []
    stack: list[Section] = []
    counter = [0]
    seen_start = False
    preamble: Section | None = None
    trailer: Section | None = None

    def emit(target: Section, text: str) -> None:
        parts = text.split("\n")
        if parts and parts[-1] == "":
            parts.pop()  # drop the empty tail after a trailing newline
        for raw_line in parts:
            counter[0] += 1
            line = raw_line.rstrip("\r")
            target.lines.append(LogLine(number=counter[0], text=line))
            if target.header is None and line.strip():
                target.header = line.strip()

    for tok in tokens:
        if tok[0] == "content":
            if stack:
                emit(stack[-1], tok[1])
            elif not seen_start:
                if preamble is None:
                    preamble = Section(name=PREAMBLE, closed=True)
                    top.append(preamble)
                emit(preamble, tok[1])
            else:
                if trailer is None:
                    trailer = Section(name=TRAILER, closed=True)
                    top.append(trailer)
                emit(trailer, tok[1])
        elif tok[0] == "start":
            seen_start = True
            sec = Section(name=tok[1], start_ts=tok[2], closed=False)
            (stack[-1].children if stack else top).append(sec)
            stack.append(sec)
        elif tok[0] == "end":
            sec = _pop_matching(stack, tok[1])
            if sec is not None:
                sec.closed = True
                sec.end_ts = tok[2]

    return top
