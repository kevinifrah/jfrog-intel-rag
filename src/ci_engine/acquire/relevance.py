from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from ci_engine.config import get as config_get
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret
from ci_engine.skills import load_skill

logger = logging.getLogger(__name__)

_KNOWN_SOURCE_HOST_FRAGMENTS = {
    "analyst": (
        "forrester",
        "gartner",
        "idc",
        "ovum",
        "techtarget",
        "451research",
        "g2",
        "gartnerpeerinsights",
    ),
    "news": (
        "darkreading",
        "securityweek",
        "thehackernews",
        "techcrunch",
        "venturebeat",
        "siliconangle",
        "sdxcentral",
        "csoonline",
        "infosecurity-magazine",
        "helpnetsecurity",
    ),
    "docs": (
        "context7.com",
        "docs.",
        "developer.",
        "developers.",
        "help.",
        "support.",
        "kb.",
    ),
}

_COMPETITOR_DOMAIN_HINTS = {
    "black duck": ("blackduck", "synopsys"),
    "endor labs": ("endorlabs",),
    "github": ("github",),
    "gitlab": ("gitlab",),
    "jfrog": ("jfrog",),
    "mend": ("mend", "whitesourcesoftware"),
    "snyk": ("snyk",),
    "sonatype": ("sonatype",),
}


def _required_config(path: str) -> Any:
    value = config_get(path)
    if value is None:
        raise RuntimeError(f"missing required config value: {path}")
    return value


def _relevance_model() -> str:
    return str(_required_config("models.relevance.name"))


def _relevance_temperature() -> float:
    return float(config_get("models.relevance.temperature", 0.0))


def _relevance_threshold() -> float:
    return float(_required_config("ingestion.relevance_threshold"))


def _llm_timeout() -> float:
    return float(config_get("ingestion.llm_timeout_s", 30))


def _content_limit() -> int:
    return int(config_get("ingestion.relevance_content_chars", 6000))


@lru_cache(maxsize=1)
def _client() -> Any:
    from anthropic import Anthropic  # noqa: PLC0415

    return Anthropic(api_key=get_secret("anthropic-key"), max_retries=0)


def _candidate_message(candidate: dict[str, Any]) -> str:
    content_excerpt = _content_excerpt(candidate)
    payload = {
        "title": candidate.get("title"),
        "snippet": candidate.get("snippet"),
        "url": candidate.get("url"),
        "competitor": candidate.get("competitor"),
        "source_kind": candidate.get("source_kind"),
        "source_reason": candidate.get("source_reason"),
        "axis_hint": candidate.get("axis"),
        "dimension_hint": candidate.get("dimension"),
        "content_excerpt": content_excerpt,
        "content_length": _content_length(candidate),
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _content_excerpt(candidate: dict[str, Any]) -> str | None:
    value = (
        candidate.get("content_excerpt")
        or candidate.get("text")
        or candidate.get("raw_text")
    )
    if value is None:
        return None

    compact = str(value).strip()
    if not compact:
        return None

    limit = max(0, _content_limit())
    if limit == 0 or len(compact) <= limit:
        return compact
    return compact[:limit].rstrip()


def _content_length(candidate: dict[str, Any]) -> int | None:
    value = candidate.get("text") or candidate.get("raw_text") or candidate.get("content_excerpt")
    if value is None:
        return None
    return len(str(value))


def _competitor_domain_hints(competitor: Any) -> tuple[str, ...]:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(competitor or "").lower()).strip()
    if normalized in _COMPETITOR_DOMAIN_HINTS:
        return _COMPETITOR_DOMAIN_HINTS[normalized]

    compact = normalized.replace(" ", "")
    return (compact,) if compact else ()


def _host_is_plausible(url: Any, competitor: Any) -> bool:
    host = urlparse(str(url or "")).netloc.lower()
    if not host:
        return False

    host = host.removeprefix("www.")
    if any(hint in host for hint in _competitor_domain_hints(competitor)):
        return True

    return any(
        fragment in host
        for fragments in _KNOWN_SOURCE_HOST_FRAGMENTS.values()
        for fragment in fragments
    )


def _log_implausible_host(candidate: dict[str, Any]) -> None:
    if _host_is_plausible(candidate.get("url"), candidate.get("competitor")):
        return

    logger.warning(
        "Candidate URL host is not obviously related to the competitor or known "
        "analyst/news/docs hosts",
        extra={
            "candidate_url": candidate.get("url"),
            "candidate_competitor": candidate.get("competitor"),
        },
    )


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text is not None:
            parts.append(str(text))

    text = "".join(parts).strip()
    if not text:
        raise ValueError("relevance model returned no text")
    return text


def _parse_response(text: str) -> dict[str, Any]:
    parsed = parse_json_object(text, label="relevance model")
    if "score" not in parsed:
        raise ValueError("relevance model response is missing score")

    parsed["score"] = float(parsed["score"])
    if not 0.0 <= parsed["score"] <= 1.0:
        raise ValueError("relevance model score must be between 0.0 and 1.0")

    return parsed


def score(candidate: dict[str, Any]) -> dict[str, Any]:
    _log_implausible_host(candidate)

    response = _client().messages.create(
        model=_relevance_model(),
        system=load_skill("relevance-rubric"),
        messages=[{"role": "user", "content": _candidate_message(candidate)}],
        max_tokens=512,
        temperature=_relevance_temperature(),
        timeout=_llm_timeout(),
    )
    result = _parse_response(_response_text(response))
    if result["score"] < _relevance_threshold():
        result["relevant"] = False

    return result


__all__ = ["score"]
