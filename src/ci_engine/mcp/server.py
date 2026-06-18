from __future__ import annotations

import hmac
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ci_engine import retrieve as retriever
from ci_engine.config import get as config_get
from ci_engine.db import repository
from ci_engine.dimension_coverage import missing_reason_for_state

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8080))
STREAMABLE_HTTP_PATH = "/mcp"

_LOCAL_ALLOWED_HOSTS = [
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
    "[::1]",
    "[::1]:*",
]
_LOCAL_ALLOWED_ORIGINS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
    "http://[::1]:*",
]


class SharedTokenMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        expected = _shared_token()
        if expected and not _authorization_matches(_authorization_header(scope), expected):
            response = PlainTextResponse("unauthorized", status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _shared_token() -> str | None:
    token = os.environ.get("MCP_SHARED_TOKEN")
    if token is None:
        return None
    token = token.strip()
    return token or None


def _authorization_header(scope: Scope) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == b"authorization":
            return value.decode("latin1").strip()
    return ""


def _authorization_matches(header: str, expected: str) -> bool:
    return hmac.compare_digest(header, expected) or hmac.compare_digest(
        header,
        f"Bearer {expected}",
    )


def _csv_env(name: str, default: Sequence[str]) -> list[str]:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return list(default)
    return [part.strip() for part in value.split(",") if part.strip()]


def _allowed_hosts() -> list[str]:
    return _csv_env("MCP_ALLOWED_HOSTS", _LOCAL_ALLOWED_HOSTS)


def _allowed_origins() -> list[str]:
    return _csv_env("MCP_ALLOWED_ORIGINS", _LOCAL_ALLOWED_ORIGINS)


def _transport_security_settings() -> TransportSecuritySettings:
    # Host and Origin validation protects browser-capable MCP clients from
    # DNS-rebinding requests against this local/deployed HTTP server.
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(),
        allowed_origins=_allowed_origins(),
    )


mcp = FastMCP(
    "ci-engine-retrieval",
    host=HOST,
    port=PORT,
    streamable_http_path=STREAMABLE_HTTP_PATH,
    json_response=True,
    stateless_http=True,
    transport_security=_transport_security_settings(),
)


@mcp.tool()
def search(
    query: str,
    axis: str | None = None,
    competitors: list[str] | None = None,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Search active cited competitor knowledge."""
    return _jsonable(
        retriever.retrieve(
            query=query,
            axis=_clean_text(axis),
            competitors=_clean_list(competitors),
            dimensions=_clean_list(dimensions),
        )
    )


@mcp.tool()
def get_competitor(name: str, axis: str | None = None) -> dict[str, Any]:
    """Get active cited knowledge for one competitor."""
    competitor = _required_text(name, "name")
    axis_filter = _clean_text(axis)
    chunks = repository.active_chunks(
        competitors=[competitor],
        axis=axis_filter,
    )
    return _jsonable(
        {
            "competitor": competitor,
            "axis": axis_filter,
            "dimensions": _group_chunks_by_dimension(chunks),
            "missing": _missing_coverage(
                competitors=[competitor],
                axis=axis_filter,
            ),
        }
    )


@mcp.tool()
def compare_competitors(
    names: list[str],
    dimension: str | None = None,
) -> dict[str, Any]:
    """Compare competitors using active cited chunks and graph-linked sources."""
    competitors = _clean_list(names) or []
    dimension_filter = _clean_text(dimension)
    dimensions = [dimension_filter] if dimension_filter else None
    if not competitors:
        return {"competitors": [], "missing": [{"reason": "empty_names"}]}

    results: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = _missing_coverage(
        competitors=competitors,
        dimensions=dimensions,
    )
    max_graph_hops = int(config_get("retrieval.max_graph_hops", 2))

    for competitor in competitors:
        direct_chunks = repository.active_chunks(
            competitors=[competitor],
            dimensions=dimensions,
        )
        source_ids = repository.graph_related_source_ids(
            [competitor],
            max_hops=max_graph_hops,
        )
        graph_chunks = (
            repository.active_chunks(
                competitors=[competitor],
                dimensions=dimensions,
                source_ids=source_ids,
            )
            if source_ids
            else []
        )
        chunks = _dedupe_chunks([*direct_chunks, *graph_chunks])
        if not chunks:
            missing.append(
                {
                    "competitor": competitor,
                    "axis": None,
                    "dimension": dimension_filter,
                    "reason": "no_matching_chunks",
                }
            )
        results.append(
            {
                "competitor": competitor,
                "dimensions": _group_chunks_by_dimension(chunks),
            }
        )

    return _jsonable(
        {
            "competitors": results,
            "missing": _dedupe_missing(missing),
        }
    )


@mcp.tool()
def latest_updates(competitor: str | None = None, days: int = 7) -> dict[str, Any]:
    """Return active sources fetched in the recent window."""
    competitor_filter = _clean_text(competitor)
    window_days = max(int(days), 0)
    fetched_since = datetime.now(timezone.utc) - timedelta(days=window_days)
    sources = repository.latest_active_sources(
        competitor=competitor_filter,
        fetched_since=fetched_since,
    )
    chunks_by_source = _chunks_by_source_id(
        repository.active_chunks(
            source_ids=[source["source_id"] for source in sources],
        )
    )
    return _jsonable(
        {
            "competitor": competitor_filter,
            "days": window_days,
            "sources": [
                {
                    **_format_source(source),
                    "chunks": chunks_by_source.get(source["source_id"], []),
                }
                for source in sources
            ],
        }
    )


@mcp.tool()
def coverage_status() -> dict[str, Any]:
    """Return active source coverage by competitor and dimension."""
    rows = repository.coverage_status()
    return _jsonable(
        {
            "coverage": rows,
            "missing": _missing_coverage(),
        }
    )


def create_app() -> SharedTokenMiddleware:
    return SharedTokenMiddleware(mcp.streamable_http_app())


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host=HOST,
        port=PORT,
        log_level="info",
    )


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _required_text(value: str, field: str) -> str:
    cleaned = _clean_text(value)
    if cleaned is None:
        raise ValueError(f"{field} is required")
    return cleaned


def _clean_list(values: Sequence[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [str(value).strip() for value in values if str(value or "").strip()]


def _missing_coverage(
    *,
    competitors: Sequence[str] | None = None,
    axis: str | None = None,
    dimensions: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    active_coverage = _active_coverage_index()
    for row in repository.dimension_coverage_status(
        competitors=competitors,
        axis=axis,
        dimensions=dimensions,
    ):
        key = (
            str(row.get("competitor")),
            str(row.get("axis")),
            str(row.get("dimension")),
        )
        state = str(row.get("state") or "unknown")
        active_sources = active_coverage.get(key, 0)
        if state == "present":
            continue
        reason = missing_reason_for_state(state)
        missing.append(
            {
                "competitor": row.get("competitor"),
                "axis": row.get("axis"),
                "dimension": row.get("dimension"),
                "active_sources": active_sources,
                "reason": reason,
                "coverage_state": state,
                "coverage_confidence": row.get("confidence"),
                "coverage_conflict": row.get("conflict"),
            }
        )
    return missing


def _active_coverage_index() -> dict[tuple[str, str, str], int]:
    index: dict[tuple[str, str, str], int] = {}
    for row in repository.coverage_status():
        key = (
            str(row.get("competitor")),
            str(row.get("axis")),
            str(row.get("dimension")),
        )
        index[key] = int(row.get("active_sources") or 0)
    return index


def _group_chunks_by_dimension(chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for chunk in sorted(chunks, key=_chunk_sort_key, reverse=True):
        dimension = chunk.get("dimension")
        key = str(dimension) if dimension is not None else ""
        group = groups.setdefault(
            key,
            {
                "dimension": dimension,
                "chunks": [],
            },
        )
        group["chunks"].append(_format_chunk(chunk))
    return list(groups.values())


def _chunk_sort_key(chunk: Mapping[str, Any]) -> tuple[str, int]:
    chunk_id = int(chunk.get("chunk_id") or 0)
    return (_iso_text(chunk.get("publish_date")), chunk_id)


def _dedupe_chunks(chunks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    deduped: list[Mapping[str, Any]] = []
    for chunk in chunks:
        key = (
            chunk.get("chunk_id"),
            chunk.get("source_id"),
            chunk.get("chunk_text"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def _dedupe_missing(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any, Any]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (
            row.get("competitor"),
            row.get("axis"),
            row.get("dimension"),
            row.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(row))
    return deduped


def _chunks_by_source_id(
    chunks: Sequence[Mapping[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for chunk in chunks:
        source_id = int(chunk["source_id"])
        grouped.setdefault(source_id, []).append(_format_chunk(chunk))
    return grouped


def _format_source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _jsonable(source.get(key))
        for key in (
            "source_id",
            "competitor",
            "axis",
            "doc_type",
            "dimension",
            "url",
            "title",
            "publish_date",
            "fetched_at",
            "source_kind",
            "raw_path",
            "citations",
        )
        if key in source
    }


def _format_chunk(chunk: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _jsonable(chunk.get(key))
        for key in (
            "chunk_id",
            "source_id",
            "chunk_text",
            "url",
            "title",
            "publish_date",
            "fetched_at",
            "axis",
            "dimension",
            "doc_type",
            "competitor",
            "source_kind",
            "raw_path",
            "similarity",
            "citations",
        )
        if key in chunk
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _iso_text(value: Any) -> str:
    converted = _jsonable(value)
    if converted is None:
        return ""
    return str(converted)


__all__ = [
    "SharedTokenMiddleware",
    "compare_competitors",
    "coverage_status",
    "create_app",
    "get_competitor",
    "latest_updates",
    "main",
    "mcp",
    "search",
]


if __name__ == "__main__":
    main()
