"""Provider-generic access to the log fixtures. Import this instead of building
fixture paths or picking a segmenter by hand.

Layout::

    fixtures/logs/<provider>/<case>.log   provider-specific raw job logs
    fixtures/expected/<case>.json         provider-NEUTRAL attribution verdicts

Logs are provider-scoped because every CI system frames a job differently.
Verdicts are not: attribution lives in provider-neutral ``core/``, so the same
scenario must classify identically whoever produced the log.

**Adding a provider** (jenkins, bitbucket, travis, ...) is two steps and touches
no test: drop ``fixtures/logs/<provider>/`` and register its segmenter in
``SEGMENTERS``. Every provider-generic test then runs against it automatically.
``test_attribution_fixtures.py`` fails if the directory and the registry drift.
"""

from pathlib import Path

from ci_doctor.core.ports import LogSegmenter
from ci_doctor.providers.github.segmenter import GitHubSegmenter
from ci_doctor.providers.gitlab.segmenter import GitLabSegmenter

FIX = Path(__file__).parent / "fixtures"
LOGS = FIX / "logs"
EXPECTED = FIX / "expected"

# provider name (== the logs/ subdirectory) -> segmenter for that log format.
SEGMENTERS: dict[str, type[LogSegmenter]] = {
    "gitlab": GitLabSegmenter,
    "github": GitHubSegmenter,
}


def providers() -> list[str]:
    """List the providers that actually ship log fixtures.

    Returns:
        Provider names, sorted, taken from the `logs/` subdirectories.
    """
    return sorted(d.name for d in LOGS.iterdir() if d.is_dir())


def log_path(provider: str, case: str) -> Path:
    """Path to one provider's log for a case.

    Args:
        provider: Provider name, matching a `logs/` subdirectory.
        case: Fixture stem, e.g. "npm_build_failure".

    Returns:
        The path. Not checked for existence — use :func:`providers_with` for that.
    """
    return LOGS / provider / f"{case}.log"


def read_log(provider: str, case: str) -> str:
    """Read one provider's log for a case.

    Args:
        provider: Provider name.
        case: Fixture stem.

    Returns:
        The raw log text.

    Raises:
        FileNotFoundError: If that provider ships no log for the case.
    """
    return log_path(provider, case).read_text()


def providers_with(case: str) -> list[str]:
    """Find every provider shipping a log for a case. Feed straight to parametrize.

    Args:
        case: Fixture stem.

    Returns:
        Provider names. A list, not a generator, so a case with no log anywhere
        yields *no tests* rather than one that silently passes.
    """
    return [p for p in providers() if log_path(p, case).is_file()]


def pairs_for(cases) -> list[tuple[str, str]]:
    """Cross a set of cases with the providers that ship each one.

    Args:
        cases: Any iterable of fixture stems, including a dict keyed by them.

    Returns:
        ``(provider, case)`` pairs, ready for `pytest.mark.parametrize`.
    """
    return [(p, case) for case in cases for p in providers_with(case)]


def log_lines(provider: str, case: str) -> list[str]:
    """One provider's log as the *pipeline* sees it, not as it sits on disk.

    Args:
        provider: Provider name.
        case: Fixture stem.

    Returns:
        Every section's lines in log order. Segmentation is what strips a
        provider's framing (GitHub prefixes every line with an ISO timestamp),
        so a matcher anchored at `^` only behaves the same across providers on
        *these* lines — reading the raw file instead tests something the
        extractor never receives.
    """
    return [line.text for sec in _walk(segment(provider, read_log(provider, case))) for line in sec.lines]


def _walk(sections):
    """Yield every section depth-first, parents before their children."""
    for sec in sections:
        yield sec
        yield from _walk(sec.children)


def segment(provider: str, raw_log: str):
    """Parse a raw log with the segmenter for that provider's format.

    Args:
        provider: Provider name.
        raw_log: The raw log text.

    Returns:
        The section tree.

    Raises:
        KeyError: If the provider has no registered segmenter — which is the
            point: a new `logs/` directory must register one.
    """
    return SEGMENTERS[provider]().segment(raw_log)
