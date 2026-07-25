"""Golden-file attribution suite: segment + phase-map + classify each fixture log,
compare {phase, reason, rule_id} to expected. No network, no LLM.

One `expected/<case>.json` covers every provider that ships a log for that case —
attribution is provider-neutral, so adding `logs/github/oom_137.log` asserts it
against the same verdict with no change here. See `tests/support.py`.
"""

import json

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job
from ci_doctor.core.phases import assign_phases
from tests import support

SPECS = sorted(p.stem for p in support.EXPECTED.glob("*.json"))
CASES = support.pairs_for(SPECS)


@pytest.mark.parametrize("provider,case", CASES, ids=[f"{p}-{c}" for p, c in CASES])
def test_attribution(provider, case):
    """A fixture log classifies to its expected phase, reason and rule."""
    spec = json.loads((support.EXPECTED / f"{case}.json").read_text())
    log = support.read_log(provider, case)
    meta = spec.get("job", {})
    job = Job(
        id=case, name=case, status=meta.get("status", "failed"),
        failure_reason=FailureReason(meta.get("failure_reason", "unknown")),
        raw_failure_reason=meta.get("raw_failure_reason", ""),
        log=log or None,
    )
    cfg = load_config(environ={})
    job.sections = support.segment(provider, job.log or "")
    assign_phases(job.sections, cfg.phases)

    attr = attribute(job, job.sections)
    exp = spec["expect"]
    who = f"{provider}/{case}"
    assert attr.phase == exp["phase"], f"{who}: phase {attr.phase} != {exp['phase']}"
    assert attr.reason == exp["reason"], f"{who}: reason {attr.reason} != {exp['reason']}"
    assert attr.rule_id == exp["rule_id"], f"{who}: rule {attr.rule_id} != {exp['rule_id']}"


def test_every_provider_dir_has_a_segmenter():
    """Guards the "drop a directory" workflow.

    A `logs/<provider>/` with no registered segmenter would silently contribute
    zero tests instead of failing.
    """
    missing = set(support.providers()) - set(support.SEGMENTERS)
    assert not missing, f"logs/ dirs with no segmenter in support.SEGMENTERS: {sorted(missing)}"


def test_every_expected_verdict_has_at_least_one_log():
    """An orphaned `expected/*.json` would otherwise silently assert nothing."""
    covered = {case for _, case in CASES}
    assert set(SPECS) == covered, f"no log for: {sorted(set(SPECS) - covered)}"


def test_regression_case_is_present():
    """Guard the guard: the noisy-log regression fixture must not go missing."""
    assert "script_failure_noisy" in SPECS
