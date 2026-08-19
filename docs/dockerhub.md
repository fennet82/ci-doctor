# ci-doctor

**Postmortem CI failure analyzer.** Reads a failed CI job's log and explains *why*
it failed: a deterministic classifier decides **where** it failed, an optional LLM
explains **why**. Read-only, air-gap friendly — no LLM, no network, no problem.

- 📖 Documentation & configuration: https://fennet82.github.io/ci-doctor/
- 🐙 Source: https://github.com/fennet82/ci-doctor

## Tags

- `latest` — the newest published alpha.
- `0.0.1aN` — a specific alpha; pin this in CI.

## Usage

Replay a captured log, offline — no network, no LLM:

```sh
docker run --rm -v "$PWD:/app" docker.io/fennet/ci-doctor analyze failing-job.log
```

For a live pipeline, pass the CI's token and predefined variables (e.g.
`CI_PIPELINE_ID` and `CI_PROJECT_ID` on GitLab, `GITHUB_REPOSITORY` on GitHub)
through to the container. Every `.ci-doctor.yml` key also maps to a `CI_DOCTOR_*`
env var. See the [configuration reference](https://fennet82.github.io/ci-doctor/reference/configuration/).

Read-only, always exits 0, and scrubs secrets from every report. MIT licensed.
