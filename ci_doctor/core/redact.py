"""Secret scrubbing. Runs twice: on the prompt before it leaves the process, and
on the rendered report before it is printed or posted.

Local regex set + exact literals of secret-named environment variables (a
best-effort stand-in for the CI's masked-variable values). Replacement keeps the
report readable: ``[REDACTED:<kind>]``. Heavier entropy scanning (detect-secrets)
is intentionally not a dependency — it stays a future opt-in flag, never a default,
and must not fetch anything.
"""

from __future__ import annotations

import os
import re

from ci_doctor.config.schema import RedactionConfig

_URL_CREDS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@")
_SECRET_ENV_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY", re.IGNORECASE)

_DEFAULT_PATTERNS = {
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    "glpat": r"glpat-[A-Za-z0-9_\-]{20,}",
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "authorization": r"(?i)authorization:\s*.+",  # whole header value, not just the scheme
    "bearer": r"(?i)bearer\s+[A-Za-z0-9._\-]{10,}",
    # identifier containing a secret-ish word (AWS_SECRET, MY_TOKEN, ...) = value
    "assignment": r"(?i)[A-Za-z0-9_]*(?:token|secret|password|passwd|api[_-]?key|access[_-]?key)[A-Za-z0-9_]*\s*[=:]\s*\S+",
}


def _compiled(cfg: RedactionConfig):
    pats = [(k, re.compile(v)) for k, v in _DEFAULT_PATTERNS.items()]
    for i, extra in enumerate(cfg.extra_patterns):
        pats.append((f"custom{i}", re.compile(extra)))
    return pats


def _env_secret_literals(environ: dict[str, str]) -> list[str]:
    vals = {v for name, v in environ.items() if v and len(v) >= 6 and _SECRET_ENV_NAME.search(name)}
    return sorted(vals, key=len, reverse=True)  # longest first, so substrings don't pre-empt


def redact_text(text: str, cfg: RedactionConfig | None = None, environ: dict[str, str] | None = None) -> str:
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


def redact_report(report, cfg: RedactionConfig | None = None, environ: dict[str, str] | None = None):
    def r(s):
        return redact_text(s, cfg, environ) if isinstance(s, str) else s

    return report.model_copy(update={
        "summary": r(report.summary),
        "root_cause": r(report.root_cause),
        "handoff_prompt": r(report.handoff_prompt),
        "contributing_factors": [r(x) for x in report.contributing_factors],
        "related_paths": [r(x) for x in report.related_paths],
        "evidence": [
            e.model_copy(update={"section": r(e.section), "excerpt": r(e.excerpt), "why_it_matters": r(e.why_it_matters)})
            for e in report.evidence
        ],
        "remediation": [
            s.model_copy(update={"action": r(s.action), "rationale": r(s.rationale), "where": r(s.where)})
            for s in report.remediation
        ],
    })
