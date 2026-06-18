from datetime import date

import pytest

from ci_engine.synthesize import pipeline


@pytest.fixture(autouse=True)
def _coverage_repo_stubs(monkeypatch):
    monkeypatch.setattr(
        pipeline.repository,
        "insert_dimension_coverage_assertions",
        lambda source_id, assertions, **kwargs: len(assertions),
    )
    monkeypatch.setattr(
        pipeline.repository,
        "refresh_dimension_coverage_status",
        lambda *args, **kwargs: {},
    )


def test_ingest_candidate_returns_report_with_mocked_synthesis_and_embed(monkeypatch):
    calls = {
        "relevance": None,
        "source": None,
        "chunks": None,
        "entities": [],
        "edges": [],
        "supersede": None,
    }

    def fake_score(candidate):
        calls["relevance"] = candidate
        return {
            "relevant": True,
            "score": 0.91,
            "axis": "technical",
            "doc_type": "docs",
            "dimension": "sbom_generation",
            "reason": "specific docs",
        }

    monkeypatch.setattr(
        pipeline.relevance,
        "score",
        fake_score,
    )
    monkeypatch.setattr(
        pipeline.compiler,
        "synthesize",
        lambda text, meta: {
            "compiled": "Snyk can export CycloneDX SBOMs from CI pipelines.",
            "facts": [
                {
                    "dimension": "sbom_generation",
                    "claim": "Snyk exports CycloneDX SBOMs.",
                    "confidence": 0.9,
                }
            ],
            "entities": [
                {"name": "Snyk", "entity_type": "competitor", "competitor": "Snyk"},
                {"name": "CycloneDX SBOM", "entity_type": "feature", "competitor": "Snyk"},
            ],
            "relationships": [
                {
                    "src": "Snyk",
                    "relation": "has_feature",
                    "dst": "CycloneDX SBOM",
                }
            ],
            "conflicts": [],
            "axis": "technical",
        },
    )
    monkeypatch.setattr(
        pipeline.gemini,
        "embed_documents",
        lambda chunks: [[0.1, 0.2, 0.3] for _ in chunks],
    )
    monkeypatch.setattr(pipeline.repository, "source_exists", lambda url, content_hash: False)

    def fake_upsert_source(**kwargs):
        calls["source"] = kwargs
        return 123

    def fake_insert_chunks(**kwargs):
        calls["chunks"] = kwargs

    def fake_upsert_entity(name, entity_type, competitor):
        entity_id = len(calls["entities"]) + 1
        calls["entities"].append((entity_id, name, entity_type, competitor))
        return entity_id

    def fake_add_relationship(src_id, dst_id, relation, source_id):
        calls["edges"].append((src_id, dst_id, relation, source_id))
        return 77

    def fake_supersede_older(competitor, dimension, publish_date):
        calls["supersede"] = (competitor, dimension, publish_date)
        return 0

    monkeypatch.setattr(pipeline.repository, "upsert_source", fake_upsert_source)
    monkeypatch.setattr(pipeline.repository, "insert_chunks", fake_insert_chunks)
    monkeypatch.setattr(pipeline.repository, "upsert_entity", fake_upsert_entity)
    monkeypatch.setattr(pipeline.repository, "add_relationship", fake_add_relationship)
    monkeypatch.setattr(pipeline.repository, "supersede_older", fake_supersede_older)

    report = pipeline.ingest_candidate(
        {
            "title": "Snyk SBOM docs",
            "url": "https://docs.snyk.io/sbom",
            "snippet": "Export SBOMs.",
            "competitor": "Snyk",
            "published": date(2026, 1, 2),
            "text": "Snyk can export CycloneDX SBOMs from CI pipelines.",
            "source_kind": "docs",
        }
    )

    assert report == {
        "source_id": 123,
        "n_chunks": 1,
        "n_entities": 2,
        "n_edges": 1,
        "superseded": 0,
        "conflicts": [],
    }
    assert calls["source"]["axis"] == "technical"
    assert calls["source"]["doc_type"] == "docs"
    assert calls["source"]["dimension"] == "sbom_generation"
    assert calls["source"]["publish_date"] == date(2026, 1, 2)
    assert calls["source"]["source_kind"] == "docs"
    assert len(calls["source"]["content_hash"]) == 64
    assert (
        calls["relevance"]["content_excerpt"]
        == "Snyk can export CycloneDX SBOMs from CI pipelines."
    )
    assert calls["chunks"]["source_id"] == 123
    assert len(calls["chunks"]["chunks"]) == 1
    assert calls["edges"] == [(1, 2, "has_feature", 123)]
    assert calls["supersede"] == ("Snyk", "sbom_generation", date(2026, 1, 2))


def test_candidate_dimension_overrides_llm_dimension_drift(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        pipeline.relevance,
        "score",
        lambda candidate: {
            "relevant": True,
            "score": 0.91,
            "axis": "technical",
            "doc_type": "blog",
            "dimension": "vulnerability_management",
        },
    )
    monkeypatch.setattr(
        pipeline.compiler,
        "synthesize",
        lambda text, meta: {
            "compiled": "GitLab Duo can remediate vulnerabilities with code fixes.",
            "facts": [
                {
                    "dimension": "vulnerability_management",
                    "claim": "GitLab Duo can remediate vulnerabilities.",
                    "confidence": 0.9,
                }
            ],
            "entities": [],
            "relationships": [],
            "conflicts": [],
            "axis": "technical",
            "dimension": "vulnerability_management",
        },
    )
    monkeypatch.setattr(pipeline.gemini, "embed_documents", lambda chunks: [[0.1]])
    monkeypatch.setattr(pipeline.repository, "source_exists", lambda url, content_hash: False)
    def fake_upsert_source(**kwargs):
        calls["source"] = kwargs
        return 321

    monkeypatch.setattr(pipeline.repository, "upsert_source", fake_upsert_source)
    monkeypatch.setattr(pipeline.repository, "insert_chunks", lambda **kwargs: None)
    monkeypatch.setattr(pipeline.repository, "supersede_older", lambda *args: 0)

    pipeline.ingest_candidate(
        {
            "title": "GitLab Duo remediation",
            "url": "https://about.gitlab.com/blog/remediate",
            "snippet": "Duo remediates vulnerabilities.",
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "autofix_remediation",
            "text": "GitLab Duo can remediate vulnerabilities with code fixes.",
        }
    )

    assert calls["source"]["dimension"] == "autofix_remediation"


def test_ingest_candidate_stores_coverage_assertions(monkeypatch):
    calls = {"assertions": None, "refreshes": []}

    monkeypatch.setattr(
        pipeline.relevance,
        "score",
        lambda candidate: {
            "relevant": True,
            "score": 0.92,
            "axis": "technical",
            "doc_type": "docs",
            "dimension": "package_firewall",
            "evidence_state": "planned",
        },
    )
    monkeypatch.setattr(
        pipeline.compiler,
        "synthesize",
        lambda text, meta: {
            "compiled": "GitLab has a package firewall proposal.",
            "coverage_assertions": [
                {
                    "dimension": "package_firewall",
                    "state": "planned",
                    "confidence": 0.81,
                    "claim": "GitLab has a package firewall proposal.",
                    "reason": "proposal evidence",
                }
            ],
            "facts": [],
            "entities": [],
            "relationships": [],
            "conflicts": [],
            "axis": "technical",
        },
    )
    monkeypatch.setattr(pipeline.gemini, "embed_documents", lambda chunks: [[0.1]])
    monkeypatch.setattr(pipeline.repository, "source_exists", lambda url, content_hash: False)
    monkeypatch.setattr(pipeline.repository, "upsert_source", lambda **kwargs: 654)
    monkeypatch.setattr(pipeline.repository, "insert_chunks", lambda **kwargs: None)
    monkeypatch.setattr(pipeline.repository, "supersede_older", lambda *args: 0)
    monkeypatch.setattr(
        pipeline.repository,
        "insert_dimension_coverage_assertions",
        lambda source_id, assertions, **kwargs: calls.update(
            {"assertions": (source_id, assertions, kwargs)}
        )
        or len(assertions),
    )
    monkeypatch.setattr(
        pipeline.repository,
        "refresh_dimension_coverage_status",
        lambda *args, **kwargs: calls["refreshes"].append((args, kwargs)) or {},
    )

    pipeline.ingest_candidate(
        {
            "title": "GitLab package firewall proposal",
            "url": "https://gitlab.com/gitlab-org/gitlab/-/issues/package-firewall",
            "snippet": "Proposal for package firewall.",
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "package_firewall",
            "text": "GitLab has a package firewall proposal.",
        }
    )

    source_id, assertions, kwargs = calls["assertions"]
    assert source_id == 654
    assert kwargs["reason"] == "ingestion"
    assert assertions[0]["dimension"] == "package_firewall"
    assert assertions[0]["state"] == "planned"
    assert calls["refreshes"][0][0] == (
        "GitLab",
        "technical",
        "package_firewall",
    )


def test_ingest_candidate_uses_scoped_verdict_without_relevance_drift(monkeypatch):
    calls = {"assertions": None, "meta": None}

    monkeypatch.setattr(
        pipeline.relevance,
        "score",
        lambda candidate: (_ for _ in ()).throw(AssertionError("relevance called")),
    )

    def fake_synthesize(text, meta):
        calls["meta"] = meta
        return {
            "compiled": "GitLab has a package firewall proposal.",
            "coverage_assertions": [],
            "facts": [],
            "entities": [],
            "relationships": [],
            "conflicts": [],
            "axis": "technical",
        }

    monkeypatch.setattr(pipeline.compiler, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline.gemini, "embed_documents", lambda chunks: [[0.1]])
    monkeypatch.setattr(pipeline.repository, "source_exists", lambda url, content_hash: False)
    monkeypatch.setattr(pipeline.repository, "upsert_source", lambda **kwargs: 987)
    monkeypatch.setattr(pipeline.repository, "insert_chunks", lambda **kwargs: None)
    monkeypatch.setattr(pipeline.repository, "supersede_older", lambda *args: 0)
    monkeypatch.setattr(
        pipeline.repository,
        "insert_dimension_coverage_assertions",
        lambda source_id, assertions, **kwargs: calls.update(
            {"assertions": (source_id, assertions, kwargs)}
        )
        or len(assertions),
    )
    monkeypatch.setattr(
        pipeline.repository,
        "refresh_dimension_coverage_status",
        lambda *args, **kwargs: {},
    )

    pipeline.ingest_candidate(
        {
            "title": "GitLab package firewall proposal",
            "url": "https://gitlab.com/gitlab-org/gitlab/-/issues/package-firewall",
            "snippet": "Proposal for package firewall.",
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "package_firewall",
            "evidence_state": "planned",
            "coverage_gap": {
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "package_firewall",
            },
            "coverage_verdict": {"state": "planned", "confidence": 0.78},
            "text": "GitLab has a package firewall proposal.",
        }
    )

    source_id, assertions, _kwargs = calls["assertions"]
    assert source_id == 987
    assert calls["meta"]["coverage_gap"]["dimension"] == "package_firewall"
    assert assertions[0]["state"] == "planned"


def test_ingest_candidate_uses_snippet_when_fetch_extracts_no_text(monkeypatch):
    seen = {}

    monkeypatch.setattr(
        pipeline.web_lane,
        "fetch",
        lambda url, competitor: {
            "title": "Fetched title",
            "text": "",
            "published": date(2026, 1, 3),
        },
    )
    monkeypatch.setattr(
        pipeline.relevance,
        "score",
        lambda candidate: {
            "relevant": True,
            "score": 0.9,
            "axis": "technical",
            "doc_type": "docs",
            "dimension": "sbom_generation",
        },
    )

    def fake_synthesize(text, meta):
        seen["text"] = text
        return {
            "compiled": text,
            "facts": [],
            "entities": [],
            "relationships": [],
            "conflicts": [],
            "axis": "technical",
        }

    monkeypatch.setattr(pipeline.compiler, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline.gemini, "embed_documents", lambda chunks: [[0.1]])
    monkeypatch.setattr(pipeline.repository, "source_exists", lambda url, content_hash: False)
    monkeypatch.setattr(pipeline.repository, "upsert_source", lambda **kwargs: 456)
    monkeypatch.setattr(pipeline.repository, "insert_chunks", lambda **kwargs: None)
    monkeypatch.setattr(pipeline.repository, "supersede_older", lambda *args: 0)

    report = pipeline.ingest_candidate(
        {
            "title": "Candidate title",
            "url": "https://example.com/empty",
            "snippet": "A useful fallback snippet.",
            "competitor": "JFrog",
        }
    )

    assert seen["text"] == "A useful fallback snippet."
    assert report["source_id"] == 456


def test_ingest_candidate_bypasses_relevance_for_official_report_slice(monkeypatch):
    seen = {}

    monkeypatch.setattr(
        pipeline.relevance,
        "score",
        lambda candidate: (_ for _ in ()).throw(AssertionError("relevance called")),
    )

    def fake_synthesize(text, meta):
        seen["text"] = text
        seen["meta"] = meta
        return {
            "compiled": text,
            "facts": [],
            "entities": [],
            "relationships": [],
            "conflicts": [],
            "axis": "business",
            "doc_type": "company_fact",
            "dimension": "company_profile",
        }

    monkeypatch.setattr(pipeline.compiler, "synthesize", fake_synthesize)
    monkeypatch.setattr(pipeline.gemini, "embed_documents", lambda chunks: [[0.1]])
    monkeypatch.setattr(pipeline.repository, "source_exists", lambda url, content_hash: False)
    monkeypatch.setattr(pipeline.repository, "upsert_source", lambda **kwargs: 789)
    monkeypatch.setattr(pipeline.repository, "insert_chunks", lambda **kwargs: None)
    monkeypatch.setattr(pipeline.repository, "supersede_older", lambda *args: 0)

    report = pipeline.ingest_candidate(
        {
            "title": "JFrog official research: company_profile",
            "url": "ci-report://official-deep-research/jfrog/abc123#company-profile",
            "snippet": "Official report slice.",
            "text": "FACT: JFrog describes its platform in official material.",
            "competitor": "JFrog",
            "published": date(2026, 1, 2),
            "axis": "business",
            "dimension": "company_profile",
            "doc_type": "company_fact",
            "source_kind": "official_llm_research_report",
            "raw_path": "raw_snapshots/jfrog/report.md",
        }
    )

    assert report["source_id"] == 789
    assert seen["text"] == "FACT: JFrog describes its platform in official material."
    assert seen["meta"]["relevance_score"] == 1.0
    assert seen["meta"]["source_kind"] == "official_llm_research_report"


def test_ingest_candidate_inserts_citations_for_official_report_slice(monkeypatch):
    calls = {"citations": None}

    monkeypatch.setattr(
        pipeline.relevance,
        "score",
        lambda candidate: (_ for _ in ()).throw(AssertionError("relevance called")),
    )
    monkeypatch.setattr(
        pipeline.compiler,
        "synthesize",
        lambda text, meta: {
            "compiled": text,
            "facts": [],
            "entities": [],
            "relationships": [],
            "conflicts": [],
            "axis": "business",
            "doc_type": "company_fact",
            "dimension": "company_profile",
        },
    )
    monkeypatch.setattr(pipeline.gemini, "embed_documents", lambda chunks: [[0.1]])
    monkeypatch.setattr(pipeline.repository, "source_exists", lambda url, content_hash: False)
    monkeypatch.setattr(pipeline.repository, "upsert_source", lambda **kwargs: 901)
    monkeypatch.setattr(pipeline.repository, "insert_chunks", lambda **kwargs: None)
    monkeypatch.setattr(pipeline.repository, "supersede_older", lambda *args: 0)
    monkeypatch.setattr(
        pipeline.repository,
        "insert_source_citations",
        lambda source_id, citations: calls.update(
            {"citations": (source_id, citations)}
        )
        or len(citations),
    )

    pipeline.ingest_candidate(
        {
            "title": "JFrog official research: company_profile",
            "url": "ci-report://official-deep-research/jfrog/abc123#company-profile",
            "snippet": "Official report slice.",
            "text": "FACT: JFrog describes its platform [https://jfrog.com/platform, 2026-01-02].",
            "competitor": "JFrog",
            "published": date(2026, 1, 2),
            "axis": "business",
            "dimension": "company_profile",
            "doc_type": "company_fact",
            "source_kind": "official_llm_research_report",
            "raw_path": "raw_snapshots/jfrog/report.md",
            "citations": [
                {
                    "url": "https://jfrog.com/platform",
                    "label": "JFrog platform",
                    "date_text": "2026-01-02",
                }
            ],
        }
    )

    assert calls["citations"] == (
        901,
        [
            {
                "url": "https://jfrog.com/platform",
                "label": "JFrog platform",
                "date_text": "2026-01-02",
            }
        ],
    )


def test_duplicate_report_slice_preserves_citations_and_skips_embedding(monkeypatch):
    calls = {"citations": None}

    monkeypatch.setattr(
        pipeline.relevance,
        "score",
        lambda candidate: (_ for _ in ()).throw(AssertionError("relevance called")),
    )
    monkeypatch.setattr(
        pipeline.compiler,
        "synthesize",
        lambda text, meta: (_ for _ in ()).throw(AssertionError("synthesis called")),
    )
    monkeypatch.setattr(pipeline.repository, "source_exists", lambda url, content_hash: True)
    monkeypatch.setattr(pipeline.repository, "upsert_source", lambda **kwargs: 902)
    monkeypatch.setattr(
        pipeline.repository,
        "insert_source_citations",
        lambda source_id, citations: calls.update(
            {"citations": (source_id, citations)}
        )
        or len(citations),
    )
    monkeypatch.setattr(
        pipeline.gemini,
        "embed_documents",
        lambda chunks: (_ for _ in ()).throw(AssertionError("embedding called")),
    )

    report = pipeline.ingest_candidate(
        {
            "title": "JFrog official research: company_profile",
            "url": "ci-report://official-deep-research/jfrog/abc123#company-profile",
            "snippet": "Official report slice.",
            "text": "FACT: JFrog describes its platform [https://jfrog.com/platform, 2026-01-02].",
            "competitor": "JFrog",
            "published": date(2026, 1, 2),
            "axis": "business",
            "dimension": "company_profile",
            "doc_type": "company_fact",
            "source_kind": "official_llm_research_report",
            "raw_path": "raw_snapshots/jfrog/report.md",
        }
    )

    assert report["skipped"] == "duplicate content"
    assert calls["citations"] == (
        902,
        [
            {
                "url": "https://jfrog.com/platform",
                "label": None,
                "date_text": "2026-01-02",
            }
        ],
    )


def test_chunk_text_prefers_paragraph_boundaries_and_word_overlap(monkeypatch):
    monkeypatch.setattr(pipeline, "config_get", lambda path, default=None: {
        "chunking.chunk_size": 45,
        "chunking.chunk_overlap": 12,
    }.get(path, default))

    chunks = pipeline.chunk_text(
        "First paragraph about Artifactory.\n\n"
        "Second paragraph about Xray.\n\n"
        "This very long paragraph contains many words about product portfolio "
        "and should split without cutting a word in half."
    )

    assert chunks[0] == "First paragraph about Artifactory."
    assert chunks[1] == "Second paragraph about Xray."
    assert all(not chunk.startswith("ing ") for chunk in chunks)
    assert all(chunk == chunk.strip() for chunk in chunks)
