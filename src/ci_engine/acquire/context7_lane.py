from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from ci_engine.acquire.snapshots import write_snapshot
from ci_engine.secrets import get_secret

logger = logging.getLogger(__name__)

CONTEXT7_MCP_URL = "https://mcp.context7.com/mcp"
RESOLVE_TOOL_NAME = "resolve-library-id"
DOCS_TOOL_NAME = "query-docs"
LEGACY_DOCS_TOOL_NAME = "get-library-docs"

TECHNICAL_TOPICS = [
    "container scanning",
    "SBOM",
    "policy",
]

Candidate = dict[str, Any]


class Context7Error(RuntimeError):
    pass


@dataclass
class Context7Client:
    url: str = CONTEXT7_MCP_URL
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self._session_id: str | None = None
        self._initialized = False
        self._next_id = 1

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        return self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "ci-engine", "version": "0.1.0"},
            },
            initialize=True,
        )
        try:
            self._post(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )
        except httpx.HTTPError as exc:
            logger.debug("Context7 initialized notification failed: %s", exc)
        self._initialized = True

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        initialize: bool = False,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        message = self._post(payload)
        if "error" in message:
            raise Context7Error(str(message["error"]))
        result = message.get("result")
        if not isinstance(result, dict):
            if initialize and result is None:
                return {}
            raise Context7Error(f"Context7 returned invalid result for {method}")
        return result

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "CONTEXT7_API_KEY": get_secret("context7-key"),
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.url, headers=headers, json=payload)
            response.raise_for_status()

        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        return _decode_mcp_response(response)


@lru_cache(maxsize=1)
def _client() -> Context7Client:
    return Context7Client()


def search(
    competitor: str,
    topics: list[str] | None = None,
    *,
    product_name: str | None = None,
) -> list[Candidate]:
    topics = topics or TECHNICAL_TOPICS
    library_name = product_name or competitor
    library_id = resolve_library_id(
        library_name,
        query=f"{library_name} product SDK docs {' '.join(topics)}",
    )

    candidates: list[Candidate] = []
    for topic in topics:
        docs = query_docs(library_id, topic)
        title = f"{competitor} docs: {topic}"
        raw_path = write_snapshot(
            competitor=competitor,
            title=title,
            url=_context7_url(library_id),
            content=docs,
            content_type="text/markdown",
        )
        candidates.append(
            {
                "title": title,
                "url": _context7_url(library_id),
                "snippet": _snippet(docs),
                "text": docs,
                "competitor": competitor,
                "published": None,
                "raw_path": str(raw_path),
                "source_kind": "docs",
                "source_reason": "context7 product documentation",
            }
        )

    return candidates


def resolve_library_id(library_name: str, *, query: str | None = None) -> str:
    result = _client().call_tool(
        RESOLVE_TOOL_NAME,
        {
            "libraryName": library_name,
            "query": query or library_name,
        },
    )
    return _extract_library_id(result)


def query_docs(library_id: str, topic: str) -> str:
    try:
        result = _client().call_tool(
            DOCS_TOOL_NAME,
            {
                "libraryId": library_id,
                "query": topic,
            },
        )
    except Context7Error:
        result = _client().call_tool(
            LEGACY_DOCS_TOOL_NAME,
            {
                "context7CompatibleLibraryID": library_id,
                "topic": topic,
                "tokens": 6000,
            },
        )
    return _result_text(result)


def _decode_mcp_response(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}

    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        return response.json()

    messages = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        messages.append(json.loads(data))

    return messages[-1] if messages else {}


def _extract_library_id(result: dict[str, Any]) -> str:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        for key in ("libraryId", "id"):
            value = structured.get(key)
            if isinstance(value, str) and value.startswith("/"):
                return value
        libraries = structured.get("libraries")
        if isinstance(libraries, list):
            for library in libraries:
                if isinstance(library, dict):
                    value = library.get("libraryId") or library.get("id")
                    if isinstance(value, str) and value.startswith("/"):
                        return value

    text = _result_text(result)
    patterns = [
        r"Context7-compatible library ID:\s*`?([/a-zA-Z0-9_.-]+)`?",
        r"`([/a-zA-Z0-9_.-]+/[/a-zA-Z0-9_.-]+)`",
        r"(?m)^([/a-zA-Z0-9_.-]+/[/a-zA-Z0-9_.-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    raise Context7Error("Could not resolve a Context7 library ID")


def _result_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in result.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))

    text = "\n".join(part for part in parts if part).strip()
    if not text:
        raise Context7Error("Context7 returned no text")
    return text


def _context7_url(library_id: str) -> str:
    return f"https://context7.com{library_id}"


def _snippet(text: str, limit: int = 600) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


__all__ = [
    "CONTEXT7_MCP_URL",
    "DOCS_TOOL_NAME",
    "RESOLVE_TOOL_NAME",
    "TECHNICAL_TOPICS",
    "Context7Client",
    "Context7Error",
    "query_docs",
    "resolve_library_id",
    "search",
]
