"""LLM report tests: deterministic path, LLM path with recorded responses, repair
retry, degraded fallback, and the end-to-end secret round-trip. No network.
"""

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.analyze import build_bundle
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job
from ci_doctor.core.phases import assign_phases
from ci_doctor.llm.report import produce_report
from tests import support

_GOOD = {
    "summary": "unit tests failed on assert",
    "failure_phase": "script",
    "category": "test",
    "confidence": "high",
    "is_infra_not_code": False,
    "likely_flaky": False,
    "root_cause": "test_add asserted 1 == 2",
    "contributing_factors": [],
    "evidence": [],
    "remediation": [],
    "related_paths": [],
    "handoff_prompt": "fix test_add",
}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete_structured(self, prompt, schema):
        r = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r


def _pipeline(log, reason=FailureReason.SCRIPT_FAILURE, overrides=None, provider="gitlab"):
    # `provider` defaults to gitlab only because _SIMPLE_LOG below is a GitLab-syntax
    # literal; fixture-driven tests pass the provider the log actually came from.
    job = Job(id="1", name="build", status="failed", failure_reason=reason, log=log)
    cfg = load_config(environ={}, overrides=overrides)
    job.sections = support.segment(provider, log)
    assign_phases(job.sections, cfg.phases)
    attr = attribute(job, job.sections)
    bundle = build_bundle(job, attr, job.sections, cfg)
    return job, attr, bundle, cfg


_LLM_ON = {"llm": {"model": "test-model", "api_base": "http://fake"}}
_SIMPLE_LOG = "section_start:1:step_script\n$ pytest\nE assert 1 == 2\nsection_end:2:step_script\nERROR: Job failed: exit code 1\n"


def test_deterministic_when_llm_disabled():
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides={"llm": {"enabled": False}})
    report = produce_report(job, attr, bundle, cfg)
    assert report.failure_phase == "script"
    assert report.remediation  # templated remediation present


def test_llm_path_returns_validated_report():
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides=_LLM_ON)
    client = FakeClient([_GOOD])
    report = produce_report(job, attr, bundle, cfg, client=client)
    assert report.summary == "unit tests failed on assert"
    assert client.calls == 1


def test_repair_retry_on_invalid_then_valid():
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides=_LLM_ON)
    client = FakeClient([{"not": "a valid report"}, _GOOD])
    report = produce_report(job, attr, bundle, cfg, client=client)
    assert report.summary == "unit tests failed on assert"
    assert client.calls == 2  # one repair retry happened


def test_infer_category_from_evidence():
    from ci_doctor.llm.report import _infer_category

    # reason map wins when it can classify
    assert _infer_category(FailureReason.RUNNER_SYSTEM, "") == "infrastructure"
    assert _infer_category(FailureReason.TIMEOUT, "") == "timeout"
    assert _infer_category(FailureReason.MISSING_DEPENDENCY, "") == "dependency"
    # script_failure -> guess from the evidence
    assert _infer_category(FailureReason.SCRIPT_FAILURE, "==== FAILURES ====\nE assert 1 == 2\nFAILED tests/x.py") == "test"
    assert _infer_category(FailureReason.SCRIPT_FAILURE, "a.ts:1 error TS2345: bad\nnpm ERR! build failed") == "build"
    assert _infer_category(FailureReason.SCRIPT_FAILURE, "ERROR: Job failed: execution took longer than 1h") == "timeout"
    assert _infer_category(FailureReason.SCRIPT_FAILURE, "exit code 137\nKilled") == "infrastructure"
    assert _infer_category(FailureReason.SCRIPT_FAILURE, "nothing recognizable here") == "unknown"


_CATEGORY_FIXTURES = {
    "npm_build_failure": "build",
    "pytest_failure_verbose": "test",
    "go_test_failure": "test",
}
_CATEGORY_PARAMS = [(p, c, _CATEGORY_FIXTURES[c]) for p, c in support.pairs_for(_CATEGORY_FIXTURES)]


@pytest.mark.parametrize("provider,fixture,expected", _CATEGORY_PARAMS,
                         ids=[f"{p}-{c}" for p, c, _ in _CATEGORY_PARAMS])
def test_deterministic_category_from_fixture(provider, fixture, expected):
    # Simulate --from-file: no provider metadata, no LLM -> category inferred from evidence.
    log = support.read_log(provider, fixture)
    job, attr, bundle, cfg = _pipeline(log, reason=FailureReason.UNKNOWN, provider=provider)
    report = produce_report(job, attr, bundle, cfg)  # deterministic
    assert report.category == expected


def test_anthropic_backend_needs_no_api_base():
    # backend=anthropic is "ready" without api_base (model defaults, auth from env).
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides={"llm": {"backend": "anthropic"}})
    report = produce_report(job, attr, bundle, cfg, client=FakeClient([_GOOD]))
    assert report.summary == "unit tests failed on assert"


def test_degraded_fallback_on_llm_error():
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides=_LLM_ON)
    client = FakeClient([RuntimeError("endpoint down")])
    report = produce_report(job, attr, bundle, cfg, client=client)
    assert report.failure_phase == "script"  # deterministic fallback still correct
    assert any("deterministic fallback" in f for f in report.contributing_factors)


def test_secrets_roundtrip_no_leak():
    log = (
        "section_start:1:step_script\n"
        "$ deploy\n"
        "using token glpat-ABCDEFGHIJKLMNOPQRSTUVWX\n"
        "clone https://user:hunter2@git.internal/repo.git\n"
        "echo pushing with s3cr3t-value-xyz\n"
        "ERROR: Job failed: exit code 1\n"
        "section_end:2:step_script\n"
    )
    env = {"CI_DEPLOY_SECRET": "s3cr3t-value-xyz"}
    job, attr, bundle, cfg = _pipeline(log, reason=FailureReason.SCRIPT_FAILURE)
    report = produce_report(job, attr, bundle, cfg, environ=env)  # llm unconfigured -> deterministic

    blob = report.model_dump_json() + report.handoff_prompt
    for secret in ("glpat-ABCDEFGHIJKLMNOPQRSTUVWX", "hunter2", "s3cr3t-value-xyz"):
        assert secret not in blob, f"leaked: {secret}"
