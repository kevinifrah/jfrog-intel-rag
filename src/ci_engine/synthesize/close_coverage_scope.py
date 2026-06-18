from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from ci_engine.acquire import context7_lane, tavily_lane, web_lane
from ci_engine.config import get as config_get
from ci_engine.db import connection, repository
from ci_engine.ontology import normalize_dimension
from ci_engine.synthesize import coverage_verdict, pipeline

logger = logging.getLogger(__name__)

_FILTER_STATES = {"unknown", "planned", "partial"}


def run(
    *,
    apply: bool = False,
    max_gaps: int | None = None,
    max_candidates_per_gap: int | None = None,
    competitor: str | Sequence[str] | None = None,
    dimension: str | Sequence[str] | None = None,
    axis: str | None = None,
    state: str | Sequence[str] | None = None,
    only_deep_map_now: bool = False,
    review_absent: bool = False,
) -> dict[str, Any]:
    competitors = _competitor_filter(competitor, only_deep_map_now=only_deep_map_now)
    dimensions = _dimension_filter(dimension, axis=axis)
    states = _state_filter(state)
    preflight_error = _preflight_database()
    if preflight_error is not None:
        return {
            "mode": "apply" if apply else "dry-run",
            "filters": _filters_summary(
                competitors=competitors,
                dimensions=dimensions,
                axis=axis,
                states=states,
                only_deep_map_now=only_deep_map_now,
                review_absent=review_absent,
            ),
            "errors": [
                {
                    "phase": "preflight",
                    "error": f"database preflight failed: {preflight_error}",
                }
            ],
            "gaps": [],
        }

    gaps = _coverage_gaps(
        max_gaps=max_gaps,
        competitors=competitors,
        dimensions=dimensions,
        axis=axis,
        states=states,
    )
    summary: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "filters": _filters_summary(
            competitors=competitors,
            dimensions=dimensions,
            axis=axis,
            states=states,
            only_deep_map_now=only_deep_map_now,
            review_absent=review_absent,
        ),
        "gaps": [_gap_summary(gap) for gap in gaps],
        "processed": [],
        "added": [],
        "updated": [],
        "skipped": [],
        "review": [],
        "errors": [],
        "still_unknown": [],
    }
    if not apply:
        summary["candidate_topics"] = [
            {**_gap_summary(gap), "topics": _topics_for_gap(gap)}
            for gap in gaps
        ]
        return summary

    repository.ensure_dimension_coverage_tables()
    candidate_limit = _candidate_limit(max_candidates_per_gap)
    for gap in gaps:
        gap_report: dict[str, Any] = {
            "gap": _gap_summary(gap),
            "before": _gap_state_summary(gap),
            "queries": _topics_for_gap(gap),
            "candidates_found": 0,
            "verdicts": [],
            "skipped": [],
            "ingested_source_ids": [],
            "after": None,
        }
        candidates = _targeted_candidates(gap, limit=candidate_limit)
        gap_report["candidates_found"] = len(candidates)
        if not candidates:
            item = {**_gap_summary(gap), "reason": "no_candidates"}
            summary["skipped"].append(item)
            gap_report["skipped"].append(item)
            gap_report["after"] = _gap_state_summary(_current_gap_status(gap))
            summary["processed"].append(gap_report)
            continue
        for candidate in candidates:
            try:
                verdict = coverage_verdict.classify_candidate(
                    candidate,
                    gap,
                    review_absent=review_absent,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Scope closure verdict failed")
                item = {
                    **_gap_summary(gap),
                    "phase": "verdict",
                    "url": candidate.get("url"),
                    "error": str(exc),
                }
                summary["errors"].append(item)
                gap_report["skipped"].append({**item, "reason": "verdict_error"})
                continue

            verdict_record = _verdict_summary(candidate, verdict)
            gap_report["verdicts"].append(verdict_record)
            if verdict.get("state") == "needs_review":
                item = {
                    **_gap_summary(gap),
                    **verdict_record,
                    "reason": verdict.get("reason") or "needs_review",
                }
                summary["review"].append(item)
                summary["skipped"].append({**item, "reason": "needs_review"})
                gap_report["skipped"].append({**item, "reason": "needs_review"})
                continue
            if not coverage_verdict.should_ingest(verdict):
                item = {
                    **_gap_summary(gap),
                    **verdict_record,
                    "reason": str(verdict.get("state") or "not_accepted"),
                }
                summary["skipped"].append(item)
                gap_report["skipped"].append(item)
                continue

            scoped_candidate = coverage_verdict.candidate_with_verdict(
                candidate,
                gap,
                verdict,
            )
            try:
                report = pipeline.ingest_candidate(scoped_candidate)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Scope closure ingestion failed")
                item = {
                    **_gap_summary(gap),
                    "phase": "ingestion",
                    "url": candidate.get("url"),
                    "error": str(exc),
                }
                summary["errors"].append(item)
                gap_report["skipped"].append({**item, "reason": "ingestion_error"})
                continue
            item = {
                **_gap_summary(gap),
                "url": candidate.get("url"),
                "verdict": verdict_record,
                "report": report,
            }
            if report.get("source_id") is not None:
                gap_report["ingested_source_ids"].append(report.get("source_id"))
            if report.get("skipped"):
                summary["skipped"].append(item)
            elif int(report.get("superseded", 0)) > 0:
                summary["updated"].append(item)
            else:
                summary["added"].append(item)
        repository.refresh_dimension_coverage_status(
            str(gap["competitor"]),
            str(gap["axis"]),
            str(gap["dimension"]),
            reason="coverage_scope_closure",
        )
        gap_report["after"] = _gap_state_summary(_current_gap_status(gap))
        summary["processed"].append(gap_report)

    summary["still_unknown"] = [
        _gap_summary(gap)
        for gap in _coverage_gaps(
            max_gaps=None,
            competitors=competitors,
            dimensions=dimensions,
            axis=axis,
            states=["unknown"],
        )
    ]
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description="Research unknown coverage gaps and ingest evidence.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Research and ingest evidence. Omit for dry-run.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="List unknown gaps and planned query topics without mutating the DB.",
    )
    parser.add_argument(
        "--max-gaps",
        type=int,
        help="Maximum unknown gaps to process.",
    )
    parser.add_argument(
        "--max-candidates-per-gap",
        type=int,
        help="Maximum candidates to ingest for each unknown gap.",
    )
    parser.add_argument(
        "--competitor",
        action="append",
        help="Limit scope closure to one competitor. Repeat for multiple competitors.",
    )
    parser.add_argument(
        "--dimension",
        action="append",
        help="Limit scope closure to one ontology dimension. Repeat for multiple dimensions.",
    )
    parser.add_argument(
        "--axis",
        choices=("technical", "business"),
        help="Limit scope closure to one ontology axis.",
    )
    parser.add_argument(
        "--state",
        action="append",
        choices=tuple(sorted(_FILTER_STATES)),
        help="Coverage state to close. Defaults to unknown. Repeat for multiple states.",
    )
    parser.add_argument(
        "--only-deep-map-now",
        action="store_true",
        help="Limit competitors to the configured deep_map_now list.",
    )
    parser.add_argument(
        "--review-absent",
        action="store_true",
        help="Send every explicit absent verdict to the review queue instead of ingesting it.",
    )
    args = parser.parse_args(argv)

    print(
        json.dumps(
            run(
                apply=bool(args.apply),
                max_gaps=args.max_gaps,
                max_candidates_per_gap=args.max_candidates_per_gap,
                competitor=args.competitor,
                dimension=args.dimension,
                axis=args.axis,
                state=args.state,
                only_deep_map_now=bool(args.only_deep_map_now),
                review_absent=bool(args.review_absent),
            ),
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def _coverage_gaps(
    *,
    max_gaps: int | None,
    competitors: list[str] | None,
    dimensions: list[str] | None,
    axis: str | None,
    states: list[str],
) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in repository.dimension_coverage_status(
            competitors=competitors,
            axis=axis,
            dimensions=dimensions,
        )
        if row.get("state") in states
    ]
    if max_gaps is None or max_gaps < 0:
        return rows
    return rows[:max_gaps]


def _targeted_candidates(
    gap: Mapping[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    competitor = str(gap["competitor"])
    axis = str(gap["axis"])
    dimension = str(gap["dimension"])
    if limit <= 0:
        return []
    topics = _topics_for_gap(gap)
    candidates: list[dict[str, Any]] = []

    if bool(config_get("ingestion.enable_tavily_lane", True)):
        for topic in topics:
            candidates.extend(
                _collect_lane(
                    f"{competitor} scope {axis}/{dimension} tavily:{topic}",
                    lambda topic=topic: tavily_lane.search(
                        competitor,
                        topics=[topic],
                        max_results=max(limit, 1),
                    ),
                )
            )

    if axis == "technical" and bool(config_get("ingestion.enable_context7_lane", True)):
        candidates.extend(
            _collect_lane(
                f"{competitor} scope {axis}/{dimension} context7",
                lambda: context7_lane.search(competitor, topics=topics[:3]),
            )
        )

    if dimension in {"company_profile", "product_portfolio"} and bool(
        config_get("ingestion.enable_web_research_lane", True)
    ):
        candidates.extend(
            _collect_lane(
                f"{competitor} scope {axis}/{dimension} web-report",
                lambda: web_lane.search(competitor),
            )
        )

    stamped = [
        {**candidate, "axis": axis, "dimension": dimension}
        for candidate in _dedupe_candidates(candidates)
    ]
    return stamped[:limit]


def _topics_for_gap(gap: Mapping[str, Any]) -> list[str]:
    competitor = str(gap["competitor"])
    topic = str(gap["dimension"]).replace("_", " ")
    return [
        topic,
        f"{competitor} {topic} supported",
        f"{competitor} {topic} not supported unsupported unavailable",
        f"{competitor} {topic} roadmap beta preview coming soon",
    ]


def _candidate_limit(value: int | None) -> int:
    if value is not None:
        return max(int(value), 0)
    return max(int(config_get("retrieval.research_on_missing.max_candidates_per_gap", 4)), 0)


def _collect_lane(label: str, collect: Any) -> list[dict[str, Any]]:
    try:
        candidates = collect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scope] %s failed: %s", label, exc)
        return []
    logger.info("[scope] %s candidates=%s", label, len(candidates))
    return candidates


def _dedupe_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        url = str(candidate.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(dict(candidate))
    return deduped


def _gap_summary(gap: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "competitor": gap.get("competitor"),
        "axis": gap.get("axis"),
        "dimension": gap.get("dimension"),
    }


def _gap_state_summary(gap: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_gap_summary(gap),
        "state": gap.get("state"),
        "confidence": gap.get("confidence"),
        "active_assertions": gap.get("active_assertions"),
        "conflict": gap.get("conflict"),
    }


def _current_gap_status(gap: Mapping[str, Any]) -> dict[str, Any]:
    rows = repository.dimension_coverage_status(
        competitors=[str(gap["competitor"])],
        axis=str(gap["axis"]),
        dimensions=[str(gap["dimension"])],
    )
    for row in rows:
        if (
            row.get("competitor") == gap.get("competitor")
            and row.get("axis") == gap.get("axis")
            and row.get("dimension") == gap.get("dimension")
        ):
            return dict(row)
    return {**dict(gap), "state": "unknown", "confidence": 0.0}


def _verdict_summary(
    candidate: Mapping[str, Any],
    verdict: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "title": candidate.get("title"),
        "url": candidate.get("url"),
        "state": verdict.get("state"),
        "confidence": verdict.get("confidence"),
        "source_trust": verdict.get("source_trust"),
        "evidence": verdict.get("evidence"),
        "reason": verdict.get("reason"),
    }


def _competitor_filter(
    value: str | Sequence[str] | None,
    *,
    only_deep_map_now: bool,
) -> list[str] | None:
    selected = _list_filter(value)
    if not only_deep_map_now:
        return selected

    deep_map_companies = _list_filter(config_get("deep_map_now", [])) or []
    if selected is None:
        return deep_map_companies
    return [competitor for competitor in selected if competitor in deep_map_companies]


def _dimension_filter(
    value: str | Sequence[str] | None,
    *,
    axis: str | None,
) -> list[str] | None:
    selected = _list_filter(value)
    if selected is None:
        return None
    return [
        normalize_dimension(dimension, axis=axis) or dimension
        for dimension in selected
    ]


def _state_filter(value: str | Sequence[str] | None) -> list[str]:
    selected = _list_filter(value) or ["unknown"]
    states: list[str] = []
    for state in selected:
        cleaned = str(state).strip().lower()
        if cleaned not in _FILTER_STATES:
            raise ValueError(
                "scope closure state must be one of: "
                + ", ".join(sorted(_FILTER_STATES))
            )
        if cleaned not in states:
            states.append(cleaned)
    return states


def _list_filter(value: str | Sequence[str] | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    cleaned: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _filters_summary(
    *,
    competitors: list[str] | None,
    dimensions: list[str] | None,
    axis: str | None,
    states: list[str],
    only_deep_map_now: bool,
    review_absent: bool,
) -> dict[str, Any]:
    return {
        "competitors": competitors,
        "dimensions": dimensions,
        "axis": axis,
        "states": states,
        "only_deep_map_now": only_deep_map_now,
        "review_absent": review_absent,
    }


def _preflight_database() -> str | None:
    try:
        connection.healthcheck()
    except Exception as exc:  # noqa: BLE001
        return connection.describe_connection_error(exc)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
