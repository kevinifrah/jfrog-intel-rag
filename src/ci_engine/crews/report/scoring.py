from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any, Protocol

from pydantic import ValidationError

from ci_engine.config import get as config_get
from ci_engine.crews.report.capabilities import capability_evidence_ids
from ci_engine.crews.report.crew import _ensure_crewai_storage, load_agent_skill
from ci_engine.crews.report.readiness import select_best_evidence
from ci_engine.crews.report.schemas import (
    EvidenceItem,
    EvidencePack,
    ScoreItem,
    ScoringAnalysis,
)
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret

SCORING_SECTIONS = (
    "executive_summary",
    "market_context",
    "product_feature_analysis",
    "technical_teardown",
    "supply_chain_security",
    "buyer_fit",
    "field_battlecard",
    "scoring",
)

SCORING_CATEGORIES: tuple[dict[str, object], ...] = (
    {
        "category": "Platform Consolidation Fit",
        "buyer_archetype": "Platform engineering and DevOps leaders consolidating artifact, release, and governance workflows.",
        "weight": 0.30,
        "definition": "Rewards breadth, artifact control plane depth, integrations, deployment flexibility, and platform operating model.",
    },
    {
        "category": "Open Source Governance Fit",
        "buyer_archetype": "AppSec and governance leaders focused on open-source policy, admission control, licenses, and package risk.",
        "weight": 0.25,
        "definition": "Rewards SCA, policy/license governance, curation, repository firewall, and pre-ingest package controls.",
    },
    {
        "category": "Security Prioritization Fit",
        "buyer_archetype": "Security teams prioritizing exploitable risk, malicious packages, reachability, CVE context, and SBOM workflows.",
        "weight": 0.25,
        "definition": "Rewards malicious-package detection, reachability, CVE context, vulnerability prioritization, and SBOM evidence.",
    },
    {
        "category": "Field Execution Fit",
        "buyer_archetype": "Sales, solution engineering, and procurement teams needing explainable differentiation and low-risk adoption.",
        "weight": 0.20,
        "definition": "Rewards buyer proof, implementation clarity, field narrative, qualify-out signals, and evidence confidence.",
    },
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


class ScoringGenerationError(ValueError):
    pass


class ScoringRunner(Protocol):
    def __call__(self, prompt: str) -> str | dict[str, Any] | ScoringAnalysis:
        ...


def build_scoring_prompt_input(
    evidence_pack: EvidencePack,
    *,
    max_items: int = 80,
) -> dict[str, Any]:
    items = _curated_scoring_items(evidence_pack, max_items=max_items)
    allowed_evidence_ids = [item.id for item in items]
    allowed_id_set = set(allowed_evidence_ids)
    readiness = []
    if evidence_pack.readiness is not None:
        readiness = [
            section.model_dump(mode="json")
            for section in evidence_pack.readiness.sections
            if section.section_id in SCORING_SECTIONS
        ]
    return {
        "task": "scoring_agent_weighted_buyer_scorecards",
        "report_voice": "executive competitive intelligence dossier",
        "jfrog": evidence_pack.jfrog,
        "competitor": evidence_pack.competitor,
        "evidence_pack_id": evidence_pack.id,
        "allowed_evidence_ids": allowed_evidence_ids,
        "score_categories": list(SCORING_CATEGORIES),
        "evidence": [_scoring_evidence_record(item) for item in items],
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
        "quality_notes": list(evidence_pack.quality_notes),
        "requirements": {
            "write_for": "executive, product, security, and field leadership",
            "style": (
                "Neutral buyer-scenario scorecards. Scores are decision support, "
                "not vendor advocacy and not benchmark claims."
            ),
            "must_include": [
                "one score for JFrog and one score for the competitor in every score category",
                "cited rationale for every score",
                "buyer scenario/archetype for every score",
                "confidence notes explaining weak or non-comparable evidence",
            ],
            "scoring_scale": {
                "5": "strong, direct, recent, and product-specific evidence for this buyer scenario",
                "4": "strong evidence with minor caveats or some vendor-stated support",
                "3": "credible but incomplete evidence or broad platform evidence",
                "2": "weak, stale, indirect, or only partially comparable evidence",
                "1": "little usable evidence or capability materially weaker in the supplied evidence",
                "0": "no usable evidence after targeted search",
            },
            "must_not_include": [
                "inline evidence IDs or bracket citations in rationales",
                "source inventory prose",
                "market-share, detection-accuracy, win-rate, or benchmark claims unless directly supported",
                "a single overall winner",
                "scores without cited evidence",
            ],
        },
    }


def build_scoring_prompt(evidence_pack: EvidencePack) -> str:
    payload = build_scoring_prompt_input(evidence_pack)
    schema = ScoringAnalysis.model_json_schema()
    skill = load_agent_skill("scoring_agent")
    return (
        f"{skill}\n\n"
        "Produce weighted buyer scorecards only.\n"
        "Return one strict JSON object and no markdown.\n"
        "Every score must cite one or more IDs from allowed_evidence_ids.\n"
        "Put evidence IDs only in JSON evidence_ids fields. Never put IDs, source numbers, "
        "URLs, source paths, tags, keywords, or bracket citations inside rationales.\n"
        "Score every category for both JFrog and the competitor. Use the exact category names "
        "from score_categories. Do not create extra categories.\n"
        "Use the capability_evidence_matrix as the primary product/feature scoring input.\n"
        "Scores must be scenario-qualified and neutral. Show where JFrog loses when the "
        "evidence supports the competitor for that buyer scenario.\n\n"
        "JSON_SCHEMA:\n"
        f"{json.dumps(schema, ensure_ascii=True, sort_keys=True)}\n\n"
        "PAYLOAD_JSON:\n"
        f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"
    )


def run_scoring_analysis(
    evidence_pack: EvidencePack,
    *,
    runner: ScoringRunner | None = None,
) -> ScoringAnalysis:
    scoring_runner = runner or CrewAIScoringRunner()
    prompt = build_scoring_prompt(evidence_pack)
    allowed_ids = set(build_scoring_prompt_input(evidence_pack)["allowed_evidence_ids"])
    errors: list[str] = []
    raw_output: str | dict[str, Any] | ScoringAnalysis | None = None

    for attempt in range(2):
        try:
            if attempt == 0:
                raw_output = scoring_runner(prompt)
            else:
                raw_output = scoring_runner(_repair_prompt(prompt, raw_output, errors[-1]))
        except Exception as exc:
            errors.append(f"scoring runner failed: {exc}")
            continue
        try:
            return parse_scoring_analysis(raw_output, allowed_evidence_ids=allowed_ids)
        except ScoringGenerationError as exc:
            errors.append(str(exc))

    raise ScoringGenerationError("; ".join(errors))


def parse_scoring_analysis(
    output: str | Mapping[str, Any] | ScoringAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> ScoringAnalysis:
    try:
        if isinstance(output, ScoringAnalysis):
            analysis = output
        elif isinstance(output, Mapping):
            analysis = ScoringAnalysis.model_validate(dict(output))
        else:
            parsed = parse_json_object(str(output), label="scoring agent")
            analysis = ScoringAnalysis.model_validate(parsed)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ScoringGenerationError(str(exc)) from exc

    _validate_scoring_contract(analysis, allowed_evidence_ids=allowed_evidence_ids)
    return analysis


class CrewAIScoringRunner:
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
            role="Scoring Agent",
            goal="Create cited, neutral buyer-scenario scorecards for JFrog competitive intelligence.",
            backstory=load_agent_skill("scoring_agent"),
            llm=llm,
            allow_delegation=False,
            verbose=False,
            memory=False,
        )
        task = Task(
            description=prompt,
            expected_output="A strict JSON object matching the ScoringAnalysis schema.",
            agent=agent,
        )
        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
            memory=False,
            tracing=False,
        )
        return _crew_output_text(crew.kickoff())


def _curated_scoring_items(
    evidence_pack: EvidencePack,
    *,
    max_items: int,
) -> list[EvidenceItem]:
    chosen: list[EvidenceItem] = []
    if evidence_pack.capability_matrix is not None:
        by_id = {item.id: item for item in evidence_pack.items}
        chosen.extend(
            by_id[evidence_id]
            for evidence_id in capability_evidence_ids(evidence_pack.capability_matrix)
            if evidence_id in by_id
        )
    for section_id in SCORING_SECTIONS:
        for company in (evidence_pack.jfrog, evidence_pack.competitor):
            section_company_items = [
                item
                for item in evidence_pack.items
                if item.report_section == section_id
                and item.company.lower() == company.lower()
            ]
            chosen.extend(select_best_evidence(section_company_items, limit=4))
    return _dedupe_items(chosen)[:max_items]


def _scoring_evidence_record(item: EvidenceItem) -> dict[str, Any]:
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
        "published": item.published.isoformat() if item.published else None,
        "summary": _short_text(item.summary or item.quote or ""),
        "capability_id": item.metadata.get("capability_id"),
        "capability_label": item.metadata.get("capability_label"),
        "source_quality_score": item.metadata.get("source_quality_score"),
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


def _validate_scoring_contract(
    analysis: ScoringAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> None:
    allowed_categories = {str(category["category"]) for category in SCORING_CATEGORIES}
    unknown_categories = sorted(
        {score.category for score in analysis.scores if score.category not in allowed_categories}
    )
    if unknown_categories:
        raise ScoringGenerationError(
            "scoring agent returned unsupported categories: " + ", ".join(unknown_categories)
        )
    unknown_ids = sorted(
        {
            evidence_id
            for score in analysis.scores
            for evidence_id in score.evidence_ids
            if evidence_id not in allowed_evidence_ids
        }
    )
    if unknown_ids:
        raise ScoringGenerationError(
            "scoring agent cited evidence outside the curated EvidencePack slice: "
            + ", ".join(unknown_ids)
        )
    score_ids = [score.id for score in analysis.scores]
    if len(score_ids) != len(set(score_ids)):
        raise ScoringGenerationError("scoring agent returned duplicate score ids")
    text_parts = [
        score.rationale
        for score in analysis.scores
    ] + list(analysis.confidence_notes)
    if any(_contains_source_list_prose(text) for text in text_parts):
        raise ScoringGenerationError(
            "scoring agent returned source-list prose instead of buyer-scenario scoring"
        )


def _repair_prompt(
    original_prompt: str,
    raw_output: str | dict[str, Any] | ScoringAnalysis | None,
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
        or bool(_SOURCE_NUMBER_RE.search(text))
    )


def _short_text(text: str, *, limit: int = 700) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0].rstrip() + "..."


_EVIDENCE_ID_RE = re.compile(r"\[[a-f0-9]{12,40}\]", re.IGNORECASE)
_SOURCE_NUMBER_RE = re.compile(r"\[(?:\d{1,2})(?:\s*,\s*\d{1,2})*\]")
_AUDIT_LABEL_RE = re.compile(r"(^|\s)(?:evidence|source|sources)\s*:", re.IGNORECASE)


__all__ = [
    "CrewAIScoringRunner",
    "SCORING_CATEGORIES",
    "SCORING_SECTIONS",
    "ScoringGenerationError",
    "ScoringRunner",
    "build_scoring_prompt",
    "build_scoring_prompt_input",
    "parse_scoring_analysis",
    "run_scoring_analysis",
]
