from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from ci_engine.acquire import relevance, web_lane
from ci_engine.acquire.snapshots import snapshot_path
from ci_engine.config import get as config_get
from ci_engine.db import repository
from ci_engine import dimension_coverage
from ci_engine.embed import gemini
from ci_engine.ontology import axis_for_dimension, normalize_dimension
from ci_engine.synthesize import compiler

logger = logging.getLogger(__name__)


def ingest_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(candidate)
    competitor = str(_required(candidate, "competitor"))
    url = str(_required(candidate, "url"))

    text, fetched = _candidate_text(candidate, competitor)
    if not text:
        raise ValueError("candidate has no text to ingest")

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    candidate.update(_candidate_metadata_from_fetch(candidate, fetched))
    candidate["content_excerpt"] = _content_excerpt(text)

    relevance_result = _trusted_report_relevance(candidate)
    if relevance_result is None:
        relevance_result = _scoped_verdict_relevance(candidate)
    if relevance_result is None:
        relevance_result = relevance.score(candidate)
        if _is_below_threshold(relevance_result):
            return {
                "skipped": relevance_result.get("reason") or "below relevance threshold",
                "score": relevance_result.get("score"),
            }

    meta = _source_meta(candidate, relevance_result)
    citations = _candidate_citations(candidate, text, meta)
    dedup_hit = repository.source_exists(url, content_hash)
    if dedup_hit:
        source_fields = _source_fields(meta, {})
        source_id = repository.upsert_source(
            competitor=competitor,
            axis=source_fields["axis"],
            doc_type=source_fields["doc_type"],
            dimension=source_fields["dimension"],
            url=url,
            title=meta.get("title"),
            publish_date=meta.get("publish_date"),
            content_hash=content_hash,
            raw_path=_raw_path(candidate, fetched, competitor, url),
            source_kind=meta.get("source_kind"),
        )
        if citations:
            repository.insert_source_citations(source_id, citations)
        _store_coverage_assertions(
            source_id,
            meta={**meta, **source_fields},
            synthesis={},
            text=text,
            reason="duplicate_ingestion",
        )
        return {
            "skipped": "duplicate content",
            "source_id": source_id,
            "n_chunks": 0,
            "n_entities": 0,
            "n_edges": 0,
            "superseded": 0,
            "conflicts": [],
        }

    synthesis = compiler.synthesize(text, meta)
    source_fields = _source_fields(meta, synthesis)

    source_id = repository.upsert_source(
        competitor=competitor,
        axis=source_fields["axis"],
        doc_type=source_fields["doc_type"],
        dimension=source_fields["dimension"],
        url=url,
        title=meta.get("title"),
        publish_date=meta.get("publish_date"),
        content_hash=content_hash,
        raw_path=_raw_path(candidate, fetched, competitor, url),
        source_kind=meta.get("source_kind"),
    )
    if citations:
        repository.insert_source_citations(source_id, citations)
    _store_coverage_assertions(
        source_id,
        meta={**meta, **source_fields},
        synthesis=synthesis,
        text=text,
        reason="ingestion",
    )

    chunks = chunk_text(str(synthesis["compiled"]))
    embeddings = gemini.embed_documents(chunks)
    repository.insert_chunks(
        source_id=source_id,
        competitor=competitor,
        axis=source_fields["axis"],
        doc_type=source_fields["doc_type"],
        publish_date=meta.get("publish_date"),
        chunks=list(zip(chunks, embeddings, strict=True)),
    )

    entity_ids = _upsert_entities(synthesis.get("entities", []), competitor)
    edge_ids = _upsert_relationships(
        synthesis.get("relationships", []),
        entity_ids,
        competitor,
        source_id,
    )
    superseded = repository.supersede_older(
        competitor,
        source_fields["dimension"],
        meta.get("publish_date"),
    )

    return {
        "source_id": source_id,
        "n_chunks": len(chunks),
        "n_entities": len(entity_ids),
        "n_edges": len(edge_ids),
        "superseded": superseded,
        "conflicts": synthesis.get("conflicts", []),
    }


def _candidate_citations(
    candidate: dict[str, Any],
    text: str,
    meta: dict[str, Any],
) -> list[dict[str, str | None]]:
    is_report_slice = meta.get("source_kind") == "official_llm_research_report"
    citations = web_lane.normalize_citations(
        candidate.get("citations"),
        fallback_text=text if is_report_slice else None,
    )
    if is_report_slice and not citations:
        logger.warning(
            "Official research report slice has no extracted citations",
            extra={
                "candidate_url": candidate.get("url"),
                "candidate_competitor": candidate.get("competitor"),
                "candidate_dimension": candidate.get("dimension"),
            },
        )
    return citations


def chunk_text(text: str) -> list[str]:
    chunk_size = int(config_get("chunking.chunk_size", 1200))
    overlap = int(config_get("chunking.chunk_overlap", 150))
    if chunk_size <= 0:
        raise ValueError("chunking.chunk_size must be positive")
    overlap = max(0, min(overlap, chunk_size - 1))

    stripped = text.strip()
    if not stripped:
        return []

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", stripped)
        if paragraph.strip()
    ]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_paragraph(paragraph, chunk_size, overlap))
            continue

        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current.strip())
        current = paragraph

    if current:
        chunks.append(current.strip())

    return [chunk for chunk in chunks if chunk]


def _split_long_paragraph(
    paragraph: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    chunks: list[str] = []
    words = paragraph.split()
    current_words: list[str] = []
    current_length = 0

    for word in words:
        extra = len(word) + (1 if current_words else 0)
        if current_words and current_length + extra > chunk_size:
            chunks.append(" ".join(current_words))
            current_words = _overlap_words(current_words, overlap)
            current_length = len(" ".join(current_words))
        current_words.append(word)
        current_length += extra

    if current_words:
        chunks.append(" ".join(current_words))
    return chunks


def _overlap_words(words: list[str], overlap: int) -> list[str]:
    if overlap <= 0:
        return []

    selected: list[str] = []
    total = 0
    for word in reversed(words):
        extra = len(word) + (1 if selected else 0)
        if selected and total + extra > overlap:
            break
        selected.append(word)
        total += extra
    selected.reverse()
    return selected


def _candidate_text(
    candidate: dict[str, Any],
    competitor: str,
) -> tuple[str, dict[str, Any] | None]:
    text = candidate.get("text") or candidate.get("raw_text")
    if text:
        return str(text), None

    fetched = web_lane.fetch(str(_required(candidate, "url")), competitor=competitor)
    fetched_text = fetched.get("text")
    if fetched_text:
        return str(fetched_text), fetched
    return str(candidate.get("snippet") or ""), fetched


def _candidate_metadata_from_fetch(
    candidate: dict[str, Any],
    fetched: dict[str, Any] | None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if fetched is not None:
        updates["title"] = candidate.get("title") or fetched.get("title")
        updates["published"] = candidate.get("published") or fetched.get("published")
    updates["snippet"] = candidate.get("snippet") or _snippet(
        candidate.get("text") or candidate.get("raw_text") or ""
    )
    return updates


def _content_excerpt(text: str) -> str:
    limit = int(config_get("ingestion.relevance_content_chars", 6000))
    stripped = text.strip()
    if limit <= 0 or len(stripped) <= limit:
        return stripped
    return stripped[:limit].rstrip()


def _source_meta(
    candidate: dict[str, Any],
    relevance_result: dict[str, Any],
) -> dict[str, Any]:
    candidate_axis = candidate.get("axis")
    candidate_dimension = normalize_dimension(
        candidate.get("dimension"),
        axis=str(candidate_axis) if candidate_axis else None,
        title=candidate.get("title"),
        url=candidate.get("url"),
        text=candidate.get("content_excerpt") or candidate.get("snippet"),
    )
    relevance_axis = relevance_result.get("axis") or candidate_axis
    relevance_dimension = normalize_dimension(
        relevance_result.get("dimension"),
        axis=str(relevance_axis) if relevance_axis else None,
        title=candidate.get("title"),
        url=candidate.get("url"),
        text=candidate.get("content_excerpt") or candidate.get("snippet"),
    )
    return {
        "competitor": candidate.get("competitor"),
        "url": candidate.get("url"),
        "title": candidate.get("title"),
        "publish_date": web_lane.parse_date(candidate.get("published")),
        "axis": candidate.get("axis") or relevance_result.get("axis"),
        "doc_type": relevance_result.get("doc_type") or candidate.get("doc_type"),
        "dimension": candidate_dimension or relevance_dimension,
        "relevance_score": relevance_result.get("score"),
        "source_kind": candidate.get("source_kind") or "unknown",
        "source_reason": candidate.get("source_reason"),
        "evidence_state": candidate.get("evidence_state")
        or relevance_result.get("evidence_state"),
        "coverage_gap": candidate.get("coverage_gap"),
        "coverage_verdict": candidate.get("coverage_verdict"),
    }


def _source_fields(
    meta: dict[str, Any],
    synthesis: dict[str, Any],
) -> dict[str, Any]:
    axis = meta.get("axis") or synthesis.get("axis") or "both"
    dimension = (
        meta.get("dimension")
        or normalize_dimension(
            synthesis.get("dimension"),
            axis=str(axis),
            title=meta.get("title"),
            url=meta.get("url"),
            text=synthesis.get("compiled"),
        )
        or normalize_dimension(
            _first_fact_dimension(synthesis.get("facts", [])),
            axis=str(axis),
            title=meta.get("title"),
            url=meta.get("url"),
            text=synthesis.get("compiled"),
        )
    )
    return {
        "axis": axis,
        "doc_type": synthesis.get("doc_type") or meta.get("doc_type") or "docs",
        "dimension": dimension,
    }


def _store_coverage_assertions(
    source_id: int,
    *,
    meta: dict[str, Any],
    synthesis: dict[str, Any],
    text: str,
    reason: str,
) -> None:
    assertions = dimension_coverage.source_assertions(
        source_id=source_id,
        meta=meta,
        synthesis=synthesis,
        text=text,
    )
    inserted = repository.insert_dimension_coverage_assertions(
        source_id,
        assertions,
        reason=reason,
    )
    if inserted <= 0:
        return

    refreshed: set[tuple[str, str, str]] = set()
    for assertion in assertions:
        competitor = assertion.get("competitor")
        dimension = assertion.get("dimension")
        if not competitor or not dimension:
            continue
        axes = _status_axes(str(assertion.get("axis") or "both"), str(dimension))
        for axis in axes:
            key = (str(competitor), axis, str(dimension))
            if key in refreshed:
                continue
            refreshed.add(key)
            repository.refresh_dimension_coverage_status(
                str(competitor),
                axis,
                str(dimension),
                reason=reason,
            )


def _status_axes(axis: str, dimension: str) -> set[str]:
    canonical_axis = axis_for_dimension(dimension)
    if axis == "both":
        return {canonical_axis} if canonical_axis else {"technical", "business"}
    return {axis}


def _first_fact_dimension(facts: Any) -> str | None:
    if not isinstance(facts, list):
        return None
    for fact in facts:
        if isinstance(fact, dict) and fact.get("dimension"):
            return str(fact["dimension"])
    return None


def _upsert_entities(
    entities: Any,
    default_competitor: str,
) -> dict[str, int]:
    entity_ids: dict[str, int] = {}
    if not isinstance(entities, list):
        return entity_ids

    for entity in entities:
        if not isinstance(entity, dict) or not entity.get("name"):
            continue
        name = str(entity["name"])
        entity_ids[name] = repository.upsert_entity(
            name=name,
            entity_type=str(entity.get("entity_type") or "feature"),
            competitor=entity.get("competitor") or default_competitor,
        )
    return entity_ids


def _upsert_relationships(
    relationships: Any,
    entity_ids: dict[str, int],
    competitor: str,
    source_id: int,
) -> set[int]:
    edge_ids: set[int] = set()
    if not isinstance(relationships, list):
        return edge_ids

    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        src = relationship.get("src")
        dst = relationship.get("dst")
        relation = relationship.get("relation")
        if not src or not dst or not relation:
            continue

        src_id = entity_ids.get(str(src)) or _upsert_placeholder_entity(
            str(src), competitor
        )
        dst_id = entity_ids.get(str(dst)) or _upsert_placeholder_entity(
            str(dst), competitor
        )
        entity_ids[str(src)] = src_id
        entity_ids[str(dst)] = dst_id
        edge_ids.add(
            repository.add_relationship(
                src_id=src_id,
                dst_id=dst_id,
                relation=str(relation),
                source_id=source_id,
            )
        )

    return edge_ids


def _upsert_placeholder_entity(name: str, competitor: str) -> int:
    return repository.upsert_entity(
        name=name,
        entity_type="feature",
        competitor=competitor,
    )


def _is_below_threshold(relevance_result: dict[str, Any]) -> bool:
    score = float(relevance_result.get("score", 0.0))
    threshold = float(config_get("ingestion.relevance_threshold", 0.6))
    return not relevance_result.get("relevant", False) or score < threshold


def _trusted_report_relevance(candidate: dict[str, Any]) -> dict[str, Any] | None:
    if candidate.get("source_kind") != "official_llm_research_report":
        return None
    return {
        "relevant": True,
        "score": 1.0,
        "axis": candidate.get("axis") or "both",
        "doc_type": candidate.get("doc_type") or "company_fact",
        "dimension": candidate.get("dimension"),
        "reason": "trusted official research report slice",
    }


def _scoped_verdict_relevance(candidate: dict[str, Any]) -> dict[str, Any] | None:
    verdict = candidate.get("coverage_verdict")
    if not isinstance(verdict, dict):
        return None

    verdict_state = str(verdict.get("state") or "").strip().lower()
    state = "absent" if verdict_state == "explicit_absent" else verdict_state
    if dimension_coverage.normalize_state(state) not in {
        "present",
        "partial",
        "planned",
        "absent",
    }:
        return None

    try:
        confidence = float(verdict.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    score = max(min(confidence, 1.0), float(config_get("ingestion.relevance_threshold", 0.6)))
    return {
        "relevant": True,
        "score": score,
        "axis": candidate.get("axis") or "both",
        "doc_type": candidate.get("doc_type") or "docs",
        "dimension": candidate.get("dimension"),
        "evidence_state": candidate.get("evidence_state") or state,
        "reason": "accepted scoped coverage verdict",
    }


def _raw_path(
    candidate: dict[str, Any],
    fetched: dict[str, Any] | None,
    competitor: str,
    url: str,
) -> str | None:
    raw_path = candidate.get("raw_path")
    if raw_path:
        return str(raw_path)
    if fetched is None:
        return None
    return str(snapshot_path(competitor=competitor, title=None, url=url))


def _required(candidate: dict[str, Any], key: str) -> Any:
    value = candidate.get(key)
    if value in (None, ""):
        raise ValueError(f"candidate is missing required field: {key}")
    return value


def _snippet(text: str, limit: int = 600) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


__all__ = ["chunk_text", "ingest_candidate"]
