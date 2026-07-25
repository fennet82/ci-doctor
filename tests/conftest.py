"""Air-gap guard: no test may open a real network connection.

Everything is exercised against in-memory doubles, so any real socket connect is
a bug (a dependency that quietly started phoning home — exactly the regression
that would surface only in the one environment where it can't be debugged).
"""

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Block every real socket connection for the duration of a test.

    Autouse, so no test can opt out by forgetting to request it.

    Args:
        monkeypatch: pytest fixture used to patch the socket module.
    """

    def guard(*args, **kwargs):
        """Stand in for socket connect/create_connection.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("network access is not allowed in tests (air-gap guarantee)")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
