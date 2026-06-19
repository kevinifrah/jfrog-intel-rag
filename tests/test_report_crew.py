from __future__ import annotations

from datetime import date, datetime, timezone
import json

from ci_engine.crews.report.checker import check_report
from ci_engine.crews.report.capabilities import (
    CAPABILITY_DEFINITIONS,
    build_capability_artifacts,
)
from ci_engine.crews.report.crew import REPORT_AGENT_SKILLS, load_agent_skill
from ci_engine.crews.report.buyer_field import (
    BuyerFieldGenerationError,
    build_buyer_field_prompt_input,
    parse_buyer_field_analysis,
)
from ci_engine.crews.report.evidence import build_evidence_pack_for_competitor, tavily_topics
from ci_engine.crews.report.renderer import render_html
from ci_engine.crews.report.market import (
    MarketGenerationError,
    build_market_prompt_input,
    parse_market_analysis,
)
from ci_engine.crews.report.product_feature import (
    ProductFeatureGenerationError,
    build_product_feature_prompt_input,
    parse_product_feature_analysis,
)
from ci_engine.crews.report.scoring import (
    ScoringGenerationError,
    build_scoring_prompt_input,
    parse_scoring_analysis,
)
from ci_engine.crews.report.schemas import (
    EvidenceItem,
    EvidencePack,
    ReportClaim,
    ReportDraft,
    ReportSection,
    TargetedSearchAttempt,
)
from ci_engine.crews.report.sections import section_specs
from ci_engine.crews.report.strategy import (
    StrategyGenerationError,
    build_strategy_prompt_input,
    parse_strategy_analysis,
)
from ci_engine.crews.report.technical import (
    TechnicalGenerationError,
    build_technical_prompt_input,
    parse_technical_analysis,
)
from ci_engine.crews.report.workflow import build_report_draft, generate_report
from ci_engine.skills import load_skill


class FakeMcpClient:
    def __init__(self):
        self.calls = []
        self.source_id = 100

    def search(self, query, axis=None, competitors=None, dimensions=None):
        self.calls.append(
            {
                "query": query,
                "axis": axis,
                "competitors": competitors,
                "dimensions": dimensions,
            }
        )
        company = "JFrog" if "JFrog" in query else "Sonatype"
        self.source_id += 1
        dimension = dimensions[0] if dimensions else "company_profile"
        source_slug = f"{dimension}-{self.source_id}"
        return {
            "chunks": [
                {
                    "chunk_id": self.source_id + 1000,
                    "source_id": self.source_id,
                    "chunk_text": f"{company} evidence for {dimension} with specific sourced detail.",
                    "url": f"https://example.com/{company.lower().replace(' ', '-')}/{source_slug}",
                    "title": f"{company} {source_slug}",
                    "publish_date": date(2026, 1, 2),
                    "fetched_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
                    "axis": axis,
                    "dimension": dimension,
                    "doc_type": "docs",
                    "competitor": company,
                    "source_kind": "official",
                    "raw_path": f"raw/{company}/{dimension}.md",
                    "similarity": 0.82,
                    "citations": [{"url": f"https://example.com/{company}/{dimension}"}],
                }
            ],
            "missing": [],
        }

    def coverage_status(self):
        return {"coverage": [], "missing": []}

    def source_inventory(self, competitors=None, dimensions=None, limit=None):
        sources = []
        source_id = 100
        for company in competitors or ["JFrog", "Sonatype"]:
            for dimension in dimensions or ["company_profile"]:
                source_id += 1
                sources.append(
                    {
                        "source_id": source_id,
                        "competitor": company,
                        "axis": "technical",
                        "dimension": dimension,
                        "doc_type": "docs",
                        "source_kind": "docs",
                        "url": f"https://example.com/{company.lower().replace(' ', '-')}/{dimension}/{source_id}",
                        "title": f"{company} {dimension}",
                        "publish_date": date(2026, 1, 2),
                        "fetched_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
                        "raw_path": f"raw/{company}/{dimension}.md",
                        "chunk_count": 2,
                        "citation_count": 1,
                    }
                )
        return {"sources": sources}


class BatchCapabilityMcpClient(FakeMcpClient):
    def __init__(self):
        super().__init__()
        self.batch_calls = []

    def build_capability_evidence_matrix(
        self,
        competitor,
        focus=None,
        max_chunks_per_company_capability=4,
    ):
        self.batch_calls.append(
            {
                "competitor": competitor,
                "focus": focus,
                "max_chunks_per_company_capability": max_chunks_per_company_capability,
            }
        )
        items = []
        attempts = []
        for company in ("JFrog", competitor):
            product_hint = "JFrog Xray" if company == "JFrog" else "Sonatype Lifecycle"
            for index, capability in enumerate(CAPABILITY_DEFINITIONS):
                item = EvidenceItem(
                    id=f"batch-{company.lower()}-{capability.id}",
                    source="db",
                    tier="primary",
                    company=company,
                    report_section="product_feature_analysis",
                    url=(
                        "https://example.com/"
                        f"{company.lower().replace(' ', '-')}/{capability.id}"
                    ),
                    title=f"{company} {capability.label}",
                    retrieved_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                    published=date(2026, 1, 2),
                    quote=(
                        f"{product_hint} product documentation describes "
                        f"{capability.label} for competitive analysis."
                    ),
                    summary=f"{company} evidence for {capability.label}.",
                    axis="technical",
                    dimension=capability.dimension,
                    confidence="high",
                    source_id=5000 + index,
                    chunk_id=6000 + index,
                    metadata={
                        "capability_id": capability.id,
                        "capability_label": capability.label,
                        "source_kind": "docs",
                        "batch_marker": True,
                    },
                )
                items.append(item)
                attempts.append(
                    TargetedSearchAttempt(
                        company=company,
                        capability_id=capability.id,
                        capability_label=capability.label,
                        source="db",
                        query=(
                            f"{company} {capability.label} "
                            "product documentation capabilities"
                        ),
                        result_count=1,
                        status="supported",
                    )
                )
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "gaps": [],
            "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
        }


class BatchSectionAndCapabilityMcpClient(BatchCapabilityMcpClient):
    def __init__(self):
        super().__init__()
        self.section_batch_calls = []

    def build_report_section_evidence(
        self,
        competitor,
        focus=None,
        sections=None,
        max_chunks_per_company_section=8,
    ):
        self.section_batch_calls.append(
            {
                "competitor": competitor,
                "focus": focus,
                "sections": sections,
                "max_chunks_per_company_section": max_chunks_per_company_section,
            }
        )
        items = []
        coverage = []
        for spec in section_specs(sections):
            for company in ("JFrog", competitor):
                item = EvidenceItem(
                    id=f"section-{company.lower()}-{spec.id}",
                    source="db",
                    tier="primary",
                    company=company,
                    report_section=spec.id,
                    url=(
                        "https://example.com/"
                        f"{company.lower().replace(' ', '-')}/{spec.id}"
                    ),
                    title=f"{company} {spec.title}",
                    retrieved_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                    published=date(2026, 1, 2),
                    quote=(
                        f"{company} sourced evidence for {spec.title} with "
                        "specific competitive intelligence detail."
                    ),
                    summary=f"{company} evidence for {spec.title}.",
                    axis=spec.axis,
                    dimension=spec.dimensions[0] if spec.dimensions else None,
                    confidence="high",
                    metadata={
                        "retrieval_mode": "mcp_batch_section",
                        "source_kind": "docs",
                    },
                )
                items.append(item)
                coverage.append(
                    {
                        "company": company,
                        "section_id": spec.id,
                        "axis": spec.axis,
                        "requested_dimensions": list(spec.dimensions),
                        "dimensions_with_evidence": [spec.dimensions[0]]
                        if spec.dimensions
                        else [],
                        "missing_dimensions": list(spec.dimensions[1:]),
                        "candidate_count": 1,
                        "result_count": 1,
                        "status": "supported",
                    }
                )
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "gaps": [],
            "coverage": coverage,
        }


class NoGlobalCoverageMcpClient(FakeMcpClient):
    def coverage_status(self):  # pragma: no cover - test fails if called.
        raise AssertionError("report evidence pack should not attach global coverage gaps")


def fake_tavily_search(company, topics=None, max_results=3):
    topic = (topics or ["market"])[0]
    return [
        {
            "title": f"{company} fresh validation",
            "url": f"https://news.example.com/{company.lower().replace(' ', '-')}",
            "snippet": f"{company} public validation result for {topic}.",
            "text": f"{company} public validation result for {topic}.",
            "competitor": company,
            "published": date(2026, 2, 4),
            "source_kind": "news",
            "source_reason": "mocked tavily result",
        }
    ]


def fake_strategy_runner(prompt: str):
    payload = json.loads(prompt.split("PAYLOAD_JSON:\n", 1)[1])
    evidence_ids = payload["allowed_evidence_ids"]
    first = evidence_ids[0]
    second = evidence_ids[1] if len(evidence_ids) > 1 else first
    third = evidence_ids[2] if len(evidence_ids) > 2 else first
    return {
        "executive_thesis": {
            "text": "JFrog should frame the comparison around platform breadth while respecting Sonatype's security depth.",
            "evidence_ids": [first, second],
            "confidence": "high",
        },
        "jfrog_advantages": [
            {
                "text": "JFrog has a credible platform-breadth story when buyers value unified software delivery and security workflows.",
                "evidence_ids": [first],
                "confidence": "high",
            }
        ],
        "competitor_strengths": [
            {
                "text": "Sonatype remains credible where buyers prioritize software composition analysis and supply-chain security proof.",
                "evidence_ids": [second],
                "confidence": "medium",
            }
        ],
        "risks": [
            {
                "text": "JFrog should avoid overstating unsupported market outcomes and keep the narrative tied to validated product evidence.",
                "evidence_ids": [third],
                "confidence": "medium",
            }
        ],
        "likely_next_moves": [
            {
                "text": "Sonatype is likely to keep emphasizing security research, SCA proof points, and enterprise governance.",
                "evidence_ids": [second],
                "confidence": "medium",
            }
        ],
        "recommended_actions": [
            {
                "text": "Lead with JFrog's integrated platform story, then validate security claims with specific cited evidence.",
                "evidence_ids": [first, third],
                "confidence": "high",
            }
        ],
        "confidence_notes": [
            "Confidence is highest where primary DB evidence and Tavily validation overlap."
        ],
        "metadata": {"test": True},
    }


def fake_market_runner(prompt: str):
    payload = json.loads(prompt.split("PAYLOAD_JSON:\n", 1)[1])
    evidence_ids = payload["allowed_evidence_ids"]
    first = evidence_ids[0]
    second = evidence_ids[1] if len(evidence_ids) > 1 else first
    third = evidence_ids[2] if len(evidence_ids) > 2 else first
    fourth = evidence_ids[3] if len(evidence_ids) > 3 else first
    return {
        "company_snapshot_thesis": {
            "text": "JFrog and Sonatype address the same supply-chain security buyer, but JFrog leads from platform consolidation while Sonatype leads from open-source governance specialization.",
            "evidence_ids": [first, second],
            "confidence": "high",
        },
        "jfrog_company_position": [
            {
                "text": "JFrog has a broad market posture when buyers want artifact management, security, and release governance in one operating layer.",
                "evidence_ids": [first],
                "confidence": "high",
            }
        ],
        "competitor_company_position": [
            {
                "text": "Sonatype remains credible in security-led evaluations where open-source governance and repository controls define the buying criteria.",
                "evidence_ids": [second],
                "confidence": "medium",
            }
        ],
        "market_context_thesis": {
            "text": "The market context favors vendors that can translate supply-chain risk into developer workflow control without forcing buyers into too many disconnected tools.",
            "evidence_ids": [first, third],
            "confidence": "high",
        },
        "buyer_segments": [
            {
                "text": "Enterprise platform teams are a natural JFrog audience when consolidation and governance across delivery workflows matter.",
                "evidence_ids": [first],
                "confidence": "medium",
            }
        ],
        "go_to_market_motion": [
            {
                "text": "Sonatype should be expected to keep framing evaluations around software composition analysis depth and open-source policy enforcement.",
                "evidence_ids": [second],
                "confidence": "medium",
            }
        ],
        "ecosystem_signals": [
            {
                "text": "Partnership and integration evidence should be treated as buyer-context support, not as standalone proof of market leadership.",
                "evidence_ids": [third],
                "confidence": "medium",
            }
        ],
        "market_risks": [
            {
                "text": "No recent data found for independently verified market share or win-loss outcomes.",
                "evidence_ids": [fourth],
                "confidence": "low",
            }
        ],
        "confidence_notes": [
            "Confidence is strongest where DB evidence and Tavily validation point in the same market direction."
        ],
        "metadata": {"test": True},
    }


def fake_technical_runner(prompt: str):
    payload = json.loads(prompt.split("PAYLOAD_JSON:\n", 1)[1])
    evidence_ids = payload["allowed_evidence_ids"]
    first = evidence_ids[0]
    second = evidence_ids[1] if len(evidence_ids) > 1 else first
    third = evidence_ids[2] if len(evidence_ids) > 2 else first
    fourth = evidence_ids[3] if len(evidence_ids) > 3 else first
    return {
        "technical_thesis": {
            "text": "JFrog should frame technical differentiation around governing artifacts and security controls in one delivery plane, while Sonatype should be treated as a credible specialist in open-source governance.",
            "evidence_ids": [first, second],
            "confidence": "high",
        },
        "jfrog_platform_capabilities": [
            {
                "text": "JFrog has a strong technical story when artifact management, software composition analysis, and release governance need to operate as one workflow.",
                "evidence_ids": [first],
                "confidence": "high",
            }
        ],
        "competitor_platform_capabilities": [
            {
                "text": "Sonatype remains technically credible where software composition analysis and repository control are the core evaluation criteria.",
                "evidence_ids": [second],
                "confidence": "medium",
            }
        ],
        "architecture_and_workflow": [
            {
                "text": "The technical comparison should focus on where enforcement happens in the developer workflow and whether controls are native to the artifact system of record.",
                "evidence_ids": [first, third],
                "confidence": "medium",
            }
        ],
        "ai_and_artifact_governance": [
            {
                "text": "AI artifact governance should be positioned cautiously unless the evidence directly supports concrete AI artifact coverage.",
                "evidence_ids": [third],
                "confidence": "medium",
            }
        ],
        "security_capability_comparison": [
            {
                "text": "Both vendors can support supply-chain security narratives, but the buyer should distinguish platform-native controls from specialist open-source governance controls.",
                "evidence_ids": [first, second],
                "confidence": "medium",
            }
        ],
        "technical_risks": [
            {
                "text": "No recent data found for independently verified head-to-head detection accuracy or benchmark outcomes.",
                "evidence_ids": [fourth],
                "confidence": "low",
            }
        ],
        "confidence_notes": [
            "Confidence is highest where technical product evidence is specific and primary."
        ],
        "metadata": {"test": True},
    }


def fake_product_feature_runner(prompt: str):
    payload = json.loads(prompt.split("PAYLOAD_JSON:\n", 1)[1])
    evidence_ids = payload["allowed_evidence_ids"]
    first = evidence_ids[0]
    second = evidence_ids[1] if len(evidence_ids) > 1 else first
    third = evidence_ids[2] if len(evidence_ids) > 2 else first
    fourth = evidence_ids[3] if len(evidence_ids) > 3 else first
    fifth = evidence_ids[4] if len(evidence_ids) > 4 else first
    sixth = evidence_ids[5] if len(evidence_ids) > 5 else first
    return {
        "product_feature_thesis": {
            "text": "JFrog has the stronger product-consolidation story when buyers want artifact-centered governance, while Sonatype is sharper when evaluations focus on open-source intake controls.",
            "evidence_ids": [first, second],
            "confidence": "high",
        },
        "capability_matrix": [
            {
                "capability": "Artifact system of record",
                "jfrog": "Core platform anchor",
                "competitor": "Repository-centered support",
                "assessment": "jfrog_advantage",
                "evidence_ids": [first],
                "confidence": "high",
            },
            {
                "capability": "Open-source: governance",
                "jfrog": "Integrated security workflow",
                "competitor": "Specialist SCA depth",
                "assessment": "competitor_advantage",
                "evidence_ids": [second],
                "confidence": "medium",
            },
            {
                "capability": "SBOM generation",
                "jfrog": "Supported governance motion",
                "competitor": "Supported governance motion",
                "assessment": "parity",
                "evidence_ids": [third],
                "confidence": "medium",
            },
            {
                "capability": "Repository firewall",
                "jfrog": "Package admission controls",
                "competitor": "Dedicated firewall positioning",
                "assessment": "competitor_advantage",
                "evidence_ids": [fourth],
                "confidence": "medium",
            },
            {
                "capability": "Policy governance",
                "jfrog": "Platform-level policy story",
                "competitor": "Open-source policy story",
                "assessment": "parity",
                "evidence_ids": [fifth],
                "confidence": "medium",
            },
            {
                "capability": "AI artifact governance",
                "jfrog": "Use cautiously where supported",
                "competitor": "No recent data found",
                "assessment": "unclear",
                "evidence_ids": [sixth],
                "confidence": "low",
            },
        ],
        "jfrog_feature_advantages": [
            {
                "text": "JFrog should lead with product consolidation across artifact management, policy, and release governance when those workflows are part of the buying criteria.",
                "evidence_ids": [first, third],
                "confidence": "high",
            }
        ],
        "competitor_feature_advantages": [
            {
                "text": "Sonatype remains feature-dangerous when the buyer narrows the decision to SCA depth, repository firewall controls, and open-source governance.",
                "evidence_ids": [second, fourth],
                "confidence": "medium",
            }
        ],
        "jfrog_limitations": [
            {
                "text": "JFrog is more exposed when the buyer wants a purpose-built repository firewall and open-source malware workflow rather than a broader artifact-centered platform.",
                "evidence_ids": [second, fourth],
                "confidence": "medium",
            }
        ],
        "feature_parity_or_gaps": [
            {
                "text": "No recent data found for independently verified head-to-head feature usage, detection accuracy, or benchmark outcomes.",
                "evidence_ids": [sixth],
                "confidence": "low",
            }
        ],
        "buyer_implications": [
            {
                "text": "The product comparison should be framed around the buyer's desired control plane: consolidated artifact governance versus a narrower open-source security control point.",
                "evidence_ids": [first, second],
                "confidence": "high",
            }
        ],
        "confidence_notes": [
            "Confidence is strongest where product documentation and validation evidence describe comparable capabilities."
        ],
        "metadata": {"test": True},
    }


def fake_buyer_field_runner(prompt: str):
    payload = json.loads(prompt.split("PAYLOAD_JSON:\n", 1)[1])
    evidence_ids = payload["allowed_evidence_ids"]
    first = evidence_ids[0]
    second = evidence_ids[1] if len(evidence_ids) > 1 else first
    third = evidence_ids[2] if len(evidence_ids) > 2 else first
    fourth = evidence_ids[3] if len(evidence_ids) > 3 else first
    return {
        "buyer_fit_thesis": {
            "text": "JFrog is the stronger fit when the buyer wants platform consolidation across artifact management, governance, and release trust; Sonatype is more dangerous when the buying frame narrows to open-source governance.",
            "evidence_ids": [first, second],
            "confidence": "high",
        },
        "jfrog_win_conditions": [
            {
                "text": "JFrog should win when platform teams need one operating layer for artifact management, security controls, and release workflows.",
                "evidence_ids": [first],
                "confidence": "high",
            }
        ],
        "competitor_win_conditions": [
            {
                "text": "Sonatype should win more often when AppSec owns the evaluation and the shortlist is centered on software composition analysis and repository firewall controls.",
                "evidence_ids": [second],
                "confidence": "medium",
            }
        ],
        "field_battlecard_thesis": {
            "text": "Field teams should keep the deal framed around operating-model consolidation while testing whether the buyer is actually optimizing for a narrow repository firewall decision.",
            "evidence_ids": [first, second],
            "confidence": "high",
        },
        "objection_handling": [
            {
                "text": "When buyers challenge JFrog on open-source governance depth, answer with platform-native package admission and security scanning, then ask whether they want another control point or one artifact system of record.",
                "evidence_ids": [first, third],
                "confidence": "medium",
            }
        ],
        "discovery_questions": [
            {
                "text": "Ask whether the buyer is trying to consolidate artifact governance across teams or solve a narrower open-source intake-control problem.",
                "evidence_ids": [first, second],
                "confidence": "medium",
            }
        ],
        "qualify_out_signals": [
            {
                "text": "If the buyer only wants a standalone repository firewall owned by AppSec, JFrog should qualify the opportunity carefully rather than forcing a platform-consolidation motion.",
                "evidence_ids": [second],
                "confidence": "medium",
            }
        ],
        "field_actions": [
            {
                "text": "Equip account teams with a side-by-side proof path that pairs JFrog artifact governance and package controls with the buyer's existing release workflow.",
                "evidence_ids": [first, fourth],
                "confidence": "medium",
            }
        ],
        "confidence_notes": [
            "Field guidance is strongest where buyer-fit evidence overlaps with technical capability evidence."
        ],
        "metadata": {"test": True},
    }


def fake_scoring_runner(prompt: str):
    payload = json.loads(prompt.split("PAYLOAD_JSON:\n", 1)[1])
    evidence_ids = payload["allowed_evidence_ids"]
    first = evidence_ids[0]
    second = evidence_ids[1] if len(evidence_ids) > 1 else first
    third = evidence_ids[2] if len(evidence_ids) > 2 else first
    jfrog = payload["jfrog"]
    competitor = payload["competitor"]
    score_values = {
        "Platform Consolidation Fit": (4.4, 3.2),
        "Open Source Governance Fit": (3.6, 4.3),
        "Security Prioritization Fit": (3.8, 4.1),
        "Field Execution Fit": (4.0, 3.7),
    }
    scores = []
    for category in payload["score_categories"]:
        name = category["category"]
        weight = category["weight"]
        archetype = category["buyer_archetype"]
        jfrog_value, competitor_value = score_values[name]
        scores.append(
            {
                "id": f"jfrog-{name.lower().replace(' ', '-')}",
                "company": jfrog,
                "category": name,
                "value": jfrog_value,
                "max_value": 5.0,
                "rationale": f"{jfrog} score reflects the buyer scenario evidence without implying an overall winner.",
                "evidence_ids": [first, third],
                "confidence": "medium",
                "buyer_archetype": archetype,
                "weight": weight,
            }
        )
        scores.append(
            {
                "id": f"competitor-{name.lower().replace(' ', '-')}",
                "company": competitor,
                "category": name,
                "value": competitor_value,
                "max_value": 5.0,
                "rationale": f"{competitor} score reflects where the competitor has buyer-scenario pressure against JFrog.",
                "evidence_ids": [second, third],
                "confidence": "medium",
                "buyer_archetype": archetype,
                "weight": weight,
            }
        )
    return {
        "scores": scores,
        "confidence_notes": [
            "Scores are ordinal buyer-scenario ratings, not market share or benchmark claims."
        ],
        "metadata": {"test": True},
    }


def test_report_agent_skills_load_from_skill_folder():
    assert set(REPORT_AGENT_SKILLS.values()) == {
        "report-db-retrieval",
        "report-evidence-quality",
        "report-extensive-web-search",
        "report-targeted-validation",
        "report-evidence-pack-builder",
        "report-strategy-analyst",
        "report-market-analyst",
        "report-product-feature-analyst",
        "report-technical-analyst",
        "report-buyer-field-analyst",
        "report-scoring-agent",
        "report-checker",
        "report-editor-auditor",
    }
    for agent_key, skill_name in REPORT_AGENT_SKILLS.items():
        body = load_agent_skill(agent_key)
        direct_body = load_skill(skill_name)
        assert direct_body
        assert "EvidencePack" in body or "evidence" in body.lower()


def test_evidence_pack_combines_db_and_tavily_and_freezes():
    fake_mcp = FakeMcpClient()

    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=fake_mcp,
        web_search=fake_tavily_search,
        include_web=True,
    )

    assert pack.frozen is True
    assert pack.competitor == "Sonatype"
    assert fake_mcp.calls
    assert any(item.source == "db" for item in pack.items)
    assert any(item.source == "tavily" for item in pack.items)
    assert all(item.url for item in pack.items)
    assert all(item.summary or item.quote for item in pack.items)
    assert pack.metadata["web_enabled"] is True
    assert pack.inventory is not None
    assert pack.inventory.sources
    assert any(item.tier == "primary" for item in pack.items if item.source == "db")
    assert all(item.tier == "validation" for item in pack.items if item.source == "tavily")
    assert pack.readiness is not None
    assert pack.readiness.overall_score > 0
    assert pack.readiness.sections


def test_evidence_pack_does_not_attach_global_coverage_gaps():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=NoGlobalCoverageMcpClient(),
        web_search=fake_tavily_search,
        include_web=False,
    )

    assert {gap.company for gap in pack.gaps} <= {"JFrog", "Sonatype"}
    assert all(gap.report_section != "coverage_status" for gap in pack.gaps)


def test_report_checker_blocks_unsupported_claims():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="executive_summary",
        url="https://example.com/jfrog",
        summary="JFrog sourced fact.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="bad",
                        text="Unsupported claim",
                        evidence_ids=(),
                        confidence="high",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert validation.passed is False
    assert any(finding.code == "unsupported_claim" for finding in validation.findings)


def test_report_checker_allows_missing_market_share_caveat():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="executive_summary",
        url="https://example.com/jfrog",
        summary="JFrog sourced fact.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={"draft_mode": "crew_strategy"},
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="strategy-executive-thesis",
                        text="No recent data found on independent win-loss outcomes, market share, or comparative pricing depth.",
                        evidence_ids=("ev1",),
                        confidence="low",
                    ),
                    ReportClaim(
                        id="strategy-recommended-action-1",
                        text="Keep competitive positioning tied to validated product evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert not any(
        finding.code == "unsupported_market_share_claim"
        for finding in validation.findings
    )


def test_report_checker_blocks_unresolved_tavily_contradiction():
    item = EvidenceItem(
        id="ev1",
        source="tavily",
        company="JFrog",
        report_section="executive_summary",
        url="https://example.com/jfrog-contradiction",
        summary="JFrog contradictory validation.",
        confidence="medium",
        classification="contradicts_db",
    )
    pack = EvidencePack(
        id="pack1",
        competitor="Sonatype",
        items=(item,),
        metadata={"web_enabled": True},
    )
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="claim1",
                        text="JFrog claim.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert validation.passed is False
    assert any(
        finding.code == "unresolved_web_contradiction"
        for finding in validation.findings
    )


def test_report_checker_requires_db_evidence_for_critical_sections():
    item = EvidenceItem(
        id="ev1",
        source="tavily",
        company="JFrog",
        report_section="executive_summary",
        url="https://example.com/jfrog",
        summary="JFrog web-only evidence.",
        confidence="medium",
        classification="adds_context",
    )
    pack = EvidencePack(
        id="pack1",
        competitor="Sonatype",
        items=(item,),
        metadata={"web_enabled": True},
    )
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="claim1",
                        text="JFrog claim.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert validation.passed is False
    assert any(finding.code == "missing_db_evidence" for finding in validation.findings)
    assert any(finding.code == "thin_section_evidence" for finding in validation.findings)


def test_report_checker_blocks_weak_critical_readiness():
    pack = EvidencePack(
        id="pack1",
        competitor="Sonatype",
        items=(),
        metadata={"web_enabled": True},
    )
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="missing",
                        text="JFrog/executive_summary: no recent data found",
                        claim_type="missing",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert validation.passed is False
    assert any(
        finding.code in {"weak_critical_evidence", "thin_section_evidence"}
        for finding in validation.findings
    )


def test_generate_report_writes_json_html_and_handles_pdf_dependency(tmp_path):
    fake_mcp = FakeMcpClient()

    result = generate_report(
        "Sonatype",
        out_dir=tmp_path,
        formats=("json", "html", "pdf"),
        mcp_client=fake_mcp,
        web_search=fake_tavily_search,
        include_web=True,
    )

    assert result.validation.passed is True
    assert any(section.agent_key == "strategy_analyst" for section in result.draft.sections)
    assert any(score.category == "Platform Breadth" for score in result.draft.scores)
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.html").exists()
    assert "JFrog vs Sonatype" in (tmp_path / "report.html").read_text()
    pdf_result = next(render for render in result.renders if render.format == "pdf")
    assert pdf_result.status in {"written", "skipped"}


def test_render_html_contains_checker_and_sources():
    fake_mcp = FakeMcpClient()
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=fake_mcp,
        web_search=fake_tavily_search,
        include_web=True,
    )
    draft = build_report_draft(pack)
    validation = check_report(pack, draft)

    html = render_html(pack, draft, validation)

    assert "Strategy Analyst" in html
    assert "report-strategy-analyst" not in html
    assert "Cited Sources" in html
    assert "Report Checker" not in html
    assert "Evidence Readiness" not in html
    assert "Source Inventory" not in html
    assert "Evidence Sources" not in html
    assert "JFrog vs Sonatype" in html


def test_analyst_draft_uses_section_agents_and_comparative_claims():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        sections=["executive_summary", "technical_teardown"],
    )

    draft = build_report_draft(pack, sections=["executive_summary", "technical_teardown"])

    executive = next(section for section in draft.sections if section.id == "executive_summary")
    technical = next(section for section in draft.sections if section.id == "technical_teardown")
    assert executive.agent_key == "strategy_analyst"
    assert executive.skill_name == "report-strategy-analyst"
    assert technical.agent_key == "technical_analyst"
    assert any(
        claim.id == "executive_summary-comparative-thesis"
        and len(claim.evidence_ids) >= 2
        for claim in executive.claims
    )
    assert draft.scores
    assert all(score.evidence_ids for score in draft.scores)


def test_strategy_prompt_input_curates_evidence_pack():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
    )

    payload = build_strategy_prompt_input(pack)

    assert payload["task"] == "strategy_analyst_executive_summary"
    assert payload["allowed_evidence_ids"]
    assert len(payload["evidence"]) <= 48
    assert {item["id"] for item in payload["evidence"]} == set(payload["allowed_evidence_ids"])
    assert payload["readiness"]
    assert payload["source_inventory"]


def test_strategy_analysis_parser_validates_json_and_citations():
    parsed = parse_strategy_analysis(
        fake_strategy_runner(
            "PAYLOAD_JSON:\n"
            + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3"]})
        ),
        allowed_evidence_ids={"ev1", "ev2", "ev3"},
    )

    assert parsed.executive_thesis.text.startswith("JFrog should frame")


def test_strategy_analysis_parser_blocks_malformed_and_uncited_output():
    try:
        parse_strategy_analysis("not json", allowed_evidence_ids={"ev1"})
    except StrategyGenerationError:
        pass
    else:  # pragma: no cover - failure path.
        raise AssertionError("malformed strategy output should fail")

    output = fake_strategy_runner(
        "PAYLOAD_JSON:\n" + json.dumps({"allowed_evidence_ids": ["ev1", "ev2"]})
    )
    output["executive_thesis"]["evidence_ids"] = ["missing"]
    try:
        parse_strategy_analysis(output, allowed_evidence_ids={"ev1", "ev2"})
    except StrategyGenerationError as exc:
        assert "outside the curated" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("uncited strategy output should fail")

    output = fake_strategy_runner(
        "PAYLOAD_JSON:\n" + json.dumps({"allowed_evidence_ids": ["ev1", "ev2"]})
    )
    output["executive_thesis"]["text"] = "JFrog has an advantage [177c02acaf3fd297]."
    try:
        parse_strategy_analysis(output, allowed_evidence_ids={"ev1", "ev2"})
    except StrategyGenerationError as exc:
        assert "source-list prose" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("inline evidence IDs should fail")


def test_market_prompt_input_curates_evidence_pack():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
    )

    payload = build_market_prompt_input(pack)

    assert payload["task"] == "market_analyst_company_and_market_sections"
    assert payload["allowed_evidence_ids"]
    assert len(payload["evidence"]) <= 42
    assert {item["id"] for item in payload["evidence"]} == set(payload["allowed_evidence_ids"])
    assert {
        item["report_section"] for item in payload["evidence"]
    } <= {"company_snapshot", "market_context", "buyer_fit", "field_battlecard"}


def test_market_analysis_parser_validates_json_and_citations():
    parsed = parse_market_analysis(
        fake_market_runner(
            "PAYLOAD_JSON:\n"
            + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
        ),
        allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"},
    )

    assert parsed.company_snapshot_thesis.text.startswith("JFrog and Sonatype")


def test_market_analysis_parser_blocks_malformed_uncited_and_source_prose():
    try:
        parse_market_analysis("not json", allowed_evidence_ids={"ev1"})
    except MarketGenerationError:
        pass
    else:  # pragma: no cover - failure path.
        raise AssertionError("malformed market output should fail")

    output = fake_market_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
    )
    output["company_snapshot_thesis"]["evidence_ids"] = ["missing"]
    try:
        parse_market_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"})
    except MarketGenerationError as exc:
        assert "outside the curated" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("uncited market output should fail")

    output = fake_market_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
    )
    output["company_snapshot_thesis"]["text"] = "Evidence: JFrog has a broad market story."
    try:
        parse_market_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"})
    except MarketGenerationError as exc:
        assert "source-list prose" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("source-list market output should fail")


def test_product_feature_prompt_input_curates_evidence_pack():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
    )

    payload = build_product_feature_prompt_input(pack)

    assert payload["task"] == "product_feature_analyst_product_feature_analysis"
    assert payload["allowed_evidence_ids"]
    assert len(payload["evidence"]) <= 64
    assert {item["id"] for item in payload["evidence"]} == set(payload["allowed_evidence_ids"])
    assert {
        item["report_section"] for item in payload["evidence"]
    } <= {
        "product_feature_analysis",
        "technical_teardown",
        "supply_chain_security",
        "buyer_fit",
        "executive_summary",
    }
    assert payload["product_catalog"]
    assert payload["capability_evidence_matrix"]["rows"]


def test_tavily_topics_use_all_section_queries_for_product_retrieval():
    topics = tavily_topics(
        "Sonatype",
        competitor="Sonatype",
        specs=section_specs(["product_feature_analysis"]),
    )

    assert any("product features" in topic for topic in topics)
    assert any("feature comparison" in topic for topic in topics)


def test_capability_retrieval_builds_targeted_matrix_and_search_attempts():
    client = FakeMcpClient()
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=client,
        web_search=fake_tavily_search,
        include_web=True,
    )

    assert pack.capability_matrix is not None
    sbom = next(row for row in pack.capability_matrix.rows if row.capability_id == "sbom_generation")
    assert sbom.must_resolve is True
    assert sbom.search_status in {"supported", "partially_supported"}
    assert sbom.evidence_ids
    assert {"db", "tavily"} <= {attempt.source for attempt in sbom.jfrog.search_attempts}
    assert {"db", "tavily"} <= {attempt.source for attempt in sbom.competitor.search_attempts}
    assert any("SBOM" in call["query"] for call in client.calls)
    assert pack.product_catalog


def test_evidence_pack_prefers_batch_capability_mcp_tool():
    client = BatchCapabilityMcpClient()
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=client,
        web_search=fake_tavily_search,
        include_web=False,
    )

    assert len(client.batch_calls) == 1
    assert pack.metadata["capability_retrieval_mode"] == "batch"
    assert pack.metadata["capability_search_attempt_count"] == len(CAPABILITY_DEFINITIONS) * 2
    assert any(
        item.metadata.get("batch_marker")
        for item in pack.items
        if item.report_section == "product_feature_analysis"
    )
    assert not any(
        "product documentation capabilities" in call["query"]
        for call in client.calls
    )
    assert pack.capability_matrix is not None
    assert all(row.evidence_ids for row in pack.capability_matrix.rows)


def test_evidence_pack_prefers_batch_section_mcp_tool():
    client = BatchSectionAndCapabilityMcpClient()
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=client,
        web_search=fake_tavily_search,
        include_web=False,
    )

    assert len(client.section_batch_calls) == 1
    assert len(client.batch_calls) == 1
    assert client.calls == []
    assert pack.metadata["section_retrieval_mode"] == "batch"
    assert pack.metadata["capability_retrieval_mode"] == "batch"
    assert pack.metadata["section_batch_coverage"]
    assert any(
        item.metadata.get("retrieval_mode") == "mcp_batch_section"
        for item in pack.items
    )


def test_capability_matrix_does_not_infer_advantage_from_missing_vendor_evidence():
    capability = CAPABILITY_DEFINITIONS[0]
    items = [
        EvidenceItem(
            id=f"sonatype-{index}",
            source="db",
            tier="primary",
            company="Sonatype",
            report_section="product_feature_analysis",
            url=f"https://sonatype.com/{capability.id}/{index}",
            summary=f"Sonatype evidence for {capability.label}.",
            axis="technical",
            dimension=capability.dimension,
            confidence="high",
            metadata={"capability_id": capability.id},
        )
        for index in range(2)
    ]
    attempts = (
        TargetedSearchAttempt(
            company="JFrog",
            capability_id=capability.id,
            capability_label=capability.label,
            source="db",
            query=f"JFrog {capability.label}",
            result_count=0,
            status="not_found_after_search",
        ),
        TargetedSearchAttempt(
            company="Sonatype",
            capability_id=capability.id,
            capability_label=capability.label,
            source="db",
            query=f"Sonatype {capability.label}",
            result_count=2,
            status="supported",
        ),
    )

    matrix, _catalog, gaps = build_capability_artifacts(
        "Sonatype",
        items=items,
        attempts=attempts,
    )

    row = next(row for row in matrix.rows if row.capability_id == capability.id)
    assert row.jfrog.status == "not_found_after_search"
    assert row.competitor.status == "supported"
    assert row.readout == "unclear"
    assert row.search_status == "unclear_needs_review"
    assert any(gap.dimension == capability.dimension for gap in gaps)


def test_product_feature_analysis_parser_validates_json_matrix_and_citations():
    parsed = parse_product_feature_analysis(
        fake_product_feature_runner(
            "PAYLOAD_JSON:\n"
            + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4", "ev5", "ev6"]})
        ),
        allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4", "ev5", "ev6"},
    )

    assert parsed.product_feature_thesis.text.startswith("JFrog has the stronger")
    assert len(parsed.capability_matrix) == 6
    assert parsed.capability_matrix[0].assessment == "jfrog_advantage"


def test_product_feature_analysis_parser_blocks_malformed_uncited_and_source_prose():
    try:
        parse_product_feature_analysis("not json", allowed_evidence_ids={"ev1"})
    except ProductFeatureGenerationError:
        pass
    else:  # pragma: no cover - failure path.
        raise AssertionError("malformed product/feature output should fail")

    output = fake_product_feature_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4", "ev5", "ev6"]})
    )
    output["capability_matrix"][0]["evidence_ids"] = ["missing"]
    try:
        parse_product_feature_analysis(
            output,
            allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4", "ev5", "ev6"},
        )
    except ProductFeatureGenerationError as exc:
        assert "outside the curated" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("uncited product/feature output should fail")

    output = fake_product_feature_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4", "ev5", "ev6"]})
    )
    output["product_feature_thesis"]["text"] = "Evidence: JFrog has a broad product story."
    try:
        parse_product_feature_analysis(
            output,
            allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4", "ev5", "ev6"},
        )
    except ProductFeatureGenerationError as exc:
        assert "source-list prose" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("source-list product/feature output should fail")


def test_technical_prompt_input_curates_evidence_pack():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
    )

    payload = build_technical_prompt_input(pack)

    assert payload["task"] == "technical_analyst_technical_and_security_sections"
    assert payload["allowed_evidence_ids"]
    assert len(payload["evidence"]) <= 56
    assert {item["id"] for item in payload["evidence"]} == set(payload["allowed_evidence_ids"])
    assert {
        item["report_section"] for item in payload["evidence"]
    } <= {"technical_teardown", "supply_chain_security", "scoring"}


def test_technical_analysis_parser_validates_json_and_citations():
    parsed = parse_technical_analysis(
        fake_technical_runner(
            "PAYLOAD_JSON:\n"
            + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
        ),
        allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"},
    )

    assert parsed.technical_thesis.text.startswith("JFrog should frame technical")


def test_technical_analysis_parser_blocks_malformed_uncited_and_source_prose():
    try:
        parse_technical_analysis("not json", allowed_evidence_ids={"ev1"})
    except TechnicalGenerationError:
        pass
    else:  # pragma: no cover - failure path.
        raise AssertionError("malformed technical output should fail")

    output = fake_technical_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
    )
    output["technical_thesis"]["evidence_ids"] = ["missing"]
    try:
        parse_technical_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"})
    except TechnicalGenerationError as exc:
        assert "outside the curated" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("uncited technical output should fail")

    output = fake_technical_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
    )
    output["technical_thesis"]["text"] = "Evidence: JFrog has a broad technical story."
    try:
        parse_technical_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"})
    except TechnicalGenerationError as exc:
        assert "source-list prose" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("source-list technical output should fail")


def test_buyer_field_prompt_input_curates_evidence_pack():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
    )

    payload = build_buyer_field_prompt_input(pack)

    assert payload["task"] == "buyer_field_analyst_buyer_fit_and_field_battlecard"
    assert payload["allowed_evidence_ids"]
    assert len(payload["evidence"]) <= 56
    assert {item["id"] for item in payload["evidence"]} == set(payload["allowed_evidence_ids"])
    assert {
        item["report_section"] for item in payload["evidence"]
    } <= {
        "buyer_fit",
        "field_battlecard",
        "company_snapshot",
        "market_context",
        "technical_teardown",
        "supply_chain_security",
    }


def test_buyer_field_analysis_parser_validates_json_and_citations():
    parsed = parse_buyer_field_analysis(
        fake_buyer_field_runner(
            "PAYLOAD_JSON:\n"
            + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
        ),
        allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"},
    )

    assert parsed.buyer_fit_thesis.text.startswith("JFrog is the stronger fit")


def test_buyer_field_analysis_parser_blocks_malformed_uncited_and_source_prose():
    try:
        parse_buyer_field_analysis("not json", allowed_evidence_ids={"ev1"})
    except BuyerFieldGenerationError:
        pass
    else:  # pragma: no cover - failure path.
        raise AssertionError("malformed buyer/field output should fail")

    output = fake_buyer_field_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
    )
    output["buyer_fit_thesis"]["evidence_ids"] = ["missing"]
    try:
        parse_buyer_field_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"})
    except BuyerFieldGenerationError as exc:
        assert "outside the curated" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("uncited buyer/field output should fail")

    output = fake_buyer_field_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3", "ev4"]})
    )
    output["buyer_fit_thesis"]["text"] = "Evidence: JFrog has a broad buyer-fit story."
    try:
        parse_buyer_field_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3", "ev4"})
    except BuyerFieldGenerationError as exc:
        assert "source-list prose" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("source-list buyer/field output should fail")


def test_scoring_prompt_input_uses_capability_matrix_and_product_catalog():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
    )

    payload = build_scoring_prompt_input(pack)

    assert payload["task"] == "scoring_agent_weighted_buyer_scorecards"
    assert payload["allowed_evidence_ids"]
    assert len(payload["evidence"]) <= 80
    assert {item["id"] for item in payload["evidence"]} == set(payload["allowed_evidence_ids"])
    assert payload["product_catalog"]
    assert payload["capability_evidence_matrix"]["rows"]
    assert {
        category["category"] for category in payload["score_categories"]
    } == {
        "Platform Consolidation Fit",
        "Open Source Governance Fit",
        "Security Prioritization Fit",
        "Field Execution Fit",
    }


def test_scoring_analysis_parser_validates_scores_and_citations():
    output = fake_scoring_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps(
            {
                "allowed_evidence_ids": ["ev1", "ev2", "ev3"],
                "jfrog": "JFrog",
                "competitor": "Sonatype",
                "score_categories": [
                    {
                        "category": "Platform Consolidation Fit",
                        "buyer_archetype": "Platform buyer",
                        "weight": 0.30,
                    },
                    {
                        "category": "Open Source Governance Fit",
                        "buyer_archetype": "Governance buyer",
                        "weight": 0.25,
                    },
                    {
                        "category": "Security Prioritization Fit",
                        "buyer_archetype": "Security buyer",
                        "weight": 0.25,
                    },
                    {
                        "category": "Field Execution Fit",
                        "buyer_archetype": "Field buyer",
                        "weight": 0.20,
                    },
                ],
            }
        )
    )

    parsed = parse_scoring_analysis(
        output,
        allowed_evidence_ids={"ev1", "ev2", "ev3"},
    )

    assert len(parsed.scores) == 8
    assert parsed.scores[0].category == "Platform Consolidation Fit"
    assert parsed.scores[0].evidence_ids


def test_scoring_analysis_parser_blocks_malformed_uncited_and_source_prose():
    try:
        parse_scoring_analysis("not json", allowed_evidence_ids={"ev1"})
    except ScoringGenerationError:
        pass
    else:  # pragma: no cover - failure path.
        raise AssertionError("malformed scoring output should fail")

    output = fake_scoring_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps(
            {
                "allowed_evidence_ids": ["ev1", "ev2", "ev3"],
                "jfrog": "JFrog",
                "competitor": "Sonatype",
                "score_categories": [
                    {
                        "category": "Platform Consolidation Fit",
                        "buyer_archetype": "Platform buyer",
                        "weight": 0.30,
                    }
                ],
            }
        )
    )
    output["scores"][0]["evidence_ids"] = ["missing"]
    try:
        parse_scoring_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3"})
    except ScoringGenerationError as exc:
        assert "outside the curated" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("uncited scoring output should fail")

    output = fake_scoring_runner(
        "PAYLOAD_JSON:\n"
        + json.dumps(
            {
                "allowed_evidence_ids": ["ev1", "ev2", "ev3"],
                "jfrog": "JFrog",
                "competitor": "Sonatype",
                "score_categories": [
                    {
                        "category": "Platform Consolidation Fit",
                        "buyer_archetype": "Platform buyer",
                        "weight": 0.30,
                    }
                ],
            }
        )
    )
    output["scores"][0]["rationale"] = "Evidence: JFrog gets this score."
    try:
        parse_scoring_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3"})
    except ScoringGenerationError as exc:
        assert "source-list prose" in str(exc)
    else:  # pragma: no cover - failure path.
        raise AssertionError("source-list scoring output should fail")


def test_generate_report_crew_strategy_replaces_executive_summary():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy",
        strategy_runner=fake_strategy_runner,
    )

    assert result.validation.passed is True
    executive = next(section for section in result.draft.sections if section.id == "executive_summary")
    assert executive.claims[0].id == "strategy-executive-thesis"
    assert any(claim.id.startswith("strategy-recommended-action") for claim in executive.claims)
    assert "current section uses" not in " ".join(claim.text for claim in executive.claims).lower()
    assert result.draft.metadata["strategy_generation_status"] == "written"


def test_generate_report_crew_strategy_market_replaces_market_sections():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
    )

    assert result.validation.passed is True
    executive = next(section for section in result.draft.sections if section.id == "executive_summary")
    company = next(section for section in result.draft.sections if section.id == "company_snapshot")
    market = next(section for section in result.draft.sections if section.id == "market_context")
    assert executive.claims[0].id == "strategy-executive-thesis"
    assert company.claims[0].id == "market-company-snapshot-thesis"
    assert market.claims[0].id == "market-context-thesis"
    assert any(claim.id.startswith("market-buyer-segment") for claim in market.claims)
    assert result.draft.metadata["market_generation_status"] == "written"


def test_generate_report_crew_strategy_market_technical_replaces_technical_sections():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market_technical",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        technical_runner=fake_technical_runner,
    )

    assert result.validation.passed is True
    technical = next(section for section in result.draft.sections if section.id == "technical_teardown")
    security = next(section for section in result.draft.sections if section.id == "supply_chain_security")
    assert technical.claims[0].id == "technical-teardown-thesis"
    assert any(claim.id.startswith("technical-jfrog-capability") for claim in technical.claims)
    assert any(claim.id.startswith("technical-security-comparison") for claim in security.claims)
    assert result.draft.metadata["technical_generation_status"] == "written"


def test_generate_report_crew_strategy_market_technical_field_replaces_buyer_sections():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market_technical_field",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        technical_runner=fake_technical_runner,
        buyer_field_runner=fake_buyer_field_runner,
    )

    assert result.validation.passed is True
    buyer = next(section for section in result.draft.sections if section.id == "buyer_fit")
    field = next(section for section in result.draft.sections if section.id == "field_battlecard")
    assert buyer.claims[0].id == "buyer-fit-thesis"
    assert any(claim.id.startswith("buyer-jfrog-win-condition") for claim in buyer.claims)
    assert field.claims[0].id == "field-battlecard-thesis"
    assert any(claim.id.startswith("field-objection-handling") for claim in field.claims)
    assert result.draft.metadata["buyer_field_generation_status"] == "written"


def test_generate_report_crew_strategy_market_product_technical_field_replaces_product_section():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market_product_technical_field",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        product_feature_runner=fake_product_feature_runner,
        technical_runner=fake_technical_runner,
        buyer_field_runner=fake_buyer_field_runner,
    )

    assert result.validation.passed is True
    product = next(section for section in result.draft.sections if section.id == "product_feature_analysis")
    buyer = next(section for section in result.draft.sections if section.id == "buyer_fit")
    assert product.claims[0].id == "product-feature-thesis"
    assert product.metadata["capability_matrix"]
    assert len(product.metadata["capability_matrix"]) == 6
    assert any(claim.id.startswith("product-jfrog-advantage") for claim in product.claims)
    assert any(claim.id.startswith("product-jfrog-limitation") for claim in product.claims)
    assert buyer.claims[0].id == "buyer-fit-thesis"
    assert result.draft.metadata["product_feature_generation_status"] == "written"


def test_generate_report_full_crew_scoring_mode_adds_scorecards():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market_product_technical_field_scoring",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        product_feature_runner=fake_product_feature_runner,
        technical_runner=fake_technical_runner,
        buyer_field_runner=fake_buyer_field_runner,
        scoring_runner=fake_scoring_runner,
    )

    assert result.validation.passed is True
    assert result.draft.metadata["scoring_generation_status"] == "written"
    assert len(result.draft.scores) == 8
    assert {
        (score.category, score.company)
        for score in result.draft.scores
    } >= {
        ("Platform Consolidation Fit", "JFrog"),
        ("Platform Consolidation Fit", "Sonatype"),
        ("Open Source Governance Fit", "JFrog"),
        ("Open Source Governance Fit", "Sonatype"),
    }
    assert all(score.buyer_archetype for score in result.draft.scores)


def test_rendered_report_hides_internal_source_metadata():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy",
        strategy_runner=fake_strategy_runner,
    )

    html = render_html(result.evidence_pack, result.draft, result.validation)
    lowered = html.lower()

    assert "current section uses" not in lowered
    assert "source types led by" not in lowered
    assert "key support:" not in lowered
    assert "official llm research report" not in lowered
    assert "raw_path" not in lowered
    assert "source path" not in lowered
    assert "metadata" not in lowered
    assert "full crewai synthesis" not in lowered
    assert "win_loss_signals" not in lowered
    assert "product_portfolio" not in lowered
    assert "evidence:" not in lowered
    assert "[177c02acaf3fd297]" not in lowered
    assert "report-strategy-analyst" not in lowered
    assert "Weighted Buyer Scorecards" not in html


def test_rendered_strategy_market_report_shows_only_validated_agent_sections():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
    )

    html = render_html(result.evidence_pack, result.draft, result.validation)
    lowered = html.lower()

    assert "Executive Summary" in html
    assert "Company Snapshot" in html
    assert "Market And Strategic Context" in html
    assert "Technical And Feature Teardown" not in html
    assert "Weighted Buyer Scorecards" not in html
    assert "report-market-analyst" not in lowered
    assert "evidence:" not in lowered
    assert "raw_path" not in lowered


def test_rendered_strategy_market_technical_report_shows_only_validated_agent_sections():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market_technical",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        technical_runner=fake_technical_runner,
    )

    html = render_html(result.evidence_pack, result.draft, result.validation)
    lowered = html.lower()

    assert "Executive Summary" in html
    assert "Company Snapshot" in html
    assert "Market And Strategic Context" in html
    assert "Technical And Feature Teardown" in html
    assert "Supply Chain Security Coverage" in html
    assert "Buyer Fit Matrix" not in html
    assert "Weighted Buyer Scorecards" not in html
    assert "report-technical-analyst" not in lowered
    assert "evidence:" not in lowered
    assert "raw_path" not in lowered


def test_rendered_strategy_market_technical_field_report_shows_only_validated_agent_sections():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market_technical_field",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        technical_runner=fake_technical_runner,
        buyer_field_runner=fake_buyer_field_runner,
    )

    html = render_html(result.evidence_pack, result.draft, result.validation)
    lowered = html.lower()

    assert "Executive Summary" in html
    assert "Company Snapshot" in html
    assert "Market And Strategic Context" in html
    assert "Technical And Feature Teardown" in html
    assert "Supply Chain Security Coverage" in html
    assert "Buyer Fit Matrix" in html
    assert "JFrog Field Battlecard" in html
    assert "Weighted Buyer Scorecards" not in html
    assert "report-buyer-field-analyst" not in lowered
    assert "evidence:" not in lowered
    assert "raw_path" not in lowered


def test_rendered_strategy_market_product_technical_field_report_shows_matrix_framework_and_no_scores():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market_product_technical_field",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        product_feature_runner=fake_product_feature_runner,
        technical_runner=fake_technical_runner,
        buyer_field_runner=fake_buyer_field_runner,
    )

    html = render_html(result.evidence_pack, result.draft, result.validation)
    lowered = html.lower()

    assert "Analysis Framework" in html
    assert "Competitive Tradeoff Matrix" in html
    assert "Product And Feature Analysis" in html
    assert "Product Catalog" in html
    assert "Capability Readout" in html
    assert "Where Each Vendor Pressures The Other" in html
    assert "Where JFrog Is Exposed" in html
    assert "Artifact system of record" in html
    assert "JFrog Field Battlecard" in html
    assert "Weighted Buyer Scorecards" not in html
    assert "report-product-feature-analyst" not in lowered
    assert "source path" not in lowered
    assert "metadata" not in lowered
    assert "evidence:" not in lowered
    assert "raw_path" not in lowered


def test_rendered_full_crew_scoring_mode_shows_buyer_scorecards():
    result = generate_report(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
        draft_mode="crew_strategy_market_product_technical_field_scoring",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        product_feature_runner=fake_product_feature_runner,
        technical_runner=fake_technical_runner,
        buyer_field_runner=fake_buyer_field_runner,
        scoring_runner=fake_scoring_runner,
    )

    html = render_html(result.evidence_pack, result.draft, result.validation)
    lowered = html.lower()

    assert "Weighted Buyer Scorecards" in html
    assert "Platform Consolidation Fit" in html
    assert "Open Source Governance Fit" in html
    assert "Confidence:" in html
    assert "Weight:" in html
    assert "report-scoring-agent" not in lowered
    assert "evidence:" not in lowered
    assert "raw_path" not in lowered


def test_checker_blocks_strategy_source_list_prose_and_missing_recommendations():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="executive_summary",
        url="https://example.com/jfrog",
        summary="JFrog sourced fact.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={"draft_mode": "crew_strategy"},
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="strategy-executive-thesis",
                        text="The current section uses source types led by vendor pages.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert any(finding.code == "source_list_prose_in_strategy" for finding in validation.findings)
    assert any(finding.code == "missing_strategy_recommendations" for finding in validation.findings)


def test_checker_blocks_market_source_list_prose_and_missing_groups():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="market_context",
        url="https://example.com/jfrog",
        summary="JFrog sourced market fact.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={"draft_mode": "crew_strategy_market"},
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="strategy-executive-thesis",
                        text="JFrog should lead with platform breadth.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="strategy-recommended-action-1",
                        text="Tie positioning to validated evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="company_snapshot",
                title="Company Snapshot",
                claims=(
                    ReportClaim(
                        id="market-company-snapshot-thesis",
                        text="Source: the current section uses market material.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="market_context",
                title="Market And Strategic Context",
                claims=(
                    ReportClaim(
                        id="market-context-thesis",
                        text="JFrog has a market story tied to platform breadth.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert any(finding.code == "source_list_prose_in_market" for finding in validation.findings)
    assert any(finding.code == "missing_market_claim_group" for finding in validation.findings)


def test_checker_blocks_product_feature_source_prose_missing_groups_and_matrix():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="product_feature_analysis",
        url="https://example.com/jfrog",
        summary="JFrog sourced product fact.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={"draft_mode": "crew_strategy_market_product_technical_field"},
        sections=(
            ReportSection(
                id="product_feature_analysis",
                title="Product And Feature Analysis",
                claims=(
                    ReportClaim(
                        id="product-feature-thesis",
                        text="Source: the current section uses product material.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert any(
        finding.code == "source_list_prose_in_product_feature"
        for finding in validation.findings
    )
    assert any(
        finding.code == "missing_product_feature_claim_group"
        for finding in validation.findings
    )
    assert any(
        finding.code == "missing_product_feature_matrix"
        for finding in validation.findings
    )


def test_checker_blocks_uncited_product_feature_matrix_row():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="product_feature_analysis",
        url="https://example.com/jfrog",
        summary="JFrog sourced product fact.",
        confidence="high",
    )
    matrix = [
        {
            "capability": f"Capability {index}",
            "jfrog": "Supported",
            "competitor": "Supported",
            "assessment": "parity",
            "evidence_ids": ["ev1"] if index > 1 else [],
            "confidence": "medium",
        }
        for index in range(1, 7)
    ]
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={"draft_mode": "crew_strategy_market_product_technical_field"},
        sections=(
            ReportSection(
                id="product_feature_analysis",
                title="Product And Feature Analysis",
                claims=(
                    ReportClaim(
                        id="product-feature-thesis",
                        text="JFrog has a product story.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="product-jfrog-advantage-1",
                        text="JFrog has an advantage.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="product-competitor-advantage-1",
                        text="Sonatype has an advantage.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="product-jfrog-limitation-1",
                        text="JFrog has a limitation in this buying scenario.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="product-parity-gap-1",
                        text="Some capabilities are similar.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="product-buyer-implication-1",
                        text="Buyers should compare control-plane fit.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
                metadata={"capability_matrix": matrix},
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert any(
        finding.code == "uncited_product_feature_matrix_row"
        for finding in validation.findings
    )


def test_checker_blocks_unclear_must_resolve_capability_without_targeted_search():
    pack = build_evidence_pack_for_competitor(
        "Sonatype",
        mcp_client=FakeMcpClient(),
        web_search=fake_tavily_search,
        include_web=True,
    )
    assert pack.capability_matrix is not None
    rows = list(pack.capability_matrix.rows)
    row = rows[0]
    rows[0] = row.model_copy(
        update={
            "search_status": "unclear_needs_review",
            "jfrog": row.jfrog.model_copy(
                update={
                    "status": "unclear_needs_review",
                    "search_attempts": (),
                }
            ),
        }
    )
    bad_pack = pack.model_copy(
        update={
            "capability_matrix": pack.capability_matrix.model_copy(
                update={"rows": tuple(rows)}
            )
        }
    )
    draft = build_report_draft(
        bad_pack,
        draft_mode="crew_strategy_market_product_technical_field",
        strategy_runner=fake_strategy_runner,
        market_runner=fake_market_runner,
        product_feature_runner=fake_product_feature_runner,
        technical_runner=fake_technical_runner,
        buyer_field_runner=fake_buyer_field_runner,
    )

    validation = check_report(bad_pack, draft)

    assert any(
        finding.code == "missing_capability_targeted_search"
        for finding in validation.findings
    )
    assert any(
        finding.code == "unclear_must_resolve_capability"
        for finding in validation.findings
    )


def test_checker_allows_market_share_caveat_without_market_share_evidence():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="company_snapshot",
        url="https://example.com/jfrog",
        summary="JFrog public-company analyst price target detail.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={"draft_mode": "crew_strategy_market"},
        sections=(
            ReportSection(
                id="company_snapshot",
                title="Company Snapshot",
                claims=(
                    ReportClaim(
                        id="market-company-snapshot-thesis",
                        text="JFrog analyst sentiment is not validated market share or win-rate evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-jfrog-company-position-1",
                        text="JFrog has public-company evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-competitor-company-position-1",
                        text="Sonatype has a separate security-led market position.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="market_context",
                title="Market And Strategic Context",
                claims=(
                    ReportClaim(
                        id="market-context-thesis",
                        text="Competitive ranking is not substantiated by market share evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-buyer-segment-1",
                        text="Buyers should treat this as an evidence gap.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-gtm-motion-1",
                        text="Sonatype pressures JFrog in security-led evaluations.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-ecosystem-signal-1",
                        text="Ecosystem claims need validation.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-risk-1",
                        text="Market share is not established by the cited evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert not any(
        finding.code == "unsupported_market_share_claim"
        for finding in validation.findings
    )


def test_checker_blocks_scoring_mode_without_scorecards():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="executive_summary",
        url="https://example.com/jfrog",
        summary="JFrog sourced strategic fact.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={
            "draft_mode": "crew_strategy_market_product_technical_field_scoring",
            "scoring_generation_error": "scoring failed",
        },
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="strategy-executive-thesis",
                        text="JFrog and Sonatype have different competitive postures.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="strategy-competitor-strength-1",
                        text="Sonatype has a security-led strength.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="strategy-risk-1",
                        text="JFrog has an exposure in narrow security-led evaluations.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="strategy-recommended-action-1",
                        text="JFrog should qualify the buyer scenario.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert any(finding.code == "scoring_generation_failed" for finding in validation.findings)
    assert any(finding.code == "missing_scoring_scores" for finding in validation.findings)


def test_checker_blocks_technical_source_list_prose_and_missing_groups():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="technical_teardown",
        url="https://example.com/jfrog",
        summary="JFrog sourced technical fact.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={"draft_mode": "crew_strategy_market_technical"},
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="strategy-executive-thesis",
                        text="JFrog should lead with platform breadth.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="strategy-recommended-action-1",
                        text="Tie positioning to validated evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="company_snapshot",
                title="Company Snapshot",
                claims=(
                    ReportClaim(
                        id="market-company-snapshot-thesis",
                        text="JFrog has a validated market position.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-jfrog-company-position-1",
                        text="JFrog has market evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-competitor-company-position-1",
                        text="Sonatype has market evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="market_context",
                title="Market And Strategic Context",
                claims=(
                    ReportClaim(
                        id="market-context-thesis",
                        text="JFrog has a market story tied to platform breadth.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-buyer-segment-1",
                        text="Platform buyers care about consolidation.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-gtm-motion-1",
                        text="GTM evidence is present.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-ecosystem-signal-1",
                        text="Ecosystem evidence is present.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-risk-1",
                        text="No recent data found for market share.",
                        evidence_ids=("ev1",),
                        confidence="low",
                    ),
                ),
            ),
            ReportSection(
                id="technical_teardown",
                title="Technical And Feature Teardown",
                claims=(
                    ReportClaim(
                        id="technical-teardown-thesis",
                        text="Source: the current section uses technical material.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="supply_chain_security",
                title="Supply Chain Security Coverage",
                claims=(
                    ReportClaim(
                        id="technical-security-comparison-1",
                        text="JFrog has a security story tied to artifact governance.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert any(finding.code == "source_list_prose_in_technical" for finding in validation.findings)
    assert any(finding.code == "missing_technical_claim_group" for finding in validation.findings)


def test_checker_blocks_buyer_field_source_list_prose_and_missing_groups():
    item = EvidenceItem(
        id="ev1",
        source="db",
        company="JFrog",
        report_section="buyer_fit",
        url="https://example.com/jfrog",
        summary="JFrog sourced buyer-fit fact.",
        confidence="high",
    )
    pack = EvidencePack(id="pack1", competitor="Sonatype", items=(item,))
    draft = ReportDraft(
        competitor="Sonatype",
        evidence_pack_id="pack1",
        metadata={"draft_mode": "crew_strategy_market_technical_field"},
        sections=(
            ReportSection(
                id="executive_summary",
                title="Executive Summary",
                claims=(
                    ReportClaim(
                        id="strategy-executive-thesis",
                        text="JFrog should lead with platform breadth.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="strategy-recommended-action-1",
                        text="Tie positioning to validated evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="company_snapshot",
                title="Company Snapshot",
                claims=(
                    ReportClaim(
                        id="market-company-snapshot-thesis",
                        text="JFrog has a validated market position.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-jfrog-company-position-1",
                        text="JFrog has market evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-competitor-company-position-1",
                        text="Sonatype has market evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="market_context",
                title="Market And Strategic Context",
                claims=(
                    ReportClaim(
                        id="market-context-thesis",
                        text="JFrog has a market story tied to platform breadth.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-buyer-segment-1",
                        text="Platform buyers care about consolidation.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-gtm-motion-1",
                        text="GTM evidence is present.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-ecosystem-signal-1",
                        text="Ecosystem evidence is present.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="market-risk-1",
                        text="No recent data found for market share.",
                        evidence_ids=("ev1",),
                        confidence="low",
                    ),
                ),
            ),
            ReportSection(
                id="technical_teardown",
                title="Technical And Feature Teardown",
                claims=(
                    ReportClaim(
                        id="technical-teardown-thesis",
                        text="JFrog has a technical platform story.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="technical-jfrog-capability-1",
                        text="JFrog has capability evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="technical-competitor-capability-1",
                        text="Sonatype has capability evidence.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="technical-architecture-workflow-1",
                        text="Workflow evidence is present.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="technical-ai-artifact-governance-1",
                        text="AI governance evidence is present.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="supply_chain_security",
                title="Supply Chain Security Coverage",
                claims=(
                    ReportClaim(
                        id="technical-security-comparison-1",
                        text="JFrog has a security story tied to artifact governance.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                    ReportClaim(
                        id="technical-risk-1",
                        text="No recent data found for independent benchmark outcomes.",
                        evidence_ids=("ev1",),
                        confidence="low",
                    ),
                ),
            ),
            ReportSection(
                id="buyer_fit",
                title="Buyer Fit Matrix",
                claims=(
                    ReportClaim(
                        id="buyer-fit-thesis",
                        text="Source: the current section uses buyer-fit material.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
            ReportSection(
                id="field_battlecard",
                title="JFrog Field Battlecard",
                claims=(
                    ReportClaim(
                        id="field-battlecard-thesis",
                        text="JFrog has a field story tied to platform breadth.",
                        evidence_ids=("ev1",),
                        confidence="medium",
                    ),
                ),
            ),
        ),
    )

    validation = check_report(pack, draft)

    assert any(finding.code == "source_list_prose_in_buyer_field" for finding in validation.findings)
    assert any(finding.code == "missing_buyer_field_claim_group" for finding in validation.findings)
