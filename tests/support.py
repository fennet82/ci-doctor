"""Provider-generic access to the log fixtures.

Import this instead of building fixture paths or picking a segmenter by hand.

Layout::

    fixtures/logs/<provider>/<case>.log   provider-specific raw job logs
    fixtures/expected/<case>.json         provider-NEUTRAL attribution verdicts

Logs are provider-scoped because every CI system frames a job differently.
Verdicts are not: attribution lives in provider-neutral ``core/``, so the same
scenario must classify identically whoever produced the log.

**Adding a provider** (jenkins, bitbucket, travis, ...) touches no test at all:
drop ``fixtures/logs/<provider>/``, and the segmenter it needs is already in
``providers/registry.py``. Every provider-generic test then runs against it
automatically, and ``test_attribution_fixtures.py`` fails if a fixture directory
has no registered segmenter.
"""

from collections.abc import Iterable
from pathlib import Path

from ci_doctor.core.models import Section, walk_sections
from ci_doctor.providers.registry import SEGMENTERS, segmenter_for

FIX = Path(__file__).parent / "fixtures"
LOGS = FIX / "logs"
EXPECTED = FIX / "expected"

# `SEGMENTERS` is the shipped registry, re-exported: a fixture directory is
# named after the CI system whose log format it holds, so the two cannot drift.


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


def pairs_for(cases: Iterable[str]) -> list[tuple[str, str]]:
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
    sections = segment(provider, read_log(provider, case))
    return [line.text for sec in walk_sections(sections) for line in sec.lines]


def segment(provider: str, raw_log: str) -> list[Section]:
    """Parse a raw log with the segmenter for that provider's format.

    Args:
        provider: Provider name.
        raw_log: The raw log text.

    Returns:
        The section tree.

    Raises:
        KeyError: If the provider has no registered segmenter — which is the
            point: a new `logs/` directory needs one in `providers/registry.py`.
    """
    if provider not in SEGMENTERS:
        raise KeyError(f"no segmenter registered for {provider!r}")
    return segmenter_for(provider).segment(raw_log)
