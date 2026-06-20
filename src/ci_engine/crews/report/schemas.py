from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EvidenceSource = Literal["db", "tavily"]
EvidenceTier = Literal["primary", "supporting", "validation"]
Confidence = Literal["high", "medium", "low", "unknown"]
ValidationSeverity = Literal["error", "warning", "info"]
WebClassification = Literal[
    "confirms_db",
    "updates_db",
    "contradicts_db",
    "fills_gap",
    "adds_context",
    "insufficient",
    "irrelevant",
]
RenderStatus = Literal["written", "skipped", "blocked"]
ReadinessStatus = Literal["ready", "needs_review", "weak"]
CapabilitySearchSource = Literal["db", "tavily"]
CapabilitySearchStatus = Literal[
    "supported",
    "partially_supported",
    "not_found_after_search",
    "contradictory",
    "unclear_needs_review",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReportBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(ReportBaseModel):
    id: str
    source: EvidenceSource
    tier: EvidenceTier = "supporting"
    company: str
    report_section: str
    url: str
    title: str | None = None
    publisher: str | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    published: date | None = None
    quote: str | None = None
    summary: str | None = None
    axis: str | None = None
    dimension: str | None = None
    confidence: Confidence = "unknown"
    classification: WebClassification | None = None
    source_id: int | None = None
    chunk_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("company", "report_section", "url", "id")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("value is required")
        return cleaned

    @model_validator(mode="after")
    def _has_content(self) -> EvidenceItem:
        if not (self.quote or self.summary):
            raise ValueError("evidence item requires quote or summary")
        if self.source == "tavily" and self.classification is None:
            raise ValueError("tavily evidence requires classification")
        return self


class EvidenceGap(ReportBaseModel):
    company: str
    report_section: str
    reason: str
    axis: str | None = None
    dimension: str | None = None
    detail: str | None = None


class SourceInventoryItem(ReportBaseModel):
    source_id: int
    company: str
    axis: str
    dimension: str | None = None
    doc_type: str
    source_kind: str
    url: str
    title: str | None = None
    published: date | None = None
    fetched_at: datetime
    raw_path: str | None = None
    chunk_count: int = 0
    citation_count: int = 0
    quality_score: float = Field(ge=0.0, le=100.0)


class SourceInventorySummary(ReportBaseModel):
    company: str
    total_sources: int = 0
    active_dimensions: int = 0
    primary_quality_sources: int = 0
    official_or_vendor_sources: int = 0
    generated_report_sources: int = 0
    newest_published: date | None = None


class SourceInventoryReport(ReportBaseModel):
    sources: tuple[SourceInventoryItem, ...] = ()
    summaries: tuple[SourceInventorySummary, ...] = ()


class EvidenceReadinessCompany(ReportBaseModel):
    company: str
    db_items: int = 0
    tavily_items: int = 0
    primary_items: int = 0
    source_count: int = 0
    high_confidence_items: int = 0
    gap_count: int = 0
    readiness_score: float = Field(ge=0.0, le=100.0)
    status: ReadinessStatus
    notes: tuple[str, ...] = ()


class EvidenceReadinessSection(ReportBaseModel):
    section_id: str
    title: str
    readiness_score: float = Field(ge=0.0, le=100.0)
    status: ReadinessStatus
    companies: tuple[EvidenceReadinessCompany, ...]
    notes: tuple[str, ...] = ()


class EvidenceReadinessReport(ReportBaseModel):
    overall_score: float = Field(ge=0.0, le=100.0)
    status: ReadinessStatus
    sections: tuple[EvidenceReadinessSection, ...]
    notes: tuple[str, ...] = ()


class CapabilityDefinition(ReportBaseModel):
    id: str
    label: str
    dimension: str
    must_resolve: bool = False
    search_terms: tuple[str, ...] = ()


class TargetedSearchAttempt(ReportBaseModel):
    company: str
    capability_id: str
    capability_label: str
    source: CapabilitySearchSource
    query: str
    result_count: int = Field(ge=0)
    searched_at: datetime = Field(default_factory=utc_now)
    status: CapabilitySearchStatus
    notes: str | None = None


class ProductCatalogItem(ReportBaseModel):
    company: str
    product_name: str
    category: str
    primary_role: str
    capabilities: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    confidence: Confidence = "unknown"


class CapabilityEvidenceCell(ReportBaseModel):
    company: str
    product_names: tuple[str, ...] = ()
    capability_statement: str
    status: CapabilitySearchStatus
    confidence: Confidence = "unknown"
    evidence_ids: tuple[str, ...] = ()
    search_attempts: tuple[TargetedSearchAttempt, ...] = ()


class CapabilityEvidenceRow(ReportBaseModel):
    capability_id: str
    capability_label: str
    dimension: str
    must_resolve: bool = False
    jfrog: CapabilityEvidenceCell
    competitor: CapabilityEvidenceCell
    readout: Literal[
        "jfrog_advantage",
        "competitor_advantage",
        "parity",
        "unclear",
    ] = "unclear"
    confidence: Confidence = "unknown"
    evidence_ids: tuple[str, ...] = ()
    search_status: CapabilitySearchStatus


class CapabilityEvidenceMatrix(ReportBaseModel):
    competitor: str
    generated_at: datetime = Field(default_factory=utc_now)
    capabilities: tuple[CapabilityDefinition, ...]
    rows: tuple[CapabilityEvidenceRow, ...]
    search_attempts: tuple[TargetedSearchAttempt, ...] = ()


class EvidencePack(ReportBaseModel):
    id: str
    competitor: str
    jfrog: str = "JFrog"
    focus: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    frozen: bool = True
    items: tuple[EvidenceItem, ...] = ()
    gaps: tuple[EvidenceGap, ...] = ()
    quality_notes: tuple[str, ...] = ()
    inventory: SourceInventoryReport | None = None
    readiness: EvidenceReadinessReport | None = None
    product_catalog: tuple[ProductCatalogItem, ...] = ()
    capability_matrix: CapabilityEvidenceMatrix | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _evidence_ids_unique(self) -> EvidencePack:
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence item ids must be unique")
        return self


class ReportClaim(ReportBaseModel):
    id: str
    text: str
    evidence_ids: tuple[str, ...] = ()
    confidence: Confidence = "unknown"
    claim_type: Literal["fact", "analysis", "missing"] = "analysis"


# --- Strategic frameworks (optional, additive; carried in ReportSection.metadata) ---
# These let analysts emit named analyst frameworks (PESTEL, Porter's Five Forces,
# strategic-group positioning, SWOT, confidence tiering) without changing draft_mode
# gating or the existing section contract. Every field defaults to empty so older
# drafts and minimal test fixtures keep validating.

FrameworkIntensity = Literal["high", "moderate", "low"]
PestelAxis = Literal[
    "political",
    "economic",
    "social",
    "technological",
    "environmental",
    "legal",
]
FiveForceName = Literal[
    "competitive_rivalry",
    "threat_of_new_entrants",
    "threat_of_substitutes",
    "buyer_power",
    "supplier_power",
]
ConfidenceTierName = Literal["high", "medium", "vendor_claim", "author_judgment"]


class PestelFactor(ReportBaseModel):
    axis: PestelAxis
    factor: str
    implication: str
    material: bool = True
    evidence_ids: tuple[str, ...] = ()


class FiveForce(ReportBaseModel):
    force: FiveForceName
    intensity: FrameworkIntensity
    rationale: str
    evidence_ids: tuple[str, ...] = ()


class PositioningPlayer(ReportBaseModel):
    name: str
    x: float = Field(ge=0.0, le=100.0)
    y: float = Field(ge=0.0, le=100.0)
    group: str | None = None
    is_focus: bool = False
    evidence_ids: tuple[str, ...] = ()


class PositioningMap(ReportBaseModel):
    x_axis_label: str
    x_low_label: str
    x_high_label: str
    y_axis_label: str
    y_low_label: str
    y_high_label: str
    players: tuple[PositioningPlayer, ...] = ()
    narrative: str | None = None


class SwotItem(ReportBaseModel):
    text: str
    evidence_ids: tuple[str, ...] = ()


class SwotQuadrants(ReportBaseModel):
    vantage: str
    strengths: tuple[SwotItem, ...] = ()
    weaknesses: tuple[SwotItem, ...] = ()
    opportunities: tuple[SwotItem, ...] = ()
    threats: tuple[SwotItem, ...] = ()


class ConfidenceTier(ReportBaseModel):
    tier: ConfidenceTierName
    summary: str


class ConfidenceTiering(ReportBaseModel):
    tiers: tuple[ConfidenceTier, ...] = ()
    spot_check: tuple[str, ...] = ()


class StrategyClaim(ReportBaseModel):
    text: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence = "unknown"

    @field_validator("text")
    @classmethod
    def _non_empty_claim_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("claim text is required")
        return cleaned


class StrategyAnalysis(ReportBaseModel):
    executive_thesis: StrategyClaim
    jfrog_advantages: tuple[StrategyClaim, ...] = Field(min_length=1)
    competitor_strengths: tuple[StrategyClaim, ...] = Field(min_length=1)
    risks: tuple[StrategyClaim, ...] = Field(min_length=1)
    likely_next_moves: tuple[StrategyClaim, ...] = Field(min_length=1)
    recommended_actions: tuple[StrategyClaim, ...] = Field(min_length=1)
    swot: SwotQuadrants | None = None
    confidence_tiering: ConfidenceTiering | None = None
    confidence_notes: tuple[str, ...] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence_notes")
    @classmethod
    def _non_empty_confidence_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(note.strip() for note in value if note.strip())
        if not cleaned:
            raise ValueError("at least one confidence note is required")
        return cleaned


class MarketClaim(ReportBaseModel):
    text: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence = "unknown"

    @field_validator("text")
    @classmethod
    def _non_empty_claim_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("claim text is required")
        return cleaned


class MarketAnalysis(ReportBaseModel):
    company_snapshot_thesis: MarketClaim
    jfrog_company_position: tuple[MarketClaim, ...] = Field(min_length=1)
    competitor_company_position: tuple[MarketClaim, ...] = Field(min_length=1)
    market_context_thesis: MarketClaim
    buyer_segments: tuple[MarketClaim, ...] = Field(min_length=1)
    go_to_market_motion: tuple[MarketClaim, ...] = Field(min_length=1)
    ecosystem_signals: tuple[MarketClaim, ...] = Field(min_length=1)
    market_risks: tuple[MarketClaim, ...] = Field(min_length=1)
    pestel: tuple[PestelFactor, ...] = ()
    five_forces: tuple[FiveForce, ...] = ()
    positioning_map: PositioningMap | None = None
    confidence_notes: tuple[str, ...] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence_notes")
    @classmethod
    def _non_empty_confidence_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(note.strip() for note in value if note.strip())
        if not cleaned:
            raise ValueError("at least one confidence note is required")
        return cleaned


class MarketOverviewAnalysis(ReportBaseModel):
    """Market-wide analysis for the standalone "Market & Strategic Context" report.

    Unlike MarketAnalysis (a JFrog-vs-competitor pairing), this describes the whole
    market: a thesis, the structural dynamics and risks, and the general PESTEL,
    Porter's Five Forces and an all-competitor positioning map. It is produced once
    per batch run by a dedicated analyst pass, independent of any single competitor.
    """

    market_thesis: MarketClaim
    market_dynamics: tuple[MarketClaim, ...] = Field(min_length=1)
    market_risks: tuple[MarketClaim, ...] = Field(min_length=1)
    pestel: tuple[PestelFactor, ...] = ()
    five_forces: tuple[FiveForce, ...] = ()
    positioning_map: PositioningMap | None = None
    confidence_notes: tuple[str, ...] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence_notes")
    @classmethod
    def _non_empty_confidence_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(note.strip() for note in value if note.strip())
        if not cleaned:
            raise ValueError("at least one confidence note is required")
        return cleaned


class TechnicalClaim(ReportBaseModel):
    text: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence = "unknown"

    @field_validator("text")
    @classmethod
    def _non_empty_claim_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("claim text is required")
        return cleaned


class TechnicalAnalysis(ReportBaseModel):
    technical_thesis: TechnicalClaim
    jfrog_platform_capabilities: tuple[TechnicalClaim, ...] = Field(min_length=1)
    competitor_platform_capabilities: tuple[TechnicalClaim, ...] = Field(min_length=1)
    architecture_and_workflow: tuple[TechnicalClaim, ...] = Field(min_length=1)
    ai_and_artifact_governance: tuple[TechnicalClaim, ...] = Field(min_length=1)
    security_capability_comparison: tuple[TechnicalClaim, ...] = Field(min_length=1)
    technical_risks: tuple[TechnicalClaim, ...] = Field(min_length=1)
    # LLMs following the skill naturally emit supply-chain security risks as a separate
    # field. Accept it here; technical_analysis_to_sections merges it into the security section.
    supply_chain_security_technical_risk: tuple[TechnicalClaim, ...] = Field(default=())
    confidence_notes: tuple[str, ...] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence_notes")
    @classmethod
    def _non_empty_confidence_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(note.strip() for note in value if note.strip())
        if not cleaned:
            raise ValueError("at least one confidence note is required")
        return cleaned


ProductFeatureAssessment = Literal[
    "jfrog_advantage",
    "competitor_advantage",
    "parity",
    "unclear",
]


class ProductFeatureClaim(ReportBaseModel):
    text: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence = "unknown"

    @field_validator("text")
    @classmethod
    def _non_empty_claim_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("claim text is required")
        return cleaned


class ProductFeatureCapability(ReportBaseModel):
    capability: str
    jfrog: str
    competitor: str
    assessment: ProductFeatureAssessment
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence = "unknown"

    @field_validator("capability", "jfrog", "competitor")
    @classmethod
    def _non_empty_capability_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("capability matrix text is required")
        return cleaned


class ProductFeatureAnalysis(ReportBaseModel):
    product_feature_thesis: ProductFeatureClaim
    capability_matrix: tuple[ProductFeatureCapability, ...] = Field(min_length=6)
    jfrog_feature_advantages: tuple[ProductFeatureClaim, ...] = Field(min_length=1)
    competitor_feature_advantages: tuple[ProductFeatureClaim, ...] = Field(min_length=1)
    jfrog_limitations: tuple[ProductFeatureClaim, ...] = Field(min_length=1)
    feature_parity_or_gaps: tuple[ProductFeatureClaim, ...] = Field(min_length=1)
    buyer_implications: tuple[ProductFeatureClaim, ...] = Field(min_length=1)
    confidence_notes: tuple[str, ...] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence_notes")
    @classmethod
    def _non_empty_confidence_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(note.strip() for note in value if note.strip())
        if not cleaned:
            raise ValueError("at least one confidence note is required")
        return cleaned


class BuyerFieldClaim(ReportBaseModel):
    text: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence = "unknown"

    @field_validator("text")
    @classmethod
    def _non_empty_claim_text(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("claim text is required")
        return cleaned


class BuyerFieldAnalysis(ReportBaseModel):
    buyer_fit_thesis: BuyerFieldClaim
    jfrog_win_conditions: tuple[BuyerFieldClaim, ...] = Field(min_length=1)
    competitor_win_conditions: tuple[BuyerFieldClaim, ...] = Field(min_length=1)
    field_battlecard_thesis: BuyerFieldClaim
    objection_handling: tuple[BuyerFieldClaim, ...] = Field(min_length=1)
    discovery_questions: tuple[BuyerFieldClaim, ...] = Field(min_length=1)
    qualify_out_signals: tuple[BuyerFieldClaim, ...] = Field(min_length=1)
    field_actions: tuple[BuyerFieldClaim, ...] = Field(min_length=1)
    confidence_notes: tuple[str, ...] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence_notes")
    @classmethod
    def _non_empty_confidence_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(note.strip() for note in value if note.strip())
        if not cleaned:
            raise ValueError("at least one confidence note is required")
        return cleaned


class ReportSection(ReportBaseModel):
    id: str
    title: str
    agent_key: str | None = None
    agent_name: str | None = None
    skill_name: str | None = None
    evidence_ids: tuple[str, ...] = ()
    claims: tuple[ReportClaim, ...] = ()
    narrative: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoreItem(ReportBaseModel):
    id: str
    company: str
    category: str
    value: float = Field(ge=0.0, le=5.0)
    max_value: float = 5.0
    rationale: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence = "unknown"
    buyer_archetype: str | None = None
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class ScoringAnalysis(ReportBaseModel):
    scores: tuple[ScoreItem, ...] = Field(min_length=2)
    confidence_notes: tuple[str, ...] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence_notes")
    @classmethod
    def _non_empty_confidence_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(note.strip() for note in value if note.strip())
        if not cleaned:
            raise ValueError("at least one confidence note is required")
        return cleaned


class ReportDraft(ReportBaseModel):
    competitor: str
    jfrog: str = "JFrog"
    generated_at: datetime = Field(default_factory=utc_now)
    evidence_pack_id: str
    sections: tuple[ReportSection, ...]
    scores: tuple[ScoreItem, ...] = ()
    missing_data: tuple[EvidenceGap, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationFinding(ReportBaseModel):
    severity: ValidationSeverity
    code: str
    message: str
    section_id: str | None = None
    claim_id: str | None = None
    evidence_ids: tuple[str, ...] = ()


class ValidationReport(ReportBaseModel):
    passed: bool
    findings: tuple[ValidationFinding, ...] = ()


class RenderResult(ReportBaseModel):
    format: Literal["json", "html", "pdf"]
    status: RenderStatus
    path: str | None = None
    message: str | None = None


class ReportRunResult(ReportBaseModel):
    evidence_pack: EvidencePack
    draft: ReportDraft
    validation: ValidationReport
    renders: tuple[RenderResult, ...] = ()
