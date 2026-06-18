from ci_engine.skills import compose, load_skill


def _compact(text: str) -> str:
    return " ".join(text.split())


def test_load_skill_non_empty():
    body = load_skill("grounding-contract")
    assert body, "load_skill returned empty string"


def test_load_skill_no_frontmatter():
    body = load_skill("grounding-contract")
    assert not body.startswith("---"), "frontmatter was not stripped"


def test_compose_joins_skills():
    result = compose("grounding-contract", "grounding-contract")
    assert "\n\n" in result


def test_relevance_rubric_treats_jfrog_neutrally():
    body = _compact(load_skill("relevance-rubric"))

    assert "including when the target is JFrog" in body
    assert "Do not use JFrog as a special anchor" in body
    assert "Official vendor sources are relevant" in body


def test_ingest_synthesis_labels_vendor_claims_without_rejecting_them():
    body = _compact(load_skill("ingest-synthesis"))

    assert "official vendor pages" in body
    assert "Do not reject a source merely because it is self-promotional" in body
    assert "vendor-stated" in body


def test_deep_company_research_is_official_source_only():
    body = load_skill("deep-company-research")

    assert "# DEEP COMPANY RESEARCH — [COMPANY NAME]" in body
    assert "Rely ONLY on live web search" in body
    assert "Do NOT use third-party sources" in body
    assert "official company URLs only" in body


def test_deep_report_splitter_extracts_citations():
    body = load_skill("deep-report-splitter")

    assert "citations" in body
    assert "Do not invent URLs or browse" in body
    assert "official URL exactly as present in the report" in body
