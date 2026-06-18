from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ci_engine.config import get as config_get
from ci_engine.db import repository
from ci_engine.dimension_coverage import missing_reason_for_state
from ci_engine.embed import gemini
from ci_engine.ontology import expand_dimension_aliases, normalize_dimension


def retrieve(
    query: str,
    axis: str | None = None,
    competitors: Sequence[str] | None = None,
    dimensions: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Retrieve active cited chunks for an LLM query."""
    query_text = str(query or "").strip()
    if not query_text:
        return {"chunks": [], "missing": [{"reason": "empty_query"}]}

    axis_filter = _clean_text(axis)
    competitor_filter = _clean_sequence(competitors)
    requested_dimensions = _clean_dimensions(dimensions, axis=axis_filter)
    search_dimensions = expand_dimension_aliases(requested_dimensions)
    query_embedding = gemini.embed_query(query_text)
    top_k = max(int(config_get("retrieval.top_k", 8)), 0)
    chunks = _retrieve_chunks(
        query_embedding=query_embedding,
        top_k=top_k,
        axis=axis_filter,
        competitors=competitor_filter,
        dimensions=search_dimensions,
    )
    return {
        "chunks": chunks,
        "missing": []
        if requested_dimensions is None
        else _missing(
            chunks,
            competitors=competitor_filter,
            axis=axis_filter,
            dimensions=requested_dimensions,
        ),
    }


def _retrieve_chunks(
    *,
    query_embedding: Sequence[float],
    top_k: int,
    axis: str | None,
    competitors: list[str] | None,
    dimensions: list[str] | None,
) -> list[dict[str, Any]]:
    if competitors is None or len(competitors) <= 1 or top_k <= 0:
        return repository.vector_search(
            query_embedding=query_embedding,
            top_k=top_k,
            axis=axis,
            competitors=competitors,
            dimensions=dimensions,
        )

    per_competitor = max(1, math.ceil(top_k / len(competitors)))
    chunks: list[dict[str, Any]] = []
    for competitor in competitors:
        chunks.extend(
            repository.vector_search(
                query_embedding=query_embedding,
                top_k=per_competitor,
                axis=axis,
                competitors=[competitor],
                dimensions=dimensions,
            )
        )
    return _dedupe_and_rank(chunks)[:top_k]


def _dedupe_and_rank(chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any]] = set()
    ranked: list[dict[str, Any]] = []
    for chunk in sorted(
        chunks,
        key=lambda row: float(row.get("similarity") or 0.0),
        reverse=True,
    ):
        key = (chunk.get("source_id"), chunk.get("chunk_text"))
        if key in seen:
            continue
        seen.add(key)
        ranked.append(dict(chunk))
    return ranked


def _missing(
    chunks: Sequence[Mapping[str, Any]],
    *,
    competitors: list[str] | None,
    axis: str | None,
    dimensions: list[str] | None,
) -> list[dict[str, Any]]:
    if competitors is None:
        return []

    missing: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    competitors_with_missing_coverage: set[str] = set()
    covered_requested_dimensions = _covered_requested_dimensions(chunks, dimensions)
    covered_competitors = {
        str(chunk.get("competitor"))
        for chunk in chunks
        if chunk.get("competitor") is not None
    }

    for row in repository.dimension_coverage_status(
        competitors=competitors,
        axis=axis,
        dimensions=dimensions,
    ):
        competitor = row.get("competitor")
        row_axis = row.get("axis")
        dimension = row.get("dimension")
        key = (str(competitor), str(row_axis), str(dimension))
        is_covered = key in covered_requested_dimensions
        state = str(row.get("state") or "unknown")

        if state in {"present", "unknown"} and is_covered:
            continue

        seen.add(key)
        competitors_with_missing_coverage.add(str(competitor))
        reason = _dimension_missing_reason(
            state=state,
            active_sources=int(row.get("active_assertions") or 0),
        )
        missing.append(
            {
                "competitor": competitor,
                "axis": row_axis,
                "dimension": dimension,
                "reason": reason,
                "coverage_state": state,
                "coverage_confidence": row.get("confidence"),
                "coverage_conflict": row.get("conflict"),
            }
        )

    for competitor in competitors:
        if competitor in covered_competitors:
            continue
        if competitor in competitors_with_missing_coverage:
            continue
        key = (competitor, axis, None)
        if key in seen:
            continue
        missing.append(
            {
                "competitor": competitor,
                "axis": axis,
                "dimension": None,
                "reason": "no_matching_chunks",
            }
        )

    return missing


def _dimension_missing_reason(*, state: str, active_sources: int) -> str:
    _ = active_sources
    if state in {"absent", "planned", "partial"}:
        return missing_reason_for_state(state)
    if state == "present":
        return "no_matching_chunks"
    return "unknown_coverage"


def _covered_requested_dimensions(
    chunks: Sequence[Mapping[str, Any]],
    dimensions: list[str] | None,
) -> set[tuple[str, str, str]]:
    if not dimensions:
        return set()

    aliases_by_dimension = {
        dimension: set(expand_dimension_aliases([dimension]) or [dimension])
        for dimension in dimensions
    }
    covered: set[tuple[str, str, str]] = set()
    for chunk in chunks:
        competitor = chunk.get("competitor")
        chunk_axis = chunk.get("axis")
        if competitor is None or chunk_axis is None:
            continue

        chunk_dimensions = _chunk_dimension_candidates(chunk)
        for requested_dimension, aliases in aliases_by_dimension.items():
            if chunk_dimensions.isdisjoint(aliases):
                continue
            for covered_axis in _covered_axes(str(chunk_axis)):
                covered.add((str(competitor), covered_axis, requested_dimension))

    return covered


def _covered_axes(axis: str) -> set[str]:
    if axis == "both":
        return {"both", "technical", "business"}
    return {axis}


def _chunk_dimension_candidates(chunk: Mapping[str, Any]) -> set[str]:
    raw_dimension = _clean_text(chunk.get("dimension"))
    if raw_dimension is None:
        return set()

    candidates = {raw_dimension}
    normalized = normalize_dimension(
        raw_dimension,
        axis=_clean_text(chunk.get("axis")),
        url=_clean_text(chunk.get("url")),
        text=_clean_text(chunk.get("chunk_text")),
    )
    if normalized:
        candidates.add(normalized)
    return candidates


def _clean_sequence(values: Sequence[str] | None) -> list[str] | None:
    if values is None:
        return None
    cleaned = [str(value).strip() for value in values if str(value or "").strip()]
    return cleaned


def _clean_dimensions(values: Sequence[str] | None, *, axis: str | None) -> list[str] | None:
    cleaned = _clean_sequence(values)
    if cleaned is None:
        return None
    return [
        normalize_dimension(value, axis=axis) or value
        for value in cleaned
    ]


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


__all__ = ["retrieve"]
