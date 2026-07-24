"""Orchestrator: turn a classified job into a budgeted evidence bundle.

This is what the LLM step (M4) will consume, and what `--dry-run` will print. It
wires the deterministic stages together; it does not call any model. Segmentation
+ attribution happen upstream (they need a provider-specific segmenter); this takes
the already-segmented job plus its Attribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ci_doctor.config.schema import Config
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.budget import estimate_tokens, fit
from ci_doctor.core.denoise import denoise
from ci_doctor.core.extract import extract
from ci_doctor.core.models import Job, Phase, Section


@dataclass
class EvidenceBundle:
    blamed_phase: Phase
    blamed_lines: list[str]
    secondary: list[str]          # one-liner context per secondary phase (non-causal)
    metadata: dict
    token_estimate: int
    truncated: bool = False
    extra: dict = field(default_factory=dict)


def _walk(sections):
    for sec in sections:
        yield sec
        yield from _walk(sec.children)


def _blamed_section(sections: list[Section], phase: Phase) -> Section | None:
    match = [s for s in _walk(sections) if s.name not in {"__preamble__", "__trailer__"} and s.phase == phase]
    return match[-1] if match else None


def build_bundle(
    job: Job,
    attr: Attribution,
    sections: list[Section],
    cfg: Config,
    *,
    strip_timestamps: bool = False,
) -> EvidenceBundle:
    blamed = _blamed_section(sections, attr.phase)
    if blamed is not None:
        raw = [line.text for line in blamed.lines]
    else:
        # No section carries the blamed phase (e.g. PROVISION with an empty log):
        # fall back to whatever text exists so the report still has context.
        raw = [line.text for sec in _walk(sections) for line in sec.lines]

    clean = denoise(raw, cfg.denoise, strip_timestamps=strip_timestamps)
    excerpt = extract(clean, cfg.extraction.matchers, cfg.extraction.tail_lines)
    blamed_budget = int(cfg.llm.max_input_tokens * 0.7)
    fitted, truncated = fit(excerpt, blamed_budget)

    secondary = [f"{phase}: warnings present (non-causal)" for phase in attr.secondary_phases]
    metadata = {
        "job": job.name,
        "stage": job.stage,
        "status": job.status,
        "failure_reason": str(job.failure_reason),
        "raw_failure_reason": job.raw_failure_reason,
        "duration": job.duration,
        "allow_failure": job.allow_failure,
        "needs": job.needs,
        "web_url": job.web_url,
        "rule_id": attr.rule_id,
        "confidence": attr.confidence,
        "terminal_evidence": attr.terminal_evidence,
    }
    token_estimate = estimate_tokens("\n".join(fitted) + "\n".join(secondary) + str(metadata))
    return EvidenceBundle(attr.phase, fitted, secondary, metadata, token_estimate, truncated)
