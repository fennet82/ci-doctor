# Contributing to ci-doctor

How to get a change in: set up, run the checks, name the commit, push. The
engineering guidelines — where code goes, the invariants that must not break, how
to write tests, the cookbook and the style rules — live in
**[GUIDELINES.md](GUIDELINES.md)**, and you should read that before writing code.

Why the architecture is shaped this way is [docs/PLAN.md](docs/PLAN.md); the
user-facing overview is the [README](README.md).

---

## 1. Setup

```sh
mise install      # uv, gitleaks, gh, glab, node, codegraph
mise run setup    # uv sync + activate the repo's git hooks
```

`mise` is optional — `uv sync` alone is enough to run the tests. It exists so the
binaries the hooks and CI call are the same versions for everyone. Python itself is
not in `.mise.toml`: uv installs it from `requires-python`, and pinning it twice is
one more thing to drift. Neither is ruff, for the same reason — it is a dev
dependency, so `uv.lock` pins it and `uv run ruff` is identical locally and in CI.

`codegraph` comes with it but is not run for you — indexing this repo into
`.codegraph/` is your call, and the index is local to your machine (the directory
ignores its own contents).

No services, accounts or credentials are needed; the suite is fully offline.

## 2. Tests and checks

```sh
mise run test         # or: uv run pytest
mise run test:matrix  # the suite on 3.11-3.14, like the CI matrix
mise run cov          # the suite with coverage, against the floor CI enforces
mise run lint         # ruff check + format (Python and markdown), then ty
mise run check        # everything CI runs: test, lint, guardrails, leaks
mise run cleanup      # delete rebuildable junk: caches, build output, report.*
```

`cleanup` works from an explicit list rather than `git clean -Xd`, because ignored
is not the same as garbage: it keeps `.venv`, `docs/site/node_modules`, the
`.codegraph` index, `llm.config.yml` and anything you have in `.git/info/exclude`.
It does remove the per-version `.venv-3.x` that `test:matrix` builds.

The suite blocks real sockets and never calls an LLM, so it runs in seconds. What a
*good* test looks like, the fixture layout, and which file to put it in are in
[GUIDELINES.md §4](GUIDELINES.md).

- **`test:matrix`** gives each version its own cached `.venv-3.x`, so your default
  `.venv` is never rebuilt underneath you. The three lists of supported versions —
  the `pyproject.toml` classifiers, the `ci.yml` matrix and this task — are pinned
  to each other by `tests/test_python_versions.py`.
- **Coverage** is a floor, not a target (`fail_under` in `pyproject.toml`). Off by
  default because measuring triples the runtime; CI always measures. Raise it when
  a real gap closes, never lower it to turn a red build green.
- **Types** are checked by [ty](https://github.com/astral-sh/ty) over `ci_doctor`,
  not `tests`. It is pre-1.0 and pinned, so a new release cannot redden an
  unrelated PR.

### 2.1 Git hooks

`mise run setup` points `core.hooksPath` at [`.githooks/`](.githooks). They are plain
shell — read them, they are short.

| Hook | Does |
|---|---|
| `pre-commit` | `ruff check --fix` then `ruff format` on staged `*.py`, `ruff format --preview` on staged `*.md`, and re-stages only what it rewrote. Fix runs first: removing an unused import leaves the blank lines it sat between. |
| `commit-msg` | Rejects anything that is not a Conventional Commit. Not cosmetic — the release workflow reads the type to decide whether to ship. |
| `pre-push` | `pytest`, `ty`, the `core/` guardrail grep, and `gitleaks` if installed. Everything CI runs, before the round trip. |

`--no-verify` skips them. CI does not, so it only moves where you find out.

### 2.2 Reading the results on GitHub

There is no coverage tab like GitLab's, so the run page carries it instead:

| What | Where |
|---|---|
| Coverage table, per version | The run's **Summary** page (`$GITHUB_STEP_SUMMARY`), missing line numbers included. |
| Failing tests | Annotated **inline on the PR diff**, by the `pytest-github-actions-annotate-failures` dev dependency. Silent outside Actions. |
| Type and lint errors | The `lint` job's log. |

All native — no third-party action, no account. Coverage *history* is the one thing
it cannot do; that needs a hosted service like Codecov.

## 3. Commits

**[Conventional Commits](https://www.conventionalcommits.org/)** — the release
pipeline parses them, so the format is functional, not cosmetic.

```
<type>(<optional scope>): <imperative summary>
```

| Type | Effect |
|---|---|
| `feat:` `fix:` `BREAKING CHANGE:` in body (or `feat!:`) | ships the next alpha |
| `docs:` `test:` `chore:` `refactor:` `perf:` `ci:` | no release; rides along in the next one |

`.github/workflows/release.yml` runs on every push to `master`: if any commit since the
last tag is a `feat:`/`fix:`/`BREAKING`, it bumps to the next alpha (`0.0.1a2` →
`0.0.1a3`), tags, builds and publishes to PyPI. Otherwise it no-ops.

The type does **not** pick how big the bump is — while the project is in alpha, only the
counter moves. [GUIDELINES.md §8](GUIDELINES.md) has the versioning scheme and where it
goes next. Keep the summary imperative and scoped to one change, and don't hand-edit the
version in `pyproject.toml` or `uv.lock` — the pipeline owns both.

### 3.1 Skipping CI

A commit message containing **`[skip ci]`** runs no workflow at all — GitHub itself
drops the `push` and `pull_request` events, so there is nothing to configure. Use it
for a commit that cannot affect the build. The release job already relies on this:
its version-bump commit ends in `[skip ci]`, which is what stops the pipeline from
re-triggering itself forever.

The token is literal. `[ci skip]`, `[no ci]`, `[skip actions]` and `[actions skip]`
work too; `[skip-ci]` with a hyphen does **not** — it is not one GitHub recognises,
and the pipeline will run as normal.

## 4. Before you push

```sh
uv run pytest                      # all green, no exceptions
uv run ty check ci_doctor          # no type errors in the shipped package
grep -ri gitlab ci_doctor/core/    # must be empty (invariant #2)
grep -ri github ci_doctor/core/    # must be empty
```

- New behaviour has a test. New matcher has a fixture.
- Never commit `report.md` / `report.json` (local run artifacts).
- Branch off `master`; don't push directly to it unless you intend to trigger a release.
