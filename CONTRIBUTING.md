# Contributing to ci-doctor

How to work in this repo — for humans and for agents. Read this before writing code.

The design spec is [docs/PLAN.md](docs/PLAN.md); the user-facing overview is the
[README](README.md). This file is the *working* guide: where things go, how to test
them, and what must never break.

---

## 1. The one-paragraph model

ci-doctor is a **read-only postmortem**. It reads a failed CI job's log and explains
why it failed. A deterministic classifier decides **where** the job failed; the LLM
only explains **why**, inside the phase already chosen. Everything is optional and
degrades: no LLM, no network, no problem — you still get a full report.

```
acquire → segment → attribute → denoise → extract → budget → (LLM) → render / deliver
```

## 2. Where code goes

| You are adding | It belongs in | Rule |
|---|---|---|
| A CI system (Jenkins, Bitbucket…) | `providers/<name>/` | Implements ports. **Never** touch `core/`. |
| Log-format parsing | `providers/<name>/segmenter.py` | Emits the canonical `Section` tree. |
| Provider failure-reason mapping | `providers/<name>/reasons.py` | Maps vendor strings → `FailureReason`. |
| Blame/classification logic | `core/attribution.py` | Pure function. No I/O, network, or clock. |
| Evidence selection | `core/{denoise,extract,budget}.py` | Config-driven where possible. |
| An LLM backend | `llm/backends.py` | Lazy-import the SDK inside the client. |
| Output format | `render/` | Implements the `Renderer` port. |
| A matcher / language pack | `config/defaults.yml` | **Data, not code.** See §5.1. |
| A tunable value | `config/schema.py` (scalar) or `defaults.yml` (data) | Prefer a config knob over a hardcoded pattern. |

`core/` imports only `core.ports` abstractions. Adapters implement them.

## 3. Invariants — do not break these

These come from [docs/PLAN.md §13](docs/PLAN.md). The first three are load-bearing:

1. **The LLM never selects the failure phase.** Attribution is deterministic and auditable.
2. **No provider identifiers in `core/`.** Verify: `grep -ri gitlab ci_doctor/core/` must be empty.
3. **Always `exit 0`.** The analyzer must never change a pipeline's outcome — `main()`
   catches `BaseException` and exits 0. Never add a code path that can exit non-zero.
4. **Nothing reaches the public internet** by default — no telemetry, update checks, or
   runtime downloads of models/rules/schemas.
5. **Never emit unredacted log content.** Redaction runs on the prompt *and* the report.
6. **Every truncation is visible** (`… [N lines elided] …`), never a silent cut.
7. `attribution.py` stays a pure function.
8. Prompts live in `llm/prompts/*.txt`, not inline strings.
9. Prefer a fixture over a manual test; prefer a config knob over a hardcoded pattern.

## 4. Tests

```sh
uv sync
uv run pytest          # must be green before you push
```

**Rules**

- **No network, ever.** `tests/conftest.py` blocks real sockets; a test that opens one
  fails by design. That guarantee is the product's air-gap promise — don't weaken it.
- **Deterministic by default.** Tests run the no-LLM path. If `config/defaults.yml`
  enables an `llm.backend`, the suite will shell out to a real model and slow to
  minutes — keep the shipped `llm:` block commented out.
- **Fixtures over hand-built strings** for anything log-shaped.
- Assert *behaviour*, not incidental formatting.

### 4.1 Fixture layout

```
tests/fixtures/
  logs/<provider>/<case>.log    provider-specific raw job logs
  expected/<case>.json          provider-NEUTRAL attribution verdicts
```

Logs are provider-scoped because every CI system frames a job differently. Verdicts
are **not**: attribution lives in provider-neutral `core/`, so the same scenario must
classify identically whoever produced the log. One `expected/oom_137.json` therefore
covers every provider that ships an `oom_137.log`.

### 4.2 Never hardcode a provider path

Use [`tests/support.py`](tests/support.py):

```python
from tests import support

@pytest.mark.parametrize("provider", support.providers_with("script_failure_noisy"))
def test_something(provider):
    log = support.read_log(provider, "script_failure_noisy")
    sections = support.segment(provider, log)
```

Adding a provider is then **two steps and zero test edits**: drop
`fixtures/logs/<provider>/` and register its segmenter in `support.SEGMENTERS`.
Every provider-generic test picks it up. `test_every_provider_dir_has_a_segmenter`
fails loudly if the directory and the registry drift.

### 4.3 Which test file

| Testing | File |
|---|---|
| Attribution verdict for a scenario | add `logs/<provider>/<case>.log` + `expected/<case>.json` — no code |
| A matcher/language pack | `test_matcher_packs.py` (add a fixture + one row) |
| Log-format parsing | `test_segmenter.py` |
| Config layering / validation | `test_config.py` |
| Report shape, category, LLM fallback | `test_report.py` |
| Terminal/markdown output | `test_render.py` |
| End-to-end offline run | `test_offline_pipeline.py` |

## 5. Cookbook

### 5.1 Add a matcher / language pack

Pure data — no Python. In `config/defaults.yml` under `extraction.matchers`:

```yaml
- id: mytool
  pattern: '^MYTOOL ERROR'   # or start:/end: for a block
  before: 2
  after: 10
  priority: 85
```

Then **add a fixture and a row in `test_matcher_packs.py`** — a matcher that never
matches fails silently, because the tail window still returns something.

Windows are `before`/`after` lines around the anchor. Anchor on the **cause**, not on a
trailing summary line: a summary-only anchor windows *past* the detail above it.

Config **lists replace, mappings deep-merge**. A repo defining `extraction.matchers`
drops every shipped pack — so packs belong in `defaults.yml`, and user docs must say so.

### 5.2 Add a deterministic category signature

`_CATEGORY_SIGNATURES` in `llm/report.py` is an **ordered, first-match-wins** list.
Order is the whole game:

- `infrastructure` / `timeout` / `permissions` first — an OOM'd build is infra, not build.
- Narrow, unambiguous build markers before `test` — the broad `\bFAILED\b` test
  signature would otherwise claim `Build FAILED.`.
- `runtime` **last** — a traceback also appears in a pytest failure (`test`) and a
  missing-import crash (`dependency`), and both are more actionable answers.

Any new signature that could collide gets a case in
`test_runtime_never_outranks_a_more_actionable_category`.

### 5.3 Add a CI provider

1. `providers/<name>/{provider,segmenter,reasons}.py` implementing `CIProvider` /
   `LogSegmenter`.
2. Canonicalise section names to the tokens the `phases:` map already uses
   (`checkout`, `run`, `post`, …) so phase assignment needs no core change.
3. Register in `cli._make_provider` / `cli._make_segmenter`.
4. Add `tests/fixtures/logs/<name>/` and register the segmenter in `support.SEGMENTERS`.
5. Confirm `grep -ri <name> ci_doctor/core/` is empty. If it isn't, the abstraction broke.

## 6. Style

- **Match the surrounding code.** Comment density, naming, and idiom are already
  consistent — follow them.
- Module docstrings explain **why**, not what. Keep that; it is the repo's main
  onboarding surface.
- Comment the non-obvious decision, not the obvious line. Rationale and known
  ceilings earn a comment; restating the code does not.
- Mark a deliberate shortcut with a `ponytail:` comment naming the ceiling and the
  upgrade path, e.g. `# ponytail: ~4 chars/token heuristic; swap for a real tokenizer`.
- No unrequested abstractions: no interface with one implementation, no config for a
  value that never changes.
- `# noqa: <CODE>` needs a trailing reason (see existing `BLE001` uses).

## 7. Commits

**[Conventional Commits](https://www.conventionalcommits.org/)** — the release
pipeline parses them, so the format is functional, not cosmetic.

```
<type>(<optional scope>): <imperative summary>
```

| Type | Effect |
|---|---|
| `feat:` | **minor** version bump + release |
| `fix:` | **patch** version bump + release |
| `BREAKING CHANGE:` in body (or `feat!:`) | **major** bump |
| `docs:` `test:` `chore:` `refactor:` `perf:` `ci:` | no release |

`.github/workflows/release.yml` runs on every push to `master`: python-semantic-release
bumps the version, updates `CHANGELOG.md`, tags, builds, and publishes to PyPI. **A
`feat:` or `fix:` on master ships a release.** If nothing releasable is found, it no-ops.

Keep the summary imperative and scoped to one change. Don't hand-edit `CHANGELOG.md`
or the version in `pyproject.toml` — the pipeline owns both.

## 8. Before you push

```sh
uv run pytest                      # all green, no exceptions
grep -ri gitlab ci_doctor/core/    # must be empty (guardrail #2)
grep -ri github ci_doctor/core/    # must be empty
```

- Update the test count in `README.md` if it changed.
- New behaviour has a test. New matcher has a fixture.
- Never commit `report.md` / `report.json` (local run artifacts).
- Branch off `master`; don't push directly to it unless you intend to trigger a release.

## 9. Gotchas that have already bitten us

Real bugs, kept here so they don't recur:

- **`rich` crops, it doesn't wrap, when `Console(soft_wrap=True)`.** Content inside a
  `Panel` gets cut at the border with no continuation. Pass `soft_wrap=False` on that
  specific `print` and build the `Text` with `overflow="fold"`.
- **`RichHandler(highlighter=None)` does not disable highlighting** — rich does
  `highlighter or ReprHighlighter()`, so `None` falls back to the default. Pass
  `NullHighlighter()`.
- **A `Panel` `subtitle=` is drawn inside the bottom border** and is cropped to the box
  width. Long text belongs in the body, not the border.
- **Category order is load-bearing.** See §5.2. Adding a signature at the wrong
  position silently mislabels whole ecosystems.
- **A matcher that matches nothing fails silently** — the tail window still returns
  output, so the test looks fine. Assert on `_windows_for(...)` being non-empty.
- **Uncommenting the `llm:` block in `defaults.yml` makes the test suite call a real
  model**, taking it from ~2s to ~2min, and breaks
  `test_backends.py::test_backend_ready_rules` (it asserts a backend is *not* ready
  without a model, which the shipped default then supplies).
