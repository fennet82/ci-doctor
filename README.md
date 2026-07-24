# ci-doctor

A postmortem CI step that runs **only when a pipeline fails**, works out *where*
and *why* it broke, and emits a structured report. Read-only: it never edits
code, never opens MRs, and **always exits 0** so it can't mask the real failure.

Central principle: **deterministic code decides *where* the job failed; the LLM
only explains *why*.** Phase attribution is a pure function of job metadata and
log structure, computed before any LLM call. See [docs/PLAN.md](docs/PLAN.md).

## Status

- **M0 — skeleton** ✅ CLI, layered config, domain model, ports, `--from-file` replay. No network, no LLM.
- M1 GitLab acquisition · M2 segmenter + classifier · M3 denoise/extract/budget · M4 LLM + redaction · M5 render/deliver · M6 GitHub adapter — not started.

## Develop

```sh
uv sync            # create venv, install deps + dev tools
uv run pytest      # run the suite
```

## Try it (offline replay)

```sh
uv run ci-doctor analyze --from-file tests/fixtures/sample.log
```

M0 loads the log into the domain model and prints a summary. The same
`--from-file` path grows real segmentation/attribution/analysis as milestones land.
