# ci-doctor — Implementation Plan

> Handoff spec. Target: Python 3.11+. Primary provider: GitLab CI. Must accept a
> GitHub Actions adapter later **without touching core**.

---

## 1. What this is

A postmortem-stage CI job that runs **only when a pipeline fails**, pulls the logs of the
failed jobs, works out what actually broke, and emits a structured report:

- rendered to the terminal (readable inside the CI job log itself),
- written as `report.md` + `report.json` artifacts,
- optionally posted as a single idempotent note on the MR.

### Non-goals (do not build these)

- **It does not fix code.** No editing, no committing, no opening MRs.
- **It is not an agentic loop.** No autonomous tool-calling, no repo exploration,
  no multi-turn planning. It is a deterministic pipeline with *one* LLM call
  (two at most, when a repair retry is needed).
- It does not replace the pipeline's own exit status. **It always exits 0** so it
  can never mask or alter the real failure.

The report includes a `handoff_prompt` field: a self-contained prompt that can be
pasted into a coding agent to actually perform the fix. That is the intended
boundary between this tool and a fixer.

---

## 2. The central design principle

**Deterministic code decides *where* the job failed. The LLM only explains *why*.**

Failure mode this defends against: a job fails with `exit code 1` inside the user
script, but the log also contains a large, noisy block from cache restore /
artifact download / runner preparation. An LLM handed the whole log will
confidently blame the runner or the missing cache, because that text is louder —
even though those blocks were non-fatal warnings. Conversely, when a job never
starts at all (no runner assigned), the log is nearly empty and there is nothing
for an LLM to latch onto — but that is exactly the case the user most needs
surfaced.

Therefore: **phase attribution is a pure function of structured metadata and log
structure, computed before any LLM call, and the LLM is never asked to choose.**
The LLM receives a pre-selected, budgeted slice and is told which phase already
lost. If the deterministic classifier is wrong, that's a bug with a failing test —
not a prompt-tuning problem.

---

## 3. Deployment constraints: self-hosted, BYO-LLM, air-gapped

Hard requirements.

### 3.1 Self-hosted GitLab
- `base_url` is configuration, never a default. Pin the API version explicitly.
- Support custom/internal CA bundles and self-signed certs (`ca_bundle`,
  `verify_ssl`), and honour `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` and
  `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`.
- Tokens from env **or from a file path** (`token_file`).
- **Feature-detect, don't assume.** Degrade gracefully on missing fields/endpoints
  and note it in the report. Log the detected GitLab version at startup.
- Generous, configurable timeouts.

### 3.2 Any LLM provider, including fully local
- `litellm`, but the first-class path is **any OpenAI-compatible endpoint via
  `api_base`** (Ollama, vLLM, llama.cpp, LocalAI, internal gateway).
- API key optional. LLM endpoint gets its own CA bundle / proxy settings.
- **`llm.enabled: false` is a supported mode**, not a failure state: deterministic
  report from the classifier. This is the M2 output and the graceful fallback.

### 3.3 No internet access at runtime
- **No runtime downloads** (models, rule packs, schemas, deps). Embedding-based
  preprocessing that pulls weights on first run is disqualified.
- **No hardcoded hostnames.** `gitlab.com` never appears in source.
- **No telemetry, analytics, update checks, or license phone-home.**
- `detect-secrets` optional plugins must not fetch; default to the local regex set.
- **Ship a self-contained Docker image**; offline wheel path too
  (`pip install --no-index --find-links ./wheels ci-doctorr`).
- **Test for it:** run the suite with sockets patched to raise on any connection
  except the test doubles.

---

## 4. Provider-neutral domain model

See `ci_doctor/core/models.py` for the implemented model (`Phase`, `FailureReason`,
`LogLine`, `RunnerInfo`, `MergeRequestRef`, `Section`, `Job`, `Run`) and
`ci_doctor/core/ports.py` for the ports (`CIProvider`, `LogSegmenter`, `LLMClient`,
`Renderer`). The `Report` output contract is `ci_doctor/llm/schema.py`.

**Guardrail: if `grep -ri gitlab core/` returns anything, the abstraction broke.**

---

## 5. Package layout (fills in per milestone; do not pre-stub)

```
ci_doctor/
  cli.py
  config/{schema.py, defaults.yml, loader.py}
  core/{ports.py, models.py, attribution.py, denoise.py, extract.py,
        budget.py, cluster.py, redact.py, analyze.py}
  llm/{client.py, schema.py, prompts/analyze.{system,user}.j2}
  render/{terminal.py, markdown.py, json_out.py}
  providers/gitlab/{provider.py, segmenter.py, reasons.py}
  providers/github/   # M6
  tests/fixtures/{logs/<provider>/, expected/}
```

---

## 6. The pipeline, stage by stage

- **S1 Acquire.** Pipeline id (`$CI_PIPELINE_ID`), `--job-id`, or `--from-file`.
  Select `status == "failed"` and not `allow_failure` (`include_allowed_failures:
  false`). Missing/empty log is valid data (`log = None`), not an error.
- **S2 Segment.** Parse GitLab `section_start`/`section_end` markers (with
  `[collapsed=true]` and nesting) into the `Section` tree. Outside-section text ->
  synthetic `__preamble__`/`__trailer__`; the trailer carries `ERROR: Job failed:`.
  Section->phase map in defaults.yml (`phases:`), user-overridable; unknown sections
  inherit nearest enclosing known phase, else `script`.
- **S3 Attribute (the classifier).** `core/attribution.py`, pure function
  `(Job, list[Section]) -> Attribution` where
  `Attribution = {phase, reason, confidence, terminal_evidence, rule_id}`.
  Precedence ladder (first match wins, records `rule_id`):
  1. No/empty log -> `PROVISION` (skip LLM entirely).
  2. Provider `failure_reason` authoritative for everything except `script_failure`.
  3. `script_failure` -> phase is `SCRIPT`, full stop; locate the terminal command.
  4. Unclosed section = where execution died.
  5. Trailer parse (`exit code N`, system failure, timeout).
  6. Fallback: last section with an `ERROR`-severity line.
  **Hard rule before rule 6:** `WARNING:`-prefixed runner lines are non-fatal; a
  section whose only negative evidence is `WARNING:`-level cannot be blamed. This
  is the deterministic answer to the noisy-logs problem. Also emit
  `secondary_phases` (context, marked non-causal).
- **S4 Denoise.** Strip ANSI SGR; collapse `\r` rewrites (60-80% cut on noisy
  logs); strip `FF_TIMESTAMPS` prefixes; dedup repeats -> `<line>  (×47)`; drop
  `noise_patterns` but never a line that matched an error anchor.
- **S5 Extract.** Tail window (`tail_lines`, default 120) + anchored regex windows
  (`before`/`after`, overlapping merge). Config-driven matchers with priorities;
  ship a starter set (generic, pytest, jest, go test, maven/gradle, tsc, npm,
  docker, OOM `137`).
- **S6 Budget & compress.** `max_input_tokens` (~12k). Blamed phase 70%, secondary
  headers+summaries ~10%, metadata/needs/MR/changed-files ~20%. Overflow: drop
  low-priority windows, shrink context, then truncate the middle with an explicit
  `… [N lines elided] …`. **Never silently truncate.**
- **S7 LLM.** One call, structured output (JSON-schema -> tool-calling ->
  prompt-and-parse), via litellm. Prompt states the phase is already determined;
  do not dispute. Contract = `llm/schema.py` `Report`. Validation failure -> one
  repair retry -> else degraded deterministic report. Never crash.
- **S8 Render & deliver.** `rich` terminal (respect `NO_COLOR`/non-TTY/`--no-color`),
  wrap own output in GitLab section markers, `report.md`+`report.json` artifacts,
  optional **idempotent** MR note (HTML-comment marker, update in place).

---

## 7. Cost & cascade control
- Skip LLM for fully-determined cases (`PROVISION`/`no_runner`/`missing_dependency`/
  `cancelled`) -> templated report.
- Cascade detection (`core/cluster.py`): DAG from `needs`+stage order; analyze the
  upstream cause, report downstream as consequence.
- Signature clustering: fingerprint `(failure_reason, normalized_terminal_error)`;
  collapse identical matrix failures.
- `max_jobs_analyzed` cap (default 3). Idempotency cache keyed on
  `sha256(job_id + log)`. `--dry-run` prints the prompt + token count.

## 8. Redaction
Runs twice: on the prompt before it leaves the process, and on the rendered report
before printed/posted. Config regex set + GitLab masked-variable literals + optional
`detect-secrets` behind a flag. Replacement `[REDACTED:<kind>]`. Round-trip test
with planted secrets -> zero leaks in prompt/stdout/markdown/json.

## 9. Configuration
Layered: `defaults.yml` -> repo `.ci-doctor.yml` -> `CI_DOCTOR_*` env -> CLI flags.
Pydantic-validated; unknown keys are an error. (Implemented in
`ci_doctor/config/`.) Env config vars nest with `__`; single-underscore secret
vars like `CI_DOCTOR_GITLAB_TOKEN` are referenced by name and ignored by the loader.

## 10. Proving the abstraction: GitHub adapter (M6)
~200-line drop-in: `##[group]`/`##[endgroup]` + per-step logs/conclusions;
phase map for checkout/cache/setup/run/upload; failure reasons from step
conclusions + annotations + `startup_failure`/cancellation. **If it needs editing
anything in `core/`, the design failed.**

## 11. Testing (non-negotiable)
`tests/fixtures/logs/<provider>/*.log` paired with a provider-neutral
`expected/*.json` of `{phase, reason, rule_id}` (see GUIDELINES.md §4.1).
Must include: noisy cache-miss block above a script `exit 1` ->
`SCRIPT` (the regression test); empty log + `stuck_or_timeout_failure` ->
`PROVISION`; runner system failure in `prepare_executor`; job timeout with unclosed
`step_script`; `missing_dependency_failure` cascade; OOM `137`; 50k lines of docker
progress -> >70% denoise cut; planted-secrets round-trip. Renderer snapshots.
LLM tests use recorded responses. Golden-file `--from-file` replay. **No test
requires network or an LLM.**

## 12. Milestones
- **M0** Skeleton: CLI, config loader, domain model, ports, `--from-file`. No net, no LLM. ✅
- **M1** GitLab acquisition (python-gitlab; self-hosted: base_url/CA/proxy/token_file/version detect).
- **M2** Segmenter + classifier; full fixture suite green. Useful with no LLM.
- **M3** Denoise + extract + budget; >70% cut with 100% anchor retention.
- **M4** LLM + structured output + redaction; smoke against local OpenAI-compatible endpoint first.
- **M5** Render + deliver; `.gitlab-ci.yml` snippet, Docker image, offline wheel bundle, no-network test.
- **M6** GitHub adapter (also the audit that core stayed clean).

## 13. Guardrails for the implementing agent
1. The LLM never selects the failure phase.
2. No GitLab identifiers in `core/`.
3. Always `exit 0`, catch at the top level.
4. Nothing reaches the public internet (no hardcoded host, telemetry, update
   checks, or runtime model/rule/schema downloads).
5. Never emit unredacted log content anywhere.
6. Every truncation is visible in the output.
7. `attribution.py` stays a pure function — no I/O, network, or clock.
8. Prompts live in template files, not inline strings.
9. Prefer a config knob over a hardcoded pattern; prefer a fixture over a manual test.

## 14. Open questions to resolve before M4
- Which GitLab version is the self-managed instance on?
- Which local model/serving stack — Ollama, vLLM, or an internal gateway?
- Internal container registry as distribution channel, or offline wheel bundle too?
- Post the MR note always, or only when `confidence >= medium`?
- Is `handoff_prompt` the right handoff format, or emit a file the fixer reads?
