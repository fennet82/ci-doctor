"""Parse GitHub Actions logs into the Section tree.

GitHub uses ``##[group]`` / ``##[endgroup]`` (every line also carries an ISO
timestamp prefix, stripped here). Group names are dynamic ("Run actions/checkout@v4"),
so we canonicalise them to stable tokens (checkout, run, setup, post, ...) that the
existing config phase-map keys on — which is why phase assignment needs no
provider-specific code in core. The original name is kept as the section header.
"""

from __future__ import annotations

import re

from ci_doctor.core.models import LogLine, Section
from ci_doctor.core.ports import LogSegmenter

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z?\s+")
_GROUP = re.compile(r"^##\[group\](.*)$")
_ENDGROUP = re.compile(r"^##\[endgroup\]\s*$")


def _canonical(name: str) -> str:
    """Reduce a dynamic GitHub group name to a stable token.

    Group names embed action versions and step titles ("Run actions/checkout@v4"),
    so they cannot be phase-map keys directly. Canonicalising here is what lets
    phase assignment in `core` stay provider-free.

    Args:
        name: The raw group name.

    Returns:
        A stable token the config phase map keys on, or the trimmed original when
        nothing matches.
    """
    n = name.strip().lower()
    if "checkout" in n:
        return "checkout"
    if "cache" in n:
        return "restore_cache"
    if n.startswith("set up job"):
        return "setup_job"
    if n.startswith("set up ") or "setup-" in n:
        return "setup"
    if n.startswith("post "):
        return "post"
    if n.startswith("complete job"):
        return "complete_job"
    if n.startswith("run "):
        return "run"
    return name.strip()


class GitHubSegmenter(LogSegmenter):
    """Segments GitHub Actions logs on `##[group]` / `##[endgroup]` markers."""

    def segment(self, raw_log: str) -> list[Section]:
        """Parse a GitHub Actions log into sections.

        Args:
            raw_log: The raw log. Every line carries an ISO timestamp prefix,
                stripped here.

        Returns:
            Top-level sections, names canonicalised and the original kept as the
            section header, plus synthetic `__preamble__`/`__trailer__`.
        """
        top: list[Section] = []
        stack: list[Section] = []
        counter = 0
        seen_group = False
        preamble: Section | None = None
        trailer: Section | None = None

        lines = raw_log.split("\n")
        if lines and lines[-1] == "":
            lines.pop()

        for physical in lines:
            line = _TS.sub("", physical)
            group = _GROUP.match(line)
            if group:
                seen_group = True
                sec = Section(name=_canonical(group.group(1)), header=group.group(1).strip(), closed=False)
                (stack[-1].children if stack else top).append(sec)
                stack.append(sec)
                continue
            if _ENDGROUP.match(line):
                if stack:
                    stack.pop().closed = True
                continue

            if stack:
                target = stack[-1]
            elif not seen_group:
                if preamble is None:
                    preamble = Section(name="__preamble__", closed=True)
                    top.append(preamble)
                target = preamble
            else:
                if trailer is None:
                    trailer = Section(name="__trailer__", closed=True)
                    top.append(trailer)
                target = trailer

            counter += 1
            target.lines.append(LogLine(number=counter, text=line))

        return top
