from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

from ci_engine.crews.report.schemas import (
    EvidenceGap,
    EvidenceItem,
    EvidencePack,
    SourceInventoryItem,
    TargetedSearchAttempt,
    WebClassification,
)
from ci_engine.crews.report.capabilities import (
    build_capability_artifacts,
    capability_query_plan,
)
from ci_engine.crews.report.inventory import (
    build_source_inventory,
    inventory_by_source_id,
)
from ci_engine.crews.report.readiness import analyze_evidence_readiness
from ci_engine.crews.report.sections import ReportSectionSpec, section_specs


TavilySearchFn = Callable[..., list[dict[str, Any]]]


class ReportMcpClient(Protocol):
    def search(
        self,
        query: str,
        axis: str | None = None,
        competitors: list[str] | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        ...

    def coverage_status(self) -> dict[str, Any]:
        ...

    def source_inventory(
        self,
        competitors: list[str] | None = None,
        dimensions: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        ...

    def build_capability_evidence_matrix(
        self,
        competitor: str,
        focus: str | None = None,
        max_chunks_per_company_capability: int = 4,
    ) -> dict[str, Any]:
        ...

    def build_report_section_evidence(
        self,
        competitor: str,
        focus: str | None = None,
        sections: list[str] | None = None,
        max_chunks_per_company_section: int = 8,
    ) -> dict[str, Any]:
        ...


class LocalMcpReportClient:
    """In-process adapter over the same read-only functions exposed as MCP tools."""

    def search(
        self,
        query: str,
        axis: str | None = None,
        competitors: list[str] | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        from ci_engine.mcp import server  # noqa: PLC0415

        return server.search(
            query=query,
            axis=axis,
            competitors=competitors,
            dimensions=dimensions,
        )

    def coverage_status(self) -> dict[str, Any]:
        from ci_engine.mcp import server  # noqa: PLC0415

        return server.coverage_status()

    def source_inventory(
        self,
        competitors: list[str] | None = None,
        dimensions: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        from ci_engine.mcp import server  # noqa: PLC0415

        return server.source_inventory(
            competitors=competitors,
            dimensions=dimensions,
            limit=limit,
        )

    def build_capability_evidence_matrix(
        self,
        competitor: str,
        focus: str | None = None,
        max_chunks_per_company_capability: int = 4,
    ) -> dict[str, Any]:
        from ci_engine.mcp import server  # noqa: PLC0415

        return server.build_capability_evidence_matrix(
            competitor=competitor,
            focus=focus,
            max_chunks_per_company_capability=max_chunks_per_company_capability,
        )

    def build_report_section_evidence(
        self,
        competitor: str,
        focus: str | None = None,
        sections: list[str] | None = None,
        max_chunks_per_company_section: int = 8,
    ) -> dict[str, Any]:
        from ci_engine.mcp import server  # noqa: PLC0415

        return server.build_report_section_evidence(
            competitor=competitor,
            focus=focus,
            sections=sections,
            max_chunks_per_company_section=max_chunks_per_company_section,
        )


def build_evidence_pack_for_competitor(
    competitor: str,
    *,
    focus: str | None = None,
    sections: list[str] | None = None,
    mcp_client: ReportMcpClient | None = None,
    include_web: bool = True,
    web_search: TavilySearchFn | None = None,
    max_web_results: int = 3,
) -> EvidencePack:
    specs = section_specs(sections)
    client = mcp_client or LocalMcpReportClient()
    inventory = build_source_inventory(
        competitor,
        specs=specs,
        client=client,
    )
    section_batch = collect_db_evidence_batch(
        competitor,
        focus=focus,
        specs=specs,
        mcp_client=client,
    )
    section_retrieval_mode = "batch" if section_batch is not None else "search_loop"
    section_batch_coverage: list[dict[str, Any]] = []
    if section_batch is None:
        db_items, db_gaps = collect_db_evidence(
            competitor,
            focus=focus,
            specs=specs,
            mcp_client=client,
            inventory_sources=inventory_by_source_id(inventory),
        )
    else:
        db_items, db_gaps, section_batch_coverage = section_batch
    capability_batch = collect_capability_db_evidence_batch(
        competitor,
        focus=focus,
        mcp_client=client,
    )
    capability_retrieval_mode = "batch" if capability_batch is not None else "search_loop"
    if capability_batch is None:
        capability_db_items, capability_db_gaps, capability_attempts = collect_capability_db_evidence(
            competitor,
            focus=focus,
            mcp_client=client,
            inventory_sources=inventory_by_source_id(inventory),
        )
    else:
        capability_db_items, capability_db_gaps, capability_attempts = capability_batch
    db_items = dedupe_evidence([*db_items, *capability_db_items])
    db_gaps = [*db_gaps, *capability_db_gaps]
    web_items: list[EvidenceItem] = []
    web_gaps: list[EvidenceGap] = []
    if include_web:
        try:
            broad_web_items = collect_tavily_evidence(
                competitor,
                focus=focus,
                specs=specs,
                db_items=db_items,
                search_fn=web_search,
                max_results=max_web_results,
            )
            capability_web_items, web_attempts = collect_capability_tavily_evidence(
                competitor,
                focus=focus,
                db_items=db_items,
                search_fn=web_search,
                max_results=max_web_results,
            )
            web_items = dedupe_evidence([*broad_web_items, *capability_web_items])
            capability_attempts.extend(web_attempts)
        except Exception as exc:  # pragma: no cover - exercised by caller-specific fakes.
            web_gaps.append(
                EvidenceGap(
                    company=competitor,
                    report_section="web_validation",
                    reason="tavily_error",
                    detail=str(exc),
                )
            )

    items = dedupe_evidence([*db_items, *web_items])
    capability_matrix, product_catalog, capability_gaps = build_capability_artifacts(
        competitor,
        items=items,
        attempts=capability_attempts,
    )
    gaps = [
        *db_gaps,
        *web_gaps,
        *capability_gaps,
        *_section_gaps(competitor, specs, items),
    ]
    pack_id = _stable_id(
        "evidence-pack",
        competitor,
        focus or "",
        ",".join(item.id for item in items),
    )
    pack = EvidencePack(
        id=pack_id,
        competitor=competitor,
        focus=focus,
        items=tuple(items),
        gaps=tuple(_dedupe_gaps(gaps)),
        quality_notes=tuple(_quality_notes(specs, items, gaps)),
        inventory=inventory,
        product_catalog=product_catalog,
        capability_matrix=capability_matrix,
        metadata={
            "sections": [spec.id for spec in specs],
            "db_evidence_count": len(db_items),
            "tavily_evidence_count": len(web_items),
            "web_enabled": include_web,
            "inventory_source_count": len(inventory.sources),
            "capability_search_attempt_count": len(capability_attempts),
            "capability_matrix_row_count": len(capability_matrix.rows),
            "product_catalog_count": len(product_catalog),
            "section_retrieval_mode": section_retrieval_mode,
            "section_batch_coverage": section_batch_coverage,
            "capability_retrieval_mode": capability_retrieval_mode,
        },
    )
    readiness = analyze_evidence_readiness(pack, specs)
    return pack.model_copy(
        update={
            "readiness": readiness,
            "quality_notes": tuple([*pack.quality_notes, *readiness.notes]),
        }
    )


def collect_db_evidence(
    competitor: str,
    *,
    focus: str | None,
    specs: Sequence[ReportSectionSpec],
    mcp_client: ReportMcpClient,
    inventory_sources: Mapping[int, SourceInventoryItem] | None = None,
) -> tuple[list[EvidenceItem], list[EvidenceGap]]:
    items: list[EvidenceItem] = []
    gaps: list[EvidenceGap] = []
    companies = ["JFrog", competitor]
    for spec in specs:
        for query in _queries_for_spec(spec, competitor=competitor, focus=focus):
            result = mcp_client.search(
                query=query,
                axis=spec.axis,
                competitors=companies,
                dimensions=list(spec.dimensions),
            )
            for index, chunk in enumerate(result.get("chunks", [])):
                item = _db_evidence_from_chunk(
                    chunk,
                    spec.id,
                    index,
                    inventory_sources=inventory_sources or {},
                )
                if item is not None:
                    items.append(item)
            gaps.extend(_gaps_from_missing(result.get("missing", []), spec.id))

    return dedupe_evidence(items), _dedupe_gaps(gaps)


def collect_db_evidence_batch(
    competitor: str,
    *,
    focus: str | None,
    specs: Sequence[ReportSectionSpec],
    mcp_client: ReportMcpClient,
) -> tuple[list[EvidenceItem], list[EvidenceGap], list[dict[str, Any]]] | None:
    batch_fn = getattr(mcp_client, "build_report_section_evidence", None)
    if batch_fn is None:
        return None
    try:
        result = batch_fn(
            competitor=competitor,
            focus=focus,
            sections=[spec.id for spec in specs],
        )
    except Exception:
        return None
    items = [
        EvidenceItem.model_validate(item)
        for item in result.get("items", [])
    ]
    gaps = [
        EvidenceGap.model_validate(gap)
        for gap in result.get("gaps", [])
    ]
    coverage = [
        dict(row)
        for row in result.get("coverage", [])
        if isinstance(row, Mapping)
    ]
    if not items and not gaps and not coverage:
        return None
    return dedupe_evidence(items), _dedupe_gaps(gaps), coverage


def collect_capability_db_evidence_batch(
    competitor: str,
    *,
    focus: str | None,
    mcp_client: ReportMcpClient,
) -> tuple[list[EvidenceItem], list[EvidenceGap], list[TargetedSearchAttempt]] | None:
    batch_fn = getattr(mcp_client, "build_capability_evidence_matrix", None)
    if batch_fn is None:
        return None
    try:
        result = batch_fn(competitor=competitor, focus=focus)
    except Exception:
        return None
    items = [
        EvidenceItem.model_validate(item)
        for item in result.get("items", [])
    ]
    gaps = [
        EvidenceGap.model_validate(gap)
        for gap in result.get("gaps", [])
    ]
    attempts = [
        TargetedSearchAttempt.model_validate(attempt)
        for attempt in result.get("attempts", [])
    ]
    if not attempts:
        return None
    return dedupe_evidence(items), _dedupe_gaps(gaps), attempts


def collect_capability_db_evidence(
    competitor: str,
    *,
    focus: str | None,
    mcp_client: ReportMcpClient,
    inventory_sources: Mapping[int, SourceInventoryItem] | None = None,
) -> tuple[list[EvidenceItem], list[EvidenceGap], list[TargetedSearchAttempt]]:
    items: list[EvidenceItem] = []
    gaps: list[EvidenceGap] = []
    attempts: list[TargetedSearchAttempt] = []
    for company in ("JFrog", competitor):
        for capability, query in capability_query_plan(
            company,
            competitor=competitor,
            focus=focus,
        ):
            result = mcp_client.search(
                query=query,
                axis="technical",
                competitors=[company],
                dimensions=[capability.dimension],
            )
            capability_items: list[EvidenceItem] = []
            for index, chunk in enumerate(result.get("chunks", [])):
                item = _db_evidence_from_chunk(
                    chunk,
                    "product_feature_analysis",
                    index,
                    inventory_sources=inventory_sources or {},
                )
                if item is None or item.company.lower() != company.lower():
                    continue
                capability_items.append(_with_capability_metadata(item, capability, query))
            items.extend(capability_items)
            gaps.extend(_gaps_from_missing(result.get("missing", []), "product_feature_analysis"))
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

    return dedupe_evidence(items), _dedupe_gaps(gaps), attempts


def collect_tavily_evidence(
    competitor: str,
    *,
    focus: str | None,
    specs: Sequence[ReportSectionSpec],
    db_items: Sequence[EvidenceItem],
    search_fn: TavilySearchFn | None,
    max_results: int,
) -> list[EvidenceItem]:
    if search_fn is None:
        from ci_engine.acquire import tavily_lane  # noqa: PLC0415

        search_fn = tavily_lane.search

    items: list[EvidenceItem] = []
    for spec in specs:
        for company in ("JFrog", competitor):
            topics = tavily_topics(
                company,
                competitor=competitor,
                specs=(spec,),
                focus=focus,
            )
            candidates = search_fn(company, topics=topics, max_results=max_results)
            for index, candidate in enumerate(candidates):
                item = _tavily_evidence_from_candidate(
                    candidate,
                    company=company,
                    competitor=competitor,
                    specs=specs,
                    db_items=db_items,
                    index=index,
                    section_id=spec.id,
                )
                if item is not None:
                    items.append(item)
    return dedupe_evidence(items)


def collect_capability_tavily_evidence(
    competitor: str,
    *,
    focus: str | None,
    db_items: Sequence[EvidenceItem],
    search_fn: TavilySearchFn | None,
    max_results: int,
) -> tuple[list[EvidenceItem], list[TargetedSearchAttempt]]:
    if search_fn is None:
        from ci_engine.acquire import tavily_lane  # noqa: PLC0415

        search_fn = tavily_lane.search

    items: list[EvidenceItem] = []
    attempts: list[TargetedSearchAttempt] = []
    for company in ("JFrog", competitor):
        for capability, query in capability_query_plan(
            company,
            competitor=competitor,
            focus=focus,
        ):
            candidates = search_fn(company, topics=[query], max_results=max_results)
            capability_items: list[EvidenceItem] = []
            for index, candidate in enumerate(candidates):
                item = _tavily_evidence_from_candidate(
                    candidate,
                    company=company,
                    competitor=competitor,
                    specs=(),
                    db_items=db_items,
                    index=index,
                    section_id="product_feature_analysis",
                )
                if item is None or item.company.lower() != company.lower():
                    continue
                capability_items.append(_with_capability_metadata(item, capability, query))
            items.extend(capability_items)
            attempts.append(
                TargetedSearchAttempt(
                    company=company,
                    capability_id=capability.id,
                    capability_label=capability.label,
                    source="tavily",
                    query=query,
                    result_count=len(capability_items),
                    status="supported" if capability_items else "not_found_after_search",
                )
            )

    return dedupe_evidence(items), attempts


def tavily_topics(
    company: str,
    *,
    competitor: str,
    specs: Sequence[ReportSectionSpec],
    focus: str | None = None,
) -> list[str]:
    topics: list[str] = []
    for spec in specs:
        for query in spec.queries:
            topics.append(query.format(company=company, competitor=competitor))
    if focus:
        topics.append(f"{company} {focus} JFrog {competitor}")
    return topics


def dedupe_evidence(items: Sequence[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[EvidenceItem] = []
    for item in items:
        key = (
            item.source,
            item.company.lower(),
            item.report_section,
            item.url,
            str(item.metadata.get("capability_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _queries_for_spec(
    spec: ReportSectionSpec,
    *,
    competitor: str,
    focus: str | None,
) -> list[str]:
    queries = [
        query.format(company="{company}", competitor=competitor)
        for query in spec.queries
    ]
    if focus:
        queries.append(f"{{company}} {focus} {competitor} evidence")
    expanded: list[str] = []
    for company in ("JFrog", competitor):
        expanded.extend(query.format(company=company) for query in queries)
    return expanded


def _db_evidence_from_chunk(
    chunk: Mapping[str, Any],
    report_section: str,
    index: int,
    *,
    inventory_sources: Mapping[int, SourceInventoryItem],
) -> EvidenceItem | None:
    url = _clean_text(chunk.get("url"))
    quote = _clean_quote(chunk.get("chunk_text"))
    company = _clean_text(chunk.get("competitor"))
    if not url or not quote or not company:
        return None
    source_id = _optional_int(chunk.get("source_id"))
    chunk_id = _optional_int(chunk.get("chunk_id"))
    inventory_item = inventory_sources.get(source_id or -1)
    source_quality_score = (
        inventory_item.quality_score
        if inventory_item is not None
        else _fallback_source_quality(chunk)
    )
    source_kind = (
        inventory_item.source_kind
        if inventory_item is not None
        else _clean_text(chunk.get("source_kind"))
    )
    return EvidenceItem(
        id=_stable_id("db", report_section, company, url, source_id, chunk_id, index),
        source="db",
        tier="primary" if source_quality_score >= 75.0 else "supporting",
        company=company,
        report_section=report_section,
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
            "source_kind": source_kind,
            "source_quality_score": source_quality_score,
            "raw_path": _clean_text(chunk.get("raw_path")),
            "citations": chunk.get("citations") or [],
        },
    )


def _tavily_evidence_from_candidate(
    candidate: Mapping[str, Any],
    *,
    company: str,
    competitor: str,
    specs: Sequence[ReportSectionSpec],
    db_items: Sequence[EvidenceItem],
    index: int,
    section_id: str | None = None,
) -> EvidenceItem | None:
    url = _clean_text(candidate.get("url"))
    quote = _clean_quote(
        candidate.get("text")
        or candidate.get("snippet")
        or candidate.get("content")
        or candidate.get("raw_content")
    )
    if not url or not quote:
        return None
    resolved_section_id = section_id or _infer_section(candidate, specs)
    return EvidenceItem(
        id=_stable_id("tavily", resolved_section_id, company, url, index),
        source="tavily",
        tier="validation",
        company=_clean_text(candidate.get("competitor")) or company,
        report_section=resolved_section_id,
        url=url,
        title=_clean_text(candidate.get("title")),
        publisher=_publisher(url),
        retrieved_at=datetime.now(timezone.utc),
        published=_parse_date(candidate.get("published")),
        quote=quote,
        summary=_summary_from_text(quote),
        confidence="medium",
        classification=_classify_web_item(
            url=url,
            company=company,
            section_id=resolved_section_id,
            db_items=db_items,
        ),
        metadata={
            "source_kind": _clean_text(candidate.get("source_kind")),
            "source_reason": _clean_text(candidate.get("source_reason")),
            "competitor_under_test": competitor,
        },
    )


def _with_capability_metadata(
    item: EvidenceItem,
    capability: Any,
    query: str,
) -> EvidenceItem:
    metadata = {
        **item.metadata,
        "capability_id": capability.id,
        "capability_label": capability.label,
        "capability_dimension": capability.dimension,
        "targeted_search_query": query,
    }
    return item.model_copy(
        update={
            "id": _stable_id(item.id, capability.id),
            "dimension": item.dimension or capability.dimension,
            "metadata": metadata,
        }
    )


def _classify_web_item(
    *,
    url: str,
    company: str,
    section_id: str,
    db_items: Sequence[EvidenceItem],
) -> WebClassification:
    if any(item.url == url for item in db_items):
        return "confirms_db"
    if not any(
        item.company.lower() == company.lower() and item.report_section == section_id
        for item in db_items
    ):
        return "fills_gap"
    return "adds_context"


def _infer_section(candidate: Mapping[str, Any], specs: Sequence[ReportSectionSpec]) -> str:
    text = " ".join(
        str(candidate.get(key) or "")
        for key in ("title", "url", "snippet", "content", "text", "raw_content")
    ).lower()
    if any(token in text for token in ("pricing", "customer", "case study", "buyer")):
        return "buyer_fit"
    if any(token in text for token in ("sbom", "sca", "firewall", "vulnerability", "artifact", "architecture")):
        return "technical_teardown"
    if any(token in text for token in ("market", "gartner", "forrester", "position")):
        return "market_context"
    if any(token in text for token in ("win", "loss", "objection", "battlecard")):
        return "field_battlecard"
    return specs[0].id if specs else "executive_summary"


def _gaps_from_missing(missing_rows: Sequence[Mapping[str, Any]], section_id: str) -> list[EvidenceGap]:
    gaps: list[EvidenceGap] = []
    for row in missing_rows:
        company = _clean_text(row.get("competitor"))
        if not company:
            continue
        gaps.append(
            EvidenceGap(
                company=company,
                report_section=section_id,
                axis=_clean_text(row.get("axis")),
                dimension=_clean_text(row.get("dimension")),
                reason=_clean_text(row.get("reason")) or "unknown_coverage",
                detail=_clean_text(row.get("coverage_state")),
            )
        )
    return gaps


def _section_gaps(
    competitor: str,
    specs: Sequence[ReportSectionSpec],
    items: Sequence[EvidenceItem],
) -> list[EvidenceGap]:
    gaps: list[EvidenceGap] = []
    for spec in specs:
        for company in ("JFrog", competitor):
            if any(
                item.company.lower() == company.lower() and item.report_section == spec.id
                for item in items
            ):
                continue
            gaps.append(
                EvidenceGap(
                    company=company,
                    report_section=spec.id,
                    axis=spec.axis,
                    reason="no_recent_data_found",
                    detail=f"{company}/{spec.id}: no recent data found",
                )
            )
    return gaps


def _dedupe_gaps(gaps: Sequence[EvidenceGap]) -> list[EvidenceGap]:
    seen: set[tuple[str, str, str | None, str | None, str]] = set()
    deduped: list[EvidenceGap] = []
    for gap in gaps:
        key = (gap.company, gap.report_section, gap.axis, gap.dimension, gap.reason)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(gap)
    return deduped


def _quality_notes(
    specs: Sequence[ReportSectionSpec],
    items: Sequence[EvidenceItem],
    gaps: Sequence[EvidenceGap],
) -> list[str]:
    notes: list[str] = []
    if not items:
        notes.append("No evidence items collected; report rendering should be blocked.")
    tavily_count = sum(1 for item in items if item.source == "tavily")
    db_count = sum(1 for item in items if item.source == "db")
    notes.append(f"Evidence mix: {db_count} DB items and {tavily_count} Tavily items.")
    uncovered = {
        gap.report_section
        for gap in gaps
        if gap.reason == "no_recent_data_found"
    }
    required_uncovered = [spec.id for spec in specs if spec.required and spec.id in uncovered]
    if required_uncovered:
        notes.append("Sections with missing evidence: " + ", ".join(sorted(required_uncovered)))
    return notes


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _publisher(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.") or None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


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


def _fallback_source_quality(chunk: Mapping[str, Any]) -> float:
    source_kind = _clean_text(chunk.get("source_kind")) or "unknown"
    scores = {
        "docs": 88.0,
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


__all__ = [
    "LocalMcpReportClient",
    "ReportMcpClient",
    "build_evidence_pack_for_competitor",
    "collect_db_evidence_batch",
    "collect_capability_db_evidence_batch",
    "collect_db_evidence",
    "collect_capability_db_evidence",
    "collect_capability_tavily_evidence",
    "collect_tavily_evidence",
    "dedupe_evidence",
    "tavily_topics",
]
