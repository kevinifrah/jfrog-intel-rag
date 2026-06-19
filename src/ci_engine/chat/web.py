from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from ci_engine.chat.schemas import ChatEvidenceItem, TavilyDepth
from ci_engine.config import get as config_get
from ci_engine.secrets import get_secret

SearchFn = Callable[..., Mapping[str, Any]]

_CACHE: dict[tuple[str, TavilyDepth, int], tuple[float, tuple[ChatEvidenceItem, ...]]] = {}


def select_tavily_depth(
    question: str,
    *,
    required: bool,
    current_depth: str | None = None,
    evidence_count: int = 0,
) -> TavilyDepth:
    if not required:
        return "ultra-fast"
    if current_depth == "fast":
        return "fast"

    text = question.lower()
    product_terms = {
        "artifact",
        "capability",
        "cve",
        "docs",
        "feature",
        "firewall",
        "license",
        "malicious",
        "package",
        "policy",
        "product",
        "reachability",
        "repository",
        "sca",
        "sbom",
        "technical",
    }
    freshness_terms = {
        "current",
        "latest",
        "new",
        "news",
        "recent",
        "still true",
        "today",
        "updated",
    }
    if any(term in text for term in product_terms):
        return "fast"
    if any(term in text for term in freshness_terms):
        return "ultra-fast"
    if evidence_count == 0:
        return "fast"
    return "ultra-fast"


def web_search_evidence(
    query: str,
    *,
    depth: TavilyDepth,
    max_results: int | None = None,
    search_fn: SearchFn | None = None,
    cache_ttl_s: int | None = None,
) -> tuple[tuple[ChatEvidenceItem, ...], dict[str, Any]]:
    query_text = str(query or "").strip()
    if not query_text:
        return (), {"status": "skipped", "reason": "empty_query", "depth": depth}

    result_limit = max(
        int(max_results or config_get("chat.tavily_max_results", 3)),
        1,
    )
    ttl = max(int(cache_ttl_s or config_get("chat.cache.tavily_ttl_s", 900)), 0)
    cache_key = (query_text.lower(), depth, result_limit)
    now = time.time()
    if ttl and cache_key in _CACHE:
        cached_at, cached_items = _CACHE[cache_key]
        if now - cached_at <= ttl:
            return cached_items, {
                "status": "ok",
                "depth": depth,
                "query": query_text,
                "cached": True,
                "result_count": len(cached_items),
            }

    try:
        response = (search_fn or _client().search)(
            query=query_text,
            search_depth=depth,
            topic="general",
            max_results=result_limit,
            include_raw_content=False,
            include_answer=False,
        )
    except Exception as exc:  # pragma: no cover - exercised with integration secrets.
        return (), {
            "status": "error",
            "depth": depth,
            "query": query_text,
            "error": str(exc),
        }

    items = tuple(
        item
        for index, result in enumerate(response.get("results", []))
        if (item := _item_from_result(result, query=query_text, depth=depth, index=index))
        is not None
    )
    if ttl:
        _CACHE[cache_key] = (now, items)
    return items, {
        "status": "ok",
        "depth": depth,
        "query": query_text,
        "cached": False,
        "result_count": len(items),
        "usage": response.get("usage"),
    }


def web_check_with_retry(
    query: str,
    *,
    depth: TavilyDepth,
    retry_with_fast_if_weak: bool,
    max_results: int | None = None,
    search_fn: SearchFn | None = None,
) -> tuple[tuple[ChatEvidenceItem, ...], dict[str, Any]]:
    first_items, first_meta = web_search_evidence(
        query,
        depth=depth,
        max_results=max_results,
        search_fn=search_fn,
    )
    if (
        retry_with_fast_if_weak
        and depth == "ultra-fast"
        and first_meta.get("status") == "error"
    ):
        second_items, second_meta = web_search_evidence(
            query,
            depth="fast",
            max_results=max_results,
            search_fn=search_fn,
        )
        return second_items, {
            **second_meta,
            "retried_from": first_meta,
            "retry_reason": "ultra_fast_error",
        }
    if (
        depth == "ultra-fast"
        and retry_with_fast_if_weak
        and _weak_web_results(first_items)
    ):
        second_items, second_meta = web_search_evidence(
            query,
            depth="fast",
            max_results=max_results,
            search_fn=search_fn,
        )
        return second_items, {
            **second_meta,
            "retried_from": first_meta,
            "retry_reason": "weak_ultra_fast_results",
        }
    return first_items, first_meta


def _weak_web_results(items: Sequence[ChatEvidenceItem]) -> bool:
    if len(items) < 2:
        return True
    return sum(1 for item in items if item.url and item.text) < 2


def _item_from_result(
    result: Mapping[str, Any],
    *,
    query: str,
    depth: TavilyDepth,
    index: int,
) -> ChatEvidenceItem | None:
    url = str(result.get("url") or "").strip()
    text = str(result.get("content") or result.get("snippet") or "").strip()
    title = str(result.get("title") or url or "").strip()
    if not url or not text:
        return None
    return ChatEvidenceItem(
        id=_stable_id("tavily", query, depth, url, index),
        source="tavily",
        text=text,
        title=title,
        url=url,
        publisher=_publisher(url),
        published=_clean_text(result.get("published_date")),
        confidence="medium" if _is_official_like(url) else "low",
        metadata={
            "query": query,
            "depth": depth,
            "score": result.get("score"),
        },
    )


@lru_cache(maxsize=1)
def _client() -> Any:
    from tavily import TavilyClient  # noqa: PLC0415

    return TavilyClient(api_key=get_secret("tavily-key"))


def _publisher(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.") or None


def _is_official_like(url: str) -> bool:
    host = _publisher(url) or ""
    return any(
        host.endswith(domain)
        for domain in ("jfrog.com", "sonatype.com", "github.com", "gitlab.com")
    )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _stable_id(*parts: Any) -> str:
    return hashlib.sha256(
        "||".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]
