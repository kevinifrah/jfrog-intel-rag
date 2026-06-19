from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ci_engine import retrieve as retriever
from ci_engine.config import get as config_get
from ci_engine.crews.report.capabilities import (
    CAPABILITY_DEFINITIONS,
    build_capability_artifacts,
    capability_query_plan,
)
from ci_engine.crews.report.schemas import EvidenceGap, EvidenceItem, TargetedSearchAttempt
from ci_engine.crews.report.sections import ReportSectionSpec, section_specs
from ci_engine.db import repository
from ci_engine.dimension_coverage import missing_reason_for_state

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8080))
STREAMABLE_HTTP_PATH = "/mcp"

_LOCAL_ALLOWED_HOSTS = [
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
    "[::1]",
    "[::1]:*",
]
_LOCAL_ALLOWED_ORIGINS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
    "http://[::1]:*",
]


class SharedTokenMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        expected = _shared_token()
        if expected and not _authorization_matches(_authorization_header(scope), expected):
            response = PlainTextResponse("unauthorized", status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _shared_token() -> str | None:
    token = os.environ.get("MCP_SHARED_TOKEN")
    if token is None:
        return None
    token = token.strip()
    return token or None


def _authorization_header(scope: Scope) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == b"authorization":
            return value.decode("latin1").strip()
    return ""


def _authorization_matches(header: str, expected: str) -> bool:
    return hmac.compare_digest(header, expected) or hmac.compare_digest(
        header,
        f"Bearer {expected}",
    )


def _csv_env(name: str, default: Sequence[str]) -> list[str]:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return list(default)
    return [part.strip() for part in value.split(",") if part.strip()]


def _allowed_hosts() -> list[str]:
    return _csv_env("MCP_ALLOWED_HOSTS", _LOCAL_ALLOWED_HOSTS)


def _allowed_origins() -> list[str]:
    return _csv_env("MCP_ALLOWED_ORIGINS", _LOCAL_ALLOWED_ORIGINS)


def _transport_security_settings() -> TransportSecuritySettings:
    # Host and Origin validation protects browser-capable MCP clients from
    # DNS-rebinding requests against this local/deployed HTTP server.
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(),
        allowed_origins=_allowed_origins(),
    )


mcp = FastMCP(
    "ci-engine-retrieval",
    host=HOST,
    port=PORT,
    streamable_http_path=STREAMABLE_HTTP_PATH,
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security_settings(),
)


@mcp.tool()
def search(
    query: str,
    axis: str | None = None,
    competitors: list[str] | None = None,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Search active cited competitor knowledge."""
    return _jsonable(
        retriever.retrieve(
            query=query,
            axis=_clean_text(axis),
            competitors=_clean_list(competitors),
            dimensions=_clean_list(dimensions),
        )
    )


@mcp.tool()
def get_competitor(name: str, axis: str | None = None) -> dict[str, Any]:
    """Get active cited knowledge for one competitor."""
    competitor = _required_text(name, "name")
    axis_filter = _clean_text(axis)
    chunks = repository.active_chunks(
        competitors=[competitor],
        axis=axis_filter,
    )
    return _jsonable(
        {
            "competitor": competitor,
            "axis": axis_filter,
            "dimensions": _group_chunks_by_dimension(chunks),
            "missing": _missing_coverage(
                competitors=[competitor],
                axis=axis_filter,
            ),
        }
    )


@mcp.tool()
def compare_competitors(
    names: list[str],
    dimension: str | None = None,
) -> dict[str, Any]:
    """Compare competitors using active cited chunks and graph-linked sources."""
    competitors = _clean_list(names) or []
    dimension_filter = _clean_text(dimension)
    dimensions = [dimension_filter] if dimension_filter else None
    if not competitors:
        return {"competitors": [], "missing": [{"reason": "empty_names"}]}

    results: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = _missing_coverage(
        competitors=competitors,
        dimensions=dimensions,
    )
    max_graph_hops = int(config_get("retrieval.max_graph_hops", 2))

    for competitor in competitors:
        direct_chunks = repository.active_chunks(
            competitors=[competitor],
            dimensions=dimensions,
        )
        source_ids = repository.graph_related_source_ids(
            [competitor],
            max_hops=max_graph_hops,
        )
        graph_chunks = (
            repository.active_chunks(
                competitors=[competitor],
                dimensions=dimensions,
                source_ids=source_ids,
            )
            if source_ids
            else []
        )
        chunks = _dedupe_chunks([*direct_chunks, *graph_chunks])
        if not chunks:
            missing.append(
                {
                    "competitor": competitor,
                    "axis": None,
                    "dimension": dimension_filter,
                    "reason": "no_matching_chunks",
                }
            )
        results.append(
            {
                "competitor": competitor,
                "dimensions": _group_chunks_by_dimension(chunks),
            }
        )

    return _jsonable(
        {
            "competitors": results,
            "missing": _dedupe_missing(missing),
        }
    )


@mcp.tool()
def latest_updates(competitor: str | None = None, days: int = 7) -> dict[str, Any]:
    """Return active sources fetched in the recent window."""
    competitor_filter = _clean_text(competitor)
    window_days = max(int(days), 0)
    fetched_since = datetime.now(timezone.utc) - timedelta(days=window_days)
    sources = repository.latest_active_sources(
        competitor=competitor_filter,
        fetched_since=fetched_since,
    )
    chunks_by_source = _chunks_by_source_id(
        repository.active_chunks(
            source_ids=[source["source_id"] for source in sources],
        )
    )
    return _jsonable(
        {
            "competitor": competitor_filter,
            "days": window_days,
            "sources": [
                {
                    **_format_source(source),
                    "chunks": chunks_by_source.get(source["source_id"], []),
                }
                for source in sources
            ],
        }
    )


@mcp.tool()
def coverage_status() -> dict[str, Any]:
    """Return active source coverage by competitor and dimension."""
    rows = repository.coverage_status()
    return _jsonable(
        {
            "coverage": rows,
            "missing": _missing_coverage(),
        }
    )


@mcp.tool()
def coverage_matrix(
    competitors: list[str] | None = None,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Return report-friendly coverage/freshness/confidence by company/dimension."""
    competitor_filter = _clean_list(competitors)
    dimension_filter = _clean_list(dimensions)
    source_rows = [
        row
        for row in repository.coverage_status()
        if _row_in_filter(row, "competitor", competitor_filter)
        and _row_in_filter(row, "dimension", dimension_filter)
    ]
    status_rows = repository.dimension_coverage_status(
        competitors=competitor_filter,
        dimensions=dimension_filter,
    )
    status_index = {
        (
            str(row.get("competitor")),
            str(row.get("axis")),
            str(row.get("dimension")),
        ): row
        for row in status_rows
    }
    matrix: list[dict[str, Any]] = []
    for row in source_rows:
        status = status_index.get(
            (
                str(row.get("competitor")),
                str(row.get("axis")),
                str(row.get("dimension")),
            ),
            {},
        )
        matrix.append(
            {
                **dict(row),
                "coverage_state": status.get("state", "unknown"),
                "coverage_confidence": status.get("confidence", 0.0),
                "coverage_conflict": bool(status.get("conflict", False)),
                "strongest_source_id": status.get("strongest_source_id"),
                "updated_at": status.get("updated_at"),
            }
        )
    return _jsonable({"coverage": matrix})


@mcp.tool()
def find_evidence_gaps(
    competitors: list[str] | None = None,
    axis: str | None = None,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Return missing/stale/partial/unknown coverage gaps for report planning."""
    return _jsonable(
        {
            "missing": _missing_coverage(
                competitors=_clean_list(competitors),
                axis=_clean_text(axis),
                dimensions=_clean_list(dimensions),
            )
        }
    )


@mcp.tool()
def compare_dimension(names: list[str], dimension: str) -> dict[str, Any]:
    """Return side-by-side evidence for one ontology dimension."""
    return compare_competitors(names=names, dimension=dimension)


@mcp.tool()
def get_source_detail(source_ids: list[int]) -> dict[str, Any]:
    """Return source metadata and active chunks for report audit trails."""
    cleaned_ids = _clean_int_list(source_ids)
    if not cleaned_ids:
        return {"sources": [], "missing": [{"reason": "empty_source_ids"}]}

    chunks = repository.active_chunks(source_ids=cleaned_ids)
    grouped: dict[int, dict[str, Any]] = {}
    for chunk in chunks:
        source_id = int(chunk["source_id"])
        source = grouped.setdefault(source_id, _source_from_chunk(chunk))
        source["chunks"].append(_format_chunk(chunk))

    found_ids = set(grouped)
    return _jsonable(
        {
            "sources": list(grouped.values()),
            "missing": [
                {"source_id": source_id, "reason": "no_active_source_or_chunks"}
                for source_id in cleaned_ids
                if source_id not in found_ids
            ],
        }
    )


@mcp.tool()
def build_report_evidence_pack(
    competitor: str,
    focus: str | None = None,
    sections: list[str] | None = None,
) -> dict[str, Any]:
    """Build a frozen DB-only evidence pack for report generation."""
    from ci_engine.crews.report.evidence import build_evidence_pack_for_competitor

    pack = build_evidence_pack_for_competitor(
        _required_text(competitor, "competitor"),
        focus=_clean_text(focus),
        sections=_clean_list(sections),
        include_web=False,
    )
    return pack.model_dump(mode="json")


@mcp.tool()
def build_report_section_evidence(
    competitor: str,
    focus: str | None = None,
    sections: list[str] | None = None,
    max_chunks_per_company_section: int = 8,
) -> dict[str, Any]:
    """Build DB-backed evidence for the main report sections in one batch."""
    competitor_name = _required_text(competitor, "competitor")
    focus_filter = _clean_text(focus)
    specs = section_specs(_clean_list(sections))
    row_limit = max(int(max_chunks_per_company_section or 8), 1)
    companies = ["JFrog", competitor_name]
    dimensions = sorted({dimension for spec in specs for dimension in spec.dimensions})
    chunks = repository.active_chunks(
        competitors=companies,
        dimensions=dimensions,
    )
    items: list[EvidenceItem] = []
    gaps: list[EvidenceGap] = []
    coverage: list[dict[str, Any]] = []

    for spec in specs:
        for company in companies:
            candidates = _section_chunk_candidates(
                chunks,
                company=company,
                spec=spec,
            )
            selected_chunks = _rank_section_chunks(
                candidates,
                spec=spec,
                company=company,
                competitor=competitor_name,
                focus=focus_filter,
            )[:row_limit]
            section_items = [
                item
                for index, chunk in enumerate(selected_chunks)
                if (
                    item := _section_evidence_from_chunk(
                        chunk,
                        spec=spec,
                        company=company,
                        index=index,
                    )
                )
                is not None
            ]
            items.extend(section_items)
            dimension_counts = _dimension_counts(candidates)
            missing_dimensions = [
                dimension
                for dimension in spec.dimensions
                if dimension_counts.get(dimension, 0) == 0
            ]
            coverage.append(
                {
                    "company": company,
                    "section_id": spec.id,
                    "axis": spec.axis,
                    "requested_dimensions": list(spec.dimensions),
                    "dimensions_with_evidence": sorted(dimension_counts),
                    "missing_dimensions": missing_dimensions,
                    "candidate_count": len(candidates),
                    "result_count": len(section_items),
                    "status": "supported" if section_items else "no_matching_chunks",
                }
            )
            if not section_items:
                gaps.append(
                    EvidenceGap(
                        company=company,
                        report_section=spec.id,
                        axis=spec.axis,
                        reason="no_matching_chunks",
                        detail=(
                            f"{company}/{spec.id}: no DB chunks matched the "
                            "section dimensions during batch retrieval."
                        ),
                    )
                )

    return _jsonable(
        {
            "competitor": competitor_name,
            "items": [item.model_dump(mode="json") for item in items],
            "gaps": [gap.model_dump(mode="json") for gap in _dedupe_report_gaps(gaps)],
            "coverage": coverage,
            "metadata": {
                "companies": companies,
                "sections": [spec.id for spec in specs],
                "dimensions": dimensions,
                "db_chunk_count": len(chunks),
                "max_chunks_per_company_section": row_limit,
            },
        }
    )


@mcp.tool()
def build_capability_evidence_matrix(
    competitor: str,
    focus: str | None = None,
    max_chunks_per_company_capability: int = 4,
) -> dict[str, Any]:
    """Build DB-backed product capability evidence for JFrog and one competitor."""
    competitor_name = _required_text(competitor, "competitor")
    focus_filter = _clean_text(focus)
    row_limit = max(int(max_chunks_per_company_capability or 4), 1)
    companies = ["JFrog", competitor_name]
    dimensions = sorted({capability.dimension for capability in CAPABILITY_DEFINITIONS})
    chunks = repository.active_chunks(
        competitors=companies,
        axis="technical",
        dimensions=dimensions,
    )
    chunk_index = _capability_chunk_index(chunks)
    items: list[EvidenceItem] = []
    gaps: list[EvidenceGap] = []
    attempts: list[TargetedSearchAttempt] = []

    for company in companies:
        for capability, query in capability_query_plan(
            company,
            competitor=competitor_name,
            focus=focus_filter,
        ):
            selected_chunks = _rank_capability_chunks(
                chunk_index.get((company.lower(), capability.dimension), ()),
                capability,
            )[:row_limit]
            capability_items = [
                item
                for index, chunk in enumerate(selected_chunks)
                if (
                    item := _capability_evidence_from_chunk(
                        chunk,
                        company=company,
                        capability=capability,
                        query=query,
                        index=index,
                    )
                )
                is not None
            ]
            items.extend(capability_items)
            attempts.append(
                TargetedSearchAttempt(
                    company=company,
                    capability_id=capability.id,
                    capability_label=capability.label,
                    source="db",
                    query=query,
                    result_count=len(capability_items),
                    status="supported" if capability_items else "not_found_after_search",
                )
            )
            if capability.must_resolve and not capability_items:
                gaps.append(
                    EvidenceGap(
                        company=company,
                        report_section="product_feature_analysis",
                        axis="technical",
                        dimension=capability.dimension,
                        reason="not_found_after_search",
                        detail=(
                            f"{company}: no DB evidence found for "
                            f"{capability.label} after batch capability retrieval."
                        ),
                    )
                )

    matrix, product_catalog, matrix_gaps = build_capability_artifacts(
        competitor_name,
        items=items,
        attempts=attempts,
    )
    return _jsonable(
        {
            "competitor": competitor_name,
            "items": [item.model_dump(mode="json") for item in items],
            "gaps": [
                gap.model_dump(mode="json")
                for gap in _dedupe_report_gaps([*gaps, *matrix_gaps])
            ],
            "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
            "capability_matrix": matrix.model_dump(mode="json"),
            "product_catalog": [
                item.model_dump(mode="json") for item in product_catalog
            ],
            "metadata": {
                "companies": companies,
                "dimensions": dimensions,
                "db_chunk_count": len(chunks),
                "max_chunks_per_company_capability": row_limit,
            },
        }
    )


@mcp.tool()
def source_inventory(
    competitors: list[str] | None = None,
    dimensions: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return active source inventory for report evidence census."""
    return _jsonable(
        {
            "sources": repository.source_inventory(
                competitors=_clean_list(competitors),
                dimensions=_clean_list(dimensions),
                limit=limit,
            )
        }
    )


@mcp.tool()
def get_report_registry(report_root: str | None = None) -> dict[str, Any]:
    """List generated report artifacts, validation status, and PDF availability."""
    from ci_engine.chat.report_store import ReportArtifactStore

    store = ReportArtifactStore(report_root or str(config_get("chat.report_root", "reports")))
    reports = [summary.model_dump(mode="json") for summary in store.list_reports()]
    return _jsonable(
        {
            "reports": reports,
            "metadata": {
                "report_root": str(store.root),
                "report_count": len(reports),
            },
        }
    )


@mcp.tool()
def search_report_sections(
    query: str,
    competitors: list[str] | None = None,
    sections: list[str] | None = None,
    max_items: int = 8,
    report_root: str | None = None,
) -> dict[str, Any]:
    """Search generated report sections, scores, gaps, and validation findings."""
    from ci_engine.chat.report_store import ReportArtifactStore

    store = ReportArtifactStore(report_root or str(config_get("chat.report_root", "reports")))
    items = store.search_report_sections(
        _required_text(query, "query"),
        competitors=_clean_list(competitors),
        sections=_clean_list(sections),
        max_items=max(int(max_items or 8), 1),
    )
    return _jsonable(
        {
            "items": [item.model_dump(mode="json") for item in items],
            "missing": []
            if items
            else [
                {
                    "reason": "no_matching_report_sections",
                    "query": query,
                    "competitors": _clean_list(competitors),
                    "sections": _clean_list(sections),
                }
            ],
            "metadata": {
                "report_root": str(store.root),
                "result_count": len(items),
            },
        }
    )


@mcp.tool()
def search_answer_context(
    query: str,
    competitors: list[str] | None = None,
    dimensions: list[str] | None = None,
    axis: str | None = None,
    include_reports: bool = True,
    max_items: int = 8,
    report_root: str | None = None,
) -> dict[str, Any]:
    """Fast DB-backed chat retrieval with optional generated-report context."""
    query_text = _required_text(query, "query")
    item_limit = max(int(max_items or 8), 1)
    db_result = search(
        query_text,
        axis=_clean_text(axis),
        competitors=_clean_list(competitors),
        dimensions=_clean_list(dimensions),
    )
    db_items = [
        item
        for chunk in db_result.get("chunks", [])[:item_limit]
        if (item := _chat_evidence_from_chunk(chunk)) is not None
    ]
    keyword_items = [
        item
        for chunk in _chat_keyword_chunks(
            query_text,
            competitors=_clean_list(competitors),
            dimensions=_clean_list(dimensions),
            axis=_clean_text(axis),
            limit=item_limit,
        )
        if (item := _chat_evidence_from_chunk(chunk)) is not None
    ]
    report_items: list[dict[str, Any]] = []
    if include_reports:
        report_result = search_report_sections(
            query_text,
            competitors=_clean_list(competitors),
            sections=None,
            max_items=max(item_limit // 2, 2),
            report_root=report_root,
        )
        report_items = list(report_result.get("items", []))

    items = _dedupe_chat_items([*db_items, *keyword_items, *report_items])[:item_limit]
    missing = list(db_result.get("missing", []))
    if include_reports and not report_items:
        missing.append(
            {
                "reason": "no_matching_report_sections",
                "query": query_text,
                "competitors": _clean_list(competitors),
            }
        )
    return _jsonable(
        {
            "items": items,
            "missing": missing,
            "used_tools": ["search", "search_report_sections"]
            if include_reports
            else ["search"],
            "metadata": {
                "query": query_text,
                "db_chunk_count": len(db_result.get("chunks", [])),
                "keyword_chunk_count": len(keyword_items),
                "report_item_count": len(report_items),
                "result_count": len(items),
            },
        }
    )


def create_app() -> SharedTokenMiddleware:
    return SharedTokenMiddleware(mcp.streamable_http_app())


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=HOST,
        port=PORT,
        log_level="info",
    )


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _required_text(value: str, field: str) -> str:
    cleaned = _clean_text(value)
    if cleaned is None:
        raise ValueError(f"{field} is required")
    return cleaned


def _clean_list(values: Sequence[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [str(value).strip() for value in values if str(value or "").strip()]


def _clean_int_list(values: Sequence[int] | None) -> list[int]:
    if values is None:
        return []
    cleaned: list[int] = []
    for value in values:
        try:
            cleaned.append(int(value))
        except (TypeError, ValueError):
            continue
    return cleaned


def _row_in_filter(
    row: Mapping[str, Any],
    key: str,
    values: Sequence[str] | None,
) -> bool:
    if values is None:
        return True
    return str(row.get(key)) in {str(value) for value in values}


def _missing_coverage(
    *,
    competitors: Sequence[str] | None = None,
    axis: str | None = None,
    dimensions: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    active_coverage = _active_coverage_index()
    for row in repository.dimension_coverage_status(
        competitors=competitors,
        axis=axis,
        dimensions=dimensions,
    ):
        key = (
            str(row.get("competitor")),
            str(row.get("axis")),
            str(row.get("dimension")),
        )
        state = str(row.get("state") or "unknown")
        active_sources = active_coverage.get(key, 0)
        if state == "present":
            continue
        reason = missing_reason_for_state(state)
        missing.append(
            {
                "competitor": row.get("competitor"),
                "axis": row.get("axis"),
                "dimension": row.get("dimension"),
                "active_sources": active_sources,
                "reason": reason,
                "coverage_state": state,
                "coverage_confidence": row.get("confidence"),
                "coverage_conflict": row.get("conflict"),
            }
        )
    return missing


def _active_coverage_index() -> dict[tuple[str, str, str], int]:
    index: dict[tuple[str, str, str], int] = {}
    for row in repository.coverage_status():
        key = (
            str(row.get("competitor")),
            str(row.get("axis")),
            str(row.get("dimension")),
        )
        index[key] = int(row.get("active_sources") or 0)
    return index


def _group_chunks_by_dimension(chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for chunk in sorted(chunks, key=_chunk_sort_key, reverse=True):
        dimension = chunk.get("dimension")
        key = str(dimension) if dimension is not None else ""
        group = groups.setdefault(
            key,
            {
                "dimension": dimension,
                "chunks": [],
            },
        )
        group["chunks"].append(_format_chunk(chunk))
    return list(groups.values())


def _chunk_sort_key(chunk: Mapping[str, Any]) -> tuple[str, int]:
    chunk_id = int(chunk.get("chunk_id") or 0)
    return (_iso_text(chunk.get("publish_date")), chunk_id)


def _dedupe_chunks(chunks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    deduped: list[Mapping[str, Any]] = []
    for chunk in chunks:
        key = (
            chunk.get("chunk_id"),
            chunk.get("source_id"),
            chunk.get("chunk_text"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def _dedupe_missing(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any, Any]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (
            row.get("competitor"),
            row.get("axis"),
            row.get("dimension"),
            row.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(row))
    return deduped


def _chunks_by_source_id(
    chunks: Sequence[Mapping[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for chunk in chunks:
        source_id = int(chunk["source_id"])
        grouped.setdefault(source_id, []).append(_format_chunk(chunk))
    return grouped


def _format_source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _jsonable(source.get(key))
        for key in (
            "source_id",
            "competitor",
            "axis",
            "doc_type",
            "dimension",
            "url",
            "title",
            "publish_date",
            "fetched_at",
            "source_kind",
            "raw_path",
            "citations",
        )
        if key in source
    }


def _source_from_chunk(chunk: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": chunk.get("source_id"),
        "competitor": chunk.get("competitor"),
        "axis": chunk.get("axis"),
        "doc_type": chunk.get("doc_type"),
        "dimension": chunk.get("dimension"),
        "url": chunk.get("url"),
        "title": chunk.get("title"),
        "publish_date": chunk.get("publish_date"),
        "fetched_at": chunk.get("fetched_at"),
        "source_kind": chunk.get("source_kind"),
        "raw_path": chunk.get("raw_path"),
        "citations": chunk.get("citations", []),
        "chunks": [],
    }


def _format_chunk(chunk: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _jsonable(chunk.get(key))
        for key in (
            "chunk_id",
            "source_id",
            "chunk_text",
            "url",
            "title",
            "publish_date",
            "fetched_at",
            "axis",
            "dimension",
            "doc_type",
            "competitor",
            "source_kind",
            "raw_path",
            "similarity",
            "citations",
        )
        if key in chunk
    }


def _chat_evidence_from_chunk(chunk: Mapping[str, Any]) -> dict[str, Any] | None:
    text = _clean_quote(chunk.get("chunk_text"), limit=1000)
    if not text:
        return None
    source_id = _optional_int(chunk.get("source_id"))
    chunk_id = _optional_int(chunk.get("chunk_id"))
    url = _clean_text(chunk.get("url"))
    return {
        "id": _stable_id("chat-db", source_id, chunk_id, text),
        "source": "db",
        "text": text,
        "company": _clean_text(chunk.get("competitor")),
        "title": _clean_text(chunk.get("title")),
        "url": url,
        "publisher": _publisher(url) if url else None,
        "section": None,
        "dimension": _clean_text(chunk.get("dimension")),
        "source_id": source_id,
        "chunk_id": chunk_id,
        "published": _jsonable(chunk.get("publish_date")),
        "confidence": _confidence_from_similarity(chunk.get("similarity")),
        "metadata": {
            "axis": _clean_text(chunk.get("axis")),
            "doc_type": _clean_text(chunk.get("doc_type")),
            "source_kind": _clean_text(chunk.get("source_kind")),
            "retrieval_mode": "chat_answer_context",
        },
    }


def _chat_keyword_chunks(
    query: str,
    *,
    competitors: Sequence[str] | None,
    dimensions: Sequence[str] | None,
    axis: str | None,
    limit: int,
) -> list[Mapping[str, Any]]:
    competitor_filter = _clean_list(competitors)
    dimension_filter = _clean_list(dimensions)
    if not competitor_filter:
        return []
    try:
        chunks = repository.active_chunks(
            competitors=competitor_filter,
            axis=axis,
            dimensions=dimension_filter,
        )
    except Exception:
        return []
    terms = _chat_search_terms(query)
    ranked = [
        (score, chunk)
        for chunk in chunks
        if (score := _chat_keyword_score(chunk, terms)) > 0
    ]
    ranked.sort(
        key=lambda pair: (
            pair[0],
            _source_quality_score(pair[1]),
            _iso_text(pair[1].get("publish_date")),
            int(pair[1].get("chunk_id") or 0),
        ),
        reverse=True,
    )
    return [chunk for _, chunk in ranked[: max(int(limit or 8), 1)]]


def _chat_search_terms(query: str) -> tuple[str, ...]:
    stop_words = {
        "about",
        "against",
        "compare",
        "compared",
        "does",
        "have",
        "main",
        "what",
        "where",
        "which",
        "with",
    }
    normalized = "".join(
        char.lower() if char.isalnum() else " " for char in str(query or "")
    )
    terms = [
        token
        for token in normalized.split()
        if len(token) >= 3 and token not in stop_words
    ]
    return tuple(dict.fromkeys(terms))


def _chat_keyword_score(
    chunk: Mapping[str, Any],
    terms: Sequence[str],
) -> int:
    text = " ".join(
        str(chunk.get(key) or "")
        for key in (
            "chunk_text",
            "title",
            "url",
            "dimension",
            "doc_type",
            "source_kind",
            "competitor",
        )
    ).lower()
    score = sum(2 for term in terms if term in text)
    for phrase in (
        "artifact",
        "artifactory",
        "xray",
        "curation",
        "nexus",
        "firewall",
        "lifecycle",
        "sbom",
        "sca",
        "reachability",
        "malicious",
        "malware",
        "cve",
        "policy",
        "license",
    ):
        if phrase in terms and phrase in text:
            score += 3
    return score


def _dedupe_chat_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        text = str(item.get("text") or "")
        key = (item_id, text[:240])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(item))
    return deduped


def _section_chunk_candidates(
    chunks: Sequence[Mapping[str, Any]],
    *,
    company: str,
    spec: ReportSectionSpec,
) -> list[Mapping[str, Any]]:
    dimension_set = set(spec.dimensions)
    candidates: list[Mapping[str, Any]] = []
    for chunk in chunks:
        if (_clean_text(chunk.get("competitor")) or "").lower() != company.lower():
            continue
        dimension = _clean_text(chunk.get("dimension"))
        if dimension not in dimension_set:
            continue
        axis = _clean_text(chunk.get("axis"))
        if spec.axis is not None and axis != spec.axis:
            continue
        candidates.append(chunk)
    return candidates


def _rank_section_chunks(
    chunks: Sequence[Mapping[str, Any]],
    *,
    spec: ReportSectionSpec,
    company: str,
    competitor: str,
    focus: str | None,
) -> list[Mapping[str, Any]]:
    terms = _section_terms(spec, company=company, competitor=competitor, focus=focus)
    return sorted(
        chunks,
        key=lambda chunk: (
            _section_term_hits(chunk, terms),
            _source_quality_score(chunk),
            _iso_text(chunk.get("publish_date")),
            int(chunk.get("chunk_id") or 0),
        ),
        reverse=True,
    )


def _section_terms(
    spec: ReportSectionSpec,
    *,
    company: str,
    competitor: str,
    focus: str | None,
) -> tuple[str, ...]:
    raw_terms: list[str] = [spec.title, *spec.dimensions]
    raw_terms.extend(
        query.format(company=company, competitor=competitor)
        for query in spec.queries
    )
    if focus:
        raw_terms.append(focus)
    terms: list[str] = []
    for raw in raw_terms:
        normalized = str(raw).replace("_", " ").lower()
        terms.append(normalized)
        terms.extend(
            token
            for token in normalized.split()
            if len(token) >= 4 and token not in {"with", "from", "that", "this"}
        )
    return tuple(dict.fromkeys(term for term in terms if term))


def _section_term_hits(chunk: Mapping[str, Any], terms: Sequence[str]) -> int:
    text = " ".join(
        str(chunk.get(key) or "")
        for key in ("chunk_text", "title", "url", "doc_type", "source_kind", "dimension")
    ).lower()
    return sum(1 for term in terms if term in text)


def _section_evidence_from_chunk(
    chunk: Mapping[str, Any],
    *,
    spec: ReportSectionSpec,
    company: str,
    index: int,
) -> EvidenceItem | None:
    url = _clean_text(chunk.get("url"))
    quote = _clean_quote(chunk.get("chunk_text"))
    chunk_company = _clean_text(chunk.get("competitor")) or company
    if not url or not quote:
        return None
    source_id = _optional_int(chunk.get("source_id"))
    chunk_id = _optional_int(chunk.get("chunk_id"))
    source_quality_score = _source_quality_score(chunk)
    return EvidenceItem(
        id=_stable_id(
            "section-db",
            spec.id,
            company,
            url,
            source_id,
            chunk_id,
            index,
        ),
        source="db",
        tier="primary" if source_quality_score >= 75.0 else "supporting",
        company=chunk_company,
        report_section=spec.id,
        url=url,
        title=_clean_text(chunk.get("title")),
        publisher=_publisher(url),
        retrieved_at=_parse_datetime(chunk.get("fetched_at")),
        published=_parse_date(chunk.get("publish_date")),
        quote=quote,
        summary=_summary_from_text(quote),
        axis=_clean_text(chunk.get("axis")),
        dimension=_clean_text(chunk.get("dimension")),
        confidence=_confidence_from_similarity(chunk.get("similarity")),
        source_id=source_id,
        chunk_id=chunk_id,
        metadata={
            "doc_type": _clean_text(chunk.get("doc_type")),
            "source_kind": _clean_text(chunk.get("source_kind")),
            "source_quality_score": source_quality_score,
            "raw_path": _clean_text(chunk.get("raw_path")),
            "citations": chunk.get("citations") or [],
            "section_title": spec.title,
            "retrieval_mode": "mcp_batch_section",
        },
    )


def _dimension_counts(chunks: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        dimension = _clean_text(chunk.get("dimension"))
        if not dimension:
            continue
        counts[dimension] = counts.get(dimension, 0) + 1
    return counts


def _capability_chunk_index(
    chunks: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    index: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for chunk in chunks:
        company = _clean_text(chunk.get("competitor"))
        dimension = _clean_text(chunk.get("dimension"))
        if not company or not dimension:
            continue
        index.setdefault((company.lower(), dimension), []).append(chunk)
    return index


def _rank_capability_chunks(
    chunks: Sequence[Mapping[str, Any]],
    capability: Any,
) -> list[Mapping[str, Any]]:
    terms = tuple(str(term).lower() for term in getattr(capability, "search_terms", ()))
    return sorted(
        chunks,
        key=lambda chunk: (
            _capability_term_hits(chunk, terms),
            _source_quality_score(chunk),
            _iso_text(chunk.get("publish_date")),
            int(chunk.get("chunk_id") or 0),
        ),
        reverse=True,
    )


def _capability_term_hits(chunk: Mapping[str, Any], terms: Sequence[str]) -> int:
    text = " ".join(
        str(chunk.get(key) or "")
        for key in ("chunk_text", "title", "url", "doc_type", "source_kind")
    ).lower()
    return sum(1 for term in terms if term and term in text)


def _capability_evidence_from_chunk(
    chunk: Mapping[str, Any],
    *,
    company: str,
    capability: Any,
    query: str,
    index: int,
) -> EvidenceItem | None:
    url = _clean_text(chunk.get("url"))
    quote = _clean_quote(chunk.get("chunk_text"))
    chunk_company = _clean_text(chunk.get("competitor")) or company
    if not url or not quote:
        return None
    source_id = _optional_int(chunk.get("source_id"))
    chunk_id = _optional_int(chunk.get("chunk_id"))
    source_quality_score = _source_quality_score(chunk)
    return EvidenceItem(
        id=_stable_id(
            "capability-db",
            company,
            capability.id,
            url,
            source_id,
            chunk_id,
            index,
        ),
        source="db",
        tier="primary" if source_quality_score >= 75.0 else "supporting",
        company=chunk_company,
        report_section="product_feature_analysis",
        url=url,
        title=_clean_text(chunk.get("title")),
        publisher=_publisher(url),
        retrieved_at=_parse_datetime(chunk.get("fetched_at")),
        published=_parse_date(chunk.get("publish_date")),
        quote=quote,
        summary=_summary_from_text(quote),
        axis=_clean_text(chunk.get("axis")) or "technical",
        dimension=_clean_text(chunk.get("dimension")) or capability.dimension,
        confidence=_confidence_from_similarity(chunk.get("similarity")),
        source_id=source_id,
        chunk_id=chunk_id,
        metadata={
            "doc_type": _clean_text(chunk.get("doc_type")),
            "source_kind": _clean_text(chunk.get("source_kind")),
            "source_quality_score": source_quality_score,
            "raw_path": _clean_text(chunk.get("raw_path")),
            "citations": chunk.get("citations") or [],
            "capability_id": capability.id,
            "capability_label": capability.label,
            "capability_dimension": capability.dimension,
            "targeted_search_query": query,
            "retrieval_mode": "mcp_batch_capability",
        },
    )


def _dedupe_report_gaps(gaps: Sequence[EvidenceGap]) -> list[EvidenceGap]:
    seen: set[tuple[str, str, str | None, str | None, str]] = set()
    deduped: list[EvidenceGap] = []
    for gap in gaps:
        key = (gap.company, gap.report_section, gap.axis, gap.dimension, gap.reason)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(gap)
    return deduped


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _publisher(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.") or None


def _clean_quote(value: Any, *, limit: int = 900) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    compact = " ".join(text.split())
    return compact[:limit].rstrip()


def _summary_from_text(text: str, *, limit: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0].rstrip() + "..."


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = _clean_text(value)
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _confidence_from_similarity(value: Any) -> str:
    try:
        similarity = float(value)
    except (TypeError, ValueError):
        return "medium"
    if similarity >= 0.75:
        return "high"
    if similarity >= 0.6:
        return "medium"
    return "low"


def _source_quality_score(chunk: Mapping[str, Any]) -> float:
    source_kind = (_clean_text(chunk.get("source_kind")) or "unknown").lower()
    scores = {
        "official": 90.0,
        "docs": 88.0,
        "vendor_docs": 88.0,
        "pricing": 86.0,
        "security_advisories": 85.0,
        "customers": 82.0,
        "vendor_site": 78.0,
        "official_llm_research_report": 62.0,
        "blog": 58.0,
        "news": 52.0,
        "unknown": 38.0,
    }
    return scores.get(source_kind, 42.0)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _iso_text(value: Any) -> str:
    converted = _jsonable(value)
    if converted is None:
        return ""
    return str(converted)


__all__ = [
    "SharedTokenMiddleware",
    "compare_competitors",
    "compare_dimension",
    "coverage_matrix",
    "coverage_status",
    "create_app",
    "find_evidence_gaps",
    "get_competitor",
    "get_source_detail",
    "latest_updates",
    "main",
    "mcp",
    "search",
    "build_report_section_evidence",
    "build_capability_evidence_matrix",
    "build_report_evidence_pack",
    "source_inventory",
]


if __name__ == "__main__":
    main()
