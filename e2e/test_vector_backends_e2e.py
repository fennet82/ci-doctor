"""Port contract against each live vector store.

The add/search/filter/delete sequence every backend must honour. Reads are wrapped in
:func:`_eventually` because most stores index asynchronously.
"""

import time
from collections.abc import Callable
from typing import TypeVar

import pytest

from ci_doctor.core.models import VectorRecord
from ci_doctor.core.ports import VectorStore

pytestmark = pytest.mark.e2e

T = TypeVar("T")

#: Must match the dimension the ``store`` fixture creates the collection with (conftest).
DIM = 8


def _vec(*head: float) -> list[float]:
    """A DIM-length vector from the given leading components, zero-padded."""
    return [*head, *([0.0] * (DIM - len(head)))]


#: Three records: a and c are near [1,0,0]; b is orthogonal. Ids are arbitrary strings
#: on purpose — the contract preserves them whatever a backend uses as its key.
_RECORDS = [
    VectorRecord("run-a", _vec(1.0, 0.0, 0.0), {"repo": "x", "branch": "main"}),
    VectorRecord("run-b", _vec(0.0, 1.0, 0.0), {"repo": "y", "branch": "main"}),
    VectorRecord("run-c", _vec(0.9, 0.1, 0.0), {"repo": "x", "branch": "dev"}),
]


def _eventually(fn: Callable[[], T], timeout: float = 30.0) -> T:
    """Retry ``fn`` until it stops raising, then re-raise at timeout — covers indexing lag."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - retry any transient indexing/consistency error
            last = e
            time.sleep(1.0)
    raise AssertionError(f"condition not met within {timeout:.0f}s") from last


def test_search_returns_nearest_first_with_original_ids(store: VectorStore) -> None:
    """The nearest vectors come back, in order, reporting the ids they were stored under."""
    store.add(_RECORDS)

    def _check() -> None:
        hits = store.search(_vec(1.0, 0.0, 0.0), k=2)
        assert [h.id for h in hits] == ["run-a", "run-c"], [h.id for h in hits]
        assert hits[0].score >= hits[1].score
        assert hits[0].payload.get("repo") == "x"

    _eventually(_check)


def test_filter_narrows_results(store: VectorStore) -> None:
    """An equality filter on metadata restricts the hits to the matching payloads."""
    store.add(_RECORDS)

    def _check() -> None:
        hits = store.search(_vec(0.0, 1.0, 0.0), k=5, filter={"repo": "y"})
        assert [h.id for h in hits] == ["run-b"], [h.id for h in hits]

    _eventually(_check)


def test_delete_removes_a_record(store: VectorStore) -> None:
    """A deleted id stops appearing in results."""
    store.add(_RECORDS)
    _eventually(lambda: _assert_present(store, "run-a"))

    store.delete(["run-a"])

    def _gone() -> None:
        ids = {h.id for h in store.search(_vec(1.0, 0.0, 0.0), k=5)}
        assert "run-a" not in ids, ids

    _eventually(_gone)


def _assert_present(store: VectorStore, record_id: str) -> None:
    """Assert a record id is currently searchable (used to confirm a write landed)."""
    ids = {h.id for h in store.search(_vec(1.0, 0.0, 0.0), k=5)}
    assert record_id in ids, ids


def test_pgvector_survives_concurrent_access(pgvector_store: VectorStore) -> None:
    """Concurrent writers and readers don't error or lose data (validates the pgvector pool)."""
    from concurrent.futures import ThreadPoolExecutor

    store = pgvector_store
    records = [VectorRecord(f"c-{i}", _vec(1.0, i * 0.001, 0.0), {"repo": "x"}) for i in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda r: store.add([r]), records))
        searches = list(pool.map(lambda _: store.search(_vec(1.0, 0.0, 0.0), k=5), range(24)))

    assert all(isinstance(hits, list) for hits in searches)
    ids = {h.id for h in store.search(_vec(1.0, 0.0, 0.0), k=100)}
    missing = {r.id for r in records} - ids
    assert not missing, missing
