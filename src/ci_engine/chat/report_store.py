from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ci_engine.chat.schemas import ChatEvidenceItem


class ReportSummary(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    slug: str
    competitor: str
    title: str
    generated_at: str | None = None
    draft_mode: str | None = None
    validation_passed: bool | None = None
    html_available: bool = False
    pdf_available: bool = False
    json_available: bool = False
    pdf_status: str = "missing"
    error_count: int = 0
    warning_count: int = 0
    blocker_codes: tuple[str, ...] = ()
    executive_status_label: str = "Review draft available"
    executive_status_tone: str = "review"
    readiness_summary: str = "The report is available for review."
    readiness_detail: str = ""
    recommended_action: str = ""
    pdf_label: str = "PDF not ready yet"
    paths: dict[str, str] = Field(default_factory=dict)


class ReportArtifactStore:
    """Filesystem-backed report artifact registry.

    V1 deliberately keeps artifacts under reports/<slug>/report.*. The store
    hides that detail from MCP and UI callers so later object storage can replace
    the implementation without changing those interfaces.
    """

    def __init__(self, root: str | Path = "reports") -> None:
        self.root = Path(root)
        self._json_cache: dict[str, dict[str, Any]] = {}

    def list_reports(self) -> list[ReportSummary]:
        if not self.root.exists():
            return []
        summaries = [
            summary
            for path in sorted(self.root.iterdir())
            if path.is_dir()
            if (summary := self.get_report(path.name)) is not None
        ]
        return sorted(summaries, key=_summary_sort_key, reverse=True)

    def get_report(self, slug: str) -> ReportSummary | None:
        report_dir = self.root / _clean_slug(slug)
        if not report_dir.exists() or not report_dir.is_dir():
            return None

        json_path = report_dir / "report.json"
        html_path = report_dir / "report.html"
        pdf_path = report_dir / "report.pdf"
        data = self.read_report(slug) if json_path.exists() else {}
        draft = _dict(data.get("draft"))
        validation = _dict(data.get("validation"))
        findings = _list(validation.get("findings"))
        renders = _list(data.get("renders"))
        competitor = str(draft.get("competitor") or _title_from_slug(slug))
        draft_metadata = _dict(draft.get("metadata"))
        is_market_report = _clean_text(draft_metadata.get("report_kind")) == "market"
        generated_at = _clean_text(draft.get("generated_at")) or _mtime_iso(json_path)
        validation_passed = validation.get("passed")
        if validation_passed is not None:
            validation_passed = bool(validation_passed)
        error_count = sum(
            1 for finding in findings if str(finding.get("severity")) == "error"
        )
        warning_count = sum(
            1 for finding in findings if str(finding.get("severity")) == "warning"
        )
        blocker_codes = _blocker_codes(findings, renders, validation_passed)
        pdf_status = _pdf_status(
            pdf_available=pdf_path.exists(),
            validation_passed=validation_passed,
            renders=renders,
        )
        readiness = _readiness_copy(
            competitor=competitor,
            validation_passed=validation_passed,
            pdf_status=pdf_status,
            blocker_codes=blocker_codes,
            error_count=error_count,
            warning_count=warning_count,
            findings=findings,
        )
        paths = {
            "json": str(json_path) if json_path.exists() else "",
            "html": str(html_path) if html_path.exists() else "",
            "pdf": str(pdf_path) if pdf_path.exists() else "",
        }
        # The standalone market report stands on its own — it is not a "JFrog vs X"
        # pairing, so it carries its own title rather than the competitor template.
        if is_market_report:
            title = _clean_text(draft_metadata.get("report_title")) or competitor
        else:
            title = f"JFrog vs {competitor}"
        return ReportSummary(
            slug=_clean_slug(slug),
            competitor=competitor,
            title=title,
            generated_at=generated_at,
            draft_mode=_clean_text(draft_metadata.get("draft_mode")),
            validation_passed=validation_passed,
            html_available=html_path.exists(),
            pdf_available=pdf_path.exists(),
            json_available=json_path.exists(),
            pdf_status=pdf_status,
            error_count=error_count,
            warning_count=warning_count,
            blocker_codes=blocker_codes,
            executive_status_label=readiness["executive_status_label"],
            executive_status_tone=readiness["executive_status_tone"],
            readiness_summary=readiness["readiness_summary"],
            readiness_detail=readiness["readiness_detail"],
            recommended_action=readiness["recommended_action"],
            pdf_label=readiness["pdf_label"],
            paths={key: value for key, value in paths.items() if value},
        )

    def read_report(self, slug: str) -> dict[str, Any]:
        cleaned_slug = _clean_slug(slug)
        if cleaned_slug in self._json_cache:
            return self._json_cache[cleaned_slug]
        path = self.root / cleaned_slug / "report.json"
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            self._json_cache[cleaned_slug] = data
            return data
        return {}

    def html_path(self, slug: str) -> Path | None:
        path = self.root / _clean_slug(slug) / "report.html"
        return path if path.exists() else None

    def pdf_path(self, slug: str) -> Path | None:
        path = self.root / _clean_slug(slug) / "report.pdf"
        return path if path.exists() else None

    def search_report_sections(
        self,
        query: str,
        *,
        competitors: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
        max_items: int = 8,
    ) -> list[ChatEvidenceItem]:
        query_text = str(query or "").strip()
        if not query_text:
            return []
        competitor_filter = {value.lower() for value in _clean_list(competitors)}
        section_filter = {value.lower() for value in _clean_list(sections)}
        terms = _search_terms(query_text)
        rows: list[tuple[int, ChatEvidenceItem]] = []

        for summary in self.list_reports():
            if competitor_filter and summary.competitor.lower() not in competitor_filter:
                continue
            data = self.read_report(summary.slug)
            for item in _report_evidence_items(summary, data):
                if section_filter and (item.section or "").lower() not in section_filter:
                    continue
                score = _text_score(
                    " ".join(
                        str(value or "")
                        for value in (
                            item.text,
                            item.title,
                            item.company,
                            item.section,
                            item.metadata.get("kind"),
                        )
                    ),
                    terms,
                )
                if score <= 0:
                    continue
                rows.append((score, item))

        rows.sort(key=lambda pair: (pair[0], pair[1].id), reverse=True)
        return [item for _, item in rows[: max(int(max_items or 8), 1)]]


def _report_evidence_items(
    summary: ReportSummary,
    data: Mapping[str, Any],
) -> list[ChatEvidenceItem]:
    draft = _dict(data.get("draft"))
    evidence: list[ChatEvidenceItem] = []
    for section in _list(draft.get("sections")):
        section_id = _clean_text(section.get("id")) or "section"
        title = _clean_text(section.get("title")) or section_id.replace("_", " ").title()
        narrative = _clean_text(section.get("narrative"))
        if narrative:
            evidence.append(
                _report_item(
                    summary,
                    section=section_id,
                    title=title,
                    text=narrative,
                    kind="section_narrative",
                )
            )
        for claim in _list(section.get("claims")):
            text = _clean_text(claim.get("text"))
            if not text:
                continue
            evidence.append(
                _report_item(
                    summary,
                    section=section_id,
                    title=title,
                    text=text,
                    kind="claim",
                    metadata={
                        "claim_type": claim.get("claim_type"),
                        "claim_confidence": claim.get("confidence"),
                    },
                )
            )

    for score in _list(draft.get("scores")):
        rationale = _clean_text(score.get("rationale"))
        if not rationale:
            continue
        category = _clean_text(score.get("category")) or "score"
        company = _clean_text(score.get("company")) or summary.competitor
        evidence.append(
            _report_item(
                summary,
                section="scoring",
                title=f"{company} - {category}",
                text=rationale,
                kind="score",
                company=company,
                metadata={
                    "score": score.get("value"),
                    "max_score": score.get("max_value"),
                    "score_confidence": score.get("confidence"),
                    "buyer_archetype": score.get("buyer_archetype"),
                },
            )
        )

    for gap in _list(draft.get("missing_data")):
        detail = _clean_text(gap.get("detail")) or _clean_text(gap.get("reason"))
        if not detail:
            continue
        evidence.append(
            _report_item(
                summary,
                section=_clean_text(gap.get("report_section")) or "missing_data",
                title="Evidence gap",
                text=detail,
                kind="missing_data",
                company=_clean_text(gap.get("company")),
                metadata={
                    "axis": gap.get("axis"),
                    "dimension": gap.get("dimension"),
                    "reason": gap.get("reason"),
                },
            )
        )

    validation = _dict(data.get("validation"))
    for finding in _list(validation.get("findings")):
        message = _clean_text(finding.get("message"))
        if not message:
            continue
        evidence.append(
            _report_item(
                summary,
                section=_clean_text(finding.get("section_id")) or "validation",
                title="Validation finding",
                text=message,
                kind="validation_finding",
                metadata={
                    "severity": finding.get("severity"),
                    "code": finding.get("code"),
                    "validation_passed": validation.get("passed"),
                },
            )
        )
    return evidence


def _report_item(
    summary: ReportSummary,
    *,
    section: str,
    title: str,
    text: str,
    kind: str,
    company: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ChatEvidenceItem:
    item_id = _stable_id("report", summary.slug, section, kind, title, text)
    merged_metadata = {
        "kind": kind,
        "report_slug": summary.slug,
        "report_title": summary.title,
        "generated_at": summary.generated_at,
        **dict(metadata or {}),
    }
    return ChatEvidenceItem(
        id=item_id,
        source="report",
        text=text,
        company=company or summary.competitor,
        title=title,
        section=section,
        confidence="medium",
        metadata=merged_metadata,
    )


def _summary_sort_key(summary: ReportSummary) -> tuple[int, int, str, str]:
    ready_rank = 1 if summary.validation_passed and summary.pdf_available else 0
    validated_rank = 1 if summary.validation_passed else 0
    return (ready_rank, validated_rank, summary.generated_at or "", summary.slug)


def _pdf_status(
    *,
    pdf_available: bool,
    validation_passed: bool | None,
    renders: Sequence[Mapping[str, Any]],
) -> str:
    if pdf_available:
        return "available"
    for render in renders:
        if str(render.get("format")) == "pdf" and str(render.get("status")) == "blocked":
            return "blocked"
    if validation_passed is False:
        return "blocked"
    return "missing"


def _blocker_codes(
    findings: Sequence[Mapping[str, Any]],
    renders: Sequence[Mapping[str, Any]],
    validation_passed: bool | None,
) -> tuple[str, ...]:
    codes: list[str] = []
    for render in renders:
        if str(render.get("format")) == "pdf" and str(render.get("status")) == "blocked":
            codes.append("pdf_render_blocked")
    severities = {"error"} if validation_passed is not False else {"error", "warning"}
    for finding in findings:
        if str(finding.get("severity")) in severities:
            code = _clean_text(finding.get("code")) or "validation_finding"
            codes.append(code)
    return tuple(dict.fromkeys(codes[:6]))


def _readiness_copy(
    *,
    competitor: str,
    validation_passed: bool | None,
    pdf_status: str,
    blocker_codes: Sequence[str],
    error_count: int,
    warning_count: int,
    findings: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    if validation_passed and pdf_status == "available":
        return {
            "executive_status_label": "Final report ready",
            "executive_status_tone": "ready",
            "readiness_summary": (
                f"The {competitor} dossier is ready for executive review, "
                "including the downloadable PDF."
            ),
            "readiness_detail": (
                "The report passed the evidence checks required for final rendering. "
                "Any remaining notes are minor evidence caveats, not release blockers."
            ),
            "recommended_action": "Use the report for review, planning, and field preparation.",
            "pdf_label": "Download PDF",
        }

    if validation_passed:
        return {
            "executive_status_label": "Report ready",
            "executive_status_tone": "ready",
            "readiness_summary": (
                f"The {competitor} dossier passed evidence review, but the PDF "
                "artifact is not present yet."
            ),
            "readiness_detail": (
                "The HTML report is ready to read. The PDF can be regenerated from "
                "the validated report output."
            ),
            "recommended_action": "Regenerate the PDF artifact when a packaged copy is needed.",
            "pdf_label": "PDF not generated yet",
        }

    themes = _finding_themes(findings, blocker_codes)
    theme_text = ", ".join(themes[:3]) if themes else "evidence support"
    evidence_phrase = _primary_evidence_phrase(themes)
    return {
        "executive_status_label": "Review draft available",
        "executive_status_tone": "review",
        "readiness_summary": (
            f"The {competitor} dossier is available as a review draft, but the "
            f"final PDF is not ready because {evidence_phrase}."
        ),
        "readiness_detail": (
            f"The report needs more validation before it should be packaged for "
            f"executive distribution. Main areas to improve: {theme_text}. "
            f"The evidence review found {error_count} blocking issue"
            f"{'' if error_count == 1 else 's'} and {warning_count} caution note"
            f"{'' if warning_count == 1 else 's'}."
        ),
        "recommended_action": (
            f"Strengthen the missing evidence for {competitor}, rerun the report, "
            "and publish the PDF only after the evidence review passes."
        ),
        "pdf_label": "PDF not ready yet",
    }


def _finding_themes(
    findings: Sequence[Mapping[str, Any]],
    blocker_codes: Sequence[str],
) -> list[str]:
    themes: list[str] = []
    code_text = " ".join(str(code or "") for code in blocker_codes)
    message_text = " ".join(str(finding.get("message") or "") for finding in findings)
    text = f"{code_text} {message_text}".lower()
    if any(term in text for term in ("product_feature", "capability", "product")):
        themes.append("product and capability evidence")
    if any(term in text for term in ("technical", "teardown", "security")):
        themes.append("technical and security evidence")
    if any(term in text for term in ("market", "business", "pricing")):
        themes.append("market and business evidence")
    if any(term in text for term in ("citation", "unsupported", "claim")):
        themes.append("claim support and citations")
    if any(term in text for term in ("missing_db_evidence", "not_found", "gap", "unknown")):
        themes.append("trusted source coverage")
    return list(dict.fromkeys(themes)) or ["trusted source coverage"]


def _primary_evidence_phrase(themes: Sequence[str]) -> str:
    if not themes:
        return "the system still needs more trusted evidence"
    if "trusted source coverage" in themes:
        return "the system still needs more trusted source coverage"
    return f"the {themes[0]} still needs more validation"


def _search_terms(query: str) -> tuple[str, ...]:
    stop_words = {
        "about",
        "after",
        "against",
        "from",
        "have",
        "into",
        "that",
        "the",
        "their",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
    terms = [
        term
        for term in "".join(
            char.lower() if char.isalnum() else " " for char in query
        ).split()
        if len(term) >= 3 and term not in stop_words
    ]
    return tuple(dict.fromkeys(terms))


def _text_score(text: str, terms: Sequence[str]) -> int:
    haystack = text.lower()
    return sum(1 for term in terms if term in haystack)


def _stable_id(*parts: Any) -> str:
    digest = hashlib.sha256(
        "||".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _clean_list(values: Sequence[str] | None) -> list[str]:
    if values is None:
        return []
    return [str(value).strip() for value in values if str(value or "").strip()]


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _clean_slug(value: str) -> str:
    cleaned = str(value or "").strip().lower().replace("_", "-")
    return "".join(char for char in cleaned if char.isalnum() or char == "-")


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in _clean_slug(slug).split("-"))


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
