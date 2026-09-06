"""Vector-store backends.

Each backend implements the VectorStore port; ``memory.backend`` selects one. Like
``llm/backends.py``, every vendor SDK is imported inside the call that needs it, so the
base install pulls in none of them and each is an optional extra (``ci-doctorr[qdrant]``).
The port speaks raw vectors; embedding is the caller's concern. The offline suite covers
selection, readiness and the lazy-import guarantee; live I/O is proved by the e2e suite.
"""

import os
import time
import uuid
from abc import abstractmethod
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import Any

from ci_doctor.config.schema import MemoryConfig
from ci_doctor.core.models import VectorHit, VectorRecord
from ci_doctor.core.ports import VectorStore

#: Qdrant/Weaviate key objects by UUID, so an arbitrary caller id is hashed to one and
#: kept here in the payload — that's how a hit reports the id that was stored.
_SOURCE_ID = "_source_id"


def _stable_uuid(raw: str) -> str:
    """Hash an arbitrary id to a deterministic UUID these stores accept as a key."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


def make_store(cfg: MemoryConfig, environ: Mapping[str, str] | None = None) -> VectorStore:
    """Build the store for ``cfg.backend``; ValueError on an unknown or unset name."""
    if cfg.backend == "pinecone":
        return PineconeStore(cfg, environ=environ)
    if cfg.backend == "qdrant":
        return QdrantStore(cfg, environ=environ)
    if cfg.backend == "milvus":
        return MilvusStore(cfg, environ=environ)
    if cfg.backend == "weaviate":
        return WeaviateStore(cfg, environ=environ)
    if cfg.backend == "pgvector":
        return PgVectorStore(cfg, environ=environ)
    raise ValueError(f"unknown memory.backend: {cfg.backend}")


def store_ready(cfg: MemoryConfig) -> bool:
    """Whether the selected backend has the config it needs to connect.

    Mirrors ``llm.backend_ready``: capability only — gating on ``cfg.enabled`` is the
    caller's job.
    """
    if cfg.backend == "pinecone":
        return bool(cfg.pinecone.api_key_env)
    if cfg.backend == "qdrant":
        return bool(cfg.qdrant.url)
    if cfg.backend == "milvus":
        return bool(cfg.milvus.url)
    if cfg.backend == "weaviate":
        return bool(cfg.weaviate.url)
    if cfg.backend == "pgvector":
        return bool(cfg.pgvector.dsn_env or cfg.pgvector.dsn_file)
    return False


class _BaseVectorStore(VectorStore):
    """Config, env, and a memoized lock-guarded client.

    The client is shared across the analysis thread pool, so it must be safe for
    concurrent use — true for HTTP-pooled SDKs; a raw DB connection must defend itself
    (see PgVectorStore).
    """

    def __init__(self, cfg: MemoryConfig, environ: Mapping[str, str] | None = None) -> None:
        """Store config; no connection opens until the first call."""
        self.cfg = cfg
        self.environ = os.environ if environ is None else environ
        self._sdk: Any = None
        self._sdk_lock = Lock()

    def _secret(self, env_name: str | None) -> str | None:
        """Read a secret named by an env var (never stored in config)."""
        return self.environ.get(env_name) if env_name else None

    def _client(self) -> Any:  # noqa: ANN401 - the vendor SDK object has no shared static type
        """Build the client once and reuse it for the whole run."""
        with self._sdk_lock:
            if self._sdk is None:
                self._sdk = self._connect()
        return self._sdk

    @abstractmethod
    def _connect(self) -> Any:  # noqa: ANN401 - the vendor SDK object has no shared static type
        """Lazy-import the SDK and open the connection; called once under the lock."""

    def close(self) -> None:
        """Close the memoized client if it holds a live connection. Safe to repeat."""
        with self._sdk_lock:
            closer = getattr(self._sdk, "close", None) if self._sdk is not None else None
            if callable(closer):
                closer()
            self._sdk = None


class PineconeStore(_BaseVectorStore):
    """Pinecone (managed, cloud). Needs an API key."""

    def _connect(self) -> Any:  # noqa: ANN401 - the vendor SDK object has no shared static type
        """Build the Pinecone client from the API-key env var."""
        from pinecone import Pinecone  # ty: ignore[unresolved-import]

        return Pinecone(
            api_key=self._secret(self.cfg.pinecone.api_key_env),
            ssl_ca_certs=self.cfg.ca_bundle,
            ssl_verify=self.cfg.verify_ssl,
        )

    def _index(self) -> Any:  # noqa: ANN401 - the vendor SDK object has no shared static type
        """Handle to the configured index."""
        return self._client().Index(self.cfg.collection)

    def ensure_collection(self, dimension: int) -> None:
        """Create the serverless index if absent, waiting until it is queryable."""
        from pinecone import ServerlessSpec  # ty: ignore[unresolved-import]

        pc = self._client()
        if pc.has_index(self.cfg.collection):
            return
        pc.create_index(
            name=self.cfg.collection,
            dimension=dimension,
            metric="cosine",
            spec=ServerlessSpec(cloud=self.cfg.pinecone.cloud, region=self.cfg.pinecone.region),
        )
        # Serverless creation is async; upserts fail until the index is ready.
        deadline = time.monotonic() + self.cfg.timeout_seconds
        while not pc.describe_index(self.cfg.collection).status.get("ready", False):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Pinecone index {self.cfg.collection!r} not ready within timeout")
            time.sleep(1.0)

    def add(self, records: list[VectorRecord]) -> None:
        """Upsert vectors with metadata."""
        self._index().upsert(
            vectors=[{"id": r.id, "values": r.vector, "metadata": r.payload} for r in records]
        )

    def search(self, vector: list[float], k: int, filter: dict[str, Any] | None = None) -> list[VectorHit]:
        """Query the k nearest; an equality filter maps to Pinecone's operators."""
        flt = {key: {"$eq": val} for key, val in filter.items()} if filter else None
        res = self._index().query(vector=vector, top_k=k, include_metadata=True, filter=flt)
        return [VectorHit(m.id, m.score, dict(m.metadata or {})) for m in res.matches]

    def delete(self, ids: list[str]) -> None:
        """Delete by id."""
        self._index().delete(ids=ids)


class QdrantStore(_BaseVectorStore):
    """Qdrant (self-hosted or cloud). Needs a URL."""

    def _connect(self) -> Any:  # noqa: ANN401 - the vendor SDK object has no shared static type
        """Build the Qdrant client from the URL and optional API key."""
        from qdrant_client import QdrantClient  # ty: ignore[unresolved-import]

        # qdrant-client forwards extra kwargs to its httpx client, whose `verify`
        # takes a CA-bundle path or a bool.
        return QdrantClient(
            url=self.cfg.qdrant.url,
            api_key=self._secret(self.cfg.qdrant.api_key_env),
            timeout=self.cfg.timeout_seconds,
            verify=self.cfg.ca_bundle or self.cfg.verify_ssl,
        )

    def ensure_collection(self, dimension: int) -> None:
        """Create a cosine collection if absent."""
        from qdrant_client.models import Distance, VectorParams  # ty: ignore[unresolved-import]

        c = self._client()
        if not c.collection_exists(self.cfg.collection):
            c.create_collection(
                self.cfg.collection, vectors_config=VectorParams(size=dimension, distance=Distance.COSINE)
            )

    def add(self, records: list[VectorRecord]) -> None:
        """Upsert points; ids are hashed to a UUID key and kept in the payload."""
        from qdrant_client.models import PointStruct  # ty: ignore[unresolved-import]

        self._client().upsert(
            self.cfg.collection,
            points=[
                PointStruct(id=_stable_uuid(r.id), vector=r.vector, payload={**r.payload, _SOURCE_ID: r.id})
                for r in records
            ],
        )

    def search(self, vector: list[float], k: int, filter: dict[str, Any] | None = None) -> list[VectorHit]:
        """Query the k nearest; an equality filter maps to a Qdrant Filter."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue  # ty: ignore[unresolved-import]

        flt = (
            Filter(must=[FieldCondition(key=key, match=MatchValue(value=val)) for key, val in filter.items()])
            if filter
            else None
        )
        res = self._client().query_points(
            self.cfg.collection, query=vector, limit=k, query_filter=flt, with_payload=True
        )
        hits = []
        for p in res.points:
            payload = dict(p.payload or {})
            hits.append(VectorHit(payload.pop(_SOURCE_ID, str(p.id)), p.score, payload))
        return hits

    def delete(self, ids: list[str]) -> None:
        """Delete points by id."""
        self._client().delete(self.cfg.collection, points_selector=[_stable_uuid(i) for i in ids])


class MilvusStore(_BaseVectorStore):
    """Milvus / Zilliz. Needs a URI."""

    def _connect(self) -> Any:  # noqa: ANN401 - the vendor SDK object has no shared static type
        """Build the Milvus client from the URI and optional token."""
        from pymilvus import MilvusClient  # ty: ignore[unresolved-import]

        tls: dict[str, Any] = (
            {"secure": True, "ca_pem_path": self.cfg.ca_bundle} if self.cfg.ca_bundle else {}
        )
        return MilvusClient(uri=self.cfg.milvus.url, token=self._secret(self.cfg.milvus.api_key_env), **tls)

    def ensure_collection(self, dimension: int) -> None:
        """Create a cosine collection with a string primary key and dynamic fields if absent."""
        from pymilvus import DataType  # ty: ignore[unresolved-import]

        c = self._client()
        if not c.has_collection(self.cfg.collection):
            # VARCHAR primary key: caller ids are arbitrary strings, not Milvus's default INT64.
            c.create_collection(
                self.cfg.collection,
                dimension=dimension,
                metric_type="COSINE",
                id_type=DataType.VARCHAR,
                max_length=512,
            )

    def add(self, records: list[VectorRecord]) -> None:
        """Upsert rows; payload keys ride as dynamic fields."""
        # ponytail: a payload key named "id" or "vector" would clobber the primary/vector field.
        self._client().upsert(
            self.cfg.collection,
            data=[{"id": r.id, "vector": r.vector, **r.payload} for r in records],
        )

    def search(self, vector: list[float], k: int, filter: dict[str, Any] | None = None) -> list[VectorHit]:
        """Query the k nearest; an equality filter maps to a Milvus expr string."""
        expr = (
            " and ".join(
                f'{key} == "{val}"' if isinstance(val, str) else f"{key} == {val}"
                for key, val in filter.items()
            )
            if filter
            else ""
        )
        res = self._client().search(
            self.cfg.collection, data=[vector], limit=k, filter=expr, output_fields=["*"]
        )
        # search is batched over query vectors; res[0] is the hit list for our single query.
        # Milvus COSINE "distance" is already a similarity (higher = nearer), so it is the score.
        return [VectorHit(str(h["id"]), float(h["distance"]), dict(h.get("entity") or {})) for h in res[0]]

    def delete(self, ids: list[str]) -> None:
        """Delete rows by id."""
        self._client().delete(self.cfg.collection, ids=ids)


class WeaviateStore(_BaseVectorStore):
    """Weaviate v4. Needs a URL.

    Objects are keyed by UUID, so the caller id is hashed for storage and kept in a
    reserved property, so a hit reports the id that was stored.
    """

    #: Property carrying the caller's original id.
    _ID_PROP = "_source_id"
    #: Name self_provided() gives the single vector; add() targets it explicitly.
    _VECTOR_NAME = "default"

    def _connect(self) -> Any:  # noqa: ANN401 - the vendor SDK object has no shared static type
        """Connect to a custom Weaviate endpoint parsed from the URL."""
        from urllib.parse import urlparse

        import weaviate  # ty: ignore[unresolved-import]
        from weaviate.classes.init import Auth  # ty: ignore[unresolved-import]

        u = urlparse(self.cfg.weaviate.url or "")
        secure = u.scheme == "https"
        port = u.port or (443 if secure else 8080)
        key = self._secret(self.cfg.weaviate.api_key_env)
        return weaviate.connect_to_custom(
            http_host=u.hostname,
            http_port=port,
            http_secure=secure,
            grpc_host=self.cfg.weaviate.grpc_host or u.hostname,
            grpc_port=self.cfg.weaviate.grpc_port,
            grpc_secure=secure,
            auth_credentials=Auth.api_key(key) if key else None,
        )

    @staticmethod
    def _uuid(raw: str) -> str:
        """Map an arbitrary id to a stable UUID Weaviate accepts."""
        from weaviate.util import generate_uuid5  # ty: ignore[unresolved-import]

        return generate_uuid5(raw)

    def ensure_collection(self, dimension: int) -> None:
        """Create a self-provided-vector cosine collection if absent (dimension is inferred)."""
        from weaviate.classes.config import Configure, VectorDistances  # ty: ignore[unresolved-import]

        client = self._client()
        if not client.collections.exists(self.cfg.collection):
            client.collections.create(
                self.cfg.collection,
                vector_config=Configure.Vectors.self_provided(
                    vector_index_config=Configure.VectorIndex.hnsw(distance_metric=VectorDistances.COSINE),
                ),
            )

    def add(self, records: list[VectorRecord]) -> None:
        """Batch-insert objects with caller-supplied vectors, keeping the original id."""
        coll = self._client().collections.get(self.cfg.collection)
        with coll.batch.dynamic() as batch:
            for r in records:
                batch.add_object(
                    properties={**r.payload, self._ID_PROP: r.id},
                    uuid=self._uuid(r.id),
                    vector={self._VECTOR_NAME: r.vector},  # named-vector config keys the vector by name
                )

    def search(self, vector: list[float], k: int, filter: dict[str, Any] | None = None) -> list[VectorHit]:
        """Near-vector query; an equality filter maps to Weaviate Filters."""
        from weaviate.classes.query import Filter, MetadataQuery  # ty: ignore[unresolved-import]

        flt = None
        if filter:
            conds = [Filter.by_property(key).equal(val) for key, val in filter.items()]
            flt = conds[0] if len(conds) == 1 else Filter.all_of(conds)
        coll = self._client().collections.get(self.cfg.collection)
        res = coll.query.near_vector(
            near_vector=vector, limit=k, filters=flt, return_metadata=MetadataQuery(distance=True)
        )
        hits = []
        for o in res.objects:
            payload = dict(o.properties)
            hit_id = payload.pop(self._ID_PROP, str(o.uuid))  # report the caller's id, not the UUID
            hits.append(VectorHit(hit_id, 1.0 - (o.metadata.distance or 0.0), payload))
        return hits

    def delete(self, ids: list[str]) -> None:
        """Delete objects by their mapped UUIDs."""
        from weaviate.classes.query import Filter  # ty: ignore[unresolved-import]

        coll = self._client().collections.get(self.cfg.collection)
        coll.data.delete_many(where=Filter.by_id().contains_any([self._uuid(i) for i in ids]))


class PgVectorStore(_BaseVectorStore):
    """Postgres + pgvector. Self-hosted, the most air-gap-friendly option. Needs a DSN.

    Backed by a connection pool: one connection is not safe for the concurrent access the
    analysis thread pool makes, so every call borrows one. The pool is the memoized client.
    """

    def _dsn(self) -> str | None:
        """Resolve the DSN: dsn_file wins over dsn_env; neither is stored in config."""
        if self.cfg.pgvector.dsn_file:
            return Path(self.cfg.pgvector.dsn_file).read_text().strip()
        return self._secret(self.cfg.pgvector.dsn_env)

    def _connect(self) -> Any:  # noqa: ANN401 - the vendor SDK object has no shared static type
        """Open a connection pool; every pooled connection gets the vector type registered."""
        from pgvector.psycopg import register_vector  # ty: ignore[unresolved-import]
        from psycopg_pool import ConnectionPool  # ty: ignore[unresolved-import]

        def _configure(conn: Any) -> None:  # noqa: ANN401 - a psycopg connection, untyped without the extra
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
            register_vector(conn)  # registers the vector adapter per connection

        return ConnectionPool(
            conninfo=self._dsn(),
            min_size=1,
            max_size=16,  # generous ceiling; the pool grows on demand and blocks only beyond it
            configure=_configure,
            timeout=self.cfg.timeout_seconds,
            open=True,
        )

    def ensure_collection(self, dimension: int) -> None:
        """Create the vectors table if absent."""
        from psycopg import sql  # ty: ignore[unresolved-import]

        with self._client().connection() as conn:
            conn.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {tbl} (id text primary key, embedding vector({dim}), payload jsonb)"
                ).format(tbl=sql.Identifier(self.cfg.collection), dim=sql.Literal(dimension))
            )

    def add(self, records: list[VectorRecord]) -> None:
        """Upsert rows by id."""
        from pgvector import Vector  # ty: ignore[unresolved-import]
        from psycopg import sql  # ty: ignore[unresolved-import]
        from psycopg.types.json import Jsonb  # ty: ignore[unresolved-import]

        stmt = sql.SQL(
            "INSERT INTO {tbl} (id, embedding, payload) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, payload = EXCLUDED.payload"
        ).format(tbl=sql.Identifier(self.cfg.collection))
        with self._client().connection() as conn, conn.cursor() as cur:
            cur.executemany(stmt, [(r.id, Vector(r.vector), Jsonb(r.payload)) for r in records])

    def search(self, vector: list[float], k: int, filter: dict[str, Any] | None = None) -> list[VectorHit]:
        """Query the k nearest by cosine distance; an equality filter uses jsonb containment."""
        from pgvector import Vector  # ty: ignore[unresolved-import]
        from psycopg import sql  # ty: ignore[unresolved-import]
        from psycopg.types.json import Jsonb  # ty: ignore[unresolved-import]

        # Wrap as a Vector so the param is sent as `vector`, not float8[] — `<=>` has no
        # `vector <=> double precision[]` form.
        query = Vector(vector)
        where = sql.SQL("WHERE payload @> %s") if filter else sql.SQL("")
        stmt = sql.SQL(
            "SELECT id, payload, 1 - (embedding <=> %s) AS score FROM {tbl} {where} "
            "ORDER BY embedding <=> %s LIMIT %s"
        ).format(tbl=sql.Identifier(self.cfg.collection), where=where)
        params: list[Any] = [query]
        if filter:
            params.append(Jsonb(filter))
        params += [query, k]
        with self._client().connection() as conn:
            rows = conn.execute(stmt, params).fetchall()
        return [VectorHit(row[0], float(row[2]), dict(row[1] or {})) for row in rows]

    def delete(self, ids: list[str]) -> None:
        """Delete rows by id."""
        from psycopg import sql  # ty: ignore[unresolved-import]

        stmt = sql.SQL("DELETE FROM {tbl} WHERE id = ANY(%s)").format(tbl=sql.Identifier(self.cfg.collection))
        with self._client().connection() as conn:
            conn.execute(stmt, (ids,))
