from ci_engine import ontology


def test_normalize_dimension_exact_aliases():
    assert ontology.normalize_dimension("secret_detection") == "secrets_detection"
    assert ontology.normalize_dimension("M&A_strategy") == "mergers_acquisitions"
    assert ontology.normalize_dimension("pricing_and_packaging") == "pricing_packaging"
    assert ontology.normalize_dimension("product_architecture") == "architecture_deployment_model"
    assert ontology.normalize_dimension("financial_performance") == "funding_ownership"


def test_normalize_dimension_conditional_aliases():
    assert (
        ontology.normalize_dimension(
            "vulnerability_scanning",
            title="Contextual Analysis of CVEs",
            text="The analysis includes reachable paths and CVE context.",
        )
        == "cve_contextual_analysis"
    )
    assert (
        ontology.normalize_dimension(
            "dependency_scanning",
            title="GitLab static reachability",
            text="GitLab static reachability improves dependency scanning.",
        )
        == "reachability_analysis"
    )
    assert (
        ontology.normalize_dimension(
            "vulnerability_management",
            title="Duo vulnerability remediation",
            text="Duo can remediate vulnerabilities by opening a merge request.",
        )
        == "autofix_remediation"
    )
    assert (
        ontology.normalize_dimension(
            "supply_chain_security",
            title="Repository firewall",
            text="Repository firewall blocks malicious packages at download time.",
        )
        == "package_firewall"
    )
    assert (
        ontology.normalize_dimension(
            "vulnerability_management",
            title="Risk-Based Vulnerability Management (RBVM)",
            text="Risk-based vulnerability management prioritizes vulnerabilities.",
        )
        == "cve_contextual_analysis"
    )
    assert (
        ontology.normalize_dimension(
            "integrations",
            axis="technical",
            title="Snyk CI/CD integrations",
            text="Developer tools for CI/CD integrations.",
        )
        == "ci_cd_ide_integrations"
    )


def test_broad_product_aliases_use_specific_signals_before_fallback():
    assert (
        ontology.normalize_dimension(
            "product_capabilities",
            axis="technical",
            title="Static Application Security Testing (SAST) Scanning",
            text="Snyk explains static code analysis.",
        )
        == "static_analysis_sast"
    )
    assert (
        ontology.normalize_dimension(
            "product_capabilities",
            axis="business",
            title="Case Study: Sonatype Ensures App Quality",
            text="A customer case study.",
        )
        == "customers_case_studies"
    )
    assert (
        ontology.normalize_dimension(
            "security_compliance",
            axis="business",
            title="Risk Assessment",
            text="Trust and compliance posture.",
        )
        == "company_profile"
    )
    assert (
        ontology.normalize_dimension(
            "product_capabilities",
            axis="technical",
            title="General platform overview",
            text="The page mentions SBOMs as one of many capabilities.",
        )
        == "product_portfolio"
    )


def test_expand_dimension_aliases_includes_reverse_aliases():
    aliases = ontology.expand_dimension_aliases(["sbom_generation"])

    assert aliases is not None
    assert "sbom_generation" in aliases
    assert "sbom_support" in aliases
    assert "sbom_management" in aliases

    autofix_aliases = ontology.expand_dimension_aliases(["autofix_remediation"])
    assert autofix_aliases is not None
    assert "vulnerability_management" in autofix_aliases
    assert "application_security" in autofix_aliases
