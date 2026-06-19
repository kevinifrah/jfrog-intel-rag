from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportSectionSpec:
    id: str
    title: str
    axis: str | None
    dimensions: tuple[str, ...]
    queries: tuple[str, ...]
    required: bool = True
    critical: bool = False


REPORT_SECTION_SPECS: tuple[ReportSectionSpec, ...] = (
    ReportSectionSpec(
        id="executive_summary",
        title="Executive Summary",
        axis=None,
        dimensions=(
            "company_profile",
            "market_positioning",
            "product_portfolio",
            "software_composition_analysis",
            "artifact_management",
        ),
        queries=(
            "{company} strategic position software supply chain security",
            "{company} product platform competitive differentiation",
        ),
        critical=True,
    ),
    ReportSectionSpec(
        id="company_snapshot",
        title="Company Snapshot",
        axis="business",
        dimensions=(
            "company_profile",
            "funding_ownership",
            "customers_case_studies",
            "leadership_strategy_signals",
        ),
        queries=(
            "{company} company profile customers leadership ownership",
            "{company} public company facts business model",
        ),
    ),
    ReportSectionSpec(
        id="market_context",
        title="Market And Strategic Context",
        axis="business",
        dimensions=(
            "market_positioning",
            "target_segments_icp",
            "analyst_positioning",
            "gtm_motion",
            "win_loss_signals",
        ),
        queries=(
            "{company} market positioning target buyers supply chain security",
            "{company} analyst positioning competitive alternatives",
        ),
        critical=True,
    ),
    ReportSectionSpec(
        id="product_feature_analysis",
        title="Product And Feature Analysis",
        axis="technical",
        dimensions=(
            "product_portfolio",
            "artifact_management",
            "software_composition_analysis",
            "sbom_generation",
            "open_source_curation",
            "package_firewall",
            "policy_governance",
            "license_compliance",
            "ci_cd_ide_integrations",
            "ai_features",
            "architecture_deployment_model",
            "malicious_package_detection",
            "cve_contextual_analysis",
            "reachability_analysis",
        ),
        queries=(
            "{company} product features artifact management SCA SBOM package firewall curation policy integrations AI",
            "{company} feature comparison Artifactory Xray Nexus Lifecycle Repository Firewall",
        ),
        critical=True,
    ),
    ReportSectionSpec(
        id="technical_teardown",
        title="Technical And Feature Teardown",
        axis="technical",
        dimensions=(
            "product_portfolio",
            "software_composition_analysis",
            "cve_contextual_analysis",
            "reachability_analysis",
            "package_firewall",
            "open_source_curation",
            "sbom_generation",
            "artifact_management",
            "architecture_deployment_model",
            "ci_cd_ide_integrations",
            "ai_features",
        ),
        queries=(
            "{company} technical architecture product capabilities SCA SBOM",
            "{company} package firewall artifact management integrations AI",
        ),
        critical=True,
    ),
    ReportSectionSpec(
        id="supply_chain_security",
        title="Supply Chain Security Coverage",
        axis="technical",
        dimensions=(
            "malicious_package_detection",
            "open_source_curation",
            "package_firewall",
            "security_research",
            "policy_governance",
            "license_compliance",
        ),
        queries=(
            "{company} software supply chain security malicious packages policy",
            "{company} curation firewall license governance security research",
        ),
    ),
    ReportSectionSpec(
        id="buyer_fit",
        title="Buyer Fit Matrix",
        axis="business",
        dimensions=(
            "target_segments_icp",
            "pricing_packaging",
            "customers_case_studies",
            "gtm_motion",
            "partnerships_ecosystem",
        ),
        queries=(
            "{company} buyer fit enterprise pricing customers partners",
            "{company} customer case studies target segments packaging",
        ),
    ),
    ReportSectionSpec(
        id="scoring",
        title="Weighted Buyer Scorecards",
        axis=None,
        dimensions=(
            "market_positioning",
            "product_portfolio",
            "artifact_management",
            "software_composition_analysis",
            "sbom_generation",
            "policy_governance",
        ),
        queries=(
            "{company} buyer scorecard platform security governance evidence",
            "{company} platform depth security breadth evidence",
        ),
    ),
    ReportSectionSpec(
        id="field_battlecard",
        title="JFrog Field Battlecard",
        axis="business",
        dimensions=(
            "market_positioning",
            "win_loss_signals",
            "customers_case_studies",
            "gtm_motion",
        ),
        queries=(
            "{company} win loss signals objections competitive positioning",
            "{company} field battlecard buyer objections discovery questions",
        ),
    ),
)


def section_specs(section_ids: list[str] | None = None) -> tuple[ReportSectionSpec, ...]:
    if section_ids is None:
        return REPORT_SECTION_SPECS
    requested = {section_id.strip() for section_id in section_ids if section_id.strip()}
    return tuple(spec for spec in REPORT_SECTION_SPECS if spec.id in requested)


def critical_section_ids() -> set[str]:
    return {spec.id for spec in REPORT_SECTION_SPECS if spec.critical}


__all__ = ["REPORT_SECTION_SPECS", "ReportSectionSpec", "critical_section_ids", "section_specs"]
