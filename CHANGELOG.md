# CHANGELOG


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
