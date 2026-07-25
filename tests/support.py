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
    """Providers that actually ship log fixtures."""
    return sorted(d.name for d in LOGS.iterdir() if d.is_dir())


def log_path(provider: str, case: str) -> Path:
    return LOGS / provider / f"{case}.log"


def read_log(provider: str, case: str) -> str:
    return log_path(provider, case).read_text()


def providers_with(case: str) -> list[str]:
    """Every provider shipping a log for `case` — feed straight to parametrize.

    Returns a list so a case with no log anywhere yields no tests rather than
    silently passing.
    """
    return [p for p in providers() if log_path(p, case).is_file()]


def pairs_for(cases) -> list[tuple[str, str]]:
    """(provider, case) for each case, across every provider that has it."""
    return [(p, case) for case in cases for p in providers_with(case)]


def segment(provider: str, raw_log: str):
    """Parse a raw log with the segmenter for that provider's format."""
    return SEGMENTERS[provider]().segment(raw_log)
