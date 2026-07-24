"""Pure job-selection policy. Provider-neutral: operates on domain Jobs only."""

from __future__ import annotations

from ci_doctor.core.models import Job


def select_failed_jobs(jobs: list[Job], include_allowed_failures: bool = False) -> list[Job]:
    """Failed jobs worth analyzing. `allow_failure` jobs are noise by default."""
    out = []
    for job in jobs:
        if job.status != "failed":
            continue
        if job.allow_failure and not include_allowed_failures:
            continue
        out.append(job)
    return out
