from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from ci_engine.chat.schemas import (
    ChatRequest,
    ChatRetrievalPlan,
    ChatWebCheck,
    PlannedToolCall,
)
from ci_engine.chat.tool_cards import tool_cards_prompt
from ci_engine.chat.web import select_tavily_depth
from ci_engine.config import get as config_get
from ci_engine.config import tracked_companies
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret
from ci_engine.skills import compose

PlannerRunner = Callable[[str], dict[str, Any] | str]

_DIMENSION_HINTS: dict[str, tuple[str, ...]] = {
    "product_portfolio": ("product", "portfolio", "main product"),
    "artifact_management": ("artifact", "artifactory", "repository", "binary"),
    "sbom_generation": ("sbom",),
    "software_composition_analysis": ("sca", "composition", "open source"),
    "reachability_analysis": ("reachability", "reachable"),
    "cve_contextual_analysis": ("cve", "contextual", "prioritization"),
    "malicious_package_detection": ("malicious", "malware"),
    "package_firewall": ("firewall", "curation", "admission"),
    "license_compliance": ("license",),
    "policy_governance": ("policy", "governance"),
    "ci_cd_ide_integrations": ("ci/cd", "ide", "integration"),
    "market_positioning": ("market", "positioning"),
    "pricing_packaging": ("pricing", "packaging"),
    "customers_case_studies": ("customer", "case study", "reference"),
}


def plan_chat(
    request: ChatRequest,
    *,
    runner: PlannerRunner | None = None,
) -> ChatRetrievalPlan:
    if _use_deterministic_planner(request):
        return strengthen_plan(request, build_fallback_plan(request))
    if runner is None:
        runner = AnthropicPlanner().run
    prompt = build_planner_prompt(request)
    try:
        raw = runner(prompt)
        payload = parse_json_object(raw if isinstance(raw, str) else json.dumps(raw), label="chat planner")
        return strengthen_plan(request, ChatRetrievalPlan.model_validate(payload))
    except Exception:
        return strengthen_plan(request, build_fallback_plan(request))


def build_planner_prompt(request: ChatRequest) -> str:
    system = compose(
        "chat-query-planner",
        "chat-mcp-tool-use",
        "chat-web-depth-selector",
    )
    payload = {
        "question": request.question,
        "competitor": request.competitor,
        "report_slug": request.report_slug,
        "include_web": request.include_web,
        "max_evidence": request.max_evidence,
    }
    return (
        f"{system}\n\n{tool_cards_prompt()}\n\n"
        "Return only the required JSON object. User request:\n"
        f"{json.dumps(payload, ensure_ascii=True, indent=2)}"
    )


def build_fallback_plan(request: ChatRequest) -> ChatRetrievalPlan:
    question = request.question.strip()
    companies = _companies_from_request(request)
    dimensions = _dimensions_from_question(question)
    style = _answer_style(question)
    web_required = request.include_web and _requires_web(question, dimensions)
    depth = select_tavily_depth(
        question,
        required=web_required,
        evidence_count=0,
    )
    tool_calls: list[PlannedToolCall] = []
    if style == "report_status":
        tool_calls.append(
            PlannedToolCall(
                tool="get_report_registry",
                arguments={},
                reason="Report registry answers report status and PDF availability.",
            )
        )
        if request.report_slug or any(term in question.lower() for term in ("why", "block", "validation")):
            tool_calls.append(
                PlannedToolCall(
                    tool="search_report_sections",
                    arguments={
                        "query": question,
                        "competitors": list(companies) or None,
                        "max_items": request.max_evidence,
                    },
                    reason="Report section search explains validation or PDF blockers.",
                )
            )
    else:
        tool_calls.append(
            PlannedToolCall(
                tool="search_answer_context",
                arguments={
                    "query": question,
                    "competitors": list(companies) or None,
                    "dimensions": list(dimensions) or None,
                    "include_reports": True,
                    "max_items": request.max_evidence,
                },
                reason="Default fast evidence retrieval for grounded chat answer.",
            )
        )
        if request.report_slug:
            tool_calls.append(
                PlannedToolCall(
                    tool="search_report_sections",
                    arguments={
                        "query": question,
                        "competitors": list(companies) or None,
                        "max_items": max(3, request.max_evidence // 2),
                    },
                    reason="Selected report may contain relevant generated analysis.",
                )
            )

    return ChatRetrievalPlan(
        rewritten_question=question,
        companies=companies,
        dimensions=dimensions,
        tool_calls=tuple(tool_calls),
        web_check=ChatWebCheck(
            required=web_required,
            query=question if web_required else None,
            depth=depth,
            reason="freshness or product validation requested" if web_required else None,
        ),
        answer_style=style,
        needs_sonnet=style in {"comparison", "field_guidance"} and len(dimensions) > 2,
        confidence="medium",
    )


def strengthen_plan(
    request: ChatRequest,
    plan: ChatRetrievalPlan,
) -> ChatRetrievalPlan:
    question = request.question.strip()
    lower = question.lower()
    companies = _merge(plan.companies, _companies_from_request(request))
    dimensions = _merge(plan.dimensions, _dimensions_from_question(question))
    is_comparison = _is_comparison_question(lower)
    is_weakness = _is_weakness_question(lower)
    is_product = _is_product_question(lower)
    is_security = _is_security_question(lower)
    is_gap = _is_gap_question(lower)
    is_direct_jfrog = _is_direct_jfrog_question(lower, request)
    if is_direct_jfrog:
        companies = ("JFrog",)
        is_comparison = False
    style = plan.answer_style
    if is_direct_jfrog and style == "comparison":
        style = "technical_explanation" if (is_product or is_security) else "short_fact"
    if is_comparison or is_weakness:
        style = "comparison"
    elif is_security or is_product:
        style = "technical_explanation"

    if is_security and not dimensions:
        dimensions = _merge(dimensions, _SECURITY_DIMENSIONS)
    if is_product and not dimensions:
        dimensions = _merge(dimensions, ("product_portfolio", *tuple(_PRODUCT_DIMENSIONS)))

    tool_calls = (
        []
        if is_direct_jfrog
        else [call for call in plan.tool_calls if _valid_planned_call(call)]
    )

    def add_call(tool: str, arguments: dict[str, Any], reason: str) -> None:
        call = PlannedToolCall(tool=tool, arguments=arguments, reason=reason)
        key = (call.tool, json.dumps(call.arguments, sort_keys=True))
        existing = {
            (existing_call.tool, json.dumps(existing_call.arguments, sort_keys=True))
            for existing_call in tool_calls
        }
        if key not in existing:
            tool_calls.append(call)

    max_items = max(request.max_evidence, int(config_get("chat.max_evidence_items", 8)))
    if style != "report_status" and not any(
        call.tool == "search_answer_context" for call in tool_calls
    ):
        add_call(
            "search_answer_context",
            {
                "query": question,
                "competitors": list(companies) or None,
                "dimensions": list(dimensions) or None,
                "include_reports": True,
                "max_items": max_items,
            },
            "Default evidence retrieval for the user question.",
        )

    if request.report_slug or is_comparison or is_weakness or is_product or is_security:
        add_call(
            "search_report_sections",
            {
                "query": question,
                "competitors": list(companies) or None,
                "max_items": max(4, max_items // 2),
            },
            "Generated dossiers may contain synthesized analysis and caveats.",
        )

    if dimensions and (is_comparison or is_weakness or is_product or is_security or is_gap):
        add_call(
            "coverage_matrix",
            {
                "competitors": list(companies) or None,
                "dimensions": list(dimensions) or None,
            },
            "Coverage status helps decide whether the answer is sufficiently supported.",
        )

    if len(companies) >= 2 and dimensions and (is_comparison or is_weakness):
        for dimension in dimensions[:4]:
            add_call(
                "compare_dimension",
                {"names": list(companies), "dimension": dimension},
                "Side-by-side dimension evidence is needed for a fair comparison.",
            )

    if is_comparison or is_weakness or is_security:
        topic = _topic_phrase(lower)
        for company in companies:
            for angle in ("strengths", "weaknesses limitations risks"):
                add_call(
                    "search_answer_context",
                    {
                        "query": f"{company} {topic} {angle}",
                        "competitors": [company],
                        "dimensions": list(dimensions) or None,
                        "include_reports": True,
                        "max_items": max(4, max_items // 2),
                    },
                    "Balanced retrieval needs both positive and negative evidence by company.",
                )

    web_required = (
        plan.web_check.required
        or is_product
        or is_security
        or is_weakness
        or is_gap
        or any(term in lower for term in ("latest", "current", "recent", "today"))
    )
    web_query = plan.web_check.query or _web_query_for(question, companies, dimensions)
    web_depth = select_tavily_depth(
        web_query,
        required=web_required,
        current_depth=plan.web_check.depth,
        evidence_count=0 if is_gap else 1,
    )
    needs_sonnet = (
        False
        if is_direct_jfrog
        else plan.needs_sonnet or (is_comparison and len(dimensions) >= 3) or is_weakness
    )
    return plan.model_copy(
        update={
            "companies": companies,
            "dimensions": dimensions,
            "tool_calls": tuple(tool_calls),
            "web_check": ChatWebCheck(
                required=web_required,
                query=web_query if web_required else None,
                depth=web_depth,
                reason="automatic freshness, gap, or competitive validation"
                if web_required
                else None,
                retry_with_fast_if_weak=True,
            ),
            "answer_style": style,
            "needs_sonnet": needs_sonnet,
        }
    )


class AnthropicPlanner:
    def run(self, prompt: str) -> str:
        from anthropic import Anthropic  # noqa: PLC0415

        client = Anthropic(api_key=get_secret("anthropic-key"), max_retries=0)
        response = client.messages.create(
            model=str(config_get("models.chat_planner.name", "claude-haiku-4-5")),
            max_tokens=int(config_get("models.chat_planner.max_tokens", 1000)),
            temperature=float(config_get("models.chat_planner.temperature", 0.0)),
            messages=[{"role": "user", "content": prompt}],
            output_config=_planner_output_config(),
            timeout=float(config_get("models.chat_planner.timeout_s", 8)),
        )
        return _response_text(response)


def _companies_from_request(request: ChatRequest) -> tuple[str, ...]:
    companies = ["JFrog"]
    if request.competitor:
        companies.append(request.competitor)
    question_lower = request.question.lower()
    for company in tracked_companies():
        if company.lower() in question_lower and company not in companies:
            companies.append(company)
    return tuple(dict.fromkeys(company for company in companies if company))


def _dimensions_from_question(question: str) -> tuple[str, ...]:
    lower = question.lower()
    dimensions: list[str] = []
    for dimension, hints in _DIMENSION_HINTS.items():
        if any(hint in lower for hint in hints):
            dimensions.append(dimension)
    return tuple(dict.fromkeys(dimensions))


_SECURITY_DIMENSIONS = (
    "software_composition_analysis",
    "cve_contextual_analysis",
    "reachability_analysis",
    "malicious_package_detection",
    "package_firewall",
    "sbom_generation",
    "policy_governance",
)

_PRODUCT_DIMENSIONS = (
    "software_composition_analysis",
    "artifact_management",
    "sbom_generation",
    "package_firewall",
    "malicious_package_detection",
    "policy_governance",
)


def _answer_style(question: str) -> str:
    lower = question.lower()
    if any(term in lower for term in ("pdf", "report", "validation", "blocked", "generated")):
        return "report_status"
    if any(term in lower for term in ("compare", "versus", " vs ", "difference", "better")):
        return "comparison"
    if any(term in lower for term in ("architecture", "technical", "api", "integration")):
        return "technical_explanation"
    if any(term in lower for term in ("sales", "field", "buyer", "objection", "position")):
        return "field_guidance"
    return "short_fact"


def _requires_web(question: str, dimensions: Sequence[str]) -> bool:
    lower = question.lower()
    if any(term in lower for term in ("latest", "current", "recent", "today", "still true", "news")):
        return True
    if dimensions and any(
        dimension
        in {
            "sbom_generation",
            "reachability_analysis",
            "cve_contextual_analysis",
            "malicious_package_detection",
            "package_firewall",
        }
        for dimension in dimensions
    ):
        return True
    return False


def _is_comparison_question(lower: str) -> bool:
    return any(term in lower for term in ("compare", "compared", "versus", " vs ", "against", "better"))


def _is_direct_jfrog_question(lower: str, request: ChatRequest) -> bool:
    if "jfrog" not in lower:
        return False
    if _is_comparison_question(lower) or _is_weakness_question(lower):
        return False
    competitor = (request.competitor or "").strip().lower()
    return not competitor or competitor not in lower


def _use_deterministic_planner(request: ChatRequest) -> bool:
    lower = request.question.lower()
    if _answer_style(lower) == "report_status":
        return True
    if _is_direct_jfrog_question(lower, request):
        return True
    return False


def _is_weakness_question(lower: str) -> bool:
    return any(term in lower for term in ("weakness", "weaknesses", "lose", "loses", "gap", "risk", "limitations", "pressure"))


def _is_product_question(lower: str) -> bool:
    return any(term in lower for term in ("product", "feature", "portfolio", "capability", "sbom", "sca", "firewall", "xray", "artifactory", "nexus"))


def _is_security_question(lower: str) -> bool:
    return any(term in lower for term in ("security", "vulnerability", "cve", "malicious", "malware", "sca", "sbom", "reachability", "license", "policy"))


def _is_gap_question(lower: str) -> bool:
    return any(term in lower for term in ("missing", "unknown", "not enough evidence", "not ready", "blocked", "validation"))


def _topic_phrase(lower: str) -> str:
    if _is_security_question(lower):
        return "security supply chain SCA malware SBOM CVE reachability"
    if _is_product_question(lower):
        return "product portfolio capabilities features"
    if "market" in lower:
        return "market positioning competitive"
    return "competitive intelligence"


def _web_query_for(
    question: str,
    companies: Sequence[str],
    dimensions: Sequence[str],
) -> str:
    company_text = " ".join(companies)
    dimension_text = " ".join(dimension.replace("_", " ") for dimension in dimensions[:4])
    return " ".join(part for part in (company_text, dimension_text, question) if part)


def _valid_planned_call(call: PlannedToolCall) -> bool:
    if call.tool in {"search_answer_context", "search_report_sections", "search"}:
        return bool(str(call.arguments.get("query") or "").strip())
    if call.tool == "compare_dimension":
        return bool(call.arguments.get("names")) and bool(call.arguments.get("dimension"))
    if call.tool == "get_source_detail":
        return bool(call.arguments.get("source_ids"))
    return call.tool in {"get_report_registry", "coverage_matrix", "source_inventory"}


def _merge(
    first: Sequence[str],
    second: Sequence[str],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in (*first, *second) if str(item).strip()))


def _planner_output_config() -> dict[str, Any]:
    tool_call_schema = {
        "type": "object",
        "properties": {
            "tool": {"type": "string"},
            "arguments": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "reason": {"type": "string"},
        },
        "required": ["tool", "arguments", "reason"],
        "additionalProperties": False,
    }
    web_check_schema = {
        "type": "object",
        "properties": {
            "required": {"type": "boolean"},
            "query": {"type": "string"},
            "depth": {"enum": ["ultra-fast", "fast"]},
            "reason": {"type": "string"},
            "retry_with_fast_if_weak": {"type": "boolean"},
        },
        "required": [
            "required",
            "query",
            "depth",
            "reason",
            "retry_with_fast_if_weak",
        ],
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "properties": {
            "rewritten_question": {"type": "string"},
            "companies": {"type": "array", "items": {"type": "string"}},
            "dimensions": {"type": "array", "items": {"type": "string"}},
            "report_sections": {"type": "array", "items": {"type": "string"}},
            "tool_calls": {"type": "array", "items": tool_call_schema},
            "web_check": web_check_schema,
            "answer_style": {
                "enum": [
                    "short_fact",
                    "comparison",
                    "technical_explanation",
                    "field_guidance",
                    "report_status",
                    "not_enough_evidence",
                ]
            },
            "needs_sonnet": {"type": "boolean"},
            "confidence": {"enum": ["high", "medium", "low", "unknown"]},
            "missing_evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "rewritten_question",
            "companies",
            "dimensions",
            "report_sections",
            "tool_calls",
            "web_check",
            "answer_style",
            "needs_sonnet",
            "confidence",
            "missing_evidence",
        ],
        "additionalProperties": False,
    }
    return {
        "format": {
            "type": "json_schema",
            "schema": schema,
        }
    }


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()
