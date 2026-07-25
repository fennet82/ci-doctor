"""Pure job-selection policy. Provider-neutral: operates on domain Jobs only."""

from __future__ import annotations

from ci_doctor.core.models import Job


def select_failed_jobs(jobs: list[Job], include_allowed_failures: bool = False) -> list[Job]:
    """Pick the failed jobs worth analyzing.

    Args:
        jobs: Every job in the run, in pipeline order.
        include_allowed_failures: Keep jobs marked `allow_failure`. They are
            noise by default — the pipeline already decided it tolerates them.

    Returns:
        The selected jobs, order preserved.
    """
    out = []
    for job in jobs:
        if job.status != "failed":
            continue
        if job.allow_failure and not include_allowed_failures:
            continue
        out.append(job)
    return out
