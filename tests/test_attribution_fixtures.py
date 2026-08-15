"""Golden-file attribution suite.

Segment + phase-map + classify each fixture log, then compare
{phase, reason, rule_id} to expected. No network, no LLM.

One `expected/<case>.json` covers every provider that ships a log for that case —
attribution is provider-neutral, so adding `logs/github/oom_137.log` asserts it
against the same verdict with no change here. See `tests/support.py`.
"""

import json

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job, LogLine, Phase, Section
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
        id=case,
        name=case,
        status=meta.get("status", "failed"),
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


def _section(name, phase, *texts, closed=True):
    """A ready-to-classify section, so these cases need no fixture file."""
    sec = Section(name=name, closed=closed, lines=[LogLine(number=i, text=t) for i, t in enumerate(texts, 1)])
    sec.phase = phase
    return sec


# The runner's own two ways of saying "warning": GitLab's literal prefix, and
# GitHub's annotation. The fixtures cannot pin this — in both of them the noisy
# section happens to come *before* the real error, so log order alone would keep
# it from being blamed.
@pytest.mark.parametrize(
    "warning",
    [
        "WARNING: failed to extract cache: ERROR 404 Not Found",
        "##[warning]Failed to restore cache: ERROR 404 Not Found",
    ],
)
def test_a_runner_warning_is_never_the_verdict(warning):
    """The "blamed the cache" bug: a warning that *mentions* an error is not one.

    The warning section is deliberately last, so only the rule — not log order —
    can keep it from being blamed.
    """
    sections = [
        _section("step_script", Phase.SCRIPT, "$ ./deploy.sh", "ERROR: deploy script aborted"),
        _section("archive_cache", Phase.POST, warning),
    ]
    job = Job(id="1", name="j", status="failed", failure_reason=FailureReason.UNKNOWN, log="x")

    attr = attribute(job, sections)
    assert attr.rule_id == "last_error_section"
    assert attr.phase == Phase.SCRIPT, f"{warning!r} was treated as fatal"
    assert attr.secondary_phases == [Phase.POST]  # mentioned as context, never blamed


# The other half of the same rule, and the reason it is case-sensitive: apt, pip
# and docker all print "Warning:" from inside a step. That is the step's output,
# not the runner excusing it, so it must not buy the section an exemption.
@pytest.mark.parametrize(
    "line",
    [
        "Warning: apt does not have a stable CLI interface. ERROR downloading package",
        "warning: docker build ran with an ERROR in the final layer",
    ],
)
def test_a_tools_own_warning_is_not_a_runner_advisory(line):
    """A step printing the word "warning" is still a step that failed."""
    sections = [
        _section("get_sources", Phase.FETCH, "Syncing repository"),
        _section("step_script", Phase.SCRIPT, "$ ./build.sh", line),
    ]
    job = Job(id="1", name="j", status="failed", failure_reason=FailureReason.UNKNOWN, log="x")

    attr = attribute(job, sections)
    assert attr.phase == Phase.SCRIPT, f"{line!r} was excused as a runner advisory"
    assert attr.rule_id == "last_error_section"


# The mirror of the rule above: the runner's own error annotation. Easy to miss,
# because the common one ("Process completed with exit code 1") is also caught by
# the exit-code branch — so only an annotation *without* an exit code proves it.
@pytest.mark.parametrize(
    "error",
    [
        "##[error]The operation was canceled.",
        "##[error]Unable to download artifact(s): Artifact not found for name: compile",
    ],
)
def test_a_runner_error_annotation_is_blamed(error):
    """A runner saying "this failed" is fatal even with no exit code in the text."""
    sections = [
        _section("get_sources", Phase.FETCH, "Syncing repository"),
        _section("step_script", Phase.SCRIPT, "building", error),
    ]
    job = Job(id="1", name="j", status="failed", failure_reason=FailureReason.UNKNOWN, log="x")

    attr = attribute(job, sections)
    assert attr.rule_id == "last_error_section"
    assert attr.phase == Phase.SCRIPT, f"{error!r} was not recognised as a failure"


def test_the_classifier_does_not_carry_a_second_matcher_catalogue():
    """`_ERROR_RE` decides which *section* failed; `extraction.matchers` picks lines.

    Keeping vendor signatures out of the classifier is what stops the catalogue
    being duplicated in code, where no `.ci-doctor.yml` could tune it. A tool that
    fails at all makes its runner say so, so the coarse question needs no help.
    """
    from ci_doctor.core.attribution import _is_error_line

    assert not _is_error_line("npm ERR! code ELIFECYCLE")
    assert not _is_error_line("    at Object.<anonymous> (/app/index.js:12:9)")
    assert _is_error_line("ERROR: Job failed: exit code 1")


def test_every_provider_dir_has_a_segmenter():
    """Guards the "drop a directory" workflow.

    A `logs/<provider>/` with no registered segmenter would silently contribute
    zero tests instead of failing.
    """
    missing = set(support.providers()) - set(support.SEGMENTERS)
    assert not missing, f"logs/ dirs with no segmenter in providers/registry.py: {sorted(missing)}"


def test_every_expected_verdict_has_at_least_one_log():
    """An orphaned `expected/*.json` would otherwise silently assert nothing."""
    covered = {case for _, case in CASES}
    assert set(SPECS) == covered, f"no log for: {sorted(set(SPECS) - covered)}"


def test_every_log_has_an_expected_verdict():
    """The inverse guard: a log with no verdict is a fixture nothing asserts on.

    Easy to drift into — a language pack only needs a log and a row in
    `test_matcher_packs.py` to look covered, while its *attribution* stays
    untested.
    """
    orphans = {p.stem for prov in support.providers() for p in (support.LOGS / prov).glob("*.log")} - set(
        SPECS
    )
    assert not orphans, f"no expected/*.json for: {sorted(orphans)}"


def test_every_case_is_covered_by_every_provider():
    """A scenario one provider ships and another does not is an untested format.

    Attribution is provider-neutral, so a missing twin means that verdict was
    only ever proved against one log format.
    """
    everywhere = set(support.providers())
    gaps = {case: everywhere - set(support.providers_with(case)) for case in SPECS}
    assert not any(gaps.values()), f"missing logs: { {c: sorted(p) for c, p in gaps.items() if p} }"


def test_regression_case_is_present():
    """Guard the guard: the noisy-log regression fixture must not go missing."""
    assert "script_failure_noisy" in SPECS
