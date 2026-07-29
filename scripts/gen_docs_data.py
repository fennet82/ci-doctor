"""Generate the docs site's matcher catalogue from the shipped defaults.

The catalogue is 35 packs that change as a body — the boundary migration rewrites
every one of them — so a hand-written table is stale by the next commit. This
reads `config/defaults.yml`, including the `# --- Group ---` headers that already
organise it, and writes the JSON the site imports.

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

#: `    # --- Rust (cargo) -------------` -> "Rust (cargo)"
_GROUP = re.compile(r"^\s*#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")
#: `    - id: rust_compile` -> "rust_compile"
_ID = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$")

#: Packs declared before the first `# ---` header in defaults.yml.
_UNGROUPED = "Test frameworks and build drivers"


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
    """
    text = DEFAULTS.read_text()
    groups = _groups(text)
    matchers = yaml.safe_load(text)["extraction"]["matchers"]

    ordered: dict[str, list[dict]] = {}
    for m in matchers:
        entry = {
            "id": m["id"],
            "kind": "block" if m.get("start") else "anchor",
            "match": m.get("start") or m.get("pattern") or "",
            "end": m.get("end") or "",
            "before": m.get("before", 0),
            "after": m.get("after", 0),
            "priority": m.get("priority", 50),
        }
        ordered.setdefault(groups.get(m["id"], _UNGROUPED), []).append(entry)
    return [{"group": g, "packs": p} for g, p in ordered.items()]


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    total = sum(len(g["packs"]) for g in build())
    print(f"wrote {OUT.relative_to(ROOT)} — {total} packs in {len(build())} groups")
