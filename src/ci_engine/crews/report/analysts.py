from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable

from ci_engine.crews.report.checker import MISSING_TEXT
from ci_engine.crews.report.crew import REPORT_AGENT_SKILLS, load_agent_skill
from ci_engine.crews.report.readiness import select_best_evidence
from ci_engine.crews.report.schemas import (
    Confidence,
    EvidenceGap,
    EvidenceItem,
    EvidencePack,
    ReportClaim,
    ReportSection,
    ScoreItem,
)
from ci_engine.crews.report.sections import ReportSectionSpec, section_specs


SECTION_AGENT_KEYS = {
    "executive_summary": "strategy_analyst",
    "company_snapshot": "market_analyst",
    "market_context": "market_analyst",
    "product_feature_analysis": "product_feature_analyst",
    "technical_teardown": "technical_analyst",
    "supply_chain_security": "technical_analyst",
    "buyer_fit": "buyer_field_analyst",
    "scoring": "scoring_agent",
    "field_battlecard": "buyer_field_analyst",
}

AGENT_DISPLAY_NAMES = {
    "strategy_analyst": "Strategy Analyst",
    "market_analyst": "Market Analyst",
    "product_feature_analyst": "Product/Feature Analyst",
    "technical_analyst": "Technical Analyst",
    "buyer_field_analyst": "Buyer/Field Analyst",
    "scoring_agent": "Scoring Agent",
}

SECTION_LENSES = {
    "executive_summary": "strategic posture",
    "company_snapshot": "business profile",
    "market_context": "market position",
    "product_feature_analysis": "product and feature fit",
    "technical_teardown": "technical capability",
    "supply_chain_security": "supply chain security coverage",
    "buyer_fit": "buyer fit",
    "scoring": "scorecard inputs",
    "field_battlecard": "field narrative",
}


@dataclass(frozen=True)
class ScoreCategory:
    name: str
    dimensions: tuple[str, ...]
    sections: tuple[str, ...]


SCORE_CATEGORIES = (
    ScoreCategory(
        name="Platform Breadth",
        dimensions=(
            "product_portfolio",
            "artifact_management",
            "architecture_deployment_model",
            "ci_cd_ide_integrations",
            "ai_features",
        ),
        sections=("product_feature_analysis", "technical_teardown", "executive_summary"),
    ),
    ScoreCategory(
        name="Security Depth",
        dimensions=(
            "software_composition_analysis",
            "cve_contextual_analysis",
            "reachability_analysis",
            "malicious_package_detection",
            "open_source_curation",
            "package_firewall",
            "sbom_generation",
            "policy_governance",
            "license_compliance",
        ),
        sections=("product_feature_analysis", "technical_teardown", "supply_chain_security"),
    ),
    ScoreCategory(
        name="Buyer Proof",
        dimensions=(
            "customers_case_studies",
            "pricing_packaging",
            "target_segments_icp",
            "gtm_motion",
            "partnerships_ecosystem",
        ),
        sections=("company_snapshot", "buyer_fit", "field_battlecard"),
    ),
)


def build_analyst_sections(
    evidence_pack: EvidencePack,
    *,
    sections: list[str] | None = None,
) -> tuple[ReportSection, ...]:
    return tuple(
        _build_analyst_section(evidence_pack, spec)
        for spec in section_specs(sections)
    )


def build_score_items(evidence_pack: EvidencePack) -> tuple[ScoreItem, ...]:
    scores: list[ScoreItem] = []
    for company in (evidence_pack.jfrog, evidence_pack.competitor):
        company_items = [
            item
            for item in evidence_pack.items
            if item.company.lower() == company.lower()
        ]
        for category in SCORE_CATEGORIES:
            category_items = _category_items(company_items, category)
            best_items = select_best_evidence(category_items, limit=4)
            if not best_items:
                continue
            scores.append(
                ScoreItem(
                    id=_slug(company, category.name),
                    company=company,
                    category=category.name,
                    value=_score_value(category_items),
                    rationale=_score_rationale(category_items),
                    evidence_ids=tuple(item.id for item in best_items),
                    confidence=_score_confidence(category_items),
                )
            )
    return tuple(scores)


def _build_analyst_section(
    evidence_pack: EvidencePack,
    spec: ReportSectionSpec,
) -> ReportSection:
    agent_key = SECTION_AGENT_KEYS.get(spec.id, "strategy_analyst")
    skill_name = REPORT_AGENT_SKILLS[agent_key]
    load_agent_skill(agent_key)
    items = [
        item
        for item in evidence_pack.items
        if item.report_section == spec.id
    ]
    gaps = [
        gap
        for gap in evidence_pack.gaps
        if gap.report_section == spec.id
    ]
    claims = _section_claims(
        evidence_pack=evidence_pack,
        spec=spec,
        items=items,
        gaps=gaps,
    )
    evidence_ids = tuple(
        item.id
        for item in select_best_evidence(items, limit=8)
    )
    return ReportSection(
        id=spec.id,
        title=spec.title,
        agent_key=agent_key,
        agent_name=_agent_name(agent_key),
        skill_name=skill_name,
        evidence_ids=evidence_ids,
        claims=tuple(claims),
        narrative=_section_narrative(evidence_pack, spec, items, gaps),
    )


def _section_claims(
    *,
    evidence_pack: EvidencePack,
    spec: ReportSectionSpec,
    items: list[EvidenceItem],
    gaps: list[EvidenceGap],
) -> list[ReportClaim]:
    claims: list[ReportClaim] = []
    for company in (evidence_pack.jfrog, evidence_pack.competitor):
        company_items = [
            item for item in items if item.company.lower() == company.lower()
        ]
        if not company_items:
            claims.append(_missing_claim(company, spec.id))
            continue
        best_items = select_best_evidence(company_items, limit=4)
        claims.append(
            ReportClaim(
                id=f"{spec.id}-{_slug(company, 'evidence-position')}",
                text=_company_position_text(company, spec, company_items, gaps),
                evidence_ids=tuple(item.id for item in best_items),
                confidence=_claim_confidence(company_items),
                claim_type="analysis",
            )
        )

    comparative_items = _comparative_items(
        items,
        evidence_pack.jfrog,
        evidence_pack.competitor,
    )
    if comparative_items:
        claims.insert(
            0,
            ReportClaim(
                id=f"{spec.id}-comparative-thesis",
                text=_comparative_text(evidence_pack, spec, items),
                evidence_ids=tuple(item.id for item in comparative_items),
                confidence=_claim_confidence(comparative_items),
                claim_type="analysis",
            )
        )

    validation_items = select_best_evidence(
        [item for item in items if item.source == "tavily"],
        limit=4,
    )
    if validation_items:
        claims.append(
            ReportClaim(
                id=f"{spec.id}-web-validation",
                text=_validation_text(validation_items),
                evidence_ids=tuple(item.id for item in validation_items),
                confidence=_claim_confidence(validation_items),
                claim_type="fact",
            )
        )
    return claims


def _company_position_text(
    company: str,
    spec: ReportSectionSpec,
    items: list[EvidenceItem],
    gaps: list[EvidenceGap],
) -> str:
    lens = SECTION_LENSES.get(spec.id, "competitive position")
    dims = _top_values(item.dimension for item in items if item.dimension)
    digest = _evidence_digest(select_best_evidence(items, limit=2))
    company_gap_count = sum(
        1
        for gap in gaps
        if gap.company.lower() == company.lower()
    )
    gap_note = (
        " Some evidence remains incomplete and should be treated with measured confidence."
        if company_gap_count
        else ""
    )
    return (
        f"{company}: the {lens} picture is clearest around {dims}. "
        f"The cited material indicates {digest}.{gap_note}"
    )


def _comparative_text(
    evidence_pack: EvidencePack,
    spec: ReportSectionSpec,
    items: list[EvidenceItem],
) -> str:
    jfrog_items = [
        item
        for item in items
        if item.company.lower() == evidence_pack.jfrog.lower()
    ]
    competitor_items = [
        item
        for item in items
        if item.company.lower() == evidence_pack.competitor.lower()
    ]
    lens = SECTION_LENSES.get(spec.id, "competitive position")
    jfrog_label = _top_values(item.dimension for item in jfrog_items if item.dimension)
    competitor_label = _top_values(
        item.dimension for item in competitor_items if item.dimension
    )
    return (
        f"Comparative thesis: for {lens}, JFrog's cited strengths cluster around "
        f"{jfrog_label}, while {evidence_pack.competitor}'s cited strengths cluster "
        f"around {competitor_label}. Conclusions should be weighted most heavily "
        "where primary documentation and recent public validation point in the same direction."
    )


def _validation_text(items: list[EvidenceItem]) -> str:
    companies = _top_values(item.company for item in items)
    return (
        f"Recent public material adds context for {companies}; the cited sources "
        "should be read alongside the primary evidence rather than as standalone proof."
    )


def _section_narrative(
    evidence_pack: EvidencePack,
    spec: ReportSectionSpec,
    items: list[EvidenceItem],
    gaps: list[EvidenceGap],
) -> str:
    return ""


def _comparative_items(
    items: list[EvidenceItem],
    jfrog: str,
    competitor: str,
) -> list[EvidenceItem]:
    jfrog_items = select_best_evidence(
        [item for item in items if item.company.lower() == jfrog.lower()],
        limit=2,
    )
    competitor_items = select_best_evidence(
        [item for item in items if item.company.lower() == competitor.lower()],
        limit=2,
    )
    return [*jfrog_items, *competitor_items]


def _missing_claim(company: str, section_id: str) -> ReportClaim:
    return ReportClaim(
        id=f"{section_id}-{_slug(company, 'missing')}",
        text=f"{company}/{section_id}: {MISSING_TEXT}",
        evidence_ids=(),
        confidence="unknown",
        claim_type="missing",
    )


def _category_items(
    company_items: list[EvidenceItem],
    category: ScoreCategory,
) -> list[EvidenceItem]:
    dimensions = set(category.dimensions)
    sections = set(category.sections)
    return [
        item
        for item in company_items
        if item.report_section in sections or (item.dimension in dimensions)
    ]


def _score_value(items: list[EvidenceItem]) -> float:
    primary_count = sum(1 for item in items if item.tier == "primary")
    validation_count = sum(1 for item in items if item.tier == "validation")
    high_count = sum(1 for item in items if item.confidence == "high")
    dimension_count = len({item.dimension for item in items if item.dimension})
    source_count = len({item.url for item in items})
    raw = 1.0
    raw += min(primary_count * 0.28, 1.4)
    raw += min(validation_count * 0.18, 0.72)
    raw += min(high_count * 0.12, 0.48)
    raw += min(dimension_count * 0.24, 1.2)
    raw += min(source_count * 0.08, 0.4)
    return round(max(0.0, min(5.0, raw)), 1)


def _score_rationale(items: list[EvidenceItem]) -> str:
    primary_count = sum(1 for item in items if item.tier == "primary")
    validation_count = sum(1 for item in items if item.tier == "validation")
    dimensions = _top_values(item.dimension for item in items if item.dimension)
    source_count = len({item.url for item in items})
    return (
        f"Score reflects {primary_count} primary evidence item(s), "
        f"{validation_count} validation item(s), {source_count} distinct source(s), "
        f"and strongest dimension coverage around {dimensions}."
    )


def _score_confidence(items: list[EvidenceItem]) -> Confidence:
    primary_count = sum(1 for item in items if item.tier == "primary")
    validation_count = sum(1 for item in items if item.tier == "validation")
    source_count = len({item.url for item in items})
    if primary_count >= 3 and validation_count >= 1 and source_count >= 4:
        return "high"
    if primary_count >= 1 and source_count >= 2:
        return "medium"
    return "low"


def _claim_confidence(items: Iterable[EvidenceItem]) -> Confidence:
    item_list = list(items)
    if not item_list:
        return "unknown"
    primary_count = sum(1 for item in item_list if item.tier == "primary")
    validation_count = sum(1 for item in item_list if item.tier == "validation")
    if primary_count >= 2 and validation_count >= 1:
        return "high"
    if primary_count >= 1 or len(item_list) >= 2:
        return "medium"
    return "low"


def _top_values(values: Iterable[str | None], *, limit: int = 3) -> str:
    cleaned = [
        str(value).replace("_", " ")
        for value in values
        if value and str(value).strip()
    ]
    if not cleaned:
        return "no recent data found"
    counts = Counter(cleaned)
    return ", ".join(value for value, _ in counts.most_common(limit))


def _evidence_digest(items: list[EvidenceItem]) -> str:
    snippets = [
        _shorten(item.summary or item.quote or MISSING_TEXT)
        for item in items
    ]
    return "; ".join(snippets) if snippets else MISSING_TEXT


def _shorten(text: str, *, limit: int = 180) -> str:
    compact = _plain_text(text)
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0].rstrip() + "..."


def _plain_text(text: str) -> str:
    compact = " ".join(text.split())
    compact = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", compact)
    compact = re.sub(r"(^|\s)#{1,6}\s*", " ", compact)
    compact = compact.replace("**", "").replace("__", "").replace("`", "")
    compact = compact.replace("_", " ")
    compact = re.sub(
        r"\b(Source type|Source overview|Source kind|Tags?|Keywords?|Metadata|Raw path|Source path)\s*:?\s*",
        "",
        compact,
        flags=re.IGNORECASE,
    )
    compact = re.sub(
        r"\bofficial\s+llm\s+research\s+report\b",
        "research brief",
        compact,
        flags=re.IGNORECASE,
    )
    compact = re.sub(
        r"\b(vendor marketing page|vendor marketing comparison page)\b",
        "vendor page",
        compact,
        flags=re.IGNORECASE,
    )
    compact = compact.lstrip("-* ").strip()
    return " ".join(compact.split())


def _agent_name(agent_key: str) -> str:
    return AGENT_DISPLAY_NAMES.get(agent_key, agent_key.replace("_", " ").title())


def _slug(*parts: str) -> str:
    return "-".join(
        part.lower().replace("/", "-").replace(" ", "-")
        for part in parts
        if part
    )


__all__ = [
    "AGENT_DISPLAY_NAMES",
    "SECTION_AGENT_KEYS",
    "build_analyst_sections",
    "build_score_items",
]
