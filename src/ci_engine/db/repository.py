from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pgvector import Vector
from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import DatabaseError

from ci_engine.config import get as config_get
from ci_engine.config import tracked_companies
from ci_engine.db.connection import get_engine
from ci_engine.dimension_coverage import COVERAGE_STATES, rollup_assertions

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
    source_kind: str | None = None,
) -> int:
    stmt = text(
        """
        WITH inserted AS (
            INSERT INTO sources (
                competitor, axis, doc_type, dimension, url, title,
                publish_date, content_hash, raw_path, source_kind
            )
            VALUES (
                :competitor, :axis, :doc_type, :dimension, :url, :title,
                :publish_date, :content_hash, :raw_path, :source_kind
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
        "source_kind": source_kind or "unknown",
    }

    try:
        with get_engine().begin() as conn:
            return int(conn.execute(stmt, params).scalar_one())
    except DatabaseError as exc:
        if not _missing_source_kind_column(exc):
            raise
        legacy_params = dict(params)
        legacy_params.pop("source_kind", None)
        with get_engine().begin() as conn:
            return int(
                conn.execute(_legacy_upsert_source_stmt(), legacy_params).scalar_one()
            )


def _legacy_upsert_source_stmt() -> Any:
    return text(
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


def _missing_source_kind_column(exc: DatabaseError) -> bool:
    return "source_kind" in str(exc).lower() and (
        "does not exist" in str(exc).lower()
        or "undefinedcolumn" in str(exc).lower()
        or "undefined column" in str(exc).lower()
    )


def source_exists(url: str, content_hash: str) -> bool:
    stmt = text(
        """
        SELECT EXISTS (
            SELECT 1
            FROM sources
            WHERE url = :url AND content_hash = :content_hash
        )
        """
    )

    with get_engine().connect() as conn:
        return bool(
            conn.execute(
                stmt,
                {
                    "url": url,
                    "content_hash": content_hash,
                },
            ).scalar_one()
        )


def insert_source_citations(
    source_id: int,
    citations: Sequence[Mapping[str, Any]],
) -> int:
    rows = _citation_rows(source_id, citations)
    if not rows:
        return 0

    stmt = text(
        """
        INSERT INTO source_citations (
            source_id, cited_url, citation_label, cited_date_text
        )
        VALUES (
            :source_id, :cited_url, :citation_label, :cited_date_text
        )
        ON CONFLICT (source_id, cited_url) DO UPDATE SET
            citation_label = EXCLUDED.citation_label,
            cited_date_text = EXCLUDED.cited_date_text
        """
    )

    batch_size = max(_insert_batch_size(), 1)
    with get_engine().begin() as conn:
        for start in range(0, len(rows), batch_size):
            conn.execute(stmt, rows[start : start + batch_size])
    return len(rows)


def _citation_rows(
    source_id: int,
    citations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for citation in citations:
        cited_url = str(citation.get("url") or citation.get("cited_url") or "").strip()
        if not cited_url or cited_url in seen:
            continue
        seen.add(cited_url)
        rows.append(
            {
                "source_id": source_id,
                "cited_url": cited_url,
                "citation_label": _optional_text(citation.get("label")),
                "cited_date_text": _optional_text(citation.get("date_text")),
            }
        )
    return rows


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def citations_for_sources(source_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    if not source_ids:
        return {}

    unique_source_ids = sorted({int(source_id) for source_id in source_ids})
    stmt = text(
        """
        SELECT source_id, cited_url, citation_label, cited_date_text
        FROM source_citations
        WHERE source_id IN :source_ids
        ORDER BY source_id, cited_url
        """
    ).bindparams(bindparam("source_ids", expanding=True))

    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                stmt,
                {"source_ids": unique_source_ids},
            ).mappings().all()
    except DatabaseError as exc:
        if not _missing_source_citations_table(exc):
            raise
        return {source_id: [] for source_id in unique_source_ids}

    citations: dict[int, list[dict[str, Any]]] = {
        source_id: [] for source_id in unique_source_ids
    }
    for row in rows:
        citations[int(row["source_id"])].append(
            {
                "url": row["cited_url"],
                "label": row["citation_label"],
                "date_text": row["cited_date_text"],
            }
        )
    return citations


def _missing_source_citations_table(exc: DatabaseError) -> bool:
    return "source_citations" in str(exc).lower() and (
        "does not exist" in str(exc).lower()
        or "undefinedtable" in str(exc).lower()
        or "undefined table" in str(exc).lower()
    )


def supersede_older(
    competitor: str,
    dimension: str | None,
    publish_date: Any,
) -> int:
    if dimension is None or publish_date is None:
        return 0

    stmt = text(
        """
        WITH older_sources AS (
            UPDATE sources
            SET status = 'superseded'
            WHERE competitor = :competitor
              AND dimension = :dimension
              AND status = 'active'
              AND publish_date < :publish_date
            RETURNING id
        ),
        older_chunks AS (
            UPDATE chunks
            SET status = 'superseded'
            WHERE source_id IN (SELECT id FROM older_sources)
              AND status = 'active'
            RETURNING id
        )
        SELECT count(*) FROM older_sources
        """
    )

    with get_engine().begin() as conn:
        return int(
            conn.execute(
                stmt,
                {
                    "competitor": competitor,
                    "dimension": dimension,
                    "publish_date": publish_date,
                },
            ).scalar_one()
        )


def latest_fetched_at(competitor: str | None = None) -> Any:
    filters = []
    params: dict[str, Any] = {}
    if competitor is not None:
        filters.append("competitor = :competitor")
        params["competitor"] = competitor

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    stmt = text(
        f"""
        SELECT max(fetched_at)
        FROM sources
        {where_clause}
        """
    )

    with get_engine().connect() as conn:
        return conn.execute(stmt, params).scalar_one()


def coverage_status() -> list[dict[str, Any]]:
    dimensions = _ontology_dimensions()
    companies = tracked_companies()
    if not companies or not dimensions:
        return []

    stmt = text(
        """
        SELECT competitor, dimension, count(*) AS active_sources,
               max(publish_date) AS freshest_publish_date
        FROM sources
        WHERE status = 'active'
          AND dimension IS NOT NULL
          AND competitor = ANY(:companies)
          AND dimension = ANY(:dimensions)
        GROUP BY competitor, dimension
        """
    ).bindparams(
        bindparam("companies", type_=ARRAY(String())),
        bindparam("dimensions", type_=ARRAY(String())),
    )

    with get_engine().connect() as conn:
        rows = conn.execute(
            stmt,
            {
                "companies": companies,
                "dimensions": [dimension for _, dimension in dimensions],
            },
        ).mappings().all()

    indexed = {
        (row["competitor"], row["dimension"]): row
        for row in rows
    }
    status: list[dict[str, Any]] = []
    for competitor in companies:
        for axis, dimension in dimensions:
            row = indexed.get((competitor, dimension))
            status.append(
                {
                    "competitor": competitor,
                    "axis": axis,
                    "dimension": dimension,
                    "active_sources": int(row["active_sources"]) if row else 0,
                    "freshest_publish_date": row["freshest_publish_date"]
                    if row
                    else None,
                }
            )
    return status


def ensure_dimension_coverage_tables() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS dimension_coverage_assertions (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            competitor TEXT NOT NULL,
            axis TEXT NOT NULL CHECK (axis IN ('technical','business','both')),
            dimension TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('present','partial','planned','absent','unknown')),
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            claim TEXT NOT NULL,
            reason TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_id, competitor, axis, dimension, state, claim)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dimension_coverage_status (
            competitor TEXT NOT NULL,
            axis TEXT NOT NULL CHECK (axis IN ('technical','business','both')),
            dimension TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('present','partial','planned','absent','unknown')),
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            active_assertions INTEGER NOT NULL DEFAULT 0,
            strongest_source_id BIGINT REFERENCES sources(id) ON DELETE SET NULL,
            conflict BOOLEAN NOT NULL DEFAULT false,
            states JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (competitor, axis, dimension)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dimension_coverage_audit (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            action TEXT NOT NULL,
            competitor TEXT NOT NULL,
            axis TEXT NOT NULL,
            dimension TEXT NOT NULL,
            old_state TEXT,
            new_state TEXT,
            source_id BIGINT REFERENCES sources(id) ON DELETE SET NULL,
            reason TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS dimension_coverage_assertions_lookup_idx
            ON dimension_coverage_assertions (competitor, axis, dimension, state)
        """,
        """
        CREATE INDEX IF NOT EXISTS dimension_coverage_assertions_source_idx
            ON dimension_coverage_assertions (source_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS dimension_coverage_assertions_source_dim_state_idx
            ON dimension_coverage_assertions (source_id, dimension, state)
        """,
        """
        CREATE INDEX IF NOT EXISTS dimension_coverage_audit_lookup_idx
            ON dimension_coverage_audit (competitor, axis, dimension)
        """,
    ]
    try:
        with get_engine().begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
    except DatabaseError as exc:
        if _permission_denied(exc) and _dimension_coverage_tables_exist():
            return
        if _permission_denied(exc):
            raise RuntimeError(
                "dimension coverage tables do not exist and this DB user cannot "
                "create them; apply src/ci_engine/db/schema.sql with a privileged "
                "role before storing coverage assertions"
            ) from exc
        raise


def _dimension_coverage_tables_exist() -> bool:
    stmt = text("SELECT 1 FROM dimension_coverage_status LIMIT 1")
    try:
        with get_engine().connect() as conn:
            conn.execute(stmt).first()
        return True
    except DatabaseError:
        return False


def insert_dimension_coverage_assertions(
    source_id: int,
    assertions: Sequence[Mapping[str, Any]],
    *,
    reason: str = "ingestion",
    ensure_tables: bool = True,
) -> int:
    rows = _coverage_assertion_rows(source_id, assertions)
    if not rows:
        return 0
    if ensure_tables:
        ensure_dimension_coverage_tables()

    stmt = text(
        """
        INSERT INTO dimension_coverage_assertions (
            source_id, competitor, axis, dimension, state,
            confidence, claim, reason, details
        )
        VALUES (
            :source_id, :competitor, :axis, :dimension, :state,
            :confidence, :claim, :reason, CAST(:details AS jsonb)
        )
        ON CONFLICT (source_id, competitor, axis, dimension, state, claim)
        DO UPDATE SET
            confidence = EXCLUDED.confidence,
            reason = EXCLUDED.reason,
            details = EXCLUDED.details,
            updated_at = now()
        """
    )

    batch_size = max(_insert_batch_size(), 1)
    with get_engine().begin() as conn:
        for start in range(0, len(rows), batch_size):
            conn.execute(stmt, rows[start : start + batch_size])
        for row in rows:
            _insert_dimension_coverage_audit(
                conn,
                action="assertion_upsert",
                competitor=row["competitor"],
                axis=row["axis"],
                dimension=row["dimension"],
                old_state=None,
                new_state=row["state"],
                source_id=int(source_id),
                reason=reason,
                details={
                    "claim": row["claim"],
                    "confidence": row["confidence"],
                    "assertion_reason": row["reason"],
                },
            )
    return len(rows)


def _coverage_assertion_rows(
    source_id: int,
    assertions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for assertion in assertions:
        competitor = _optional_text(assertion.get("competitor"))
        axis = _optional_text(assertion.get("axis"))
        dimension = _optional_text(assertion.get("dimension"))
        state = _optional_text(assertion.get("state"))
        claim = _optional_text(assertion.get("claim"))
        if (
            not competitor
            or axis not in {"technical", "business", "both"}
            or not dimension
            or state not in COVERAGE_STATES
            or not claim
        ):
            continue
        rows.append(
            {
                "source_id": int(source_id),
                "competitor": competitor,
                "axis": axis,
                "dimension": dimension,
                "state": state,
                "confidence": float(assertion.get("confidence") or 0.0),
                "claim": claim,
                "reason": _optional_text(assertion.get("reason")) or "unspecified",
                "details": json.dumps(
                    dict(assertion.get("details") or {}),
                    sort_keys=True,
                    default=str,
                ),
            }
        )
    return rows


def refresh_dimension_coverage_status(
    competitor: str,
    axis: str,
    dimension: str,
    *,
    reason: str = "rollup_refresh",
    ensure_tables: bool = True,
) -> dict[str, Any]:
    if ensure_tables:
        ensure_dimension_coverage_tables()

    with get_engine().begin() as conn:
        previous = conn.execute(
            text(
                """
                SELECT state, conflict
                FROM dimension_coverage_status
                WHERE competitor = :competitor
                  AND axis = :axis
                  AND dimension = :dimension
                FOR UPDATE
                """
            ),
            {
                "competitor": competitor,
                "axis": axis,
                "dimension": dimension,
            },
        ).mappings().first()
        assertion_rows = conn.execute(
            text(
                """
                SELECT
                    a.source_id,
                    a.state,
                    a.confidence,
                    a.claim,
                    a.reason
                FROM dimension_coverage_assertions a
                JOIN sources s ON s.id = a.source_id
                WHERE s.status = 'active'
                  AND a.competitor = :competitor
                  AND a.dimension = :dimension
                  AND (a.axis = :axis OR a.axis = 'both' OR :axis = 'both')
                ORDER BY a.updated_at DESC, a.id DESC
                """
            ),
            {
                "competitor": competitor,
                "axis": axis,
                "dimension": dimension,
            },
        ).mappings().all()
        rollup = rollup_assertions(assertion_rows)
        new_state = str(rollup["state"])
        conn.execute(
            text(
                """
                INSERT INTO dimension_coverage_status (
                    competitor, axis, dimension, state, confidence,
                    active_assertions, strongest_source_id, conflict, states,
                    updated_at
                )
                VALUES (
                    :competitor, :axis, :dimension, :state, :confidence,
                    :active_assertions, :strongest_source_id, :conflict,
                    CAST(:states AS jsonb), now()
                )
                ON CONFLICT (competitor, axis, dimension)
                DO UPDATE SET
                    state = EXCLUDED.state,
                    confidence = EXCLUDED.confidence,
                    active_assertions = EXCLUDED.active_assertions,
                    strongest_source_id = EXCLUDED.strongest_source_id,
                    conflict = EXCLUDED.conflict,
                    states = EXCLUDED.states,
                    updated_at = now()
                """
            ),
            {
                "competitor": competitor,
                "axis": axis,
                "dimension": dimension,
                "state": new_state,
                "confidence": float(rollup["confidence"]),
                "active_assertions": int(rollup["active_assertions"]),
                "strongest_source_id": rollup["strongest_source_id"],
                "conflict": bool(rollup["conflict"]),
                "states": json.dumps(rollup["states"], sort_keys=True),
            },
        )
        if (
            previous is None
            or previous["state"] != new_state
            or bool(previous["conflict"]) != bool(rollup["conflict"])
        ):
            _insert_dimension_coverage_audit(
                conn,
                action="status_refresh",
                competitor=competitor,
                axis=axis,
                dimension=dimension,
                old_state=previous["state"] if previous else None,
                new_state=new_state,
                source_id=rollup["strongest_source_id"],
                reason=reason,
                details={
                    "active_assertions": rollup["active_assertions"],
                    "conflict": rollup["conflict"],
                    "states": rollup["states"],
                },
            )
    return {
        "competitor": competitor,
        "axis": axis,
        "dimension": dimension,
        **rollup,
    }


def refresh_all_dimension_coverage_statuses(
    *,
    reason: str = "full_rollup_refresh",
) -> list[dict[str, Any]]:
    ensure_dimension_coverage_tables()
    refreshed: list[dict[str, Any]] = []
    for competitor in tracked_companies():
        for axis, dimension in _ontology_dimensions():
            refreshed.append(
                refresh_dimension_coverage_status(
                    competitor,
                    axis,
                    dimension,
                    reason=reason,
                    ensure_tables=False,
                )
            )
    return refreshed


def dimension_coverage_status(
    *,
    competitors: Sequence[str] | None = None,
    axis: str | None = None,
    dimensions: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if _empty_filter(competitors):
        return []
    if _empty_filter(dimensions):
        return []

    selected_companies = list(competitors) if competitors is not None else tracked_companies()
    dimension_rows = [
        (row_axis, dimension)
        for row_axis, dimension in _ontology_dimensions()
        if (axis is None or row_axis == axis)
        and (dimensions is None or dimension in dimensions)
    ]
    if not selected_companies or not dimension_rows:
        return []

    status_rows: list[Mapping[str, Any]]
    try:
        stmt = text(
            """
            SELECT
                competitor,
                axis,
                dimension,
                state,
                confidence,
                active_assertions,
                strongest_source_id,
                conflict,
                states,
                updated_at
            FROM dimension_coverage_status
            WHERE competitor = ANY(:competitors)
              AND dimension = ANY(:dimensions)
            """
        ).bindparams(
            bindparam("competitors", type_=ARRAY(String())),
            bindparam("dimensions", type_=ARRAY(String())),
        )
        with get_engine().connect() as conn:
            status_rows = conn.execute(
                stmt,
                {
                    "competitors": selected_companies,
                    "dimensions": [dimension for _, dimension in dimension_rows],
                },
            ).mappings().all()
    except DatabaseError as exc:
        if not _missing_dimension_coverage_table(exc):
            raise
        status_rows = []

    indexed = {
        (row["competitor"], row["axis"], row["dimension"]): row
        for row in status_rows
    }
    results: list[dict[str, Any]] = []
    for competitor in selected_companies:
        for row_axis, dimension in dimension_rows:
            row = indexed.get((competitor, row_axis, dimension))
            if row is None:
                results.append(
                    {
                        "competitor": competitor,
                        "axis": row_axis,
                        "dimension": dimension,
                        "state": "unknown",
                        "confidence": 0.0,
                        "active_assertions": 0,
                        "strongest_source_id": None,
                        "conflict": False,
                        "states": {},
                        "updated_at": None,
                    }
                )
                continue
            results.append(
                {
                    "competitor": row["competitor"],
                    "axis": row["axis"],
                    "dimension": row["dimension"],
                    "state": row["state"],
                    "confidence": float(row["confidence"] or 0.0),
                    "active_assertions": int(row["active_assertions"] or 0),
                    "strongest_source_id": row["strongest_source_id"],
                    "conflict": bool(row["conflict"]),
                    "states": dict(row["states"] or {}),
                    "updated_at": row["updated_at"],
                }
            )
    return results


def _missing_dimension_coverage_table(exc: DatabaseError) -> bool:
    text_value = str(exc).lower()
    return "dimension_coverage_status" in text_value and (
        "does not exist" in text_value
        or "undefinedtable" in text_value
        or "undefined table" in text_value
    )


def _ontology_dimensions() -> list[tuple[str, str]]:
    ontology = config_get("ontology", {})
    dimensions: list[tuple[str, str]] = []
    for axis in ("technical", "business"):
        for dimension in ontology.get(axis, []):
            dimensions.append((axis, str(dimension)))
    return dimensions


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
    dimensions: Sequence[str] | None = None,
    doc_types: Sequence[str] | None = None,
    source_kinds: Sequence[str] | None = None,
    published_after: Any | None = None,
    published_before: Any | None = None,
) -> list[dict[str, Any]]:
    if _empty_filter(competitors):
        return []
    if _empty_filter(dimensions):
        return []
    if _empty_filter(doc_types):
        return []
    if _empty_filter(source_kinds):
        return []

    k = max(int(top_k if top_k is not None else config_get("retrieval.top_k", 8)), 0)
    if k == 0:
        return []
    threshold = float(
        similarity_threshold
        if similarity_threshold is not None
        else config_get("retrieval.similarity_threshold", 0.55)
    )
    k_fetch = max(k * _VECTOR_FETCH_MULTIPLIER, 1)

    filters = ["c.status = 'active'", "s.status = 'active'"]
    dimension_join = ""
    dimension_select = "s.dimension"
    params: dict[str, Any] = {
        "q": _vector_literal(query_embedding),
        "k_fetch": k_fetch,
    }
    bindparams = []

    if axis is not None:
        if axis == "both":
            filters.append("c.axis = :axis")
        else:
            filters.append("(c.axis = :axis OR c.axis = 'both')")
        params["axis"] = axis

    if competitors is not None:
        filters.append("c.competitor = ANY(:competitors)")
        params["competitors"] = list(competitors)
        bindparams.append(bindparam("competitors", type_=ARRAY(String())))

    if dimensions is not None:
        dimension_join = """
        LEFT JOIN LATERAL (
            SELECT a.dimension
            FROM dimension_coverage_assertions a
            WHERE a.source_id = s.id
              AND a.dimension = ANY(:dimensions)
              AND a.state IN ('present', 'partial', 'planned', 'absent')
            ORDER BY
              CASE a.state
                WHEN 'present' THEN 1
                WHEN 'partial' THEN 2
                WHEN 'planned' THEN 3
                WHEN 'absent' THEN 4
                ELSE 5
              END,
              a.confidence DESC,
              a.updated_at DESC,
              a.id DESC
            LIMIT 1
        ) dca ON TRUE
        """
        dimension_select = "COALESCE(dca.dimension, s.dimension)"
        filters.append("(s.dimension = ANY(:dimensions) OR dca.dimension IS NOT NULL)")
        params["dimensions"] = list(dimensions)
        bindparams.append(bindparam("dimensions", type_=ARRAY(String())))

    if doc_types is not None:
        filters.append("c.doc_type = ANY(:doc_types)")
        params["doc_types"] = list(doc_types)
        bindparams.append(bindparam("doc_types", type_=ARRAY(String())))

    if source_kinds is not None:
        filters.append("s.source_kind = ANY(:source_kinds)")
        params["source_kinds"] = list(source_kinds)
        bindparams.append(bindparam("source_kinds", type_=ARRAY(String())))

    if published_after is not None:
        filters.append("c.publish_date >= :published_after")
        params["published_after"] = published_after

    if published_before is not None:
        filters.append("c.publish_date <= :published_before")
        params["published_before"] = published_before

    stmt = text(
        f"""
        SELECT
            s.id AS source_id,
            c.chunk_text,
            s.url,
            s.source_kind,
            s.raw_path,
            c.publish_date,
            c.axis,
            {dimension_select} AS dimension,
            c.doc_type,
            c.competitor,
            1 - (c.embedding <=> CAST(:q AS vector)) AS similarity
        FROM chunks c
        JOIN sources s ON s.id = c.source_id
        {dimension_join}
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
                "source_id": int(row["source_id"]),
                "chunk_text": row["chunk_text"],
                "url": row["url"],
                "source_kind": row["source_kind"],
                "raw_path": row["raw_path"],
                "publish_date": row["publish_date"],
                "axis": row["axis"],
                "dimension": row["dimension"],
                "doc_type": row["doc_type"],
                "competitor": row["competitor"],
                "similarity": similarity,
                "citations": [],
            }
        )
        if len(results) >= k:
            break
    citations = citations_for_sources([result["source_id"] for result in results])
    for result in results:
        result["citations"] = citations.get(result["source_id"], [])
    return results


def source_chunks(
    source_id: int,
    *,
    status: str | None = "active",
) -> list[dict[str, Any]]:
    filters = ["s.id = :source_id"]
    params: dict[str, Any] = {"source_id": source_id}
    if status is not None:
        filters.append("c.status = :status")
        filters.append("s.status = :status")
        params["status"] = status

    stmt = text(
        f"""
        SELECT
            c.id AS chunk_id,
            s.id AS source_id,
            c.chunk_text,
            s.url,
            s.source_kind,
            s.raw_path,
            c.publish_date,
            c.axis,
            s.dimension,
            c.doc_type,
            c.competitor
        FROM chunks c
        JOIN sources s ON s.id = c.source_id
        WHERE {" AND ".join(filters)}
        ORDER BY c.id
        """
    )

    with get_engine().connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()

    citations = citations_for_sources([source_id]).get(source_id, [])
    return [
        {
            "chunk_id": int(row["chunk_id"]),
            "source_id": int(row["source_id"]),
            "chunk_text": row["chunk_text"],
            "url": row["url"],
            "source_kind": row["source_kind"],
            "raw_path": row["raw_path"],
            "publish_date": row["publish_date"],
            "axis": row["axis"],
            "dimension": row["dimension"],
            "doc_type": row["doc_type"],
            "competitor": row["competitor"],
            "citations": citations,
        }
        for row in rows
    ]


def active_chunks(
    *,
    competitors: Sequence[str] | None = None,
    axis: str | None = None,
    dimensions: Sequence[str] | None = None,
    source_ids: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    if _empty_filter(competitors):
        return []
    if _empty_filter(dimensions):
        return []
    if _empty_filter(source_ids):
        return []

    filters = ["c.status = 'active'", "s.status = 'active'"]
    params: dict[str, Any] = {}
    bindparams = []

    if competitors is not None:
        filters.append("c.competitor IN :competitors")
        params["competitors"] = list(competitors)
        bindparams.append(bindparam("competitors", expanding=True))

    if axis is not None:
        if axis == "both":
            filters.append("c.axis = :axis")
        else:
            filters.append("(c.axis = :axis OR c.axis = 'both')")
        params["axis"] = axis

    if dimensions is not None:
        filters.append("s.dimension IN :dimensions")
        params["dimensions"] = list(dimensions)
        bindparams.append(bindparam("dimensions", expanding=True))

    if source_ids is not None:
        filters.append("s.id IN :source_ids")
        params["source_ids"] = [int(source_id) for source_id in source_ids]
        bindparams.append(bindparam("source_ids", expanding=True))

    stmt = text(
        f"""
        SELECT
            c.id AS chunk_id,
            s.id AS source_id,
            c.chunk_text,
            s.url,
            s.title,
            s.source_kind,
            s.raw_path,
            c.publish_date,
            s.fetched_at,
            c.axis,
            s.dimension,
            c.doc_type,
            c.competitor
        FROM chunks c
        JOIN sources s ON s.id = c.source_id
        WHERE {" AND ".join(filters)}
        ORDER BY c.competitor, s.dimension NULLS LAST,
                 c.publish_date DESC NULLS LAST, c.id DESC
        """
    )
    if bindparams:
        stmt = stmt.bindparams(*bindparams)

    with get_engine().connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()

    citations = citations_for_sources([int(row["source_id"]) for row in rows])
    return [
        {
            "chunk_id": int(row["chunk_id"]),
            "source_id": int(row["source_id"]),
            "chunk_text": row["chunk_text"],
            "url": row["url"],
            "title": row["title"],
            "source_kind": row["source_kind"],
            "raw_path": row["raw_path"],
            "publish_date": row["publish_date"],
            "fetched_at": row["fetched_at"],
            "axis": row["axis"],
            "dimension": row["dimension"],
            "doc_type": row["doc_type"],
            "competitor": row["competitor"],
            "citations": citations.get(int(row["source_id"]), []),
        }
        for row in rows
    ]


def latest_active_sources(
    *,
    competitor: str | None = None,
    fetched_since: Any | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    filters = ["status = 'active'"]
    params: dict[str, Any] = {}

    if competitor is not None:
        filters.append("competitor = :competitor")
        params["competitor"] = competitor

    if fetched_since is not None:
        filters.append("fetched_at >= :fetched_since")
        params["fetched_since"] = fetched_since

    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT :limit"
        params["limit"] = max(int(limit), 0)

    stmt = text(
        f"""
        SELECT
            id AS source_id,
            competitor,
            axis,
            doc_type,
            dimension,
            url,
            title,
            publish_date,
            fetched_at,
            source_kind,
            raw_path
        FROM sources
        WHERE {" AND ".join(filters)}
        ORDER BY fetched_at DESC, id DESC
        {limit_clause}
        """
    )

    with get_engine().connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()

    citations = citations_for_sources([int(row["source_id"]) for row in rows])
    return [
        {
            "source_id": int(row["source_id"]),
            "competitor": row["competitor"],
            "axis": row["axis"],
            "doc_type": row["doc_type"],
            "dimension": row["dimension"],
            "url": row["url"],
            "title": row["title"],
            "publish_date": row["publish_date"],
            "fetched_at": row["fetched_at"],
            "source_kind": row["source_kind"],
            "raw_path": row["raw_path"],
            "citations": citations.get(int(row["source_id"]), []),
        }
        for row in rows
    ]


def ensure_source_healing_audit() -> None:
    stmt = text(
        """
        CREATE TABLE IF NOT EXISTS source_healing_audit (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            source_id BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            action TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            old_dimension TEXT,
            new_dimension TEXT,
            reason TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    try:
        with get_engine().begin() as conn:
            conn.execute(stmt)
    except DatabaseError as exc:
        if _permission_denied(exc) and _source_healing_audit_exists():
            return
        if _permission_denied(exc):
            raise RuntimeError(
                "source_healing_audit does not exist and this DB user cannot "
                "create it; apply src/ci_engine/db/schema.sql with a privileged "
                "role before running the healer with --apply"
            ) from exc
        raise


def _source_healing_audit_exists() -> bool:
    stmt = text("SELECT 1 FROM source_healing_audit LIMIT 1")
    try:
        with get_engine().connect() as conn:
            conn.execute(stmt).first()
        return True
    except DatabaseError:
        return False


def _permission_denied(exc: DatabaseError) -> bool:
    text_value = str(exc).lower()
    return "permission denied" in text_value or "'c': '42501'" in text_value


def healing_source_rows(
    *,
    statuses: Sequence[str] | None = ("active",),
) -> list[dict[str, Any]]:
    if _empty_filter(statuses):
        return []

    filters = []
    params: dict[str, Any] = {}
    bindparams = []
    if statuses is not None:
        filters.append("s.status IN :statuses")
        params["statuses"] = list(statuses)
        bindparams.append(bindparam("statuses", expanding=True))

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    stmt = text(
        f"""
        SELECT
            s.id AS source_id,
            s.competitor,
            s.axis,
            s.doc_type,
            s.dimension,
            s.url,
            s.title,
            s.publish_date,
            s.fetched_at,
            s.status,
            s.source_kind,
            COALESCE(c.chunk_text, '') AS chunk_text
        FROM sources s
        LEFT JOIN LATERAL (
            SELECT string_agg(chunk_text, E'\n\n' ORDER BY id) AS chunk_text
            FROM chunks
            WHERE source_id = s.id
        ) c ON true
        {where_clause}
        ORDER BY s.id
        """
    )
    if bindparams:
        stmt = stmt.bindparams(*bindparams)

    with get_engine().connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()
    return [
        {
            "source_id": int(row["source_id"]),
            "competitor": row["competitor"],
            "axis": row["axis"],
            "doc_type": row["doc_type"],
            "dimension": row["dimension"],
            "url": row["url"],
            "title": row["title"],
            "publish_date": row["publish_date"],
            "fetched_at": row["fetched_at"],
            "status": row["status"],
            "source_kind": row["source_kind"],
            "chunk_text": row["chunk_text"],
        }
        for row in rows
    ]


def update_source_dimension(
    source_id: int,
    new_dimension: str,
    *,
    reason: str,
    details: Mapping[str, Any] | None = None,
    ensure_audit: bool = True,
) -> dict[str, Any]:
    if ensure_audit:
        ensure_source_healing_audit()
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, status, dimension
                FROM sources
                WHERE id = :source_id
                FOR UPDATE
                """
            ),
            {"source_id": int(source_id)},
        ).mappings().one()

        old_dimension = row["dimension"]
        if old_dimension == new_dimension:
            return {
                "source_id": int(source_id),
                "changed": False,
                "old_dimension": old_dimension,
                "new_dimension": new_dimension,
            }

        conn.execute(
            text("UPDATE sources SET dimension = :dimension WHERE id = :source_id"),
            {"source_id": int(source_id), "dimension": new_dimension},
        )
        _insert_source_healing_audit(
            conn,
            source_id=int(source_id),
            action="dimension_update",
            old_status=row["status"],
            new_status=row["status"],
            old_dimension=old_dimension,
            new_dimension=new_dimension,
            reason=reason,
            details=details,
        )
    return {
        "source_id": int(source_id),
        "changed": True,
        "old_dimension": old_dimension,
        "new_dimension": new_dimension,
    }


def mark_source_status(
    source_id: int,
    new_status: str,
    *,
    reason: str,
    details: Mapping[str, Any] | None = None,
    ensure_audit: bool = True,
) -> dict[str, Any]:
    if ensure_audit:
        ensure_source_healing_audit()
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, status, dimension
                FROM sources
                WHERE id = :source_id
                FOR UPDATE
                """
            ),
            {"source_id": int(source_id)},
        ).mappings().one()

        old_status = row["status"]
        if old_status == new_status:
            return {
                "source_id": int(source_id),
                "changed": False,
                "old_status": old_status,
                "new_status": new_status,
            }

        conn.execute(
            text("UPDATE sources SET status = :status WHERE id = :source_id"),
            {"source_id": int(source_id), "status": new_status},
        )
        conn.execute(
            text("UPDATE chunks SET status = :status WHERE source_id = :source_id"),
            {"source_id": int(source_id), "status": new_status},
        )
        _insert_source_healing_audit(
            conn,
            source_id=int(source_id),
            action="status_update",
            old_status=old_status,
            new_status=new_status,
            old_dimension=row["dimension"],
            new_dimension=row["dimension"],
            reason=reason,
            details=details,
        )
    return {
        "source_id": int(source_id),
        "changed": True,
        "old_status": old_status,
        "new_status": new_status,
    }


def _insert_source_healing_audit(
    conn: Any,
    *,
    source_id: int,
    action: str,
    old_status: str | None,
    new_status: str | None,
    old_dimension: str | None,
    new_dimension: str | None,
    reason: str,
    details: Mapping[str, Any] | None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO source_healing_audit (
                source_id, action, old_status, new_status,
                old_dimension, new_dimension, reason, details
            )
            VALUES (
                :source_id, :action, :old_status, :new_status,
                :old_dimension, :new_dimension, :reason,
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "source_id": source_id,
            "action": action,
            "old_status": old_status,
            "new_status": new_status,
            "old_dimension": old_dimension,
            "new_dimension": new_dimension,
            "reason": reason,
            "details": json.dumps(dict(details or {}), sort_keys=True),
        },
    )


def _insert_dimension_coverage_audit(
    conn: Any,
    *,
    action: str,
    competitor: str,
    axis: str,
    dimension: str,
    old_state: str | None,
    new_state: str | None,
    source_id: int | None,
    reason: str,
    details: Mapping[str, Any] | None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO dimension_coverage_audit (
                action, competitor, axis, dimension, old_state, new_state,
                source_id, reason, details
            )
            VALUES (
                :action, :competitor, :axis, :dimension, :old_state, :new_state,
                :source_id, :reason, CAST(:details AS jsonb)
            )
            """
        ),
        {
            "action": action,
            "competitor": competitor,
            "axis": axis,
            "dimension": dimension,
            "old_state": old_state,
            "new_state": new_state,
            "source_id": source_id,
            "reason": reason,
            "details": json.dumps(dict(details or {}), sort_keys=True, default=str),
        },
    )


def source_text(source_id: int, *, status: str | None = "active") -> str:
    return _merge_chunk_texts(
        [chunk["chunk_text"] for chunk in source_chunks(source_id, status=status)]
    )


def _merge_chunk_texts(chunks: Sequence[str]) -> str:
    texts = [str(chunk or "") for chunk in chunks if str(chunk or "").strip()]
    if not texts:
        return ""

    merged = texts[0].strip()
    for text in texts[1:]:
        next_text = text.strip()
        overlap = _suffix_prefix_overlap(merged, next_text)
        if overlap:
            merged = f"{merged}{next_text[overlap:]}"
        else:
            merged = f"{merged}\n\n{next_text}"
    return merged.strip()


def _suffix_prefix_overlap(left: str, right: str, max_chars: int = 2000) -> int:
    limit = min(len(left), len(right), max_chars)
    for size in range(limit, 0, -1):
        if left.endswith(right[:size]):
            return size
    return 0


def _empty_filter(value: Sequence[Any] | None) -> bool:
    return value is not None and len(value) == 0


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


def graph_related_source_ids(
    seed_entity_names: Sequence[str],
    max_hops: int | None = None,
) -> list[int]:
    entity_ids = expand_entities(seed_entity_names, max_hops=max_hops)
    if not entity_ids:
        return []

    stmt = text(
        """
        SELECT DISTINCT r.source_id
        FROM relationships r
        JOIN sources s ON s.id = r.source_id
        WHERE r.source_id IS NOT NULL
          AND s.status = 'active'
          AND (
            r.src_entity_id IN :entity_ids
            OR r.dst_entity_id IN :entity_ids
          )
        ORDER BY r.source_id
        """
    ).bindparams(bindparam("entity_ids", expanding=True))

    with get_engine().connect() as conn:
        return [
            int(row.source_id)
            for row in conn.execute(stmt, {"entity_ids": entity_ids})
        ]


__all__ = [
    "add_relationship",
    "active_chunks",
    "citations_for_sources",
    "coverage_status",
    "dimension_coverage_status",
    "ensure_dimension_coverage_tables",
    "ensure_source_healing_audit",
    "expand_entities",
    "graph_related_source_ids",
    "healing_source_rows",
    "insert_chunks",
    "insert_dimension_coverage_assertions",
    "insert_source_citations",
    "latest_active_sources",
    "latest_fetched_at",
    "mark_source_status",
    "refresh_all_dimension_coverage_statuses",
    "refresh_dimension_coverage_status",
    "source_chunks",
    "source_exists",
    "source_text",
    "supersede_older",
    "update_source_dimension",
    "upsert_entity",
    "upsert_source",
    "vector_search",
]
