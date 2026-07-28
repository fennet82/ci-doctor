"""Every shipped language pack must fire on a realistic job log.

A matcher that never matches fails silently (the tail window still returns
something), so each pack gets a fixture log plus the causal line it must pull
into the evidence — checked twice: on the matcher alone, and end to end through
segment -> attribute -> build_bundle -> deterministic_report.
"""

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.analyze import build_bundle
from ci_doctor.core.attribution import attribute
from ci_doctor.core.extract import _windows_for, extract
from ci_doctor.core.models import FailureReason, Job
from ci_doctor.core.phases import assign_phases
from ci_doctor.llm.report import _infer_category, deterministic_report
from tests import support

CFG = load_config(environ={})
MATCHERS = {m.id: m for m in CFG.extraction.matchers}

# (fixture stem, matcher ids it must trigger, causal line, deterministic category)
CASES = [
    ("rust_test_failure", ["rust_test", "rust_panic"], "assertion `left == right` failed", "test"),
    ("rust_compile_error", ["rust_compile"], "expected `Decimal`, found `Option<Decimal>`", "build"),
    ("dotnet_test_failure", ["dotnet_test"], "Assert.Equal() Failure: Values differ", "test"),
    ("dotnet_build_error", ["dotnet_build"], "error CS1061", "build"),
    ("rspec_failure", ["rspec"], "expected: 2700", "test"),
    ("minitest_failure", ["minitest"], "Expected: 2700", "test"),
    (
        "ruby_rake_exception",
        ["ruby_exception"],
        "undefined method `default_tier' for nil (NoMethodError)",
        "runtime",
    ),
    ("bundler_missing_gem", ["bundler"], "Could not find gem 'sidekiq-pro (~> 7.2)'", "dependency"),
    ("phpunit_failure", ["phpunit"], "Failed asserting that 2000 matches expected 1900.", "test"),
    ("php_fatal_error", ["php_fatal"], "must be of type float, string given", "runtime"),
    (
        "composer_conflict",
        ["composer"],
        "your php version (8.1.27) does not satisfy that requirement",
        "dependency",
    ),
    ("gradle_build_failure", ["gradle"], "Execution failed for task ':app:compileJava'.", "build"),
    ("bazel_build_failure", ["bazel"], "'GL_TEXTURE_2D_ARRAY' was not declared in this scope", "build"),
    ("playwright_failure", ["playwright"], 'Expected string: "$27.00"', "test"),
    ("cypress_failure", ["cypress"], "but the text was '$30.00'", "test"),
    ("python_traceback", ["python_traceback"], "ZeroDivisionError: float division by zero", "runtime"),
    ("go_panic", ["go_panic"], "index out of range [512] with length 512", "runtime"),
    ("cpp_link_error", ["cc_cpp"], "undefined reference to `Pipeline::flush()'", "build"),
    ("eslint_errors", ["eslint"], "'discount' is assigned a value but never used", "build"),
    ("mypy_type_error", ["python_lint"], 'Unsupported operand types for / ("Decimal" and "None")', "build"),
    ("terraform_apply_error", ["terraform"], "BucketAlreadyOwnedByYou", "config"),
    ("pnpm_lockfile_outdated", ["pnpm"], "ERR_PNPM_OUTDATED_LOCKFILE", "dependency"),
    ("yarn_missing_package", ["yarn"], 'Couldn\'t find package "@acme/design-tokens@^3.1.0"', "dependency"),
    ("bun_test_failure", ["bun"], "error: expect(received).toBe(expected)", "test"),
    ("node_uncaught_error", ["node_runtime"], "Error: missing DATABASE_URL in production", "runtime"),
    ("pytest_failure_verbose", ["pytest"], "assert 3000 == 2700", "test"),
    ("jest_test_failure", ["jest"], "Cart › applies a percentage discount to the subtotal", "test"),
    ("go_test_failure", ["go_test"], "drift not detected", "test"),
    ("maven_build_failure", ["maven_gradle"], "incompatible types: java.util.Optional", "build"),
    # One log, two packs: `npm run build` shells out to tsc, so the wrapper's
    # epilogue and the compiler's diagnosis both live here.
    ("npm_build_failure", ["tsc", "npm"], "Argument of type 'string | undefined'", "build"),
    # `infrastructure`, not `build`: the compiler died because the runner ran out
    # of memory, so the actionable answer is the runner, not the C++.
    (
        "docker_build_oom",
        ["docker_build", "oom"],
        "Killed signal terminated program cc1plus",
        "infrastructure",
    ),
]

# Cross every case with the providers that ship a log for it, so a future
# logs/<provider>/rust_test_failure.log is exercised without touching this file.
BY_CASE = {c[0]: c for c in CASES}
PARAMS = [(p, *BY_CASE[c]) for p, c in support.pairs_for(BY_CASE)]
IDS = [f"{p}-{c}" for p, c, *_ in PARAMS]


def _analyze(provider: str, stem: str):
    """Run a fixture through the real deterministic pipeline."""
    log = support.read_log(provider, stem)
    job = Job(id=stem, name=stem, status="failed", failure_reason=FailureReason.SCRIPT_FAILURE, log=log)
    job.sections = support.segment(provider, log)
    assign_phases(job.sections, CFG.phases)
    attr = attribute(job, job.sections)
    return job, attr, build_bundle(job, attr, job.sections, CFG)


@pytest.mark.parametrize("provider,stem,matcher_ids,causal,_category", PARAMS, ids=IDS)
def test_pack_matches_and_stays_targeted(provider, stem, matcher_ids, causal, _category):
    """The pack fires, captures the causal line, and windows rather than swallows."""
    lines = support.log_lines(provider, stem)
    for mid in matcher_ids:
        assert _windows_for(lines, [MATCHERS[mid]]), f"{mid} matched nothing in {stem}.log"

    # tail_lines=0 so only these matchers can supply output: proves the pack
    # itself captures the cause, and that it selects rather than swallows.
    out = extract(lines, [MATCHERS[m] for m in matcher_ids], tail_lines=0)
    assert any(causal in line for line in out), f"{matcher_ids} missed {causal!r}"
    assert len(out) < len(lines), f"{matcher_ids} captured the whole log instead of a window"


@pytest.mark.parametrize("provider,stem,matcher_ids,causal,_category", PARAMS, ids=IDS)
def test_causal_line_survives_into_the_bundle(provider, stem, matcher_ids, causal, _category):
    """The causal line survives the full pipeline into the evidence bundle."""
    _job, _attr, bundle = _analyze(provider, stem)
    joined = "\n".join(bundle.blamed_lines)
    assert causal in joined, f"{stem}: {causal!r} missing from the evidence bundle"
    # The blamed section is the script phase, so runner/setup chrome stays out.
    assert "Preparing the " not in joined


@pytest.mark.parametrize("provider,stem,matcher_ids,causal,category", PARAMS, ids=IDS)
def test_deterministic_category(provider, stem, matcher_ids, causal, category):
    """Each fixture classifies to its expected category with no LLM involved."""
    job, attr, bundle = _analyze(provider, stem)
    report = deterministic_report(job, attr, bundle)
    assert report.category == category


# Every failed job ends with the runner saying so, in the runner's own words. A
# pack that fires on *that* line has coverage on paper for every fixture in the
# suite while proving nothing about the tool it claims to match — which is how
# `bazel` came to open a priority-85 window on 40 of 41 logs, on the strength of
# `^ERROR: .*(failed|FAILED)` matching "ERROR: Job failed: exit code 1".
RUNNER_TRAILERS = [
    # GitLab
    "ERROR: Job failed",
    "ERROR: Job failed: exit code 1",
    "ERROR: Job failed: exit code 2",
    "ERROR: Job failed: exit code 255",
    "ERROR: Job failed: execution took longer than 1h0m0s",
    "ERROR: Job failed: missing dependency failure",
    "ERROR: Job failed (system failure): prepare environment: exit status 1",
    'ERROR: Preparation failed: failed to pull image "registry.internal/ci/builder:2025.09"',
    # GitHub
    "##[error]Process completed with exit code 1.",
    "##[error]The operation was canceled.",
    "##[error]Unable to download artifact(s): Artifact not found for name: compile",
]

# The two packs that read the runner's framing on purpose. `generic_error` is the
# fallback for tools with no signature at all, and `exit code 137` in the trailer
# is the *only* place a GitLab job says it was OOM-killed.
FRAMING_READERS = {"generic_error", "oom"}


@pytest.mark.parametrize("matcher_id", sorted(set(MATCHERS) - FRAMING_READERS))
def test_no_pack_fires_on_the_runners_own_trailer(matcher_id):
    """A language pack must recognise its tool, not the runner announcing failure."""
    hits = [line for line in RUNNER_TRAILERS if _windows_for([line], [MATCHERS[matcher_id]])]
    assert not hits, f"{matcher_id} matches runner framing, not its tool: {hits}"


def test_every_shipped_pack_is_covered_by_a_case():
    """A pack with no `CASES` row is a pack nothing proves ever fires.

    `generic_error` is exempt: it is the fallback for tools with no signature,
    so it is asserted below instead — the shared "windows rather than swallows"
    check contradicts a matcher whose whole job is to be greedy.
    """
    uncovered = set(MATCHERS) - {mid for _stem, ids, *_ in CASES for mid in ids} - {"generic_error"}
    assert not uncovered, f"no fixture case exercises: {sorted(uncovered)}"


@pytest.mark.parametrize("provider", support.providers())
def test_generic_error_catches_a_tool_with_no_pack(provider):
    """The fallback fires when an unrecognised tool reports a fatal problem."""
    lines = support.log_lines(provider, "warning_only_fetch")
    out = extract(lines, [MATCHERS["generic_error"]], tail_lines=0)
    assert any("custom tool reported a fatal problem" in line for line in out)


# `runtime` is checked last on purpose: a traceback also appears in a pytest
# failure and in a missing-import crash, and those answers are more actionable.
@pytest.mark.parametrize(
    "expected,text",
    [
        ("test", "=== FAILURES ===\nTraceback (most recent call last):\nAssertionError: assert 1 == 2"),
        ("dependency", "Traceback (most recent call last):\nModuleNotFoundError: No module named 'requests'"),
        ("runtime", "Traceback (most recent call last):\nZeroDivisionError: division by zero"),
        # node:internal/ frames show up in every JS stack, including a jest failure
        # and a missing-module crash — neither of which is a "runtime" answer.
        (
            "test",
            "● Cart > applies discount\nexpect(received).toBe(expected)\n    at Module._compile (node:internal/modules/cjs/loader:1356:14)",
        ),
        (
            "dependency",
            "Error [ERR_MODULE_NOT_FOUND]: Cannot find module\n    at node:internal/modules/esm/resolve:264:11\nNode.js v20.11.1",
        ),
        (
            "runtime",
            "Error: missing DATABASE_URL\n    at node:internal/main/run_main_module:28:49\nNode.js v20.11.1",
        ),
    ],
)
def test_runtime_never_outranks_a_more_actionable_category(expected, text):
    """Pins the signature ordering: a traceback alone never wins over test/dependency."""
    assert _infer_category(FailureReason.SCRIPT_FAILURE, text) == expected
