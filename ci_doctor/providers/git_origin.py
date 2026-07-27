"""Repository identity from the local git checkout.

ci-doctor also runs on a laptop, where none of a CI system's predefined variables
exist. Rather than making the user export `GITHUB_REPOSITORY` / `CI_PROJECT_ID`
by hand, adapters fall back to whatever `origin` points at — and say so, loudly,
because a guessed repository is worth one warning line.

Shared by every adapter, so the guess is parsed once and identically. Lives here
rather than in `core/` only because it is I/O; it names no provider.
"""

from __future__ import annotations

import logging
import subprocess
from functools import lru_cache
from urllib.parse import urlsplit

log = logging.getLogger("ci_doctor.git")


@lru_cache(maxsize=None)
def origin_repo(env_var: str) -> str | None:
    """Derive "owner/name" (or "group/sub/project") from the `origin` remote.

    Args:
        env_var: Name of the variable that should have carried this, used in the
            warning so the user knows what to set to silence it.

    Returns:
        The repository path, or None outside a checkout / with no usable origin.
        Cached: the answer cannot change mid-run, and neither should the warning
        repeat once per job log.
    """
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:  # no git, no repo, hung command
        log.debug("could not read git origin: %s", exc)
        return None
    url = proc.stdout.strip().removesuffix(".git")
    # Two shapes: "https://host/group/project" and scp-style "git@host:group/project".
    # Splitting on the path (not a regex over the tail) keeps GitLab's nested groups.
    path = urlsplit(url).path if "://" in url else url.partition(":")[2]
    repo = path.strip("/")
    if not repo:
        return None
    log.warning("%s is not set; using %r from git origin", env_var, repo)
    return repo
