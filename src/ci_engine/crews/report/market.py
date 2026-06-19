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
    EvidenceItem,
    EvidencePack,
    MarketAnalysis,
    MarketClaim,
    ReportClaim,
    ReportSection,
)
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret
from ci_engine.skills import load_skill

MARKET_SECTIONS = (
    "company_snapshot",
    "market_context",
    "buyer_fit",
    "field_battlecard",
)

# Analyst frameworks the Market Analyst also produces (taught via these skills,
# emitted into the optional MarketAnalysis framework fields).
# report-market-cross-report MUST be first — it defines canonical axes and baselines
# that the per-framework skills below must follow to ensure cross-report comparability.
MARKET_FRAMEWORK_SKILLS = (
    "report-market-cross-report",
    "report-framework-pestel",
    "report-framework-five-forces",
    "report-framework-positioning-map",
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


class MarketGenerationError(ValueError):
    pass


class MarketRunner(Protocol):
    def __call__(self, prompt: str) -> str | dict[str, Any] | MarketAnalysis:
        ...


def build_market_prompt_input(
    evidence_pack: EvidencePack,
    *,
    max_items: int = 42,
) -> dict[str, Any]:
    items = _curated_market_items(evidence_pack, max_items=max_items)
    allowed_evidence_ids = [item.id for item in items]
    gaps = [
        gap.model_dump(mode="json")
        for gap in evidence_pack.gaps
        if gap.report_section in MARKET_SECTIONS
    ][:24]
    readiness = []
    if evidence_pack.readiness is not None:
        readiness = [
            section.model_dump(mode="json")
            for section in evidence_pack.readiness.sections
            if section.section_id in MARKET_SECTIONS
        ]
    inventory = []
    if evidence_pack.inventory is not None:
        inventory = [
            summary.model_dump(mode="json")
            for summary in evidence_pack.inventory.summaries
        ]
    return {
        "task": "market_analyst_company_and_market_sections",
        "report_voice": "executive competitive intelligence dossier",
        "jfrog": evidence_pack.jfrog,
        "competitor": evidence_pack.competitor,
        "evidence_pack_id": evidence_pack.id,
        "allowed_evidence_ids": allowed_evidence_ids,
        "evidence": [_market_evidence_record(item) for item in items],
        "readiness": readiness,
        "gaps": gaps,
        "source_inventory": inventory,
        "quality_notes": list(evidence_pack.quality_notes),
        "requirements": {
            "write_for": "executive, product, sales, and market leadership",
            "style": (
                "Market intelligence for senior leaders: concise, commercially useful, "
                "buyer-centered, and free of audit-trail mechanics."
            ),
            "must_include": [
                "company snapshot thesis",
                "JFrog business and market position",
                "competitor business and market position",
                "market context thesis",
                "target buyer and ICP implications",
                "go-to-market motion",
                "ecosystem and partnership signals",
                "market risks or open questions",
                "confidence notes",
            ],
            "must_not_include": [
                "inline evidence IDs or bracket citations in text",
                "source numbers in text",
                "Evidence: lines",
                "raw source inventory prose",
                "source domains unless strategically material",
                "ontology keys or metadata names",
                "uncited claims",
                "market share unless directly supported by cited evidence",
                "analyst ranking or placement unless directly supported",
            ],
            "good_style_example": (
                "JFrog should position the market conversation around consolidation of "
                "software delivery controls, while Sonatype should be expected to defend "
                "security-led evaluations by emphasizing open-source governance depth and "
                "repository firewall control points."
            ),
        },
    }


def build_market_prompt(evidence_pack: EvidencePack) -> str:
    payload = build_market_prompt_input(evidence_pack)
    schema = MarketAnalysis.model_json_schema()
    skill = load_agent_skill("market_analyst")
    frameworks = "\n\n".join(load_skill(name) for name in MARKET_FRAMEWORK_SKILLS)
    return (
        f"{skill}\n\n"
        f"{frameworks}\n\n"
        "Write the company_snapshot and market_context sections only.\n"
        "Also populate the pestel, five_forces, and positioning_map fields per the framework skills above. "
        "For the positioning_map, you MUST use the canonical axes defined in the cross-report skill: "
        "x_axis_label='Supply-chain coverage breadth', y_axis_label='Security specialization depth'. "
        "Do not invent different axes — these are fixed across all dossiers so reports are comparable. "
        "For five_forces, start from the baseline intensities in the cross-report skill and adjust only "
        "when your evidence strongly supports a different rating. "
        "If the EvidencePack cannot support a framework, return it empty rather than inventing data.\n"
        "Return one strict JSON object and no markdown.\n"
        "Every claim must cite one or more IDs from allowed_evidence_ids.\n"
        "Put evidence IDs only in JSON evidence_ids fields. Never put IDs or "
        "bracket citations inside text fields.\n"
        "Use market-intelligence language, not source-list language. Do not say phrases like "
        "'Evidence:', 'Source:', 'current section uses', 'source types led by', or 'key support'.\n"
        "Do not mention source paths, ontology keys, tags, keywords, or metadata.\n"
        "Do not infer market share, analyst placement, revenue, win rates, or customer counts "
        "unless the claim is directly supported by cited evidence. If a fact is weak or absent, "
        "lower confidence or use the exact phrase 'no recent data found'.\n\n"
        "JSON_SCHEMA:\n"
        f"{json.dumps(schema, ensure_ascii=True, sort_keys=True)}\n\n"
        "PAYLOAD_JSON:\n"
        f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"
    )


def run_market_analysis(
    evidence_pack: EvidencePack,
    *,
    runner: MarketRunner | None = None,
) -> MarketAnalysis:
    market_runner = runner or CrewAIMarketRunner()
    prompt = build_market_prompt(evidence_pack)
    allowed_ids = set(build_market_prompt_input(evidence_pack)["allowed_evidence_ids"])
    errors: list[str] = []
    raw_output: str | dict[str, Any] | MarketAnalysis | None = None

    for attempt in range(2):
        try:
            if attempt == 0:
                raw_output = market_runner(prompt)
            else:
                raw_output = market_runner(_repair_prompt(prompt, raw_output, errors[-1]))
        except Exception as exc:
            errors.append(f"market runner failed: {exc}")
            continue
        try:
            return parse_market_analysis(raw_output, allowed_evidence_ids=allowed_ids)
        except MarketGenerationError as exc:
            errors.append(str(exc))

    raise MarketGenerationError("; ".join(errors))


def parse_market_analysis(
    output: str | Mapping[str, Any] | MarketAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> MarketAnalysis:
    try:
        if isinstance(output, MarketAnalysis):
            analysis = output
        elif isinstance(output, Mapping):
            analysis = MarketAnalysis.model_validate(dict(output))
        else:
            parsed = parse_json_object(str(output), label="market analyst")
            analysis = MarketAnalysis.model_validate(parsed)
    except (ValidationError, ValueError, TypeError) as exc:
        raise MarketGenerationError(str(exc)) from exc

    _validate_market_citations(analysis, allowed_evidence_ids=allowed_evidence_ids)
    _validate_market_language(analysis)
    return analysis


def market_analysis_to_sections(
    evidence_pack: EvidencePack,
    analysis: MarketAnalysis,
) -> tuple[ReportSection, ReportSection]:
    company_claims = [
        _report_claim("market-company-snapshot-thesis", analysis.company_snapshot_thesis),
        *_bucket_claims("market-jfrog-company-position", analysis.jfrog_company_position),
        *_bucket_claims(
            "market-competitor-company-position",
            analysis.competitor_company_position,
        ),
    ]
    market_claims = [
        _report_claim("market-context-thesis", analysis.market_context_thesis),
        *_bucket_claims("market-buyer-segment", analysis.buyer_segments),
        *_bucket_claims("market-gtm-motion", analysis.go_to_market_motion),
        *_bucket_claims("market-ecosystem-signal", analysis.ecosystem_signals),
        *_bucket_claims("market-risk", analysis.market_risks),
    ]
    confidence = " ".join(_presentation_text(note) for note in analysis.confidence_notes)
    return (
        ReportSection(
            id="company_snapshot",
            title="Company Snapshot",
            agent_key="market_analyst",
            agent_name=AGENT_DISPLAY_NAMES["market_analyst"],
            skill_name=REPORT_AGENT_SKILLS["market_analyst"],
            evidence_ids=tuple(_unique_claim_evidence(company_claims)),
            claims=tuple(company_claims),
            narrative=f"Market Analyst business-position synthesis. Confidence notes: {confidence}",
        ),
        ReportSection(
            id="market_context",
            title="Market And Strategic Context",
            agent_key="market_analyst",
            agent_name=AGENT_DISPLAY_NAMES["market_analyst"],
            skill_name=REPORT_AGENT_SKILLS["market_analyst"],
            evidence_ids=tuple(_unique_claim_evidence(market_claims)),
            claims=tuple(market_claims),
            narrative=f"Market Analyst market-context synthesis. Confidence notes: {confidence}",
            metadata=_market_framework_metadata(analysis),
        ),
    )


def _market_framework_metadata(analysis: MarketAnalysis) -> dict[str, Any]:
    """Stash optional analyst frameworks into section metadata (renderer reads them)."""
    meta: dict[str, Any] = {}
    if analysis.pestel:
        meta["pestel"] = [
            {
                "axis": factor.axis,
                "factor": _presentation_text(factor.factor),
                "implication": _presentation_text(factor.implication),
                "material": factor.material,
                "evidence_ids": list(factor.evidence_ids),
            }
            for factor in analysis.pestel
        ]
    if analysis.five_forces:
        meta["five_forces"] = [
            {
                "force": force.force,
                "intensity": force.intensity,
                "rationale": _presentation_text(force.rationale),
                "evidence_ids": list(force.evidence_ids),
            }
            for force in analysis.five_forces
        ]
    if analysis.positioning_map is not None:
        pmap = analysis.positioning_map
        meta["positioning_map"] = {
            "x_axis_label": _presentation_text(pmap.x_axis_label),
            "x_low_label": _presentation_text(pmap.x_low_label),
            "x_high_label": _presentation_text(pmap.x_high_label),
            "y_axis_label": _presentation_text(pmap.y_axis_label),
            "y_low_label": _presentation_text(pmap.y_low_label),
            "y_high_label": _presentation_text(pmap.y_high_label),
            "narrative": _presentation_text(pmap.narrative or ""),
            "players": [
                {
                    "name": player.name,
                    "x": player.x,
                    "y": player.y,
                    "group": player.group,
                    "is_focus": player.is_focus,
                    "evidence_ids": list(player.evidence_ids),
                }
                for player in pmap.players
            ],
        }
    return meta


def _market_framework_evidence_ids(analysis: MarketAnalysis) -> list[str]:
    ids: list[str] = []
    for factor in analysis.pestel:
        ids.extend(factor.evidence_ids)
    for force in analysis.five_forces:
        ids.extend(force.evidence_ids)
    if analysis.positioning_map is not None:
        for player in analysis.positioning_map.players:
            ids.extend(player.evidence_ids)
    return ids


class CrewAIMarketRunner:
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
            role="Market Analyst",
            goal="Create trustworthy company and market-context sections for JFrog competitive intelligence.",
            backstory=load_agent_skill("market_analyst"),
            llm=llm,
            allow_delegation=False,
            verbose=True,
            memory=False,
        )
        task = Task(
            description=prompt,
            expected_output="A strict JSON object matching the MarketAnalysis schema.",
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


def _curated_market_items(
    evidence_pack: EvidencePack,
    *,
    max_items: int,
) -> list[EvidenceItem]:
    chosen: list[EvidenceItem] = []
    for section_id in MARKET_SECTIONS:
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
                    limit=1,
                )
            )
            chosen.extend(
                select_best_evidence(
                    [item for item in section_company_items if item.tier == "supporting"],
                    limit=1,
                )
            )
    return _dedupe_items(chosen)[:max_items]


def _market_evidence_record(item: EvidenceItem) -> dict[str, Any]:
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


def _validate_market_citations(
    analysis: MarketAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> None:
    claim_ids = (
        evidence_id
        for claim in _market_claims(analysis)
        for evidence_id in claim.evidence_ids
    )
    unknown_ids = sorted(
        {
            evidence_id
            for evidence_id in (*claim_ids, *_market_framework_evidence_ids(analysis))
            if evidence_id not in allowed_evidence_ids
        }
    )
    if unknown_ids:
        raise MarketGenerationError(
            "market analyst cited evidence outside the curated EvidencePack slice: "
            + ", ".join(unknown_ids)
        )


def _validate_market_language(analysis: MarketAnalysis) -> None:
    bad_claims = [
        claim.text
        for claim in [
            *_market_claims(analysis),
            *(
                MarketClaim(text=note, evidence_ids=("confidence-note",), confidence="medium")
                for note in analysis.confidence_notes
            ),
        ]
        if _contains_source_list_prose(claim.text)
    ]
    if bad_claims:
        raise MarketGenerationError(
            "market analyst returned source-list prose instead of market intelligence synthesis"
        )


def _market_claims(analysis: MarketAnalysis) -> list[MarketClaim]:
    return [
        analysis.company_snapshot_thesis,
        *analysis.jfrog_company_position,
        *analysis.competitor_company_position,
        analysis.market_context_thesis,
        *analysis.buyer_segments,
        *analysis.go_to_market_motion,
        *analysis.ecosystem_signals,
        *analysis.market_risks,
    ]


def _bucket_claims(prefix: str, claims: Sequence[MarketClaim]) -> list[ReportClaim]:
    return [
        _report_claim(f"{prefix}-{index}", claim)
        for index, claim in enumerate(claims, start=1)
    ]


def _report_claim(claim_id: str, claim: MarketClaim) -> ReportClaim:
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
    raw_output: str | dict[str, Any] | MarketAnalysis | None,
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
_AUDIT_LABEL_RE = re.compile(r"^\s*(?:[-*•·]\s*)?(?:evidence|sources?)\s*:", re.IGNORECASE | re.MULTILINE)


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
    "CrewAIMarketRunner",
    "MARKET_SECTIONS",
    "MarketGenerationError",
    "MarketRunner",
    "build_market_prompt",
    "build_market_prompt_input",
    "market_analysis_to_sections",
    "parse_market_analysis",
    "run_market_analysis",
]
