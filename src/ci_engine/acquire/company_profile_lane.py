from __future__ import annotations

import logging
from typing import Any

from ci_engine.acquire import tavily_lane, web_lane
from ci_engine.config import get as config_get

PROFILE_TOPICS = [
    "{competitor} official products platform",
    "{competitor} product portfolio",
    "{competitor} documentation products",
    "{competitor} pricing packaging",
    "{competitor} customers case studies",
    "{competitor} security trust release notes",
]

PROFILE_DIMENSION_BY_KIND = {
    "vendor_site": "company_profile",
    "pricing": "pricing_packaging",
    "customers": "customers_case_studies",
    "rss_feed": "release_cadence",
    "release_notes": "release_cadence",
    "changelog": "release_cadence",
    "blog": "leadership_strategy_signals",
    "security_advisories": "security_research",
    "status": "architecture_deployment_model",
}

PROFILE_DOC_TYPE_BY_KIND = {
    "api_docs": "docs",
    "blog": "blog",
    "changelog": "release_notes",
    "customers": "company_fact",
    "docs": "docs",
    "pricing": "pricing",
    "release_notes": "release_notes",
    "rss_feed": "blog",
    "security_advisories": "company_fact",
    "status": "company_fact",
    "vendor_site": "company_fact",
}

Candidate = dict[str, Any]
logger = logging.getLogger(__name__)


def search(competitor: str, *, limit: int | None = None) -> list[Candidate]:
    candidates: list[Candidate] = []
    candidate_limit = _candidate_limit(limit)
    try:
        source_records = web_lane.discover_must_follow_sources(
            competitor,
            max_uses=max(1, min(8, candidate_limit)),
        )
        candidates.extend(
            web_lane.collect(
                competitor,
                source_records[:candidate_limit],
                limit_per_feed=int(config_get("ingestion.profile_feed_limit", 3)),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Company profile web source discovery failed for %s: %s",
            competitor,
            exc,
        )

    try:
        remaining = candidate_limit - len(_dedupe_candidates(candidates))
        if remaining > 0:
            candidates.extend(
                tavily_lane.search(
                    competitor,
                    topics=PROFILE_TOPICS[:remaining],
                    max_results=remaining,
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Company profile Tavily search failed for %s: %s",
            competitor,
            exc,
        )

    return _dedupe_candidates(
        [_tag_profile_candidate(candidate) for candidate in candidates]
    )[:candidate_limit]


def _tag_profile_candidate(candidate: Candidate) -> Candidate:
    candidate = dict(candidate)
    source_kind = str(
        candidate.get("source_kind") or web_lane.source_kind_from_url(candidate["url"])
    )
    dimension = PROFILE_DIMENSION_BY_KIND.get(source_kind, "product_portfolio")
    candidate.setdefault("source_kind", source_kind)
    candidate.setdefault("axis", _axis_for_dimension(dimension))
    candidate.setdefault("dimension", dimension)
    candidate.setdefault("doc_type", PROFILE_DOC_TYPE_BY_KIND.get(source_kind, "company_fact"))
    return candidate


def _axis_for_dimension(dimension: str) -> str:
    if dimension in {
        "company_profile",
        "pricing_packaging",
        "customers_case_studies",
        "leadership_strategy_signals",
    }:
        return "business"
    if dimension == "product_portfolio":
        return "both"
    return "technical"


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    deduped: list[Candidate] = []
    for candidate in candidates:
        url = str(candidate.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(candidate)
    return deduped


def _candidate_limit(limit: int | None) -> int:
    if limit is None:
        limit = int(config_get("ingestion.max_company_profile_candidates", 8))
    return max(1, limit)


__all__ = ["PROFILE_TOPICS", "search"]
