"""LLM report tests.

Deterministic path, LLM path with recorded responses, degraded fallback, and
the end-to-end secret round-trip. No network.

The repair retry on a schema-invalid reply now lives inside each backend
(`llm/backends.py::PydanticAILLMClient`'s `Agent(retries=1)`), not in
`_call_and_validate` — see tests/test_backends.py for that. `FakeClient` here
fakes the `LLMClient` port directly, standing in for an already-fully-run
backend, so an invalid first reply degrades immediately with no second call.
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
    """Scripted LLM client: replays queued responses, raising any that are exceptions."""

    def __init__(self, responses):
        """Queue the responses to replay, last one repeating once exhausted."""
        self.responses = list(responses)
        self.calls = 0

    def complete_structured(self, prompt):
        """Return the next queued response, or raise it if it is an exception."""
        r = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r


def _pipeline(log, reason=FailureReason.SCRIPT_FAILURE, overrides=None, provider="gitlab"):
    """Run a log through the deterministic stages up to (not including) the report.

    `provider` defaults to gitlab only because `_SIMPLE_LOG` is a GitLab-syntax
    literal; fixture-driven tests pass the provider the log actually came from.

    Returns:
        A ``(job, attribution, bundle, config)`` tuple.
    """
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
    """With the LLM off, the report is still complete and correctly phased."""
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides={"llm": {"enabled": False}})
    report = produce_report(job, attr, bundle, cfg)
    assert report.failure_phase == "script"
    assert report.remediation  # templated remediation present


def test_llm_path_returns_validated_report():
    """A valid LLM reply is used directly, in a single call."""
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides=_LLM_ON)
    client = FakeClient([_GOOD])
    report = produce_report(job, attr, bundle, cfg, client=client)
    assert report.summary == "unit tests failed on assert"
    assert client.calls == 1


def test_skip_llm_for_short_circuits_before_the_client():
    """A reason listed in `analysis.skip_llm_for` never reaches the backend.

    The reason already settles the verdict, so paying for a call would buy
    nothing — and the report must still be complete, not a stub.
    """
    log = "section_start:1:prepare_executor\nnothing here\n"
    job, attr, bundle, cfg = _pipeline(log, reason=FailureReason.NO_RUNNER, overrides=_LLM_ON)
    client = FakeClient([_GOOD])
    report = produce_report(job, attr, bundle, cfg, client=client)
    assert client.calls == 0
    assert report.failure_phase == "provision"
    assert report.remediation  # templated remediation, not an empty shell


def test_skip_llm_for_can_be_emptied():
    """Clearing the list lets even a fully-determined reason reach the LLM."""
    log = "section_start:1:prepare_executor\nnothing here\n"
    job, attr, bundle, cfg = _pipeline(
        log, reason=FailureReason.NO_RUNNER, overrides={**_LLM_ON, "analysis": {"skip_llm_for": []}}
    )
    client = FakeClient([_GOOD])
    produce_report(job, attr, bundle, cfg, client=client)
    assert client.calls == 1


def test_the_llm_cannot_overrule_the_attributed_phase():
    """Invariant #1: attribution owns the phase, in code and not only in the prompt.

    The reply here is schema-valid and claims `script`, while attribution said
    `provision`. Accepting it would let a model relabel infrastructure as the
    user's bug — the one verdict the deterministic pipeline exists to protect.
    """
    log = "section_start:1:prepare_executor\nnothing here\n"
    job, attr, bundle, cfg = _pipeline(
        log, reason=FailureReason.NO_RUNNER, overrides={**_LLM_ON, "analysis": {"skip_llm_for": []}}
    )
    assert _GOOD["failure_phase"] == "script"  # the reply disagrees on purpose
    report = produce_report(job, attr, bundle, cfg, client=FakeClient([_GOOD]))
    assert attr.phase == "provision"
    assert report.failure_phase == "provision"
    assert report.summary == _GOOD["summary"]  # only the phase is overruled


def test_invalid_reply_degrades_without_a_second_call_here():
    """An invalid reply degrades directly — no retry at this layer.

    The repair retry now lives inside the backend itself (Pydantic AI's
    `Agent(retries=1)`); by the time a reply reaches `_call_and_validate`,
    that ceiling has already run its course. A second hand-rolled attempt
    here would just duplicate it — the exact bug fixed once already (see
    git history around `149313e`).
    """
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides=_LLM_ON)
    client = FakeClient([{"not": "a valid report"}, _GOOD])
    report = produce_report(job, attr, bundle, cfg, client=client)
    assert client.calls == 1  # no retry issued from report.py itself
    assert any("deterministic fallback" in f for f in report.contributing_factors)


def test_infer_category_from_evidence():
    """The reason decides when it can; otherwise the evidence signatures do."""
    from ci_doctor.llm.report import _infer_category

    # reason map wins when it can classify
    assert _infer_category(FailureReason.RUNNER_SYSTEM, "") == "infrastructure"
    assert _infer_category(FailureReason.TIMEOUT, "") == "timeout"
    assert _infer_category(FailureReason.MISSING_DEPENDENCY, "") == "dependency"
    # script_failure -> guess from the evidence
    assert (
        _infer_category(
            FailureReason.SCRIPT_FAILURE, "==== FAILURES ====\nE assert 1 == 2\nFAILED tests/x.py"
        )
        == "test"
    )
    assert (
        _infer_category(FailureReason.SCRIPT_FAILURE, "a.ts:1 error TS2345: bad\nnpm ERR! build failed")
        == "build"
    )
    assert (
        _infer_category(FailureReason.SCRIPT_FAILURE, "ERROR: Job failed: execution took longer than 1h")
        == "timeout"
    )
    assert _infer_category(FailureReason.SCRIPT_FAILURE, "exit code 137\nKilled") == "infrastructure"
    assert _infer_category(FailureReason.SCRIPT_FAILURE, "nothing recognizable here") == "unknown"


_CATEGORY_FIXTURES = {
    "npm_build_failure": "build",
    "pytest_failure_verbose": "test",
    "go_test_failure": "test",
}
_CATEGORY_PARAMS = [(p, c, _CATEGORY_FIXTURES[c]) for p, c in support.pairs_for(_CATEGORY_FIXTURES)]


@pytest.mark.parametrize(
    "provider,fixture,expected", _CATEGORY_PARAMS, ids=[f"{p}-{c}" for p, c, _ in _CATEGORY_PARAMS]
)
def test_deterministic_category_from_fixture(provider, fixture, expected):
    """Offline replay, with no metadata and no LLM, still classifies correctly."""
    log = support.read_log(provider, fixture)
    job, attr, bundle, cfg = _pipeline(log, reason=FailureReason.UNKNOWN, provider=provider)
    report = produce_report(job, attr, bundle, cfg)  # deterministic
    assert report.category == expected


def test_litellm_backend_needs_no_api_base():
    """Litellm is ready on `model` alone — it routes by model name, not endpoint."""
    job, attr, bundle, cfg = _pipeline(
        _SIMPLE_LOG, overrides={"llm": {"backend": "litellm", "model": "bedrock/anthropic.claude-v2"}}
    )
    report = produce_report(job, attr, bundle, cfg, client=FakeClient([_GOOD]))
    assert report.summary == "unit tests failed on assert"


def test_degraded_fallback_on_llm_error():
    """A failed LLM call still yields a correct report, and says it degraded."""
    job, attr, bundle, cfg = _pipeline(_SIMPLE_LOG, overrides=_LLM_ON)
    client = FakeClient([RuntimeError("endpoint down")])
    report = produce_report(job, attr, bundle, cfg, client=client)
    assert report.failure_phase == "script"  # deterministic fallback still correct
    assert any("deterministic fallback" in f for f in report.contributing_factors)


def test_secrets_roundtrip_no_leak():
    """No secret in the log reaches the report, the JSON dump or the handoff prompt."""
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
