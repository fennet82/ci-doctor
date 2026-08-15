r"""Parse GitLab collapsible-section markers into the Section tree.

Real markers look like::

    \e[0Ksection_start:1735689600:step_script\r\e[0KRunning script
    ...
    \e[0Ksection_end:1735689660:step_script\r\e[0K

with optional ``[collapsed=true]`` after the name and legal nesting. The ANSI
wrapper and the trailing ``\r\e[0K`` are optional in this parser so plain-text
fixtures parse the same way. Content outside any section becomes the synthetic
``__preamble__`` (before the first section) or ``__trailer__`` (after) — the
trailer carries the terminal ``ERROR: Job failed:`` line.
"""

import re

from ci_doctor.core.models import PREAMBLE, TRAILER, LogLine, Section
from ci_doctor.core.ports import LogSegmenter

#: One item of the token stream: ``(kind, payload, ts)``. `kind` is "content",
#: "start" or "end"; `payload` is the text run or the section name; `ts` is the
#: marker's epoch seconds, and None for a content run, which carries no time.
_Token = tuple[str, str, int | None]

_MARKER = re.compile(
    r"(?:\x1b\[0K)?"
    r"section_(?P<kind>start|end):(?P<ts>\d+):(?P<name>[A-Za-z0-9_.\-]+)"
    r"(?:\[[^\]]*\])?"  # options like [collapsed=true] — parsed but ignored
    r"(?:\r(?:\x1b\[0K)?)?"  # optional CR + line-clear
    r"\n?"  # swallow the marker's own trailing newline so it
    # doesn't leak into a section as an empty line, or
    # split depth-0 content between back-to-back sections
)


class GitLabSegmenter(LogSegmenter):
    """Segments GitLab job traces on `section_start`/`section_end` markers."""

    def segment(self, raw_log: str) -> list[Section]:
        """Parse a GitLab trace into sections.

        Args:
            raw_log: The raw trace, ANSI wrappers optional.

        Returns:
            Top-level sections, plus synthetic `__preamble__`/`__trailer__` for
            content outside any marker.
        """
        return _assemble(_tokenize(raw_log))


def _tokenize(raw: str) -> list[_Token]:
    """Split a trace into content runs and section markers.

    Args:
        raw: The raw trace.

    Returns:
        The tokens in log order.
    """
    tokens: list[_Token] = []
    pos = 0
    for m in _MARKER.finditer(raw):
        if m.start() > pos:
            tokens.append(("content", raw[pos : m.start()], None))
        tokens.append((m.group("kind"), m.group("name"), int(m.group("ts"))))
        pos = m.end()
    if pos < len(raw):
        tokens.append(("content", raw[pos:], None))
    return tokens


def _pop_matching(stack: list[Section], name: str) -> Section | None:
    """Close the innermost open section with a given name.

    Args:
        stack: Currently open sections, outermost first. Mutated.
        name: The name from the `section_end` marker.

    Returns:
        The matched section, or None when no open section has that name. Any
        unclosed sections nested above it are dropped from the stack — a
        malformed log should not leave phantom sections open forever.
    """
    for i in range(len(stack) - 1, -1, -1):
        if stack[i].name == name:
            sec = stack[i]
            del stack[i:]  # also drop any unclosed nested sections above it (malformed)
            return sec
    return None


def _assemble(tokens: list[_Token]) -> list[Section]:
    """Build the section tree from a token stream.

    Args:
        tokens: Output of :func:`_tokenize`.

    Returns:
        Top-level sections. Line numbers run continuously across the whole log,
        so evidence can always point back at the original trace. A section left
        open at EOF keeps `closed=False` — that is the signal the job died inside it.
    """
    top: list[Section] = []
    stack: list[Section] = []
    counter = 0
    seen_start = False
    preamble: Section | None = None
    trailer: Section | None = None

    def emit(target: Section, text: str) -> None:
        """Append a content run's lines to a section, numbering them.

        Args:
            target: The section receiving the lines. Mutated.
            text: A content run, possibly multi-line.
        """
        nonlocal counter
        parts = text.split("\n")
        if parts and parts[-1] == "":
            parts.pop()  # drop the empty tail after a trailing newline
        for raw_line in parts:
            counter += 1
            line = raw_line.rstrip("\r")
            target.lines.append(LogLine(number=counter, text=line))
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
