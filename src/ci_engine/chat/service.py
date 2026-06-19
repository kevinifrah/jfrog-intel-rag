from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from ci_engine.chat.answer import AnswerRunner, write_answer
from ci_engine.chat.planner import PlannerRunner, plan_chat
from ci_engine.chat.retrieval import McpChatExecutor
from ci_engine.chat.schemas import ChatAnswer, ChatEvidenceItem, ChatRequest
from ci_engine.chat.web import select_tavily_depth, web_check_with_retry
from ci_engine.config import get as config_get


def run_chat(
    request: ChatRequest,
    *,
    report_root: str | None = None,
    planner_runner: PlannerRunner | None = None,
    answer_runner: AnswerRunner | None = None,
    fallback_runner: AnswerRunner | None = None,
    web_search_fn: Any | None = None,
) -> ChatAnswer:
    plan = plan_chat(request, runner=planner_runner)
    executor = McpChatExecutor(
        report_root=report_root or str(config_get("chat.report_root", "reports"))
    )
    tool_result = executor.execute_plan(plan)
    evidence = list(tool_result.evidence)
    used_tools = list(tool_result.used_tools)
    missing = list(tool_result.missing_evidence)
    web_metadata: dict[str, Any] = {"status": "skipped"}

    if plan.web_check.required:
        query = plan.web_check.query or plan.rewritten_question
        depth = select_tavily_depth(
            query,
            required=True,
            current_depth=plan.web_check.depth,
            evidence_count=len(evidence),
        )
        web_items, web_metadata = web_check_with_retry(
            query,
            depth=depth,
            retry_with_fast_if_weak=plan.web_check.retry_with_fast_if_weak,
            max_results=int(config_get("chat.tavily_max_results", 3)),
            search_fn=web_search_fn,
        )
        evidence.extend(web_items)
        used_tools.append(f"tavily:{web_metadata.get('depth', depth)}")
        if web_metadata.get("status") == "error":
            missing.append(f"web check failed: {web_metadata.get('error')}")

    evidence = _trim_evidence(
        _rank_evidence_for_question(evidence, request.question),
        request.max_evidence,
    )
    answer, findings = write_answer(
        request,
        plan,
        evidence,
        used_tools=used_tools,
        missing_evidence=missing,
        web_metadata=web_metadata,
        runner=answer_runner,
        fallback_runner=fallback_runner,
    )
    return answer.model_copy(
        update={
            "metadata": {
                **answer.metadata,
                "plan": plan.model_dump(mode="json"),
                "grounding_findings": [
                    finding.model_dump(mode="json") for finding in findings
                ],
                "evidence_count": len(evidence),
                "web": web_metadata,
            }
        }
    )


async def stream_chat_events(
    request: ChatRequest,
    *,
    report_root: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    yield {"event": "status", "message": "planning"}
    answer = run_chat(request, report_root=report_root)
    yield {"event": "answer", "data": answer.model_dump(mode="json")}
    yield {"event": "done"}


def _trim_evidence(
    evidence: Sequence[ChatEvidenceItem],
    max_items: int,
) -> list[ChatEvidenceItem]:
    limit = max(int(max_items or 8), 1)
    return list(evidence[: limit + 3])


def _rank_evidence_for_question(
    evidence: Sequence[ChatEvidenceItem],
    question: str,
) -> list[ChatEvidenceItem]:
    terms = _search_terms(question)
    ranked = sorted(
        evidence,
        key=lambda item: (
            _evidence_score(item, terms),
            item.confidence == "high",
            item.confidence == "medium",
            item.source == "db",
            item.source == "report",
        ),
        reverse=True,
    )
    seen: set[tuple[str, str]] = set()
    deduped: list[ChatEvidenceItem] = []
    for item in ranked:
        key = (item.source, item.id)
        text_key = (item.source, item.text[:220])
        if key in seen or text_key in seen:
            continue
        seen.add(key)
        seen.add(text_key)
        deduped.append(item)
    return deduped


def _evidence_score(item: ChatEvidenceItem, terms: Sequence[str]) -> int:
    text = " ".join(
        str(value or "")
        for value in (
            item.text,
            item.title,
            item.company,
            item.section,
            item.dimension,
            item.publisher,
        )
    ).lower()
    score = sum(2 for term in terms if term in text)
    if item.source == "db":
        score += 3
    if item.source == "report":
        score += 2
    if item.source == "tavily":
        score += 1
    if item.confidence == "high":
        score += 3
    elif item.confidence == "medium":
        score += 2
    return score


def _search_terms(question: str) -> tuple[str, ...]:
    stop_words = {
        "about",
        "against",
        "compare",
        "does",
        "from",
        "have",
        "main",
        "what",
        "where",
        "which",
        "with",
    }
    normalized = "".join(
        char.lower() if char.isalnum() else " " for char in str(question or "")
    )
    return tuple(
        dict.fromkeys(
            token
            for token in normalized.split()
            if len(token) >= 3 and token not in stop_words
        )
    )
