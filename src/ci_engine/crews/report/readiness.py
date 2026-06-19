from __future__ import annotations

from statistics import mean

from ci_engine.crews.report.schemas import (
    EvidenceItem,
    EvidencePack,
    EvidenceReadinessCompany,
    EvidenceReadinessReport,
    EvidenceReadinessSection,
    ReadinessStatus,
)
from ci_engine.crews.report.sections import ReportSectionSpec, critical_section_ids


def analyze_evidence_readiness(
    evidence_pack: EvidencePack,
    specs: tuple[ReportSectionSpec, ...],
) -> EvidenceReadinessReport:
    sections = tuple(
        _section_readiness(evidence_pack, spec)
        for spec in specs
    )
    overall = round(mean(section.readiness_score for section in sections), 1) if sections else 0.0
    status = _overall_status(sections)
    notes = _report_notes(sections)
    return EvidenceReadinessReport(
        overall_score=overall,
        status=status,
        sections=sections,
        notes=tuple(notes),
    )


def _section_readiness(
    evidence_pack: EvidencePack,
    spec: ReportSectionSpec,
) -> EvidenceReadinessSection:
    companies = tuple(
        _company_readiness(evidence_pack, spec, company)
        for company in (evidence_pack.jfrog, evidence_pack.competitor)
    )
    score = round(mean(company.readiness_score for company in companies), 1)
    status = _lowest_status([company.status for company in companies])
    notes: list[str] = []
    if any(company.status == "weak" for company in companies):
        notes.append("At least one company has weak evidence for this section.")
    if any(company.tavily_items == 0 for company in companies):
        notes.append("At least one company lacks Tavily validation evidence.")
    if any(company.db_items == 0 for company in companies):
        notes.append("At least one company lacks DB-backed evidence.")
    return EvidenceReadinessSection(
        section_id=spec.id,
        title=spec.title,
        readiness_score=score,
        status=status,
        companies=companies,
        notes=tuple(notes),
    )


def _company_readiness(
    evidence_pack: EvidencePack,
    spec: ReportSectionSpec,
    company: str,
) -> EvidenceReadinessCompany:
    items = [
        item
        for item in evidence_pack.items
        if item.company.lower() == company.lower()
        and item.report_section == spec.id
    ]
    gaps = [
        gap
        for gap in evidence_pack.gaps
        if gap.company.lower() == company.lower()
        and gap.report_section == spec.id
    ]
    db_items = [item for item in items if item.source == "db"]
    tavily_items = [item for item in items if item.source == "tavily"]
    primary_items = [item for item in items if item.tier == "primary"]
    source_count = len({item.url for item in items})
    high_confidence = [item for item in items if item.confidence == "high"]
    score = _score_items(
        db_count=len(db_items),
        tavily_count=len(tavily_items),
        primary_count=len(primary_items),
        source_count=source_count,
        high_confidence_count=len(high_confidence),
        gap_count=len(gaps),
    )
    status = _company_status(
        score=score,
        db_count=len(db_items),
        tavily_count=len(tavily_items),
        critical=spec.id in critical_section_ids(),
    )
    notes = _company_notes(
        db_count=len(db_items),
        tavily_count=len(tavily_items),
        source_count=source_count,
        gap_count=len(gaps),
    )
    return EvidenceReadinessCompany(
        company=company,
        db_items=len(db_items),
        tavily_items=len(tavily_items),
        primary_items=len(primary_items),
        source_count=source_count,
        high_confidence_items=len(high_confidence),
        gap_count=len(gaps),
        readiness_score=score,
        status=status,
        notes=tuple(notes),
    )


def select_best_evidence(
    items: list[EvidenceItem],
    *,
    limit: int = 6,
) -> list[EvidenceItem]:
    return sorted(items, key=_evidence_sort_key, reverse=True)[:limit]


def _score_items(
    *,
    db_count: int,
    tavily_count: int,
    primary_count: int,
    source_count: int,
    high_confidence_count: int,
    gap_count: int,
) -> float:
    score = 0.0
    score += min(db_count * 9.0, 36.0)
    score += min(primary_count * 7.0, 28.0)
    score += min(tavily_count * 8.0, 24.0)
    score += min(source_count * 3.0, 12.0)
    score += min(high_confidence_count * 3.0, 12.0)
    score -= min(gap_count * 1.5, 18.0)
    return round(max(0.0, min(100.0, score)), 1)


def _company_status(
    *,
    score: float,
    db_count: int,
    tavily_count: int,
    critical: bool,
) -> ReadinessStatus:
    if score >= 70.0 and db_count >= 2 and tavily_count >= 1:
        return "ready"
    if score >= 45.0 and db_count >= 1 and (tavily_count >= 1 or not critical):
        return "needs_review"
    return "weak"


def _overall_status(sections: tuple[EvidenceReadinessSection, ...]) -> ReadinessStatus:
    critical = critical_section_ids()
    if any(section.status == "weak" and section.section_id in critical for section in sections):
        return "weak"
    if any(section.status != "ready" for section in sections):
        return "needs_review"
    return "ready"


def _lowest_status(statuses: list[ReadinessStatus]) -> ReadinessStatus:
    if "weak" in statuses:
        return "weak"
    if "needs_review" in statuses:
        return "needs_review"
    return "ready"


def _report_notes(sections: tuple[EvidenceReadinessSection, ...]) -> list[str]:
    weak = [section.section_id for section in sections if section.status == "weak"]
    review = [section.section_id for section in sections if section.status == "needs_review"]
    notes: list[str] = []
    if weak:
        notes.append("Weak evidence sections: " + ", ".join(weak))
    if review:
        notes.append("Sections needing review: " + ", ".join(review))
    if not notes:
        notes.append("All sections meet the current evidence-readiness threshold.")
    return notes


def _company_notes(
    *,
    db_count: int,
    tavily_count: int,
    source_count: int,
    gap_count: int,
) -> list[str]:
    notes: list[str] = []
    if db_count == 0:
        notes.append("No DB-backed evidence.")
    if tavily_count == 0:
        notes.append("No Tavily validation evidence.")
    if source_count < 3:
        notes.append("Low source diversity.")
    if gap_count:
        notes.append(f"{gap_count} evidence gaps remain.")
    return notes


def _evidence_sort_key(item: EvidenceItem) -> tuple[int, int, float, int, str]:
    tier_score = {"primary": 3, "supporting": 2, "validation": 1}[item.tier]
    source_score = 2 if item.source == "db" else 1
    confidence_score = {"high": 3, "medium": 2, "low": 1, "unknown": 0}[item.confidence]
    quality_score = float(item.metadata.get("source_quality_score") or 0.0)
    citation_score = 1 if item.metadata.get("citations") else 0
    date_value = item.published.isoformat() if item.published else ""
    return (tier_score, source_score, quality_score, confidence_score + citation_score, date_value)


__all__ = ["analyze_evidence_readiness", "select_best_evidence"]
