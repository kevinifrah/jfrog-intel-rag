from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from ci_engine.chat.schemas import (
    ChatAnswer,
    ChatEvidenceItem,
    ChatGroundingFinding,
    ChatRequest,
    ChatRetrievalPlan,
    ChatSource,
)
from ci_engine.config import get as config_get
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret
from ci_engine.skills import compose

AnswerRunner = Callable[[str], dict[str, Any] | str]


def write_answer(
    request: ChatRequest,
    plan: ChatRetrievalPlan,
    evidence: Sequence[ChatEvidenceItem],
    *,
    used_tools: Sequence[str],
    missing_evidence: Sequence[str],
    web_metadata: dict[str, Any] | None = None,
    runner: AnswerRunner | None = None,
    fallback_runner: AnswerRunner | None = None,
) -> tuple[ChatAnswer, tuple[ChatGroundingFinding, ...]]:
    if not evidence:
        answer = _not_enough_evidence_answer(
            missing_evidence=missing_evidence or ("no supporting evidence retrieved",),
            used_tools=used_tools,
            web_metadata=web_metadata,
        )
        return answer, validate_grounding(answer, evidence)

    prompt = build_answer_prompt(
        request,
        plan,
        evidence,
        used_tools=used_tools,
        missing_evidence=missing_evidence,
        web_metadata=web_metadata,
    )
    initial_runner = runner
    if initial_runner is None:
        initial_runner = (
            fallback_runner or AnthropicAnswerRunner("models.chat_fallback").run
            if plan.needs_sonnet
            else AnthropicAnswerRunner("models.chat_answer").run
        )
    answer = _run_answer(
        prompt,
        runner=initial_runner,
        evidence=evidence,
        used_tools=used_tools,
        missing_evidence=missing_evidence,
        web_metadata=web_metadata,
    )
    findings = validate_grounding(answer, evidence)
    if _has_grounding_errors(findings) and not plan.needs_sonnet:
        fallback = fallback_runner or AnthropicAnswerRunner("models.chat_fallback").run
        answer = _run_answer(
            prompt,
            runner=fallback,
            evidence=evidence,
            used_tools=used_tools,
            missing_evidence=missing_evidence,
            web_metadata=web_metadata,
        )
        findings = validate_grounding(answer, evidence)
    return answer, findings


def build_answer_prompt(
    request: ChatRequest,
    plan: ChatRetrievalPlan,
    evidence: Sequence[ChatEvidenceItem],
    *,
    used_tools: Sequence[str],
    missing_evidence: Sequence[str],
    web_metadata: dict[str, Any] | None = None,
) -> str:
    system = compose("chat-answer-writer", "chat-grounding-checker")
    payload = {
        "question": request.question,
        "selected_competitor": request.competitor,
        "selected_report_slug": request.report_slug,
        "answer_style": plan.answer_style,
        "retrieval_context": {
            "rewritten_question": plan.rewritten_question,
            "companies": list(plan.companies),
            "dimensions": list(plan.dimensions),
            "web_check": plan.web_check.model_dump(mode="json"),
        },
        "used_tools": list(used_tools),
        "missing_evidence": list(missing_evidence)[:6],
        "web_metadata": web_metadata or {},
        "evidence": _answer_evidence_payload(evidence),
    }
    return (
        f"{system}\n\n"
        "Return only the required JSON object. Evidence IDs are citation handles for "
        "the `sources` array; do not print raw IDs in the prose unless essential.\n"
        f"{json.dumps(payload, ensure_ascii=True, indent=2)}"
    )


def _answer_evidence_payload(
    evidence: Sequence[ChatEvidenceItem],
) -> list[dict[str, Any]]:
    max_items = int(config_get("chat.answer_prompt_max_evidence", 8))
    text_limit = int(config_get("chat.answer_evidence_text_chars", 700))
    return [
        {
            "id": item.id,
            "source": item.source,
            "company": item.company,
            "title": item.title,
            "url": item.url,
            "publisher": item.publisher,
            "section": item.section,
            "dimension": item.dimension,
            "confidence": item.confidence,
            "text": _truncate(item.text, text_limit),
        }
        for item in evidence[:max_items]
    ]


def validate_grounding(
    answer: ChatAnswer,
    evidence: Sequence[ChatEvidenceItem],
) -> tuple[ChatGroundingFinding, ...]:
    findings: list[ChatGroundingFinding] = []
    evidence_ids = {item.id for item in evidence}
    answer_source_ids = {source.id for source in answer.sources}
    unknown_ids = sorted(answer_source_ids - evidence_ids)
    if unknown_ids:
        findings.append(
            ChatGroundingFinding(
                severity="error",
                code="unknown_source_id",
                message="Answer cites sources that were not retrieved.",
                evidence_ids=tuple(unknown_ids),
            )
        )
    if evidence and not answer.sources:
        findings.append(
            ChatGroundingFinding(
                severity="error",
                code="missing_sources",
                message="Answer used evidence but returned no structured sources.",
            )
        )
    if not evidence and "not enough evidence" not in answer.answer.lower():
        findings.append(
            ChatGroundingFinding(
                severity="error",
                code="unsupported_answer",
                message="No evidence was retrieved, but the answer did not fail closed.",
            )
        )
    if _looks_like_unsupported_superiority(answer.answer) and not answer.sources:
        findings.append(
            ChatGroundingFinding(
                severity="error",
                code="unsupported_superiority",
                message="Answer makes a superiority claim without citations.",
            )
        )
    return tuple(findings)


class AnthropicAnswerRunner:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def run(self, prompt: str) -> str:
        from anthropic import Anthropic  # noqa: PLC0415

        client = Anthropic(api_key=get_secret("anthropic-key"), max_retries=0)
        response = client.messages.create(
            model=str(config_get(f"{self.model_path}.name", "claude-haiku-4-5")),
            max_tokens=int(config_get(f"{self.model_path}.max_tokens", 1200)),
            temperature=float(config_get(f"{self.model_path}.temperature", 0.1)),
            messages=[{"role": "user", "content": prompt}],
            output_config=_answer_output_config(),
            timeout=float(config_get(f"{self.model_path}.timeout_s", 12)),
        )
        return _response_text(response)


def _run_answer(
    prompt: str,
    *,
    runner: AnswerRunner,
    evidence: Sequence[ChatEvidenceItem],
    used_tools: Sequence[str],
    missing_evidence: Sequence[str],
    web_metadata: dict[str, Any] | None,
) -> ChatAnswer:
    try:
        raw = runner(prompt)
        payload = parse_json_object(raw if isinstance(raw, str) else json.dumps(raw), label="chat answer")
        return ChatAnswer.model_validate(payload)
    except Exception as exc:
        return _deterministic_answer(
            evidence,
            used_tools=used_tools,
            missing_evidence=missing_evidence,
            web_metadata=web_metadata,
            fallback_error=str(exc),
        )


def _deterministic_answer(
    evidence: Sequence[ChatEvidenceItem],
    *,
    used_tools: Sequence[str],
    missing_evidence: Sequence[str],
    web_metadata: dict[str, Any] | None,
    fallback_error: str | None = None,
) -> ChatAnswer:
    selected = list(evidence[:3])
    if not selected:
        return _not_enough_evidence_answer(
            missing_evidence=missing_evidence,
            used_tools=used_tools,
            web_metadata=web_metadata,
        )
    lines = [_fallback_answer(selected)]
    if missing_evidence:
        lines.append(
            "The answer should still be treated with some caution because "
            + "; ".join(missing_evidence[:3])
            + "."
        )
    return ChatAnswer(
        answer="\n\n".join(lines),
        confidence=_combined_confidence(selected),
        sources=tuple(_source_from_evidence(item) for item in selected),
        used_tools=tuple(dict.fromkeys(used_tools)),
        missing_evidence=tuple(dict.fromkeys(missing_evidence)),
        metadata={
            "mode": "deterministic_fallback",
            "fallback_error": fallback_error,
            "web": web_metadata or {},
        },
    )


def _not_enough_evidence_answer(
    *,
    missing_evidence: Sequence[str],
    used_tools: Sequence[str],
    web_metadata: dict[str, Any] | None,
) -> ChatAnswer:
    missing = tuple(dict.fromkeys(str(item) for item in missing_evidence if str(item).strip()))
    detail = "; ".join(missing[:4]) if missing else "no supporting evidence retrieved"
    return ChatAnswer(
        answer=(
            "Not enough evidence to answer this confidently yet. "
            f"The missing piece is: {detail}. A reliable answer needs stronger "
            "company-specific evidence before it should guide an executive decision."
        ),
        confidence="unknown",
        sources=(),
        used_tools=tuple(dict.fromkeys(used_tools)),
        missing_evidence=missing,
        metadata={"mode": "fail_closed", "web": web_metadata or {}},
    )


def _source_from_evidence(item: ChatEvidenceItem) -> ChatSource:
    return ChatSource(
        id=item.id,
        title=_human_source_title(item),
        url=item.url,
        source=item.source,
        company=item.company,
    )


def _combined_confidence(items: Sequence[ChatEvidenceItem]) -> str:
    if not items:
        return "unknown"
    values = {item.confidence for item in items}
    if values == {"high"}:
        return "high"
    if "medium" in values or "high" in values:
        return "medium"
    if "low" in values:
        return "low"
    return "unknown"


def _has_grounding_errors(findings: Sequence[ChatGroundingFinding]) -> bool:
    return any(finding.severity == "error" for finding in findings)


def _looks_like_unsupported_superiority(text: str) -> bool:
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "best",
            "clear leader",
            "decisive advantage",
            "more complete",
            "superior",
        )
    )


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def _fallback_answer(items: Sequence[ChatEvidenceItem]) -> str:
    product_names = _known_products_from_evidence(items)
    if product_names:
        products = ", ".join(product_names[:8])
        return (
            "JFrog's portfolio is best understood as a software supply chain "
            f"platform rather than a single security tool. The retrieved evidence "
            f"supports these main product families: {products}. In practical terms, "
            "Artifactory is the system of record for binaries and packages, while "
            "the security and governance products extend control over what enters, "
            "moves through, and ships from the software supply chain."
        )

    companies = [item.company for item in items if item.company]
    company_text = " and ".join(dict.fromkeys(companies[:2]))
    if company_text:
        return (
            f"The evidence points to a practical distinction around {company_text}, "
            "but the answer is limited by what the retrieved sources support."
        )
    return (
        "The available evidence gives a partial answer, but it is not strong enough "
        "to treat as a complete competitive readout."
    )


def _known_products_from_evidence(
    items: Sequence[ChatEvidenceItem],
) -> list[str]:
    names = (
        "Artifactory",
        "Xray",
        "Curation",
        "Advanced Security",
        "Runtime",
        "Distribution",
        "Connect",
        "AppTrust",
        "Evidence",
        "JFrog ML",
        "AI Catalog",
        "MCP Registry",
        "Agent Skills Registry",
        "FrogBot",
        "Workers",
        "Projects",
        "CLI",
    )
    haystack = " ".join(item.text for item in items).lower()
    return [name for name in names if name.lower() in haystack]


def _human_source_title(item: ChatEvidenceItem) -> str | None:
    if item.source == "report":
        if item.section == "scoring":
            return "Generated scorecard"
        if item.metadata.get("kind") == "validation_finding":
            return "Report readiness review"
        if item.metadata.get("kind") == "missing_data":
            return "Evidence gap review"
        if item.section:
            return "Generated competitor dossier"
    if item.source == "tavily":
        publisher = item.publisher or _publisher_from_url(item.url)
        if publisher:
            return f"Public web source: {publisher}"
        return "Public web source"
    if item.source == "db":
        title = item.title or ""
        lower = title.lower()
        if "jfrog" in lower or (item.url or "").lower().find("jfrog.com") >= 0:
            return "JFrog product documentation"
        if "sonatype" in lower or (item.url or "").lower().find("sonatype.com") >= 0:
            return "Sonatype documentation"
        if item.dimension:
            return item.dimension.replace("_", " ").title()
    return item.title


def _publisher_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    return host.removeprefix("www.") or None


def _answer_output_config() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "confidence": {"enum": ["high", "medium", "low", "unknown"]},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "source": {"enum": ["db", "report", "tavily"]},
                                "company": {"type": "string"},
                            },
                            "required": ["id", "title", "url", "source", "company"],
                            "additionalProperties": False,
                        },
                    },
                    "used_tools": {"type": "array", "items": {"type": "string"}},
                    "missing_evidence": {"type": "array", "items": {"type": "string"}},
                    "followups": {"type": "array", "items": {"type": "string"}},
                    "metadata": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "answer",
                    "confidence",
                    "sources",
                    "used_tools",
                    "missing_evidence",
                    "followups",
                    "metadata",
                ],
                "additionalProperties": False,
            },
        }
    }


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()
