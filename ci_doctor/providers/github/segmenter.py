"""Parse GitHub Actions logs into the Section tree.

GitHub uses ``##[group]`` / ``##[endgroup]`` (every line also carries an ISO
timestamp prefix, stripped here). Group names are dynamic ("Run actions/checkout@v4"),
so we canonicalise them to stable tokens (checkout, run, setup, post, ...) that the
existing config phase-map keys on — which is why phase assignment needs no
provider-specific code in core. The original name is kept as the section header.

The structural trap: **a step's group only wraps its header** (the `with:`/`env:`/
`shell:` block). Everything the step actually printed comes *after* `##[endgroup]`,
so reading a group as "the step" collects the step's inputs and drops its output —
which is exactly the evidence we exist to find. A step therefore stays current past
its `##[endgroup]`, owning every line until the next step opens; groups that are not
steps (checkout's "Fetching the repository", …) nest inside the current one.

Steps end when the runner says so, not at `##[endgroup]`: `Process completed with
exit code N` means the step ran to completion, whereas a cancelled or timed-out job
just stops mid-output. That distinction is what leaves a section *open* for the
classifier to blame, and it is the direct analogue of GitLab's `section_end`.
"""

from __future__ import annotations

import re

from ci_doctor.core.models import LogLine, Section
from ci_doctor.core.ports import LogSegmenter

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z?\s+")
_GROUP = re.compile(r"^##\[group\](.*)$")
_ENDGROUP = re.compile(r"^##\[endgroup\]\s*$")

#: Every token `_canonical` can return other than the untouched original. A group
#: naming one of these is a *step*; any other group is a sub-block of the step
#: currently running.
_STEP_TOKENS = {"checkout", "restore_cache", "download_artifacts", "setup_job", "setup",
                "post", "complete_job", "run"}

#: The runner's own "that step is over" annotations. Anything else (a cancellation,
#: a timeout, the log simply stopping) leaves the step open — which is the signal
#: attribution needs to blame the phase that was still running.
_STEP_DONE = re.compile(
    r"^##\[error\]Process completed with exit code|^Post job cleanup\.|^Cleaning up orphan processes"
)


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
    if "download" in n and "artifact" in n:  # not upload-artifact, which is a post step
        return "download_artifacts"
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
    """Segments GitHub Actions logs into steps, each owning the output it printed."""

    def segment(self, raw_log: str) -> list[Section]:
        """Parse a GitHub Actions log into sections.

        Args:
            raw_log: The raw log. Every line carries an ISO timestamp prefix,
                stripped here.

        Returns:
            Top-level sections, names canonicalised and the original kept as the
            section header, plus synthetic `__preamble__`/`__trailer__`. A step
            is `closed` only if the runner reported it finished.
        """
        top: list[Section] = []
        nested: list[Section] = []       # sub-groups open inside the current step
        current: Section | None = None   # the step that owns the output being read
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
                header = group.group(1).strip()
                sec = Section(name=_canonical(header), header=header, closed=False)
                if sec.name in _STEP_TOKENS:
                    if current is not None:
                        current.closed = True  # the next step starting is what ends this one
                    # An action can error out without closing its own sub-group. A
                    # later step running proves execution did not die in there, so
                    # close them too — an unclosed section is a blame signal.
                    for abandoned in nested:
                        abandoned.closed = True
                    nested.clear()
                    current = sec
                    top.append(sec)
                elif current is not None:
                    (nested[-1] if nested else current).children.append(sec)
                    nested.append(sec)
                else:
                    top.append(sec)
                    nested.append(sec)
                continue

            if _ENDGROUP.match(line):
                # For a step this only ends the header block; the step keeps
                # collecting. For a sub-group it is a real close.
                if nested:
                    nested.pop().closed = True
                continue

            if _STEP_DONE.match(line) and current is not None and not nested:
                current.closed = True
                current = None
                # fall through: the annotation itself is the runner's verdict, and
                # belongs in the trailer where the classifier reads it.

            if nested:
                target = nested[-1]
            elif current is not None:
                target = current
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
