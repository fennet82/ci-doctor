# ci-doctor

**Postmortem CI failure analyzer.** It reads a failed CI job's log and explains
*why* it failed. A deterministic classifier decides **where** the job failed
(which phase); an optional LLM only explains **why**, inside the phase already
chosen. Read-only, air-gap friendly — no LLM, no network, no problem: you still
get a full report.

- 📖 Full documentation: https://fennet82.github.io/ci-doctor/
- 🐙 Source: https://github.com/fennet82/ci-doctor

## Tags

- `latest` — the newest published alpha.
- `0.0.1aN` — a specific alpha. Pin this in CI.

## Quick start

Replay a captured log, offline — no network, no LLM:

```sh
docker run --rm -v "$PWD:/app" docker.io/fennet/ci-doctor analyze failing-job.log
```

Against a live pipeline (reads the CI's predefined variables when run inside CI):

```sh
docker run --rm \
  -e CI_DOCTOR_CI=gitlab \
  -e CI_DOCTOR_GITLAB_TOKEN="$TOKEN" \
  docker.io/fennet/ci-doctor analyze "$CI_PIPELINE_ID"
```

`ci-doctor` writes `report.md` and `report.json` to the working directory and
prints a rendered report; mount a volume (`-v "$PWD:/app"`) to keep the files.

## Configuration (env vars)

Every `.ci-doctor.yml` key maps to `CI_DOCTOR_*`, nesting on `__`:

| Variable | What |
|---|---|
| `CI_DOCTOR_CI` | `github` (default) or `gitlab` |
| `CI_DOCTOR_GITHUB_TOKEN` / `CI_DOCTOR_GITLAB_TOKEN` | read-only API token |
| `CI_DOCTOR_LLM__MODEL` / `CI_DOCTOR_LLM__API_BASE` | optional LLM endpoint |

The LLM step is optional throughout — disabled, unconfigured or unreachable,
ci-doctor emits the deterministic report instead of failing.

Full configuration reference: https://fennet82.github.io/ci-doctor/reference/configuration/

## Guarantees

- **Read-only.** The only write is an optional MR/PR note; nothing retries,
  cancels or restarts a job.
- **Always exits 0.** The analyzer can never turn a passing pipeline red.
- **Secrets are scrubbed** from every rendered report and LLM prompt.

MIT licensed.
