from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

from ci_engine.crews.report.schemas import (
    SourceInventoryItem,
    SourceInventoryReport,
    SourceInventorySummary,
)
from ci_engine.crews.report.sections import ReportSectionSpec


class SourceInventoryClient(Protocol):
    def source_inventory(
        self,
        competitors: list[str] | None = None,
        dimensions: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        ...


def build_source_inventory(
    competitor: str,
    *,
    specs: Sequence[ReportSectionSpec],
    client: SourceInventoryClient,
) -> SourceInventoryReport:
    dimensions = sorted({dimension for spec in specs for dimension in spec.dimensions})
    raw = client.source_inventory(
        competitors=["JFrog", competitor],
        dimensions=dimensions,
        limit=None,
    )
    items = tuple(
        _inventory_item(row)
        for row in raw.get("sources", [])
    )
    return SourceInventoryReport(
        sources=items,
        summaries=tuple(_summaries(items)),
    )


def inventory_by_source_id(
    inventory: SourceInventoryReport | None,
) -> dict[int, SourceInventoryItem]:
    if inventory is None:
        return {}
    return {item.source_id: item for item in inventory.sources}


def _inventory_item(row: Mapping[str, Any]) -> SourceInventoryItem:
    source_kind = _clean_text(row.get("source_kind")) or "unknown"
    doc_type = _clean_text(row.get("doc_type")) or "unknown"
    url = _clean_text(row.get("url")) or ""
    return SourceInventoryItem(
        source_id=int(row.get("source_id") or row.get("id") or 0),
        company=_clean_text(row.get("competitor")) or _clean_text(row.get("company")) or "",
        axis=_clean_text(row.get("axis")) or "both",
        dimension=_clean_text(row.get("dimension")),
        doc_type=doc_type,
        source_kind=source_kind,
        url=url,
        title=_clean_text(row.get("title")),
        published=_parse_date(row.get("publish_date") or row.get("published")),
        fetched_at=_parse_datetime(row.get("fetched_at")),
        raw_path=_clean_text(row.get("raw_path")),
        chunk_count=int(row.get("chunk_count") or 0),
        citation_count=int(row.get("citation_count") or 0),
        quality_score=_source_quality_score(
            source_kind=source_kind,
            doc_type=doc_type,
            url=url,
            chunk_count=int(row.get("chunk_count") or 0),
            citation_count=int(row.get("citation_count") or 0),
        ),
    )


def _summaries(items: Sequence[SourceInventoryItem]) -> list[SourceInventorySummary]:
    companies = sorted({item.company for item in items})
    summaries: list[SourceInventorySummary] = []
    for company in companies:
        company_items = [item for item in items if item.company == company]
        newest = max(
            (item.published for item in company_items if item.published is not None),
            default=None,
        )
        summaries.append(
            SourceInventorySummary(
                company=company,
                total_sources=len(company_items),
                active_dimensions=len({item.dimension for item in company_items if item.dimension}),
                primary_quality_sources=sum(1 for item in company_items if item.quality_score >= 75.0),
                official_or_vendor_sources=sum(
                    1
                    for item in company_items
                    if item.source_kind in {
                        "docs",
                        "pricing",
                        "security_advisories",
                        "vendor_site",
                        "customers",
                    }
                ),
                generated_report_sources=sum(
                    1
                    for item in company_items
                    if item.source_kind == "official_llm_research_report"
                ),
                newest_published=newest,
            )
        )
    return summaries


def _source_quality_score(
    *,
    source_kind: str,
    doc_type: str,
    url: str,
    chunk_count: int,
    citation_count: int,
) -> float:
    source_kind_scores = {
        "docs": 88.0,
        "pricing": 86.0,
        "security_advisories": 85.0,
        "customers": 82.0,
        "vendor_site": 78.0,
        "analyst": 72.0,
        "release_notes": 78.0,
        "official_llm_research_report": 62.0,
        "blog": 58.0,
        "news": 52.0,
        "unknown": 38.0,
    }
    doc_type_bonus = {
        "docs": 4.0,
        "pricing": 4.0,
        "release_notes": 3.0,
        "company_fact": 2.0,
        "case_study": 3.0,
    }.get(doc_type, 0.0)
    host_bonus = 4.0 if _is_vendor_domain(url) else 0.0
    evidence_bonus = min(float(chunk_count), 3.0) + min(float(citation_count), 3.0)
    return round(
        max(
            0.0,
            min(
                100.0,
                source_kind_scores.get(source_kind, 42.0)
                + doc_type_bonus
                + host_bonus
                + evidence_bonus,
            ),
        ),
        1,
    )


def _is_vendor_domain(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host.endswith(("jfrog.com", "sonatype.com", "snyk.io", "gitlab.com", "github.com"))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


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


__all__ = ["build_source_inventory", "inventory_by_source_id"]
