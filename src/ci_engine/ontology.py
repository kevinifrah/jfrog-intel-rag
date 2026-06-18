from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from ci_engine.config import get as config_get


_EXACT_ALIASES = {
    "vulnerability_remediation": "autofix_remediation",
    "secret_detection": "secrets_detection",
    "secrets_management": "secrets_detection",
    "container_scanning": "container_image_scanning",
    "sbom_support": "sbom_generation",
    "sbom_management": "sbom_generation",
    "sbom_analysis": "sbom_generation",
    "sbom_export": "sbom_generation",
    "model_registry": "mlops_model_registry",
    "pricing": "pricing_packaging",
    "pricing_and_packaging": "pricing_packaging",
    "market_position": "market_positioning",
    "competitive_positioning": "market_positioning",
    "product_positioning": "market_positioning",
    "partnerships": "partnerships_ecosystem",
    "integrations": "partnerships_ecosystem",
    "funding": "funding_ownership",
    "funding_and_investors": "funding_ownership",
    "funding_and_valuation": "funding_ownership",
    "funding_valuation": "funding_ownership",
    "funding_investors": "funding_ownership",
    "funding_and_financials": "funding_ownership",
    "financial_performance": "funding_ownership",
    "investor_relations": "funding_ownership",
    "m_and_a": "mergers_acquisitions",
    "m&a": "mergers_acquisitions",
    "acquisitions": "mergers_acquisitions",
    "m&a_strategy": "mergers_acquisitions",
    "m_and_a_strategy": "mergers_acquisitions",
    "ma_strategy": "mergers_acquisitions",
    "m&a_and_partnerships": "mergers_acquisitions",
    "m&a_partnerships": "mergers_acquisitions",
    "acquisitions_partnerships": "mergers_acquisitions",
    "customer_base": "customers_case_studies",
    "customer_evidence": "customers_case_studies",
    "customer_segments": "target_segments_icp",
    "customers": "customers_case_studies",
    "go_to_market": "gtm_motion",
    "go_to_market_strategy": "gtm_motion",
    "leadership_strategy": "leadership_strategy_signals",
    "leadership_governance": "leadership_strategy_signals",
    "leadership_team": "leadership_strategy_signals",
    "leadership_and_organization": "leadership_strategy_signals",
    "company_leadership": "leadership_strategy_signals",
    "security_policy_enforcement": "policy_governance",
    "policy_enforcement": "policy_governance",
    "governance_and_policy": "policy_governance",
    "governance_and_compliance": "policy_governance",
    "governance_policy_management": "policy_governance",
    "policy_management": "policy_governance",
    "artifact_repository": "artifact_management",
    "container_registry": "artifact_management",
    "release_management": "release_lifecycle_management",
    "release_process": "release_lifecycle_management",
    "ci_cd_release_management": "release_lifecycle_management",
    "product_lifecycle": "release_lifecycle_management",
    "product_versioning_and_release_management": "release_lifecycle_management",
    "ci_cd_deployment": "release_lifecycle_management",
    "product_versioning_release_cadence": "release_cadence",
    "product_versioning": "release_cadence",
    "product_releases": "release_cadence",
    "product_roadmap": "release_cadence",
    "ai_capabilities": "ai_features",
    "ai_ml_capabilities": "ai_features",
    "ai_security_features": "ai_features",
    "ai_code_review": "ai_features",
    "ci_cd_capabilities": "ci_cd_ide_integrations",
    "ci_cd_integration": "ci_cd_ide_integrations",
    "ci_cd_platform": "ci_cd_ide_integrations",
    "ci_cd_platform_capabilities": "ci_cd_ide_integrations",
    "ci_cd_security": "ci_cd_ide_integrations",
    "ci_cd_runner_configuration": "ci_cd_ide_integrations",
    "security_scanning_integration": "ci_cd_ide_integrations",
    "devsecops_integration": "ci_cd_ide_integrations",
    "product_integrations": "ci_cd_ide_integrations",
    "product_integration": "ci_cd_ide_integrations",
    "infrastructure_as_code_security": "iac_security",
    "malware_detection": "malicious_package_detection",
    "malicious_code_detection": "malicious_package_detection",
    "open_source_security": "open_source_curation",
    "license_scanning": "license_compliance",
    "product_architecture": "architecture_deployment_model",
    "deployment_architecture": "architecture_deployment_model",
    "product_deployment_models": "architecture_deployment_model",
    "infrastructure_deployment": "architecture_deployment_model",
    "container_security": "container_image_scanning",
    "container_registry_scanning": "container_image_scanning",
    "sbom_capabilities": "sbom_generation",
    "security_research_capabilities": "security_research",
    "research_and_threat_intelligence": "security_research",
    "vulnerability_research": "security_research",
    "threat_intelligence": "security_research",
    "security_vulnerabilities": "cve_contextual_analysis",
    "vulnerability_database": "cve_contextual_analysis",
    "risk_prioritization": "cve_contextual_analysis",
    "authentication_access_control": "policy_governance",
    "access_control": "policy_governance",
    "ai_governance": "policy_governance",
    "compliance_automation": "policy_governance",
    "market_research": "market_positioning",
    "supply_chain_security_market_position": "market_positioning",
    "organizational_structure": "leadership_strategy_signals",
    "strategy_roadmap": "leadership_strategy_signals",
    "business_model": "company_profile",
    "value_proposition": "market_positioning",
    "product_features": "product_portfolio",
    "product_adoption_and_deployment": "architecture_deployment_model",
    "product_deployment": "architecture_deployment_model",
    "product_technical_requirements": "architecture_deployment_model",
    "security_model": "company_profile",
    "roi_and_business_value": "market_positioning",
}

_FILTER_ALIASES = {
    "autofix_remediation": {
        "ai_code_review",
        "application_security",
        "vulnerability_management",
        "vulnerability_remediation",
    },
    "container_image_scanning": {"container_scanning"},
    "mlops_model_registry": {"model_registry"},
    "reachability_analysis": {
        "dependency_scanning",
        "vulnerability_detection",
        "vulnerability_prioritization",
        "vulnerability_analysis",
        "vulnerability_management",
    },
    "software_composition_analysis": {
        "dependency_management",
        "dependency_scanning",
        "open_source_security",
    },
    "package_firewall": {
        "package_management",
        "supply_chain_security",
        "repository_firewall",
    },
    "sbom_generation": {"sbom_support", "sbom_management", "sbom_analysis", "sbom_export"},
}

_CONDITIONAL_ALIASES = {
    "ai_governance",
    "ai_supply_chain_security",
    "api_capabilities",
    "application_security",
    "application_security_testing",
    "compliance_and_governance",
    "data_handling_security",
    "dependency_scanning",
    "dependency_management",
    "integrations",
    "open_source_risk_management",
    "package_management",
    "product_capabilities",
    "product_capability",
    "product_comparison",
    "product_reliability",
    "product_roadmap",
    "product_security",
    "product_strategy",
    "product_troubleshooting",
    "secure_sdlc",
    "security_capabilities",
    "security_compliance",
    "security_governance",
    "security_hardening",
    "security_incident",
    "security_practices",
    "security_posture",
    "security_scanning",
    "security_threats",
    "security_vulnerability",
    "service_offerings",
    "software_supply_chain_security",
    "supply_chain_security",
    "supply_chain_security_compliance",
    "supply_chain_security_controls",
    "supply_chain_security_features",
    "supply_chain_security_scanning",
    "supply_chain_risk_detection",
    "supply_chain_risk_management",
    "supply_chain_security_market_position",
    "thought_leadership",
    "vulnerability_analysis",
    "vulnerability_detection",
    "vulnerability_management",
    "vulnerability_prioritization",
    "vulnerability_scanning",
}

_BROAD_CONDITIONAL_ALIASES = {
    "package_management",
    "product_capabilities",
    "product_capability",
    "supply_chain_security",
}


def canonical_dimensions(axis: str | None = None) -> set[str]:
    if axis is None:
        return {dimension for _, dimension in _ontology_dimensions()}
    return {
        dimension
        for row_axis, dimension in _ontology_dimensions()
        if row_axis == axis
    }


def axis_for_dimension(dimension: str | None) -> str | None:
    lookup = {dimension: axis for axis, dimension in _ontology_dimensions()}
    return lookup.get(str(dimension or "").strip())


def is_canonical_dimension(dimension: str | None) -> bool:
    return str(dimension or "").strip() in canonical_dimensions()


def normalize_dimension(
    dimension: str | None,
    *,
    axis: str | None = None,
    title: str | None = None,
    url: str | None = None,
    text: str | None = None,
) -> str | None:
    cleaned = _clean_dimension(dimension)
    if cleaned is None:
        return None
    if _axis_allows(cleaned, axis) and cleaned in canonical_dimensions():
        return cleaned

    key = _alias_key(cleaned)
    exact = _EXACT_ALIASES.get(key)
    if exact and _axis_allows(exact, axis):
        return exact

    conditional = _conditional_alias(key, axis=axis, title=title, url=url, text=text)
    if conditional and _axis_allows(conditional, axis):
        return conditional

    fallback = _axis_fallback(key, axis)
    if fallback:
        return fallback

    return cleaned


def expand_dimension_aliases(dimensions: list[str] | None) -> list[str] | None:
    if dimensions is None:
        return None

    expanded: list[str] = []
    for dimension in dimensions:
        cleaned = _clean_dimension(dimension)
        canonical = normalize_dimension(cleaned)
        for value in (cleaned, canonical):
            _append_unique(expanded, value)
        if canonical:
            for alias in aliases_for_dimension(canonical):
                _append_unique(expanded, alias)
    return expanded


def aliases_for_dimension(dimension: str) -> list[str]:
    canonical = normalize_dimension(dimension) or dimension
    aliases = {
        alias
        for alias, target in _EXACT_ALIASES.items()
        if target == canonical
    }
    aliases.update(_FILTER_ALIASES.get(canonical, set()))
    return sorted(aliases)


def _conditional_alias(
    key: str,
    *,
    axis: str | None,
    title: str | None,
    url: str | None,
    text: str | None,
) -> str | None:
    if key not in _CONDITIONAL_ALIASES:
        return None

    title_text = str(title or "").lower()
    title_url = " ".join(part for part in (title, url) if part).lower()
    evidence = " ".join(part for part in (title, url, text) if part).lower()
    if not evidence:
        return None

    match_text = title_url or evidence

    if re.search(r"\b(case study|customer story|customer success|customers?)\b", title_text):
        return "customers_case_studies"
    if re.search(r"\b(acquisition|acquires?|merger|m&a)\b", match_text):
        return "mergers_acquisitions"
    if re.search(r"\b(pricing|packaging|subscription|paid plan|free plan|enterprise plan|plans? and pricing)\b", match_text):
        return "pricing_packaging"
    if re.search(r"\b(earnings|revenue|arr|stock|investor|valuation|funding)\b", match_text):
        return "funding_ownership"
    if "market position" in match_text or "market research" in match_text:
        return "market_positioning"
    if "reachab" in title_url:
        return "reachability_analysis"
    if re.search(r"\b(cve|cvss|epss|contextual vulnerabilit|contextual analysis)\b", title_url):
        return "cve_contextual_analysis"
    if "sbom" in match_text or "software bill of materials" in match_text:
        return "sbom_generation"
    if re.search(r"\b(fix(?:es|ed|ing)?|remediat\w*|code fix|merge request|duo)\b", match_text):
        return "autofix_remediation"
    if re.search(r"\b(model registry|mlflow|mlops)\b", match_text):
        return "mlops_model_registry"
    if re.search(r"\b(ai model|ai/ml|machine learning model|ml model|model scan|model security)\b", match_text):
        return "ai_model_scanning"
    if re.search(r"\b(ai assistant|generative ai|ai-native|ai powered|ai-powered|agentic|copilot|duo)\b", match_text):
        return "ai_features"
    if re.search(r"\b(secret|token|credential)\b", match_text):
        return "secrets_detection"
    if re.search(r"\b(sast|static application security|static code analysis|taint analysis|source code analysis|code scanning)\b", match_text):
        return "static_analysis_sast"
    if "misconfigur" in match_text:
        return "services_misconfiguration"
    if re.search(r"\b(iac|infrastructure as code|terraform)\b", match_text):
        return "iac_security"
    if "runtime" in match_text:
        return "runtime_security"
    if re.search(r"\b(container|docker|image scanning|container image|image analysis)\b", match_text):
        return "container_image_scanning"
    if re.search(r"\b(package|dependency|repository)\s+firewall\b", match_text):
        return "package_firewall"
    if re.search(r"\b(block|quarantine|malicious packages?|malware|malwares|package[- ]hunter|supply chain attacks?|typosquat)\b", match_text):
        return "malicious_package_detection"
    if "license" in match_text:
        return "license_compliance"
    if re.search(r"\b(policy|policies|governance|compliance|access control|data handling)\b", match_text):
        return "policy_governance"
    if re.search(r"\b(cve|cvss|epss|exploit|risk score|prioriti[sz]\w*|vulnerability data|vulnerability lookup|vulnerability report|vulnerability severity|vulnerability management|vulnerability assessment)\b", match_text):
        return "cve_contextual_analysis"
    if "reachab" in match_text:
        return "reachability_analysis"
    if re.search(r"\b(security research|security labs?|advisor(?:y|ies)|threat intelligence|security threats?)\b", match_text):
        return "security_research"
    if re.search(r"\b(software composition analysis|sca|dependency scanning|open source scanning)\b", match_text):
        return "software_composition_analysis"
    if re.search(r"\b(operational risk|risk management|risk assessment|managing risk|software supply chain risks?)\b", match_text):
        return "operational_risk"
    if re.search(r"\b(curation|open source package|open source use|open source supply chain|software supply chain)\b", match_text):
        return "open_source_curation"
    if re.search(r"\b(artifact|artifactory|nexus repository|repository manager|package registr(?:y|ies))\b", match_text):
        return "artifact_management"
    if "distribution" in match_text:
        return "software_distribution"
    if re.search(r"\b(release notes?|what'?s new|version|sunsetting)\b", match_text):
        return "release_cadence"
    if re.search(r"\b(release lifecycle|release process|lifecycle management)\b", match_text):
        return "release_lifecycle_management"
    if re.search(r"\b(reference architecture|architecture|deployment|hybrid|run as a service)\b", match_text):
        return "architecture_deployment_model"
    if re.search(r"\b(ci/cd|cicd|pipeline|ide|developer tools?|api|supported language|package managers?|frameworks?)\b", match_text):
        return "ci_cd_ide_integrations"
    if re.search(r"\b(secure sdlc|security posture|security hardening|secure gitlab|shared responsibility)\b", match_text):
        return "policy_governance"
    if re.search(r"\b(digital operational resilience act|dora|regulation|resilience act)\b", match_text):
        return "policy_governance"
    if re.search(r"\b(comparison|compare| vs |versus|alternative)\b", match_text):
        return "market_positioning"
    if key in {"product_strategy", "thought_leadership"}:
        return "leadership_strategy_signals"
    if key in {"security_compliance", "security_governance"} and axis == "business":
        return "company_profile"
    if key == "service_offerings" and axis == "business":
        return "company_profile"
    if key in {"product_capabilities", "product_capability", "product_security", "application_security", "application_security_testing", "security_capabilities", "security_scanning"}:
        return "product_portfolio"
    if key in {"dependency_scanning", "vulnerability_scanning"}:
        return "software_composition_analysis"
    if key == "package_management":
        return "artifact_management"
    return None


def _axis_fallback(key: str, axis: str | None) -> str | None:
    if axis == "business":
        if key in {
            "business_model",
            "product_capabilities",
            "product_lifecycle",
            "security_compliance",
            "security_governance",
            "security_incident",
            "security_model",
            "service_offerings",
        }:
            return "company_profile"
        if key in {"product_roadmap", "product_strategy", "strategy_roadmap", "organizational_structure"}:
            return "leadership_strategy_signals"
        if key in {"roi_and_business_value", "value_proposition"}:
            return "market_positioning"
    return None


def _axis_allows(dimension: str, axis: str | None) -> bool:
    if axis in (None, "both"):
        return True
    dimension_axis = axis_for_dimension(dimension)
    return dimension_axis is None or dimension_axis == axis


def _clean_dimension(dimension: str | None) -> str | None:
    cleaned = str(dimension or "").strip()
    return cleaned or None


def _alias_key(dimension: str) -> str:
    key = dimension.strip().lower()
    key = key.replace("-", "_").replace(" ", "_").replace("/", "_")
    key = re.sub(r"__+", "_", key)
    return key


def _append_unique(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


@lru_cache(maxsize=1)
def _ontology_dimensions() -> tuple[tuple[str, str], ...]:
    ontology = config_get("ontology", {})
    dimensions: list[tuple[str, str]] = []
    if not isinstance(ontology, dict):
        return ()
    for axis in ("technical", "business"):
        for dimension in ontology.get(axis, []):
            dimensions.append((axis, str(dimension)))
    return tuple(dimensions)


__all__ = [
    "aliases_for_dimension",
    "axis_for_dimension",
    "canonical_dimensions",
    "expand_dimension_aliases",
    "is_canonical_dimension",
    "normalize_dimension",
]
