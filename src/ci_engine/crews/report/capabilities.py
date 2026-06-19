from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ci_engine.crews.report.schemas import (
    CapabilityDefinition,
    CapabilityEvidenceCell,
    CapabilityEvidenceMatrix,
    CapabilityEvidenceRow,
    Confidence,
    EvidenceGap,
    EvidenceItem,
    ProductCatalogItem,
    TargetedSearchAttempt,
)

PRODUCT_FEATURE_SECTION_IDS = {
    "product_feature_analysis",
    "technical_teardown",
    "supply_chain_security",
    "executive_summary",
}


CAPABILITY_DEFINITIONS: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        id="artifact_repository",
        label="Artifact repository and package formats",
        dimension="artifact_management",
        must_resolve=True,
        search_terms=("artifact repository", "package formats", "binary management"),
    ),
    CapabilityDefinition(
        id="software_composition_analysis",
        label="Software composition analysis",
        dimension="software_composition_analysis",
        must_resolve=True,
        search_terms=("software composition analysis", "SCA", "open source vulnerabilities"),
    ),
    CapabilityDefinition(
        id="sbom_generation",
        label="SBOM generation and export",
        dimension="sbom_generation",
        must_resolve=True,
        search_terms=("SBOM", "generate SBOM", "CycloneDX", "SPDX", "export SBOM"),
    ),
    CapabilityDefinition(
        id="open_source_curation",
        label="Open source package curation",
        dimension="open_source_curation",
        must_resolve=True,
        search_terms=("package curation", "open source curation", "package approval"),
    ),
    CapabilityDefinition(
        id="package_firewall",
        label="Repository firewall and package admission",
        dimension="package_firewall",
        must_resolve=True,
        search_terms=("repository firewall", "package admission", "quarantine packages"),
    ),
    CapabilityDefinition(
        id="malicious_package_detection",
        label="Malicious package detection",
        dimension="malicious_package_detection",
        must_resolve=True,
        search_terms=("malicious package detection", "malware detection", "typosquatting"),
    ),
    CapabilityDefinition(
        id="policy_license_governance",
        label="Policy, license, and governance controls",
        dimension="policy_governance",
        must_resolve=True,
        search_terms=("policy governance", "license compliance", "open source policy"),
    ),
    CapabilityDefinition(
        id="reachability_analysis",
        label="Reachability analysis",
        dimension="reachability_analysis",
        must_resolve=True,
        search_terms=("reachability analysis", "reachable vulnerabilities", "reachable CVE"),
    ),
    CapabilityDefinition(
        id="cve_contextual_analysis",
        label="CVE contextual prioritization",
        dimension="cve_contextual_analysis",
        must_resolve=True,
        search_terms=("CVE contextual analysis", "vulnerability prioritization", "exploitability"),
    ),
    CapabilityDefinition(
        id="ci_cd_ide_integrations",
        label="CI/CD and IDE integrations",
        dimension="ci_cd_ide_integrations",
        search_terms=("CI/CD integrations", "IDE integrations", "developer workflow"),
    ),
    CapabilityDefinition(
        id="deployment_model",
        label="Architecture and deployment model",
        dimension="architecture_deployment_model",
        search_terms=("SaaS self hosted hybrid deployment", "architecture deployment model"),
    ),
    CapabilityDefinition(
        id="ai_mlop_security",
        label="AI, MLOps, and model/package governance",
        dimension="ai_features",
        search_terms=("AI security", "MLOps", "model governance", "AI packages"),
    ),
)


@dataclass(frozen=True)
class ProductAlias:
    company: str
    product_name: str
    category: str
    primary_role: str
    keywords: tuple[str, ...]
    capability_ids: tuple[str, ...]


PRODUCT_ALIASES: tuple[ProductAlias, ...] = (
    ProductAlias(
        "JFrog",
        "JFrog Artifactory",
        "Artifact Repository",
        "Universal artifact repository and binary system of record.",
        ("artifactory", "artifact repository"),
        ("artifact_repository", "deployment_model", "ci_cd_ide_integrations"),
    ),
    ProductAlias(
        "JFrog",
        "JFrog Xray",
        "Software Supply Chain Security",
        "SCA, vulnerability, license, and security analysis for artifacts and builds.",
        ("xray", "sca", "software composition analysis", "vulnerability"),
        (
            "software_composition_analysis",
            "sbom_generation",
            "malicious_package_detection",
            "policy_license_governance",
            "reachability_analysis",
            "cve_contextual_analysis",
        ),
    ),
    ProductAlias(
        "JFrog",
        "JFrog Curation",
        "Package Curation",
        "Pre-download package governance and open-source package approval controls.",
        ("curation", "package curation", "open source curation"),
        (
            "open_source_curation",
            "package_firewall",
            "malicious_package_detection",
            "policy_license_governance",
        ),
    ),
    ProductAlias(
        "JFrog",
        "JFrog Advanced Security",
        "Advanced Security",
        "Contextual and advanced security analysis across software artifacts.",
        ("advanced security", "contextual analysis", "reachability"),
        (
            "malicious_package_detection",
            "reachability_analysis",
            "cve_contextual_analysis",
        ),
    ),
    ProductAlias(
        "JFrog",
        "JFrog Catalog",
        "Software Catalog",
        "Catalog and governance visibility for software assets and services.",
        ("jfrog catalog", "catalog"),
        ("deployment_model", "ai_mlop_security"),
    ),
    ProductAlias(
        "Sonatype",
        "Nexus Repository",
        "Artifact Repository",
        "Repository manager for open-source and internal package components.",
        ("nexus repository", "repository manager"),
        ("artifact_repository", "deployment_model", "ci_cd_ide_integrations"),
    ),
    ProductAlias(
        "Sonatype",
        "Sonatype Lifecycle",
        "Software Composition Analysis",
        "SCA, policy, license, and open-source governance controls.",
        ("sonatype lifecycle", "nexus lifecycle", "lifecycle"),
        (
            "software_composition_analysis",
            "sbom_generation",
            "policy_license_governance",
            "reachability_analysis",
            "cve_contextual_analysis",
            "ci_cd_ide_integrations",
        ),
    ),
    ProductAlias(
        "Sonatype",
        "Sonatype Repository Firewall",
        "Repository Firewall",
        "Package admission and quarantine control point for open-source components.",
        ("repository firewall", "nexus firewall", "firewall"),
        (
            "open_source_curation",
            "package_firewall",
            "malicious_package_detection",
            "policy_license_governance",
        ),
    ),
    ProductAlias(
        "Sonatype",
        "Sonatype SBOM Manager",
        "SBOM Management",
        "SBOM ingestion, management, and governance workflows.",
        ("sbom manager", "sbom management", "sbom"),
        ("sbom_generation", "policy_license_governance"),
    ),
    ProductAlias(
        "Snyk",
        "Snyk Open Source",
        "Software Composition Analysis",
        "Open-source dependency vulnerability and license analysis.",
        ("snyk open source", "open source"),
        (
            "software_composition_analysis",
            "sbom_generation",
            "policy_license_governance",
            "reachability_analysis",
            "cve_contextual_analysis",
        ),
    ),
    ProductAlias(
        "Snyk",
        "Snyk Container",
        "Container Security",
        "Container image vulnerability analysis and remediation guidance.",
        ("snyk container", "container"),
        ("malicious_package_detection", "cve_contextual_analysis"),
    ),
    ProductAlias(
        "GitLab",
        "GitLab Ultimate",
        "DevSecOps Platform",
        "Integrated DevSecOps platform features for source, CI/CD, and security.",
        ("gitlab ultimate", "gitlab platform", "devsecops"),
        (
            "artifact_repository",
            "software_composition_analysis",
            "sbom_generation",
            "policy_license_governance",
            "ci_cd_ide_integrations",
            "deployment_model",
        ),
    ),
)


def capability_query_plan(
    company: str,
    *,
    competitor: str,
    focus: str | None = None,
    capabilities: Sequence[CapabilityDefinition] = CAPABILITY_DEFINITIONS,
) -> list[tuple[CapabilityDefinition, str]]:
    product_hint = " ".join(_alias_names_for_company(company)[:3])
    focus_text = f" {focus}" if focus else ""
    queries: list[tuple[CapabilityDefinition, str]] = []
    for capability in capabilities:
        terms = " ".join(capability.search_terms)
        query = (
            f"{company} {product_hint} {terms}{focus_text} "
            f"product documentation capabilities"
        )
        if competitor.lower() not in {company.lower(), "jfrog"}:
            query = f"{query} {competitor}"
        queries.append((capability, " ".join(query.split())))
    return queries


def build_capability_artifacts(
    competitor: str,
    *,
    items: Sequence[EvidenceItem],
    attempts: Sequence[TargetedSearchAttempt],
    jfrog: str = "JFrog",
) -> tuple[CapabilityEvidenceMatrix, tuple[ProductCatalogItem, ...], tuple[EvidenceGap, ...]]:
    companies = (jfrog, competitor)
    rows: list[CapabilityEvidenceRow] = []
    gaps: list[EvidenceGap] = []
    for capability in CAPABILITY_DEFINITIONS:
        cells = {
            company: _build_cell(
                company,
                capability=capability,
                items=_capability_items(items, company=company, capability=capability),
                attempts=_capability_attempts(
                    attempts,
                    company=company,
                    capability_id=capability.id,
                ),
            )
            for company in companies
        }
        row_evidence_ids = _unique(
            [
                *cells[jfrog].evidence_ids,
                *cells[competitor].evidence_ids,
            ]
        )
        readout = _capability_readout(cells[jfrog], cells[competitor])
        search_status = _row_status(cells[jfrog], cells[competitor])
        row = CapabilityEvidenceRow(
            capability_id=capability.id,
            capability_label=capability.label,
            dimension=capability.dimension,
            must_resolve=capability.must_resolve,
            jfrog=cells[jfrog],
            competitor=cells[competitor],
            readout=readout,
            confidence=_row_confidence(cells[jfrog], cells[competitor]),
            evidence_ids=tuple(row_evidence_ids),
            search_status=search_status,
        )
        rows.append(row)
        if capability.must_resolve and search_status in {
            "not_found_after_search",
            "unclear_needs_review",
            "contradictory",
        }:
            gaps.append(
                EvidenceGap(
                    company=f"{jfrog}/{competitor}",
                    report_section="product_feature_analysis",
                    axis="technical",
                    dimension=capability.dimension,
                    reason=search_status,
                    detail=(
                        f"{capability.label}: targeted DB and web search did not produce "
                        "enough comparable evidence for both vendors."
                    ),
                )
            )

    matrix = CapabilityEvidenceMatrix(
        competitor=competitor,
        capabilities=CAPABILITY_DEFINITIONS,
        rows=tuple(rows),
        search_attempts=tuple(attempts),
    )
    catalog = _build_product_catalog(companies, items=items)
    return matrix, catalog, tuple(gaps)


def capability_evidence_ids(matrix: CapabilityEvidenceMatrix) -> tuple[str, ...]:
    return tuple(_unique(evidence_id for row in matrix.rows for evidence_id in row.evidence_ids))


def _build_cell(
    company: str,
    *,
    capability: CapabilityDefinition,
    items: Sequence[EvidenceItem],
    attempts: Sequence[TargetedSearchAttempt],
) -> CapabilityEvidenceCell:
    product_names = _product_names(company, items)
    status = _cell_status(items, attempts)
    confidence = _cell_confidence(items, status)
    statement = _cell_statement(
        company,
        capability=capability,
        products=product_names,
        status=status,
    )
    return CapabilityEvidenceCell(
        company=company,
        product_names=product_names,
        capability_statement=statement,
        status=status,
        confidence=confidence,
        evidence_ids=tuple(item.id for item in items[:5]),
        search_attempts=tuple(attempts),
    )


def _capability_items(
    items: Sequence[EvidenceItem],
    *,
    company: str,
    capability: CapabilityDefinition,
) -> list[EvidenceItem]:
    matches: list[EvidenceItem] = []
    for item in items:
        if item.company.lower() != company.lower():
            continue
        if item.report_section not in PRODUCT_FEATURE_SECTION_IDS:
            continue
        metadata_capability = str(item.metadata.get("capability_id") or "")
        if metadata_capability == capability.id:
            matches.append(item)
            continue
        if item.dimension == capability.dimension:
            matches.append(item)
            continue
        text = _evidence_text(item)
        if any(term.lower() in text for term in capability.search_terms):
            matches.append(item)
    return _dedupe_items(matches)


def _capability_attempts(
    attempts: Sequence[TargetedSearchAttempt],
    *,
    company: str,
    capability_id: str,
) -> list[TargetedSearchAttempt]:
    return [
        attempt
        for attempt in attempts
        if attempt.company.lower() == company.lower()
        and attempt.capability_id == capability_id
    ]


def _cell_status(
    items: Sequence[EvidenceItem],
    attempts: Sequence[TargetedSearchAttempt],
) -> str:
    if len(items) >= 2 and any(item.tier == "primary" for item in items):
        return "supported"
    if items:
        return "partially_supported"
    if attempts:
        return "not_found_after_search"
    return "unclear_needs_review"


def _cell_confidence(items: Sequence[EvidenceItem], status: str) -> Confidence:
    if status == "supported":
        if any(_official_first(item) for item in items):
            return "high"
        return "medium"
    if status == "partially_supported":
        return "medium"
    if status == "not_found_after_search":
        return "low"
    return "unknown"


def _cell_statement(
    company: str,
    *,
    capability: CapabilityDefinition,
    products: tuple[str, ...],
    status: str,
) -> str:
    product_text = ", ".join(products[:3]) if products else f"{company} product evidence"
    if status == "supported":
        return f"{product_text} supports {capability.label.lower()}."
    if status == "partially_supported":
        return f"{product_text} indicates {capability.label.lower()}, with incomplete product detail."
    if status == "not_found_after_search":
        return "no recent data found after targeted search."
    return "unclear; targeted search still needed."


def _capability_readout(
    jfrog: CapabilityEvidenceCell,
    competitor: CapabilityEvidenceCell,
) -> str:
    unresolved = {"contradictory", "unclear_needs_review", "not_found_after_search"}
    if jfrog.status in unresolved or competitor.status in unresolved:
        return "unclear"
    jfrog_score = _status_score(jfrog.status)
    competitor_score = _status_score(competitor.status)
    if jfrog_score == 0 and competitor_score == 0:
        return "unclear"
    if jfrog_score > competitor_score:
        return "jfrog_advantage"
    if competitor_score > jfrog_score:
        return "competitor_advantage"
    if jfrog_score >= 2 and competitor_score >= 2:
        return "parity"
    return "unclear"


def _row_status(
    jfrog: CapabilityEvidenceCell,
    competitor: CapabilityEvidenceCell,
) -> str:
    statuses = {jfrog.status, competitor.status}
    if "contradictory" in statuses:
        return "contradictory"
    if "unclear_needs_review" in statuses:
        return "unclear_needs_review"
    if "not_found_after_search" in statuses:
        if statuses == {"not_found_after_search"}:
            return "not_found_after_search"
        return "unclear_needs_review"
    if "partially_supported" in statuses:
        return "partially_supported"
    return "supported"


def _row_confidence(
    jfrog: CapabilityEvidenceCell,
    competitor: CapabilityEvidenceCell,
) -> Confidence:
    order = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    value = min(order[jfrog.confidence], order[competitor.confidence])
    for label, score in order.items():
        if score == value:
            return label  # type: ignore[return-value]
    return "unknown"


def _status_score(status: str) -> int:
    return {
        "supported": 3,
        "partially_supported": 2,
        "unclear_needs_review": 1,
        "not_found_after_search": 0,
        "contradictory": 0,
    }.get(status, 0)


def _build_product_catalog(
    companies: Sequence[str],
    *,
    items: Sequence[EvidenceItem],
) -> tuple[ProductCatalogItem, ...]:
    catalog: list[ProductCatalogItem] = []
    for company in companies:
        company_items = [
            item
            for item in items
            if item.company.lower() == company.lower()
            and item.report_section in PRODUCT_FEATURE_SECTION_IDS
        ]
        aliases = _aliases_for_company(company)
        matched_any = False
        for alias in aliases:
            alias_items = [
                item
                for item in company_items
                if any(keyword in _evidence_text(item) for keyword in alias.keywords)
            ]
            if not alias_items:
                continue
            matched_any = True
            catalog.append(
                ProductCatalogItem(
                    company=company,
                    product_name=alias.product_name,
                    category=alias.category,
                    primary_role=alias.primary_role,
                    capabilities=tuple(
                        _capability_labels_for_alias(alias)
                    ),
                    evidence_ids=tuple(item.id for item in alias_items[:5]),
                    confidence=_catalog_confidence(alias_items),
                )
            )
        if company_items and not matched_any:
            catalog.append(
                ProductCatalogItem(
                    company=company,
                    product_name=f"{company} platform",
                    category="Platform",
                    primary_role="Product platform referenced by collected evidence.",
                    capabilities=tuple(
                        _unique(
                            capability.label
                            for capability in CAPABILITY_DEFINITIONS
                            if _capability_items(
                                company_items,
                                company=company,
                                capability=capability,
                            )
                        )
                    ),
                    evidence_ids=tuple(item.id for item in company_items[:5]),
                    confidence=_catalog_confidence(company_items),
                )
            )
    return tuple(catalog)


def _capability_labels_for_alias(alias: ProductAlias) -> list[str]:
    labels_by_id = {capability.id: capability.label for capability in CAPABILITY_DEFINITIONS}
    return [
        labels_by_id[capability_id]
        for capability_id in alias.capability_ids
        if capability_id in labels_by_id
    ]


def _catalog_confidence(items: Sequence[EvidenceItem]) -> Confidence:
    if any(item.confidence == "high" for item in items) and any(_official_first(item) for item in items):
        return "high"
    if items:
        return "medium"
    return "unknown"


def _product_names(company: str, items: Sequence[EvidenceItem]) -> tuple[str, ...]:
    names: list[str] = []
    for alias in _aliases_for_company(company):
        if any(any(keyword in _evidence_text(item) for keyword in alias.keywords) for item in items):
            names.append(alias.product_name)
    return tuple(_unique(names))


def _alias_names_for_company(company: str) -> list[str]:
    return [alias.product_name for alias in _aliases_for_company(company)]


def _aliases_for_company(company: str) -> tuple[ProductAlias, ...]:
    return tuple(
        alias for alias in PRODUCT_ALIASES if alias.company.lower() == company.lower()
    )


def _official_first(item: EvidenceItem) -> bool:
    source_kind = str(item.metadata.get("source_kind") or "").lower()
    publisher = (item.publisher or "").lower()
    company = item.company.lower()
    return (
        item.tier == "primary"
        or source_kind in {"docs", "pricing", "security_advisories", "vendor_site", "official"}
        or company.replace(" ", "") in publisher.replace("-", "").replace(".", "")
    )


def _evidence_text(item: EvidenceItem) -> str:
    return " ".join(
        part
        for part in (
            item.title,
            item.summary,
            item.quote,
            item.url,
            item.dimension,
            str(item.metadata.get("source_kind") or ""),
        )
        if part
    ).lower()


def _dedupe_items(items: Sequence[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[str] = set()
    deduped: list[EvidenceItem] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        deduped.append(item)
    return deduped


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


__all__ = [
    "CAPABILITY_DEFINITIONS",
    "PRODUCT_FEATURE_SECTION_IDS",
    "build_capability_artifacts",
    "capability_evidence_ids",
    "capability_query_plan",
]
