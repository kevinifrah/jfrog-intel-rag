from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx

from ci_engine.acquire.snapshots import fetch_html, write_snapshot
from ci_engine.acquire.web_lane import parse_date, source_kind_from_url
from ci_engine.secrets import get_secret

logger = logging.getLogger(__name__)

BUSINESS_TOPICS = [
    "pricing",
    "funding",
    "Gartner OR Forrester",
    "customers case study",
    "news",
]

TECHNICAL_TOPICS = [
    "documentation",
    "release notes",
    "container scanning",
    "SBOM",
    "policy",
    "API SDK integration",
]

DEFAULT_TOPICS = [
    *TECHNICAL_TOPICS,
    *BUSINESS_TOPICS,
]

Candidate = dict[str, Any]


@lru_cache(maxsize=1)
def _client() -> Any:
    from tavily import TavilyClient  # noqa: PLC0415

    return TavilyClient(api_key=get_secret("tavily-key"))


def search(
    competitor: str,
    topics: list[str] | None = None,
    *,
    max_results: int = 5,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen_urls: set[str] = set()

    for topic in topics or DEFAULT_TOPICS:
        query = _query_for_topic(competitor, topic)
        response = _client().search(
            query=query,
            search_depth="advanced",
            topic="general",
            max_results=max_results,
            include_raw_content="markdown",
        )
        for result in response.get("results", []):
            candidate = _candidate_from_result(result, competitor)
            if not candidate["url"] or candidate["url"] in seen_urls:
                continue

            seen_urls.add(candidate["url"])
            candidates.append(candidate)
            _snapshot_result(result, candidate)

    return candidates


def _query_for_topic(competitor: str, topic: str) -> str:
    if "{competitor}" in topic:
        return topic.format(competitor=competitor)
    if competitor.lower() in topic.lower():
        return topic
    return f"{competitor} {topic}"


def _candidate_from_result(result: dict[str, Any], competitor: str) -> Candidate:
    raw_content = result.get("raw_content")
    return {
        "title": result.get("title") or result.get("url") or "",
        "url": result.get("url") or "",
        "snippet": result.get("content") or raw_content or "",
        "text": raw_content,
        "competitor": competitor,
        "published": parse_date(result.get("published_date")),
        "source_kind": source_kind_from_url(str(result.get("url") or "")),
        "source_reason": "tavily search result",
    }


def _snapshot_result(result: dict[str, Any], candidate: Candidate) -> None:
    raw_content = result.get("raw_content")
    if raw_content:
        write_snapshot(
            competitor=candidate["competitor"],
            title=candidate["title"],
            url=candidate["url"],
            content=raw_content,
            content_type="text/markdown",
            published=candidate["published"],
        )
        return

    try:
        fetch_html(
            candidate["url"],
            competitor=candidate["competitor"],
            title=candidate["title"],
            published=candidate["published"],
            timeout=8.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Could not snapshot Tavily result HTML: %s", exc)


__all__ = ["BUSINESS_TOPICS", "DEFAULT_TOPICS", "TECHNICAL_TOPICS", "search"]
