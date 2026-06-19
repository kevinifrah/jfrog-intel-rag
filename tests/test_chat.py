from __future__ import annotations

import json

from ci_engine.chat.answer import validate_grounding
from ci_engine.chat.planner import build_fallback_plan, plan_chat
from ci_engine.chat.report_store import ReportArtifactStore
from ci_engine.chat.schemas import ChatAnswer, ChatEvidenceItem, ChatRequest, ChatSource
from ci_engine.chat.web import select_tavily_depth, web_check_with_retry
from ci_engine.skills import load_skill


def test_chat_skills_load():
    for skill in (
        "chat-query-planner",
        "chat-mcp-tool-use",
        "chat-answer-writer",
        "chat-web-depth-selector",
        "chat-grounding-checker",
    ):
        assert load_skill(skill)


def test_report_artifact_store_lists_and_searches_reports(tmp_path):
    _write_report(
        tmp_path,
        "sonatype",
        competitor="Sonatype",
        validation_passed=False,
        claim_text="Sonatype Repository Firewall blocks risky open-source packages before ingestion.",
    )

    store = ReportArtifactStore(tmp_path)

    summary = store.get_report("sonatype")
    assert summary is not None
    assert summary.pdf_status == "blocked"
    assert summary.blocker_codes == ("evidence_gap",)
    assert store.list_reports()[0].slug == "sonatype"

    results = store.search_report_sections("repository firewall", competitors=["Sonatype"])
    assert len(results) == 1
    assert results[0].source == "report"
    assert "Firewall" in results[0].text


def test_fallback_planner_uses_capability_web_check():
    plan = build_fallback_plan(
        ChatRequest(
            question="Does Sonatype support SBOM generation compared with JFrog?",
            competitor="Sonatype",
        )
    )

    assert plan.web_check.required is True
    assert plan.web_check.depth == "fast"
    assert plan.tool_calls[0].tool == "search_answer_context"
    assert "sbom_generation" in plan.dimensions


def test_plan_strengthening_balances_weakness_questions():
    plan = build_fallback_plan(
        ChatRequest(
            question="What are the weaknesses of JFrog in security compared to Sonatype?",
            competitor="Sonatype",
        )
    )
    from ci_engine.chat.planner import strengthen_plan

    strengthened = strengthen_plan(
        ChatRequest(
            question="What are the weaknesses of JFrog in security compared to Sonatype?",
            competitor="Sonatype",
        ),
        plan,
    )

    queries = [
        call.arguments.get("query", "")
        for call in strengthened.tool_calls
        if call.tool == "search_answer_context"
    ]
    assert strengthened.web_check.required is True
    assert strengthened.needs_sonnet is True
    assert any("JFrog" in query and "weaknesses" in query for query in queries)
    assert any("Sonatype" in query and "strengths" in query for query in queries)


def test_plan_strengthening_keeps_direct_jfrog_questions_simple():
    request = ChatRequest(
        question="What are JFrog main products?",
        competitor="Sonatype",
    )
    plan = build_fallback_plan(request)
    from ci_engine.chat.planner import strengthen_plan

    strengthened = strengthen_plan(request, plan)

    assert strengthened.companies == ("JFrog",)
    assert strengthened.answer_style == "technical_explanation"
    assert strengthened.needs_sonnet is False
    assert all(call.tool != "compare_dimension" for call in strengthened.tool_calls)
    assert all(
        "Sonatype" not in json.dumps(call.arguments)
        for call in strengthened.tool_calls
    )


def test_direct_jfrog_questions_skip_llm_planner():
    def fail_runner(_prompt: str):
        raise AssertionError("planner should not be called")

    plan = plan_chat(
        ChatRequest(
            question="What are JFrog main products?",
            competitor="Sonatype",
        ),
        runner=fail_runner,
    )

    assert plan.companies == ("JFrog",)
    assert plan.needs_sonnet is False


def test_tavily_depth_and_retry():
    calls = []

    def fake_search(**kwargs):
        calls.append(kwargs["search_depth"])
        if kwargs["search_depth"] == "ultra-fast":
            return {
                "results": [
                    {
                        "title": "Thin",
                        "url": "https://example.com/thin",
                        "content": "thin result",
                    }
                ]
            }
        return {
            "results": [
                {
                    "title": "JFrog docs",
                    "url": "https://jfrog.com/help",
                    "content": "JFrog SBOM export documentation.",
                },
                {
                    "title": "Sonatype docs",
                    "url": "https://sonatype.com/docs",
                    "content": "Sonatype SBOM documentation.",
                },
            ]
        }

    assert (
        select_tavily_depth("is this still true?", required=True, evidence_count=3)
        == "ultra-fast"
    )
    items, metadata = web_check_with_retry(
        "unique retry query",
        depth="ultra-fast",
        retry_with_fast_if_weak=True,
        search_fn=fake_search,
    )

    assert calls == ["ultra-fast", "fast"]
    assert len(items) == 2
    assert metadata["retry_reason"] == "weak_ultra_fast_results"


def test_grounding_blocks_unknown_source_id():
    evidence = (
        ChatEvidenceItem(id="E1", source="db", text="Supported evidence."),
    )
    answer = ChatAnswer(
        answer="A claim.",
        confidence="medium",
        sources=(ChatSource(id="E2", source="db"),),
    )

    findings = validate_grounding(answer, evidence)

    assert findings[0].code == "unknown_source_id"


def test_report_summary_includes_human_readiness_copy(tmp_path):
    _write_report(
        tmp_path,
        "github",
        competitor="GitHub",
        validation_passed=False,
        claim_text="GitHub has supply-chain security evidence.",
    )

    summary = ReportArtifactStore(tmp_path).get_report("github")

    assert summary is not None
    assert summary.executive_status_label == "Review draft available"
    assert "PDF is not ready" in summary.readiness_summary
    assert "Strengthen the missing evidence" in summary.recommended_action


def test_report_registry_prioritizes_validated_reports(tmp_path):
    _write_report(
        tmp_path,
        "newer-draft",
        competitor="Newer Draft",
        validation_passed=False,
        claim_text="Draft evidence.",
        generated_at="2026-06-19T10:00:00Z",
    )
    _write_report(
        tmp_path,
        "validated",
        competitor="Validated",
        validation_passed=True,
        claim_text="Validated evidence.",
        generated_at="2026-06-19T08:00:00Z",
        pdf_available=True,
    )

    summaries = ReportArtifactStore(tmp_path).list_reports()

    assert [summary.slug for summary in summaries] == ["validated", "newer-draft"]
    assert summaries[0].executive_status_label == "Final report ready"


def _write_report(
    root,
    slug: str,
    *,
    competitor: str,
    validation_passed: bool,
    claim_text: str,
    generated_at: str = "2026-06-19T08:00:00Z",
    pdf_available: bool = False,
) -> None:
    report_dir = root / slug
    report_dir.mkdir()
    (report_dir / "report.html").write_text("<html><body>report</body></html>", encoding="utf-8")
    if pdf_available:
        (report_dir / "report.pdf").write_bytes(b"%PDF-1.4\n")
    (report_dir / "report.json").write_text(
        json.dumps(
            {
                "draft": {
                    "competitor": competitor,
                    "generated_at": generated_at,
                    "metadata": {"draft_mode": "test"},
                    "sections": [
                        {
                            "id": "product_feature_analysis",
                            "title": "Product and Feature Analysis",
                            "claims": [
                                {
                                    "id": "claim-1",
                                    "text": claim_text,
                                    "confidence": "medium",
                                    "evidence_ids": ["E1"],
                                }
                            ],
                        }
                    ],
                },
                "validation": {
                    "passed": validation_passed,
                    "findings": [
                        {
                            "severity": "warning",
                            "code": "evidence_gap",
                            "message": "missing source",
                            "section_id": "product_feature_analysis",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
