# Matcher boundaries, PyPI metadata, and concept docs

Date: 2026-07-28
Status: §0 done; §1-§3 proposed, awaiting review

---

## 0. Fixture coverage (done)

Prerequisite to the migration: every pack needed a log to verify its new `end`
against. Measuring it found that coverage was real for 31 of 35 packs and
*phantom* for four — the matcher fired only on another tool's output, or on the
runner's own framing:

| pack | what it actually matched | fix |
|---|---|---|
| `bazel` | `ERROR: Job failed: exit code 1` — GitLab's universal job trailer, so it opened a priority-85 window on 40 of 41 logs | require `path:line:col:`; require `//target` before `FAILED in \d` (gradle prints `BUILD FAILED in 34s`) |
| `jest` | `FAIL example.com/svc/internal/reconcile` — `go test` | require a `.test.`/`.spec.` JS filename |
| `maven_gradle` | `FAILURE: Build failed with an exception.` — gradle, never maven's `[ERROR]` half | new maven fixture |
| `rust_compile` | `error: expect(received).toBe(expected)` — bun | drop the bare `^error: `, keep `error[E\d+]` and rustc's driver messages |

`bazel` was a live bug, not just a test gap: it shipped, and it ranked above the
real evidence under budget pressure on every GitLab job.

Added: `jest_test_failure` and `maven_build_failure` fixtures (gitlab + github +
shared verdict), a `//target FAILED in 0.4s` line to the bazel fixture so that
alternative is exercised, and six `CASES` rows covering `pytest`, `jest`,
`go_test`, `maven_gradle`, `tsc`, `npm`, `docker_build` and `oom`.

Guards so this cannot recur: `test_no_pack_fires_on_the_runners_own_trailer`
(every pack except `generic_error` and `oom`, which read the runner's framing on
purpose) and `test_every_shipped_pack_is_covered_by_a_case`. `generic_error` gets
its own assertion rather than a `CASES` row — the shared "windows rather than
swallows" check contradicts a fallback whose job is to be greedy.

Every pack now has a log under both providers, and 424 tests pass.

Three pieces of work, in dependency order. The matcher change lands first because
the docs describe it.

---

## 1. Matcher boundaries (the substantive change)

### Problem

`extraction.matchers` ships two mutually exclusive shapes:

- `pattern` + `before`/`after` — a fixed line count around an anchor.
- `start`/`end` — a bounded block.

Both are broken in complementary ways.

**Fixed `after` cannot span a variable-length diagnostic.** `rust_compile` uses
`after: 8`. A single `E0308` with a long type diff is 20+ lines, so the evidence
is cut mid-diagnostic and the report never sees the `expected ... found ...`
line that explains the failure. There is no line count that is right in advance —
that is the whole problem. The same applies to `python_traceback` (`after: 25`),
`playwright` (`after: 20`), and every other pack that guesses a number.

**`start`/`end` is unbounded.** `extract.py:57-59` — when `end` never matches,
`j` walks to the end of the log and the window swallows everything after the
anchor. One bad regex silently turns a windowed excerpt into the whole log.

**Neither shape expresses what tools actually do.** A diagnostic ends at the
first line that is not part of it — the next column-0 line for rustc, the summary
line for eslint, the exception line for a Python traceback. That is a *boundary*,
not a paired delimiter and not a length.

### Design

One shape. Three fields plus `priority`:

| field | meaning |
|---|---|
| `start` | Anchor regex. Required. (Today's `pattern`, renamed.) |
| `end` | Boundary regex. The window extends to the first line at or after `start+1` that matches, **inclusive**. Optional. |
| `before` | Lines of context kept *above* the anchor. Optional, default 0. |

`after` is **deleted**. Forward extent is `end`, or nothing.

Window resolution, in order:

1. Anchor at each line matching `start`.
2. `end` present → extend to the first matching line after the anchor, capped by
   the new global `extraction.max_window_lines`.
3. `end` absent → the anchor line alone.
4. Extend `before` lines upward.

Everything downstream is unchanged: overlapping windows still merge taking the
max priority, `_drop_to_fit` still sheds by priority under budget, `_render`
still marks every gap with an explicit elision count.

#### Why `before` survives and `after` does not

`end` replaces `after` everywhere, because a forward extent always has a
boundary to look for. `before` has no equivalent: it exists for anchors that are
*trailing symptoms*, where the cause is above and nothing marks its start.

```yaml
- id: oom
  start: 'exit code 137|Killed|Out of memory'
  before: 5          # what was running when the kernel killed it
- id: node_runtime
  start: '^\s*at .+\(node:internal'
  before: 12         # the error message sits above the stack frame
```

There is no regex for "five lines before the OOM kill" — the kernel prints
nothing distinctive above it. `oom`, `node_runtime` and `generic_error` all
depend on this.

#### New global: `extraction.max_window_lines`

Default 200. Hard ceiling on any single window. Today an `end` that never fires
runs to EOF; once every pack carries an `end`, that failure mode needs a rail. A
wrong `end` then costs 200 lines of padding rather than the entire log, and the
elision markers make it visible.

#### Coupled change: stop stripping blank lines

`defaults.yml` ships `denoise.noise_patterns: ['^\s*$']`, which deletes every
blank line before `extract` runs, so any `end: '^\s*$'` would be unreachable —
the boundary is destroyed upstream of the matcher that needs it. Remove that
noise pattern; `dedupe_repeats` already collapses runs of blanks, so the volume
argument is thin.

This matters for `eslint` and for `generic_error` — the fallback pack for tools
with no signature, where a blank line is the only boundary available. It is *not*
a universal boundary: `npm_build_failure.log:37,44` shows blank lines *inside* a
tsc diagnostic, which is why `tsc` bounds on `error TS\d+:|^Found \d+ error`
instead.

### Migration

All 35 shipped packs move in one change. Supporting both shapes in parallel
would recreate exactly the two-form confusion this deletes.

Mechanical part: `pattern:` → `start:`, and `after: N` → an `end` regex.
`before:` values carry over unchanged.

**Packs with an existing fixture — `end` is verifiable immediately** (26):
`rust_test`, `rust_panic`, `rust_compile`, `dotnet_test`, `dotnet_build`,
`rspec`, `minitest`, `ruby_exception`, `bundler`, `phpunit`, `php_fatal`,
`composer`, `gradle`, `bazel`, `playwright`, `cypress`, `python_traceback`,
`go_panic`, `cc_cpp`, `eslint`, `python_lint`, `terraform`, `pnpm`, `yarn`,
`bun`, `node_runtime`.

Every pack now has a covering fixture under both providers (§0), so every `end`
regex written here is verifiable the moment it is written. Nothing in the
migration is authored blind.

`oom` and `generic_error` need no `end` (before-only and blank-line-bounded
respectively).

#### Rule: prefixed blocks need no `end`

When a tool prefixes *every* line of its block (`npm ERR!`, mypy's
`file.py:12: error:`, eslint's `12:5  error`), each line anchors its own window
and adjacent windows merge into the block for free. `end` is only required for
blocks with an **unprefixed body** — rustc, pytest, Python tracebacks, tsc's
pretty output. This decides the shape for a large part of the catalogue and
removes `end` from the migration for those packs entirely.

Representative rewrites:

```yaml
# The headline fix — spans a 60-line type diff exactly as far as it goes.
- id: rust_compile
  start: '^error(\[E\d+\])?: |^error: could not compile'
  end: '^\S'                    # next column-0 line closes the diagnostic
  priority: 82

# Unchanged in behaviour, only renamed.
- id: pytest
  start: '^=+ FAILURES =+'
  end: '^=+ short test summary'
  priority: 90

# before-only: the anchor is a trailing symptom.
- id: oom
  start: 'exit code 137|Killed|Out of memory'
  before: 5
  priority: 95

# ends at its own summary line, not at a blank.
- id: eslint
  start: '^\s*\d+:\d+\s+error\s+'
  end: '✖ \d+ problems?'
  before: 2
  priority: 78

# tsc's pretty code frame starts at column 0, so `^\S` would cut the window
# short; bound on the next diagnostic or the summary instead.
- id: tsc
  start: 'error TS\d+:'
  end: 'error TS\d+:|^Found \d+ error'
  before: 1
  priority: 80

# every line is prefixed, so each anchors its own window and they merge.
- id: npm
  start: 'npm ERR!'
  before: 2
  priority: 75
```

### What is deliberately NOT built

**No `overrides:` and no `role: cause|wrapper`.** Both were considered as ways to
express "tsc is the root cause, `npm ERR!` is a wrapper complaining about it".
Rejected: they only change behaviour when the token budget binds and windows get
dropped, and with `max_input_tokens` raised (below) that branch is effectively
never taken. A modern model reasons "npm ERR! is downstream of tsc" from the full
evidence without help. Revisit only if a real report blames the wrapper.

`priority` and `_drop_to_fit` stay as they are, for small-context local models
where the budget still binds.

### Bug found while reading

`_drop_to_fit` (`extract.py:111-114`) sorts by `(priority, -start)` and pops
index 0, so among equal priorities it drops the **latest** window first. The
docstring claims it drops "the earliest, since later output sits closer to the
failure". The code is right — the first compile error is the root cause and later
ones cascade from it — and the docstring is wrong. Fix the comment and add a test
pinning the tiebreak, which is currently unasserted.

### Config raise: `llm.max_input_tokens`

12000 → 32000. The 12000 ceiling is what makes budget pressure a routine event
rather than an edge case; raising it means `_drop_to_fit` rarely fires and the
evidence arrives whole.

32000 rather than something larger because the default backend points at a local
OpenAI-compatible endpoint. Document the trap: **Ollama's default `num_ctx` is
4096** regardless of the model's native window, and it truncates silently from
the front. Users on Ollama must raise `num_ctx` or lower `max_input_tokens`.

### Testing

- `test_extract.py`: rewrite `_m()` for the new field set. Add a case proving a
  variable-length block is captured whole (a 60-line rust diagnostic bounded by
  `^\S`), a case proving `max_window_lines` caps a never-firing `end`, and a case
  pinning the `_drop_to_fit` equal-priority tiebreak.
- `test_matcher_packs.py`: all 31 cases from §0 must keep passing unchanged —
  that is the regression net for the migration.
- `test_config.py`: `after` removal is a schema change; assert an old-style
  config with `after:` fails validation with a clear message rather than being
  silently ignored.
- `test_denoise.py`: blank lines survive denoise by default.

### Breaking changes

`extraction.matchers` entries using `pattern:` or `after:` stop validating —
`extra="forbid"` turns them into an error, not a silent no-op, which is the
intended behaviour. This is a minor-version break; the CHANGELOG needs a
migration note showing `pattern` → `start` and `after: N` → `end: <regex>`.

---

## 2. PyPI project description

`pyproject.toml` has no `readme` key, so the PyPI page is blank.

Adding `readme = "README.md"` alone ships a broken page: PyPI does not resolve
relative paths, and `README.md:4` points at
`docs/site/public/ci-doctor-logo.png`. Also required:

- Absolute `raw.githubusercontent.com` URL for the logo `<img src>`. PyPI strips
  `<source>` from the `<picture>` element, so only the `<img>` matters and the
  dark-mode variant is lost there regardless.
- Absolute repo URLs for the `GUIDELINES.md`, `CONTRIBUTING.md` and
  `docs/PLAN.md` links (`README.md:203-208`).

Verify before release: `uv build && uvx twine check dist/*`.

---

## 3. Concept documentation

The core concepts exist only in `GUIDELINES.md`, which is contributor-facing.
`configuration.astro` documents knobs while assuming the reader already knows
what a matcher, a phase, or denoising is.

Add one **Concepts** page to the docs site, nav slot 2 (after Overview), walking
the pipeline end to end:

`fetch → segment → phase attribution → denoise → extract (matchers) → budget → redact → LLM → render`

Each stage gets: what it does, which config keys tune it, and one short
before/after log sample. The matcher section is the centrepiece and describes the
`start`/`end`/`before` shape from §1 — which is why this lands after the matcher
change, not before.

`configuration.astro` then links into it instead of re-explaining, and its
`extraction.matchers` example is updated to the new shape.

---

## Order of work

0. ~~Fixture coverage~~ — done.
1. §1 matchers — schema, `extract.py`, all 35 packs, tests.
2. §2 PyPI — small and independent, can ride along in any commit.
3. §3 docs — written against the shape that shipped in §1.
