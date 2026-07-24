"""Air-gap guard: no test may open a real network connection.

Everything is exercised against in-memory doubles, so any real socket connect is
a bug (a dependency that quietly started phoning home — exactly the regression
that would surface only in the one environment where it can't be debugged).
"""

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise RuntimeError("network access is not allowed in tests (air-gap guarantee)")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
