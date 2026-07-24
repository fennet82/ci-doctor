# Offline / air-gapped install

ci-doctor makes **no** network calls except to the GitLab and LLM endpoints you
configure — no telemetry, no update checks, no runtime model/rule/schema
downloads. Build the artifact once where there is internet, then ship it inside.

## Option A — container image (recommended)

```sh
docker build -t ci-doctor:$VERSION .
docker tag  ci-doctor:$VERSION registry.internal.example.com/ci-doctor:$VERSION
docker push registry.internal.example.com/ci-doctor:$VERSION
```

Reference it from `.gitlab-ci.yml` — see `examples/gitlab-ci.example.yml`.

## Option B — offline wheel bundle

```sh
# where there IS internet:
pip wheel . -w ./wheels
# copy ./wheels inside, then on the air-gapped host:
pip install --no-index --find-links ./wheels ci-doctor
```

## Minimum config

- `gitlab.base_url` — your instance (there is no default host).
- A token: `CI_DOCTOR_GITLAB_TOKEN` env, or `gitlab.token_file` (k8s/Vault mount).
- LLM is optional: leave it unset or set `llm.enabled: false` for the deterministic
  report. To enable, set `llm.model` + `llm.api_base` (any OpenAI-compatible endpoint).
- Custom CA / proxy: `gitlab.ca_bundle`, `llm.ca_bundle`, and the standard
  `HTTPS_PROXY` / `NO_PROXY` / `REQUESTS_CA_BUNDLE` env vars are honoured.

See `docs/PLAN.md` §9 for every knob.
