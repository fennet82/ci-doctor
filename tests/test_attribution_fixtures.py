"""Golden-file attribution suite: segment + phase-map + classify each fixture log,
compare {phase, reason, rule_id} to expected. No network, no LLM.
"""

import json
from pathlib import Path

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job
from ci_doctor.core.phases import assign_phases
from ci_doctor.providers.gitlab.segmenter import GitLabSegmenter

FIX = Path(__file__).parent / "fixtures"
CASES = sorted(p.stem for p in (FIX / "expected").glob("*.json"))


@pytest.mark.parametrize("case", CASES)
def test_attribution(case):
    spec = json.loads((FIX / "expected" / f"{case}.json").read_text())
    log = (FIX / "logs" / f"{case}.log").read_text()
    meta = spec.get("job", {})
    job = Job(
        id=case, name=case, status=meta.get("status", "failed"),
        failure_reason=FailureReason(meta.get("failure_reason", "unknown")),
        raw_failure_reason=meta.get("raw_failure_reason", ""),
        log=log or None,
    )
    cfg = load_config(environ={})
    job.sections = GitLabSegmenter().segment(job.log or "")
    assign_phases(job.sections, cfg.phases)

    attr = attribute(job, job.sections)
    exp = spec["expect"]
    assert attr.phase == exp["phase"], f"{case}: phase {attr.phase} != {exp['phase']}"
    assert attr.reason == exp["reason"], f"{case}: reason {attr.reason} != {exp['reason']}"
    assert attr.rule_id == exp["rule_id"], f"{case}: rule {attr.rule_id} != {exp['rule_id']}"


def test_regression_case_is_present():
    # Guard the guard: the noisy-log regression fixture must exist and target SCRIPT.
    assert "script_failure_noisy" in CASES
