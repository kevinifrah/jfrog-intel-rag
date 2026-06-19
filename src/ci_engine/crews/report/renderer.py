from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ci_engine.crews.report.schemas import (
    EvidencePack,
    RenderResult,
    ReportDraft,
    ReportSection,
    ScoreItem,
    ValidationReport,
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


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
    cited_ids = _cited_evidence_ids(sections, scores)
    cited_evidence = [
        item
        for item in evidence_pack.items
        if item.id in cited_ids
    ]
    return template.render(
        evidence_pack=evidence_pack,
        draft=draft,
        validation=validation,
        sections=sections,
        scores=scores,
        framework_cards=_framework_cards(sections),
        comparison_rows=_comparison_rows(sections, draft.competitor),
        section_readouts=_section_readouts(sections),
        capability_summary=_capability_summary(sections, draft.competitor),
        product_catalog_rows=_product_catalog_rows(sections),
        product_advantage_rows=_product_advantage_rows(sections),
        capability_gap_rows=_capability_gap_rows(sections, draft.competitor),
        evidence_by_id={item.id: item for item in evidence_pack.items},
        cited_evidence=[_cited_source_row(item) for item in cited_evidence],
    )


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
    raise ValueError(f"External resource fetching is disabled for report PDF rendering: {url}")


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


def _cited_evidence_ids(
    sections: tuple[ReportSection, ...],
    scores: tuple[ScoreItem, ...],
) -> set[str]:
    cited: set[str] = set()
    for section in sections:
        cited.update(section.evidence_ids)
        for claim in section.claims:
            cited.update(claim.evidence_ids)
    for score in scores:
        cited.update(score.evidence_ids)
    return cited


def _framework_cards(sections: tuple[ReportSection, ...]) -> list[dict[str, object]]:
    labels = {
        "executive_summary": ("Strategic Posture", "Where the comparison is won or lost"),
        "company_snapshot": ("Company Signal", "Business context and operating posture"),
        "market_context": ("Market Frame", "Buyer demand and competitive motion"),
        "product_feature_analysis": ("Product & Features", "Capability-by-capability tradeoffs"),
        "technical_teardown": ("Architecture Lens", "Workflow, deployment, and technical control points"),
        "supply_chain_security": ("Security Coverage", "Supply-chain controls and governance depth"),
        "buyer_fit": ("Buyer Fit", "Where each vendor is most dangerous"),
        "field_battlecard": ("Field Action", "How teams should qualify and respond"),
    }
    cards: list[dict[str, object]] = []
    for section in sections:
        title, caption = labels.get(section.id, (section.title, "Evidence-backed analysis"))
        cards.append(
            {
                "title": title,
                "caption": caption,
                "claim_count": len(section.claims),
                "summary": _shorten_for_display(
                    next(
                        (
                            claim.text
                            for claim in section.claims
                            if claim.claim_type != "missing"
                        ),
                        section.narrative or "",
                    ),
                    limit=155,
                ),
            }
        )
    return cards


def _capability_summary(
    sections: tuple[ReportSection, ...],
    competitor: str,
) -> list[dict[str, object]]:
    product = next(
        (section for section in sections if section.id == "product_feature_analysis"),
        None,
    )
    if product is None:
        return []
    matrix = product.metadata.get("capability_matrix")
    if not isinstance(matrix, list):
        return []
    counts = {
        "jfrog_advantage": 0,
        "competitor_advantage": 0,
        "parity": 0,
        "unclear": 0,
    }
    for row in matrix:
        if not isinstance(row, dict):
            continue
        assessment = str(row.get("assessment", "unclear"))
        if assessment in counts:
            counts[assessment] += 1
    total = sum(counts.values()) or 1
    rows = [
        ("JFrog advantage", "jfrog", counts["jfrog_advantage"]),
        (f"{competitor} advantage", "rival", counts["competitor_advantage"]),
        ("Parity", "parity", counts["parity"]),
        ("Unclear", "unclear", counts["unclear"]),
    ]
    return [
        {
            "label": label,
            "class": css_class,
            "count": count,
            "width": max(3, round(count / total * 100)) if count else 0,
        }
        for label, css_class, count in rows
    ]


def _product_catalog_rows(
    sections: tuple[ReportSection, ...],
) -> list[dict[str, object]]:
    product = next(
        (section for section in sections if section.id == "product_feature_analysis"),
        None,
    )
    if product is None:
        return []
    catalog = product.metadata.get("product_catalog")
    if not isinstance(catalog, list):
        return []
    rows: list[dict[str, object]] = []
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
                "confidence": item.get("confidence", "unknown"),
            }
        )
    return rows


def _product_advantage_rows(
    sections: tuple[ReportSection, ...],
) -> dict[str, list[dict[str, object]]]:
    product = next(
        (section for section in sections if section.id == "product_feature_analysis"),
        None,
    )
    if product is None:
        return {"jfrog": [], "competitor": []}
    matrix = product.metadata.get("capability_matrix")
    if not isinstance(matrix, list):
        return {"jfrog": [], "competitor": []}
    rows = {"jfrog": [], "competitor": []}
    for item in matrix:
        if not isinstance(item, dict):
            continue
        assessment = str(item.get("assessment") or "")
        row = {
            "capability": item.get("capability", ""),
            "jfrog": item.get("jfrog", ""),
            "competitor": item.get("competitor", ""),
            "confidence": item.get("confidence", "unknown"),
        }
        if assessment == "jfrog_advantage":
            rows["jfrog"].append(row)
        elif assessment == "competitor_advantage":
            rows["competitor"].append(row)
    return rows


def _capability_gap_rows(
    sections: tuple[ReportSection, ...],
    competitor: str,
) -> list[dict[str, object]]:
    product = next(
        (section for section in sections if section.id == "product_feature_analysis"),
        None,
    )
    if product is None:
        return []
    gaps = product.metadata.get("capability_evidence_gaps")
    if not isinstance(gaps, list):
        return []
    rows: list[dict[str, object]] = []
    for row in gaps:
        if not isinstance(row, dict):
            continue
        jfrog = row.get("jfrog") if isinstance(row.get("jfrog"), dict) else {}
        rival = row.get("competitor") if isinstance(row.get("competitor"), dict) else {}
        rows.append(
            {
                "capability": row.get("capability_label", ""),
                "jfrog_status": jfrog.get("status", "unknown"),
                "competitor_status": rival.get("status", "unknown"),
                "competitor": competitor,
                "readout": row.get("search_status", "unknown"),
                "confidence": row.get("confidence", "unknown"),
            }
        )
    return rows


def _comparison_rows(
    sections: tuple[ReportSection, ...],
    competitor: str,
) -> list[dict[str, object]]:
    by_id = {section.id: section for section in sections}
    row_specs = [
        (
            "Strategy",
            "executive_summary",
            ("strategy-jfrog-advantage",),
            ("strategy-competitor-strength",),
            ("strategy-recommended-action", "strategy-risk"),
        ),
        (
            "Market",
            "market_context",
            ("market-buyer-segment", "market-gtm-motion"),
            ("market-competitor-company-position", "market-risk"),
            ("market-context-thesis", "market-ecosystem-signal"),
        ),
        (
            "Product & Features",
            "product_feature_analysis",
            ("product-jfrog-advantage",),
            ("product-competitor-advantage", "product-jfrog-limitation"),
            ("product-buyer-implication", "product-parity-gap"),
        ),
        (
            "Technical",
            "technical_teardown",
            ("technical-jfrog-capability",),
            ("technical-competitor-capability", "technical-risk"),
            ("technical-architecture-workflow", "technical-ai-artifact-governance"),
        ),
        (
            "Buyer Fit",
            "buyer_fit",
            ("buyer-jfrog-win-condition",),
            ("buyer-competitor-win-condition", "buyer-qualify-out-signal"),
            ("buyer-fit-thesis",),
        ),
        (
            "Field Action",
            "field_battlecard",
            ("field-action",),
            ("field-objection-handling",),
            ("field-battlecard-thesis", "field-discovery-question"),
        ),
    ]
    rows: list[dict[str, object]] = []
    for lens, section_id, jfrog_prefixes, competitor_prefixes, implication_prefixes in row_specs:
        section = by_id.get(section_id)
        if section is None:
            continue
        rows.append(
            {
                "lens": lens,
                "jfrog": _first_claim_text(section, jfrog_prefixes),
                "competitor": _first_claim_text(section, competitor_prefixes),
                "implication": _first_claim_text(section, implication_prefixes),
                "competitor_label": competitor,
            }
        )
    return [
        row
        for row in rows
        if row["jfrog"] or row["competitor"] or row["implication"]
    ]


def _section_readouts(
    sections: tuple[ReportSection, ...],
) -> dict[str, list[dict[str, object]]]:
    readout_specs: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
        "executive_summary": (
            ("Thesis", ("strategy-executive-thesis",)),
            ("JFrog Edge", ("strategy-jfrog-advantage",)),
            ("Competitor Edge", ("strategy-competitor-strength",)),
            ("Risk", ("strategy-risk",)),
            ("Recommended Action", ("strategy-recommended-action",)),
        ),
        "company_snapshot": (
            ("Company Thesis", ("market-company-snapshot-thesis",)),
            ("JFrog Position", ("market-jfrog-company-position",)),
            ("Competitor Position", ("market-competitor-company-position",)),
        ),
        "market_context": (
            ("Market Thesis", ("market-context-thesis",)),
            ("Buyer Segment", ("market-buyer-segment",)),
            ("GTM Motion", ("market-gtm-motion",)),
            ("Risk", ("market-risk",)),
        ),
        "product_feature_analysis": (
            ("Product Thesis", ("product-feature-thesis",)),
            ("JFrog Advantage", ("product-jfrog-advantage",)),
            ("Competitor Advantage", ("product-competitor-advantage",)),
            ("Where JFrog Is Exposed", ("product-jfrog-limitation",)),
            ("Buyer Implication", ("product-buyer-implication",)),
        ),
        "technical_teardown": (
            ("Technical Thesis", ("technical-teardown-thesis",)),
            ("JFrog Capability", ("technical-jfrog-capability",)),
            ("Competitor Capability", ("technical-competitor-capability",)),
            ("Architecture Implication", ("technical-architecture-workflow",)),
            ("AI / Artifact Governance", ("technical-ai-artifact-governance",)),
        ),
        "supply_chain_security": (
            ("Security Comparison", ("technical-security-comparison",)),
            ("Technical Risk", ("technical-risk",)),
        ),
        "buyer_fit": (
            ("Buyer-Fit Thesis", ("buyer-fit-thesis",)),
            ("Where JFrog Wins", ("buyer-jfrog-win-condition",)),
            ("Where Competitor Wins", ("buyer-competitor-win-condition",)),
            ("Qualify-Out Signal", ("buyer-qualify-out-signal",)),
        ),
        "field_battlecard": (
            ("Battlecard Thesis", ("field-battlecard-thesis",)),
            ("Objection Handling", ("field-objection-handling",)),
            ("Discovery Question", ("field-discovery-question",)),
            ("Field Action", ("field-action",)),
        ),
    }
    section_rows: dict[str, list[dict[str, object]]] = {}
    for section in sections:
        rows: list[dict[str, object]] = []
        for label, prefixes in readout_specs.get(section.id, ()):
            claim = _first_claim(section, prefixes)
            if claim is None:
                continue
            rows.append(
                {
                    "label": label,
                    "text": claim.text,
                    "confidence": claim.confidence,
                }
            )
        if rows:
            section_rows[section.id] = rows
    return section_rows


def _first_claim_text(section: ReportSection, prefixes: tuple[str, ...]) -> str:
    claim = _first_claim(section, prefixes)
    return claim.text if claim is not None else ""


def _first_claim(section: ReportSection, prefixes: tuple[str, ...]):
    return next(
        (
            claim
            for claim in section.claims
            if claim.claim_type != "missing"
            and any(claim.id.startswith(prefix) for prefix in prefixes)
        ),
        None,
    )


def _cited_source_row(item: object) -> dict[str, object]:
    title = getattr(item, "title", None) or getattr(item, "publisher", None) or "Source"
    return {
        "id": getattr(item, "id"),
        "company": getattr(item, "company"),
        "report_section": str(getattr(item, "report_section")).replace("_", " ").title(),
        "citation": _reader_safe_citation_text(str(title)),
        "date": getattr(item, "published", None) or getattr(item, "retrieved_at").date(),
    }


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


def _shorten_for_display(text: str, *, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0].rstrip() + "..."


__all__ = ["render_html", "write_report_artifacts"]
