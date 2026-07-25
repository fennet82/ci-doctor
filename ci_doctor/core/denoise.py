"""Log denoising. Cuts the volume before extraction without losing the signal.

Order per line: strip ANSI, collapse carriage-return rewrites (progress bars),
optionally strip the runner timestamp prefix, drop configured noise patterns.
Then collapse runs of identical lines. A line that looks like an error is never
dropped by a noise pattern — the escape hatch that keeps the cause visible.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ci_doctor.config.schema import DenoiseConfig

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")  # CSI sequences (SGR colour, cursor, ...)
_TS_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z?\s+")  # FF_TIMESTAMPS ISO prefix
_ERRORISH = re.compile(
    r"\b(ERROR|FATAL)\b|Traceback \(most recent call last\)|exit code \d+|npm ERR!|\bFAILED?\b"
)


def _default_keep(line: str) -> bool:
    """Whether a line is protected from noise-pattern removal.

    The escape hatch that keeps the cause visible: an over-broad noise pattern
    can never delete the one line that explains the failure.

    Args:
        line: A denoised log line.

    Returns:
        True if the line looks error-ish and must survive.
    """
    return bool(_ERRORISH.search(line))


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI escape sequences (colour, cursor movement).

    Args:
        text: Raw text from a log.

    Returns:
        The text with escape sequences removed.
    """
    return _ANSI.sub("", text)


def _collapse_cr(line: str) -> str:
    """Reduce a carriage-return-rewritten line to its final visible state.

    Args:
        line: A line possibly containing `\\r` progress-bar rewrites.

    Returns:
        The segment after the last `\\r`.
    """
    # Progress bars rewrite in place via \r; keep the final visible segment.
    # ponytail: naive last-\r-segment, not a true column overwrite. Fine for progress spam.
    return line.rsplit("\r", 1)[-1] if "\r" in line else line


def denoise(
    lines: list[str],
    cfg: DenoiseConfig,
    *,
    keep: Callable[[str], bool] | None = None,
    strip_timestamps: bool = False,
) -> list[str]:
    """Cut log volume without losing the signal.

    Per line: strip ANSI, collapse `\\r` rewrites, optionally strip the timestamp
    prefix, then drop configured noise — unless `keep` protects the line. Finally
    runs of identical lines collapse to one with a `(×N)` count.

    Args:
        lines: Raw log lines.
        cfg: Denoise knobs from `config.denoise`.
        keep: Predicate marking lines that noise patterns may not remove.
            Defaults to :func:`_default_keep`.
        strip_timestamps: Drop the runner's ISO timestamp prefix.

    Returns:
        The cleaned lines.
    """
    keep = keep or _default_keep
    noise = [re.compile(p) for p in cfg.noise_patterns]
    out: list[str] = []
    for raw in lines:
        line = strip_ansi(raw)
        if cfg.collapse_carriage_returns:
            line = _collapse_cr(line)
        if strip_timestamps:
            line = _TS_PREFIX.sub("", line)
        if not keep(line) and any(n.search(line) for n in noise):
            continue  # noise — but an anchor line is never dropped here
        out.append(line)
    return _dedupe_consecutive(out) if cfg.dedupe_repeats else out


def _dedupe_consecutive(lines: list[str]) -> list[str]:
    """Fold runs of identical adjacent lines into one, with a count.

    Only *consecutive* duplicates collapse: a line repeating far apart in the log
    is usually meaningful, while a hundred in a row is a retry loop.

    Args:
        lines: Cleaned log lines.

    Returns:
        The lines with runs replaced by "<line>  (×N)".
    """
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        j = i + 1
        while j < n and lines[j] == lines[i]:
            j += 1
        count = j - i
        out.append(lines[i] if count == 1 else f"{lines[i]}  (×{count})")
        i = j
    return out
