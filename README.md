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
- **M4 — LLM + structured output + redaction** ✅ BYO OpenAI-compatible client (any `api_base`, no runtime downloads), JSON-mode + pydantic validation + one repair retry, deterministic report when LLM disabled/unconfigured/unreachable, twice-run redaction (prompt + report) with a planted-secret round-trip test. `llm.enabled: false` is a first-class mode.
- M5 render/deliver · M6 GitHub adapter — not started.

> **Note:** the plan named `litellm`; ci-doctor uses the lighter `openai` SDK instead because litellm can pull `tiktoken`, which downloads vocab at runtime (breaks the air-gap rule). The `LLMClient` port keeps a litellm-backed client a drop-in for hosted multi-provider use.

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
