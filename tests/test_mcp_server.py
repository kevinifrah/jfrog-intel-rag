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


def test_coverage_matrix_merges_source_and_rollup_status(monkeypatch):
    monkeypatch.setattr(
        server.repository,
        "coverage_status",
        lambda: [
            {
                "competitor": "Sonatype",
                "axis": "technical",
                "dimension": "sbom_generation",
                "active_sources": 2,
                "freshest_publish_date": date(2026, 2, 1),
            }
        ],
    )
    monkeypatch.setattr(
        server.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "Sonatype",
                "axis": "technical",
                "dimension": "sbom_generation",
                "state": "present",
                "confidence": 0.8,
                "strongest_source_id": 42,
                "conflict": False,
                "updated_at": datetime(2026, 2, 2, tzinfo=timezone.utc),
            }
        ],
    )

    result = server.coverage_matrix(
        competitors=["Sonatype"],
        dimensions=["sbom_generation"],
    )

    assert result["coverage"] == [
        {
            "competitor": "Sonatype",
            "axis": "technical",
            "dimension": "sbom_generation",
            "active_sources": 2,
            "freshest_publish_date": "2026-02-01",
            "coverage_state": "present",
            "coverage_confidence": 0.8,
            "coverage_conflict": False,
            "strongest_source_id": 42,
            "updated_at": "2026-02-02T00:00:00+00:00",
        }
    ]


def test_find_evidence_gaps_filters_missing(monkeypatch):
    monkeypatch.setattr(server.repository, "coverage_status", lambda: [])
    monkeypatch.setattr(
        server.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "Sonatype",
                "axis": "business",
                "dimension": "pricing_packaging",
                "state": "unknown",
                "confidence": 0.0,
                "conflict": False,
            }
        ],
    )

    result = server.find_evidence_gaps(
        competitors=["Sonatype"],
        axis="business",
        dimensions=["pricing_packaging"],
    )

    assert result["missing"][0]["reason"] == "unknown_coverage"
    assert result["missing"][0]["dimension"] == "pricing_packaging"


def test_get_source_detail_groups_active_chunks(monkeypatch):
    monkeypatch.setattr(
        server.repository,
        "active_chunks",
        lambda **kwargs: [
            {
                "chunk_id": 1,
                "source_id": 7,
                "chunk_text": "Source detail chunk.",
                "url": "https://example.com/source",
                "title": "Source",
                "publish_date": date(2026, 3, 1),
                "fetched_at": datetime(2026, 3, 2, tzinfo=timezone.utc),
                "axis": "technical",
                "dimension": "sbom_generation",
                "doc_type": "docs",
                "competitor": "Sonatype",
                "source_kind": "official",
                "raw_path": "raw/source.md",
                "citations": [{"url": "https://example.com/source"}],
            }
        ],
    )

    result = server.get_source_detail([7, 8])

    assert result["sources"][0]["source_id"] == 7
    assert result["sources"][0]["chunks"][0]["chunk_text"] == "Source detail chunk."
    assert result["missing"] == [{"source_id": 8, "reason": "no_active_source_or_chunks"}]


def test_source_inventory_delegates_to_repository_and_serializes(monkeypatch):
    captured = {}

    def fake_source_inventory(**kwargs):
        captured.update(kwargs)
        return [
            {
                "source_id": 9,
                "competitor": "Sonatype",
                "axis": "technical",
                "doc_type": "docs",
                "dimension": "sbom_generation",
                "url": "https://sonatype.com/sbom",
                "title": "SBOM",
                "publish_date": date(2026, 4, 1),
                "fetched_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                "source_kind": "docs",
                "raw_path": "raw/sonatype/sbom.md",
                "chunk_count": 3,
                "citation_count": 1,
            }
        ]

    monkeypatch.setattr(server.repository, "source_inventory", fake_source_inventory)

    result = server.source_inventory(
        competitors=["Sonatype"],
        dimensions=["sbom_generation"],
        limit=10,
    )

    assert captured == {
        "competitors": ["Sonatype"],
        "dimensions": ["sbom_generation"],
        "limit": 10,
    }
    assert result["sources"][0]["publish_date"] == "2026-04-01"
    assert result["sources"][0]["fetched_at"] == "2026-04-02T00:00:00+00:00"


def test_build_capability_evidence_matrix_batches_active_chunks(monkeypatch):
    captured = {}

    def fake_active_chunks(**kwargs):
        captured.update(kwargs)
        return [
            {
                "chunk_id": 1,
                "source_id": 11,
                "chunk_text": "JFrog Xray generates SBOM export with CycloneDX and SPDX evidence.",
                "url": "https://jfrog.com/help/sbom",
                "title": "JFrog Xray SBOM",
                "publish_date": date(2026, 2, 1),
                "fetched_at": datetime(2026, 2, 2, tzinfo=timezone.utc),
                "axis": "technical",
                "dimension": "sbom_generation",
                "doc_type": "docs",
                "competitor": "JFrog",
                "source_kind": "docs",
                "raw_path": "raw/jfrog/sbom.md",
                "similarity": 0.91,
                "citations": [{"url": "https://jfrog.com/help/sbom"}],
            },
            {
                "chunk_id": 2,
                "source_id": 12,
                "chunk_text": "Sonatype SBOM Manager supports SBOM management and export workflows.",
                "url": "https://sonatype.com/products/sbom-manager",
                "title": "Sonatype SBOM Manager",
                "publish_date": date(2026, 2, 3),
                "fetched_at": datetime(2026, 2, 4, tzinfo=timezone.utc),
                "axis": "technical",
                "dimension": "sbom_generation",
                "doc_type": "docs",
                "competitor": "Sonatype",
                "source_kind": "docs",
                "raw_path": "raw/sonatype/sbom.md",
                "similarity": 0.89,
                "citations": [{"url": "https://sonatype.com/products/sbom-manager"}],
            },
        ]

    monkeypatch.setattr(server.repository, "active_chunks", fake_active_chunks)

    result = server.build_capability_evidence_matrix(
        "Sonatype",
        max_chunks_per_company_capability=1,
    )

    assert captured["competitors"] == ["JFrog", "Sonatype"]
    assert captured["axis"] == "technical"
    assert "sbom_generation" in captured["dimensions"]
    assert len(result["items"]) == 2
    assert {item["metadata"]["capability_id"] for item in result["items"]} == {
        "sbom_generation"
    }
    assert len(result["attempts"]) == len(server.CAPABILITY_DEFINITIONS) * 2
    sbom = next(
        row
        for row in result["capability_matrix"]["rows"]
        if row["capability_id"] == "sbom_generation"
    )
    assert sbom["evidence_ids"]
    assert result["product_catalog"]
    assert "build_capability_evidence_matrix" in server.__all__


def test_build_report_section_evidence_batches_active_chunks(monkeypatch):
    captured = {}

    def fake_active_chunks(**kwargs):
        captured.update(kwargs)
        return [
            {
                "chunk_id": 21,
                "source_id": 31,
                "chunk_text": "JFrog company profile and customer evidence for an executive snapshot.",
                "url": "https://jfrog.com/company",
                "title": "JFrog Company",
                "publish_date": date(2026, 3, 1),
                "fetched_at": datetime(2026, 3, 2, tzinfo=timezone.utc),
                "axis": "business",
                "dimension": "company_profile",
                "doc_type": "docs",
                "competitor": "JFrog",
                "source_kind": "vendor_site",
                "raw_path": "raw/jfrog/company.md",
                "citations": [{"url": "https://jfrog.com/company"}],
            },
            {
                "chunk_id": 22,
                "source_id": 32,
                "chunk_text": "Sonatype company profile and customer evidence for an executive snapshot.",
                "url": "https://sonatype.com/company",
                "title": "Sonatype Company",
                "publish_date": date(2026, 3, 3),
                "fetched_at": datetime(2026, 3, 4, tzinfo=timezone.utc),
                "axis": "business",
                "dimension": "company_profile",
                "doc_type": "docs",
                "competitor": "Sonatype",
                "source_kind": "vendor_site",
                "raw_path": "raw/sonatype/company.md",
                "citations": [{"url": "https://sonatype.com/company"}],
            },
        ]

    monkeypatch.setattr(server.repository, "active_chunks", fake_active_chunks)

    result = server.build_report_section_evidence(
        "Sonatype",
        sections=["company_snapshot"],
        max_chunks_per_company_section=1,
    )

    assert captured["competitors"] == ["JFrog", "Sonatype"]
    assert "company_profile" in captured["dimensions"]
    assert len(result["items"]) == 2
    assert {item["report_section"] for item in result["items"]} == {"company_snapshot"}
    assert all(
        item["metadata"]["retrieval_mode"] == "mcp_batch_section"
        for item in result["items"]
    )
    assert {
        (row["company"], row["section_id"], row["result_count"])
        for row in result["coverage"]
    } == {
        ("JFrog", "company_snapshot", 1),
        ("Sonatype", "company_snapshot", 1),
    }
    assert result["gaps"] == []
    assert "build_report_section_evidence" in server.__all__


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
