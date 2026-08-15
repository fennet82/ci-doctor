"""Orchestrator: turn a classified job into a budgeted evidence bundle.

This is what the report step consumes, and all it consumes. It wires the
deterministic stages together and calls no model. Segmentation + attribution
happen upstream (they need a provider-specific segmenter); this takes the
already-segmented job plus its Attribution.
"""

import logging
from dataclasses import dataclass

from ci_doctor.config.schema import Config
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.budget import estimate_tokens, fit
from ci_doctor.core.denoise import denoise
from ci_doctor.core.extract import extract
from ci_doctor.core.models import SYNTHETIC_SECTIONS, Job, Phase, Section, walk_sections

log = logging.getLogger("ci_doctor.analyze")


@dataclass
class EvidenceBundle:
    """Everything the report step gets to see — and all it gets to see.

    Attributes:
        blamed_phase: The phase attribution settled on.
        blamed_lines: Denoised, windowed, budget-fitted lines from that phase.
        secondary: One non-causal line per warning-only phase.
        metadata: Job facts and the attribution's own reasoning.
        token_estimate: Approximate prompt cost of the whole bundle.
        truncated: Whether the budget dropped lines. Always surfaced in the
            report — a silent truncation would be a lie about the evidence.
    """

    blamed_phase: Phase
    blamed_lines: list[str]
    secondary: list[str]  # one-liner context per secondary phase (non-causal)
    metadata: dict
    token_estimate: int
    truncated: bool = False


def _blamed_section(sections: list[Section], phase: Phase) -> Section | None:
    """Find the section to draw evidence from.

    Args:
        sections: Top-level sections.
        phase: The phase attribution blamed.

    Returns:
        The *last* real section carrying that phase — later output is closer to
        the failure — or None when no section matches.
    """
    match = [s for s in walk_sections(sections) if s.name not in SYNTHETIC_SECTIONS and s.phase == phase]
    return match[-1] if match else None


def build_bundle(job: Job, attr: Attribution, sections: list[Section], cfg: Config) -> EvidenceBundle:
    """Denoise, window and budget the blamed section into a bundle.

    Deterministic and offline: no model is called here. Only the blamed phase's
    lines are considered, which is what keeps fetch/prepare noise out of the
    evidence for a plain script failure.

    Args:
        job: The failed job.
        attr: The classifier's verdict.
        sections: The job's sections, phases already assigned.
        cfg: Config supplying the denoise, extraction and budget knobs.

    Returns:
        The bundle the report step consumes.
    """
    blamed = _blamed_section(sections, attr.phase)
    if blamed is not None:
        raw = [line.text for line in blamed.lines]
    else:
        # No section carries the blamed phase (e.g. PROVISION with an empty log):
        # fall back to whatever text exists so the report still has context.
        raw = [line.text for sec in walk_sections(sections) for line in sec.lines]

    clean = denoise(raw, cfg.denoise)
    blamed_budget = int(cfg.llm.max_input_tokens * 0.7)
    # Budget the *selection* by matcher priority first; `fit` is the last resort
    # that cuts inside whatever survives.
    excerpt = extract(clean, cfg.extraction.matchers, cfg.extraction.tail_lines, blamed_budget)
    fitted, truncated = fit(excerpt, blamed_budget)
    log.debug(
        "blamed phase %s: denoise %d->%d, extract ->%d, fit ->%d lines (truncated=%s)",
        attr.phase,
        len(raw),
        len(clean),
        len(excerpt),
        len(fitted),
        truncated,
    )

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
