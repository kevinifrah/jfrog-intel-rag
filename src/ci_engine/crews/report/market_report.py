"""Standalone "Market & Strategic Context" report.

The per-competitor dossier drops Part 1 (market context); its market-wide view is
published here instead. A dedicated analyst pass runs ONCE per batch run, reading
active evidence across the whole tracked field and producing a general market thesis,
structural dynamics and risks, plus the market-level PESTEL, Porter's Five Forces and
an all-competitor positioning map.

Like every generator, this reads ONLY active stored evidence via the MCP layer and
never touches the web. The grounding-contract skill is composed in front of the
market-overview skill, and every claim/factor/force/player cites the frozen pack.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from ci_engine.config import get as config_get
from ci_engine.config import tracked_companies
from ci_engine.crews.report.crew import _ensure_crewai_storage
from ci_engine.crews.report.evidence import (
    LocalMcpReportClient,
    ReportMcpClient,
    dedupe_evidence,
)
from ci_engine.crews.report.evidence import _db_evidence_from_chunk  # internal reuse
from ci_engine.crews.report.market import (
    _crew_output_text,
    _crewai_model_name,
    _presentation_text,
    _short_text,
    _uses_effort_model,
)
from ci_engine.crews.report.renderer import write_report_artifacts
from ci_engine.crews.report.schemas import (
    EvidenceItem,
    EvidencePack,
    MarketClaim,
    MarketOverviewAnalysis,
    ReportClaim,
    ReportDraft,
    ReportRunResult,
    ReportSection,
    ValidationFinding,
    ValidationReport,
)
from ci_engine.crews.report.sections import REPORT_SECTION_SPECS, ReportSectionSpec
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret
from ci_engine.skills import compose, load_skill

# The market-wide read uses the same business-axis sections that defined the old
# per-competitor Part 1 — company snapshot + market context — but across every company.
MARKET_OVERVIEW_SECTION_IDS = ("company_snapshot", "market_context")
MARKET_OVERVIEW_SECTION_ID = "market_overview"
MARKET_OVERVIEW_AGENT_KEY = "market_overview_analyst"
MARKET_OVERVIEW_SKILL = "report-market-overview"


class MarketReportError(ValueError):
    pass


class MarketOverviewRunner(Protocol):
    def __call__(self, prompt: str) -> str | dict[str, Any] | MarketOverviewAnalysis:
        ...


# --------------------------------------------------------------------------- #
# Config helpers (every tunable lives in config.yaml)
# --------------------------------------------------------------------------- #
def market_report_enabled() -> bool:
    return bool(config_get("report.market_report.enabled", False))


def market_report_slug() -> str:
    return str(config_get("report.market_report.slug", "market"))


def market_report_title() -> str:
    return str(config_get("report.market_report.title", "Market & Strategic Context"))


def market_report_max_companies() -> int:
    return int(config_get("report.market_report.max_companies", 12))


def market_framework_enabled(name: str) -> bool:
    """Whether a market-level framework is on for the standalone report."""
    return bool(config_get(f"report.market_report.frameworks.{name}", True))


def market_overview_framework_skills() -> tuple[str, ...]:
    """Framework skills composed for the market analyst, honoring config toggles.

    The cross-report skill is always first — it defines the canonical positioning axes
    and Five Forces baselines the per-framework skills must follow.
    """
    skills = ["report-market-cross-report"]
    if market_framework_enabled("pestel"):
        skills.append("report-framework-pestel")
    if market_framework_enabled("five_forces"):
        skills.append("report-framework-five-forces")
    if market_framework_enabled("positioning_map"):
        skills.append("report-framework-positioning-map")
    return tuple(skills)


def _market_specs() -> tuple[ReportSectionSpec, ...]:
    return tuple(
        spec for spec in REPORT_SECTION_SPECS if spec.id in MARKET_OVERVIEW_SECTION_IDS
    )


# --------------------------------------------------------------------------- #
# Evidence: read active stored evidence across the whole tracked field
# --------------------------------------------------------------------------- #
def collect_market_overview_evidence(
    *,
    companies: Sequence[str] | None = None,
    mcp_client: ReportMcpClient | None = None,
    max_companies: int | None = None,
    max_items_per_company: int = 6,
) -> EvidencePack:
    client = mcp_client or LocalMcpReportClient()
    selected = _selected_market_companies(companies, max_companies)
    specs = _market_specs()
    items: list[EvidenceItem] = []
    for company in selected:
        company_items: list[EvidenceItem] = []
        for spec in specs:
            for query in spec.queries:
                result = client.search(
                    query=query.format(company=company),
                    axis=spec.axis,
                    competitors=[company],
                    dimensions=list(spec.dimensions),
                )
                for index, chunk in enumerate(result.get("chunks", [])):
                    item = _db_evidence_from_chunk(
                        chunk,
                        MARKET_OVERVIEW_SECTION_ID,
                        index,
                        inventory_sources={},
                    )
                    if item is not None:
                        company_items.append(item)
        items.extend(dedupe_evidence(company_items)[:max_items_per_company])

    items = dedupe_evidence(items)
    pack_id = _market_pack_id(selected, items)
    return EvidencePack(
        id=pack_id,
        competitor=market_report_title(),
        focus="market_overview",
        items=tuple(items),
        quality_notes=(
            f"Market-wide evidence across {len(selected)} tracked companies: "
            f"{len(items)} active items.",
        ),
        metadata={
            "report_kind": "market",
            "companies": list(selected),
            "sections": list(MARKET_OVERVIEW_SECTION_IDS),
            "db_evidence_count": len(items),
        },
    )


def _selected_market_companies(
    companies: Sequence[str] | None,
    max_companies: int | None,
) -> list[str]:
    raw = list(companies) if companies else tracked_companies()
    seen: set[str] = set()
    deduped: list[str] = []
    for company in raw:
        cleaned = str(company).strip()
        if not cleaned or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        deduped.append(cleaned)
    cap = max_companies if max_companies is not None else market_report_max_companies()
    return deduped[: max(int(cap), 1)]


def _market_pack_id(companies: Sequence[str], items: Sequence[EvidenceItem]) -> str:
    import hashlib  # noqa: PLC0415

    raw = "market-overview|" + ",".join(companies) + "|" + ",".join(item.id for item in items)
    return "market-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def build_market_overview_prompt(
    evidence_pack: EvidencePack,
    *,
    companies: Sequence[str],
) -> str:
    skill = compose("grounding-contract", "neutral-ci-contract", MARKET_OVERVIEW_SKILL)
    frameworks = "\n\n".join(load_skill(name) for name in market_overview_framework_skills())
    schema = MarketOverviewAnalysis.model_json_schema()
    payload = build_market_overview_prompt_input(evidence_pack, companies=companies)

    enabled = [
        name
        for name in ("pestel", "five_forces", "positioning_map")
        if market_framework_enabled(name)
    ]
    disabled = [
        name
        for name in ("pestel", "five_forces", "positioning_map")
        if not market_framework_enabled(name)
    ]
    framework_lines: list[str] = []
    if enabled:
        framework_lines.append(
            f"Populate the {', '.join(enabled)} field(s) at the MARKET level per the "
            "framework skills above — describe the whole market, not a single pairing. "
        )
    if disabled:
        framework_lines.append(
            f"Leave the {', '.join(disabled)} field(s) empty — disabled for this report. "
        )
    if market_framework_enabled("positioning_map"):
        framework_lines.append(
            "For positioning_map, use the canonical axes from the cross-report skill verbatim "
            "(x_axis_label='Supply-chain coverage breadth', y_axis_label='Security specialization "
            "depth') and plot the whole tracked field — JFrog plus every tracked competitor for "
            "which the EvidencePack supports a placement. Mark JFrog is_focus=true; do not single "
            "out one competitor as the focus. "
        )
    if market_framework_enabled("five_forces"):
        framework_lines.append(
            "For five_forces, start from the baseline intensities in the cross-report skill and "
            "adjust only on strong cited evidence. "
        )

    return (
        f"{skill}\n\n"
        f"{frameworks}\n\n"
        "Write the standalone market overview: a market thesis, market dynamics, market risks, "
        "and the enabled market-level frameworks.\n"
        f"{''.join(framework_lines)}"
        "If the EvidencePack cannot support a framework, return it empty rather than inventing data.\n"
        "Return one strict JSON object and no markdown.\n"
        "Every claim, factor, force, and plotted player must cite one or more IDs from "
        "allowed_evidence_ids. Put evidence IDs only in JSON evidence_ids fields — never inside "
        "text fields, and never as bracket citations or source numbers.\n"
        "Do not mention source paths, ontology keys, tags, keywords, or metadata. Do not infer "
        "market share, analyst placement, revenue, growth, or customer counts unless directly "
        "supported by cited evidence; otherwise lower confidence or use 'no recent data found'.\n\n"
        "JSON_SCHEMA:\n"
        f"{json.dumps(schema, ensure_ascii=True, sort_keys=True)}\n\n"
        "PAYLOAD_JSON:\n"
        f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"
    )


def build_market_overview_prompt_input(
    evidence_pack: EvidencePack,
    *,
    companies: Sequence[str],
) -> dict[str, Any]:
    items = list(evidence_pack.items)
    return {
        "task": "market_overview_standalone_report",
        "report_voice": "executive market intelligence briefing",
        "jfrog": "JFrog",
        "tracked_companies": list(companies),
        "evidence_pack_id": evidence_pack.id,
        "allowed_evidence_ids": [item.id for item in items],
        "evidence": [_market_evidence_record(item) for item in items],
        "quality_notes": list(evidence_pack.quality_notes),
        "requirements": {
            "write_for": "executive, product, and market leadership",
            "style": (
                "Market-wide intelligence for senior leaders: concise, structural, and "
                "buyer-centered. Describe the playing field, not one rivalry."
            ),
            "must_include": [
                "market thesis",
                "structural market dynamics",
                "market risks and open questions",
                "market-level PESTEL (if enabled)",
                "market-level Porter's Five Forces (if enabled)",
                "all-competitor positioning map (if enabled)",
                "confidence notes",
            ],
            "must_not_include": [
                "inline evidence IDs or bracket citations in text",
                "source numbers in text",
                "single-competitor framing",
                "ontology keys or metadata names",
                "uncited claims",
                "market share or analyst placement unless directly supported",
            ],
        },
    }


def _market_evidence_record(item: EvidenceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "company": item.company,
        "source": item.source,
        "tier": item.tier,
        "confidence": item.confidence,
        "dimension": item.dimension,
        "title": item.title,
        "publisher": item.publisher,
        "url": item.url,
        "published": item.published.isoformat() if item.published else None,
        "summary": _short_text(item.summary or item.quote or ""),
    }


# --------------------------------------------------------------------------- #
# Run + parse
# --------------------------------------------------------------------------- #
def run_market_overview_analysis(
    evidence_pack: EvidencePack,
    *,
    companies: Sequence[str],
    runner: MarketOverviewRunner | None = None,
) -> MarketOverviewAnalysis:
    market_runner = runner or CrewAIMarketOverviewRunner()
    prompt = build_market_overview_prompt(evidence_pack, companies=companies)
    allowed_ids = {item.id for item in evidence_pack.items}
    errors: list[str] = []
    raw_output: str | dict[str, Any] | MarketOverviewAnalysis | None = None

    for attempt in range(2):
        try:
            if attempt == 0:
                raw_output = market_runner(prompt)
            else:
                raw_output = market_runner(_repair_prompt(prompt, raw_output, errors[-1]))
        except Exception as exc:
            errors.append(f"market overview runner failed: {exc}")
            continue
        try:
            return parse_market_overview_analysis(raw_output, allowed_evidence_ids=allowed_ids)
        except MarketReportError as exc:
            errors.append(str(exc))

    raise MarketReportError("; ".join(errors) or "market overview generation failed")


def parse_market_overview_analysis(
    output: str | Mapping[str, Any] | MarketOverviewAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> MarketOverviewAnalysis:
    try:
        if isinstance(output, MarketOverviewAnalysis):
            analysis = output
        elif isinstance(output, Mapping):
            analysis = MarketOverviewAnalysis.model_validate(dict(output))
        else:
            parsed = parse_json_object(str(output), label="market overview analyst")
            analysis = MarketOverviewAnalysis.model_validate(parsed)
    except (ValidationError, ValueError, TypeError) as exc:
        raise MarketReportError(str(exc)) from exc

    _validate_citations(analysis, allowed_evidence_ids=allowed_evidence_ids)
    return analysis


def _validate_citations(
    analysis: MarketOverviewAnalysis,
    *,
    allowed_evidence_ids: set[str],
) -> None:
    cited: list[str] = []
    for claim in [analysis.market_thesis, *analysis.market_dynamics, *analysis.market_risks]:
        cited.extend(claim.evidence_ids)
    for factor in analysis.pestel:
        cited.extend(factor.evidence_ids)
    for force in analysis.five_forces:
        cited.extend(force.evidence_ids)
    if analysis.positioning_map is not None:
        for player in analysis.positioning_map.players:
            cited.extend(player.evidence_ids)
    unknown = sorted({eid for eid in cited if eid not in allowed_evidence_ids})
    if unknown:
        raise MarketReportError(
            "market overview cited evidence outside the EvidencePack: " + ", ".join(unknown)
        )


def _repair_prompt(prompt: str, raw_output: Any, error: str) -> str:
    return (
        prompt
        + "\n\nREPAIR_REQUIRED:\n"
        + error
        + "\n\nPrevious output was invalid. Return a corrected strict JSON object only.\n"
        + "PREVIOUS_OUTPUT:\n"
        + _short_text(str(raw_output or ""), limit=4000)
    )


# --------------------------------------------------------------------------- #
# Draft assembly
# --------------------------------------------------------------------------- #
def market_overview_to_draft(
    evidence_pack: EvidencePack,
    analysis: MarketOverviewAnalysis,
    *,
    companies: Sequence[str],
) -> ReportDraft:
    claims: list[ReportClaim] = [
        _report_claim("market-thesis", analysis.market_thesis),
        *_bucket_claims("market-dynamic", analysis.market_dynamics),
        *_bucket_claims("market-risk", analysis.market_risks),
    ]
    confidence = " ".join(_presentation_text(note) for note in analysis.confidence_notes)
    section = ReportSection(
        id=MARKET_OVERVIEW_SECTION_ID,
        title=market_report_title(),
        agent_key=MARKET_OVERVIEW_AGENT_KEY,
        agent_name="Market Overview Analyst",
        skill_name=MARKET_OVERVIEW_SKILL,
        evidence_ids=tuple(_unique_evidence(claims)),
        claims=tuple(claims),
        narrative=f"Market-wide synthesis across the tracked field. Confidence notes: {confidence}",
        metadata=_framework_metadata(analysis),
    )
    return ReportDraft(
        competitor=market_report_title(),
        evidence_pack_id=evidence_pack.id,
        sections=(section,),
        scores=(),
        metadata={
            "report_kind": "market",
            "report_title": market_report_title(),
            "draft_mode": "market_overview",
            "draft_builder": "report_market_overview_v1",
            "report_style": "luxury-light-tech-jfrog",
            "companies": list(companies),
            "evidence_count": len(evidence_pack.items),
            "confidence_notes": list(analysis.confidence_notes),
        },
    )


def _framework_metadata(analysis: MarketOverviewAnalysis) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if analysis.pestel and market_framework_enabled("pestel"):
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
    if analysis.five_forces and market_framework_enabled("five_forces"):
        meta["five_forces"] = [
            {
                "force": force.force,
                "intensity": force.intensity,
                "rationale": _presentation_text(force.rationale),
                "evidence_ids": list(force.evidence_ids),
            }
            for force in analysis.five_forces
        ]
    if analysis.positioning_map is not None and market_framework_enabled("positioning_map"):
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


def _report_claim(claim_id: str, claim: MarketClaim) -> ReportClaim:
    return ReportClaim(
        id=claim_id,
        text=_presentation_text(claim.text),
        evidence_ids=claim.evidence_ids,
        confidence=claim.confidence,
        claim_type="analysis",
    )


def _bucket_claims(prefix: str, claims: Sequence[MarketClaim]) -> list[ReportClaim]:
    return [
        _report_claim(f"{prefix}-{index}", claim)
        for index, claim in enumerate(claims, start=1)
    ]


def _unique_evidence(claims: Sequence[ReportClaim]) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for claim in claims:
        for evidence_id in claim.evidence_ids:
            if evidence_id not in seen:
                seen.add(evidence_id)
                ids.append(evidence_id)
    return ids


# --------------------------------------------------------------------------- #
# Validation (lenient — the report renders so leadership can read it)
# --------------------------------------------------------------------------- #
def validate_market_report(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
) -> ValidationReport:
    findings: list[ValidationFinding] = []
    section = next(
        (s for s in draft.sections if s.id == MARKET_OVERVIEW_SECTION_ID),
        None,
    )
    if section is None:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_market_overview_section",
                message="The market overview section is missing from the draft.",
            )
        )
        return ValidationReport(passed=False, findings=tuple(findings))

    allowed_ids = {item.id for item in evidence_pack.items}
    thesis = next(
        (c for c in section.claims if c.id == "market-thesis" and c.claim_type != "missing"),
        None,
    )
    if thesis is None:
        findings.append(
            ValidationFinding(
                severity="error",
                code="missing_market_thesis",
                message="The market overview is missing a market thesis.",
                section_id=section.id,
            )
        )
    cited = {
        evidence_id
        for claim in section.claims
        for evidence_id in claim.evidence_ids
        if evidence_id in allowed_ids
    }
    if not cited:
        findings.append(
            ValidationFinding(
                severity="error",
                code="uncited_market_overview",
                message="No market overview claim cites stored active evidence.",
                section_id=section.id,
            )
        )
    passed = not any(finding.severity == "error" for finding in findings)
    return ValidationReport(passed=passed, findings=tuple(findings))


# --------------------------------------------------------------------------- #
# CrewAI runner (default; tests inject a fake)
# --------------------------------------------------------------------------- #
class CrewAIMarketOverviewRunner:
    def __call__(self, prompt: str) -> str:
        _ensure_crewai_storage()
        from crewai import Agent, Crew, LLM, Process, Task  # noqa: PLC0415

        model = str(config_get("models.report.name", "claude-sonnet-4-6"))
        llm_kwargs: dict[str, Any] = {
            "model": _crewai_model_name(model),
            "provider": "anthropic",
            "is_anthropic": True,
            "api_key": get_secret("anthropic-key"),
            "max_tokens": int(config_get("models.report.max_tokens", 10000)),
            "timeout": float(config_get("models.report.timeout_s", 240)),
        }
        if _uses_effort_model(model):
            llm_kwargs["reasoning_effort"] = str(config_get("models.report.thinking", "high"))
        else:
            llm_kwargs["temperature"] = float(config_get("models.report.temperature", 0.3))
        llm = LLM(**llm_kwargs)
        agent = Agent(
            role="Market Overview Analyst",
            goal=(
                "Produce a trustworthy, market-wide strategic context briefing for JFrog "
                "competitive intelligence, grounded only in stored evidence."
            ),
            backstory=load_skill(MARKET_OVERVIEW_SKILL),
            llm=llm,
            allow_delegation=False,
            verbose=True,
            memory=False,
        )
        task = Task(
            description=prompt,
            expected_output="A strict JSON object matching the MarketOverviewAnalysis schema.",
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


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def generate_market_report(
    *,
    companies: Sequence[str] | None = None,
    out_dir: str | Path | None = None,
    formats: tuple[str, ...] = ("pdf", "html", "json"),
    mcp_client: ReportMcpClient | None = None,
    runner: MarketOverviewRunner | None = None,
    max_companies: int | None = None,
    progress: Any = None,
) -> ReportRunResult:
    def _emit(message: str) -> None:
        if progress is not None:
            progress(f"[market] {message}")

    selected = _selected_market_companies(companies, max_companies)
    _emit(f"building market-wide evidence across {len(selected)} companies")
    evidence_pack = collect_market_overview_evidence(
        companies=selected,
        mcp_client=mcp_client,
        max_companies=max_companies,
    )
    _emit(f"market evidence frozen: {len(evidence_pack.items)} items")
    if not evidence_pack.items:
        raise MarketReportError(
            "no active stored evidence found for the market report — nothing to ground it in"
        )

    _emit("running Market Overview Analyst")
    analysis = run_market_overview_analysis(
        evidence_pack,
        companies=selected,
        runner=runner,
    )
    draft = market_overview_to_draft(evidence_pack, analysis, companies=selected)
    validation = validate_market_report(evidence_pack, draft)
    _emit(f"validation: passed={validation.passed}; findings={len(validation.findings)}")

    renders: tuple[Any, ...] = ()
    if out_dir is not None:
        _emit(f"rendering artifacts ({','.join(formats)}) to {Path(out_dir)}")
        renders = tuple(
            write_report_artifacts(
                evidence_pack,
                draft,
                validation,
                out_dir=Path(out_dir),
                formats=formats,
            )
        )
        _emit(
            "render complete: "
            + ", ".join(f"{render.format}:{render.status}" for render in renders)
        )
    return ReportRunResult(
        evidence_pack=evidence_pack,
        draft=draft,
        validation=validation,
        renders=renders,
    )


__all__ = [
    "CrewAIMarketOverviewRunner",
    "MARKET_OVERVIEW_SECTION_ID",
    "MarketOverviewRunner",
    "MarketReportError",
    "build_market_overview_prompt",
    "build_market_overview_prompt_input",
    "collect_market_overview_evidence",
    "generate_market_report",
    "market_overview_to_draft",
    "market_report_enabled",
    "market_report_slug",
    "market_report_title",
    "parse_market_overview_analysis",
    "run_market_overview_analysis",
    "validate_market_report",
]
