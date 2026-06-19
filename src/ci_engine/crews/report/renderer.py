from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ci_engine.crews.report.schemas import (
    EvidenceItem,
    EvidencePack,
    RenderResult,
    ReportDraft,
    ReportSection,
    ScoreItem,
    ValidationReport,
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Canonical reading order of the dossier (the inverted pyramid).
_SECTION_ORDER = (
    "executive_summary",
    "company_snapshot",
    "market_context",
    "product_feature_analysis",
    "technical_teardown",
    "supply_chain_security",
    "buyer_fit",
    "field_battlecard",
)

# Mono eyebrow shown above each section title (analyst-report styling, not agent names).
_SECTION_EYEBROW = {
    "executive_summary": "Executive summary",
    "company_snapshot": "Company snapshot",
    "market_context": "Part 1 · Market & strategic context",
    "product_feature_analysis": "Part 2 · Product & feature analysis",
    "technical_teardown": "Part 2 · Technical teardown",
    "supply_chain_security": "Part 2 · Supply-chain security",
    "buyer_fit": "Part 3 · Buyer-fit matrix",
    "field_battlecard": "Part 4 · Field battlecard",
}

_FIVE_FORCE_LABELS = {
    "competitive_rivalry": "Competitive rivalry",
    "threat_of_new_entrants": "Threat of new entrants",
    "threat_of_substitutes": "Threat of substitutes",
    "buyer_power": "Buyer power",
    "supplier_power": "Supplier power",
}

_PESTEL_LABELS = {
    "political": "Political",
    "economic": "Economic",
    "social": "Social",
    "technological": "Technological",
    "environmental": "Environmental",
    "legal": "Legal",
}

_CONFIDENCE_TIER_LABELS = {
    "high": "High confidence — primary sources",
    "medium": "Medium confidence — third-party / point-in-time",
    "vendor_claim": "Vendor claims — attributed, not verified",
    "author_judgment": "Author's judgment — not data",
}


class _CiteRegistry:
    """Assigns a stable reference number to each cited source, de-duplicated by URL.

    Numbers are allocated in the order `cite()` is called, so callers build their
    presentation structures in reading order and the References list comes out
    numbered top-to-bottom like a real dossier.
    """

    def __init__(self, evidence_by_id: dict[str, EvidenceItem]) -> None:
        self._by_id = evidence_by_id
        self._key_to_num: dict[str, int] = {}
        self._num_to_item: dict[int, EvidenceItem] = {}
        self._next = 1

    def cite(self, evidence_ids: Iterable[str]) -> list[int]:
        numbers: list[int] = []
        for evidence_id in evidence_ids or ():
            item = self._by_id.get(evidence_id)
            if item is None:
                continue
            key = (item.url or "").strip().lower() or f"id:{evidence_id}"
            number = self._key_to_num.get(key)
            if number is None:
                number = self._next
                self._next += 1
                self._key_to_num[key] = number
                self._num_to_item[number] = item
            if number not in numbers:
                numbers.append(number)
        return sorted(numbers)

    def references(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for number in sorted(self._num_to_item):
            item = self._num_to_item[number]
            rows.append(
                {
                    "number": number,
                    "citation": _reader_safe_citation_text(
                        str(getattr(item, "title", None) or getattr(item, "publisher", None) or "Source")
                    ),
                    "publisher": getattr(item, "publisher", None) or getattr(item, "company", ""),
                    "url": getattr(item, "url", None) or "",
                    "date": getattr(item, "published", None)
                    or getattr(item, "retrieved_at").date(),
                }
            )
        return rows


def render_html(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
    validation: ValidationReport,
) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "xml", "j2")),
    )
    environment.filters["report_text"] = _reader_safe_body_text
    template = environment.get_template("dossier.html.j2")

    sections = _presentation_sections(draft)
    scores = _presentation_scores(draft)
    by_id = {section.id: section for section in sections}
    citer = _CiteRegistry({item.id: item for item in evidence_pack.items})

    # Build presentation structures in reading order so reference numbers flow.
    executive = _executive_brief(by_id, draft.competitor, citer)
    presentation_sections = _ordered_presentation_sections(sections, draft.competitor, citer)
    scorecards = _presentation_scorecards(scores, citer)
    confidence_tiering = _confidence_tiering(by_id)
    references = citer.references()

    return template.render(
        draft=draft,
        validation=validation,
        competitor=draft.competitor,
        jfrog=draft.jfrog,
        generated_date=draft.generated_at.date(),
        evidence_count=len(evidence_pack.items),
        executive=executive,
        sections=presentation_sections,
        scorecards=scorecards,
        confidence_tiering=confidence_tiering,
        references=references,
        toc=_table_of_contents(presentation_sections, scorecards, confidence_tiering, references),
    )


# --------------------------------------------------------------------------- #
# Executive brief (thesis + recommended actions + tradeoff matrix + SWOT)
# --------------------------------------------------------------------------- #
def _executive_brief(
    by_id: dict[str, ReportSection],
    competitor: str,
    citer: _CiteRegistry,
) -> dict[str, Any]:
    executive = by_id.get("executive_summary")
    thesis = _cited(_first_claim(executive, ("strategy-executive-thesis",)), citer) if executive else None
    actions = (
        [
            _cited(claim, citer)
            for claim in executive.claims
            if claim.claim_type != "missing"
            and claim.id.startswith("strategy-recommended-action")
        ]
        if executive
        else []
    )
    return {
        "thesis": thesis,
        "recommended_actions": actions,
        "tradeoff_rows": _comparison_rows(tuple(by_id.values()), competitor, citer),
        "swot": _swot(executive, citer) if executive else None,
    }


# --------------------------------------------------------------------------- #
# Sections (readouts + per-section framework visuals)
# --------------------------------------------------------------------------- #
def _ordered_presentation_sections(
    sections: tuple[ReportSection, ...],
    competitor: str,
    citer: _CiteRegistry,
) -> list[dict[str, Any]]:
    by_id = {section.id: section for section in sections}
    # executive_summary is rendered via the executive brief (thesis + tradeoff + SWOT),
    # so it is omitted here to avoid duplicating the same claims.
    ordered_ids = [sid for sid in _SECTION_ORDER if sid in by_id and sid != "executive_summary"]
    ordered_ids += [
        section.id
        for section in sections
        if section.id not in _SECTION_ORDER and section.id != "executive_summary"
    ]
    presentation: list[dict[str, Any]] = []
    for section_id in ordered_ids:
        section = by_id[section_id]
        presentation.append(_present_section(section, competitor, citer))
    return presentation


def _present_section(
    section: ReportSection,
    competitor: str,
    citer: _CiteRegistry,
) -> dict[str, Any]:
    readouts = _section_readouts(section, citer)
    lead = None
    body_readouts = readouts
    if readouts and readouts[0]["label"].lower().endswith("thesis"):
        lead = readouts[0]
        body_readouts = readouts[1:]
    missing = [
        _reader_safe_body_text(claim.text)
        for claim in section.claims
        if claim.claim_type == "missing"
    ]
    return {
        "id": section.id,
        "title": section.title,
        "eyebrow": _SECTION_EYEBROW.get(section.id, "Analysis"),
        "lead": lead,
        "readouts": body_readouts,
        "missing": missing,
        "pestel": _pestel(section),
        "five_forces": _five_forces(section),
        "positioning": _positioning(section),
        "capability_matrix": _capability_matrix(section),
        "product_catalog": _product_catalog_rows(section),
        "capability_gaps": _capability_gap_rows(section, competitor),
        "is_product": section.id == "product_feature_analysis",
        "is_executive": section.id == "executive_summary",
    }


def _section_readouts(section: ReportSection, citer: _CiteRegistry) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, prefixes in _READOUT_SPECS.get(section.id, ()):
        claim = _first_claim(section, prefixes)
        if claim is None:
            continue
        rows.append({"label": label, **_cited(claim, citer)})
    return rows


# --------------------------------------------------------------------------- #
# Framework extractors (read the optional metadata stashed by the analysts)
# --------------------------------------------------------------------------- #
def _pestel(section: ReportSection) -> list[dict[str, Any]]:
    factors = section.metadata.get("pestel")
    if not isinstance(factors, list):
        return []
    rows: list[dict[str, Any]] = []
    for factor in factors:
        if not isinstance(factor, dict):
            continue
        rows.append(
            {
                "axis": _PESTEL_LABELS.get(str(factor.get("axis")), str(factor.get("axis", "")).title()),
                "factor": _reader_safe_body_text(str(factor.get("factor", ""))),
                "implication": _reader_safe_body_text(str(factor.get("implication", ""))),
                "material": bool(factor.get("material", True)),
            }
        )
    return rows


def _five_forces(section: ReportSection) -> list[dict[str, Any]]:
    forces = section.metadata.get("five_forces")
    if not isinstance(forces, list):
        return []
    rows: list[dict[str, Any]] = []
    for force in forces:
        if not isinstance(force, dict):
            continue
        rows.append(
            {
                "force": _FIVE_FORCE_LABELS.get(str(force.get("force")), str(force.get("force", "")).title()),
                "intensity": str(force.get("intensity", "moderate")),
                "rationale": _reader_safe_body_text(str(force.get("rationale", ""))),
            }
        )
    return rows


def _positioning(section: ReportSection) -> dict[str, Any] | None:
    pmap = section.metadata.get("positioning_map")
    if not isinstance(pmap, dict):
        return None
    players = [
        {
            "name": str(player.get("name", "")),
            "x": _coord(player.get("x")),
            "y": _coord(player.get("y")),
            "group": player.get("group"),
            "is_focus": bool(player.get("is_focus", False)),
        }
        for player in pmap.get("players", [])
        if isinstance(player, dict) and player.get("name")
    ]
    if not players:
        return None
    return {
        "x_axis_label": _reader_safe_body_text(str(pmap.get("x_axis_label", ""))),
        "x_low_label": _reader_safe_body_text(str(pmap.get("x_low_label", ""))),
        "x_high_label": _reader_safe_body_text(str(pmap.get("x_high_label", ""))),
        "y_axis_label": _reader_safe_body_text(str(pmap.get("y_axis_label", ""))),
        "y_low_label": _reader_safe_body_text(str(pmap.get("y_low_label", ""))),
        "y_high_label": _reader_safe_body_text(str(pmap.get("y_high_label", ""))),
        "narrative": _reader_safe_body_text(str(pmap.get("narrative", ""))),
        "players": players,
    }


def _swot(section: ReportSection, citer: _CiteRegistry) -> dict[str, Any] | None:
    swot = section.metadata.get("swot")
    if not isinstance(swot, dict):
        return None

    def _quadrant(key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in swot.get(key, []):
            if not isinstance(item, dict):
                continue
            text = _reader_safe_body_text(str(item.get("text", "")))
            if not text:
                continue
            rows.append({"text": text, "cites": citer.cite(item.get("evidence_ids", ()))})
        return rows

    quadrants = {
        "strengths": _quadrant("strengths"),
        "weaknesses": _quadrant("weaknesses"),
        "opportunities": _quadrant("opportunities"),
        "threats": _quadrant("threats"),
    }
    if not any(quadrants.values()):
        return None
    return {"vantage": _reader_safe_body_text(str(swot.get("vantage", ""))), **quadrants}


def _confidence_tiering(by_id: dict[str, ReportSection]) -> dict[str, Any] | None:
    executive = by_id.get("executive_summary")
    if executive is None:
        return None
    tiering = executive.metadata.get("confidence_tiering")
    if not isinstance(tiering, dict):
        return None
    tiers = [
        {
            "label": _CONFIDENCE_TIER_LABELS.get(str(tier.get("tier")), str(tier.get("tier", "")).title()),
            "summary": _reader_safe_body_text(str(tier.get("summary", ""))),
        }
        for tier in tiering.get("tiers", [])
        if isinstance(tier, dict) and tier.get("summary")
    ]
    spot_check = [
        _reader_safe_body_text(str(note))
        for note in tiering.get("spot_check", [])
        if str(note).strip()
    ]
    if not tiers and not spot_check:
        return None
    return {"tiers": tiers, "spot_check": spot_check}


# --------------------------------------------------------------------------- #
# Product tables
# --------------------------------------------------------------------------- #
def _capability_matrix(section: ReportSection) -> list[dict[str, Any]]:
    matrix = section.metadata.get("capability_matrix")
    if not isinstance(matrix, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in matrix:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "capability": _reader_safe_body_text(str(row.get("capability", ""))),
                "jfrog": _reader_safe_body_text(str(row.get("jfrog", ""))),
                "competitor": _reader_safe_body_text(str(row.get("competitor", ""))),
                "assessment": str(row.get("assessment", "unclear")),
            }
        )
    return rows


def _product_catalog_rows(section: ReportSection) -> list[dict[str, Any]]:
    catalog = section.metadata.get("product_catalog")
    if not isinstance(catalog, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "company": item.get("company", ""),
                "product_name": item.get("product_name", ""),
                "category": item.get("category", ""),
                "primary_role": item.get("primary_role", ""),
                "capabilities": ", ".join(
                    str(capability)
                    for capability in item.get("capabilities", [])
                    if str(capability).strip()
                ),
            }
        )
    return rows


def _capability_gap_rows(section: ReportSection, competitor: str) -> list[dict[str, Any]]:
    gaps = section.metadata.get("capability_evidence_gaps")
    if not isinstance(gaps, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in gaps:
        if not isinstance(row, dict):
            continue
        jfrog = row.get("jfrog") if isinstance(row.get("jfrog"), dict) else {}
        rival = row.get("competitor") if isinstance(row.get("competitor"), dict) else {}
        rows.append(
            {
                "capability": row.get("capability_label", ""),
                "jfrog_status": str(jfrog.get("status", "unknown")).replace("_", " "),
                "competitor_status": str(rival.get("status", "unknown")).replace("_", " "),
                "readout": str(row.get("search_status", "unknown")).replace("_", " "),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Tradeoff matrix + scorecards
# --------------------------------------------------------------------------- #
def _comparison_rows(
    sections: tuple[ReportSection, ...],
    competitor: str,
    citer: _CiteRegistry,
) -> list[dict[str, Any]]:
    by_id = {section.id: section for section in sections}
    row_specs = [
        ("Strategy", "executive_summary", ("strategy-jfrog-advantage",),
         ("strategy-competitor-strength",), ("strategy-recommended-action", "strategy-risk")),
        ("Market", "market_context", ("market-buyer-segment", "market-gtm-motion"),
         ("market-competitor-company-position", "market-risk"),
         ("market-context-thesis", "market-ecosystem-signal")),
        ("Product", "product_feature_analysis", ("product-jfrog-advantage",),
         ("product-competitor-advantage", "product-jfrog-limitation"),
         ("product-buyer-implication", "product-parity-gap")),
        ("Technical", "technical_teardown", ("technical-jfrog-capability",),
         ("technical-competitor-capability", "technical-risk"),
         ("technical-architecture-workflow", "technical-ai-artifact-governance")),
        ("Buyer fit", "buyer_fit", ("buyer-jfrog-win-condition",),
         ("buyer-competitor-win-condition", "buyer-qualify-out-signal"), ("buyer-fit-thesis",)),
        ("Field action", "field_battlecard", ("field-action",),
         ("field-objection-handling",), ("field-battlecard-thesis", "field-discovery-question")),
    ]
    rows: list[dict[str, Any]] = []
    for lens, section_id, jfrog_prefixes, competitor_prefixes, implication_prefixes in row_specs:
        section = by_id.get(section_id)
        if section is None:
            continue
        jfrog = _cited(_first_claim(section, jfrog_prefixes), citer)
        rival = _cited(_first_claim(section, competitor_prefixes), citer)
        implication = _cited(_first_claim(section, implication_prefixes), citer)
        if not (jfrog["text"] or rival["text"] or implication["text"]):
            continue
        rows.append({"lens": lens, "jfrog": jfrog, "competitor": rival, "implication": implication})
    return rows


def _presentation_scorecards(
    scores: tuple[ScoreItem, ...],
    citer: _CiteRegistry,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for score in scores:
        max_value = score.max_value or 5.0
        cards.append(
            {
                "company": score.company,
                "category": score.category,
                "buyer_archetype": _reader_safe_body_text(score.buyer_archetype or ""),
                "value": score.value,
                "max_value": max_value,
                "percent": max(0, min(100, round(score.value / max_value * 100))) if max_value else 0,
                "rationale": _reader_safe_body_text(score.rationale),
                "weight": round((score.weight or 0) * 100),
                "cites": citer.cite(score.evidence_ids),
            }
        )
    return cards


# --------------------------------------------------------------------------- #
# Table of contents
# --------------------------------------------------------------------------- #
def _table_of_contents(
    sections: list[dict[str, Any]],
    scorecards: list[dict[str, Any]],
    confidence_tiering: dict[str, Any] | None,
    references: list[dict[str, Any]],
) -> list[dict[str, str]]:
    entries = [{"anchor": "executive-summary", "label": "Executive Summary"}]
    for section in sections:
        if section["id"] == "executive_summary":
            continue
        entries.append({"anchor": section["id"].replace("_", "-"), "label": section["title"]})
    if scorecards:
        entries.append({"anchor": "scorecards", "label": "Weighted Buyer Scorecards"})
    if confidence_tiering:
        entries.append({"anchor": "methodology", "label": "Methodology & confidence"})
    if references:
        entries.append({"anchor": "references", "label": "References"})
    return entries


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _cited(claim: Any, citer: _CiteRegistry) -> dict[str, Any]:
    if claim is None:
        return {"text": "", "cites": []}
    return {
        "text": _trim_to_sentence(_reader_safe_body_text(claim.text)),
        "cites": citer.cite(getattr(claim, "evidence_ids", ()) or ()),
    }


def _first_claim(section: ReportSection | None, prefixes: tuple[str, ...]):
    if section is None:
        return None
    return next(
        (
            claim
            for claim in section.claims
            if claim.claim_type != "missing"
            and any(claim.id.startswith(prefix) for prefix in prefixes)
        ),
        None,
    )


def _coord(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 50.0


_READOUT_SPECS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "executive_summary": (
        ("Thesis", ("strategy-executive-thesis",)),
        ("JFrog edge", ("strategy-jfrog-advantage",)),
        ("Competitor edge", ("strategy-competitor-strength",)),
        ("Risk", ("strategy-risk",)),
        ("Recommended action", ("strategy-recommended-action",)),
    ),
    "company_snapshot": (
        ("Company thesis", ("market-company-snapshot-thesis",)),
        ("JFrog position", ("market-jfrog-company-position",)),
        ("Competitor position", ("market-competitor-company-position",)),
    ),
    "market_context": (
        ("Market thesis", ("market-context-thesis",)),
        ("Buyer segment", ("market-buyer-segment",)),
        ("GTM motion", ("market-gtm-motion",)),
        ("Risk", ("market-risk",)),
    ),
    "product_feature_analysis": (
        ("Product thesis", ("product-feature-thesis",)),
        ("JFrog advantage", ("product-jfrog-advantage",)),
        ("Competitor advantage", ("product-competitor-advantage",)),
        ("Where JFrog is exposed", ("product-jfrog-limitation",)),
        ("Buyer implication", ("product-buyer-implication",)),
    ),
    "technical_teardown": (
        ("Technical thesis", ("technical-teardown-thesis",)),
        ("JFrog capability", ("technical-jfrog-capability",)),
        ("Competitor capability", ("technical-competitor-capability",)),
        ("Architecture implication", ("technical-architecture-workflow",)),
        ("AI / artifact governance", ("technical-ai-artifact-governance",)),
    ),
    "supply_chain_security": (
        ("Security comparison", ("technical-security-comparison",)),
        ("Technical risk", ("technical-risk",)),
    ),
    "buyer_fit": (
        ("Buyer-fit thesis", ("buyer-fit-thesis",)),
        ("Where JFrog wins", ("buyer-jfrog-win-condition",)),
        ("Where competitor wins", ("buyer-competitor-win-condition",)),
        ("Qualify-out signal", ("buyer-qualify-out-signal",)),
    ),
    "field_battlecard": (
        ("Battlecard thesis", ("field-battlecard-thesis",)),
        ("Objection handling", ("field-objection-handling",)),
        ("Discovery question", ("field-discovery-question",)),
        ("Field action", ("field-action",)),
    ),
}


def write_report_artifacts(
    evidence_pack: EvidencePack,
    draft: ReportDraft,
    validation: ValidationReport,
    *,
    out_dir: Path,
    formats: Iterable[str],
) -> list[RenderResult]:
    out_dir.mkdir(parents=True, exist_ok=True)
    requested = {fmt.lower().strip() for fmt in formats}
    results: list[RenderResult] = []
    html: str | None = None

    if "json" in requested:
        json_path = out_dir / "report.json"
        json_path.write_text(
            json.dumps(
                {
                    "evidence_pack": evidence_pack.model_dump(mode="json"),
                    "draft": draft.model_dump(mode="json"),
                    "validation": validation.model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        results.append(RenderResult(format="json", status="written", path=str(json_path)))

    if "html" in requested or "pdf" in requested:
        html = render_html(evidence_pack, draft, validation)

    if "html" in requested and html is not None:
        html_path = out_dir / "report.html"
        html_path.write_text(html, encoding="utf-8")
        results.append(RenderResult(format="html", status="written", path=str(html_path)))

    if "pdf" in requested:
        if not validation.passed:
            results.append(
                RenderResult(
                    format="pdf",
                    status="blocked",
                    message="PDF rendering blocked because report validation failed.",
                )
            )
        elif html is None:
            results.append(
                RenderResult(
                    format="pdf",
                    status="skipped",
                    message="HTML was not generated, so PDF rendering was skipped.",
                )
            )
        else:
            results.append(_write_pdf(html, out_dir / "report.pdf"))

    return results


def _write_pdf(html: str, path: Path) -> RenderResult:
    _ensure_pdf_cache_dir()
    try:
        from weasyprint import HTML  # noqa: PLC0415
    except ImportError:
        return RenderResult(
            format="pdf",
            status="skipped",
            message="WeasyPrint is not installed; install project PDF dependencies to enable PDF output.",
        )

    HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf(
        path,
        url_fetcher=_blocked_url_fetcher,
    )
    return RenderResult(format="pdf", status="written", path=str(path))


def _ensure_pdf_cache_dir() -> None:
    cache_dir = Path(tempfile.gettempdir()) / "ci-engine-weasyprint-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))


def _blocked_url_fetcher(url: str) -> dict[str, object]:
    # Local font/asset files (file://) bundled next to the template are allowed for
    # PDF rendering. Network resources (e.g. the web-font stylesheet that browsers load
    # for the screen view) are NOT fetched at render time — they are stubbed empty so the
    # PDF falls back to the system font stack without crashing or touching the web.
    if url.startswith("file:"):
        from weasyprint.urls import default_url_fetcher  # noqa: PLC0415

        return default_url_fetcher(url)
    return {"string": "", "mime_type": "text/css"}


def _presentation_sections(draft: ReportDraft) -> tuple[ReportSection, ...]:
    mode = draft.metadata.get("draft_mode")
    if mode == "crew_strategy":
        visible_ids = {"executive_summary"}
    elif mode == "crew_strategy_market":
        visible_ids = {"executive_summary", "company_snapshot", "market_context"}
    elif mode == "crew_strategy_market_technical":
        visible_ids = {
            "executive_summary",
            "company_snapshot",
            "market_context",
            "technical_teardown",
            "supply_chain_security",
        }
    elif mode == "crew_strategy_market_technical_field":
        visible_ids = {
            "executive_summary",
            "company_snapshot",
            "market_context",
            "technical_teardown",
            "supply_chain_security",
            "buyer_fit",
            "field_battlecard",
        }
    elif mode in {
        "crew_strategy_market_product_technical_field",
        "crew_strategy_market_product_technical_field_scoring",
    }:
        visible_ids = {
            "executive_summary",
            "company_snapshot",
            "market_context",
            "product_feature_analysis",
            "technical_teardown",
            "supply_chain_security",
            "buyer_fit",
            "field_battlecard",
        }
    else:
        return draft.sections
    return tuple(section for section in draft.sections if section.id in visible_ids)


def _presentation_scores(draft: ReportDraft) -> tuple[ScoreItem, ...]:
    if draft.metadata.get("draft_mode") == "crew_strategy_market_product_technical_field_scoring":
        return draft.scores
    if draft.metadata.get("draft_mode") in {
        "crew_strategy",
        "crew_strategy_market",
        "crew_strategy_market_technical",
        "crew_strategy_market_technical_field",
        "crew_strategy_market_product_technical_field",
    }:
        return ()
    return draft.scores


def _reader_safe_citation_text(text: str) -> str:
    cleaned = str(text or "").replace("_", " ")
    cleaned = cleaned.replace("official-deep-research", "internal research brief")
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\b(raw path|source path|metadata|tags?|keywords?)\b:?", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bofficial\s+llm\s+research\s+report\b", "research brief", cleaned, flags=re.I)
    return " ".join(cleaned.split()) or "Source"


def _reader_safe_body_text(text: str) -> str:
    cleaned = str(text or "")
    replacements = {
        "raw_path": "",
        "raw path": "",
        "source path": "",
        "official_llm_research_report": "research brief",
        "official deep-research": "research brief",
        "official deep research": "research brief",
        "from the frozen EvidencePack": "from available evidence",
        "from the frozen evidencepack": "from available evidence",
        "EvidencePack": "available evidence",
        "evidence readiness": "supporting evidence",
        "win_loss_signals": "win/loss signals",
        "product_portfolio": "product portfolio",
    }
    for raw, replacement in replacements.items():
        cleaned = re.sub(re.escape(raw), replacement, cleaned, flags=re.I)
    cleaned = re.sub(r"\b(tags?|keywords?)\b:?", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bmetadata\b", "package intelligence", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(Evidence|Source|Sources)\s*:\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\[[a-f0-9]{12,40}\]", "", cleaned, flags=re.I)
    return " ".join(cleaned.split())


def _trim_to_sentence(text: str) -> str:
    """Defensive guard against truncated model output: if a string ends mid-word
    (no terminal punctuation and not a short phrase), trim back to the last
    complete sentence so the reader never sees a dangling fragment."""
    compact = " ".join(str(text or "").split())
    if not compact or compact[-1] in ".!?:\")]}":
        return compact
    # Short cells/phrases are fine without terminal punctuation.
    if len(compact) <= 90:
        return compact
    match = list(re.finditer(r"[.!?](?=\s|$)", compact))
    if match:
        return compact[: match[-1].end()].rstrip()
    return compact


def _shorten_for_display(text: str, *, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0].rstrip() + "..."


__all__ = ["render_html", "write_report_artifacts"]
