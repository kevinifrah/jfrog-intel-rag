from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
import re
from typing import Any, Protocol

from pydantic import ValidationError

from ci_engine.config import get as config_get
from ci_engine.crews.report.analysts import AGENT_DISPLAY_NAMES
from ci_engine.crews.report.capabilities import capability_evidence_ids
from ci_engine.crews.report.crew import REPORT_AGENT_SKILLS, _ensure_crewai_storage, load_agent_skill
from ci_engine.crews.report.readiness import select_best_evidence
from ci_engine.crews.report.schemas import (
    EvidenceItem,
    EvidencePack,
    ProductFeatureAnalysis,
    ProductFeatureCapability,
    ProductFeatureClaim,
    ReportClaim,
    ReportSection,
)
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret

PRODUCT_FEATURE_SECTIONS = (
    "product_feature_analysis",
    "technical_teardown",
    "supply_chain_security",
    "buyer_fit",
    "executive_summary",
)

SOURCE_LIST_PROSE_PATTERNS = (
    "current section uses",
    "source types led by",
    "key support:",
    "evidence:",
    "source:",
    "web validation contributes",
    "from the frozen evidencepack",
)


class ProductFeatureGenerationError(ValueError):
    pass


class ProductFeatureRunner(Protocol):
    def __call__(self, prompt: str) -> str | dict[str, Any] | ProductFeatureAnalysis:
        ...


def build_product_feature_prompt_input(
    evidence_pack: EvidencePack,
    *,
    max_items: int = 64,
) -> dict[str, Any]:
    all_items = _curated_product_feature_items(evidence_pack, max_items=max_items)
    all_items = _with_capability_evidence_items(evidence_pack, all_items)
    items = all_items[:max_items]
    allowed_evidence_ids = [item.id for item in items]
    allowed_id_set = set(allowed_evidence_ids)
    gaps = [
        gap.model_dump(mode="json")
        for gap in evidence_pack.gaps
        if gap.report_section in PRODUCT_FEATURE_SECTIONS
    ][:36]
    readiness = []
    if evidence_pack.readiness is not None:
        readiness = [
            section.model_dump(mode="json")
            for section in evidence_pack.readiness.sections
            if section.section_id in PRODUCT_FEATURE_SECTIONS
        ]
    inventory = []
    if evidence_pack.inventory is not None:
        inventory = [
            summary.model_dump(mode="json")
            for summary in evidence_pack.inventory.summaries
        ]
    return {
        "task": "product_feature_analyst_product_feature_analysis",
        "report_voice": "executive product competitive intelligence dossier",
        "jfrog": evidence_pack.jfrog,
        "competitor": evidence_pack.competitor,
        "evidence_pack_id": evidence_pack.id,
        "allowed_evidence_ids": allowed_evidence_ids,
        "evidence": [_product_feature_evidence_record(item) for item in items],
        "product_catalog": [
            _product_catalog_prompt_record(item, allowed_id_set)
            for item in evidence_pack.product_catalog
        ],
        "capability_evidence_matrix": (
            _capability_matrix_prompt_record(evidence_pack, allowed_id_set)
            if evidence_pack.capability_matrix is not None
            else None
        ),
        "readiness": readiness,
        "gaps": gaps,
        "source_inventory": inventory,
        "quality_notes": list(evidence_pack.quality_notes),
        "requirements": {
            "write_for": "C-level, product, security, architecture, and field leadership",
            "style": (
                "Neutral product competitive intelligence: structured, buyer-useful, concise, "
                "evidence-grounded, adversarially honest, and free of audit-trail mechanics."
            ),
            "must_include": [
                "product-feature thesis",
                "product-specific comparisons using the supplied product_catalog",
                "capability matrix with at least six rows",
                "JFrog feature advantages",
                "competitor feature advantages",
                "JFrog limitations, exposures, or places where the competitor is stronger",
                "feature parity, gaps, or unclear areas",
                "buyer implications",
                "confidence notes",
            ],
            "capability_matrix_guidance": [
                "Use capability_evidence_matrix as the starting truth table.",
                "Use standard comparison-table structure: capability as row, companies as columns.",
                "Keep cell text short and parallel across vendors.",
                "Use business-readable capability labels, not ontology keys.",
                "Name concrete products when product_catalog and evidence support them.",
                "Prefer meaningful buyer attributes over exhaustive metadata.",
                "Mark unclear only when the capability_evidence_matrix is unresolved after targeted search.",
                "Mark competitor_advantage when the competitor evidence is more specific, closer to the buyer workflow, more directly productized, or materially stronger than JFrog's evidence.",
                "Do not hide competitor wins as parity just because JFrog has an adjacent capability.",
                "For must-resolve capabilities, do not write 'no recent data found' if the supplied matrix contains supported evidence.",
            ],
            "must_not_include": [
                "inline evidence IDs or bracket citations in text",
                "source numbers in text",
                "Evidence: lines",
                "raw source inventory prose",
                "source domains unless materially relevant",
                "ontology keys or metadata names",
                "uncited claims",
                "unsupported feature superiority",
                "benchmarks, detection accuracy, package counts, or coverage outcomes unless directly supported",
            ],
            "good_style_example": (
                "JFrog is stronger when the buyer prioritizes one artifact-centered control plane, "
                "but Sonatype can be stronger when the decision narrows to open-source admission "
                "control, repository firewall enforcement, and malware-defense controls. Treat both "
                "statements as conditional, not as a vendor preference."
            ),
        },
    }


def build_product_feature_prompt(evidence_pack: EvidencePack) -> str:
    payload = build_product_feature_prompt_input(evidence_pack)
    schema = ProductFeatureAnalysis.model_json_schema()
    skill = load_agent_skill("product_feature_analyst")
    return (
        f"{skill}\n\n"
        "Write the product_feature_analysis section only.\n"
        "Return one strict JSON object and no markdown.\n"
        "Every claim and every capability_matrix row must cite one or more IDs from allowed_evidence_ids.\n"
        "Put evidence IDs only in JSON evidence_ids fields. Never put IDs or "
        "bracket citations inside text fields.\n"
        "Use product competitive-intelligence language, not source-list language. Do not say phrases like "
        "'Evidence:', 'Source:', 'current section uses', 'source types led by', or 'key support'.\n"
        "Do not mention the EvidencePack, source paths, ontology keys, tags, keywords, or metadata.\n"
        "Keep matrix cells short, parallel, and readable for executives.\n"
        "Use the supplied product_catalog and capability_evidence_matrix before writing the matrix. "
        "Capability rows marked supported or partially_supported should be treated as found evidence, not as unknown.\n"
        "Be neutral: include explicit JFrog limitations and mark real competitor wins. "
        "Do not describe JFrog as stronger unless the cited evidence supports that specific buyer scenario.\n"
        "Do not infer benchmarks, detection accuracy, package counts, product usage, coverage breadth, "
        "or feature superiority unless directly supported by cited evidence. If a fact is weak or absent, "
        "lower confidence or use the exact phrase 'no recent data found'.\n\n"
        "JSON_SCHEMA:\n"
        f"{json.dumps(schema, ensure_ascii=True, sort_keys=True)}\n\n"
        "PAYLOAD_JSON:\n"
        f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"
    )


def run_product_feature_analysis(
    evidence_pack: EvidencePack,
    *,
    runner: ProductFeatureRunner | None = None,
) -> ProductFeatureAnalysis:
    product_feature_runner = runner or CrewAIProductFeatureRunner()
    prompt = build_product_feature_prompt(evidence_pack)
    allowed_ids = set(build_product_feature_prompt_input(evidence_pack)["allowed_evidence_ids"])
    errors: list[str] = []
    raw_output: str | dict[str, Any] | ProductFeatureAnalysis | None = None

    for attempt in range(2):
        try:
            if attempt == 0:
                raw_output = product_feature_runner(prompt)
            else:
                raw_output = product_feature_runner(_repair_prompt(prompt, raw_output, errors[-1]))
        except Exception as exc:
            errors.append(f"product/feature runner failed: {exc}")
            continue
        try:
            return parse_product_feature_analysis(raw_output, allowed_evidence_ids=allowed_ids)
        except ProductFeatureGenerationError as exc:
            errors.append(str(exc))

    raise ProductFeatureGenerationError("; ".join(errors))


def parse_product_feature_analysis(
    output: str | Mapping[str, Any] | ProductFeatureAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> ProductFeatureAnalysis:
    try:
        if isinstance(output, ProductFeatureAnalysis):
            analysis = output
        elif isinstance(output, Mapping):
            analysis = ProductFeatureAnalysis.model_validate(dict(output))
        else:
            parsed = parse_json_object(str(output), label="product/feature analyst")
            analysis = ProductFeatureAnalysis.model_validate(parsed)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ProductFeatureGenerationError(str(exc)) from exc

    _validate_product_feature_citations(analysis, allowed_evidence_ids=allowed_evidence_ids)
    _validate_product_feature_language(analysis)
    return analysis


def product_feature_analysis_to_section(
    evidence_pack: EvidencePack,
    analysis: ProductFeatureAnalysis,
) -> ReportSection:
    claims = [
        _report_claim("product-feature-thesis", analysis.product_feature_thesis),
        *_bucket_claims("product-jfrog-advantage", analysis.jfrog_feature_advantages),
        *_bucket_claims(
            "product-competitor-advantage",
            analysis.competitor_feature_advantages,
        ),
        *_bucket_claims("product-jfrog-limitation", analysis.jfrog_limitations),
        *_bucket_claims("product-parity-gap", analysis.feature_parity_or_gaps),
        *_bucket_claims("product-buyer-implication", analysis.buyer_implications),
    ]
    confidence = " ".join(_presentation_text(note) for note in analysis.confidence_notes)
    return ReportSection(
        id="product_feature_analysis",
        title="Product And Feature Analysis",
        agent_key="product_feature_analyst",
        agent_name=AGENT_DISPLAY_NAMES["product_feature_analyst"],
        skill_name=REPORT_AGENT_SKILLS["product_feature_analyst"],
        evidence_ids=tuple(
            _unique_ids(
                [
                    *_unique_claim_evidence(claims),
                    *(
                        evidence_id
                        for row in analysis.capability_matrix
                        for evidence_id in row.evidence_ids
                    ),
                ]
            )
        ),
        claims=tuple(claims),
        narrative=f"Product/Feature Analyst capability synthesis. Confidence notes: {confidence}",
        metadata={
            "capability_matrix": [
                {
                    "capability": _presentation_text(row.capability),
                    "jfrog": _presentation_text(row.jfrog),
                    "competitor": _presentation_text(row.competitor),
                    "assessment": row.assessment,
                    "confidence": row.confidence,
                    "evidence_ids": list(row.evidence_ids),
                }
                for row in analysis.capability_matrix
            ],
            "product_catalog": [
                item.model_dump(mode="json") for item in evidence_pack.product_catalog
            ],
            "capability_evidence_matrix": (
                evidence_pack.capability_matrix.model_dump(mode="json")
                if evidence_pack.capability_matrix is not None
                else None
            ),
            "capability_evidence_gaps": (
                [
                    row.model_dump(mode="json")
                    for row in evidence_pack.capability_matrix.rows
                    if row.must_resolve
                    and row.search_status
                    in {
                        "not_found_after_search",
                        "unclear_needs_review",
                        "contradictory",
                    }
                ]
                if evidence_pack.capability_matrix is not None
                else []
            ),
        },
    )


class CrewAIProductFeatureRunner:
    def __call__(self, prompt: str) -> str:
        _ensure_crewai_storage()
        from crewai import Agent, Crew, LLM, Process, Task  # noqa: PLC0415

        model = str(config_get("models.report.name", "claude-sonnet-4-6"))
        llm_kwargs: dict[str, Any] = {
            "model": _crewai_model_name(model),
            "provider": "anthropic",
            "is_anthropic": True,
            "api_key": get_secret("anthropic-key"),
            "max_tokens": int(config_get("models.report.max_tokens", 6000)),
            "timeout": float(config_get("models.report.timeout_s", 180)),
        }
        if _uses_effort_model(model):
            llm_kwargs["reasoning_effort"] = str(
                config_get("models.report.thinking", "high")
            )
        else:
            llm_kwargs["temperature"] = float(config_get("models.report.temperature", 0.2))
        llm = LLM(**llm_kwargs)
        agent = Agent(
            role="Product/Feature Analyst",
            goal="Create a trustworthy product and feature comparison section for JFrog competitive intelligence.",
            backstory=load_agent_skill("product_feature_analyst"),
            llm=llm,
            allow_delegation=False,
            verbose=True,
            memory=False,
        )
        task = Task(
            description=prompt,
            expected_output="A strict JSON object matching the ProductFeatureAnalysis schema.",
            agent=agent,
        )
        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=True,
            memory=False,
            tracing=False,
        )
        return _crew_output_text(crew.kickoff())


def _curated_product_feature_items(
    evidence_pack: EvidencePack,
    *,
    max_items: int,
) -> list[EvidenceItem]:
    chosen: list[EvidenceItem] = []
    for section_id in PRODUCT_FEATURE_SECTIONS:
        for company in (evidence_pack.jfrog, evidence_pack.competitor):
            section_company_items = [
                item
                for item in evidence_pack.items
                if item.report_section == section_id
                and item.company.lower() == company.lower()
            ]
            chosen.extend(
                select_best_evidence(
                    [item for item in section_company_items if item.tier == "primary"],
                    limit=4,
                )
            )
            chosen.extend(
                select_best_evidence(
                    [item for item in section_company_items if item.tier == "validation"],
                    limit=2,
                )
            )
            chosen.extend(
                select_best_evidence(
                    [item for item in section_company_items if item.tier == "supporting"],
                    limit=1,
                )
            )
    return _dedupe_items(chosen)[:max_items]


def _with_capability_evidence_items(
    evidence_pack: EvidencePack,
    items: Sequence[EvidenceItem],
) -> list[EvidenceItem]:
    if evidence_pack.capability_matrix is None:
        return list(items)
    by_id = {item.id: item for item in evidence_pack.items}
    matrix_items = [
        by_id[evidence_id]
        for evidence_id in capability_evidence_ids(evidence_pack.capability_matrix)
        if evidence_id in by_id
    ]
    return _dedupe_items([*matrix_items, *items])


def _product_feature_evidence_record(item: EvidenceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "company": item.company,
        "report_section": item.report_section,
        "source": item.source,
        "tier": item.tier,
        "classification": item.classification,
        "confidence": item.confidence,
        "dimension": item.dimension,
        "title": item.title,
        "publisher": item.publisher,
        "url": item.url,
        "published": item.published.isoformat() if item.published else None,
        "summary": _short_text(item.summary or item.quote or ""),
        "source_kind": item.metadata.get("source_kind"),
        "source_quality_score": item.metadata.get("source_quality_score"),
        "capability_id": item.metadata.get("capability_id"),
        "capability_label": item.metadata.get("capability_label"),
    }


def _product_catalog_prompt_record(item: Any, allowed_ids: set[str]) -> dict[str, Any]:
    record = item.model_dump(mode="json")
    record["evidence_ids"] = [
        evidence_id for evidence_id in record.get("evidence_ids", []) if evidence_id in allowed_ids
    ]
    return record


def _capability_matrix_prompt_record(
    evidence_pack: EvidencePack,
    allowed_ids: set[str],
) -> dict[str, Any]:
    if evidence_pack.capability_matrix is None:
        return {}
    record = evidence_pack.capability_matrix.model_dump(mode="json")
    record["search_attempts"] = []
    for row in record.get("rows", []):
        row["evidence_ids"] = [
            evidence_id for evidence_id in row.get("evidence_ids", []) if evidence_id in allowed_ids
        ]
        for cell_key in ("jfrog", "competitor"):
            cell = row.get(cell_key)
            if not isinstance(cell, dict):
                continue
            cell["evidence_ids"] = [
                evidence_id
                for evidence_id in cell.get("evidence_ids", [])
                if evidence_id in allowed_ids
            ]
            cell["search_attempts"] = []
    return record


def _validate_product_feature_citations(
    analysis: ProductFeatureAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> None:
    unknown_ids = sorted(
        {
            evidence_id
            for claim in _product_feature_claims(analysis)
            for evidence_id in claim.evidence_ids
            if evidence_id not in allowed_evidence_ids
        }
        | {
            evidence_id
            for row in analysis.capability_matrix
            for evidence_id in row.evidence_ids
            if evidence_id not in allowed_evidence_ids
        }
    )
    if unknown_ids:
        raise ProductFeatureGenerationError(
            "product/feature analyst cited evidence outside the curated EvidencePack slice: "
            + ", ".join(unknown_ids)
        )


def _validate_product_feature_language(analysis: ProductFeatureAnalysis) -> None:
    text_parts = [
        claim.text
        for claim in [
            *_product_feature_claims(analysis),
            *(
                ProductFeatureClaim(text=note, evidence_ids=("confidence-note",), confidence="medium")
                for note in analysis.confidence_notes
            ),
        ]
    ]
    for row in analysis.capability_matrix:
        text_parts.extend((row.capability, row.jfrog, row.competitor))
    if any(_contains_source_list_prose(text) for text in text_parts):
        raise ProductFeatureGenerationError(
            "product/feature analyst returned source-list prose instead of product CI synthesis"
        )


def _product_feature_claims(analysis: ProductFeatureAnalysis) -> list[ProductFeatureClaim]:
    return [
        analysis.product_feature_thesis,
        *analysis.jfrog_feature_advantages,
        *analysis.competitor_feature_advantages,
        *analysis.jfrog_limitations,
        *analysis.feature_parity_or_gaps,
        *analysis.buyer_implications,
    ]


def _bucket_claims(prefix: str, claims: Sequence[ProductFeatureClaim]) -> list[ReportClaim]:
    return [
        _report_claim(f"{prefix}-{index}", claim)
        for index, claim in enumerate(claims, start=1)
    ]


def _report_claim(claim_id: str, claim: ProductFeatureClaim) -> ReportClaim:
    return ReportClaim(
        id=claim_id,
        text=_presentation_text(claim.text),
        evidence_ids=claim.evidence_ids,
        confidence=claim.confidence,
        claim_type="analysis",
    )


def _unique_claim_evidence(claims: Sequence[ReportClaim]) -> list[str]:
    return _unique_ids(
        evidence_id
        for claim in claims
        for evidence_id in claim.evidence_ids
    )


def _unique_ids(evidence_ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for evidence_id in evidence_ids:
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        unique.append(evidence_id)
    return unique


def _repair_prompt(
    original_prompt: str,
    raw_output: str | dict[str, Any] | ProductFeatureAnalysis | None,
    error: str,
) -> str:
    return (
        original_prompt
        + "\n\nREPAIR_REQUIRED:\n"
        + error
        + "\n\nPrevious output was invalid. Return a corrected strict JSON object only.\n"
        + "PREVIOUS_OUTPUT:\n"
        + _short_text(str(raw_output or ""), limit=4000)
    )


def _crew_output_text(output: Any) -> str:
    for attribute in ("raw", "json", "content"):
        value = getattr(output, attribute, None)
        if value:
            return str(value)
    json_dict = getattr(output, "json_dict", None)
    if json_dict:
        return json.dumps(json_dict, ensure_ascii=True, sort_keys=True)
    pydantic_output = getattr(output, "pydantic", None)
    if pydantic_output is not None:
        if hasattr(pydantic_output, "model_dump_json"):
            return str(pydantic_output.model_dump_json())
        return str(pydantic_output)
    return str(output)


def _crewai_model_name(model: str) -> str:
    return model.removeprefix("anthropic/")


def _uses_effort_model(model: str) -> bool:
    return "opus-4-8" in model or "sonnet-4-5" in model


def _dedupe_items(items: Sequence[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[str] = set()
    deduped: list[EvidenceItem] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        deduped.append(item)
    return deduped


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
    )


def _short_text(text: str, *, limit: int = 700) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0].rstrip() + "..."


_EVIDENCE_ID_RE = re.compile(r"\[[a-f0-9]{12,40}\]", re.IGNORECASE)
_SOURCE_NUMBER_RE = re.compile(r"\[(?:\d{1,2})(?:\s*,\s*\d{1,2})*\]")
_AUDIT_LABEL_RE = re.compile(r"(^|\s)(?:evidence|source|sources)\s*:", re.IGNORECASE)


def _presentation_text(text: str) -> str:
    replacements = {
        "win_loss_signals": "win/loss signals",
        "product_portfolio": "product portfolio",
        "software_composition_analysis": "software composition analysis",
        "artifact_management": "artifact management",
        "market_positioning": "market positioning",
        "target_segments_icp": "target segments and ICP",
        "gtm_motion": "go-to-market motion",
        "customers_case_studies": "customer case studies",
        "pricing_packaging": "pricing and packaging",
        "partnerships_ecosystem": "partnerships and ecosystem",
        "policy_governance": "policy and governance",
        "malicious_package_detection": "malicious package detection",
        "open_source_curation": "open source curation",
        "package_firewall": "package firewall",
        "license_compliance": "license compliance",
        "sbom_generation": "SBOM generation",
        "cve_contextual_analysis": "CVE contextual analysis",
        "reachability_analysis": "reachability analysis",
        "architecture_deployment_model": "architecture and deployment model",
        "ci_cd_ide_integrations": "CI/CD and IDE integrations",
        "ai_features": "AI capabilities",
        "official deep-research slices": "recent research material",
        "official deep research slices": "recent research material",
        "from the frozen EvidencePack": "from available evidence",
        "from the frozen evidencepack": "from available evidence",
    }
    cleaned = str(text or "")
    cleaned = _EVIDENCE_ID_RE.sub("", cleaned)
    cleaned = _SOURCE_NUMBER_RE.sub("", cleaned)
    cleaned = re.sub(r"\b(Evidence|Source|Sources)\s*:\s*", "", cleaned, flags=re.I)
    for raw, replacement in replacements.items():
        cleaned = cleaned.replace(raw, replacement)
    cleaned = cleaned.replace("official_llm_research_report", "research brief")
    cleaned = cleaned.replace("official llm research report", "research brief")
    return " ".join(cleaned.split())


__all__ = [
    "CrewAIProductFeatureRunner",
    "PRODUCT_FEATURE_SECTIONS",
    "ProductFeatureGenerationError",
    "ProductFeatureRunner",
    "build_product_feature_prompt",
    "build_product_feature_prompt_input",
    "parse_product_feature_analysis",
    "product_feature_analysis_to_section",
    "run_product_feature_analysis",
]
