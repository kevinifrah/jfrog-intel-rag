from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any, Protocol

from pydantic import ValidationError

from ci_engine.config import get as config_get
from ci_engine.crews.report.analysts import AGENT_DISPLAY_NAMES
from ci_engine.crews.report.crew import REPORT_AGENT_SKILLS, _ensure_crewai_storage, load_agent_skill
from ci_engine.crews.report.readiness import select_best_evidence
from ci_engine.crews.report.schemas import (
    BuyerFieldAnalysis,
    BuyerFieldClaim,
    EvidenceItem,
    EvidencePack,
    ReportClaim,
    ReportSection,
)
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret

BUYER_FIELD_SECTIONS = (
    "buyer_fit",
    "field_battlecard",
    "company_snapshot",
    "market_context",
    "technical_teardown",
    "supply_chain_security",
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


class BuyerFieldGenerationError(ValueError):
    pass


class BuyerFieldRunner(Protocol):
    def __call__(self, prompt: str) -> str | dict[str, Any] | BuyerFieldAnalysis:
        ...


def build_buyer_field_prompt_input(
    evidence_pack: EvidencePack,
    *,
    max_items: int = 56,
) -> dict[str, Any]:
    items = _curated_buyer_field_items(evidence_pack, max_items=max_items)
    allowed_evidence_ids = [item.id for item in items]
    gaps = [
        gap.model_dump(mode="json")
        for gap in evidence_pack.gaps
        if gap.report_section in BUYER_FIELD_SECTIONS
    ][:32]
    readiness = []
    if evidence_pack.readiness is not None:
        readiness = [
            section.model_dump(mode="json")
            for section in evidence_pack.readiness.sections
            if section.section_id in BUYER_FIELD_SECTIONS
        ]
    inventory = []
    if evidence_pack.inventory is not None:
        inventory = [
            summary.model_dump(mode="json")
            for summary in evidence_pack.inventory.summaries
        ]
    return {
        "task": "buyer_field_analyst_buyer_fit_and_field_battlecard",
        "report_voice": "field-ready executive competitive intelligence dossier",
        "jfrog": evidence_pack.jfrog,
        "competitor": evidence_pack.competitor,
        "evidence_pack_id": evidence_pack.id,
        "allowed_evidence_ids": allowed_evidence_ids,
        "evidence": [_buyer_field_evidence_record(item) for item in items],
        "readiness": readiness,
        "gaps": gaps,
        "source_inventory": inventory,
        "quality_notes": list(evidence_pack.quality_notes),
        "requirements": {
            "write_for": "field leaders, account teams, solution engineers, product marketing, and sales leadership",
            "style": (
                "Field-ready competitive intelligence: direct, buyer-centered, practical, "
                "honest about evidence limits, and free of audit-trail mechanics."
            ),
            "must_include": [
                "buyer fit thesis",
                "where JFrog wins",
                "where the competitor wins",
                "field battlecard thesis",
                "objection handling",
                "discovery questions",
                "qualify-out signals",
                "field actions",
                "confidence notes",
            ],
            "must_not_include": [
                "inline evidence IDs or bracket citations in text",
                "source numbers in text",
                "Evidence: lines",
                "raw source inventory prose",
                "source domains unless commercially material",
                "ontology keys or metadata names",
                "uncited claims",
                "unverified win-rate, pricing, customer-count, or displacement claims",
                "generic sales advice detached from the evidence",
            ],
            "good_style_example": (
                "JFrog should qualify hard for buyers who want one operating layer for "
                "artifact management, governance, and release trust; Sonatype is more dangerous "
                "when the deal is scoped narrowly around open-source governance, firewall blocking, "
                "and AppSec-owned SCA controls."
            ),
        },
    }


def build_buyer_field_prompt(evidence_pack: EvidencePack) -> str:
    payload = build_buyer_field_prompt_input(evidence_pack)
    schema = BuyerFieldAnalysis.model_json_schema()
    skill = load_agent_skill("buyer_field_analyst")
    return (
        f"{skill}\n\n"
        "Write the buyer_fit and field_battlecard sections only.\n"
        "Return one strict JSON object and no markdown.\n"
        "Every claim must cite one or more IDs from allowed_evidence_ids.\n"
        "Put evidence IDs only in JSON evidence_ids fields. Never put IDs or "
        "bracket citations inside text fields.\n"
        "Use field competitive-intelligence language, not source-list language. Do not say phrases like "
        "'Evidence:', 'Source:', 'current section uses', 'source types led by', or 'key support'.\n"
        "Do not mention source paths, ontology keys, tags, keywords, or metadata.\n"
        "Do not infer win rates, pricing advantage, displacement, customer counts, or buyer outcomes "
        "unless directly supported by cited evidence. If a fact is weak or absent, lower confidence "
        "or use the exact phrase 'no recent data found'.\n\n"
        "JSON_SCHEMA:\n"
        f"{json.dumps(schema, ensure_ascii=True, sort_keys=True)}\n\n"
        "PAYLOAD_JSON:\n"
        f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"
    )


def run_buyer_field_analysis(
    evidence_pack: EvidencePack,
    *,
    runner: BuyerFieldRunner | None = None,
) -> BuyerFieldAnalysis:
    buyer_field_runner = runner or CrewAIBuyerFieldRunner()
    prompt = build_buyer_field_prompt(evidence_pack)
    allowed_ids = set(build_buyer_field_prompt_input(evidence_pack)["allowed_evidence_ids"])
    errors: list[str] = []
    raw_output: str | dict[str, Any] | BuyerFieldAnalysis | None = None

    for attempt in range(2):
        try:
            if attempt == 0:
                raw_output = buyer_field_runner(prompt)
            else:
                raw_output = buyer_field_runner(_repair_prompt(prompt, raw_output, errors[-1]))
        except Exception as exc:
            errors.append(f"buyer/field runner failed: {exc}")
            continue
        try:
            return parse_buyer_field_analysis(raw_output, allowed_evidence_ids=allowed_ids)
        except BuyerFieldGenerationError as exc:
            errors.append(str(exc))

    raise BuyerFieldGenerationError("; ".join(errors))


def parse_buyer_field_analysis(
    output: str | Mapping[str, Any] | BuyerFieldAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> BuyerFieldAnalysis:
    try:
        if isinstance(output, BuyerFieldAnalysis):
            analysis = output
        elif isinstance(output, Mapping):
            analysis = BuyerFieldAnalysis.model_validate(dict(output))
        else:
            parsed = parse_json_object(str(output), label="buyer/field analyst")
            analysis = BuyerFieldAnalysis.model_validate(parsed)
    except (ValidationError, ValueError, TypeError) as exc:
        raise BuyerFieldGenerationError(str(exc)) from exc

    _validate_buyer_field_citations(analysis, allowed_evidence_ids=allowed_evidence_ids)
    _validate_buyer_field_language(analysis)
    return analysis


def buyer_field_analysis_to_sections(
    evidence_pack: EvidencePack,
    analysis: BuyerFieldAnalysis,
) -> tuple[ReportSection, ReportSection]:
    buyer_claims = [
        _report_claim("buyer-fit-thesis", analysis.buyer_fit_thesis),
        *_bucket_claims("buyer-jfrog-win-condition", analysis.jfrog_win_conditions),
        *_bucket_claims(
            "buyer-competitor-win-condition",
            analysis.competitor_win_conditions,
        ),
        *_bucket_claims("buyer-qualify-out-signal", analysis.qualify_out_signals),
    ]
    field_claims = [
        _report_claim("field-battlecard-thesis", analysis.field_battlecard_thesis),
        *_bucket_claims("field-objection-handling", analysis.objection_handling),
        *_bucket_claims("field-discovery-question", analysis.discovery_questions),
        *_bucket_claims("field-action", analysis.field_actions),
    ]
    confidence = " ".join(_presentation_text(note) for note in analysis.confidence_notes)
    return (
        ReportSection(
            id="buyer_fit",
            title="Buyer Fit Matrix",
            agent_key="buyer_field_analyst",
            agent_name=AGENT_DISPLAY_NAMES["buyer_field_analyst"],
            skill_name=REPORT_AGENT_SKILLS["buyer_field_analyst"],
            evidence_ids=tuple(_unique_claim_evidence(buyer_claims)),
            claims=tuple(buyer_claims),
            narrative=f"Buyer/Field Analyst buyer-fit synthesis. Confidence notes: {confidence}",
        ),
        ReportSection(
            id="field_battlecard",
            title="JFrog Field Battlecard",
            agent_key="buyer_field_analyst",
            agent_name=AGENT_DISPLAY_NAMES["buyer_field_analyst"],
            skill_name=REPORT_AGENT_SKILLS["buyer_field_analyst"],
            evidence_ids=tuple(_unique_claim_evidence(field_claims)),
            claims=tuple(field_claims),
            narrative=f"Buyer/Field Analyst field-battlecard synthesis. Confidence notes: {confidence}",
        ),
    )


class CrewAIBuyerFieldRunner:
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
            role="Buyer/Field Analyst",
            goal="Create trustworthy buyer-fit and field battlecard sections for JFrog competitive intelligence.",
            backstory=load_agent_skill("buyer_field_analyst"),
            llm=llm,
            allow_delegation=False,
            verbose=True,
            memory=False,
        )
        task = Task(
            description=prompt,
            expected_output="A strict JSON object matching the BuyerFieldAnalysis schema.",
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


def _curated_buyer_field_items(
    evidence_pack: EvidencePack,
    *,
    max_items: int,
) -> list[EvidenceItem]:
    chosen: list[EvidenceItem] = []
    for section_id in BUYER_FIELD_SECTIONS:
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
                    limit=3,
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


def _buyer_field_evidence_record(item: EvidenceItem) -> dict[str, Any]:
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
    }


def _validate_buyer_field_citations(
    analysis: BuyerFieldAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> None:
    unknown_ids = sorted(
        {
            evidence_id
            for claim in _buyer_field_claims(analysis)
            for evidence_id in claim.evidence_ids
            if evidence_id not in allowed_evidence_ids
        }
    )
    if unknown_ids:
        raise BuyerFieldGenerationError(
            "buyer/field analyst cited evidence outside the curated EvidencePack slice: "
            + ", ".join(unknown_ids)
        )


def _validate_buyer_field_language(analysis: BuyerFieldAnalysis) -> None:
    bad_claims = [
        claim.text
        for claim in [
            *_buyer_field_claims(analysis),
            *(
                BuyerFieldClaim(text=note, evidence_ids=("confidence-note",), confidence="medium")
                for note in analysis.confidence_notes
            ),
        ]
        if _contains_source_list_prose(claim.text)
    ]
    if bad_claims:
        raise BuyerFieldGenerationError(
            "buyer/field analyst returned source-list prose instead of field CI synthesis"
        )


def _buyer_field_claims(analysis: BuyerFieldAnalysis) -> list[BuyerFieldClaim]:
    return [
        analysis.buyer_fit_thesis,
        *analysis.jfrog_win_conditions,
        *analysis.competitor_win_conditions,
        analysis.field_battlecard_thesis,
        *analysis.objection_handling,
        *analysis.discovery_questions,
        *analysis.qualify_out_signals,
        *analysis.field_actions,
    ]


def _bucket_claims(prefix: str, claims: Sequence[BuyerFieldClaim]) -> list[ReportClaim]:
    return [
        _report_claim(f"{prefix}-{index}", claim)
        for index, claim in enumerate(claims, start=1)
    ]


def _report_claim(claim_id: str, claim: BuyerFieldClaim) -> ReportClaim:
    return ReportClaim(
        id=claim_id,
        text=_presentation_text(claim.text),
        evidence_ids=claim.evidence_ids,
        confidence=claim.confidence,
        claim_type="analysis",
    )


def _unique_claim_evidence(claims: Sequence[ReportClaim]) -> list[str]:
    seen: set[str] = set()
    evidence_ids: list[str] = []
    for claim in claims:
        for evidence_id in claim.evidence_ids:
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            evidence_ids.append(evidence_id)
    return evidence_ids


def _repair_prompt(
    original_prompt: str,
    raw_output: str | dict[str, Any] | BuyerFieldAnalysis | None,
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
        "official deep-research slices": "recent research material",
        "official deep research slices": "recent research material",
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
    "BUYER_FIELD_SECTIONS",
    "BuyerFieldGenerationError",
    "BuyerFieldRunner",
    "CrewAIBuyerFieldRunner",
    "build_buyer_field_prompt",
    "build_buyer_field_prompt_input",
    "buyer_field_analysis_to_sections",
    "parse_buyer_field_analysis",
    "run_buyer_field_analysis",
]
