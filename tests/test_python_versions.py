"""The supported Python versions are declared in three files, so they must agree.

`pyproject.toml` classifiers are the claim users read, `ci.yml` is what actually
proves it, and the `test:matrix` mise task is the local rehearsal of the same thing.
Adding a version to one and not the others is silent: the classifier promises 3.15,
nothing tests it, and the first user on 3.15 finds out. This pins them together.
"""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

#: "Programming Language :: Python :: 3.11" -> "3.11". Only the X.Y classifiers;
#: the bare "Programming Language :: Python :: 3" one, if ever added, is not a version.
_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (\d+\.\d+)$")


def _classifier_versions() -> list[str]:
    """The versions pyproject.toml advertises to PyPI."""
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    found = (_CLASSIFIER.match(c) for c in meta["project"]["classifiers"])
    return [m.group(1) for m in found if m]


def _ci_matrix_versions() -> list[str]:
    """The versions the `test` job in ci.yml actually runs."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    return list(workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"])


def _mise_task_versions() -> list[str]:
    """The versions the `test:matrix` task loops over."""
    config = tomllib.loads((ROOT / ".mise.toml").read_text())
    body = config["tasks"]["test:matrix"]["run"]
    loop = re.search(r"for v in ([\d. ]+); do", body)
    assert loop, "test:matrix no longer loops over a literal version list; update this test"
    return loop.group(1).split()


def test_ci_matrix_matches_the_classifiers():
    """A version claimed on PyPI is a version CI has to prove."""
    assert _ci_matrix_versions() == _classifier_versions()


def test_mise_task_matches_the_ci_matrix():
    """`mise run test:matrix` is meant to be the local rehearsal of the CI job."""
    assert _mise_task_versions() == _ci_matrix_versions()


def test_requires_python_matches_the_lowest_claimed_version():
    """requires-python is the floor pip enforces; the classifiers are the claim."""
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    # Keyed numerically, not lexically: "3.9" sorts above "3.11" as a string.
    lowest = min(_classifier_versions(), key=lambda v: tuple(int(p) for p in v.split(".")))
    assert meta["project"]["requires-python"] == f">={lowest}"
