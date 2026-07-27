# CHANGELOG


## v1.1.1 (2026-07-27)

### Bug Fixes

- **extract**: Let matcher priority survive budget pressure
  ([`5ee2c79`](https://github.com/fennet82/ci-doctor/commit/5ee2c7914aca04aa6e089bfa1161040c9c28a013))

extraction.matchers has carried a priority field documented as "higher priority survives budget
  pressure", but nothing read it: extract() returned list[str], discarding the windows, and
  budget.fit() then cut positionally from the head.

That picks wrong whenever a tool reports the cause before its own epilogue. On a tsc-then-npm build
  over budget, the old path discarded the compiler errors entirely and spent the whole budget on
  npm's complaint:

OLD: TS errors=False npm noise=True truncated=True

NEW: TS errors=True npm noise=False truncated=False

extract() now takes an optional max_tokens and sheds whole low-priority windows worst-first; fit()
  stays the last resort that cuts inside what survived. It never drops the last window — a single
  unbounded start/end block can exceed the budget alone.

Note the limit: _merge fuses adjacent windows and takes the max priority, so only windows separated
  by unselected lines are rankable.

### Chores

- Schema hint in defaults.yml, lockfile and plugin settings
  ([`b8d38e7`](https://github.com/fennet82/ci-doctor/commit/b8d38e742332093e1375fa6be2694386fce30f98))

Your changes, carried through: the yaml-language-server $schema comment moved to the top of
  defaults.yml, and the frontend-design plugin enabled. uv.lock picks up the 1.1.0 version it had
  lagged behind.

### Documentation

- Consolidate the markdown onto one home per topic
  ([`b83d0ec`](https://github.com/fennet82/ci-doctor/commit/b83d0eccffa7f01bf7bedc62595e49a546117455))

The four docs had grown to ~940 lines with the same content in three places: the invariants list in
  README, GUIDELINES and PLAN; the pipeline diagram in all three; air-gap instructions across four
  files.

Each topic now has one owner. GUIDELINES owns the invariants and the package layout; the site owns
  the CLI, config and backend references, so README links out instead of restating them. PLAN drops
  the milestones, the "open questions" and the duplicated invariants, keeping the design rationale
  that outlives the code. OFFLINE.md is deleted — README plus the site's air-gap sections already
  covered all of it, and its one unique line pointed at a PLAN section that was a four-line stub.

Also fixes: the stale 347-test count (now stated nowhere, so it cannot drift), a duplicate "GitHub
  Actions" heading, and PLAN still marking the GitHub adapter as future work.

941 -> 782 lines, with every relative link verified to resolve.

- Rebuild the site and publish it to GitHub Pages
  ([`42bf99e`](https://github.com/fennet82/ci-doctor/commit/42bf99eb69badc9974d2876c7c92b3d693b84123))

Design: real token system with a dark palette, fluid type scale, theme toggle (with a pre-paint
  script so dark-theme visitors get no white flash), collapsing mobile nav, skip link, focus rings,
  reduced-motion, OG/canonical meta.

Content: adds an /action/ page — the shipped GitHub Action was nowhere in the docs, and the Actions
  example still hand-rolled uv sync. Also covers local runs via the git-origin fallback, the config
  subcommand, and token scopes per provider.

Publishing: docs.yml deploys from master only, path-filtered; ci.yml gains a docs job that builds on
  PRs but cannot publish.

The base gotcha: Pages serves from /ci-doctor and Astro does not rewrite plain href/src attributes,
  so every existing link worked under astro dev and would have 404'd in production. All of them now
  go through src/lib/url.ts, verified against the built HTML — 8/8 targets resolve.

### Refactoring

- Drop the redundant __future__ annotations import
  ([`80c6e9f`](https://github.com/fennet82/ci-doctor/commit/80c6e9f06ff371d94c19b5cc7bf2a375a9c694d3))

requires-python is >=3.11, where PEP 604 unions and builtin generics are native. The only genuine
  need is a forward reference, and the two places that have one (core/ports.py under TYPE_CHECKING,
  models.Section.children) already quote theirs — so the import was insuring against a risk the code
  had already handled another way.

Verified beyond the suite, since a broken forward ref fails at import time rather than test time:
  every module imports, get_type_hints() resolves on every dataclass, and both pydantic schemas
  build.


## v1.1.0 (2026-07-27)

### Chores

- Merge master (1.0.0) into the branch
  ([`cb291cb`](https://github.com/fennet82/ci-doctor/commit/cb291cb0d91c8fe261849298fd21d4cb944db445))

Master released 1.0.0 and dropped .claude/skills/ while this branch was open. Clean merge; the
  markdown and gitleaks path exclusions still name .claude/**, which now simply matches nothing
  rather than being wrong.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- Sync uv.lock to the 1.0.0 version from master
  ([`47b2b0f`](https://github.com/fennet82/ci-doctor/commit/47b2b0f8b3665dbf5f127590035375fa81898f10))


## v1.0.0 (2026-07-25)

### Bug Fixes

- **action**: Use a venv and never fail the caller's workflow
  ([`d1c8dfe`](https://github.com/fennet82/ci-doctor/commit/d1c8dfefe3a682446bc469f7c7bec1b8e5a9a0aa))

Three defects found reviewing the action, none reachable from the test suite:

- `pip install` into the runner's system Python fails outright on Ubuntu 24.04, which is
  externally-managed under PEP 668. Installs into a venv instead. - The step inherited -e from
  GitHub's `shell: bash`, despite a comment claiming otherwise, so a non-zero analyze or a malformed
  report.json would have failed the caller's workflow — breaking invariant #3 from the outside,
  whatever the analyzer itself guarantees. Now explicitly `set +e`, guarded JSON read, `exit 0`. -
  Docs told people to use @v1. No such tag exists and none will: semantic-release publishes exact
  versions, so there is no floating major to resolve.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Chores

- Added skills for claude
  ([`6bb28f0`](https://github.com/fennet82/ci-doctor/commit/6bb28f0f18ac3d864ec76679a1b791683bfb47f5))

### Code Style

- Apply ruff format across the tree
  ([`dd7390b`](https://github.com/fennet82/ci-doctor/commit/dd7390b4459a45f54b7bcab5949a5b3f693de830))

The repo had never been formatted, so adding a formatter to the hooks and to CI meant either
  restyling every file as it was next touched, or one commit that gets it over with. This is that
  commit — no behaviour change, tests untouched at 347.

line-length is 110 rather than ruff's default 88: the code was written to it (p99 line is 109
  chars), and exploding the long single-line `return Attribution(...)` calls onto five lines each
  costs more than it buys.

Markdown is included but only reformats Python code blocks inside the docs, never prose or tables,
  and skips .claude/ (vendored third-party skill docs).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

### Continuous Integration

- Add a reusable action, PR checks, git hooks and a mise toolchain
  ([`9cc829c`](https://github.com/fennet82/ci-doctor/commit/9cc829cf30dc4ee337b6b5988ce7c285e99aeda0))

action.yml — a composite GitHub Action, so consumers get one step instead of a uv-install recipe.
  Composite rather than Docker on purpose: it installs the source tree the caller pinned (`uses:
  fennet82/ci-doctor@v1` -> that ref's code), so the action version and the analyzer version cannot
  drift, with no registry or PyPI round-trip at release time. run-id defaults to the run that
  triggered it, and the verdict (phase, category, confidence, is-infra-not-code) is exposed as
  outputs so a workflow can branch on it — e.g. only retry when the failure was infrastructure.

.github/workflows/ci.yml — on PRs to master and on master itself: tests, lint (ruff check + format,
  Python and markdown), the core/ guardrail grep, and gitleaks. Separate jobs so a red check names
  the kind of problem without reading the log.

.githooks/ — plain shell, enabled by `mise run setup`, no framework to install. pre-commit fixes and
  formats staged files then re-stages what it rewrote; commit-msg enforces Conventional Commits,
  which semantic-release parses to pick the bump; pre-push runs the suite, the guardrail and
  gitleaks.

.mise.toml — uv, gitleaks, gh, glab, node, plus tasks wrapping every check. Python and ruff are
  deliberately absent: uv installs Python from requires-python, and ruff is now a dev dependency, so
  uv.lock pins both and `uv run ruff` is the same binary locally and in CI. Before this, `uv run
  ruff` resolved to whatever ruff happened to be on PATH.

.gitleaks.toml — the redaction suite plants fake tokens and asserts they are scrubbed, so the
  scanner finds them every run. The exemption is scoped to those two files and that one rule; a real
  token in application code still fails the build.

ruff is pinned <0.16: 0.16 widened the default rule set (25 new findings across existing code),
  which is its own change, not this one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

- **mise**: Install codegraph via the npm backend
  ([`c85cf75`](https://github.com/fennet82/ci-doctor/commit/c85cf751749e808e0e0878e5a1e5530e152f2219))

`node` was listed "for codegraph" without anything actually installing it. @colbymchenry/codegraph
  is declared as a tool rather than shelled out in the setup task, so `mise install` pins its
  version alongside everything else.

Indexing is deliberately not part of `mise run setup`: building .codegraph/ is a per-developer
  choice, and the directory ignores its own contents so the 21MB index never reaches a commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

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

- **github**: Read GitHub through PyGithub, run locally, and fix the segmenter
  ([`9b62eaf`](https://github.com/fennet82/ci-doctor/commit/9b62eaf67080d02abf67f7089bbedd020dee7aae))

Replaces the hand-rolled REST client with PyGithub, makes a run analyzable from a laptop, and fixes
  three bugs that writing realistic GitHub fixtures exposed.

Adapter: - Delete GitHubApi (~130 lines) in favour of PyGithub. ref/sha/web_url now come off the
  workflow run instead of GITHUB_* env vars, and run.pull_requests identifies the PR authoritatively
  (GITHUB_REF stays the fork fallback). - Normalise PyGithub's datetime timestamps at the boundary:
  the domain models are plain dataclasses, so one would sail through to a JSON-dump crash. - Check
  the status of the log-blob fetch; an expired signed URL answers with an XML body that would
  otherwise be analyzed as the log.

Local runs: - providers/git_origin.py resolves the repository from `git remote origin` when
  GITHUB_REPOSITORY / CI_PROJECT_ID is unset, warning once. Both adapters use it.

Fixes, each verified against the pre-change code: - A `##[group]` wraps a step's *header*; its
  output follows `##[endgroup]`. Reading the group as the step collected `with:`/`env:` and dropped
  everything the command printed, so the evidence bundle held the step's inputs. A step now owns
  output until the next step opens, and closes when the runner reports it — which is also what
  leaves a section open for a cancelled or timed-out job. - `##[error]` was invisible to the
  classifier (`\b(ERROR|FATAL)\b` is case-sensitive), and `##[warning]` was treated as fatal — the
  "blamed the cache" bug on the GitHub path. Both annotations are now recognised, matched
  case-sensitively so a tool's own "Warning:" is not excused. - Drop the vendor tokens (`npm ERR!`,
  `Traceback`) from the classifier: they are a second, hardcoded copy of the matcher catalogue that
  no config can tune, and both already ship in defaults.yml.

Fixtures: every GitLab log now has a GitHub twin (41/41) and every log has a verdict (41). Tool
  output is copied verbatim; runner framing is re-written, since a GitLab runner line in a GitHub
  log asserts something that cannot happen. Three guards keep the grid square, and
  test_matcher_packs now feeds matchers the lines the pipeline actually produces rather than the raw
  file.

Docs: split the engineering guidelines out of CONTRIBUTING.md into GUIDELINES.md.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>


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
