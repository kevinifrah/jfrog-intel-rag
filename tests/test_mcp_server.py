import asyncio
from datetime import date, datetime, timezone

from starlette.responses import PlainTextResponse

from ci_engine.mcp import server


async def _ok_app(scope, receive, send):
    response = PlainTextResponse("ok")
    await response(scope, receive, send)


def _request(app, headers=None):
    async def run():
        messages = []
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/mcp",
            "headers": [
                (key.lower().encode("latin1"), value.encode("latin1"))
                for key, value in (headers or {}).items()
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        status = next(
            message["status"]
            for message in messages
            if message["type"] == "http.response.start"
        )
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return status, body

    return asyncio.run(run())


def test_search_delegates_to_retriever_and_serializes_dates(monkeypatch):
    captured = {}

    def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return {
            "chunks": [
                {
                    "chunk_text": "Raw sourced evidence.",
                    "url": "https://example.com/evidence",
                    "publish_date": date(2026, 1, 2),
                    "citations": [{"url": "https://example.com/evidence"}],
                }
            ],
            "missing": [],
        }

    monkeypatch.setattr(server.retriever, "retrieve", fake_retrieve)

    result = server.search(
        " evidence ",
        axis="business",
        competitors=["JFrog"],
    )

    assert captured == {
        "query": " evidence ",
        "axis": "business",
        "competitors": ["JFrog"],
        "dimensions": None,
    }
    assert result["chunks"] == [
        {
            "chunk_text": "Raw sourced evidence.",
            "url": "https://example.com/evidence",
            "publish_date": "2026-01-02",
            "citations": [{"url": "https://example.com/evidence"}],
        }
    ]
    assert result["missing"] == []


def test_search_passes_dimensions(monkeypatch):
    captured = {}

    def fake_retrieve(**kwargs):
        captured.update(kwargs)
        return {"chunks": [], "missing": []}

    monkeypatch.setattr(server.retriever, "retrieve", fake_retrieve)

    server.search(
        "sbom",
        axis="technical",
        competitors=["Snyk"],
        dimensions=["sbom_generation"],
    )

    assert captured == {
        "query": "sbom",
        "axis": "technical",
        "competitors": ["Snyk"],
        "dimensions": ["sbom_generation"],
    }


def test_get_competitor_groups_chunks_and_missing_dimensions(monkeypatch):
    monkeypatch.setattr(
        server.repository,
        "active_chunks",
        lambda **kwargs: [
            {
                "chunk_id": 10,
                "source_id": 20,
                "chunk_text": "JFrog platform chunk.",
                "url": "https://jfrog.com/platform",
                "publish_date": date(2026, 2, 3),
                "axis": "business",
                "dimension": "company_profile",
                "competitor": "JFrog",
                "citations": [{"url": "https://jfrog.com/platform"}],
            }
        ],
    )
    monkeypatch.setattr(
        server.repository,
        "coverage_status",
        lambda: [
            {
                "competitor": "JFrog",
                "axis": "business",
                "dimension": "company_profile",
                "active_sources": 1,
                "freshest_publish_date": date(2026, 2, 3),
            },
            {
                "competitor": "JFrog",
                "axis": "business",
                "dimension": "pricing_packaging",
                "active_sources": 0,
                "freshest_publish_date": None,
            },
        ],
    )
    monkeypatch.setattr(
        server.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "JFrog",
                "axis": "business",
                "dimension": "company_profile",
                "state": "present",
                "confidence": 0.9,
                "conflict": False,
            },
            {
                "competitor": "JFrog",
                "axis": "business",
                "dimension": "pricing_packaging",
                "state": "unknown",
                "confidence": 0.0,
                "conflict": False,
            },
        ],
    )

    result = server.get_competitor("JFrog", axis="business")

    assert result["dimensions"] == [
        {
            "dimension": "company_profile",
            "chunks": [
                {
                    "chunk_id": 10,
                    "source_id": 20,
                    "chunk_text": "JFrog platform chunk.",
                    "url": "https://jfrog.com/platform",
                    "publish_date": "2026-02-03",
                    "axis": "business",
                    "dimension": "company_profile",
                    "competitor": "JFrog",
                    "citations": [{"url": "https://jfrog.com/platform"}],
                }
            ],
        }
    ]
    assert result["missing"] == [
        {
            "competitor": "JFrog",
            "axis": "business",
            "dimension": "pricing_packaging",
            "active_sources": 0,
            "reason": "unknown_coverage",
            "coverage_state": "unknown",
            "coverage_confidence": 0.0,
            "coverage_conflict": False,
        }
    ]


def test_compare_competitors_combines_direct_and_graph_chunks(monkeypatch):
    graph_calls = []

    def fake_active_chunks(**kwargs):
        if kwargs.get("source_ids") == [99]:
            return [
                {
                    "chunk_id": 2,
                    "source_id": 99,
                    "chunk_text": "Graph-linked SBOM evidence.",
                    "url": "https://snyk.io/sbom-graph",
                    "publish_date": date(2026, 1, 4),
                    "dimension": "sbom_generation",
                    "competitor": "Snyk",
                    "citations": [{"url": "https://snyk.io/sbom-graph"}],
                }
            ]
        return [
            {
                "chunk_id": 1,
                "source_id": 50,
                "chunk_text": "Direct SBOM evidence.",
                "url": "https://snyk.io/sbom",
                "publish_date": date(2026, 1, 3),
                "dimension": "sbom_generation",
                "competitor": "Snyk",
                "citations": [{"url": "https://snyk.io/sbom"}],
            }
        ]

    def fake_graph_related_source_ids(names, max_hops=None):
        graph_calls.append((names, max_hops))
        return [99]

    monkeypatch.setattr(server.repository, "active_chunks", fake_active_chunks)
    monkeypatch.setattr(
        server.repository,
        "graph_related_source_ids",
        fake_graph_related_source_ids,
    )
    monkeypatch.setattr(server.repository, "coverage_status", lambda: [])
    monkeypatch.setattr(server.repository, "dimension_coverage_status", lambda **kwargs: [])
    monkeypatch.setattr(
        server,
        "config_get",
        lambda path, default=None: 1 if path == "retrieval.max_graph_hops" else default,
    )

    result = server.compare_competitors(["Snyk"], dimension="sbom_generation")

    chunks = result["competitors"][0]["dimensions"][0]["chunks"]
    assert graph_calls == [(["Snyk"], 1)]
    assert {chunk["chunk_text"] for chunk in chunks} == {
        "Direct SBOM evidence.",
        "Graph-linked SBOM evidence.",
    }
    assert result["missing"] == []


def test_latest_updates_serializes_sources_and_attaches_chunks(monkeypatch):
    captured = {}
    fetched_at = datetime(2026, 2, 4, 10, 30, tzinfo=timezone.utc)

    def fake_latest_active_sources(**kwargs):
        captured.update(kwargs)
        return [
            {
                "source_id": 7,
                "competitor": "Snyk",
                "axis": "business",
                "doc_type": "news",
                "dimension": "market_positioning",
                "url": "https://snyk.io/news",
                "title": "News",
                "publish_date": date(2026, 2, 4),
                "fetched_at": fetched_at,
                "source_kind": "news",
                "raw_path": "raw/snyk/news.html",
                "citations": [{"url": "https://snyk.io/news"}],
            }
        ]

    def fake_active_chunks(**kwargs):
        captured["source_ids"] = kwargs.get("source_ids")
        return [
            {
                "chunk_id": 70,
                "source_id": 7,
                "chunk_text": "Recent sourced update.",
                "url": "https://snyk.io/news",
                "publish_date": date(2026, 2, 4),
                "dimension": "market_positioning",
                "competitor": "Snyk",
                "citations": [{"url": "https://snyk.io/news"}],
            }
        ]

    monkeypatch.setattr(
        server.repository,
        "latest_active_sources",
        fake_latest_active_sources,
    )
    monkeypatch.setattr(server.repository, "active_chunks", fake_active_chunks)

    result = server.latest_updates(competitor="Snyk", days=3)

    assert captured["competitor"] == "Snyk"
    assert captured["fetched_since"].tzinfo is not None
    assert captured["source_ids"] == [7]
    assert result["sources"][0]["fetched_at"] == "2026-02-04T10:30:00+00:00"
    assert result["sources"][0]["chunks"][0]["chunk_text"] == "Recent sourced update."


def test_coverage_status_returns_gap_map(monkeypatch):
    monkeypatch.setattr(
        server.repository,
        "coverage_status",
        lambda: [
            {
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "sbom_generation",
                "active_sources": 0,
                "freshest_publish_date": None,
            }
        ],
    )
    monkeypatch.setattr(
        server.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "sbom_generation",
                "state": "unknown",
                "confidence": 0.0,
                "conflict": False,
            }
        ],
    )

    assert server.coverage_status() == {
        "coverage": [
            {
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "sbom_generation",
                "active_sources": 0,
                "freshest_publish_date": None,
            }
        ],
        "missing": [
            {
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "sbom_generation",
                "active_sources": 0,
                "reason": "unknown_coverage",
                "coverage_state": "unknown",
                "coverage_confidence": 0.0,
                "coverage_conflict": False,
            }
        ],
    }


def test_shared_token_middleware_skips_auth_when_unset(monkeypatch):
    monkeypatch.delenv("MCP_SHARED_TOKEN", raising=False)

    status, body = _request(server.SharedTokenMiddleware(_ok_app))

    assert status == 200
    assert body == b"ok"


def test_shared_token_middleware_accepts_raw_and_bearer_tokens(monkeypatch):
    monkeypatch.setenv("MCP_SHARED_TOKEN", "secret")

    raw_status, _raw_body = _request(
        server.SharedTokenMiddleware(_ok_app),
        headers={"Authorization": "secret"},
    )
    bearer_status, _bearer_body = _request(
        server.SharedTokenMiddleware(_ok_app),
        headers={"Authorization": "Bearer secret"},
    )

    assert raw_status == 200
    assert bearer_status == 200


def test_shared_token_middleware_rejects_invalid_token(monkeypatch):
    monkeypatch.setenv("MCP_SHARED_TOKEN", "secret")

    status, body = _request(
        server.SharedTokenMiddleware(_ok_app),
        headers={"Authorization": "wrong"},
    )

    assert status == 401
    assert body == b"unauthorized"


def test_origin_and_host_allowlists_default_to_localhost(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

    assert "localhost:*" in server._allowed_hosts()
    assert "127.0.0.1:*" in server._allowed_hosts()
    assert "http://localhost:*" in server._allowed_origins()
    assert "http://127.0.0.1:*" in server._allowed_origins()


def test_origin_and_host_allowlists_read_env(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "ui.example.com, openclaw.example.com")
    monkeypatch.setenv(
        "MCP_ALLOWED_ORIGINS",
        "https://ui.example.com, https://openclaw.example.com",
    )

    assert server._allowed_hosts() == ["ui.example.com", "openclaw.example.com"]
    assert server._allowed_origins() == [
        "https://ui.example.com",
        "https://openclaw.example.com",
    ]
