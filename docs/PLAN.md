# ci-doctor — design rationale

> The *why* behind the architecture. Kept because the reasoning outlives the code:
> it explains decisions that look arbitrary until you know what they defend against.
>
> This was originally a pre-implementation handoff spec. The parts that have since
> become the code's own documentation moved out — how the code is organised and the
> rules a change must follow are in [GUIDELINES.md](../GUIDELINES.md), the user-facing
> reference is the [documentation site](https://fennet82.github.io/ci-doctor).

---

## 1. What this is

A postmortem-stage CI job that runs **only when a pipeline fails**, pulls the logs of the
failed jobs, works out what actually broke, and emits a structured report: to the terminal
(readable inside the CI job log itself), as `report.md` + `report.json` artifacts, and
optionally as a single idempotent note on the MR/PR.

### Non-goals — deliberately not built

- **It does not fix code.** No editing, no committing, no opening MRs.
- **It is not an agentic loop.** No autonomous tool-calling, no repo exploration, no
  multi-turn planning. It is a deterministic pipeline with *one* LLM call (two at most,
  when a repair retry is needed).
- It does not replace the pipeline's own exit status. **It always exits 0** so it can
  never mask or alter the real failure.

The report includes a `handoff_prompt`: a self-contained prompt that can be pasted into a
coding agent to actually perform the fix. That is the intended boundary between this tool
and a fixer.

## 2. The central design principle

**Deterministic code decides *where* the job failed. The LLM only explains *why*.**

The failure mode this defends against: a job fails with `exit code 1` inside the user
script, but the log also contains a large, noisy block from cache restore, artifact
download, or runner preparation. An LLM handed the whole log will confidently blame the
runner or the missing cache, because that text is louder — even though those blocks were
non-fatal warnings. Conversely, when a job never starts at all (no runner assigned) the
log is nearly empty and there is nothing for an LLM to latch onto — but that is exactly
the case the user most needs surfaced.

Therefore: **phase attribution is a pure function of structured metadata and log
structure, computed before any LLM call, and the LLM is never asked to choose.** The LLM
receives a pre-selected, budgeted slice and is told which phase already lost. If the
deterministic classifier is wrong, that is a bug with a failing test — not a
prompt-tuning problem.

## 3. Deployment constraints

Three hard requirements shaped most of the rest.

### 3.1 Self-hosted providers

`base_url` is configuration, never a default; API versions are pinned explicitly. Custom
and internal CA bundles, self-signed certs, and the standard proxy variables are all
honoured. Tokens come from an env var **or a file path**, because secrets arrive as
mounts under k8s and Vault.

**Feature-detect, don't assume:** degrade gracefully on missing fields or endpoints and
say so in the report, rather than crashing against an older instance.

### 3.2 Any LLM provider, including fully local

The first-class path is **any OpenAI-compatible endpoint via `api_base`** — Ollama, vLLM,
llama.cpp, LocalAI, an internal gateway. An API key is optional, because local servers
do not need one.

**`llm.enabled: false` is a supported mode, not a failure state.** The deterministic
report is the product's floor, not a degraded fallback nobody tested.

### 3.3 No internet access at runtime

No runtime downloads of models, rule packs or schemas — which disqualifies any
embedding-based preprocessing that fetches weights on first run. No hardcoded hostnames,
no telemetry, no update checks, no license phone-home.

**This is tested, not asserted:** the suite runs with sockets patched to raise on any
connection except the test doubles.

## 4. The pipeline, stage by stage

```
acquire → segment → attribute → denoise → extract → budget → (LLM) → render / deliver
```

- **Acquire.** Pipeline id, `--job-id`, or a log path. Select failed jobs that are not
  `allow_failure`. A missing or empty log is valid data (`log = None`), not an error —
  it is the "never got a runner" case.
- **Segment.** Parse the provider's own markers into a `Section` tree. Text outside any
  section becomes a synthetic `__preamble__`/`__trailer__`; the trailer is where the
  runner's verdict lives. Unknown sections inherit the nearest enclosing known phase.
- **Attribute.** A pure function `(Job, list[Section]) -> Attribution`, first-match-wins
  over a six-rule ladder, recording which rule fired. **Before the last-resort rule:** a
  section whose only negative evidence is warning-level cannot be blamed. That single
  rule is the deterministic answer to the noisy-logs problem in §2.
- **Denoise.** Strip ANSI, collapse `\r` rewrites (60–80% cut on progress-bar logs),
  dedupe repeats — but never drop a line that matched an error anchor.
- **Extract.** A tail window plus anchored regex windows, config-driven and merged by id.
- **Budget.** Fit the evidence to the model's input limit. Overflow sheds whole
  low-priority windows first, then truncates. **Never silently** — every cut is marked.
- **LLM.** One call, structured output, validated against the `Report` schema. A
  validation failure gets one repair retry, then degrades to the deterministic report.
  It never crashes.
- **Render & deliver.** Terminal, artifacts, and one idempotent MR/PR note that updates
  itself in place via an HTML-comment marker.

## 5. Cost & cascade control

Fully-determined cases (`no_runner`, `missing_dependency`, `cancelled`) skip the LLM
entirely and get a templated report — there is nothing for a model to add. Downstream
jobs that failed only because an upstream one did are reported as consequences, not
causes. Identical matrix failures collapse to one signature.

## 6. Redaction

Runs **twice**: on the prompt before it leaves the process, and on the rendered report
before it is printed or posted. Two passes because they have different failure modes —
a leak to the model and a leak to the MR are not the same incident. Round-trip tested
with planted secrets against every output channel.

## 7. Questions the implementation answered

Left here as a record of what was genuinely open at the start:

- **Which LLM serving stack?** Settled by making it irrelevant — `llm.backend` selects
  openai / litellm / anthropic / claude_code, and none is required.
- **Post the MR note always, or only when confident?** Only at medium or high
  confidence; a low-confidence guess on a merge request is worse than silence.
- **Is `handoff_prompt` the right handoff format?** Kept, as a field in `report.json`
  rather than a separate file — one artifact is easier to plumb than two.
