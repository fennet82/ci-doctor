"""Vector-store backends: selection, readiness, lazy imports, and the port contract.

No network and no vendor SDK installed — construction and dispatch are testable
because clients are lazy-importing. The five real backends' I/O is out of scope for
the offline suite (they need a live service); the port contract is proved here against
a small in-test fake, which is the only VectorStore this file ships.
"""

import math
import sys

import pytest
from pydantic import ValidationError

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import VectorHit, VectorRecord
from ci_doctor.core.ports import VectorStore
from ci_doctor.memory.backends import (
    MilvusStore,
    PgVectorStore,
    PineconeStore,
    QdrantStore,
    WeaviateStore,
    make_store,
    store_ready,
)


def _mem(**over):
    """Build a MemoryConfig from the shipped defaults, overridden by kwargs."""
    return load_config(environ={}, overrides={"memory": over}).memory


@pytest.mark.parametrize(
    "backend,cls",
    [
        ("pinecone", PineconeStore),
        ("qdrant", QdrantStore),
        ("milvus", MilvusStore),
        ("weaviate", WeaviateStore),
        ("pgvector", PgVectorStore),
    ],
)
def test_make_store_selects_backend(backend, cls):
    """Each backend name builds its own store class, without importing any SDK."""
    assert isinstance(make_store(_mem(backend=backend)), cls)


def test_unknown_backend_raises():
    """The factory rejects an unknown backend.

    Config-level Literal validation blocks bad values earlier, so the factory is
    exercised directly here.
    """
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="unknown memory.backend"):
        make_store(SimpleNamespace(backend="nope"))


def test_store_ready_rules():
    """Each backend reports ready only when it has its required connection knobs."""
    assert store_ready(_mem(backend="pinecone", pinecone={"api_key_env": "PC_KEY"})) is True
    assert store_ready(_mem(backend="pinecone")) is False  # needs an api key env
    assert store_ready(_mem(backend="qdrant", qdrant={"url": "http://localhost:6333"})) is True
    assert store_ready(_mem(backend="qdrant")) is False  # needs a url
    assert store_ready(_mem(backend="milvus", milvus={"url": "http://localhost:19530"})) is True
    assert store_ready(_mem(backend="milvus")) is False  # needs a url
    assert store_ready(_mem(backend="weaviate", weaviate={"url": "http://localhost:8080"})) is True
    assert store_ready(_mem(backend="weaviate")) is False  # needs a url
    assert store_ready(_mem(backend="pgvector", pgvector={"dsn_env": "PG_DSN"})) is True
    assert store_ready(_mem(backend="pgvector", pgvector={"dsn_file": "/run/secrets/dsn"})) is True
    assert store_ready(_mem(backend="pgvector")) is False  # needs a dsn
    assert store_ready(_mem()) is False  # no backend selected


def test_importing_the_module_pulls_in_no_vendor_sdk():
    """Importing the registry must not drag any vector-DB SDK into the process.

    That guarantee is what keeps the base install lean and air-gap-clean — the SDKs
    live behind optional extras and are imported only inside the call that needs them.
    """
    import ci_doctor.memory.backends  # noqa: F401 - imported for its side effects on sys.modules

    for sdk in ("pinecone", "qdrant_client", "pymilvus", "weaviate", "psycopg", "pgvector"):
        assert sdk not in sys.modules, f"{sdk} was imported at module load"


def test_enabled_requires_a_backend():
    """An enabled store with no backend is a hard config error, not a silent default."""
    with pytest.raises(ValidationError, match="memory.backend is not set"):
        load_config(environ={}, overrides={"memory": {"enabled": True}})


def test_enabled_with_a_backend_is_valid():
    """Enabling a store with a backend chosen validates."""
    cfg = load_config(environ={}, overrides={"memory": {"enabled": True, "backend": "qdrant"}}).memory
    assert cfg.enabled and cfg.backend == "qdrant"


def test_memory_is_off_by_default():
    """The store does nothing on a normal run: disabled, no backend."""
    cfg = load_config(environ={}).memory
    assert cfg.enabled is False
    assert cfg.backend is None


def test_close_is_safe_without_a_connection():
    """close() before any call is a no-op — no lazy connection is opened to close."""
    store = make_store(_mem(backend="qdrant", qdrant={"url": "http://localhost:6333"}))
    store.close()  # must not raise, and must not import the SDK to do nothing
    assert "qdrant_client" not in sys.modules


# --- Port contract, proved against an in-test fake VectorStore -------------------


def _cos(a, b):
    """Cosine similarity between two equal-length vectors; 0 when either is a zero vector."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class _FakeStore(VectorStore):
    """A minimal, offline VectorStore for exercising the port contract."""

    def __init__(self):
        """Start empty."""
        self.records: dict[str, VectorRecord] = {}
        self.dimension: int | None = None

    def ensure_collection(self, dimension):
        """Record the fixed dimension."""
        self.dimension = dimension

    def add(self, records):
        """Upsert by id."""
        for r in records:
            self.records[r.id] = r

    def search(self, vector, k, filter=None):
        """Linear cosine scan, honouring an equality filter, nearest first."""
        items = [
            r
            for r in self.records.values()
            if not filter or all(r.payload.get(key) == val for key, val in filter.items())
        ]
        ranked = sorted(items, key=lambda r: _cos(vector, r.vector), reverse=True)
        return [VectorHit(r.id, _cos(vector, r.vector), r.payload) for r in ranked[:k]]

    def delete(self, ids):
        """Drop ids; missing ones are ignored."""
        for i in ids:
            self.records.pop(i, None)


def _seed():
    """A fake store holding three orthogonal-ish vectors with metadata."""
    store = _FakeStore()
    store.ensure_collection(3)
    store.add(
        [
            VectorRecord("a", [1.0, 0.0, 0.0], {"repo": "x"}),
            VectorRecord("b", [0.0, 1.0, 0.0], {"repo": "y"}),
            VectorRecord("c", [0.9, 0.1, 0.0], {"repo": "x"}),
        ]
    )
    return store


def test_ensure_collection_records_dimension():
    """ensure_collection fixes the store's dimension."""
    store = _FakeStore()
    store.ensure_collection(3)
    assert store.dimension == 3


def test_search_returns_nearest_first():
    """The closest vector ranks first, by descending score."""
    hits = _seed().search([1.0, 0.0, 0.0], k=2)
    assert [h.id for h in hits] == ["a", "c"]
    assert hits[0].score >= hits[1].score
    assert hits[0].payload == {"repo": "x"}


def test_search_k_caps_results():
    """The k argument bounds the number of hits."""
    assert len(_seed().search([1.0, 0.0, 0.0], k=1)) == 1


def test_search_on_empty_store_returns_empty():
    """A query against a store with nothing in it yields no hits, not an error."""
    store = _FakeStore()
    store.ensure_collection(3)
    assert store.search([1.0, 0.0, 0.0], k=5) == []


def test_filter_narrows_results():
    """An equality filter excludes non-matching payloads."""
    hits = _seed().search([0.0, 1.0, 0.0], k=5, filter={"repo": "y"})
    assert [h.id for h in hits] == ["b"]


def test_delete_removes_records():
    """A deleted id no longer appears in results."""
    store = _seed()
    store.delete(["a"])
    assert "a" not in {h.id for h in store.search([1.0, 0.0, 0.0], k=5)}


def test_add_upserts_by_id():
    """Re-adding an id overwrites rather than duplicating."""
    store = _seed()
    store.add([VectorRecord("a", [0.0, 0.0, 1.0], {"repo": "z"})])
    hits = store.search([0.0, 0.0, 1.0], k=1)
    assert hits[0].id == "a" and hits[0].payload == {"repo": "z"}
