from __future__ import annotations

from pathlib import Path
from typing import Callable

from ci_engine.crews.report.analysts import build_analyst_sections, build_score_items
from ci_engine.crews.report.buyer_field import (
    BuyerFieldGenerationError,
    BuyerFieldRunner,
    buyer_field_analysis_to_sections,
    run_buyer_field_analysis,
)
from ci_engine.crews.report.checker import MISSING_TEXT
from ci_engine.crews.report.checker import check_report
from ci_engine.crews.report.evidence import (
    ReportMcpClient,
    TavilySearchFn,
    build_evidence_pack_for_competitor,
)
from ci_engine.crews.report.market import (
    MarketGenerationError,
    MarketRunner,
    market_analysis_to_sections,
    run_market_analysis,
)
from ci_engine.crews.report.product_feature import (
    ProductFeatureGenerationError,
    ProductFeatureRunner,
    product_feature_analysis_to_section,
    run_product_feature_analysis,
)
from ci_engine.crews.report.renderer import write_report_artifacts
from ci_engine.crews.report.scoring import (
    ScoringGenerationError,
    ScoringRunner,
    run_scoring_analysis,
)
from ci_engine.crews.report.schemas import (
    EvidencePack,
    ReportClaim,
    ReportDraft,
    ReportRunResult,
    ReportSection,
)
from ci_engine.crews.report.strategy import (
    StrategyGenerationError,
    StrategyRunner,
    run_strategy_analysis,
    strategy_analysis_to_section,
)
from ci_engine.crews.report.technical import (
    TechnicalGenerationError,
    TechnicalRunner,
    run_technical_analysis,
    technical_analysis_to_sections,
)

DraftMode = str
ProgressFn = Callable[[str], None]


def generate_report(
    competitor: str,
    *,
    focus: str | None = None,
    out_dir: str | Path | None = None,
    formats: tuple[str, ...] = ("pdf", "html", "json"),
    include_web: bool = True,
    sections: list[str] | None = None,
    mcp_client: ReportMcpClient | None = None,
    web_search: TavilySearchFn | None = None,
    draft_mode: DraftMode = "deterministic",
    strategy_runner: StrategyRunner | None = None,
    market_runner: MarketRunner | None = None,
    product_feature_runner: ProductFeatureRunner | None = None,
    technical_runner: TechnicalRunner | None = None,
    buyer_field_runner: BuyerFieldRunner | None = None,
    scoring_runner: ScoringRunner | None = None,
    progress: ProgressFn | None = None,
) -> ReportRunResult:
    _emit_progress(
        progress,
        competitor,
        "building evidence pack "
        + ("from DB + Tavily validation" if include_web else "from DB only"),
    )
    evidence_pack = build_evidence_pack_for_competitor(
        competitor,
        focus=focus,
        sections=sections,
        mcp_client=mcp_client,
        include_web=include_web,
        web_search=web_search,
    )
    _emit_progress(
        progress,
        competitor,
        _evidence_pack_summary(evidence_pack),
    )
    _emit_progress(progress, competitor, f"building draft ({draft_mode})")
    draft = build_report_draft(
        evidence_pack,
        sections=sections,
        draft_mode=draft_mode,
        strategy_runner=strategy_runner,
        market_runner=market_runner,
        product_feature_runner=product_feature_runner,
        technical_runner=technical_runner,
        buyer_field_runner=buyer_field_runner,
        scoring_runner=scoring_runner,
        progress=progress,
    )
    _emit_progress(progress, competitor, "running Report Checker")
    validation = check_report(evidence_pack, draft)
    _emit_progress(
        progress,
        competitor,
        f"checker complete: passed={validation.passed}; findings={len(validation.findings)}",
    )
    renders = ()
    if out_dir is not None:
        _emit_progress(
            progress,
            competitor,
            f"rendering artifacts ({','.join(formats)}) to {Path(out_dir)}",
        )
        renders = tuple(
            write_report_artifacts(
                evidence_pack,
                draft,
                validation,
                out_dir=Path(out_dir),
                formats=formats,
            )
        )
        _emit_progress(
            progress,
            competitor,
            "render complete: "
            + ", ".join(f"{render.format}:{render.status}" for render in renders),
        )
    return ReportRunResult(
        evidence_pack=evidence_pack,
        draft=draft,
        validation=validation,
        renders=renders,
    )


def build_report_draft(
    evidence_pack: EvidencePack,
    *,
    sections: list[str] | None = None,
    draft_mode: DraftMode = "deterministic",
    strategy_runner: StrategyRunner | None = None,
    market_runner: MarketRunner | None = None,
    product_feature_runner: ProductFeatureRunner | None = None,
    technical_runner: TechnicalRunner | None = None,
    buyer_field_runner: BuyerFieldRunner | None = None,
    scoring_runner: ScoringRunner | None = None,
    progress: ProgressFn | None = None,
) -> ReportDraft:
    if draft_mode not in {
        "deterministic",
        "crew_strategy",
        "crew_strategy_market",
        "crew_strategy_market_technical",
        "crew_strategy_market_technical_field",
        "crew_strategy_market_product_technical_field",
        "crew_strategy_market_product_technical_field_scoring",
    }:
        raise ValueError(f"unsupported report draft mode: {draft_mode}")
    report_sections = list(build_analyst_sections(evidence_pack, sections=sections))
    metadata = {
        "report_style": "luxury-light-tech-jfrog",
        "draft_mode": draft_mode,
        "draft_builder": (
            "report_crew_strategy_v1"
            if draft_mode == "crew_strategy"
            else "report_crew_strategy_market_v1"
            if draft_mode == "crew_strategy_market"
            else "report_crew_strategy_market_technical_v1"
            if draft_mode == "crew_strategy_market_technical"
            else "report_crew_strategy_market_technical_field_v1"
            if draft_mode == "crew_strategy_market_technical_field"
            else "report_crew_strategy_market_product_technical_field_v1"
            if draft_mode == "crew_strategy_market_product_technical_field"
            else "report_crew_strategy_market_product_technical_field_scoring_v1"
            if draft_mode == "crew_strategy_market_product_technical_field_scoring"
            else "report_analyst_sections_v1"
        ),
        "evidence_count": len(evidence_pack.items),
        "quality_notes": list(evidence_pack.quality_notes),
    }
    _emit_progress(
        progress,
        evidence_pack.competitor,
        f"deterministic section skeleton ready: {len(report_sections)} sections",
    )
    if draft_mode in {
        "crew_strategy",
        "crew_strategy_market",
        "crew_strategy_market_technical",
        "crew_strategy_market_technical_field",
        "crew_strategy_market_product_technical_field",
        "crew_strategy_market_product_technical_field_scoring",
    } and _section_requested("executive_summary", sections):
        try:
            _emit_progress(progress, evidence_pack.competitor, "starting Strategy Analyst")
            strategy_analysis = run_strategy_analysis(
                evidence_pack,
                runner=strategy_runner,
            )
            _replace_section(
                report_sections,
                strategy_analysis_to_section(evidence_pack, strategy_analysis),
            )
            metadata["strategy_generation_status"] = "written"
            _emit_progress(progress, evidence_pack.competitor, "finished Strategy Analyst")
        except StrategyGenerationError as exc:
            _replace_section(report_sections, _failed_strategy_section(str(exc)))
            metadata["strategy_generation_status"] = "failed"
            metadata["strategy_generation_error"] = str(exc)
            _emit_progress(
                progress,
                evidence_pack.competitor,
                f"Strategy Analyst failed: {exc}",
            )

    if draft_mode in {
        "crew_strategy_market",
        "crew_strategy_market_technical",
        "crew_strategy_market_technical_field",
        "crew_strategy_market_product_technical_field",
        "crew_strategy_market_product_technical_field_scoring",
    } and _market_sections_requested(sections):
        try:
            _emit_progress(progress, evidence_pack.competitor, "starting Market Analyst")
            market_analysis = run_market_analysis(
                evidence_pack,
                runner=market_runner,
            )
            for section in market_analysis_to_sections(evidence_pack, market_analysis):
                _replace_section(report_sections, section)
            metadata["market_generation_status"] = "written"
            _emit_progress(progress, evidence_pack.competitor, "finished Market Analyst")
        except MarketGenerationError as exc:
            for section in _failed_market_sections(str(exc)):
                _replace_section(report_sections, section)
            metadata["market_generation_status"] = "failed"
            metadata["market_generation_error"] = str(exc)
            _emit_progress(
                progress,
                evidence_pack.competitor,
                f"Market Analyst failed: {exc}",
            )

    if draft_mode in {
        "crew_strategy_market_product_technical_field",
        "crew_strategy_market_product_technical_field_scoring",
    } and _product_feature_sections_requested(sections):
        try:
            _emit_progress(
                progress,
                evidence_pack.competitor,
                "starting Product/Feature Analyst",
            )
            product_feature_analysis = run_product_feature_analysis(
                evidence_pack,
                runner=product_feature_runner,
            )
            _replace_section(
                report_sections,
                product_feature_analysis_to_section(
                    evidence_pack,
                    product_feature_analysis,
                ),
            )
            metadata["product_feature_generation_status"] = "written"
            _emit_progress(
                progress,
                evidence_pack.competitor,
                "finished Product/Feature Analyst",
            )
        except ProductFeatureGenerationError as exc:
            _replace_section(report_sections, _failed_product_feature_section(str(exc)))
            metadata["product_feature_generation_status"] = "failed"
            metadata["product_feature_generation_error"] = str(exc)
            _emit_progress(
                progress,
                evidence_pack.competitor,
                f"Product/Feature Analyst failed: {exc}",
            )

    if draft_mode in {
        "crew_strategy_market_technical",
        "crew_strategy_market_technical_field",
        "crew_strategy_market_product_technical_field",
        "crew_strategy_market_product_technical_field_scoring",
    } and _technical_sections_requested(sections):
        try:
            _emit_progress(progress, evidence_pack.competitor, "starting Technical Analyst")
            technical_analysis = run_technical_analysis(
                evidence_pack,
                runner=technical_runner,
            )
            for section in technical_analysis_to_sections(evidence_pack, technical_analysis):
                _replace_section(report_sections, section)
            metadata["technical_generation_status"] = "written"
            _emit_progress(progress, evidence_pack.competitor, "finished Technical Analyst")
        except TechnicalGenerationError as exc:
            for section in _failed_technical_sections(str(exc)):
                _replace_section(report_sections, section)
            metadata["technical_generation_status"] = "failed"
            metadata["technical_generation_error"] = str(exc)
            _emit_progress(
                progress,
                evidence_pack.competitor,
                f"Technical Analyst failed: {exc}",
            )

    if draft_mode in {
        "crew_strategy_market_technical_field",
        "crew_strategy_market_product_technical_field",
        "crew_strategy_market_product_technical_field_scoring",
    } and _buyer_field_sections_requested(sections):
        try:
            _emit_progress(progress, evidence_pack.competitor, "starting Buyer/Field Analyst")
            buyer_field_analysis = run_buyer_field_analysis(
                evidence_pack,
                runner=buyer_field_runner,
            )
            for section in buyer_field_analysis_to_sections(evidence_pack, buyer_field_analysis):
                _replace_section(report_sections, section)
            metadata["buyer_field_generation_status"] = "written"
            _emit_progress(progress, evidence_pack.competitor, "finished Buyer/Field Analyst")
        except BuyerFieldGenerationError as exc:
            for section in _failed_buyer_field_sections(str(exc)):
                _replace_section(report_sections, section)
            metadata["buyer_field_generation_status"] = "failed"
            metadata["buyer_field_generation_error"] = str(exc)
            _emit_progress(
                progress,
                evidence_pack.competitor,
                f"Buyer/Field Analyst failed: {exc}",
            )

    score_items = build_score_items(evidence_pack)
    if draft_mode == "crew_strategy_market_product_technical_field_scoring":
        try:
            _emit_progress(progress, evidence_pack.competitor, "starting Scoring Agent")
            scoring_analysis = run_scoring_analysis(
                evidence_pack,
                runner=scoring_runner,
            )
            score_items = scoring_analysis.scores
            metadata["scoring_generation_status"] = "written"
            metadata["scoring_confidence_notes"] = list(scoring_analysis.confidence_notes)
            _emit_progress(progress, evidence_pack.competitor, "finished Scoring Agent")
        except ScoringGenerationError as exc:
            score_items = ()
            metadata["scoring_generation_status"] = "failed"
            metadata["scoring_generation_error"] = str(exc)
            _emit_progress(
                progress,
                evidence_pack.competitor,
                f"Scoring Agent failed: {exc}",
            )
    _emit_progress(
        progress,
        evidence_pack.competitor,
        f"draft ready: sections={len(report_sections)}; scores={len(score_items)}",
    )

    return ReportDraft(
        competitor=evidence_pack.competitor,
        evidence_pack_id=evidence_pack.id,
        sections=tuple(report_sections),
        scores=score_items,
        missing_data=evidence_pack.gaps,
        metadata=metadata,
    )


def _emit_progress(
    progress: ProgressFn | None,
    competitor: str,
    message: str,
) -> None:
    if progress is None:
        return
    progress(f"[{competitor}] {message}")


def _evidence_pack_summary(evidence_pack: EvidencePack) -> str:
    db_items = sum(1 for item in evidence_pack.items if item.source == "db")
    tavily_items = sum(1 for item in evidence_pack.items if item.source == "tavily")
    capability_rows = (
        len(evidence_pack.capability_matrix.rows)
        if evidence_pack.capability_matrix is not None
        else 0
    )
    product_count = len(evidence_pack.product_catalog)
    readiness = (
        f"; readiness={evidence_pack.readiness.status}"
        if evidence_pack.readiness is not None
        else ""
    )
    return (
        "evidence pack frozen: "
        f"items={len(evidence_pack.items)} "
        f"(db={db_items}, tavily={tavily_items}); "
        f"gaps={len(evidence_pack.gaps)}; "
        f"products={product_count}; "
        f"capability_rows={capability_rows}"
        f"{readiness}"
    )


def _section_requested(section_id: str, sections: list[str] | None) -> bool:
    if sections is None:
        return True
    return section_id in {section.strip() for section in sections}


def _market_sections_requested(sections: list[str] | None) -> bool:
    if sections is None:
        return True
    requested = {section.strip() for section in sections}
    return bool({"company_snapshot", "market_context"} & requested)


def _technical_sections_requested(sections: list[str] | None) -> bool:
    if sections is None:
        return True
    requested = {section.strip() for section in sections}
    return bool({"technical_teardown", "supply_chain_security"} & requested)


def _product_feature_sections_requested(sections: list[str] | None) -> bool:
    if sections is None:
        return True
    requested = {section.strip() for section in sections}
    return "product_feature_analysis" in requested


def _buyer_field_sections_requested(sections: list[str] | None) -> bool:
    if sections is None:
        return True
    requested = {section.strip() for section in sections}
    return bool({"buyer_fit", "field_battlecard"} & requested)


def _replace_section(
    sections: list[ReportSection],
    replacement: ReportSection,
) -> None:
    for index, section in enumerate(sections):
        if section.id == replacement.id:
            sections[index] = replacement
            return
    sections.insert(0, replacement)


def _failed_strategy_section(error: str) -> ReportSection:
    return ReportSection(
        id="executive_summary",
        title="Executive Summary",
        agent_key="strategy_analyst",
        agent_name="Strategy Analyst",
        skill_name="report-strategy-analyst",
        claims=(
            ReportClaim(
                id="strategy-generation-failed",
                text=f"Strategy Analyst/executive_summary: {MISSING_TEXT}",
                evidence_ids=(),
                confidence="unknown",
                claim_type="missing",
            ),
        ),
        narrative=f"Strategy Analyst generation failed: {error}",
    )


def _failed_market_sections(error: str) -> tuple[ReportSection, ReportSection]:
    return (
        ReportSection(
            id="company_snapshot",
            title="Company Snapshot",
            agent_key="market_analyst",
            agent_name="Market Analyst",
            skill_name="report-market-analyst",
            claims=(
                ReportClaim(
                    id="market-generation-failed-company",
                    text=f"Market Analyst/company_snapshot: {MISSING_TEXT}",
                    evidence_ids=(),
                    confidence="unknown",
                    claim_type="missing",
                ),
            ),
            narrative=f"Market Analyst generation failed: {error}",
        ),
        ReportSection(
            id="market_context",
            title="Market And Strategic Context",
            agent_key="market_analyst",
            agent_name="Market Analyst",
            skill_name="report-market-analyst",
            claims=(
                ReportClaim(
                    id="market-generation-failed-context",
                    text=f"Market Analyst/market_context: {MISSING_TEXT}",
                    evidence_ids=(),
                    confidence="unknown",
                    claim_type="missing",
                ),
            ),
            narrative=f"Market Analyst generation failed: {error}",
        ),
    )


def _failed_product_feature_section(error: str) -> ReportSection:
    return ReportSection(
        id="product_feature_analysis",
        title="Product And Feature Analysis",
        agent_key="product_feature_analyst",
        agent_name="Product/Feature Analyst",
        skill_name="report-product-feature-analyst",
        claims=(
            ReportClaim(
                id="product-feature-generation-failed",
                text=f"Product/Feature Analyst/product_feature_analysis: {MISSING_TEXT}",
                evidence_ids=(),
                confidence="unknown",
                claim_type="missing",
            ),
        ),
        narrative=f"Product/Feature Analyst generation failed: {error}",
    )


def _failed_technical_sections(error: str) -> tuple[ReportSection, ReportSection]:
    return (
        ReportSection(
            id="technical_teardown",
            title="Technical And Feature Teardown",
            agent_key="technical_analyst",
            agent_name="Technical Analyst",
            skill_name="report-technical-analyst",
            claims=(
                ReportClaim(
                    id="technical-generation-failed-teardown",
                    text=f"Technical Analyst/technical_teardown: {MISSING_TEXT}",
                    evidence_ids=(),
                    confidence="unknown",
                    claim_type="missing",
                ),
            ),
            narrative=f"Technical Analyst generation failed: {error}",
        ),
        ReportSection(
            id="supply_chain_security",
            title="Supply Chain Security Coverage",
            agent_key="technical_analyst",
            agent_name="Technical Analyst",
            skill_name="report-technical-analyst",
            claims=(
                ReportClaim(
                    id="technical-generation-failed-security",
                    text=f"Technical Analyst/supply_chain_security: {MISSING_TEXT}",
                    evidence_ids=(),
                    confidence="unknown",
                    claim_type="missing",
                ),
            ),
            narrative=f"Technical Analyst generation failed: {error}",
        ),
    )


def _failed_buyer_field_sections(error: str) -> tuple[ReportSection, ReportSection]:
    return (
        ReportSection(
            id="buyer_fit",
            title="Buyer Fit Matrix",
            agent_key="buyer_field_analyst",
            agent_name="Buyer/Field Analyst",
            skill_name="report-buyer-field-analyst",
            claims=(
                ReportClaim(
                    id="buyer-field-generation-failed-fit",
                    text=f"Buyer/Field Analyst/buyer_fit: {MISSING_TEXT}",
                    evidence_ids=(),
                    confidence="unknown",
                    claim_type="missing",
                ),
            ),
            narrative=f"Buyer/Field Analyst generation failed: {error}",
        ),
        ReportSection(
            id="field_battlecard",
            title="JFrog Field Battlecard",
            agent_key="buyer_field_analyst",
            agent_name="Buyer/Field Analyst",
            skill_name="report-buyer-field-analyst",
            claims=(
                ReportClaim(
                    id="buyer-field-generation-failed-battlecard",
                    text=f"Buyer/Field Analyst/field_battlecard: {MISSING_TEXT}",
                    evidence_ids=(),
                    confidence="unknown",
                    claim_type="missing",
                ),
            ),
            narrative=f"Buyer/Field Analyst generation failed: {error}",
        ),
    )


__all__ = ["build_report_draft", "generate_report"]
