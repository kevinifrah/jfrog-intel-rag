import random
from datetime import date
from uuid import uuid4

import pytest
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
from google.auth.transport.requests import Request
from pgvector import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.exc import DatabaseError

from ci_engine.config import get as config_get


def _is_iam_auth_failure(exc: DatabaseError) -> bool:
    return "Cloud SQL IAM service account authentication failed" in str(exc)


def _require_gcp_credentials():
    try:
        import google.auth

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not credentials.valid:
            credentials.refresh(Request())
    except (DefaultCredentialsError, RefreshError, TransportError) as exc:
        pytest.skip(f"GCP application default credentials are not usable: {exc}")


def _random_vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    dimensions = int(config_get("embedding.dimensions", 1536))
    return [rng.uniform(-1.0, 1.0) for _ in range(dimensions)]


def test_vector_search_orders_chunks_by_similarity():
    _require_gcp_credentials()

    from ci_engine.db import connection, repository

    marker = uuid4().hex
    competitor = f"RepositoryTest-{marker}"
    source_id = None
    query_embedding = _random_vector(1)
    less_similar_embedding = _random_vector(2)
    near_chunk = f"nearest chunk {marker}"
    far_chunk = f"farther chunk {marker}"
    inactive_chunk = f"inactive chunk {marker}"

    try:
        source_id = repository.upsert_source(
            competitor=competitor,
            axis="technical",
            doc_type="docs",
            dimension="software_composition_analysis",
            url=f"https://example.invalid/repository-test/{marker}",
            title="Repository integration test",
            publish_date=date.today(),
            content_hash=marker,
            raw_path=f"test/{marker}.txt",
        )
        repository.insert_chunks(
            source_id=source_id,
            competitor=competitor,
            axis="technical",
            doc_type="docs",
            publish_date=date.today(),
            chunks=[
                (near_chunk, query_embedding),
                (far_chunk, less_similar_embedding),
            ],
        )
        with connection.get_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO chunks (
                        source_id, competitor, axis, doc_type, publish_date,
                        status, chunk_text, embedding
                    )
                    VALUES (
                        :source_id, :competitor, 'technical', 'docs', :publish_date,
                        'stale', :chunk_text, CAST(:embedding AS vector)
                    )
                    """
                ),
                {
                    "source_id": source_id,
                    "competitor": competitor,
                    "publish_date": date.today(),
                    "chunk_text": inactive_chunk,
                    "embedding": Vector(query_embedding).to_text(),
                },
            )

        results = repository.vector_search(
            query_embedding=query_embedding,
            top_k=2,
            similarity_threshold=-1.0,
            axis="technical",
            competitors=[competitor],
        )
    except (DefaultCredentialsError, RefreshError, TransportError) as exc:
        pytest.skip(f"GCP application default credentials are not usable: {exc}")
    except DatabaseError as exc:
        if _is_iam_auth_failure(exc):
            pytest.skip(f"GCP credentials cannot authenticate as the CI DB user: {exc}")
        raise
    finally:
        if source_id is not None:
            with connection.get_engine().begin() as conn:
                conn.execute(
                    text("DELETE FROM sources WHERE id = :source_id"),
                    {"source_id": source_id},
                )
        connection.close_engine()

    assert [row["chunk_text"] for row in results] == [near_chunk, far_chunk]
    assert inactive_chunk not in {row["chunk_text"] for row in results}
    assert results[0]["similarity"] > results[1]["similarity"]
    assert results[0]["url"].endswith(marker)


def test_expand_entities_returns_reachable_ids():
    _require_gcp_credentials()

    from ci_engine.db import connection, repository

    marker = uuid4().hex
    names = [
        f"entity-a-{marker}",
        f"entity-b-{marker}",
        f"entity-c-{marker}",
        f"entity-d-{marker}",
    ]
    entity_ids: list[int] = []

    try:
        entity_ids = [
            repository.upsert_entity(name, "repository_test", "RepositoryTest")
            for name in names
        ]
        a_id, b_id, c_id, d_id = entity_ids
        repository.add_relationship(a_id, b_id, "links_to", None)
        repository.add_relationship(b_id, c_id, "links_to", None)
        repository.add_relationship(c_id, d_id, "links_to", None)

        one_hop = repository.expand_entities([names[0]], max_hops=1)
        two_hops = repository.expand_entities([names[0]], max_hops=2)
    except (DefaultCredentialsError, RefreshError, TransportError) as exc:
        pytest.skip(f"GCP application default credentials are not usable: {exc}")
    except DatabaseError as exc:
        if _is_iam_auth_failure(exc):
            pytest.skip(f"GCP credentials cannot authenticate as the CI DB user: {exc}")
        raise
    finally:
        if entity_ids:
            with connection.get_engine().begin() as conn:
                conn.execute(
                    text("DELETE FROM entities WHERE id IN :entity_ids").bindparams(
                        bindparam("entity_ids", expanding=True)
                    ),
                    {"entity_ids": entity_ids},
                )
        connection.close_engine()

    assert set(one_hop) == {a_id, b_id}
    assert set(two_hops) == {a_id, b_id, c_id}
    assert d_id not in two_hops
