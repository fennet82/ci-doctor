"""Secret scrubbing.

Runs twice: on the prompt before it leaves the process, and on the rendered
report before it is printed or posted.

Local regex set + exact literals of secret-named environment variables (a
best-effort stand-in for the CI's masked-variable values). Replacement keeps the
report readable: ``[REDACTED:<kind>]``. Heavier entropy scanning (detect-secrets)
is intentionally not a dependency — it stays a future opt-in flag, never a default,
and must not fetch anything.
"""

import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ci_doctor.config.schema import RedactionConfig

if TYPE_CHECKING:
    # Only for the annotation: `core/` must not import `llm/` at runtime.
    from ci_doctor.llm.schema import Report

_URL_CREDS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@")
_SECRET_ENV_NAME = re.compile(
    r"TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY", re.IGNORECASE
)

#: Secret shapes scrubbed everywhere, keyed by the label that replaces them.
#: Deliberately conservative and local — no entropy scanning, no network.
_DEFAULT_PATTERNS = {
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    "glpat": r"glpat-[A-Za-z0-9_\-]{20,}",
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "authorization": r"(?i)authorization:\s*.+",  # whole header value, not just the scheme
    "bearer": r"(?i)bearer\s+[A-Za-z0-9._\-]{10,}",
    # identifier containing a secret-ish word (AWS_SECRET, MY_TOKEN, ...) = value
    "assignment": r"(?i)[A-Za-z0-9_]*(?:token|secret|password|passwd|api[_-]?key|access[_-]?key)[A-Za-z0-9_]*\s*[=:]\s*\S+",
}


def _compiled(cfg: RedactionConfig) -> list[tuple[str, re.Pattern[str]]]:
    """Compile the built-in patterns plus the user's extras.

    Args:
        cfg: Redaction config supplying `extra_patterns`.

    Returns:
        ``(kind, compiled_regex)`` pairs; user extras are labelled `customN`.
    """
    pats = [(k, re.compile(v)) for k, v in _DEFAULT_PATTERNS.items()]
    for i, extra in enumerate(cfg.extra_patterns):
        pats.append((f"custom{i}", re.compile(extra)))
    return pats


def _env_secret_literals(environ: Mapping[str, str]) -> list[str]:
    """Collect the *values* of secret-named environment variables.

    A best-effort stand-in for the CI's masked-variable list: if the runner
    exported it under a secret-ish name, its literal value must not appear in
    output even when no regex would recognise its shape.

    Args:
        environ: The environment to scan.

    Returns:
        Distinct values of at least 6 characters, longest first so a short
        secret that is a substring of a longer one cannot pre-empt it.
    """
    vals = {v for name, v in environ.items() if v and len(v) >= 6 and _SECRET_ENV_NAME.search(name)}
    return sorted(vals, key=len, reverse=True)  # longest first, so substrings don't pre-empt


def redact_text(
    text: str, cfg: RedactionConfig | None = None, environ: Mapping[str, str] | None = None
) -> str:
    """Scrub secrets from a string.

    Args:
        text: The text to scrub.
        cfg: Redaction config. Defaults to the built-in set.
        environ: Environment to harvest secret literals from. Defaults to
            :data:`os.environ`; tests pass an explicit dict.

    Returns:
        The text with each secret replaced by ``[REDACTED:<kind>]``, or unchanged
        when redaction is disabled.
    """
    cfg = cfg or RedactionConfig()
    if not cfg.enabled:
        return text
    environ = os.environ if environ is None else environ
    for literal in _env_secret_literals(environ):
        text = text.replace(literal, "[REDACTED:env]")
    text = _URL_CREDS.sub(r"\1[REDACTED:credentials]@", text)
    for kind, rx in _compiled(cfg):
        text = rx.sub(f"[REDACTED:{kind}]", text)
    return text


def redact_report(
    report: "Report", cfg: RedactionConfig | None = None, environ: Mapping[str, str] | None = None
) -> "Report":
    """Scrub every free-text field of a report.

    Applied to the finished report, after the LLM has spoken — a model can quote
    a secret straight out of the log it was shown, so scrubbing the prompt alone
    is not enough.

    Args:
        report: The validated report.
        cfg: Redaction config. Defaults to the built-in set.
        environ: Environment to harvest secret literals from.

    Returns:
        A scrubbed copy. The original is not mutated.
    """

    def r(value: str | None) -> str | None:
        """Scrub a value if it is a string, else pass it through."""
        return redact_text(value, cfg, environ) if isinstance(value, str) else value

    return report.model_copy(
        update={
            "summary": r(report.summary),
            "root_cause": r(report.root_cause),
            "handoff_prompt": r(report.handoff_prompt),
            "contributing_factors": [r(x) for x in report.contributing_factors],
            "related_paths": [r(x) for x in report.related_paths],
            "evidence": [
                e.model_copy(
                    update={
                        "section": r(e.section),
                        "excerpt": r(e.excerpt),
                        "why_it_matters": r(e.why_it_matters),
                    }
                )
                for e in report.evidence
            ],
            "remediation": [
                s.model_copy(update={"action": r(s.action), "rationale": r(s.rationale), "where": r(s.where)})
                for s in report.remediation
            ],
        }
    )
