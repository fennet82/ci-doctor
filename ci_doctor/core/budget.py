"""Token budgeting. Keeps the evidence under the model's input limit.

The blamed slice is already small after denoise+extract; this is the last-resort
cap. When it still overflows we keep the tail (the failure lives at the end) and
elide the head with a visible marker — never a silent truncation.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    # ponytail: ~4 chars/token heuristic; swap for a local tokenizer if precision matters.
    return max(1, len(text) // 4)


def fit(lines: list[str], max_tokens: int) -> tuple[list[str], bool]:
    """Return (lines, truncated). Fits by keeping the tail; head elision is visible.

    ponytail: tail-keep. Upgrade to phase-aware 70/10/20 + drop-lowest-priority-
    windows-first (see extract._Window.priority) if the tail heuristic misses causes.
    """
    if estimate_tokens("\n".join(lines)) <= max_tokens:
        return lines, False

    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        cost = estimate_tokens(line) + 1
        if total + cost > max_tokens:
            break
        kept.append(line)
        total += cost
    kept.reverse()
    dropped = len(lines) - len(kept)
    return [f"… [{dropped} lines elided to fit token budget] …", *kept], True
