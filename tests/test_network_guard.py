import socket

import pytest


def test_real_connections_are_blocked():
    # Proves the autouse air-gap guard in conftest is actually active.
    with pytest.raises(RuntimeError, match="network access is not allowed"):
        socket.create_connection(("example.com", 80))
