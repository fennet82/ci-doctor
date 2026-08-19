"""Framing for a multi-job pipeline, shared by every output format.

A single failed job needs no framing — the report *is* the answer. A pipeline of
several failed jobs needs a way in: which jobs failed, in what order, and which
are the reader's own fault versus infrastructure. These helpers compute that from
the `JobResult`s the pipeline already returns — no new schema, no LLM. The
terminal and markdown renderers turn the same facts into their own shapes.
"""

from ci_doctor.pipeline import JobResult


def triage(results: list[JobResult]) -> tuple[int, int, int]:
    """Count the failures by whose fault they are.

    Args:
        results: The analyzed jobs.

    Returns:
        ``(total, code, infra)`` — total failed, how many are the code's fault,
        how many are infrastructure. The single most useful thing to know first.
    """
    total = len(results)
    infra = sum(1 for r in results if r.report.is_infra_not_code)
    return total, total - infra, infra


def triage_line(results: list[JobResult]) -> str:
    """One phrase summarising who is to blame across the pipeline.

    Args:
        results: The analyzed jobs.

    Returns:
        e.g. ``"2 failed · 1 your code · 1 infra"``. Zero-count halves are
        dropped, so a clean infra outage reads ``"3 failed · 3 infra"``.
    """
    total, code, infra = triage(results)
    bits = [f"{total} failed"]
    if code:
        bits.append(f"{code} your code")
    if infra:
        bits.append(f"{infra} infra")
    return " · ".join(bits)


def header_line(run_id: str, ref: str, sha: str, results: list[JobResult]) -> str:
    """The one-line pipeline header.

    Args:
        run_id: The run/pipeline id.
        ref: Branch or tag.
        sha: Commit analyzed.
        results: The analyzed jobs, for the triage tail.

    Returns:
        e.g. ``"Pipeline 18234 · main @ a1b2c3d — 2 failed · 1 your code · 1 infra"``.
        The ``ref @ sha`` clause is dropped when neither is known (offline replay).
    """
    where = f"{ref} @ {sha[:7]}" if sha else ref
    prefix = f"Pipeline {run_id}"
    if where:
        prefix += f" · {where}"
    return f"{prefix} — {triage_line(results)}"


def job_verdict(jr: JobResult) -> str:
    """The compact verdict for one job: phase, category, confidence.

    Args:
        jr: One analyzed job.

    Returns:
        e.g. ``"script · build · high"``. The three fields that place a failure
        without opening it.
    """
    r = jr.report
    return f"{r.failure_phase} · {r.category} · {r.confidence}"
