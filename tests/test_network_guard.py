"""Meta-test: proves the air-gap guard itself works."""

import socket

import pytest


def test_real_connections_are_blocked():
    """The autouse guard in conftest actually blocks a real connection attempt."""
    with pytest.raises(RuntimeError, match="network access is not allowed"):
        socket.create_connection(("example.com", 80))
