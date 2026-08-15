"""The analysis pipeline, with no CLI in it.

`segment -> attribute -> evidence -> report`, over a live run or a log file on
disk. It lives outside `cli.py` because the CLI is not the only caller that
needs it: anything driving ci-doctor programmatically wants the same three
functions without a typer command in the call stack.

Not in `core/`: choosing an adapter and reading a run is I/O, and `core/` stays
pure and provider-neutral (invariant #2). This is the layer that knows both.
"""

import logging
from pathlib import Path
from typing import NamedTuple

from ci_doctor.config.schema import Config
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.models import FailureReason, Job, Run
from ci_doctor.core.ports import CIProvider
from ci_doctor.llm.schema import Report
from ci_doctor.providers.registry import make_ci_provider, make_segmenter

log = logging.getLogger("ci_doctor.pipeline")


class JobResult(NamedTuple):
    """One analyzed job: what failed, where, and what to tell the reader.

    Attributes:
        job: The failed job, log and sections attached.
        attribution: The classifier's verdict. Owns the phase; the LLM may not.
        report: The final report, validated and redacted.
    """

    job: Job
    attribution: Attribution
    report: Report


def process_job(job: Job, cfg: Config) -> JobResult:
    """Segment, classify, assemble evidence and produce the report for one job.

    Deterministic when the LLM is disabled or unconfigured; one LLM call otherwise.

    Args:
        job: The failed job, log already attached.
        cfg: The effective config.

    Returns:
        The job, its verdict, and its report.
    """
    from ci_doctor.core.analyze import build_bundle
    from ci_doctor.core.attribution import attribute
    from ci_doctor.core.phases import assign_phases
    from ci_doctor.llm.report import produce_report

    job.sections = make_segmenter(cfg).segment(job.log or "")
    log.debug("job %s: %d top-level sections", job.name, len(job.sections))
    assign_phases(job.sections, cfg.phases)
    attr = attribute(job, job.sections)
    log.debug(
        "job %s: attribution phase=%s reason=%s rule=%s confidence=%s",
        job.name,
        attr.phase,
        attr.reason,
        attr.rule_id,
        attr.confidence,
    )
    bundle = build_bundle(job, attr, job.sections, cfg)
    report = produce_report(job, attr, bundle, cfg)
    log.debug(
        "job %s: report category=%s confidence=%s infra=%s",
        job.name,
        report.category,
        report.confidence,
        report.is_infra_not_code,
    )
    return JobResult(job, attr, report)


def analyze_run(
    run_id: str, cfg: Config, job_id: str | None = None
) -> tuple[Run, CIProvider, list[JobResult]]:
    """Fetch a run from the provider and analyze its failed jobs.

    Args:
        run_id: The provider's run/pipeline id.
        cfg: The effective config.
        job_id: Restrict to a single job id, or None for all failed ones.

    Returns:
        A ``(run, provider, results)`` tuple. The provider is returned so the
        caller can post an MR note without reconnecting.
    """
    from ci_doctor.core.select import select_failed_jobs

    provider = make_ci_provider(cfg)
    run = provider.fetch_run(run_id)
    jobs = select_failed_jobs(run.jobs, cfg.analysis.include_allowed_failures)
    if job_id is not None:
        jobs = [j for j in jobs if j.id == job_id]
    log.debug("run %s: %d jobs, %d failed and selected", run_id, len(run.jobs), len(jobs))

    selected = jobs[: cfg.analysis.max_jobs_analyzed]
    log.info("analyzing %d failed job(s) from run %s", len(selected), run_id)
    results = []
    for job in selected:
        job.log = provider.fetch_job_log(job)
        results.append(process_job(job, cfg))
    return run, provider, results


def analyze_log(path: Path, cfg: Config) -> list[JobResult]:
    """Analyze a captured job log offline.

    Args:
        path: The log file.
        cfg: The effective config.

    Returns:
        One result per job in the synthetic run — today always exactly one.

    Raises:
        OSError: If the file cannot be read.
    """
    jobs = run_from_file(path).jobs
    log.info("analyzing %d job(s) from %s", len(jobs), path)
    return [process_job(job, cfg) for job in jobs]


def run_from_file(path: Path) -> Run:
    """Load a raw job log into the domain model for offline replay.

    Args:
        path: The log file.

    Returns:
        A synthetic single-job run. The failure reason is UNKNOWN — a bare log
        carries no provider metadata — which sends attribution down its
        structural fallback rules.

    Raises:
        OSError: If the file cannot be read.
    """
    raw = path.read_text()
    job = Job(id="local", name=path.stem, status="failed", failure_reason=FailureReason.UNKNOWN, log=raw)
    return Run(id="local", jobs=[job])
