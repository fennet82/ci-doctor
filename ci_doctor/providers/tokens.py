"""API token resolution, identical for every provider.

A file first, then the environment. The file is what a secret manager gives you
— a Kubernetes projected volume, a Vault agent sidecar, a Docker secret — and it
wins because a mounted secret is deliberate, while the env var is often inherited
from the runner. A missing file is a warning and a fallback, never a failure:
plenty of instances serve a public project unauthenticated.

Lives here rather than in `core/` only because it is I/O; it names no provider.
"""

import logging
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger("ci_doctor.tokens")


def read_token(token_file: str | None, token_env: str, environ: Mapping[str, str]) -> str | None:
    """Resolve an API token from its file, else from the environment.

    Args:
        token_file: Path to a file holding the token, or None.
        token_env: Name of the env var holding it.
        environ: The environment to read.

    Returns:
        The token, or None when neither source has one — which is not an error,
        a public project still works unauthenticated.
    """
    if token_file:
        path = Path(token_file)
        if path.is_file():
            return path.read_text().strip()
        log.warning("token_file %s not found; falling back to env", path)
    return environ.get(token_env)
