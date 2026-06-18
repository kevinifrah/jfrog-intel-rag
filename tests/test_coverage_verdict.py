import pytest

from ci_engine.synthesize import coverage_verdict


def test_verdict_parser_rejects_invalid_states():
    with pytest.raises(ValueError):
        coverage_verdict.normalize_verdict(
            {
                "state": "maybe",
                "confidence": 0.9,
                "evidence": "evidence",
                "reason": "reason",
            },
            candidate={"url": "https://example.com"},
            gap={"competitor": "GitLab"},
        )


def test_no_results_never_becomes_absent(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {},
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "package_firewall",
        },
    )

    assert verdict["state"] == "still_unknown"
    assert not coverage_verdict.should_ingest(verdict)


def test_official_explicit_unsupported_evidence_can_be_absent():
    verdict = coverage_verdict.classify_candidate(
        {
            "title": "Package firewall support",
            "url": "https://docs.gitlab.com/user/packages/package_firewall/",
            "text": "Package firewall is not currently supported.",
            "source_kind": "docs",
        },
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "package_firewall",
        },
    )

    assert verdict["state"] == "explicit_absent"
    assert coverage_verdict.should_ingest(verdict)
    assert coverage_verdict.evidence_state(verdict) == "absent"


def test_third_party_negative_evidence_needs_review():
    verdict = coverage_verdict.classify_candidate(
        {
            "title": "Capability matrix",
            "url": "https://example.com/gitlab-package-firewall",
            "text": "GitLab package firewall is not supported.",
            "source_kind": "blog",
        },
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "package_firewall",
        },
    )

    assert verdict["state"] == "needs_review"
    assert not coverage_verdict.should_ingest(verdict)


def test_partner_page_does_not_close_supported_ecosystems(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "JFrog Partner Ecosystem",
            "url": "https://jfrog.com/partners",
            "text": "Cloud alliances, consulting partners, and technology partners.",
            "source_kind": "vendor_site",
        },
        {
            "competitor": "JFrog",
            "axis": "technical",
            "dimension": "supported_ecosystems",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_official_package_formats_close_supported_ecosystems(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "Software Supply Chain Platform - JFrog",
            "url": "https://jfrog.com/platform",
            "text": (
                "With native support for 40+ package technology types including "
                "npm, Maven, PyPI, NuGet, and Docker, JFrog connects the "
                "development ecosystem."
            ),
            "source_kind": "vendor_site",
        },
        {
            "competitor": "JFrog",
            "axis": "technical",
            "dimension": "supported_ecosystems",
        },
    )

    assert verdict["state"] == "present"
    assert coverage_verdict.should_ingest(verdict)


def test_third_party_package_firewall_integration_is_irrelevant(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "Package Firewall - Endor Labs Documentation",
            "url": "https://docs.endorlabs.com/integrations/package-firewall",
            "text": (
                "JFrog Artifactory uses the Package Firewall as its remote source "
                "instead of upstream package registries."
            ),
            "source_kind": "docs",
        },
        {
            "competitor": "JFrog",
            "axis": "technical",
            "dimension": "package_firewall",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_jfrog_curation_can_close_package_firewall(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "Getting started with JFrog Curation",
            "url": "https://academy.jfrog.com/getting-started-with-jfrog-curation",
            "text": (
                "JFrog Curation controls which open-source packages enter the "
                "software supply chain before they reach developers. Curation "
                "policies allow, block, or flag open-source packages."
            ),
            "source_kind": "vendor_site",
        },
        {
            "competitor": "JFrog",
            "axis": "technical",
            "dimension": "package_firewall",
        },
    )

    assert verdict["state"] == "present"
    assert coverage_verdict.should_ingest(verdict)
    assert coverage_verdict.evidence_state(verdict) == "present"


def test_package_named_firewall_advisory_is_irrelevant(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "firewall | Snyk",
            "url": "https://security.snyk.io/package/npm/firewall",
            "text": (
                "# firewall Add or remove system firewall rules from Node.js. "
                "Licenses: MIT Published: 11 years ago Latest version: 0.0.5 "
                "Package Health Score 40/100."
            ),
            "source_kind": "vendor_site",
        },
        {
            "competitor": "Snyk",
            "axis": "technical",
            "dimension": "package_firewall",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_vendor_tool_rollout_does_not_close_software_distribution(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "Distribution at scale | Agent security | Snyk User Docs",
            "url": "https://docs.snyk.io/evo-by-snyk/agentic-security-with-snyk-studio/distribution-at-scale",
            "text": (
                "Snyk Studio integrates with Claude Code, Codex CLI, Cursor, "
                "and Gemini CLI. Distribution is managed via MDM tools such as "
                "Intune or Jamf, with IT administrators incorporating installer "
                "scripts into MDM playbooks."
            ),
            "source_kind": "docs",
        },
        {
            "competitor": "Snyk",
            "axis": "technical",
            "dimension": "software_distribution",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_vendor_platform_packaging_does_not_close_software_distribution(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "Distribution | The GitLab Handbook",
            "url": "https://handbook.gitlab.com/handbook/engineering/infrastructure-platforms/gitlab-delivery/distribution",
            "text": (
                "The Distribution team ensures the experience of installing and "
                "maintaining GitLab is easy and safe. Distribution:Build produces "
                "GitLab components, Omnibus packages, Helm Charts, Operators, and "
                "AWS Marketplace images for users' platforms."
            ),
            "source_kind": "vendor_site",
        },
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "software_distribution",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_ai_roi_dashboard_does_not_close_technical_impact_analysis(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "Developing GitLab Duo: AI Impact analytics dashboard measures the ROI of AI",
            "url": "https://about.gitlab.com/blog/developing-gitlab-duo-ai-impact-analytics-dashboard-measures-the-roi-of-ai",
            "text": (
                "The AI Impact analytics dashboard measures ROI of AI with Code "
                "Suggestions Usage Rate, Cycle Time, Lead Time, and Deployment "
                "Frequency."
            ),
            "source_kind": "blog",
        },
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "impact_analysis",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_internal_model_validation_does_not_close_ai_model_scanning(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "AI Model Validation at GitLab",
            "url": "https://handbook.gitlab.com/handbook/engineering/ai/ai-framework/model-validation/model_evaluation",
            "text": (
                "GitLab's AI Model Validation framework evaluates AI models for "
                "its AI-powered features, tracking quality, cost, legal approval, "
                "and response reliability."
            ),
            "source_kind": "vendor_site",
        },
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "ai_model_scanning",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_generic_ai_code_scanning_does_not_close_ai_model_scanning(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "AI-Powered Managed Security Services in GitLab",
            "url": "https://www.kineticskunk.io/ai-code-quality-security-gitlab",
            "text": (
                "GitLab AI scans code continuously, identifies potential "
                "vulnerabilities early, and offers AI code quality security."
            ),
            "source_kind": "blog",
        },
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "ai_model_scanning",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_third_party_edge_ci_example_does_not_close_edge_node_delivery(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "CI/CD at the Edge with K3s and GitLab",
            "url": "https://armkeil.blob.core.windows.net/developer/Files/pdf/white-paper/orchestrating-applications-at-the-edge.pdf",
            "text": (
                "Arm Project Cassini uses Rancher K3s and GitLab CI/CD. "
                "This is third-party documentation that references GitLab as a "
                "component in an edge deployment pipeline."
            ),
            "source_kind": "whitepaper",
        },
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "edge_node_delivery",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_vendor_delivery_stage_does_not_close_edge_node_delivery(monkeypatch):
    monkeypatch.setattr(
        coverage_verdict,
        "_llm_verdict",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    verdict = coverage_verdict.classify_candidate(
        {
            "title": "GitLab Delivery | The GitLab Handbook",
            "url": "https://handbook.gitlab.com/handbook/engineering/infrastructure-platforms/gitlab-delivery",
            "text": (
                "The GitLab Delivery Stage focuses on delivering GitLab across "
                "SaaS, Self-Managed, and Dedicated offerings."
            ),
            "source_kind": "vendor_site",
        },
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "edge_node_delivery",
        },
    )

    assert verdict["state"] == "irrelevant"
    assert not coverage_verdict.should_ingest(verdict)


def test_candidate_with_verdict_stamps_gap_context():
    candidate = {
        "title": "Package firewall proposal",
        "url": "https://gitlab.com/gitlab-org/gitlab/-/issues/package-firewall",
        "snippet": "Proposal for package firewall.",
    }
    gap = {
        "competitor": "GitLab",
        "axis": "technical",
        "dimension": "package_firewall",
    }
    verdict = {
        "state": "planned",
        "confidence": 0.78,
        "evidence": "Proposal for package firewall.",
        "reason": "roadmap evidence",
    }

    stamped = coverage_verdict.candidate_with_verdict(candidate, gap, verdict)

    assert stamped["axis"] == "technical"
    assert stamped["dimension"] == "package_firewall"
    assert stamped["evidence_state"] == "planned"
    assert stamped["coverage_gap"] == gap
    assert stamped["coverage_verdict"] == verdict
