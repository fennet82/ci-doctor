"""Assign a Phase to each Section from the config section->phase map.

Provider-neutral: it only sees section names and the map (which ships per provider
in defaults.yml). Unknown section names inherit the nearest enclosing *known*
phase, else SCRIPT. Synthetic preamble/trailer stay UNKNOWN — the classifier
handles them specially.
"""

from ci_doctor.core.models import Phase, Section

_SYNTHETIC = {"__preamble__", "__trailer__"}


def assign_phases(sections: list[Section], phase_map: dict[str, str]) -> None:
    """Set `.phase` on every section in the tree, in place.

    Args:
        sections: Top-level sections from a segmenter. Mutated.
        phase_map: Section name -> phase name, from `config.phases`.
    """
    for sec in sections:
        _assign(sec, phase_map, inherited=None)


def _assign(sec: Section, phase_map: dict[str, str], inherited: Phase | None) -> None:
    """Assign one section's phase and recurse into its children.

    Args:
        sec: The section to assign. Mutated.
        phase_map: Section name -> phase name.
        inherited: Nearest enclosing *mapped* phase, or None at the top level.
            An unknown section name takes this, falling back to SCRIPT — an
            unrecognised block is far more often the user's command than infra.
    """
    mapped = phase_map.get(sec.name)
    if mapped is not None:
        sec.phase = Phase(mapped)
        child_inherited = sec.phase
    elif sec.name in _SYNTHETIC:
        sec.phase = Phase.UNKNOWN
        child_inherited = inherited
    else:
        sec.phase = inherited if inherited is not None else Phase.SCRIPT
        child_inherited = inherited
    for child in sec.children:
        _assign(child, phase_map, child_inherited)
