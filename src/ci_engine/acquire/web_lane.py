from __future__ import annotations

import json
import logging
import hashlib
import re
from datetime import date, datetime
from functools import lru_cache
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Any, Iterable

import feedparser
import httpx
import trafilatura
from trafilatura.metadata import extract_metadata

from ci_engine.acquire.snapshots import fetch_html, slugify, write_snapshot
from ci_engine.config import get as config_get
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret
from ci_engine.skills import load_skill

Candidate = dict[str, Any]
logger = logging.getLogger(__name__)

MUST_FOLLOW_SOURCE_TYPES = [
    "vendor_site",
    "docs",
    "api_docs",
    "release_notes",
    "changelog",
    "blog",
    "security_advisories",
    "pricing",
    "customers",
    "rss_feed",
    "status",
    "other",
]

DISCOVERY_TOPICS = [
    "vendor home page",
    "official product docs",
    "developer or API docs",
    "changelog page",
    "release notes RSS feed",
    "security blog RSS feed",
    "security advisories",
    "pricing page",
    "customer case studies",
]

_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+")
_TRAILING_URL_PUNCTUATION = ".,;:!?"


def fetch(url: str, competitor: str | None = None) -> dict[str, Any]:
    html = fetch_html(url, competitor=competitor or "_unknown")
    return extract_html(html, url=url)


def extract_html(html: str, url: str | None = None) -> dict[str, Any]:
    metadata = extract_metadata(html, default_url=url)
    text = (
        trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            deduplicate=True,
        )
        or ""
    )
    return {
        "title": getattr(metadata, "title", None) or "",
        "text": text.strip(),
        "published": parse_date(getattr(metadata, "date", None)),
    }


def collect(
    competitor: str,
    sources: Iterable[str | dict[str, str]],
    *,
    limit_per_feed: int = 10,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for source in sources:
        url, source_kind, reason = _source_parts(source)
        try:
            if _looks_like_feed(url):
                candidates.extend(
                    from_feed(
                        url,
                        competitor,
                        limit=limit_per_feed,
                        source_kind=source_kind,
                        source_reason=reason,
                    )
                )
            else:
                candidates.append(
                    candidate_from_url(
                        url,
                        competitor,
                        source_kind=source_kind,
                        source_reason=reason,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Web lane failed to collect source url=%s: %s",
                url,
                exc,
            )
    return candidates


def search(
    competitor: str,
    sources: Iterable[str | dict[str, str]] | None = None,
    *,
    limit_per_feed: int = 10,
    topics: Iterable[str] | None = None,
    max_uses: int = 8,
    source_limit: int | None = None,
) -> list[Candidate]:
    del sources, limit_per_feed, topics, max_uses, source_limit
    report = generate_report(competitor)
    return split_report(
        str(report["report_markdown"]),
        competitor,
        str(report["raw_path"]),
        published=report["published"],
    )


def render_prompt(competitor: str) -> str:
    return load_skill("deep-company-research").replace("[COMPANY NAME]", competitor)


def generate_report(competitor: str) -> dict[str, Any]:
    with _anthropic_client().messages.stream(
        model=str(config_get("models.web_research.name", "claude-sonnet-4-6")),
        max_tokens=int(config_get("models.web_research.max_tokens", 12000)),
        temperature=float(config_get("models.web_research.temperature", 0.0)),
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": int(config_get("models.web_research.web_search_max_uses", 12)),
            }
        ],
        messages=[{"role": "user", "content": render_prompt(competitor)}],
        timeout=float(config_get("models.web_research.timeout_s", 180)),
    ) as stream:
        report_markdown = stream.get_final_text().strip()
    if not report_markdown:
        raise ValueError("web research model returned no text")
    raw_path = write_snapshot(
        competitor=competitor,
        title=f"{competitor} official deep company research",
        url=_report_root_url(competitor, report_markdown),
        content=report_markdown,
        content_type="text/markdown",
        published=date.today(),
    )
    return {
        "report_markdown": report_markdown,
        "raw_path": str(raw_path),
        "published": date.today(),
        "report_hash": _report_hash(report_markdown),
    }


def split_report(
    report_markdown: str,
    competitor: str,
    raw_path: str,
    *,
    published: date | None = None,
) -> list[Candidate]:
    response = _anthropic_client().messages.create(
        model=str(config_get("models.report_splitter.name", "claude-haiku-4-5")),
        system=load_skill("deep-report-splitter"),
        messages=[
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "company": competitor,
                        "ontology": config_get("ontology", {}),
                        "report_markdown": report_markdown,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            }
        ],
        max_tokens=int(config_get("models.report_splitter.max_tokens", 4096)),
        temperature=float(config_get("models.report_splitter.temperature", 0.0)),
        timeout=float(config_get("models.report_splitter.timeout_s", 60)),
    )
    splitter_text = _response_text(response, label="report splitter model")
    try:
        payload = parse_json_object(
            splitter_text,
            label="report splitter model",
        )
        slices = payload.get("slices", [])
    except ValueError as exc:
        logger.warning("Report splitter returned invalid JSON; using section fallback: %s", exc)
        slices = _fallback_report_slices(report_markdown)
    return _candidates_from_slices(
        slices,
        competitor=competitor,
        raw_path=raw_path,
        report_markdown=report_markdown,
        published=published or date.today(),
    )


def _candidates_from_slices(
    slices: Any,
    *,
    competitor: str,
    raw_path: str,
    report_markdown: str,
    published: date,
) -> list[Candidate]:
    if not isinstance(slices, list):
        return []

    report_hash = _report_hash(report_markdown)
    dimensions = _ontology_dimensions()
    max_slices = int(config_get("ingestion.max_report_slices", 32))
    seen_dimensions: set[str] = set()
    candidates: list[Candidate] = []
    for item in slices:
        if not isinstance(item, dict):
            continue
        dimension = str(item.get("dimension") or "").strip()
        text = str(item.get("text") or "").strip()
        if not dimension or not text or dimension in seen_dimensions:
            continue
        if dimensions and dimension not in dimensions:
            continue

        seen_dimensions.add(dimension)
        axis = _normalized_axis(item.get("axis"))
        doc_type = _normalized_doc_type(item.get("doc_type"))
        title = str(item.get("title") or f"{competitor} official research: {dimension}")
        summary = str(item.get("summary") or _snippet(text))
        citations = normalize_citations(item.get("citations"), fallback_text=text)
        if not citations:
            logger.warning(
                "Official research report slice has no extracted citations",
                extra={
                    "candidate_competitor": competitor,
                    "candidate_dimension": dimension,
                },
            )
        candidates.append(
            {
                "title": title,
                "url": _report_slice_url(competitor, report_hash, dimension),
                "snippet": _snippet(summary),
                "text": text,
                "competitor": competitor,
                "published": published,
                "axis": axis,
                "dimension": dimension,
                "doc_type": doc_type,
                "source_kind": "official_llm_research_report",
                "source_reason": "official-source deep company research report slice",
                "raw_path": raw_path,
                "citations": citations,
            }
        )
        if len(candidates) >= max_slices:
            break

    return candidates


def normalize_citations(
    citations: Any,
    *,
    fallback_text: str | None = None,
) -> list[dict[str, str | None]]:
    normalized: list[dict[str, str | None]] = []
    seen: set[str] = set()

    if isinstance(citations, list):
        for item in citations:
            citation = _citation_from_value(item)
            if citation is None:
                continue
            _append_citation(normalized, seen, citation)

    if fallback_text:
        for citation in extract_citations(fallback_text):
            _append_citation(normalized, seen, citation)

    return normalized


def extract_citations(text: str) -> list[dict[str, str | None]]:
    citations: list[dict[str, str | None]] = []
    for match in _URL_RE.finditer(str(text or "")):
        url = _clean_citation_url(match.group(0))
        if not url:
            continue
        label = _markdown_link_label(str(text or ""), match.start())
        citations.append(
            {
                "url": url,
                "label": label,
                "date_text": _date_text_near_url(str(text or ""), match, label),
            }
        )
    return citations


def _citation_from_value(value: Any) -> dict[str, str | None] | None:
    if isinstance(value, str):
        url = _clean_citation_url(value)
        if not url:
            return None
        return {"url": url, "label": None, "date_text": None}

    if not isinstance(value, dict):
        return None

    url = _clean_citation_url(value.get("url") or value.get("cited_url") or "")
    if not url:
        return None
    return {
        "url": url,
        "label": _clean_optional_text(value.get("label") or value.get("citation_label")),
        "date_text": _clean_optional_text(
            value.get("date_text") or value.get("cited_date_text")
        ),
    }


def _append_citation(
    citations: list[dict[str, str | None]],
    seen: set[str],
    citation: dict[str, str | None],
) -> None:
    url = citation["url"]
    if not url or url in seen:
        return
    seen.add(url)
    citations.append(citation)


def _clean_citation_url(value: Any) -> str:
    url = str(value or "").strip().strip("<>")
    url = url.rstrip(_TRAILING_URL_PUNCTUATION)
    if not url.startswith(("http://", "https://")):
        return ""
    return url


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _markdown_link_label(text: str, url_start: int) -> str | None:
    if url_start == 0 or text[url_start - 1] != "(":
        return None
    label_end = text.rfind("]", 0, url_start - 1)
    if label_end == -1:
        return None
    label_start = text.rfind("[", 0, label_end)
    if label_start == -1:
        return None
    return _clean_optional_text(text[label_start + 1 : label_end])


def _date_text_near_url(
    text: str,
    match: re.Match[str],
    markdown_label: str | None,
) -> str | None:
    if match.start() > 0 and text[match.start() - 1] == "(":
        return _date_text_from_label(markdown_label)

    bracket_start = text.rfind("[", 0, match.start() + 1)
    bracket_end = text.find("]", match.end())
    if bracket_start == -1 or bracket_end == -1:
        return None
    if bracket_end - bracket_start > 180:
        return None

    bracket_text = text[bracket_start + 1 : bracket_end]
    if match.group(0) not in bracket_text:
        return None
    tail = bracket_text.split(match.group(0), 1)[-1].strip(" ,;-")
    return _clean_optional_text(tail)


def _date_text_from_label(label: str | None) -> str | None:
    if not label or "," not in label:
        return None
    tail = label.split(",", 1)[1].strip()
    if not re.search(r"\d", tail):
        return None
    return _clean_optional_text(tail)


def _fallback_report_slices(report_markdown: str) -> list[dict[str, Any]]:
    sections = _markdown_sections(report_markdown)
    if not sections:
        return [
            {
                "axis": "business",
                "dimension": "company_profile",
                "doc_type": "company_fact",
                "title": "Official deep company research report",
                "summary": "Official-source deep company research report.",
                "text": report_markdown,
                "confidence": 0.5,
            }
        ]

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for heading, text in sections:
        mapping = _section_mapping(heading)
        if mapping is None or not text.strip():
            continue
        axis, dimension, doc_type = mapping
        key = (axis, dimension, doc_type)
        item = grouped.setdefault(
            key,
            {
                "axis": axis,
                "dimension": dimension,
                "doc_type": doc_type,
                "title": heading,
                "texts": [],
                "confidence": 0.5,
            },
        )
        item["texts"].append(f"## {heading}\n\n{text.strip()}")

    slices: list[dict[str, Any]] = []
    for item in grouped.values():
        combined_text = "\n\n".join(item.pop("texts")).strip()
        item["summary"] = _snippet(combined_text)
        item["text"] = combined_text
        slices.append(item)
    return slices


def _markdown_sections(report_markdown: str) -> list[tuple[str, str]]:
    matches = list(
        re.finditer(r"(?m)^#{2,3}\s+\d+(?:\.\d+)*\.?\s+(.+)$", report_markdown)
    )
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(report_markdown)
        sections.append((match.group(1).strip(), report_markdown[start:end].strip()))
    return sections


def _section_mapping(heading: str) -> tuple[str, str, str] | None:
    normalized = heading.lower()
    if "snapshot" in normalized:
        return "business", "company_profile", "company_fact"
    if "business overview" in normalized:
        return "business", "company_profile", "company_fact"
    if "market positioning" in normalized:
        return "business", "market_positioning", "company_fact"
    if "financials" in normalized or "funding" in normalized:
        return "business", "funding_ownership", "company_fact"
    if "people" in normalized or "org" in normalized:
        return "business", "leadership_strategy_signals", "company_fact"
    if "technical deep dive" in normalized:
        return "technical", "architecture_deployment_model", "docs"
    if "product" in normalized or "traction" in normalized:
        return "both", "product_portfolio", "company_fact"
    if "strategy" in normalized or "trajectory" in normalized:
        return "business", "leadership_strategy_signals", "company_fact"
    if "swot" in normalized:
        return "business", "market_positioning", "company_fact"
    if "takeaways" in normalized or "open questions" in normalized:
        return "business", "company_profile", "company_fact"
    return None


def _response_text(response: Any, *, label: str) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = None
        if _block_value(block, "type") == "text":
            text = _block_value(block, "text")
        elif _block_value(block, "text") is not None:
            text = _block_value(block, "text")
        if text is not None:
            parts.append(str(text))

    text = "".join(parts).strip()
    if not text:
        raise ValueError(f"{label} returned no text")
    return text


def _report_hash(report_markdown: str) -> str:
    return hashlib.sha1(report_markdown.encode("utf-8")).hexdigest()[:12]


def _report_root_url(competitor: str, report_markdown: str) -> str:
    return f"ci-report://official-deep-research/{slugify(competitor)}/{_report_hash(report_markdown)}"


def _report_slice_url(competitor: str, report_hash: str, dimension: str) -> str:
    return (
        "ci-report://official-deep-research/"
        f"{slugify(competitor)}/{report_hash}#{slugify(dimension)}"
    )


def _ontology_dimensions() -> set[str]:
    ontology = config_get("ontology", {})
    dimensions: set[str] = set()
    if not isinstance(ontology, dict):
        return dimensions
    for axis_dimensions in ontology.values():
        if isinstance(axis_dimensions, list):
            dimensions.update(str(dimension) for dimension in axis_dimensions)
    return dimensions


def _normalized_axis(value: Any) -> str:
    axis = str(value or "both").lower()
    if axis in {"technical", "business", "both"}:
        return axis
    return "both"


def _normalized_doc_type(value: Any) -> str:
    doc_type = str(value or "company_fact").lower()
    allowed = {
        "company_fact",
        "docs",
        "pricing",
        "release_notes",
        "news",
        "blog",
        "analyst",
    }
    if doc_type in allowed:
        return doc_type
    return "company_fact"


def discover_sources(
    competitor: str,
    topics: Iterable[str] | None = None,
    *,
    max_uses: int = 5,
) -> list[str]:
    return [
        source["url"]
        for source in discover_must_follow_sources(
            competitor,
            topics=topics,
            max_uses=max_uses,
        )
    ]


def discover_must_follow_sources(
    competitor: str,
    topics: Iterable[str] | None = None,
    *,
    max_uses: int = 8,
) -> list[dict[str, str]]:
    response = _anthropic_client().messages.create(
        model=str(config_get("models.chat_answer.name", "claude-sonnet-4-6")),
        max_tokens=1400,
        temperature=0.0,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_uses,
            }
        ],
        messages=[
            {
                "role": "user",
                "content": _must_follow_prompt(competitor, topics or DISCOVERY_TOPICS),
            }
        ],
        timeout=float(config_get("ingestion.web_discovery_timeout_s", 30)),
    )
    return _source_records_from_response(response)


def from_feed(
    feed_url: str,
    competitor: str,
    *,
    limit: int = 10,
    source_kind: str = "rss_feed",
    source_reason: str | None = None,
) -> list[Candidate]:
    with httpx.Client(follow_redirects=True, timeout=20.0) as client:
        response = client.get(feed_url)
        response.raise_for_status()

    write_snapshot(
        competitor=competitor,
        title=f"{competitor} feed",
        url=str(response.url),
        content=response.text,
        content_type=response.headers.get("content-type", "application/xml"),
    )

    feed = feedparser.parse(response.content)
    candidates: list[Candidate] = []
    for entry in feed.entries[:limit]:
        url = entry.get("link") or feed_url
        title = entry.get("title") or url
        published = parse_date(
            entry.get("published")
            or entry.get("updated")
            or entry.get("created")
            or entry.get("published_parsed")
            or entry.get("updated_parsed")
        )
        snippet = _entry_snippet(entry)

        try:
            page = fetch(url, competitor=competitor)
        except (httpx.HTTPError, ValueError):
            page = {}

        candidates.append(
            {
                "title": page.get("title") or title,
                "url": url,
                "snippet": _snippet(page.get("text") or snippet),
                "text": page.get("text"),
                "competitor": competitor,
                "published": page.get("published") or published,
                "source_kind": source_kind,
                "source_reason": source_reason,
            }
        )

    return candidates


def candidate_from_url(
    url: str,
    competitor: str,
    *,
    source_kind: str | None = None,
    source_reason: str | None = None,
) -> Candidate:
    page = fetch(url, competitor=competitor)
    return {
        "title": page["title"] or url,
        "url": url,
        "snippet": _snippet(page["text"]),
        "text": page["text"],
        "competitor": competitor,
        "published": page["published"],
        "source_kind": source_kind or source_kind_from_url(url),
        "source_reason": source_reason,
    }


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, struct_time):
        return date(value.tm_year, value.tm_mon, value.tm_mday)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        for parser in (date.fromisoformat,):
            try:
                return parser(value[:10])
            except ValueError:
                pass
        try:
            return parsedate_to_datetime(value).date()
        except (TypeError, ValueError):
            return None
    return None


def _entry_snippet(entry: Any) -> str:
    if entry.get("summary"):
        return str(entry["summary"])
    if entry.get("description"):
        return str(entry["description"])
    if entry.get("content"):
        return " ".join(str(item.get("value", "")) for item in entry["content"])
    return ""


def _looks_like_feed(url: str) -> bool:
    lowered = url.lower()
    return lowered.endswith((".rss", ".xml", ".atom")) or "feed" in lowered


def _source_parts(source: str | dict[str, str]) -> tuple[str, str, str | None]:
    if isinstance(source, dict):
        url = source["url"]
        source_kind = source.get("kind") or source.get("source_kind")
        return url, source_kind or source_kind_from_url(url), source.get("reason")
    return source, source_kind_from_url(source), None


@lru_cache(maxsize=1)
def _anthropic_client() -> Any:
    from anthropic import Anthropic  # noqa: PLC0415

    return Anthropic(api_key=get_secret("anthropic-key"), max_retries=0)


def _discovery_prompt(competitor: str, topics: Iterable[str]) -> str:
    return _must_follow_prompt(competitor, topics)


def _must_follow_prompt(competitor: str, topics: Iterable[str]) -> str:
    kinds = ", ".join(MUST_FOLLOW_SOURCE_TYPES)
    return (
        f"Build the must-follow source map for {competitor}. Find canonical URLs "
        "or RSS/Atom feeds that should be monitored continuously for competitive "
        "intelligence. Include vendor site, docs, developer/API docs, release notes, "
        "changelogs, blogs, security advisories, pricing, customers/case studies, "
        "and status/trust pages where available. Search for these topics: "
        f"{', '.join(topics)}. Return ONLY a compact JSON array of objects with "
        f'keys "kind", "url", and "reason". The "kind" must be one of: {kinds}.'
    )


def _sources_from_response(response: Any) -> list[str]:
    return [source["url"] for source in _source_records_from_response(response)]


def _source_records_from_response(response: Any) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for block in getattr(response, "content", []) or []:
        block_type = _block_value(block, "type")
        if block_type == "web_search_tool_result":
            _append_search_result_sources(sources, _block_value(block, "content"))
        elif block_type == "text":
            _append_json_sources(sources, str(_block_value(block, "text") or ""))

    return _dedupe_source_records(sources)


def _append_search_result_sources(sources: list[dict[str, str]], content: Any) -> None:
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            title = str(item.get("title") or "web search result")
            sources.append(
                {
                    "kind": "other",
                    "url": url,
                    "reason": title[:160],
                }
            )


def _append_json_sources(sources: list[dict[str, str]], text: str) -> None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return
    if isinstance(parsed, dict):
        parsed = [parsed]
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return

    for item in parsed:
        source = _normalize_source_record(item)
        if source is not None:
            sources.append(source)


def _normalize_source_record(item: Any) -> dict[str, str] | None:
    if isinstance(item, str):
        url = item
        kind = source_kind_from_url(url)
        reason = kind.replace("_", " ")
    elif isinstance(item, dict):
        url = item.get("url") or item.get("feed_url")
        if not isinstance(url, str):
            return None
        kind = str(item.get("kind") or item.get("type") or source_kind_from_url(url))
        reason = str(item.get("reason") or item.get("title") or kind.replace("_", " "))
    else:
        return None

    if not url.startswith(("http://", "https://")):
        return None
    if kind not in MUST_FOLLOW_SOURCE_TYPES:
        kind = source_kind_from_url(url)
    return {
        "kind": kind,
        "url": url,
        "reason": reason[:240],
    }


def source_kind_from_url(url: str) -> str:
    lowered = url.lower()
    if "rss" in lowered or "feed" in lowered or lowered.endswith((".xml", ".atom")):
        return "rss_feed"
    if "doc" in lowered or "help" in lowered:
        return "docs"
    if "api" in lowered or "developer" in lowered:
        return "api_docs"
    if "release" in lowered:
        return "release_notes"
    if "changelog" in lowered:
        return "changelog"
    if "blog" in lowered:
        return "blog"
    if "security" in lowered or "advis" in lowered:
        return "security_advisories"
    if "pricing" in lowered:
        return "pricing"
    if "customer" in lowered or "case-stud" in lowered:
        return "customers"
    if "status" in lowered or "trust" in lowered:
        return "status"
    return "vendor_site"


def _dedupe_source_records(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for source in sources:
        url = source["url"]
        if url in seen:
            continue
        seen.add(url)
        deduped.append(source)
    return deduped


def _block_value(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _snippet(text: str, limit: int = 600) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


__all__ = [
    "Candidate",
    "DISCOVERY_TOPICS",
    "MUST_FOLLOW_SOURCE_TYPES",
    "candidate_from_url",
    "collect",
    "discover_must_follow_sources",
    "discover_sources",
    "extract_html",
    "fetch",
    "from_feed",
    "generate_report",
    "parse_date",
    "render_prompt",
    "search",
    "split_report",
    "source_kind_from_url",
]
