# CHANGELOG


## v1.0.0 (2026-07-25)

### Features

- Rework the config/analyze CLI surface and document every symbol
  ([`98bc4c5`](https://github.com/fennet82/ci-doctor/commit/98bc4c5762ac13a2ef3a5e10dfefc4a4f4778cd7))

`analyze` now takes one positional target: an existing path replays that log offline, anything else
  is fetched as a pipeline id. A missing target that looks like a path reports "no such log file"
  rather than being fetched as an id.

`--config` gains a `-f` alias and is repeatable on both subcommands, applied left to right so the
  rightmost file wins. `config --validate` merges every layer and names what failed instead of
  raising.

`config` output pages through $PAGER on a terminal (`--less` forces it, `--plain` and piping skip
  it) and is syntax-highlighted for all three modes, including `--schema` and the `--diff` view.

Matcher packs sharing a shipped id now override field by field, so retuning `priority` on the
  `pytest` pack keeps its `start`/`end` instead of blanking them into a matcher that can never fire.

Also carries the in-flight docstring pass across the package and tests.

BREAKING CHANGE: `analyze --from-file <log>` is now `analyze <log>`.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Breaking Changes

- `analyze --from-file <log>` is now `analyze <log>`.


## v0.2.0 (2026-07-25)

### Documentation

- Add CONTRIBUTING.md and refresh README, PLAN and roadmap
  ([`37e583f`](https://github.com/fennet82/ci-doctor/commit/37e583f47b53196299a80f274b544c39f48c397e))

CONTRIBUTING.md is the working guide the repo lacked: where code goes, the invariants that must not
  break (read-only, always exit 0, no provider names in core/, no network in tests) with the greps
  that verify them, how to add a matcher pack or a CI provider, the provider-generic fixture layout,
  conventional commits and what actually triggers a release, and the pre-push checklist.

It ends with the gotchas that already cost time — rich cropping instead of wrapping under
  soft_wrap=True, highlighter=None not disabling highlighting, category ordering being load-bearing,
  and matchers that match nothing failing silently because the tail window still returns output.

Also flag a real footgun now that 34 matchers ship: config lists REPLACE, so a repo defining
  extraction.matchers silently drops every language pack.

Linked from README (humans) and .claude/CLAUDE.md (agents).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Features

- **cli**: Log analysis progress and colour-code log levels
  ([`dfe7809`](https://github.com/fennet82/ci-doctor/commit/dfe78096e168ac0b0fcbf483105f4ef6fe2376a7))

The LLM call shells out to a model and can take tens of seconds, but the only log line before it was
  DEBUG — so a default-level run went silent with no feedback. Promote it to INFO and add a matching
  line for the --from-file path, which previously printed nothing at all before finishing.

Route logging through rich's RichHandler with an explicit level theme (DEBUG blue / INFO green /
  WARNING yellow / ERROR red). Uses NullHighlighter: passing highlighter=None does NOT disable
  highlighting, rich falls back to ReprHighlighter and repr-colours the message body over the level
  colour.

Also correct the --from-file help text: it skips the network fetch but still calls the LLM when one
  is configured.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- **matchers**: Language packs for 12 ecosystems and a runtime category
  ([`056d2cc`](https://github.com/fennet82/ci-doctor/commit/056d2cc384cc5956fd2b05bf90205e3987398d40))

Adds 26 matchers (8 -> 34) as pure config data: Rust, .NET, Ruby, PHP, Gradle, Bazel,
  Playwright/Cypress, node + pnpm/yarn/bun, C/C++, eslint/mypy, Terraform, plus Python tracebacks
  and Go panics, which had no matcher at all.

Extraction alone would still report category "unknown" for every new language, so
  _CATEGORY_SIGNATURES grows to match. That list is ordered and first-match-wins, and the ordering
  is the load-bearing part:

- Narrow build markers now precede the broad `test` signature, whose \bFAILED\b was claiming "Build
  FAILED." (dotnet), ":app:compileJava FAILED" (gradle) and "FAILED: Build did NOT complete"
  (bazel). - Cypress's "Timed out retrying after 4000ms" is assertion-retry wording, not a job
  timeout — excluded via a negative lookahead. - The new `runtime` category (app crashed: traceback,
  panic, PHP fatal, node stack) is checked LAST, because a traceback also appears in a pytest
  failure (test) and a missing-import crash (dependency), and both are more actionable. - `config`
  had no signature at all despite being in the Category enum.

Each pack gets a realistic fixture log and three checks: the matcher fires and windows a subset
  rather than the whole log, the causal line survives into the evidence bundle, and the
  deterministic category is correct. That caught two real bugs: bun anchored only on its trailing "
  N fail" summary and windowed past the assertion above it, and node:internal/ frames appear in
  every JS stack including jest failures, which the runtime signature would have stolen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- **render**: Readable report layout — wrapping evidence, copyable handoff
  ([`728ae44`](https://github.com/fennet82/ci-doctor/commit/728ae44a19c77dcf95546b075dc1b9bd1c960f23))

Three fixes to text being silently cut or hard to read:

- Console(soft_wrap=True) makes rich CROP panel content at the border instead of wrapping it, so
  both the excerpt and the why-it-matters subtitle were truncated mid-word. A Panel subtitle is also
  drawn inside the bottom border, where it can never wrap. Evidence is now one panel per item
  carrying a Description and an Error header, printed with soft_wrap=False and overflow="fold" so
  nothing is ever cropped. - The handoff prompt was boxed, so copying it picked up border characters
  and long lines were cut. It is now plain text under a title. - Blank lines between sections, via
  end="\n\n" on the content prints.

Colour: un-dim the data a developer actually needs (phase/category/confidence,

contributing factors, the evidence description, remediation file:line); the raw supporting log lines
  stay visually secondary.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Refactoring

- **tests**: Scope log fixtures by provider behind a generic support module
  ([`610d32d`](https://github.com/fennet82/ci-doctor/commit/610d32d46aa61aaf276f6ffa4166506094061e15))

Logs are provider-specific — every CI system frames a job differently — so they move to
  fixtures/logs/<provider>/. Expected verdicts deliberately do NOT move: attribution lives in
  provider-neutral core/, so the same scenario must classify identically whoever produced the log.
  One expected/oom_137.json now covers every provider shipping an oom_137.log, turning "core is
  provider-neutral" from a claim into an assertion.

tests/support.py is the single place that knows the layout. No test names a provider, so adding one
  (jenkins, bitbucket, travis...) is two steps and zero test edits: drop fixtures/logs/<provider>/
  and register its segmenter in SEGMENTERS. Verified both directions — a provider dir with no
  segmenter fails test_every_provider_dir_has_a_segmenter, and adding a GitHub Actions sample.log
  made the CLI smoke test run for both providers with no test changes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>


## v0.1.0 (2026-07-24)

### Documentation

- Astro documentation site + GitHub Actions example
  ([`522cf47`](https://github.com/fennet82/ci-doctor/commit/522cf476ca450fd37bcfd06344518f642eeaaae5))

- docs/site: static Astro site (overview, requirements, configuration, usage, CI/CD examples) with a
  shared layout, light/dark styles, and Shiki-highlighted snippets. Builds to dist/ (npm run build);
  node_modules/dist gitignored. - examples/github-actions.example.yml: workflow_run-triggered
  analyzer job - README: link to the docs site

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Refresh README (accurate test count, example endpoint); bump Astro to 7
  ([`03aeabf`](https://github.com/fennet82/ci-doctor/commit/03aeabf4f6fc9a8babd1c09b386b1d506bec9a6a))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Features

- Automated release pipeline and publishable package
  ([`8339e19`](https://github.com/fennet82/ci-doctor/commit/8339e199cb5f90b3812d0888ef6db039653586a4))

Push to master -> python-semantic-release bumps the version from conventional commits, tags,
  generates the GitHub Release, builds the sdist/wheel; the workflow then attaches the Docker image
  (gzipped tar) to the release and publishes to PyPI via Trusted Publishing (OIDC, no token).

Distribution renamed to `ci-doctorr` (the `ci-doctor` name was taken on PyPI); the import package
  `ci_doctor` and the CLI command `ci-doctor` are unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
