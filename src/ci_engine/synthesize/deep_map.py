from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from ci_engine.acquire import context7_lane, tavily_lane, web_lane
from ci_engine.config import get as config_get
from ci_engine.db import connection, repository
from ci_engine.synthesize import pipeline

logger = logging.getLogger(__name__)


def run(
    competitor: str | None = None,
    *,
    max_candidates_per_dimension: int | None = None,
) -> dict[str, Any]:
    companies = [competitor] if competitor else list(config_get("deep_map_now", []))
    dimensions = _dimensions()
    ingested: list[dict[str, Any]] = []
    dimension_limit = _limit_value(
        max_candidates_per_dimension,
        "ingestion.max_candidates_per_dimension",
        8,
    )

    logger.info(
        "[deep-map] starting companies=%s dimensions=%s",
        ", ".join(companies),
        len(dimensions),
    )
    preflight_error = _preflight_database()
    if preflight_error is not None:
        logger.error("[deep-map] database preflight failed: %s", preflight_error)
        return {
            "error": f"database preflight failed: {preflight_error}",
            "ingested": [],
            "coverage": [],
        }

    for name in companies:
        logger.info("[deep-map] company=%s", name)
        web_candidates = gather_web_report_candidates(name)
        logger.info("[deep-map] web report candidates=%s", len(web_candidates))
        _ingest_candidates(
            ingested,
            name,
            web_candidates,
            axis="both",
            dimension="web_report",
            label="web-report",
        )
        for index, (axis, dimension) in enumerate(dimensions, start=1):
            logger.info(
                "[deep-map] %s/%s %s/%s gathering candidates",
                index,
                len(dimensions),
                axis,
                dimension,
            )
            candidates = gather_candidates(name, axis, dimension, limit=dimension_limit)
            candidates = _limit_candidates(
                candidates,
                dimension_limit,
                f"{axis}/{dimension}",
            )
            logger.info(
                "[deep-map] %s/%s candidates=%s",
                axis,
                dimension,
                len(candidates),
            )
            _ingest_candidates(
                ingested,
                name,
                candidates,
                axis=axis,
                dimension=dimension,
                label=f"{axis}/{dimension}",
            )

    coverage = coverage_report(companies)
    for row in coverage:
        logger.info(
            "[coverage] %s %s/%s covered=%s active_sources=%s freshest=%s",
            row.get("competitor"),
            row.get("axis"),
            row.get("dimension"),
            row.get("covered"),
            row.get("active_sources"),
            row.get("freshest_publish_date"),
        )
    logger.info("[deep-map] done ingested=%s", len(ingested))
    return {
        "ingested": ingested,
        "coverage": coverage,
    }


def gather_web_report_candidates(competitor: str) -> list[dict[str, Any]]:
    if not bool(config_get("ingestion.enable_web_research_lane", True)):
        logger.info("[gather] web-report disabled")
        return []
    return _dedupe_candidates(
        _collect_lane("web-report", lambda: web_lane.search(competitor))
    )


def gather_candidates(
    competitor: str,
    axis: str,
    dimension: str,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    topic = _topic_for_dimension(dimension)
    lane_limit = max(1, limit or int(config_get("ingestion.max_candidates_per_dimension", 8)))
    candidates: list[dict[str, Any]] = []

    if bool(config_get("ingestion.enable_tavily_lane", True)):
        candidates.extend(
            _collect_lane(
                f"{axis}/{dimension} tavily",
                lambda: tavily_lane.search(
                    competitor,
                    topics=[topic],
                    max_results=lane_limit,
                ),
            )
        )
    if axis == "technical" and bool(config_get("ingestion.enable_context7_lane", True)):
        candidates.extend(
            _collect_lane(
                f"{axis}/{dimension} context7",
                lambda: context7_lane.search(competitor, topics=[topic]),
            )
        )
    return candidates


def _ingest_with_report(candidate: dict[str, Any]) -> dict[str, Any]:
    try:
        return pipeline.ingest_candidate(candidate)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Deep-map ingestion failed")
        return {"error": str(exc)}


def coverage_report(companies: list[str]) -> list[dict[str, Any]]:
    wanted = set(companies)
    return [
        {
            **row,
            "covered": int(row.get("active_sources", 0)) >= 1,
        }
        for row in repository.coverage_status()
        if row.get("competitor") in wanted
    ]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Deep-map configured companies")
    parser.add_argument("--competitor", help="Company name to deep-map")
    parser.add_argument(
        "--max-candidates-per-dimension",
        type=int,
        help="Maximum candidates to ingest for each ontology dimension",
    )
    args = parser.parse_args(argv)

    print(
        json.dumps(
            run(
                args.competitor,
                max_candidates_per_dimension=args.max_candidates_per_dimension,
            ),
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def _dimensions() -> list[tuple[str, str]]:
    ontology = config_get("ontology", {})
    dimensions: list[tuple[str, str]] = []
    for axis in ("technical", "business"):
        for dimension in ontology.get(axis, []):
            dimensions.append((axis, str(dimension)))
    return dimensions


def _topic_for_dimension(dimension: str) -> str:
    return dimension.replace("_", " ")


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


def _ingest_candidates(
    ingested: list[dict[str, Any]],
    competitor: str,
    candidates: list[dict[str, Any]],
    *,
    axis: str,
    dimension: str,
    label: str,
) -> None:
    for candidate_index, candidate in enumerate(candidates, start=1):
        candidate_for_ingest = dict(candidate)
        if dimension != "web_report":
            candidate_for_ingest["axis"] = axis
            candidate_for_ingest["dimension"] = dimension
        logger.info(
            "[ingest] %s %s/%s %s",
            label,
            candidate_index,
            len(candidates),
            candidate_for_ingest.get("url"),
        )
        report = _ingest_with_report(candidate_for_ingest)
        logger.info("[ingest] %s", _report_outcome(report))
        ingested.append(
            {
                "competitor": competitor,
                "axis": candidate_for_ingest.get("axis", axis),
                "dimension": candidate_for_ingest.get("dimension", dimension),
                "url": candidate_for_ingest.get("url"),
                "report": report,
            }
        )


def _preflight_database() -> str | None:
    if not bool(config_get("ingestion.preflight_db", True)):
        return None
    try:
        connection.healthcheck()
    except Exception as exc:  # noqa: BLE001
        return connection.describe_connection_error(exc)
    return None


def _limit_value(value: int | None, config_path: str, default: int) -> int:
    if value is not None:
        return value
    return int(config_get(config_path, default))


def _limit_candidates(
    candidates: list[dict[str, Any]],
    limit: int,
    label: str,
) -> list[dict[str, Any]]:
    deduped = _dedupe_candidates(candidates)
    if limit <= 0 or len(deduped) <= limit:
        return deduped

    logger.info(
        "[gather] %s limited candidates=%s/%s",
        label,
        limit,
        len(deduped),
    )
    return deduped[:limit]


def _collect_lane(
    label: str,
    collect: Any,
) -> list[dict[str, Any]]:
    try:
        candidates = collect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[gather] %s failed: %s", label, exc)
        return []

    logger.info("[gather] %s candidates=%s", label, len(candidates))
    return candidates


def _report_outcome(report: dict[str, Any]) -> str:
    if "error" in report:
        return f"error={report['error']}"
    if report.get("skipped"):
        return f"skipped={report['skipped']}"
    return (
        f"source_id={report.get('source_id')} "
        f"chunks={report.get('n_chunks', 0)} "
        f"entities={report.get('n_entities', 0)} "
        f"edges={report.get('n_edges', 0)} "
        f"superseded={report.get('superseded', 0)}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
