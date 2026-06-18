from datetime import date

from ci_engine import retrieve
from ci_engine.db import repository


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, stmt, params):
        self._calls.append((str(stmt), params))
        return _FakeResult(self._rows)


class _FakeEngine:
    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    def connect(self):
        return _FakeConnection(self._rows, self._calls)


def test_vector_search_filters_first_and_returns_citations(monkeypatch):
    calls = []
    rows = [
        {
            "source_id": 42,
            "chunk_text": "JFrog describes its platform in official material.",
            "url": "ci-report://official-deep-research/jfrog/report#company-profile",
            "source_kind": "official_llm_research_report",
            "raw_path": "raw_snapshots/jfrog/report.md",
            "publish_date": date(2026, 1, 2),
            "axis": "business",
            "dimension": "company_profile",
            "doc_type": "company_fact",
            "competitor": "JFrog",
            "similarity": 0.91,
        }
    ]

    monkeypatch.setattr(repository, "_vector_literal", lambda embedding: "[0.1,0.2]")
    monkeypatch.setattr(repository, "get_engine", lambda: _FakeEngine(rows, calls))
    monkeypatch.setattr(
        repository,
        "citations_for_sources",
        lambda source_ids: {
            42: [
                {
                    "url": "https://jfrog.com/platform",
                    "label": "JFrog platform",
                    "date_text": "2026-01-02",
                }
            ]
        },
    )

    results = repository.vector_search(
        [0.1, 0.2],
        top_k=1,
        similarity_threshold=0.5,
        axis="business",
        competitors=["JFrog"],
        dimensions=["company_profile"],
        doc_types=["company_fact"],
        source_kinds=["official_llm_research_report"],
        published_after=date(2026, 1, 1),
        published_before=date(2026, 1, 3),
    )

    sql, params = calls[0]
    assert "dimension_coverage_assertions" in sql
    assert "COALESCE(dca.dimension, s.dimension) AS dimension" in sql
    assert "(s.dimension = ANY(:dimensions) OR dca.dimension IS NOT NULL)" in sql
    assert "c.doc_type = ANY(:doc_types)" in sql
    assert "s.source_kind = ANY(:source_kinds)" in sql
    assert params["competitors"] == ["JFrog"]
    assert params["dimensions"] == ["company_profile"]
    assert params["doc_types"] == ["company_fact"]
    assert params["source_kinds"] == ["official_llm_research_report"]
    assert params["published_after"] == date(2026, 1, 1)
    assert params["published_before"] == date(2026, 1, 3)

    assert results == [
        {
            "source_id": 42,
            "chunk_text": "JFrog describes its platform in official material.",
            "url": "ci-report://official-deep-research/jfrog/report#company-profile",
            "source_kind": "official_llm_research_report",
            "raw_path": "raw_snapshots/jfrog/report.md",
            "publish_date": date(2026, 1, 2),
            "axis": "business",
            "dimension": "company_profile",
            "doc_type": "company_fact",
            "competitor": "JFrog",
            "similarity": 0.91,
            "citations": [
                {
                    "url": "https://jfrog.com/platform",
                    "label": "JFrog platform",
                    "date_text": "2026-01-02",
                }
            ],
        }
    ]


def test_vector_search_caps_results_after_threshold(monkeypatch):
    calls = []
    rows = [
        {
            "source_id": index,
            "chunk_text": f"chunk {index}",
            "url": f"https://example.com/{index}",
            "source_kind": "blog",
            "raw_path": None,
            "publish_date": date(2026, 1, index),
            "axis": "technical",
            "dimension": "sbom_generation",
            "doc_type": "blog",
            "competitor": "Snyk",
            "similarity": 0.9 - (index * 0.01),
        }
        for index in range(1, 5)
    ]

    monkeypatch.setattr(repository, "_vector_literal", lambda embedding: "[0.1,0.2]")
    monkeypatch.setattr(repository, "get_engine", lambda: _FakeEngine(rows, calls))
    monkeypatch.setattr(repository, "citations_for_sources", lambda source_ids: {})

    results = repository.vector_search(
        [0.1, 0.2],
        top_k=2,
        similarity_threshold=0.0,
        competitors=["Snyk"],
    )

    assert [result["source_id"] for result in results] == [1, 2]


def test_retrieve_embeds_query_filters_vector_search_and_reports_missing(monkeypatch):
    captured = {}
    vector_calls = []
    chunks = [
        {
            "source_id": 42,
            "chunk_text": "JFrog active evidence.",
            "url": "https://jfrog.com/platform",
            "publish_date": date(2026, 1, 2),
            "axis": "business",
            "dimension": "company_profile",
            "competitor": "JFrog",
            "citations": [{"url": "https://jfrog.com/platform"}],
        }
    ]

    def fake_embed_query(query):
        captured["query"] = query
        return [0.1, 0.2]

    def fake_vector_search(**kwargs):
        vector_calls.append(kwargs)
        if kwargs["competitors"] == ["JFrog"]:
            return chunks
        return []

    monkeypatch.setattr(retrieve.gemini, "embed_query", fake_embed_query)
    monkeypatch.setattr(retrieve.repository, "vector_search", fake_vector_search)
    monkeypatch.setattr(
        retrieve.repository,
        "coverage_status",
        lambda: [
            {
                "competitor": "JFrog",
                "axis": "business",
                "dimension": "company_profile",
                "active_sources": 1,
                "freshest_publish_date": date(2026, 1, 2),
            },
            {
                "competitor": "Snyk",
                "axis": "business",
                "dimension": "company_profile",
                "active_sources": 0,
                "freshest_publish_date": None,
            },
        ],
    )
    monkeypatch.setattr(
        retrieve.repository,
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
                "competitor": "Snyk",
                "axis": "business",
                "dimension": "company_profile",
                "state": "unknown",
                "confidence": 0.0,
                "conflict": False,
            },
        ],
    )

    result = retrieve.retrieve(
        " platform facts ",
        axis="business",
        competitors=["JFrog", "Snyk"],
        dimensions=["company_profile"],
    )

    assert captured["query"] == "platform facts"
    assert [call["query_embedding"] for call in vector_calls] == [[0.1, 0.2], [0.1, 0.2]]
    assert [call["top_k"] for call in vector_calls] == [4, 4]
    assert [call["axis"] for call in vector_calls] == ["business", "business"]
    assert [call["competitors"] for call in vector_calls] == [["JFrog"], ["Snyk"]]
    assert [call["dimensions"] for call in vector_calls] == [
        ["company_profile", "business_model", "security_model"],
        ["company_profile", "business_model", "security_model"],
    ]
    assert result["chunks"] == chunks
    assert result["missing"] == [
        {
            "competitor": "Snyk",
            "axis": "business",
            "dimension": "company_profile",
            "reason": "unknown_coverage",
            "coverage_state": "unknown",
            "coverage_confidence": 0.0,
            "coverage_conflict": False,
        }
    ]


def test_retrieve_expands_dimension_aliases_and_balances_competitors(monkeypatch):
    captured = []

    monkeypatch.setattr(retrieve.gemini, "embed_query", lambda query: [0.1, 0.2])

    def fake_vector_search(**kwargs):
        captured.append(kwargs)
        competitor = kwargs["competitors"][0]
        return [
            {
                "source_id": 1 if competitor == "GitLab" else 2,
                "chunk_text": f"{competitor} reachability evidence.",
                "url": f"https://example.com/{competitor}",
                "publish_date": date(2026, 1, 2),
                "axis": "technical",
                "dimension": "dependency_scanning",
                "competitor": competitor,
                "similarity": 0.9 if competitor == "GitLab" else 0.8,
                "citations": [],
            }
        ]

    monkeypatch.setattr(retrieve.repository, "vector_search", fake_vector_search)
    monkeypatch.setattr(retrieve.repository, "coverage_status", lambda: [])
    monkeypatch.setattr(retrieve.repository, "dimension_coverage_status", lambda **kwargs: [])
    monkeypatch.setattr(retrieve, "config_get", lambda path, default=None: 4)

    result = retrieve.retrieve(
        "reachability",
        axis="technical",
        competitors=["GitLab", "Snyk"],
        dimensions=["reachability_analysis"],
    )

    assert [call["competitors"] for call in captured] == [["GitLab"], ["Snyk"]]
    assert all(call["top_k"] == 2 for call in captured)
    assert all("dependency_scanning" in call["dimensions"] for call in captured)
    assert [chunk["competitor"] for chunk in result["chunks"]] == ["GitLab", "Snyk"]
    assert result["missing"] == []


def test_retrieve_does_not_report_missing_when_alias_chunk_covers_requested_dimension(
    monkeypatch,
):
    monkeypatch.setattr(retrieve.gemini, "embed_query", lambda query: [0.1, 0.2])
    monkeypatch.setattr(
        retrieve.repository,
        "vector_search",
        lambda **kwargs: [
            {
                "source_id": 42,
                "chunk_text": "Sonatype SBOM evidence.",
                "url": "https://help.sonatype.com/en/software-bill-of-materials-sbom.html",
                "publish_date": date(2026, 1, 2),
                "axis": "both",
                "dimension": "sbom_support",
                "competitor": "Sonatype",
                "similarity": 0.9,
                "citations": [],
            }
        ],
    )
    monkeypatch.setattr(
        retrieve.repository,
        "coverage_status",
        lambda: [
            {
                "competitor": "Sonatype",
                "axis": "technical",
                "dimension": "sbom_generation",
                "active_sources": 0,
                "freshest_publish_date": None,
            }
        ],
    )
    monkeypatch.setattr(
        retrieve.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "Sonatype",
                "axis": "technical",
                "dimension": "sbom_generation",
                "state": "unknown",
                "confidence": 0.0,
                "conflict": False,
            }
        ],
    )

    result = retrieve.retrieve(
        "sbom",
        axis="technical",
        competitors=["Sonatype"],
        dimensions=["sbom_generation"],
    )

    assert result["missing"] == []


def test_retrieve_omits_coverage_missing_when_dimensions_not_requested(monkeypatch):
    monkeypatch.setattr(retrieve.gemini, "embed_query", lambda query: [0.1, 0.2])
    monkeypatch.setattr(retrieve.repository, "vector_search", lambda **kwargs: [])
    monkeypatch.setattr(
        retrieve.repository,
        "coverage_status",
        lambda: (_ for _ in ()).throw(AssertionError("coverage should not run")),
    )

    result = retrieve.retrieve("anything", axis="business", competitors=["GitLab"])

    assert result == {"chunks": [], "missing": []}


def test_retrieve_reports_planned_partial_and_absent_coverage(monkeypatch):
    monkeypatch.setattr(retrieve.gemini, "embed_query", lambda query: [0.1, 0.2])
    monkeypatch.setattr(retrieve.repository, "vector_search", lambda **kwargs: [])
    monkeypatch.setattr(retrieve.repository, "coverage_status", lambda: [])
    monkeypatch.setattr(
        retrieve.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "package_firewall",
                "state": "planned",
                "confidence": 0.8,
                "conflict": False,
            },
            {
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "package_firewall",
                "state": "partial",
                "confidence": 0.7,
                "conflict": False,
            },
            {
                "competitor": "Sonatype",
                "axis": "technical",
                "dimension": "package_firewall",
                "state": "absent",
                "confidence": 0.85,
                "conflict": False,
            },
        ],
    )

    result = retrieve.retrieve(
        "package firewall",
        axis="technical",
        competitors=["GitLab", "Snyk", "Sonatype"],
        dimensions=["package_firewall"],
    )

    assert [row["reason"] for row in result["missing"]] == [
        "planned_only",
        "partial_coverage",
        "known_absent",
    ]


def test_retrieve_reports_no_matching_chunks_for_present_coverage(monkeypatch):
    monkeypatch.setattr(retrieve.gemini, "embed_query", lambda query: [0.1, 0.2])
    monkeypatch.setattr(retrieve.repository, "vector_search", lambda **kwargs: [])
    monkeypatch.setattr(
        retrieve.repository,
        "coverage_status",
        lambda: [
            {
                "competitor": "JFrog",
                "axis": "technical",
                "dimension": "reachability_analysis",
                "active_sources": 0,
                "freshest_publish_date": None,
            }
        ],
    )
    monkeypatch.setattr(
        retrieve.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "JFrog",
                "axis": "technical",
                "dimension": "reachability_analysis",
                "state": "present",
                "confidence": 0.9,
                "conflict": False,
            }
        ],
    )

    result = retrieve.retrieve(
        "reachability",
        axis="technical",
        competitors=["JFrog"],
        dimensions=["reachability_analysis"],
    )

    assert result["missing"] == [
        {
            "competitor": "JFrog",
            "axis": "technical",
            "dimension": "reachability_analysis",
            "reason": "no_matching_chunks",
            "coverage_state": "present",
            "coverage_confidence": 0.9,
            "coverage_conflict": False,
        }
    ]


def test_retrieve_reports_unknown_coverage_even_with_unknown_assertions(monkeypatch):
    monkeypatch.setattr(retrieve.gemini, "embed_query", lambda query: [0.1, 0.2])
    monkeypatch.setattr(retrieve.repository, "vector_search", lambda **kwargs: [])
    monkeypatch.setattr(
        retrieve.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "software_distribution",
                "state": "unknown",
                "confidence": 0.0,
                "active_assertions": 1,
                "conflict": False,
            }
        ],
    )

    result = retrieve.retrieve(
        "software distribution",
        axis="technical",
        competitors=["Snyk"],
        dimensions=["software_distribution"],
    )

    assert result["missing"] == [
        {
            "competitor": "Snyk",
            "axis": "technical",
            "dimension": "software_distribution",
            "reason": "unknown_coverage",
            "coverage_state": "unknown",
            "coverage_confidence": 0.0,
            "coverage_conflict": False,
        }
    ]


def test_source_text_returns_all_active_chunks_in_source_order(monkeypatch):
    calls = []
    rows = [
        {
            "chunk_id": 10,
            "source_id": 42,
            "chunk_text": "First product chunk.",
            "url": "ci-report://official-deep-research/jfrog/report#product",
            "source_kind": "official_llm_research_report",
            "raw_path": "raw_snapshots/jfrog/report.md",
            "publish_date": date(2026, 1, 2),
            "axis": "both",
            "dimension": "product_portfolio",
            "doc_type": "company_fact",
            "competitor": "JFrog",
        },
        {
            "chunk_id": 11,
            "source_id": 42,
            "chunk_text": "Second product chunk.",
            "url": "ci-report://official-deep-research/jfrog/report#product",
            "source_kind": "official_llm_research_report",
            "raw_path": "raw_snapshots/jfrog/report.md",
            "publish_date": date(2026, 1, 2),
            "axis": "both",
            "dimension": "product_portfolio",
            "doc_type": "company_fact",
            "competitor": "JFrog",
        },
    ]

    monkeypatch.setattr(repository, "get_engine", lambda: _FakeEngine(rows, calls))
    monkeypatch.setattr(
        repository,
        "citations_for_sources",
        lambda source_ids: {
            42: [{"url": "https://jfrog.com", "label": "JFrog", "date_text": None}]
        },
    )

    chunks = repository.source_chunks(42)
    text = repository.source_text(42)

    sql, params = calls[0]
    assert "ORDER BY c.id" in sql
    assert params == {"source_id": 42, "status": "active"}
    assert [chunk["chunk_id"] for chunk in chunks] == [10, 11]
    assert chunks[0]["citations"] == [
        {"url": "https://jfrog.com", "label": "JFrog", "date_text": None}
    ]
    assert text == "First product chunk.\n\nSecond product chunk."


def test_source_text_merges_exact_chunk_overlap():
    assert (
        repository._merge_chunk_texts(
            [
                "JFrog products include Artifactory and Xray.",
                "Xray. It also includes Curation.",
            ]
        )
        == "JFrog products include Artifactory and Xray. It also includes Curation."
    )
