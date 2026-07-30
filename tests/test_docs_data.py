"""The docs site's matcher catalogue is generated, so it must not drift.

The site imports `src/data/matchers.json` instead of listing 35 packs by hand —
they change as a body, and a hand-written table is stale by the next commit. This
pins the committed file to what `defaults.yml` actually ships.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gen_docs_data import OUT, PHASES_OUT, build, build_phases  # noqa: E402
from ci_doctor.config.loader import load_config  # noqa: E402


def test_docs_data_is_current():
    """A pack added to defaults.yml is a pack the docs must list.

    `build()` also raises on a pack with no NOTES entry, so this fails rather than
    letting the site render a blank "what it catches" cell.
    """
    assert json.loads(OUT.read_text()) == build(), (
        "docs/site/src/data/matchers.json is stale — run `mise run docs:data`"
    )


def test_docs_phase_map_is_current():
    """The docs' section->phase table is the shipped map, not a copy of it."""
    assert json.loads(PHASES_OUT.read_text()) == build_phases(), (
        "docs/site/src/data/phases.json is stale — run `mise run docs:data`"
    )


def test_every_shipped_section_name_reaches_the_docs():
    """Guards the phase-block parser the same way the pack test guards the group one."""
    shipped = load_config(environ={}).phases
    listed = {row["section"]: row["phase"] for row in build_phases()}
    assert listed == shipped


def test_every_shipped_pack_reaches_the_catalogue():
    """Guards the generator itself: a parse bug would silently drop packs.

    The regex that reads the `# --- Group ---` headers cannot see a pack whose
    `- id:` line is formatted unusually, and the table would just be short.
    """
    shipped = {m.id for m in load_config(environ={}).extraction.matchers}
    listed = {pack["id"] for group in build() for pack in group["packs"]}
    assert listed == shipped, f"catalogue misses {sorted(shipped - listed)}"
