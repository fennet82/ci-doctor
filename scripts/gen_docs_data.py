"""Generate the docs site's matcher catalogue and phase map from the shipped defaults.

The catalogue is 35 packs that change as a body — the boundary migration rewrites
every one of them — so a hand-written table is stale by the next commit. This
reads `config/defaults.yml`, including the `# --- Group ---` headers that already
organise it, and writes the JSON the site imports. The section->phase map is
generated for the same reason.

The one thing that cannot be derived from the config is *what a pack is for*, so
that prose lives in :data:`NOTES` here — keyed by id, and a pack without an entry
fails the generator rather than rendering a blank cell.

Run: ``mise run docs:data``. `test_docs_data_is_current` fails when the committed
JSON drifts from the config.
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "ci_doctor" / "config" / "defaults.yml"
OUT = ROOT / "docs" / "site" / "src" / "data" / "matchers.json"
PHASES_OUT = ROOT / "docs" / "site" / "src" / "data" / "phases.json"

#: `    # --- Rust (cargo) -------------` -> "Rust (cargo)"
_GROUP = re.compile(r"^\s*#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")
#: `    - id: rust_compile` -> "rust_compile"
_ID = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$")
#: `  step_script: script` -> ("step_script", "script"), inside the `phases:` block.
_PHASE_ENTRY = re.compile(r"^ {2}(\w+):\s*(\w+)\s*$")
#: The comment in `phases:` that separates the GitLab names from the GitHub ones.
_GITHUB_MARKER = re.compile(r"^\s*#.*GitHub", re.IGNORECASE)

#: Packs declared before the first `# ---` header in defaults.yml.
_UNGROUPED = "Test frameworks and build drivers"

#: One line per shipped pack: which tool it recognises and what it pulls in.
#: Add an entry when you add a pack — the generator refuses to run without one.
NOTES = {
    "pytest": "pytest's `=== FAILURES ===` block, from the first failing test through to the short summary.",
    "jest": "A jest failure — the `●` bullet or a `FAIL src/x.test.ts` header — up to the `Tests:` tally. The JS filename is what stops it eating `go test`'s own `FAIL` line.",
    "go_test": "`go test`'s per-test `--- FAIL` marker, plus the assertion output printed under it.",
    "maven_gradle": "Maven's `[ERROR]` lines and the `BUILD FAILURE` banner both Maven and Gradle print.",
    "tsc": "A TypeScript compile error (`error TS2345:`) with the source excerpt around it.",
    "npm": "`npm ERR!` lines. Ranked under the compilers deliberately — npm reports the failure it wrapped, never the cause.",
    "pnpm": "pnpm's `ERR_PNPM_*` codes and the `ELIFECYCLE` a failed lifecycle script exits with.",
    "yarn": "Yarn Classic's `error <Capital>` lines and Berry's `➤ YN####:` diagnostic codes.",
    "bun": "A failed `bun` script or test. Anchors on the `✗` marker as well as the summary, because the summary trails the assertion detail.",
    "node_runtime": "An uncaught Node error: internal stack frames, ESM/CJS resolution failures, unhandled rejections. `before: 12` because the message sits *above* the stack.",
    "docker_build": "BuildKit giving up — `failed to solve` / `executor failed running` — around the Dockerfile step that failed.",
    "oom": "The job being killed: exit code 137, `Killed`, `Out of memory`. Top priority — nothing else in the log explains a SIGKILL.",
    "rust_test": "`cargo test`'s `failures:` block through to the `test result:` line.",
    "rust_panic": "A Rust panic, with the message and the frames that follow it.",
    "rust_compile": "rustc's coded errors (`error[E0308]`) and its own driver messages. A bare `error:` is not Rust enough — bun prints one too.",
    "dotnet_test": "`dotnet test`: xunit/nunit `[FAIL]` markers and the `Failed!` summary line.",
    "dotnet_build": "MSBuild, Roslyn and NuGet error codes — `error CS0103`, `error NU1101`, `error MSB3073`.",
    "rspec": "RSpec's `Failures:` block through to the `N examples, N failures` tally.",
    "minitest": "Minitest's numbered `1) Failure:` entries and the assertion beneath each one.",
    "ruby_exception": "An unhandled Ruby exception — a backtrace frame naming the error class.",
    "bundler": "Bundler failing to resolve: a missing gem, or a version conflict it prints the tree for.",
    "phpunit": "PHPUnit's `There were N failures` block through to its `FAILURES`/`ERRORS`/`OK` footer.",
    "php_fatal": "A PHP fatal or parse error and the stack trace after it.",
    "composer": "Composer's resolver refusing the requirements, with the explanation it prints underneath.",
    "gradle": "Gradle's `* What went wrong:` block, stopping before the `* Try:` boilerplate advice.",
    "bazel": "Bazel's `ERROR path:line:col:` and failed targets. The `path:line:col` is what keeps it off `ERROR: Job failed: exit code 1`.",
    "playwright": "A Playwright failure — the numbered `1) suite › test` header — plus the 20 lines of trace and diff after it.",
    "cypress": "A `CypressError` or a retry timeout, with the command log leading up to it.",
    "python_traceback": "A Python traceback. Weighted `after`, because the exception type is at the *bottom* of a traceback, not the top.",
    "go_panic": "A Go `panic:` or runtime `fatal error:` with the goroutine dump beneath it.",
    "cc_cpp": "C/C++ builds: CMake errors, `make: ***` failures, and linker `undefined reference` errors.",
    "eslint": "ESLint's `line:col error rule-name` entries and its `✖ N problems` footer.",
    "python_lint": "A `file.py:12: error` diagnostic from mypy, ruff, flake8 or pylint.",
    "terraform": "Terraform's boxed `│ Error:` and the `on main.tf line 12` it points at.",
    "generic_error": "The fallback for a tool with no pack of its own: any line starting `ERROR` or `FATAL`. Lowest priority, so it is the first thing shed under budget pressure.",
}


def _groups(text: str) -> dict[str, str]:
    """Map each matcher id to the comment header it is declared under.

    Args:
        text: Raw `defaults.yml` source.

    Returns:
        ``{matcher_id: group_name}``.
    """
    out, current = {}, _UNGROUPED
    for line in text.splitlines():
        if m := _GROUP.match(line):
            current = m.group(1)
        elif m := _ID.match(line):
            out[m.group(1)] = current
    return out


def build() -> list[dict]:
    """Read the shipped matchers into the site's render-ready shape.

    Returns:
        One entry per group, in declaration order, each with its packs.

    Raises:
        KeyError: If a shipped pack has no :data:`NOTES` entry.
    """
    text = DEFAULTS.read_text()
    groups = _groups(text)
    matchers = yaml.safe_load(text)["extraction"]["matchers"]

    ordered: dict[str, list[dict]] = {}
    for m in matchers:
        if m["id"] not in NOTES:
            raise KeyError(f"matcher {m['id']!r} has no NOTES entry — add one in {Path(__file__).name}")
        entry = {
            "id": m["id"],
            "kind": "block" if m.get("start") else "anchor",
            "match": m.get("start") or m.get("pattern") or "",
            "end": m.get("end") or "",
            "before": m.get("before", 0),
            "after": m.get("after", 0),
            "priority": m.get("priority", 50),
            "note": NOTES[m["id"]],
        }
        ordered.setdefault(groups.get(m["id"], _UNGROUPED), []).append(entry)
    return [{"group": g, "packs": p} for g, p in ordered.items()]


def build_phases() -> list[dict]:
    """Read the shipped section-name -> phase map, in declaration order.

    The map is keyed by the section names a segmenter emits, and defaults.yml
    groups them by CI system with a comment. That grouping is the useful part for
    a reader — which names come from *their* provider — so it is carried through.

    Returns:
        ``[{"section", "phase", "provider"}]``, in the order defaults.yml lists them.
    """
    out: list[dict] = []
    provider, inside = "GitLab", False
    for line in DEFAULTS.read_text().splitlines():
        if line.startswith("phases:"):
            inside = True
            continue
        if not inside:
            continue
        if line and not line.startswith((" ", "#")):
            break  # next top-level key ends the block
        if _GITHUB_MARKER.match(line):
            provider = "GitHub Actions"
        elif m := _PHASE_ENTRY.match(line):
            out.append({"section": m.group(1), "phase": m.group(2), "provider": provider})
    return out


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    catalogue, phases = build(), build_phases()
    OUT.write_text(json.dumps(catalogue, indent=2) + "\n")
    PHASES_OUT.write_text(json.dumps(phases, indent=2) + "\n")
    total = sum(len(g["packs"]) for g in catalogue)
    print(f"wrote {OUT.relative_to(ROOT)} — {total} packs in {len(catalogue)} groups")
    print(f"wrote {PHASES_OUT.relative_to(ROOT)} — {len(phases)} section names")
