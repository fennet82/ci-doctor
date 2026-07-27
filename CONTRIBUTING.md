# Contributing to ci-doctor

How to get a change in: set up, run the checks, name the commit, push. The
engineering guidelines — where code goes, the invariants that must not break, how
to write tests, the cookbook and the style rules — live in
**[GUIDELINES.md](GUIDELINES.md)**, and you should read that before writing code.

The design spec is [docs/PLAN.md](docs/PLAN.md); the user-facing overview is the
[README](README.md).

---

## 1. Setup

```sh
uv sync
```

Python ≥ 3.11. No other services, accounts or credentials are needed — the suite is
fully offline.

## 2. Tests

```sh
uv run pytest          # must be green before you push
```

The suite blocks real sockets and never calls an LLM, so it runs in seconds. What a
*good* test looks like, the fixture layout, and which file to put it in are in
[GUIDELINES.md §4](GUIDELINES.md).

## 3. Commits

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

## 4. Before you push

```sh
uv run pytest                      # all green, no exceptions
grep -ri gitlab ci_doctor/core/    # must be empty (invariant #2)
grep -ri github ci_doctor/core/    # must be empty
```

- Update the test count in `README.md` if it changed.
- New behaviour has a test. New matcher has a fixture.
- Never commit `report.md` / `report.json` (local run artifacts).
- Branch off `master`; don't push directly to it unless you intend to trigger a release.
