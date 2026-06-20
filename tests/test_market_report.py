from __future__ import annotations

import json
from datetime import date, datetime, timezone

from ci_engine.crews.report.market_report import (
    MarketReportError,
    build_market_overview_prompt,
    collect_market_overview_evidence,
    generate_market_report,
    market_overview_to_draft,
    parse_market_overview_analysis,
    run_market_overview_analysis,
    validate_market_report,
)
from ci_engine.crews.report.renderer import render_html


class FakeMarketMcpClient:
    """Returns one stored chunk per (company, query), labelled to the queried company."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._source_id = 200

    def search(self, query, axis=None, competitors=None, dimensions=None):
        self.calls.append(
            {"query": query, "axis": axis, "competitors": competitors, "dimensions": dimensions}
        )
        company = (competitors or ["JFrog"])[0]
        self._source_id += 1
        dimension = dimensions[0] if dimensions else "market_positioning"
        slug = company.lower().replace(" ", "-")
        return {
            "chunks": [
                {
                    "chunk_id": self._source_id + 1000,
                    "source_id": self._source_id,
                    "chunk_text": (
                        f"{company} market evidence for {dimension} with specific sourced detail."
                    ),
                    "url": f"https://example.com/{slug}/{dimension}-{self._source_id}",
                    "title": f"{company} {dimension}",
                    "publish_date": date(2026, 1, 2),
                    "fetched_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
                    "axis": axis,
                    "dimension": dimension,
                    "doc_type": "analyst",
                    "competitor": company,
                    "source_kind": "analyst",
                    "raw_path": f"raw/{company}/{dimension}.md",
                    "similarity": 0.8,
                    "citations": [],
                }
            ],
            "missing": [],
        }


def fake_market_overview_runner(prompt: str):
    payload = json.loads(prompt.split("PAYLOAD_JSON:\n", 1)[1])
    ids = payload["allowed_evidence_ids"]
    a = ids[0]
    b = ids[1] if len(ids) > 1 else a
    c = ids[2] if len(ids) > 2 else a
    return {
        "market_thesis": {
            "text": (
                "The software supply-chain security market is consolidating around vendors that "
                "turn dependency risk into developer-workflow control."
            ),
            "evidence_ids": [a, b],
            "confidence": "high",
        },
        "market_dynamics": [
            {
                "text": "Regulatory pressure and SBOM mandates are pulling governance depth into buying criteria.",
                "evidence_ids": [a],
                "confidence": "high",
            },
            {
                "text": "Platform consolidation pressure rewards breadth across the software delivery lifecycle.",
                "evidence_ids": [b],
                "confidence": "medium",
            },
        ],
        "market_risks": [
            {
                "text": "Commoditisation of basic software composition analysis compresses differentiation.",
                "evidence_ids": [c],
                "confidence": "medium",
            }
        ],
        "pestel": [
            {
                "axis": "legal",
                "factor": "Supply-chain regulation tightens disclosure obligations",
                "implication": "Rewards SBOM and governance depth",
                "material": True,
                "evidence_ids": [a],
            }
        ],
        "five_forces": [
            {
                "force": "competitive_rivalry",
                "intensity": "high",
                "rationale": "Multiple credible vendors chase the same supply-chain security budget.",
                "evidence_ids": [a],
            }
        ],
        "positioning_map": {
            "x_axis_label": "Supply-chain coverage breadth",
            "x_low_label": "Single ecosystem / one workflow",
            "x_high_label": "Universal repository + full SDLC",
            "y_axis_label": "Security specialization depth",
            "y_low_label": "Platform with security add-ons",
            "y_high_label": "Purpose-built security toolchain",
            "narrative": "Axes are an analytical judgment, not measured data.",
            "players": [
                {"name": "JFrog", "x": 85.0, "y": 55.0, "group": "platform", "is_focus": True, "evidence_ids": [a]},
                {"name": "Sonatype", "x": 55.0, "y": 75.0, "group": "security", "is_focus": False, "evidence_ids": [b]},
            ],
        },
        "confidence_notes": [
            "Confidence is strongest where multiple tracked companies share the same market signal."
        ],
        "metadata": {"test": True},
    }


def test_collect_market_overview_evidence_spans_tracked_companies():
    client = FakeMarketMcpClient()
    pack = collect_market_overview_evidence(
        companies=["JFrog", "Snyk", "Sonatype"],
        mcp_client=client,
        max_companies=3,
    )

    assert pack.metadata["report_kind"] == "market"
    assert pack.items
    companies = {item.company for item in pack.items}
    assert {"JFrog", "Snyk", "Sonatype"} <= companies
    # Every item is tagged to the standalone market section.
    assert all(item.report_section == "market_overview" for item in pack.items)


def test_parse_market_overview_rejects_uncited_evidence():
    output = fake_market_overview_runner(
        "PAYLOAD_JSON:\n" + json.dumps({"allowed_evidence_ids": ["ev1", "ev2", "ev3"]})
    )
    output["market_thesis"]["evidence_ids"] = ["missing-id"]
    try:
        parse_market_overview_analysis(output, allowed_evidence_ids={"ev1", "ev2", "ev3"})
    except MarketReportError as exc:
        assert "outside the EvidencePack" in str(exc)
    else:  # pragma: no cover - failure path
        raise AssertionError("uncited market overview should fail")


def test_generate_market_report_builds_standalone_market_draft(tmp_path):
    result = generate_market_report(
        companies=["JFrog", "Snyk", "Sonatype"],
        out_dir=tmp_path,
        formats=("json", "html"),
        mcp_client=FakeMarketMcpClient(),
        runner=fake_market_overview_runner,
    )

    draft = result.draft
    assert result.validation.passed
    assert draft.metadata["report_kind"] == "market"
    assert draft.metadata["draft_mode"] == "market_overview"
    assert draft.competitor == "Market & Strategic Context"
    section = draft.sections[0]
    assert section.id == "market_overview"
    assert any(claim.id == "market-thesis" for claim in section.claims)
    assert "pestel" in section.metadata
    assert "five_forces" in section.metadata
    assert "positioning_map" in section.metadata
    # all tracked players plotted, not a single competitor focus
    names = {p["name"] for p in section.metadata["positioning_map"]["players"]}
    assert {"JFrog", "Sonatype"} <= names


def test_market_report_html_renders_frameworks_and_market_header():
    pack = collect_market_overview_evidence(
        companies=["JFrog", "Snyk", "Sonatype"],
        mcp_client=FakeMarketMcpClient(),
        max_companies=3,
    )
    analysis = run_market_overview_analysis(
        pack,
        companies=["JFrog", "Snyk", "Sonatype"],
        runner=fake_market_overview_runner,
    )
    draft = market_overview_to_draft(pack, analysis, companies=["JFrog", "Snyk", "Sonatype"])
    validation = validate_market_report(pack, draft)

    html = render_html(pack, draft, validation)

    # Market-wide header, not "JFrog vs X", and no executive summary.
    assert "Market &amp; Strategic Context" in html
    assert "JFrog vs" not in html
    assert "Executive Summary" not in html
    # The market-level frameworks all render (forced on regardless of customer toggles).
    assert "PESTEL — macro forces" in html
    assert "Porter's Five Forces" in html
    assert 'class="posmap"' in html


def test_market_report_prompt_composes_grounding_and_frameworks():
    pack = collect_market_overview_evidence(
        companies=["JFrog", "Sonatype"],
        mcp_client=FakeMarketMcpClient(),
        max_companies=2,
    )
    prompt = build_market_overview_prompt(pack, companies=["JFrog", "Sonatype"])

    # Grounding contract is composed in front of the market-overview skill.
    assert "GROUNDING" in prompt or "grounded" in prompt.lower()
    assert "Supply-chain coverage breadth" in prompt  # canonical positioning axes
    assert "PAYLOAD_JSON:" in prompt
