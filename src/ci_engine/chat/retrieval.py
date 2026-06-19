from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from ci_engine.chat.schemas import (
    ChatEvidenceItem,
    ChatRetrievalPlan,
    ChatToolResult,
    PlannedToolCall,
)

ALLOWED_TOOLS = {
    "search_answer_context",
    "search_report_sections",
    "get_report_registry",
    "search",
    "compare_dimension",
    "coverage_matrix",
    "source_inventory",
    "get_source_detail",
}


class McpChatExecutor:
    def __init__(self, *, report_root: str | None = None) -> None:
        self.report_root = report_root

    def execute_plan(self, plan: ChatRetrievalPlan) -> ChatToolResult:
        evidence: list[ChatEvidenceItem] = []
        used_tools: list[str] = []
        missing: list[str] = [*plan.missing_evidence]
        for call in plan.tool_calls:
            if call.tool not in ALLOWED_TOOLS:
                missing.append(f"tool not allowed: {call.tool}")
                continue
            result = self._execute_call(call)
            used_tools.append(call.tool)
            evidence.extend(_evidence_from_result(call.tool, result))
            missing.extend(_missing_from_result(result))

        deduped = _dedupe_evidence(evidence)
        return ChatToolResult(
            evidence=tuple(deduped),
            used_tools=tuple(dict.fromkeys(used_tools)),
            missing_evidence=tuple(dict.fromkeys(str(item) for item in missing if item)),
            metadata={"evidence_count": len(deduped)},
        )

    def _execute_call(self, call: PlannedToolCall) -> Mapping[str, Any]:
        from ci_engine.mcp import server  # noqa: PLC0415

        args = _clean_arguments(dict(call.arguments))
        if self.report_root and call.tool in {
            "search_answer_context",
            "search_report_sections",
            "get_report_registry",
        }:
            args.setdefault("report_root", self.report_root)

        tool = getattr(server, call.tool)
        result = tool(**args)
        return result if isinstance(result, Mapping) else {"result": result}


def _evidence_from_result(
    tool: str,
    result: Mapping[str, Any],
) -> list[ChatEvidenceItem]:
    evidence: list[ChatEvidenceItem] = []
    for item in _list(result.get("items")):
        converted = _item_from_mapping(item, default_source="report" if tool == "search_report_sections" else "db")
        if converted:
            evidence.append(converted)
    for chunk in _list(result.get("chunks")):
        converted = _item_from_chunk(chunk, tool=tool)
        if converted:
            evidence.append(converted)
    for competitor in _list(result.get("competitors")):
        for dimension in _list(competitor.get("dimensions")):
            for chunk in _list(dimension.get("chunks")):
                chunk.setdefault("competitor", competitor.get("competitor"))
                chunk.setdefault("dimension", dimension.get("dimension"))
                converted = _item_from_chunk(chunk, tool=tool)
                if converted:
                    evidence.append(converted)
    for source in _list(result.get("sources")):
        for chunk in _list(source.get("chunks")):
            merged = {**source, **chunk}
            converted = _item_from_chunk(merged, tool=tool)
            if converted:
                evidence.append(converted)
        if not source.get("chunks"):
            converted = _item_from_source(source, tool=tool)
            if converted:
                evidence.append(converted)
    for row in _list(result.get("coverage")):
        evidence.append(_item_from_coverage(row, tool=tool))
    for row in _list(result.get("reports")):
        evidence.append(_item_from_report_summary(row))
    return evidence


def _item_from_mapping(
    item: Mapping[str, Any],
    *,
    default_source: str,
) -> ChatEvidenceItem | None:
    text = _clean_text(item.get("text") or item.get("quote") or item.get("summary"))
    item_id = _clean_text(item.get("id"))
    if not text or not item_id:
        return None
    source = _clean_text(item.get("source")) or default_source
    if source not in {"db", "report", "tavily"}:
        source = default_source
    return ChatEvidenceItem(
        id=item_id,
        source=source,  # type: ignore[arg-type]
        text=text,
        company=_clean_text(item.get("company")),
        title=_clean_text(item.get("title")),
        url=_clean_text(item.get("url")),
        publisher=_clean_text(item.get("publisher")),
        section=_clean_text(item.get("section") or item.get("report_section")),
        dimension=_clean_text(item.get("dimension")),
        source_id=_optional_int(item.get("source_id")),
        chunk_id=_optional_int(item.get("chunk_id")),
        published=_clean_text(item.get("published") or item.get("publish_date")),
        confidence=_confidence(item.get("confidence")),
        metadata=dict(item.get("metadata") or {}),
    )


def _item_from_chunk(chunk: Mapping[str, Any], *, tool: str) -> ChatEvidenceItem | None:
    text = _clean_text(chunk.get("chunk_text") or chunk.get("text"))
    if not text:
        return None
    source_id = _optional_int(chunk.get("source_id"))
    chunk_id = _optional_int(chunk.get("chunk_id"))
    return ChatEvidenceItem(
        id=_stable_id("db", source_id, chunk_id, text),
        source="db",
        text=text,
        company=_clean_text(chunk.get("competitor") or chunk.get("company")),
        title=_clean_text(chunk.get("title")),
        url=_clean_text(chunk.get("url")),
        section=_clean_text(chunk.get("section") or chunk.get("report_section")),
        dimension=_clean_text(chunk.get("dimension")),
        source_id=source_id,
        chunk_id=chunk_id,
        published=_clean_text(chunk.get("publish_date") or chunk.get("published")),
        confidence=_confidence_from_similarity(chunk.get("similarity")),
        metadata={
            "tool": tool,
            "axis": chunk.get("axis"),
            "doc_type": chunk.get("doc_type"),
            "source_kind": chunk.get("source_kind"),
        },
    )


def _item_from_source(source: Mapping[str, Any], *, tool: str) -> ChatEvidenceItem | None:
    title = _clean_text(source.get("title") or source.get("url"))
    if not title:
        return None
    text = "Source available"
    if source.get("doc_type"):
        text += f": {source.get('doc_type')}"
    return ChatEvidenceItem(
        id=_stable_id("source", source.get("source_id"), title),
        source="db",
        text=text,
        company=_clean_text(source.get("competitor")),
        title=title,
        url=_clean_text(source.get("url")),
        source_id=_optional_int(source.get("source_id")),
        published=_clean_text(source.get("publish_date")),
        confidence="low",
        metadata={"tool": tool, "kind": "source_inventory"},
    )


def _item_from_coverage(row: Mapping[str, Any], *, tool: str) -> ChatEvidenceItem:
    company = _clean_text(row.get("competitor")) or "unknown company"
    dimension = _clean_text(row.get("dimension")) or "unknown dimension"
    state = _clean_text(row.get("coverage_state")) or "unknown"
    confidence = row.get("coverage_confidence")
    active_sources = row.get("active_sources")
    text = (
        f"{company} coverage for {dimension}: state={state}, "
        f"active_sources={active_sources}, confidence={confidence}."
    )
    return ChatEvidenceItem(
        id=_stable_id("coverage", company, dimension, state, confidence),
        source="db",
        text=text,
        company=company,
        dimension=dimension,
        confidence="medium" if state == "present" else "low",
        metadata={"tool": tool, "kind": "coverage"},
    )


def _item_from_report_summary(row: Mapping[str, Any]) -> ChatEvidenceItem:
    slug = _clean_text(row.get("slug")) or "report"
    competitor = _clean_text(row.get("competitor")) or slug
    pdf_status = _clean_text(row.get("pdf_status")) or "missing"
    validation = row.get("validation_passed")
    text = (
        f"Report {slug} ({competitor}) generated_at={row.get('generated_at')}; "
        f"validation_passed={validation}; pdf_status={pdf_status}; "
        f"blocker_codes={', '.join(row.get('blocker_codes') or []) or 'none'}."
    )
    return ChatEvidenceItem(
        id=_stable_id("report_registry", slug, row.get("generated_at"), pdf_status),
        source="report",
        text=text,
        company=competitor,
        title=_clean_text(row.get("title")) or f"JFrog vs {competitor}",
        confidence="high",
        metadata={"kind": "report_registry", "slug": slug, **dict(row)},
    )


def _missing_from_result(result: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for item in _list(result.get("missing")):
        reason = _clean_text(item.get("reason")) or "missing"
        company = _clean_text(item.get("competitor") or item.get("company"))
        dimension = _clean_text(item.get("dimension"))
        parts = [part for part in (company, dimension, reason) if part]
        missing.append(": ".join(parts))
    for item in result.get("missing_evidence") or ():
        if str(item).strip():
            missing.append(str(item).strip())
    return missing


def _dedupe_evidence(items: Sequence[ChatEvidenceItem]) -> list[ChatEvidenceItem]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ChatEvidenceItem] = []
    for item in items:
        key = (item.source, item.id)
        text_key = (item.source, item.text[:240])
        if key in seen or text_key in seen:
            continue
        seen.add(key)
        seen.add(text_key)
        deduped.append(item)
    return deduped


def _clean_arguments(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if value is not None}


def _list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _confidence(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in {"high", "medium", "low", "unknown"} else "unknown"


def _confidence_from_similarity(value: Any) -> str:
    try:
        similarity = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if similarity >= 0.75:
        return "high"
    if similarity >= 0.6:
        return "medium"
    return "low"


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _stable_id(*parts: Any) -> str:
    return hashlib.sha256(
        "||".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
