from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

SNAPSHOT_ROOT = Path("raw_snapshots")


def slugify(value: Any, fallback: str = "snapshot") -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").lower()).strip("-")
    return text[:72] or fallback


def snapshot_path(
    *,
    competitor: str,
    title: str | None,
    url: str | None,
    fetched_on: date | None = None,
) -> Path:
    fetched_on = fetched_on or date.today()
    source = title or url or "snapshot"
    digest = hashlib.sha1(str(url or source).encode("utf-8")).hexdigest()[:8]
    slug = f"{slugify(source)}-{digest}"
    return SNAPSHOT_ROOT / slugify(competitor, "_unknown") / f"{fetched_on}-{slug}.md"


def write_snapshot(
    *,
    competitor: str,
    title: str | None,
    url: str | None,
    content: str,
    content_type: str = "text/html",
    published: date | None = None,
) -> Path:
    path = snapshot_path(competitor=competitor, title=title, url=url)
    path.parent.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()
    body = (
        "---\n"
        f"title: {title or ''}\n"
        f"url: {url or ''}\n"
        f"competitor: {competitor}\n"
        f"fetched_at: {fetched_at}\n"
        f"published: {published.isoformat() if published else ''}\n"
        f"content_type: {content_type}\n"
        "---\n\n"
        f"~~~{_fence_language(content_type)}\n"
        f"{content}\n"
        "~~~\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def fetch_html(
    url: str,
    *,
    competitor: str,
    title: str | None = None,
    published: date | None = None,
    timeout: float = 8.0,
) -> str:
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()
    html = response.text
    write_snapshot(
        competitor=competitor,
        title=title,
        url=str(response.url),
        content=html,
        content_type=response.headers.get("content-type", "text/html"),
        published=published,
    )
    return html


def _fence_language(content_type: str) -> str:
    normalized = content_type.lower()
    if "html" in normalized:
        return "html"
    if "xml" in normalized or "rss" in normalized or "atom" in normalized:
        return "xml"
    if "json" in normalized:
        return "json"
    return "text"
