from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pgvector import Vector
from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from ci_engine.config import get as config_get
from ci_engine.db.connection import get_engine

_VECTOR_FETCH_MULTIPLIER = 3


def _embedding_dimensions() -> int:
    return int(config_get("embedding.dimensions", 1536))


def _insert_batch_size() -> int:
    return int(config_get("ingestion.insert_batch_size", 16))


def _vector_literal(embedding: Sequence[float]) -> str:
    vector = Vector(embedding)
    dimensions = _embedding_dimensions()
    if vector.dimensions() != dimensions:
        raise ValueError(
            f"expected {dimensions} embedding dimensions, got {vector.dimensions()}"
        )
    return vector.to_text()


def upsert_source(
    competitor: str,
    axis: str,
    doc_type: str,
    dimension: str | None,
    url: str,
    title: str | None,
    publish_date: Any,
    content_hash: str,
    raw_path: str | None,
) -> int:
    stmt = text(
        """
        WITH inserted AS (
            INSERT INTO sources (
                competitor, axis, doc_type, dimension, url, title,
                publish_date, content_hash, raw_path
            )
            VALUES (
                :competitor, :axis, :doc_type, :dimension, :url, :title,
                :publish_date, :content_hash, :raw_path
            )
            ON CONFLICT (url, content_hash) DO NOTHING
            RETURNING id
        )
        SELECT id FROM inserted
        UNION ALL
        SELECT id FROM sources
        WHERE url = :url AND content_hash = :content_hash
        LIMIT 1
        """
    )
    params = {
        "competitor": competitor,
        "axis": axis,
        "doc_type": doc_type,
        "dimension": dimension,
        "url": url,
        "title": title,
        "publish_date": publish_date,
        "content_hash": content_hash,
        "raw_path": raw_path,
    }

    with get_engine().begin() as conn:
        return int(conn.execute(stmt, params).scalar_one())


def insert_chunks(
    source_id: int,
    competitor: str,
    axis: str,
    doc_type: str,
    publish_date: Any,
    chunks: Sequence[tuple[str, Sequence[float]]],
) -> None:
    if not chunks:
        return

    stmt = text(
        """
        INSERT INTO chunks (
            source_id, competitor, axis, doc_type, publish_date,
            status, chunk_text, embedding
        )
        VALUES (
            :source_id, :competitor, :axis, :doc_type, :publish_date,
            'active', :chunk_text, CAST(:embedding AS vector)
        )
        """
    )
    rows = [
        {
            "source_id": source_id,
            "competitor": competitor,
            "axis": axis,
            "doc_type": doc_type,
            "publish_date": publish_date,
            "chunk_text": chunk_text,
            "embedding": _vector_literal(embedding),
        }
        for chunk_text, embedding in chunks
    ]

    batch_size = max(_insert_batch_size(), 1)
    with get_engine().begin() as conn:
        for start in range(0, len(rows), batch_size):
            conn.execute(stmt, rows[start : start + batch_size])


def upsert_entity(name: str, entity_type: str, competitor: str | None) -> int:
    stmt = text(
        """
        WITH inserted AS (
            INSERT INTO entities (name, entity_type, competitor)
            VALUES (:name, :entity_type, :competitor)
            ON CONFLICT (name, entity_type) DO NOTHING
            RETURNING id
        )
        SELECT id FROM inserted
        UNION ALL
        SELECT id FROM entities
        WHERE name = :name AND entity_type = :entity_type
        LIMIT 1
        """
    )

    with get_engine().begin() as conn:
        return int(
            conn.execute(
                stmt,
                {
                    "name": name,
                    "entity_type": entity_type,
                    "competitor": competitor,
                },
            ).scalar_one()
        )


def add_relationship(
    src_id: int,
    dst_id: int,
    relation: str,
    source_id: int | None,
) -> int:
    stmt = text(
        """
        WITH inserted AS (
            INSERT INTO relationships (
                src_entity_id, dst_entity_id, relation, source_id
            )
            VALUES (:src_id, :dst_id, :relation, :source_id)
            ON CONFLICT (src_entity_id, dst_entity_id, relation) DO NOTHING
            RETURNING id
        )
        SELECT id FROM inserted
        UNION ALL
        SELECT id FROM relationships
        WHERE src_entity_id = :src_id
          AND dst_entity_id = :dst_id
          AND relation = :relation
        LIMIT 1
        """
    )

    with get_engine().begin() as conn:
        return int(
            conn.execute(
                stmt,
                {
                    "src_id": src_id,
                    "dst_id": dst_id,
                    "relation": relation,
                    "source_id": source_id,
                },
            ).scalar_one()
        )


def vector_search(
    query_embedding: Sequence[float],
    top_k: int | None = None,
    similarity_threshold: float | None = None,
    axis: str | None = None,
    competitors: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if competitors is not None and len(competitors) == 0:
        return []

    k = int(top_k if top_k is not None else config_get("retrieval.top_k", 8))
    threshold = float(
        similarity_threshold
        if similarity_threshold is not None
        else config_get("retrieval.similarity_threshold", 0.55)
    )
    k_fetch = max(k * _VECTOR_FETCH_MULTIPLIER, 1)

    filters = ["c.status = 'active'"]
    params: dict[str, Any] = {
        "q": _vector_literal(query_embedding),
        "k_fetch": k_fetch,
    }
    bindparams = []

    if axis is not None:
        filters.append("c.axis = :axis")
        params["axis"] = axis

    if competitors is not None:
        filters.append("c.competitor = ANY(:competitors)")
        params["competitors"] = list(competitors)
        bindparams.append(bindparam("competitors", type_=ARRAY(String())))

    stmt = text(
        f"""
        SELECT
            c.chunk_text,
            s.url,
            c.publish_date,
            c.doc_type,
            c.competitor,
            1 - (c.embedding <=> CAST(:q AS vector)) AS similarity
        FROM chunks c
        JOIN sources s ON s.id = c.source_id
        WHERE {" AND ".join(filters)}
        ORDER BY c.embedding <=> CAST(:q AS vector)
        LIMIT :k_fetch
        """
    )
    if bindparams:
        stmt = stmt.bindparams(*bindparams)

    with get_engine().connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()

    results: list[dict[str, Any]] = []
    for row in rows:
        similarity = float(row["similarity"])
        if similarity < threshold:
            continue
        results.append(
            {
                "chunk_text": row["chunk_text"],
                "url": row["url"],
                "publish_date": row["publish_date"],
                "doc_type": row["doc_type"],
                "competitor": row["competitor"],
                "similarity": similarity,
            }
        )
    return results


def expand_entities(
    seed_entity_names: Sequence[str],
    max_hops: int | None = None,
) -> list[int]:
    if not seed_entity_names:
        return []

    hops = int(
        max_hops
        if max_hops is not None
        else config_get("retrieval.max_graph_hops", 2)
    )
    stmt = text(
        """
        WITH RECURSIVE reachable(entity_id, depth, path) AS (
            SELECT e.id, 0, ARRAY[e.id]::bigint[]
            FROM entities e
            WHERE e.name = ANY(:seed_entity_names)

            UNION

            SELECT neighbors.entity_id, reachable.depth + 1,
                   reachable.path || neighbors.entity_id::bigint
            FROM reachable
            JOIN LATERAL (
                SELECT r.dst_entity_id AS entity_id
                FROM relationships r
                WHERE r.src_entity_id = reachable.entity_id

                UNION

                SELECT r.src_entity_id AS entity_id
                FROM relationships r
                WHERE r.dst_entity_id = reachable.entity_id
            ) neighbors ON true
            WHERE reachable.depth < :max_hops
              AND NOT neighbors.entity_id = ANY(reachable.path)
        )
        SELECT DISTINCT entity_id
        FROM reachable
        ORDER BY entity_id
        """
    ).bindparams(bindparam("seed_entity_names", type_=ARRAY(String())))

    with get_engine().connect() as conn:
        return [
            int(row.entity_id)
            for row in conn.execute(
                stmt,
                {
                    "seed_entity_names": list(seed_entity_names),
                    "max_hops": max(hops, 0),
                },
            )
        ]


__all__ = [
    "add_relationship",
    "expand_entities",
    "insert_chunks",
    "upsert_entity",
    "upsert_source",
    "vector_search",
]
