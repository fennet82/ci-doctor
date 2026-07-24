# ci-doctor

A postmortem CI step that runs **only when a pipeline fails**, works out *where*
and *why* it broke, and emits a structured report. Read-only: it never edits
code, never opens MRs, and **always exits 0** so it can't mask the real failure.

Central principle: **deterministic code decides *where* the job failed; the LLM
only explains *why*.** Phase attribution is a pure function of job metadata and
log structure, computed before any LLM call. See [docs/PLAN.md](docs/PLAN.md).

## Status

- **M0 — skeleton** ✅ CLI, layered config, domain model, ports, `--from-file` replay. No network, no LLM.
- **M1 — GitLab acquisition** ✅ python-gitlab adapter (self-hosted base_url/CA/proxy/token_file, version detect, empty-log handling), job selection, air-gap network guard in tests.
- **M2 — segmenter + classifier** ✅ section parsing (nesting, preamble/trailer), config phase-map, the full precedence ladder + WARNING-never-fatal rule, golden-file fixture suite (incl. the noisy-log regression). Useful with no LLM: `--from-file` prints phase/reason/rule.
- **M3 — denoise + extract + budget** ✅ ANSI/CR/dedup/noise denoising (>70% cut on noisy logs, anchors retained), tail + anchored-window extraction with visible elision, token budgeting; `build_bundle` assembles the evidence the LLM will consume.
- **M4 — LLM + structured output + redaction** ✅ pluggable backends (`openai` / `litellm` / `anthropic` / `claude_code`, selected by `llm.backend`) behind one port, JSON parse + pydantic validation + one repair retry, deterministic report when LLM disabled/unconfigured/unreachable, twice-run redaction (prompt + report) with a planted-secret round-trip test. `llm.enabled: false` is a first-class mode.
- **M5 — render + deliver** ✅ rich terminal (NO_COLOR/non-TTY/`--no-color`, collapsible inside GitLab CI), `report.md` + `report.json` artifacts, idempotent MR note (marker-based update, gated on confidence ≥ medium). Ships `Dockerfile`, `examples/gitlab-ci.example.yml`, `docs/OFFLINE.md`, and a full-pipeline no-network test.
- **M6 — GitHub adapter** ✅ `##[group]`/`##[endgroup]` segmenter (canonical section names), conclusion→reason mapping, REST provider mapping into the shared domain model — added with **zero `core/` edits** (the abstraction audit). Selected via `provider: github`.

All six milestones complete · 66 tests · no network, no LLM in the suite · `grep -riE 'gitlab|github' core/` clean.

> **LLM backends** (`llm.backend`): `openai` (default, any OpenAI-compatible `api_base` — base install, no runtime downloads) · `litellm` (any litellm provider — `pip install ci-doctor[litellm]`; may pull `tiktoken`, so avoid in strict air-gap) · `anthropic` (official SDK, defaults to `claude-opus-4-8` — `ci-doctor[anthropic]`) · `claude_code` (the local `claude` CLI). All behind one `LLMClient` port.

## Docs

A full documentation site (overview, requirements, configuration, usage, CI/CD
examples) lives in [`docs/site/`](docs/site/) — built with Astro:

```sh
cd docs/site && npm install && npm run dev   # or: npm run build -> dist/
```

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
