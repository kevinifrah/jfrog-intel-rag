from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Confidence = Literal["high", "medium", "low", "unknown"]
AnswerStyle = Literal[
    "short_fact",
    "comparison",
    "technical_explanation",
    "field_guidance",
    "report_status",
    "not_enough_evidence",
]
EvidenceSource = Literal["db", "report", "tavily"]
TavilyDepth = Literal["ultra-fast", "fast"]
GroundingSeverity = Literal["error", "warning", "info"]


class ChatBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class PlannedToolCall(ChatBaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None

    @field_validator("tool")
    @classmethod
    def _tool_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("tool is required")
        return cleaned


class ChatWebCheck(ChatBaseModel):
    required: bool = False
    query: str | None = None
    depth: TavilyDepth = "ultra-fast"
    reason: str | None = None
    retry_with_fast_if_weak: bool = True


class ChatRetrievalPlan(ChatBaseModel):
    rewritten_question: str
    companies: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    report_sections: tuple[str, ...] = ()
    tool_calls: tuple[PlannedToolCall, ...] = ()
    web_check: ChatWebCheck = Field(default_factory=ChatWebCheck)
    answer_style: AnswerStyle = "short_fact"
    needs_sonnet: bool = False
    confidence: Confidence = "unknown"
    missing_evidence: tuple[str, ...] = ()

    @field_validator("rewritten_question")
    @classmethod
    def _question_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("rewritten_question is required")
        return cleaned


class ChatEvidenceItem(ChatBaseModel):
    id: str
    source: EvidenceSource
    text: str
    company: str | None = None
    title: str | None = None
    url: str | None = None
    publisher: str | None = None
    section: str | None = None
    dimension: str | None = None
    source_id: int | None = None
    chunk_id: int | None = None
    published: str | None = None
    confidence: Confidence = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "text")
    @classmethod
    def _required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("id and text are required")
        return cleaned


class ChatSource(ChatBaseModel):
    id: str
    title: str | None = None
    url: str | None = None
    source: EvidenceSource
    company: str | None = None

    @field_validator("id")
    @classmethod
    def _id_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("id is required")
        return cleaned


class ChatAnswer(ChatBaseModel):
    answer: str
    confidence: Confidence = "unknown"
    sources: tuple[ChatSource, ...] = ()
    used_tools: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    followups: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("answer")
    @classmethod
    def _answer_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("answer is required")
        return cleaned


class ChatGroundingFinding(ChatBaseModel):
    severity: GroundingSeverity
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()


class ChatRequest(ChatBaseModel):
    question: str
    competitor: str | None = None
    report_slug: str | None = None
    include_web: bool = True
    max_evidence: int = Field(default=8, ge=1, le=30)

    @field_validator("question")
    @classmethod
    def _question_text_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question is required")
        return cleaned


class ChatToolResult(ChatBaseModel):
    evidence: tuple[ChatEvidenceItem, ...] = ()
    used_tools: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
