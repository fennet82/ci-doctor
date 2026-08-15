"""Which adapter, and which segmenter, serve a vendor name.

One place to register a provider, so adding one is a line here rather than an
edit inside a command. Both tables map a name to a dotted path and import it
only when that name is actually asked for: `core/`, the offline replay path and
`ci-doctor config` must never drag a vendor SDK into the process.

The two tables are keyed differently on purpose. Adapters are keyed by vendor
because a vendor may serve as the CI system, the git host, or both. Segmenters
are keyed by **CI system only** — log framing is whatever the *runner* printed,
which is also why one segmenter can serve several CI systems (Forgejo and Gitea
Actions both run act_runner, which emits GitHub's `##[group]` markers).
"""

import logging
from importlib import import_module
from typing import Any

from ci_doctor.config.schema import Config
from ci_doctor.core.ports import CIProvider, LogSegmenter, SCMProvider

log = logging.getLogger("ci_doctor.providers")

#: Vendor name -> adapter. Which ports it implements is discovered from the class,
#: not declared here: a vendor that is both a CI system and a git host (GitLab,
#: GitHub) implements both over one client.
ADAPTERS: dict[str, str] = {
    "gitlab": "ci_doctor.providers.gitlab.provider:GitLabProvider",
    "github": "ci_doctor.providers.github.provider:GitHubProvider",
}

#: CI system -> segmenter for the log format its runner emits.
SEGMENTERS: dict[str, str] = {
    "gitlab": "ci_doctor.providers.gitlab.segmenter:GitLabSegmenter",
    "github": "ci_doctor.providers.github.segmenter:GitHubSegmenter",
}

#: Segmenter used when the configured CI system has none of its own — offline
#: replay of a bare log file has no run metadata to go on.
_FALLBACK_SEGMENTER = SEGMENTERS["gitlab"]


def _load(spec: str) -> Any:  # noqa: ANN401 - an import resolved by name cannot be typed
    """Import a `"module:attribute"` target.

    Args:
        spec: A dotted module path, a colon, and the attribute name.

    Returns:
        The attribute. Imported here and not at module scope so a vendor's SDK
        is only ever loaded by a run that actually reaches that vendor.
    """
    module, _, name = spec.partition(":")
    return getattr(import_module(module), name)


def make_adapter(vendor: str, cfg: Config) -> CIProvider | SCMProvider | None:
    """Build the adapter for one vendor, whichever ports it implements.

    Args:
        vendor: The vendor name from `ci` or `scm`.
        cfg: The effective config.

    Returns:
        The adapter, or None when no adapter exists for that name. The caller
        checks which ports it got.
    """
    spec = ADAPTERS.get(vendor)
    return _load(spec)(cfg) if spec else None


def make_ci_provider(cfg: Config) -> CIProvider:
    """Build the adapter that reads the run, named by `config.ci`.

    Args:
        cfg: The effective config.

    Returns:
        A :class:`~ci_doctor.core.ports.CIProvider`.

    Raises:
        ValueError: When no adapter reads that CI system.
    """
    adapter = make_adapter(cfg.ci, cfg)
    if not isinstance(adapter, CIProvider):
        raise ValueError(f"unsupported CI system: {cfg.ci}")
    return adapter


def make_scm_provider(cfg: Config, ci_provider: object = None) -> SCMProvider | None:
    """Build the adapter that posts the note, named by `config.scm`.

    Args:
        cfg: The effective config.
        ci_provider: The already-connected CI adapter. Reused when the same
            vendor serves both roles — GitLab's pipelines and merge requests are
            one API behind one client, and connecting twice would mean a second
            token read and a second version probe for nothing.

    Returns:
        An :class:`~ci_doctor.core.ports.SCMProvider`, or None when the note has
        nowhere to go — which is not an error, the report still lands in the
        terminal and the artifacts.
    """
    vendor = cfg.scm_vendor
    if vendor == "none":
        return None
    if vendor == cfg.ci and isinstance(ci_provider, SCMProvider):
        return ci_provider
    adapter = make_adapter(vendor, cfg)
    if not isinstance(adapter, SCMProvider):
        log.debug("no git-host adapter for %r; the note has nowhere to go", vendor)
        return None
    return adapter


def segmenter_for(ci: str) -> LogSegmenter:
    """Build the log segmenter for one CI system, by name.

    Args:
        ci: The CI system's name, matching a `SEGMENTERS` key.

    Returns:
        A :class:`~ci_doctor.core.ports.LogSegmenter`, defaulting to GitLab's —
        offline replay of a bare log has no run metadata to go on.
    """
    return _load(SEGMENTERS.get(ci, _FALLBACK_SEGMENTER))()


def make_segmenter(cfg: Config) -> LogSegmenter:
    """Build the log segmenter for the configured CI system.

    Args:
        cfg: The effective config. Read for `ci` and never for `scm`: the log was
            framed by whoever *ran* the job, not by wherever the code lives.

    Returns:
        A :class:`~ci_doctor.core.ports.LogSegmenter`.
    """
    return segmenter_for(cfg.ci)
