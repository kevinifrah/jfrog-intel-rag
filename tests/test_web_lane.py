from datetime import date
from pathlib import Path
from types import SimpleNamespace

from ci_engine.acquire import web_lane
from ci_engine.acquire.web_lane import extract_html
from ci_engine.skills import load_skill


def test_extract_html_from_local_fixture():
    fixture = Path(__file__).parent / "fixtures" / "web_lane_sample.html"

    result = extract_html(fixture.read_text(encoding="utf-8"), url="https://example.com/release")

    assert result["title"] == "Sample Product Release Notes"
    assert result["published"] == date(2026, 5, 4)
    assert "CycloneDX SBOM files" in result["text"]
    assert "Policy enforcement can block builds" in result["text"]
    assert "Navigation should not dominate" not in result["text"]


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.stream_responses = []
        self.calls = []
        self.stream_calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return FakeStream(self.stream_responses.pop(0))


class FakeStream:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, exc_tb):
        return None

    def get_final_text(self):
        return self.text


def test_render_prompt_uses_exact_deep_company_research_skill():
    prompt = web_lane.render_prompt("JFrog")

    assert prompt == load_skill("deep-company-research").replace("[COMPANY NAME]", "JFrog")
    assert "# DEEP COMPANY RESEARCH — JFrog" in prompt
    assert "Rely ONLY on live web search" in prompt
    assert "official company URLs only" in prompt


def test_generate_report_loads_skill_and_snapshots(monkeypatch, tmp_path):
    calls = []
    fake_messages = FakeMessages([])
    fake_messages.stream_responses.append("# DEEP COMPANY RESEARCH — Snyk\n\nOfficial report.")

    def fake_write_snapshot(**kwargs):
        calls.append(kwargs)
        return tmp_path / "report.md"

    monkeypatch.setattr(
        web_lane,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=fake_messages),
    )
    monkeypatch.setattr(web_lane, "write_snapshot", fake_write_snapshot)

    report = web_lane.generate_report("Snyk")

    assert report["report_markdown"] == "# DEEP COMPANY RESEARCH — Snyk\n\nOfficial report."
    assert report["raw_path"] == str(tmp_path / "report.md")
    assert "# DEEP COMPANY RESEARCH — Snyk" in fake_messages.stream_calls[0]["messages"][0]["content"]
    assert fake_messages.stream_calls[0]["tools"][0]["type"] == "web_search_20250305"
    assert calls[0]["content_type"] == "text/markdown"


def test_split_report_returns_report_slice_candidates(monkeypatch):
    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="text",
                text=(
                    "{"
                    '"slices":['
                    "{"
                    '"axis":"technical",'
                    '"dimension":"sbom_generation",'
                    '"doc_type":"docs",'
                    '"title":"Snyk official research: sbom_generation",'
                    '"summary":"Snyk documents SBOM support.",'
                    '"text":"FACT: Snyk documents SBOM support [https://docs.snyk.io, 2026-01-01].",'
                    '"citations":[{"url":"https://docs.snyk.io","label":"Snyk docs","date_text":"2026-01-01"}],'
                    '"confidence":0.9'
                    "},"
                    "{"
                    '"axis":"business",'
                    '"dimension":"not_in_ontology",'
                    '"doc_type":"company_fact",'
                    '"title":"Invalid",'
                    '"summary":"Invalid",'
                    '"text":"Invalid",'
                    '"confidence":0.1'
                    "}"
                    "]}"
                ),
            )
        ]
    )
    fake_messages = FakeMessages([response])
    monkeypatch.setattr(
        web_lane,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=fake_messages),
    )

    candidates = web_lane.split_report(
        "Official report markdown",
        "Snyk",
        "raw_snapshots/snyk/report.md",
        published=date(2026, 1, 2),
    )

    assert candidates == [
        {
            "title": "Snyk official research: sbom_generation",
            "url": candidates[0]["url"],
            "snippet": "Snyk documents SBOM support.",
            "text": "FACT: Snyk documents SBOM support [https://docs.snyk.io, 2026-01-01].",
            "competitor": "Snyk",
            "published": date(2026, 1, 2),
            "axis": "technical",
            "dimension": "sbom_generation",
            "doc_type": "docs",
            "source_kind": "official_llm_research_report",
            "source_reason": "official-source deep company research report slice",
            "raw_path": "raw_snapshots/snyk/report.md",
            "citations": [
                {
                    "url": "https://docs.snyk.io",
                    "label": "Snyk docs",
                    "date_text": "2026-01-01",
                }
            ],
        }
    ]
    assert candidates[0]["url"].startswith("ci-report://official-deep-research/snyk/")
    assert candidates[0]["url"].endswith("#sbom-generation")
    assert fake_messages.calls[0]["system"] == load_skill("deep-report-splitter")


def test_split_report_extracts_citation_fallback_from_slice_text(monkeypatch):
    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="text",
                text=(
                    "{"
                    '"slices":['
                    "{"
                    '"axis":"business",'
                    '"dimension":"company_profile",'
                    '"doc_type":"company_fact",'
                    '"title":"Snyk official research: company_profile",'
                    '"summary":"Snyk describes its platform.",'
                    '"text":"FACT: Snyk describes its platform [https://snyk.io/about, accessed 2026-01-02].",'
                    '"confidence":0.8'
                    "}"
                    "]}"
                ),
            )
        ]
    )
    fake_messages = FakeMessages([response])
    monkeypatch.setattr(
        web_lane,
        "_anthropic_client",
        lambda: SimpleNamespace(messages=fake_messages),
    )

    candidates = web_lane.split_report(
        "Official report markdown",
        "Snyk",
        "raw_snapshots/snyk/report.md",
        published=date(2026, 1, 2),
    )

    assert candidates[0]["citations"] == [
        {
            "url": "https://snyk.io/about",
            "label": None,
            "date_text": "accessed 2026-01-02",
        }
    ]


def test_fallback_report_slices_split_level_two_numbered_sections():
    slices = web_lane._fallback_report_slices(
        """
# Report

## 1. SNAPSHOT

Snyk overview [https://snyk.io/about, 2026-01-02].

## 2. BUSINESS OVERVIEW

Snyk product overview [https://snyk.io/product, 2026-01-02].
"""
    )

    assert [item["dimension"] for item in slices] == ["company_profile"]
    assert "SNAPSHOT" in slices[0]["title"]
    assert "Snyk overview" in slices[0]["text"]
    assert "Snyk product overview" in slices[0]["text"]


def test_fallback_report_slices_include_numbered_subsections_for_product_portfolio():
    slices = web_lane._fallback_report_slices(
        """
# Report

## 2. BUSINESS OVERVIEW

Company overview.

### 2.3 Core Products & Services

Artifactory, Xray, Curation, and Distribution.

## 7. PRODUCT & TRACTION

JFrog ML and AppTrust launches.
"""
    )

    product_slice = next(item for item in slices if item["dimension"] == "product_portfolio")
    assert "Core Products & Services" in product_slice["text"]
    assert "Artifactory, Xray, Curation, and Distribution" in product_slice["text"]
    assert "PRODUCT & TRACTION" in product_slice["text"]
    assert "JFrog ML and AppTrust launches" in product_slice["text"]


def test_extract_citations_reads_markdown_link_labels_without_overwide_dates():
    citations = web_lane.extract_citations(
        "| Field | Source |\n"
        "| Revenue | [investors.jfrog.com, Feb 12, 2026]"
        "(https://investors.jfrog.com/news/default.aspx) |\n"
        "| Founded | [jfrog.com/about](https://jfrog.com/about/) |\n"
    )

    assert citations == [
        {
            "url": "https://investors.jfrog.com/news/default.aspx",
            "label": "investors.jfrog.com, Feb 12, 2026",
            "date_text": "Feb 12, 2026",
        },
        {
            "url": "https://jfrog.com/about/",
            "label": "jfrog.com/about",
            "date_text": None,
        },
    ]


def test_collect_preserves_source_kind(monkeypatch):
    monkeypatch.setattr(
        web_lane,
        "fetch",
        lambda url, competitor=None: {
            "title": "Docs",
            "text": "Official docs text.",
            "published": None,
        },
    )

    candidates = web_lane.collect(
        "Snyk",
        [
            {
                "kind": "docs",
                "url": "https://docs.snyk.io/",
                "reason": "official docs root",
            }
        ],
    )

    assert candidates == [
        {
            "title": "Docs",
            "url": "https://docs.snyk.io/",
            "snippet": "Official docs text.",
            "text": "Official docs text.",
            "competitor": "Snyk",
            "published": None,
            "source_kind": "docs",
            "source_reason": "official docs root",
        }
    ]
