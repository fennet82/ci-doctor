"""Fixtures for the opt-in vector-store e2e suite.

Outside ``tests/`` so the offline socket-block conftest does not apply — these must
reach the docker containers. A fixture skips when its extra is missing or its service
is down, so a partial boot narrows the run rather than failing it.
"""

import importlib.util
import os
import time
import uuid
from collections.abc import Iterator

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.ports import VectorStore
from ci_doctor.memory.backends import make_store

#: Small dimension keeps the fixtures cheap; the vectors below fill the rest with zeros.
DIM = 8

_PG_DSN = "postgresql://ci_doctor:ci_doctor@localhost:5432/ci_doctor"

#: backend name -> (module that must import for the extra to be present, config overrides).
_BACKENDS: dict[str, tuple[str, dict[str, object]]] = {
    "qdrant": ("qdrant_client", {"backend": "qdrant", "qdrant": {"url": "http://localhost:6333"}}),
    "weaviate": ("weaviate", {"backend": "weaviate", "weaviate": {"url": "http://localhost:8080"}}),
    "pgvector": ("psycopg", {"backend": "pgvector", "pgvector": {"dsn_env": "E2E_PG_DSN"}}),
    "milvus": ("pymilvus", {"backend": "milvus", "milvus": {"url": "http://localhost:19530"}}),
}


def pytest_configure(config: pytest.Config) -> None:
    """Register the e2e marker so ``-m e2e`` works without a warning."""
    config.addinivalue_line("markers", "e2e: exercises a live vector store in docker (opt-in).")


def _wait_ready(store: VectorStore, name: str, timeout: float = 120.0) -> None:
    """Poll ``ensure_collection`` until the service answers (also sets up the collection), or skip."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            store.ensure_collection(DIM)
            return
        except Exception as e:  # noqa: BLE001 - any connection error means "not up yet", so retry
            last = e
            time.sleep(2.0)
    pytest.skip(f"{name}: service not ready within {timeout:.0f}s ({last})")


def _ready_store(name: str) -> Iterator[VectorStore]:
    """Build a ready store on a fresh unique collection, or skip if its extra/service is absent."""
    module, overrides = _BACKENDS[name]
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"{name}: optional extra not installed (uv run --extra {name} …)")
    if name == "pgvector":
        os.environ["E2E_PG_DSN"] = _PG_DSN
    collection = f"e2e_{name}_{uuid.uuid4().hex[:8]}"
    cfg = load_config(
        environ=os.environ,
        overrides={"memory": {**overrides, "enabled": True, "collection": collection, "dimension": DIM}},
    ).memory
    st = make_store(cfg)
    _wait_ready(st, name)
    try:
        yield st
    finally:
        st.close()


@pytest.fixture(params=list(_BACKENDS), ids=list(_BACKENDS))
def store(request: pytest.FixtureRequest) -> Iterator[VectorStore]:
    """A ready store for each backend, on its own fresh collection."""
    yield from _ready_store(request.param)


@pytest.fixture
def pgvector_store() -> Iterator[VectorStore]:
    """A ready pgvector store — for tests specific to that backend."""
    yield from _ready_store("pgvector")
