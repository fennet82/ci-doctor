"""Pure job-selection policy. Provider-neutral: operates on domain Jobs only.

The one place that decides which statuses are worth a postmortem, so the answer
cannot drift between adapters — it did, silently, when GitHub flattened a
cancelled job into "failed" and GitLab left it as its own status.
"""

from ci_doctor.core.models import Job

#: Statuses worth analyzing. A cancelled job is included because *why* it was
#: still running when the axe fell is exactly the question — a timeout, a
#: superseding pipeline, or a hang — and attribution has a rule for it
#: (`reason_cancelled`, which blames whatever section was open). It is cheap:
#: `analysis.skip_llm_for` ships with "cancelled" in it, so no model is called.
_ANALYZED = frozenset({"failed", "cancelled"})


def select_failed_jobs(jobs: list[Job], include_allowed_failures: bool = False) -> list[Job]:
    """Pick the jobs worth analyzing.

    Args:
        jobs: Every job in the run, in pipeline order.
        include_allowed_failures: Keep jobs marked `allow_failure`. They are
            noise by default — the pipeline already decided it tolerates them.

    Returns:
        The selected jobs, order preserved.
    """
    out = []
    for job in jobs:
        if job.status not in _ANALYZED:
            continue
        if job.allow_failure and not include_allowed_failures:
            continue
        out.append(job)
    return out
