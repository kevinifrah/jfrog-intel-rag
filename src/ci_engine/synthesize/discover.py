from __future__ import annotations

import json
import logging
from typing import Any

from ci_engine.acquire import context7_lane, tavily_lane, web_lane
from ci_engine.config import get as config_get
from ci_engine.config import tracked_companies
from ci_engine.db import connection, repository
from ci_engine.synthesize import pipeline

logger = logging.getLogger(__name__)


def run() -> dict[str, Any]:
    companies = tracked_companies()
    summary: dict[str, list[dict[str, Any]]] = {
        "added": [],
        "updated": [],
        "skipped": [],
        "errors": [],
        "still_missing": [],
    }
    preflight_error = _preflight_database()
    if preflight_error is not None:
        summary["errors"].append(
            {
                "phase": "preflight",
                "error": f"database preflight failed: {preflight_error}",
            }
        )
        return summary

    for company in companies:
        candidates: list[dict[str, Any]] = []
        if bool(config_get("ingestion.enable_web_research_lane", True)):
            candidates.extend(
                _collect_lane(f"{company} web-report", lambda: web_lane.search(company))
            )
        if bool(config_get("ingestion.enable_tavily_lane", True)):
            candidates.extend(
                _collect_lane(f"{company} tavily", lambda: tavily_lane.search(company))
            )
        _ingest_many(candidates, summary, phase="incremental")

    gaps = _coverage_gaps(repository.coverage_status())
    for gap in gaps:
        candidates = _targeted_candidates(gap)
        _ingest_many(candidates, summary, phase="gap", gap=gap)

    summary["still_missing"] = _coverage_gaps(repository.coverage_status())
    return summary


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True, default=str))
    return 0


def _targeted_candidates(gap: dict[str, Any]) -> list[dict[str, Any]]:
    competitor = str(gap["competitor"])
    axis = str(gap["axis"])
    dimension = str(gap["dimension"])
    topic = dimension.replace("_", " ")
    candidates: list[dict[str, Any]] = []

    if bool(config_get("ingestion.enable_tavily_lane", True)):
        candidates.extend(
            _collect_lane(
                f"{competitor} gap {axis}/{dimension} tavily",
                lambda: tavily_lane.search(competitor, topics=[topic]),
            )
        )
    if axis == "technical" and bool(config_get("ingestion.enable_context7_lane", True)):
        candidates.extend(
            _collect_lane(
                f"{competitor} gap {axis}/{dimension} context7",
                lambda: context7_lane.search(competitor, topics=[topic]),
            )
        )

    if _gap_needs_company_report(dimension) and bool(
        config_get("ingestion.enable_web_research_lane", True)
    ):
        candidates.extend(
            _collect_lane(
                f"{competitor} gap {axis}/{dimension} web-report",
                lambda: web_lane.search(competitor),
            )
        )
    return [
        {**candidate, "axis": axis, "dimension": dimension}
        for candidate in _dedupe_candidates(candidates)
    ]


def _ingest_many(
    candidates: list[dict[str, Any]],
    summary: dict[str, list[dict[str, Any]]],
    *,
    phase: str,
    gap: dict[str, Any] | None = None,
) -> None:
    for candidate in _dedupe_candidates(candidates):
        try:
            report = pipeline.ingest_candidate(candidate)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Discovery ingestion failed")
            summary["errors"].append(
                _summary_item(candidate, {"error": str(exc)}, phase, gap)
            )
            continue

        item = _summary_item(candidate, report, phase, gap)
        if report.get("skipped"):
            summary["skipped"].append(item)
        elif int(report.get("superseded", 0)) > 0:
            summary["updated"].append(item)
        else:
            summary["added"].append(item)


def _summary_item(
    candidate: dict[str, Any],
    report: dict[str, Any],
    phase: str,
    gap: dict[str, Any] | None,
) -> dict[str, Any]:
    item = {
        "competitor": candidate.get("competitor"),
        "url": candidate.get("url"),
        "phase": phase,
        "report": report,
    }
    if gap is not None:
        item["dimension"] = gap.get("dimension")
        item["axis"] = gap.get("axis")
    return item


def _coverage_gaps(coverage: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in coverage
        if int(row.get("active_sources", 0)) == 0
    ]


def _preflight_database() -> str | None:
    try:
        connection.healthcheck()
    except Exception as exc:  # noqa: BLE001
        return connection.describe_connection_error(exc)
    return None


def _collect_lane(label: str, collect: Any) -> list[dict[str, Any]]:
    try:
        candidates = collect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[discover] %s failed: %s", label, exc)
        return []
    logger.info("[discover] %s candidates=%s", label, len(candidates))
    return candidates


def _gap_needs_company_report(dimension: str) -> bool:
    return dimension in {"company_profile", "product_portfolio"}


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        url = str(candidate.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(candidate)
    return deduped


if __name__ == "__main__":
    raise SystemExit(main())
