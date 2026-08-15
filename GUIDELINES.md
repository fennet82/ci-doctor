# ci-doctor engineering guidelines

How the code is built: where things go, what must never break, and the conventions
every change follows. **Read this before writing code** — for humans and for agents.

For the contribution *process* (setup, running tests, commits, pushing) see
[CONTRIBUTING.md](CONTRIBUTING.md). For *why* the architecture is shaped this way —
the failure modes it defends against — see [docs/PLAN.md](docs/PLAN.md).

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

```
ci_doctor/
  cli.py            typer entrypoint (always exits 0)
  config/           pydantic schema + defaults.yml + layered loader
  core/             provider-neutral: models, ports, attribution (pure), denoise,
                    extract, budget, redact, analyze
  llm/              Report schema, prompt templates, backends
  render/           terminal (rich), markdown
  providers/
    gitlab/         python-gitlab adapter + segmenter + reasons
    github/         PyGithub adapter + segmenter + reasons
tests/              fixtures + golden-file attribution suite
docs/site/          Astro documentation site — see docs/site/GUIDELINES.md before editing
examples/           .gitlab-ci.yml + GitHub Actions workflows
```

| You are adding | It belongs in | Rule |
|---|---|---|
| A CI system (Jenkins, Bitbucket…) | `providers/<name>/` | Implements ports. **Never** touch `core/`. |
| Log-format parsing | `providers/<name>/segmenter.py` | Emits the canonical `Section` tree. |
| Provider failure-reason mapping | `providers/<name>/reasons.py` | Maps vendor strings → `FailureReason`. |
| Something every provider needs | `providers/<shared>.py` | Provider-neutral, but does I/O, so not `core/` (see `git_origin.py`). |
| Blame/classification logic | `core/attribution.py` | Pure function. No I/O, network, or clock. |
| Evidence selection | `core/{denoise,extract,budget}.py` | Config-driven where possible. |
| An LLM backend | `llm/backends.py` | Lazy-import the SDK inside the client. |
| Output format | `render/` | Implements the `Renderer` port. |
| A matcher / language pack | `config/defaults.yml` | **Data, not code.** See §5.1. |
| A tunable value | `config/schema.py` (scalar) or `defaults.yml` (data) | Prefer a config knob over a hardcoded pattern. |

`core/` imports only `core.ports` abstractions. Adapters implement them.

## 3. Invariants — do not break these

This list is the single source of truth for them, and code comments cite it **by
number** — renumber an entry and you invalidate every reference to it, so new
invariants go on the end. The first three, and #10, are load-bearing:

1. **The LLM never selects the failure phase.** Attribution is deterministic and auditable.
2. **No provider identifiers in `core/` code** — no import, identifier or string
   literal. Pinned by `test_core_carries_no_vendor_name_in_its_code`, which strips
   comments and docstrings first: prose naming the vendors a port deliberately
   spans is the invariant being honoured, not broken.
3. **Always `exit 0`.** The analyzer must never change a pipeline's outcome — `main()`
   catches `BaseException` and exits 0. Never add a code path that can exit non-zero.
4. **Nothing reaches the public internet** by default — no telemetry, update checks, or
   runtime downloads of models/rules/schemas.
5. **Never emit unredacted log content.** Redaction runs on the prompt *and* the report.
6. **Every truncation is visible** (`… [N lines elided] …`), never a silent cut.
7. `attribution.py` stays a pure function.
8. Prompts live in `llm/prompts/*.txt`, not inline strings.
9. Prefer a fixture over a manual test; prefer a config knob over a hardcoded pattern.
10. **Read-only.** Every provider method is a read. The single exception in the whole
    tool is `SCMProvider.post_note`, which writes to a discussion thread. Nothing
    retries, cancels, restarts, pushes or merges anything — see `core/ports.py`.

## 4. Writing tests

Running them is in [CONTRIBUTING.md §2](CONTRIBUTING.md). What they must look like:

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

Three tests keep that grid square, and all three are load-bearing:

| Test | Catches |
|---|---|
| `test_every_expected_verdict_has_at_least_one_log` | a verdict asserting nothing |
| `test_every_log_has_an_expected_verdict` | a log nothing asserts on — easy to hit, since a language pack looks covered with only a `test_matcher_packs.py` row |
| `test_every_case_is_covered_by_every_provider` | a scenario proved against one log format only |

So a new case means **one log per provider plus one verdict**, and a new provider
means a full set of logs. That is the point: the same scenario, framed every way a
runner can frame it, must reach the same verdict.

When writing another provider's twin, re-frame the log — do not translate it. The
tool output (the rust panic, the phpunit diff) is provider-neutral and belongs in
verbatim; the runner's own lines are not, and a GitLab runner line inside a GitHub
log is a fixture asserting something that can never happen.

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

Then **add the fixture and a row in `test_matcher_packs.py`**, or the pack proves
nothing — a matcher that never matches fails silently, because the tail window still
returns something. A fixture is three files, and two tests enforce that:

- `tests/fixtures/logs/gitlab/<case>.log` and `logs/github/<case>.log` — both, always
  (`test_every_case_is_covered_by_every_provider`).
- `tests/fixtures/expected/<case>.json` — one provider-neutral verdict shared by both
  (`test_every_log_has_an_expected_verdict`).

`test_every_shipped_pack_is_covered_by_a_case` then fails if a pack has no `CASES` row.

Anchor on the **cause**, not on a trailing summary line: a summary-only anchor windows
*past* the detail above it. And anchor on something only *your tool* prints — see the
two runner/wrong-tool traps in §7 before choosing a regex.

Windows are `before`/`after` lines around the anchor.

`priority` decides who gets cut when the evidence exceeds the token budget: `extract.py`
sheds whole low-priority windows before `budget.py` truncates what is left. Rank a pack
by how *diagnostic* it is, not how loud — `npm ERR!` (75) trails the compiler errors that
caused it, so it must lose to `tsc` (80). Only windows separated by unselected lines are
rankable; adjacent ones merge and take the highest priority among them.

Config **lists replace, mappings deep-merge** — except lists whose entries all carry an
`id`, which merge per id (`_merge_by_id` in `config/loader.py`). So a user pack with a new
id is *added* to the shipped ones, and one reusing a shipped id *overrides* that pack and
logs a warning naming it. The override is field by field: `priority: 95` on the shipped
`pytest` pack keeps its `start`/`end`, because blanking them would leave a matcher that
can never fire. A user `pattern` still wins over an inherited `start`/`end` — `extract.py`
checks `pattern` first.

Every field you add to `config/schema.py` needs a `description=` — it becomes the text in
the published `ci-doctor.schema.json`, and `test_json_schema_documents_every_field` fails
without it.

The docs site's matcher catalogue is **generated** from `defaults.yml`, including the
`# --- Group ---` comment headers that organise it. After adding or retuning a pack, run
`mise run docs:data`; `test_docs_data_is_current` fails if the committed JSON drifts.

### 5.2 Where a pattern goes: the classifier or the catalogue

There are two places a "this looks like a failure" regex could land, and they answer
different questions at different stages:

| | `_ERROR_RE` in `core/attribution.py` | `extraction.matchers` in `defaults.yml` |
|---|---|---|
| Question | did anything in this **section** fail? | which **lines** are the evidence? |
| Output | one boolean per line | a windowed excerpt, prioritised |
| Runs | before extraction, and only on rule 6 (last resort) | after the phase is already decided |
| Tunable | no — `attribute()` takes no `Config`, by design (invariant #7) | yes, per repo, merged by id |

So the classifier's list stays **runner-level**: the runner's own error annotations
(`##[error]`, `ERROR:`, `FATAL`) and a non-zero exit. A `pytest`/`npm`/`go` signature
added there would be a second copy of the catalogue, hardcoded where no
`.ci-doctor.yml` can reach it — and it buys nothing, because a tool that fails at all
makes its runner say so. `test_the_classifier_does_not_carry_a_second_matcher_catalogue`
pins that.

New language pack → `defaults.yml`, always. Touch `_ERROR_RE` only when a **runner**
has a failure marker we cannot see.

### 5.3 Add a deterministic category signature

`_CATEGORY_SIGNATURES` in `llm/report.py` is an **ordered, first-match-wins** list.
Order is the whole game:

- `infrastructure` / `timeout` / `permissions` first — an OOM'd build is infra, not build.
- Narrow, unambiguous build markers before `test` — the broad `\bFAILED\b` test
  signature would otherwise claim `Build FAILED.`.
- `runtime` **last** — a traceback also appears in a pytest failure (`test`) and a
  missing-import crash (`dependency`), and both are more actionable answers.

Any new signature that could collide gets a case in
`test_runtime_never_outranks_a_more_actionable_category`.

### 5.4 Add a provider

**Two ports, chosen independently.** `CIProvider` reads the failed run; `SCMProvider`
posts the note. Config picks them separately (`ci:` and `scm:`) because the real world
mixes them — Jenkins builds GitLab repos, Woodpecker builds Forgejo ones. A vendor that
is both (GitLab, GitHub) implements both ports on **one class over one client**: their
pipelines and merge requests are the same API, and connecting twice would mean a second
token read and a second version probe for nothing.

1. `providers/<name>/{provider,segmenter,reasons}.py` implementing `CIProvider`,
   `SCMProvider`, or both, plus `LogSegmenter` for a CI.
2. Canonicalise section names to the tokens the `phases:` map already uses
   (`checkout`, `run`, `post`, …) so phase assignment needs no core change.
3. Register in `cli._adapter`. `_make_ci_provider` / `_make_scm_provider` sort out
   which ports it implements; a CI with no git host simply posts no note.
4. Segmenter selection keys on `ci`, never on `scm` — log framing is whatever the
   *runner* printed, which is why one segmenter can serve several CI systems
   (Forgejo and Gitea Actions both run act_runner and emit GitHub's `##[group]`).
5. Add `tests/fixtures/logs/<name>/` and register the segmenter in `support.SEGMENTERS`.
   Fixtures are keyed on log format, so a git-host-only adapter owes none.
6. `test_core_carries_no_vendor_name_in_its_code` must stay green — add the new
   vendor to its regex. If it fires, the abstraction broke.

**Prefer the vendor's own SDK** over hand-rolled REST calls — `python-gitlab` and
`PyGithub` are already dependencies, and pagination, retries and auth are not worth
re-implementing. Keep the SDK's objects inside the adapter: `_to_job` is the only place
allowed to know what they look like.

### 5.5 Resolve the repository outside CI

ci-doctor runs on a laptop too, where a CI system's predefined variables do not exist.
Adapters resolve the repository as *env var first, then* `providers/git_origin.py`,
which reads the `origin` remote and **warns** that it guessed. Any new provider should
do the same rather than hard-failing on a missing variable.

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

## 7. Gotchas that have already bitten us

Real bugs, kept here so they don't recur:

- **`rich` crops, it doesn't wrap, when `Console(soft_wrap=True)`.** Content inside a
  `Panel` gets cut at the border with no continuation. Pass `soft_wrap=False` on that
  specific `print` and build the `Text` with `overflow="fold"`.
- **`RichHandler(highlighter=None)` does not disable highlighting** — rich does
  `highlighter or ReprHighlighter()`, so `None` falls back to the default. Pass
  `NullHighlighter()`.
- **A `Panel` `subtitle=` is drawn inside the bottom border** and is cropped to the box
  width. Long text belongs in the body, not the border.
- **Category order is load-bearing.** See §5.3. Adding a signature at the wrong
  position silently mislabels whole ecosystems.
- **A matcher that matches nothing fails silently** — the tail window still returns
  output, so the test looks fine. Assert on `_windows_for(...)` being non-empty.
- **The report is the last place evidence can be lost, and it was losing it.**
  `deterministic_report` re-trimmed `bundle.blamed_lines` to a hardcoded `[-15:]`,
  throwing away the selection that denoise + matcher priority + `budget.fit` had just
  made — silently, with `bundle.truncated` still `False`. On a two-error rust build it
  kept E0599 and decapitated E0308, the error that *caused* it. Never re-cut the bundle
  by a fixed count; it is already budgeted. If a display cap is genuinely needed, it
  announces itself (`_capped`), like every other cut in the pipeline.
- **A matcher that matches the *runner* is worse than one that matches nothing.** Every
  failed job ends with `ERROR: Job failed: exit code N` (GitLab) or
  `##[error]Process completed with exit code N.` (GitHub), so a pack anchored on a bare
  `^ERROR: .*failed` looks covered by every fixture in the suite while proving nothing.
  `bazel` shipped that way and opened a priority-85 window on 40 of 41 logs.
  `test_no_pack_fires_on_the_runners_own_trailer` pins it; only `generic_error` (the
  fallback) and `oom` (the trailer's `exit code 137` is the *only* OOM signal GitLab
  gives) are exempt.
- **A pack can also be "covered" by the wrong tool.** `jest`'s `^FAIL ` matched
  `go test`'s `FAIL example.com/pkg 0.038s`, and `maven_gradle` only ever fired on
  gradle's half of its own pattern — both looked green for months. When adding a pack,
  check what it matches across *every* fixture, not just its own.
- **Uncommenting the `llm:` block in `defaults.yml` makes the test suite call a real
  model**, taking it from ~2s to ~2min, and breaks
  `test_backends.py::test_backend_ready_rules` (it asserts a backend is *not* ready
  without a model, which the shipped default then supplies).
- **A GitHub `##[group]` wraps a step's *header*, not the step.** The `with:`/`env:`/
  `shell:` block is inside the group; everything the step actually printed comes after
  `##[endgroup]`. Reading a group as "the step" therefore collects the step's inputs and
  drops its output — the evidence we exist to find — and it looks fine until you segment
  a real job log. A step stays current past its `##[endgroup]` and ends when the runner
  says so (`Process completed with exit code N`), which is also what leaves a section
  *open* for a cancelled or timed-out job.
- **A runner's warning is not a tool printing "warning".** GitLab's runner prefixes its
  own lines with `WARNING:`; GitHub uses the `##[warning]` annotation. `_is_warning_line`
  matches those two **case-sensitively** on purpose: `Warning:` and `warning:` are what
  apt, pip and docker print from *inside* a step, and excusing those would let a real
  failure go unblamed. It is also warnings only, not the whole `##[...]` syntax — the
  predicate also decides which phases are reported as contributing factors, so admitting
  `##[notice]`/`##[debug]` would surface a debug line to the user as a warning. Adding a
  provider whose form is missing silently re-opens the "blamed the cache" bug, and
  fixture ordering hides it whenever the noisy section comes first — pin it with a unit
  case, not a log.
- **A `^`-anchored matcher does not see the raw file.** Segmentation strips the
  provider's framing (GitHub prefixes every line with an ISO timestamp) before
  `extract()` runs, so test matchers against `support.log_lines(...)`, never
  `read_log(...).splitlines()`.
- **The domain models are plain dataclasses, not pydantic** — nothing validates what an
  adapter puts in them. A `datetime` where the model says `str | None` sails through and
  crashes much later, in the JSON dump. Normalise at the adapter boundary.

## 8. Releases

Versions are **[PEP 440](https://peps.python.org/pep-0440/)** — Python packaging's own
spec, and the one PyPI normalises against. Not SemVer: `0.0.1a2`, never `0.0.1-a.2`.

`uv version` is the only thing that writes a version. It updates `pyproject.toml` and
re-locks `uv.lock` in one step, so the two can never disagree. Never hand-edit either,
and never create a release tag by hand — the pipeline owns both.

### 8.1 Today — master ships alphas, the base version stays put

Every push to `master` that carries a `feat:`, `fix:` or `BREAKING CHANGE:` commit
publishes the next alpha. Only the alpha counter moves:

```
0.0.1a1 → 0.0.1a2 → 0.0.1a3 → …
```

`.github/workflows/release.yml` does the whole thing: `uv version --bump alpha`, a
`chore(release): <version> [skip ci]` commit, the `v<version>` tag, `uv build`, the image
pushed to Docker Hub, a GitHub Release with the image tarball, the config JSON Schema and
the SBOM attached, then PyPI. A push with only `docs:`/`chore:`/`refactor:` commits ships
nothing and rides along in the next alpha. The full pipeline map is
[docs/ci-cd.md](docs/ci-cd.md).

Note what is *missing*: nothing decides how big the next version is. A commit message
cannot move the base version, only the counter. That is deliberate — while the shape of
the tool is still moving, `0.0.1aN` means exactly one thing ("the Nth alpha"), and
promoting it is a decision a person makes, not a side effect of typing `feat:`.

Two consequences worth knowing:

- **`pip install ci-doctorr` works anyway.** pip installs a pre-release when a project
  has no stable release. Once a final version exists, alphas need `--pre`.
- **The GitHub Releases are not flagged as pre-releases.** `/releases/latest` resolves to
  the newest *non*-prerelease, and the config schema `$id` points through it
  (`releases/latest/download/ci-doctor.schema.json`). Flagging them would break every
  editor resolving that URL.

### 8.2 Next — release candidates on master, the final release by hand

Not built yet. Written down so the alpha flow above doesn't have to be unpicked to get
here. Two pipelines:

1. **`master`, automatic.** Bumps the base version and publishes a **release
   candidate** — never a final version.
2. **release, manual** (`workflow_dispatch`). Drops the pre-release suffix, tags the
   version itself, publishes it. This is the approval gate.

One cycle, end to end:

| When | Command | Version |
|---|---|---|
| First feature after a release | `uv version --bump minor --bump rc` | `0.1.0` → `0.2.0rc1` |
| Every push after that | `uv version --bump rc` | `0.2.0rc1` → `0.2.0rc2` → `rc3` |
| Approved — manual run | `uv version --bump stable` | `0.2.0rc3` → `0.2.0` |
| Next feature | `uv version --bump minor --bump rc` | `0.2.0` → `0.3.0rc1` |

The rule that makes it a cycle: **the base version moves once, when the cycle opens.**
From then on master only increments the rc counter, however many pushes land, until the
manual pipeline tags the final version. That tag closes the cycle; the next feature opens
the next one.

Open question for whoever builds this: a `feat:` landing mid-cycle in a `fix:`-sized
cycle (base at `0.1.1rc2`, a feature arrives). Either re-pick the base and restart at
`rc1`, or hold the feature's bump for the next cycle. Decide it then; both are defensible,
and the alpha flow above never has to face it.
