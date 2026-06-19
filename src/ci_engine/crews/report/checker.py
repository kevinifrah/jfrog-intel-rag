from __future__ import annotations

import re

from ci_engine.crews.report.schemas import (
    EvidencePack,
    ReportDraft,
    ValidationFinding,
    ValidationReport,
)
from ci_engine.crews.report.sections import critical_section_ids

MISSING_TEXT = "no recent data found"
MIN_CRITICAL_EVIDENCE_PER_COMPANY = 2
MIN_REQUIRED_EVIDENCE_PER_COMPANY = 1
SOURCE_LIST_PROSE_PATTERNS = (
    "current section uses",
    "source types led by",
    "key support:",
    "evidence:",
    "source:",
    "web validation contributes",
    "from the frozen evidencepack",
)
_EVIDENCE_ID_RE = re.compile(r"\[[a-f0-9]{12,40}\]", re.IGNORECASE)
_SOURCE_NUMBER_RE = re.compile(r"\[(?:\d{1,2})(?:\s*,\s*\d{1,2})*\]")
_AUDIT_LABEL_RE = re.compile(r"^\s*(?:[-*•·]\s*)?(?:evidence|sources?)\s*:", re.IGNORECASE | re.MULTILINE)

FULL_CREW_SCORING_MODE = "crew_strategy_market_product_technical_field_scoring"
STRATEGY_MODES = {
    "crew_strategy",
    "crew_strategy_market",
    "crew_strategy_market_technical",
    "crew_strategy_market_technical_field",
    "crew_strategy_market_product_technical_field",
    FULL_CREW_SCORING_MODE,
}
MARKET_MODES = {
    "crew_strategy_market",
    "crew_strategy_market_technical",
    "crew_strategy_market_technical_field",
    "crew_strategy_market_product_technical_field",
    FULL_CREW_SCORING_MODE,
}
PRODUCT_FEATURE_MODES = {
    "crew_strategy_market_product_technical_field",
    FULL_CREW_SCORING_MODE,
}
TECHNICAL_MODES = {
    "crew_strategy_market_technical",
    "crew_strategy_market_technical_field",
    "crew_strategy_market_product_technical_field",
    FULL_CREW_SCORING_MODE,
}
BUYER_FIELD_MODES = {
    "crew_strategy_market_technical_field",
    "crew_strategy_market_product_technical_field",
    FULL_CREW_SCORING_MODE,
}
SCORING_MODES = {FULL_CREW_SCORING_MODE}
SCORING_CATEGORY_NAMES = (
    "Platform Consolidation Fit",
    "Open Source Governance Fit",
    "Security Prioritization Fit",
    "Field Execution Fit",
)


def check_report(evidence_pack: EvidencePack, draft: ReportDraft) -> ValidationReport:
    findings: list[ValidationFinding] = []
    evidence_ids = {item.id for item in evidence_pack.items}
    sections = {section.id: section for section in draft.sections}
    findings.extend(_check_evidence_pack(evidence_pack, draft))
    findings.extend(_check_readiness(evidence_pack))

    if draft.evidence_pack_id != evidence_pack.id:
        findings.append(
            ValidationFinding(
                severity="error",
                code="evidence_pack_mismatch",
                message="Report draft references a different evidence pack.",
            )
        )
    findings.extend(_check_strategy_mode(evidence_pack, draft))
    findings.extend(_check_market_mode(evidence_pack, draft))
    findings.extend(_check_product_feature_mode(evidence_pack, draft))
    findings.extend(_check_technical_mode(evidence_pack, draft))
    findings.extend(_check_buyer_field_mode(evidence_pack, draft))
    findings.extend(_check_scoring_mode(draft))
    findings.extend(_check_neutrality_contract(draft))

    for section in draft.sections:
        unknown_section_ids = tuple(
            evidence_id
            for evidence_id in section.evidence_ids
            if evidence_id not in evidence_ids
        )
        if unknown_section_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="broken_section_citation",
                    message="Section cites evidence IDs that are not in the frozen evidence pack.",
                    section_id=section.id,
                    evidence_ids=unknown_section_ids,
                )
            )
        for claim in section.claims:
            unknown_ids = tuple(
                evidence_id
                for evidence_id in claim.evidence_ids
                if evidence_id not in evidence_ids
            )
            if unknown_ids:
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="broken_citation",
                        message="Claim cites evidence IDs that are not in the frozen evidence pack.",
                        section_id=section.id,
                        claim_id=claim.id,
                        evidence_ids=unknown_ids,
                    )
                )
            if not claim.evidence_ids and claim.claim_type != "missing":
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="unsupported_claim",
                        message="Claim has no supporting evidence.",
                        section_id=section.id,
                        claim_id=claim.id,
                    )
                )
            if claim.claim_type == "missing" and MISSING_TEXT not in claim.text.lower():
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="bad_missing_phrase",
                        message='Missing-data claims must include "no recent data found".',
                        section_id=section.id,
                        claim_id=claim.id,
                    )
                )

    for score in draft.scores:
        if not score.evidence_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="uncited_score",
                    message="Score item has no cited evidence.",
                    evidence_ids=(),
                )
            )
        unknown_ids = tuple(
            evidence_id
            for evidence_id in score.evidence_ids
            if evidence_id not in evidence_ids
        )
        if unknown_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="broken_score_citation",
                    message="Score item cites evidence IDs that are not in the frozen evidence pack.",
                    evidence_ids=unknown_ids,
                )
            )

    item_sections = {item.report_section for item in evidence_pack.items}
    for section_id in sorted(critical_section_ids()):
        if section_id not in sections:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="missing_critical_section",
                    message=f"Critical section {section_id} is absent from the report draft.",
                    section_id=section_id,
                )
            )
        elif section_id not in item_sections:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="weak_critical_evidence",
                    message=f"Critical section {section_id} has no evidence in the frozen pack.",
                    section_id=section_id,
                )
            )

    for gap in evidence_pack.gaps:
        findings.append(
            ValidationFinding(
                severity="warning",
                code="evidence_gap",
                message=f"{gap.company}/{gap.report_section}: {gap.reason}",
                section_id=gap.report_section,
            )
        )

    return ValidationReport(
        passed=not any(finding.severity == "error" for finding in findings),
        findings=tuple(findings),
    )


def _check_evidence_pack(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    section_ids = {section.id for section in draft.sections}
    expected_companies = {draft.jfrog.lower(), draft.competitor.lower()}
    web_enabled = bool(evidence_pack.metadata.get("web_enabled"))

    for item in evidence_pack.items:
        if item.report_section not in section_ids:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    code="non_pertinent_evidence_section",
                    message="Evidence item is not mapped to a report section.",
                    section_id=item.report_section,
                    evidence_ids=(item.id,),
                )
            )
        if item.company.lower() not in expected_companies:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    code="non_pertinent_evidence_company",
                    message="Evidence item is not for JFrog or the selected competitor.",
                    section_id=item.report_section,
                    evidence_ids=(item.id,),
                )
            )
        if item.source == "tavily" and item.classification == "contradicts_db":
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="unresolved_web_contradiction",
                    message="Tavily evidence contradicts DB evidence and must be resolved before rendering.",
                    section_id=item.report_section,
                    evidence_ids=(item.id,),
                )
            )

    for section in draft.sections:
        for company in (draft.jfrog, draft.competitor):
            company_items = [
                item
                for item in evidence_pack.items
                if item.report_section == section.id
                and item.company.lower() == company.lower()
            ]
            min_count = (
                MIN_CRITICAL_EVIDENCE_PER_COMPANY
                if section.id in critical_section_ids()
                else MIN_REQUIRED_EVIDENCE_PER_COMPANY
            )
            if len(company_items) < min_count:
                findings.append(
                    ValidationFinding(
                        severity="error" if section.id in critical_section_ids() else "warning",
                        code="thin_section_evidence",
                        message=(
                            f"{company}/{section.id} has {len(company_items)} evidence "
                            f"items; expected at least {min_count}."
                        ),
                        section_id=section.id,
                        evidence_ids=tuple(item.id for item in company_items),
                    )
                )

            if web_enabled and not any(item.source == "tavily" for item in company_items):
                findings.append(
                    ValidationFinding(
                        severity="warning",
                        code="missing_web_validation",
                        message=f"{company}/{section.id} has no Tavily validation evidence.",
                        section_id=section.id,
                        evidence_ids=tuple(item.id for item in company_items),
                    )
                )
            if not any(item.source == "db" for item in company_items):
                findings.append(
                    ValidationFinding(
                        severity="error" if section.id in critical_section_ids() else "warning",
                        code="missing_db_evidence",
                        message=f"{company}/{section.id} has no DB-backed evidence.",
                        section_id=section.id,
                        evidence_ids=tuple(item.id for item in company_items),
                    )
                )

    return findings


def _check_readiness(evidence_pack: EvidencePack) -> list[ValidationFinding]:
    if evidence_pack.readiness is None:
        return [
            ValidationFinding(
                severity="warning",
                code="missing_evidence_readiness",
                message="EvidencePack has no readiness analysis.",
            )
        ]

    findings: list[ValidationFinding] = []
    critical = critical_section_ids()
    for section in evidence_pack.readiness.sections:
        if section.status == "ready":
            continue
        severity = "error" if section.section_id in critical and section.status == "weak" else "warning"
        findings.append(
            ValidationFinding(
                severity=severity,
                code=f"evidence_readiness_{section.status}",
                message=(
                    f"{section.section_id} evidence readiness is {section.status} "
                    f"with score {section.readiness_score}."
                ),
                section_id=section.section_id,
            )
        )
    return findings


def _check_strategy_mode(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
) -> list[ValidationFinding]:
    if draft.metadata.get("draft_mode") not in STRATEGY_MODES:
        return []

    findings: list[ValidationFinding] = []
    error = draft.metadata.get("strategy_generation_error")
    if error:
        findings.append(
            ValidationFinding(
                severity="error",
                code="strategy_generation_failed",
                message=str(error),
                section_id="executive_summary",
            )
        )

    executive = next(
        (section for section in draft.sections if section.id == "executive_summary"),
        None,
    )
    if executive is None:
        return findings

    if not any(
        claim.id.startswith("strategy-recommended-action")
        for claim in executive.claims
    ):
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_strategy_recommendations",
                message="Strategy Analyst executive summary must include cited recommended actions.",
                section_id="executive_summary",
            )
        )

    texts = [executive.narrative or "", *(claim.text for claim in executive.claims)]
    for text in texts:
        if _contains_source_list_prose(text):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="source_list_prose_in_strategy",
                    message="Strategy Analyst executive summary reads like source inventory instead of CI synthesis.",
                    section_id="executive_summary",
                )
            )
            break

    for claim in executive.claims:
        claim_text = claim.text.lower()
        if "market share" not in claim_text or _is_missing_market_share_statement(claim_text):
            continue
        if not _claim_cited_text_contains(evidence_pack, claim.evidence_ids, "market share"):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="unsupported_market_share_claim",
                    message="Strategy claim mentions market share without cited market-share evidence.",
                    section_id="executive_summary",
                    claim_id=claim.id,
                    evidence_ids=claim.evidence_ids,
                )
            )
    return findings


def _check_market_mode(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
) -> list[ValidationFinding]:
    if draft.metadata.get("draft_mode") not in MARKET_MODES:
        return []

    findings: list[ValidationFinding] = []
    error = draft.metadata.get("market_generation_error")
    if error:
        findings.append(
            ValidationFinding(
                severity="error",
                code="market_generation_failed",
                message=str(error),
                section_id="market_context",
            )
        )

    sections = {section.id: section for section in draft.sections}
    company = sections.get("company_snapshot")
    market = sections.get("market_context")
    if company is None or market is None:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_market_sections",
                message="Market Analyst must produce company_snapshot and market_context.",
            )
        )
        return findings

    required_prefixes = {
        "company_snapshot": (
            "market-company-snapshot-thesis",
            "market-jfrog-company-position",
            "market-competitor-company-position",
        ),
        "market_context": (
            "market-context-thesis",
            "market-buyer-segment",
            "market-gtm-motion",
            "market-ecosystem-signal",
            "market-risk",
        ),
    }
    for section in (company, market):
        claim_ids = [claim.id for claim in section.claims]
        for prefix in required_prefixes[section.id]:
            if not any(claim_id.startswith(prefix) for claim_id in claim_ids):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="missing_market_claim_group",
                        message=f"Market Analyst section {section.id} is missing {prefix}.",
                        section_id=section.id,
                    )
                )

        texts = [section.narrative or "", *(claim.text for claim in section.claims)]
        for text in texts:
            if _contains_source_list_prose(text):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="source_list_prose_in_market",
                        message="Market Analyst section reads like source inventory instead of market intelligence synthesis.",
                        section_id=section.id,
                    )
                )
                break

        for claim in section.claims:
            claim_text = claim.text.lower()
            if "market share" not in claim_text or _is_missing_market_share_statement(claim_text):
                continue
            if not _claim_cited_text_contains(evidence_pack, claim.evidence_ids, "market share"):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="unsupported_market_share_claim",
                        message="Market claim mentions market share without cited market-share evidence.",
                        section_id=section.id,
                        claim_id=claim.id,
                        evidence_ids=claim.evidence_ids,
                    )
                )
    return findings


def _check_product_feature_mode(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
) -> list[ValidationFinding]:
    if draft.metadata.get("draft_mode") not in PRODUCT_FEATURE_MODES:
        return []

    findings: list[ValidationFinding] = []
    error = draft.metadata.get("product_feature_generation_error")
    if error:
        findings.append(
            ValidationFinding(
                severity="error",
                code="product_feature_generation_failed",
                message=str(error),
                section_id="product_feature_analysis",
            )
        )

    sections = {section.id: section for section in draft.sections}
    product = sections.get("product_feature_analysis")
    if product is None:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_product_feature_section",
                message="Product/Feature Analyst must produce product_feature_analysis.",
                section_id="product_feature_analysis",
            )
        )
        return findings

    claim_ids = [claim.id for claim in product.claims]
    for prefix in (
        "product-feature-thesis",
        "product-jfrog-advantage",
        "product-competitor-advantage",
        "product-jfrog-limitation",
        "product-parity-gap",
        "product-buyer-implication",
    ):
        if not any(claim_id.startswith(prefix) for claim_id in claim_ids):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="missing_product_feature_claim_group",
                    message=f"Product/Feature Analyst section is missing {prefix}.",
                    section_id="product_feature_analysis",
                )
            )

    texts = [product.narrative or "", *(claim.text for claim in product.claims)]
    if any(_contains_source_list_prose(text) for text in texts):
        findings.append(
            ValidationFinding(
                severity="error",
                code="source_list_prose_in_product_feature",
                message="Product/Feature Analyst section reads like source inventory instead of product CI synthesis.",
                section_id="product_feature_analysis",
            )
        )

    matrix = product.metadata.get("capability_matrix")
    if not isinstance(matrix, list) or len(matrix) < 6:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_product_feature_matrix",
                message="Product/Feature Analyst must include a cited capability matrix with at least six rows.",
                section_id="product_feature_analysis",
            )
        )
        return findings

    evidence_ids = {item.id for item in evidence_pack.items}
    competitor_advantage_rows = 0
    for index, row in enumerate(matrix, start=1):
        if not isinstance(row, dict):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="invalid_product_feature_matrix_row",
                    message=f"Capability matrix row {index} is not structured.",
                    section_id="product_feature_analysis",
                )
            )
            continue
        if row.get("assessment") == "competitor_advantage":
            competitor_advantage_rows += 1
        row_texts = [
            str(row.get("capability", "")),
            str(row.get("jfrog", "")),
            str(row.get("competitor", "")),
        ]
        if any(not text.strip() for text in row_texts):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="invalid_product_feature_matrix_row",
                    message=f"Capability matrix row {index} has empty comparison text.",
                    section_id="product_feature_analysis",
                )
            )
        if any(_contains_source_list_prose(text) for text in row_texts):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="source_list_prose_in_product_feature_matrix",
                    message=f"Capability matrix row {index} contains source-list prose.",
                    section_id="product_feature_analysis",
                )
            )
        row_evidence_ids = tuple(str(evidence_id) for evidence_id in row.get("evidence_ids", ()))
        if not row_evidence_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="uncited_product_feature_matrix_row",
                    message=f"Capability matrix row {index} has no cited evidence.",
                    section_id="product_feature_analysis",
                )
            )
            continue
        unknown_ids = tuple(
            evidence_id for evidence_id in row_evidence_ids if evidence_id not in evidence_ids
        )
        if unknown_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="broken_product_feature_matrix_citation",
                    message=f"Capability matrix row {index} cites evidence outside the frozen pack.",
                    section_id="product_feature_analysis",
                    evidence_ids=unknown_ids,
                )
            )

    if competitor_advantage_rows == 0:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_product_feature_competitor_advantage_row",
                message=(
                    "Product/Feature capability matrix must include at least one cited row "
                    "where the competitor has the advantage."
                ),
                section_id="product_feature_analysis",
            )
        )

    findings.extend(_check_capability_evidence_matrix(evidence_pack, draft, product))

    for claim in product.claims:
        claim_text = claim.text.lower()
        if "market share" not in claim_text or _is_missing_market_share_statement(claim_text):
            continue
        if not _claim_cited_text_contains(evidence_pack, claim.evidence_ids, "market share"):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="unsupported_market_share_claim",
                    message="Product/feature claim mentions market share without cited market-share evidence.",
                    section_id=product.id,
                    claim_id=claim.id,
                    evidence_ids=claim.evidence_ids,
                )
            )
    return findings


def _check_capability_evidence_matrix(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
    product: object,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    matrix = evidence_pack.capability_matrix
    if matrix is None:
        return [
            ValidationFinding(
                severity="error",
                code="missing_capability_evidence_matrix",
                message="Product/Feature mode requires a frozen capability evidence matrix.",
                section_id="product_feature_analysis",
            )
        ]

    product_catalog = getattr(evidence_pack, "product_catalog", ())
    if not product_catalog:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_product_catalog",
                message="Product/Feature mode requires a product catalog for product-specific comparison.",
                section_id="product_feature_analysis",
            )
        )

    web_enabled = bool(evidence_pack.metadata.get("web_enabled"))
    reported_gap_ids = {
        str(row.get("capability_id"))
        for row in getattr(product, "metadata", {}).get("capability_evidence_gaps", [])
        if isinstance(row, dict)
    }
    unresolved_statuses = {
        "not_found_after_search",
        "unclear_needs_review",
        "contradictory",
    }
    for row in matrix.rows:
        if not row.must_resolve:
            continue
        for cell in (row.jfrog, row.competitor):
            sources = {attempt.source for attempt in cell.search_attempts}
            if "db" not in sources or (web_enabled and "tavily" not in sources):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="missing_capability_targeted_search",
                        message=(
                            f"{cell.company}/{row.capability_label} lacks required "
                            "targeted DB and web search attempts."
                        ),
                        section_id="product_feature_analysis",
                        evidence_ids=cell.evidence_ids,
                    )
                )
            if cell.status == "unclear_needs_review":
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="unclear_must_resolve_capability",
                        message=(
                            f"{cell.company}/{row.capability_label} remained unclear "
                            "without a closed search outcome."
                        ),
                        section_id="product_feature_analysis",
                        evidence_ids=cell.evidence_ids,
                    )
                )
        if row.search_status in unresolved_statuses and row.capability_id not in reported_gap_ids:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="unreported_capability_evidence_gap",
                    message=(
                        f"{row.capability_label} is unresolved after targeted search "
                        "but is not exposed in the product evidence gap table."
                    ),
                    section_id="product_feature_analysis",
                    evidence_ids=row.evidence_ids,
                )
            )
    return findings


def _check_neutrality_contract(draft: ReportDraft) -> list[ValidationFinding]:
    mode = draft.metadata.get("draft_mode")
    if mode not in STRATEGY_MODES:
        return []
    requirements = {
        "executive_summary": {
            "competitor": ("strategy-competitor-strength",),
            "exposure": ("strategy-risk",),
        },
        "market_context": {
            "competitor": ("market-risk", "market-gtm-motion"),
            "exposure": ("market-risk",),
        },
        "product_feature_analysis": {
            "competitor": ("product-competitor-advantage",),
            "exposure": ("product-jfrog-limitation",),
        },
        "technical_teardown": {
            "competitor": ("technical-competitor-capability",),
            "exposure": ("technical-competitor-capability",),
        },
        "supply_chain_security": {
            "competitor": ("technical-security-comparison",),
            "exposure": ("technical-risk",),
        },
        "buyer_fit": {
            "competitor": ("buyer-competitor-win-condition",),
            "exposure": ("buyer-qualify-out-signal",),
        },
        "field_battlecard": {
            "competitor": ("field-objection-handling",),
            "exposure": ("field-objection-handling", "field-discovery-question"),
        },
    }
    findings: list[ValidationFinding] = []
    sections = {section.id: section for section in draft.sections}
    active_section_ids = _active_neutrality_section_ids(str(mode))
    for section_id, prefixes in requirements.items():
        if section_id not in active_section_ids:
            continue
        section = sections.get(section_id)
        if section is None:
            continue
        claim_ids = [claim.id for claim in section.claims]
        if not any(
            any(claim_id.startswith(prefix) for prefix in prefixes["competitor"])
            for claim_id in claim_ids
        ):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="missing_neutral_competitor_pressure",
                    message=(
                        f"{section.title} must include cited competitor strength, "
                        "pressure, or win-condition analysis."
                    ),
                    section_id=section_id,
                )
            )
        if not any(
            any(claim_id.startswith(prefix) for prefix in prefixes["exposure"])
            for claim_id in claim_ids
        ):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="missing_neutral_jfrog_exposure",
                    message=(
                        f"{section.title} must include cited JFrog risk, limitation, "
                        "loss condition, or exposure analysis."
                    ),
                    section_id=section_id,
                )
            )
    return findings


def _active_neutrality_section_ids(mode: str) -> set[str]:
    if mode == "crew_strategy":
        return {"executive_summary"}
    if mode == "crew_strategy_market":
        return {"executive_summary", "market_context"}
    if mode == "crew_strategy_market_technical":
        return {
            "executive_summary",
            "market_context",
            "technical_teardown",
            "supply_chain_security",
        }
    if mode == "crew_strategy_market_technical_field":
        return {
            "executive_summary",
            "market_context",
            "technical_teardown",
            "supply_chain_security",
            "buyer_fit",
            "field_battlecard",
        }
    if mode in {
        "crew_strategy_market_product_technical_field",
        FULL_CREW_SCORING_MODE,
    }:
        return {
            "executive_summary",
            "market_context",
            "product_feature_analysis",
            "technical_teardown",
            "supply_chain_security",
            "buyer_fit",
            "field_battlecard",
        }
    return set()


def _check_technical_mode(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
) -> list[ValidationFinding]:
    if draft.metadata.get("draft_mode") not in TECHNICAL_MODES:
        return []

    findings: list[ValidationFinding] = []
    error = draft.metadata.get("technical_generation_error")
    if error:
        findings.append(
            ValidationFinding(
                severity="error",
                code="technical_generation_failed",
                message=str(error),
                section_id="technical_teardown",
            )
        )

    sections = {section.id: section for section in draft.sections}
    teardown = sections.get("technical_teardown")
    security = sections.get("supply_chain_security")
    if teardown is None or security is None:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_technical_sections",
                message="Technical Analyst must produce technical_teardown and supply_chain_security.",
            )
        )
        return findings

    required_prefixes = {
        "technical_teardown": (
            "technical-teardown-thesis",
            "technical-jfrog-capability",
            "technical-competitor-capability",
            "technical-architecture-workflow",
            "technical-ai-artifact-governance",
        ),
        "supply_chain_security": (
            "technical-security-comparison",
            "technical-risk",
        ),
    }
    for section in (teardown, security):
        claim_ids = [claim.id for claim in section.claims]
        for prefix in required_prefixes[section.id]:
            if not any(claim_id.startswith(prefix) for claim_id in claim_ids):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="missing_technical_claim_group",
                        message=f"Technical Analyst section {section.id} is missing {prefix}.",
                        section_id=section.id,
                    )
                )

        texts = [section.narrative or "", *(claim.text for claim in section.claims)]
        for text in texts:
            if _contains_source_list_prose(text):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="source_list_prose_in_technical",
                        message="Technical Analyst section reads like source inventory instead of technical CI synthesis.",
                        section_id=section.id,
                    )
                )
                break

        for claim in section.claims:
            claim_text = claim.text.lower()
            if "market share" not in claim_text or _is_missing_market_share_statement(claim_text):
                continue
            if not _claim_cited_text_contains(evidence_pack, claim.evidence_ids, "market share"):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="unsupported_market_share_claim",
                        message="Technical claim mentions market share without cited market-share evidence.",
                        section_id=section.id,
                        claim_id=claim.id,
                        evidence_ids=claim.evidence_ids,
                    )
                )
    return findings


def _check_buyer_field_mode(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
) -> list[ValidationFinding]:
    if draft.metadata.get("draft_mode") not in BUYER_FIELD_MODES:
        return []

    findings: list[ValidationFinding] = []
    error = draft.metadata.get("buyer_field_generation_error")
    if error:
        findings.append(
            ValidationFinding(
                severity="error",
                code="buyer_field_generation_failed",
                message=str(error),
                section_id="buyer_fit",
            )
        )

    sections = {section.id: section for section in draft.sections}
    buyer = sections.get("buyer_fit")
    field = sections.get("field_battlecard")
    if buyer is None or field is None:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_buyer_field_sections",
                message="Buyer/Field Analyst must produce buyer_fit and field_battlecard.",
            )
        )
        return findings

    required_prefixes = {
        "buyer_fit": (
            "buyer-fit-thesis",
            "buyer-jfrog-win-condition",
            "buyer-competitor-win-condition",
            "buyer-qualify-out-signal",
        ),
        "field_battlecard": (
            "field-battlecard-thesis",
            "field-objection-handling",
            "field-discovery-question",
            "field-action",
        ),
    }
    for section in (buyer, field):
        claim_ids = [claim.id for claim in section.claims]
        for prefix in required_prefixes[section.id]:
            if not any(claim_id.startswith(prefix) for claim_id in claim_ids):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="missing_buyer_field_claim_group",
                        message=f"Buyer/Field Analyst section {section.id} is missing {prefix}.",
                        section_id=section.id,
                    )
                )

        texts = [section.narrative or "", *(claim.text for claim in section.claims)]
        for text in texts:
            if _contains_source_list_prose(text):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="source_list_prose_in_buyer_field",
                        message="Buyer/Field Analyst section reads like source inventory instead of field CI synthesis.",
                        section_id=section.id,
                    )
                )
                break

        for claim in section.claims:
            claim_text = claim.text.lower()
            if "market share" not in claim_text or _is_missing_market_share_statement(claim_text):
                continue
            if not _claim_cited_text_contains(evidence_pack, claim.evidence_ids, "market share"):
                findings.append(
                    ValidationFinding(
                        severity="error",
                        code="unsupported_market_share_claim",
                        message="Buyer/field claim mentions market share without cited market-share evidence.",
                        section_id=section.id,
                        claim_id=claim.id,
                        evidence_ids=claim.evidence_ids,
                    )
                )
    return findings


def _check_scoring_mode(draft: ReportDraft) -> list[ValidationFinding]:
    if draft.metadata.get("draft_mode") not in SCORING_MODES:
        return []

    findings: list[ValidationFinding] = []
    error = draft.metadata.get("scoring_generation_error")
    if error:
        findings.append(
            ValidationFinding(
                severity="error",
                code="scoring_generation_failed",
                message=str(error),
            )
        )
    if not draft.scores:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_scoring_scores",
                message="Scoring Agent mode requires cited buyer-scenario scorecards.",
            )
        )
        return findings

    companies = {draft.jfrog.lower(), draft.competitor.lower()}
    score_pairs = {
        (score.category, score.company.lower())
        for score in draft.scores
    }
    for category in SCORING_CATEGORY_NAMES:
        for company in companies:
            if (category, company) in score_pairs:
                continue
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="missing_scoring_category_score",
                    message=f"Scoring Agent is missing {category} for {company}.",
                )
            )

    for score in draft.scores:
        if score.category not in SCORING_CATEGORY_NAMES:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="unsupported_scoring_category",
                    message=f"Scoring Agent returned unsupported category {score.category}.",
                    evidence_ids=score.evidence_ids,
                )
            )
        if score.company.lower() not in companies:
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="unsupported_scoring_company",
                    message=f"Scoring Agent returned score for unsupported company {score.company}.",
                    evidence_ids=score.evidence_ids,
                )
            )
        if _contains_source_list_prose(score.rationale):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="source_list_prose_in_scoring",
                    message="Scoring rationale reads like source inventory instead of buyer-scenario scoring.",
                    evidence_ids=score.evidence_ids,
                )
            )
        score_text = score.rationale.lower()
        if "market share" in score_text and not _is_missing_market_share_statement(score_text):
            findings.append(
                ValidationFinding(
                    severity="error",
                    code="unsupported_market_share_score_claim",
                    message="Score rationale mentions market share without a missing-data caveat.",
                    evidence_ids=score.evidence_ids,
                )
            )
    return findings


def _claim_cited_text_contains(
    evidence_pack: EvidencePack,
    evidence_ids: tuple[str, ...],
    phrase: str,
) -> bool:
    evidence_by_id = {item.id: item for item in evidence_pack.items}
    cited_text = " ".join(
        " ".join(
            part
            for part in (
                item.title,
                item.summary,
                item.quote,
            )
            if part
        )
        for item in (
            evidence_by_id.get(evidence_id)
            for evidence_id in evidence_ids
        )
        if item is not None
    ).lower()
    return phrase.lower() in cited_text


def _is_missing_market_share_statement(text: str) -> bool:
    missing_markers = (
        "no recent data found",
        "no data found",
        "not found",
        "not validated",
        "not substantiated",
        "not established",
        "not present",
        "unavailable",
        "not available",
        "insufficient evidence",
        "limiting the ability to quantify",
        "cannot be quantified",
        "avoids inferring",
        "no independently",        # "no independently validated/verified ... market share"
        "no independent",          # "no independent analyst ... market share"
        "limits confidence",       # "limits confidence in any claim about market share"
        "carries meaningful uncertainty",
        "carries uncertainty",
        "carries material uncertainty",  # "market share claims carry material uncertainty"
        "carry material uncertainty",
        "carry meaningful uncertainty",
        "material uncertainty",
    )
    return "market share" in text and any(marker in text for marker in missing_markers)


def _contains_source_list_prose(text: str) -> bool:
    lowered = text.lower()
    return (
        any(
            pattern in lowered
            for pattern in SOURCE_LIST_PROSE_PATTERNS
            if pattern not in {"evidence:", "source:", "from the frozen evidencepack"}
        )
        or bool(_AUDIT_LABEL_RE.search(text))
        or bool(_EVIDENCE_ID_RE.search(text))
        or bool(_SOURCE_NUMBER_RE.search(text))
    )


__all__ = [
    "MIN_CRITICAL_EVIDENCE_PER_COMPANY",
    "MIN_REQUIRED_EVIDENCE_PER_COMPANY",
    "MISSING_TEXT",
    "SOURCE_LIST_PROSE_PATTERNS",
    "check_report",
]
