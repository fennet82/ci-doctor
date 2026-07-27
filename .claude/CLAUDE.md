## Working in this repo

Read [GUIDELINES.md](../GUIDELINES.md) before writing code. It covers where code
goes, the invariants that must not break (read-only, always `exit 0`, no provider
names in `core/`, no network in tests), how to add a matcher pack or a CI provider,
the provider-generic fixture layout, and the style rules.

[CONTRIBUTING.md](../CONTRIBUTING.md) is the shorter process guide: setup, the test
command, conventional commits, and the pre-push checks.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->
