import logging
from datetime import date

from ci_engine.synthesize import close_coverage_scope, deep_map, discover


def test_deep_map_gathers_axis_specific_lanes_and_reports_coverage(
    monkeypatch,
    caplog,
):
    ingested = []
    calls = {"context7": [], "tavily": [], "web": []}

    def fake_config_get(path, default=None):
        if path == "deep_map_now":
            return ["Snyk"]
        if path == "ontology":
            return {
                "technical": ["sbom_generation"],
                "business": ["pricing_packaging"],
            }
        if path == "ingestion.preflight_db":
            return False
        return default

    def fake_context7_search(competitor, topics=None, **kwargs):
        calls["context7"].append((competitor, topics))
        return [
            {
                "title": "Context7 docs",
                "url": "https://context7.com/snyk/docs",
                "snippet": "SBOM docs",
                "competitor": competitor,
                "published": None,
            }
        ]

    def fake_tavily_search(competitor, topics=None, **kwargs):
        calls["tavily"].append((competitor, topics))
        return [
            {
                "title": "Pricing",
                "url": "https://snyk.io/pricing",
                "snippet": "Pricing page",
                "competitor": competitor,
                "published": None,
            }
        ]

    def fake_web_search(competitor, **kwargs):
        calls["web"].append((competitor, kwargs.get("topics")))
        return [
            {
                "title": "Web official research",
                "url": "ci-report://official-deep-research/snyk/abc#company-profile",
                "snippet": "Official research",
                "competitor": competitor,
                "published": None,
                "source_kind": "official_llm_research_report",
                "axis": "business",
                "dimension": "company_profile",
                "doc_type": "company_fact",
            }
        ]

    def fake_ingest(candidate):
        ingested.append(candidate)
        return {"source_id": len(ingested), "superseded": 0}

    monkeypatch.setattr(deep_map, "config_get", fake_config_get)
    monkeypatch.setattr(deep_map.context7_lane, "search", fake_context7_search)
    monkeypatch.setattr(deep_map.tavily_lane, "search", fake_tavily_search)
    monkeypatch.setattr(deep_map.web_lane, "search", fake_web_search)
    monkeypatch.setattr(deep_map.pipeline, "ingest_candidate", fake_ingest)
    monkeypatch.setattr(
        deep_map.repository,
        "coverage_status",
        lambda: [
            {
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "sbom_generation",
                "active_sources": 1,
                "freshest_publish_date": date(2026, 1, 1),
            },
            {
                "competitor": "Snyk",
                "axis": "business",
                "dimension": "pricing_packaging",
                "active_sources": 0,
                "freshest_publish_date": None,
            },
        ],
    )
    caplog.set_level(logging.INFO, logger=deep_map.__name__)

    report = deep_map.run()

    assert len(ingested) == 4
    targeted = [
        candidate
        for candidate in ingested
        if candidate.get("source_kind") != "official_llm_research_report"
    ]
    assert [(candidate["axis"], candidate["dimension"]) for candidate in targeted] == [
        ("technical", "sbom_generation"),
        ("technical", "sbom_generation"),
        ("business", "pricing_packaging"),
    ]
    assert calls["context7"] == [("Snyk", ["sbom generation"])]
    assert calls["tavily"] == [
        ("Snyk", ["sbom generation"]),
        ("Snyk", ["pricing packaging"]),
    ]
    assert calls["web"] == [("Snyk", None)]
    assert [row["covered"] for row in report["coverage"]] == [True, False]
    assert "[deep-map] company=Snyk" in caplog.text
    assert "[gather] technical/sbom_generation tavily candidates=1" in caplog.text
    assert "[ingest] source_id=1" in caplog.text
    assert "[coverage] Snyk technical/sbom_generation covered=True" in caplog.text


def test_deep_map_stops_early_when_database_preflight_fails(monkeypatch, caplog):
    def fake_config_get(path, default=None):
        if path == "deep_map_now":
            return ["Snyk"]
        if path == "ontology":
            return {"technical": ["sbom_generation"], "business": []}
        return default

    def fail_healthcheck():
        raise RuntimeError("db auth failed")

    monkeypatch.setattr(deep_map.connection, "healthcheck", fail_healthcheck)
    monkeypatch.setattr(
        deep_map.web_lane,
        "search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )
    caplog.set_level(logging.ERROR, logger=deep_map.__name__)

    report = deep_map.run()

    assert report == {
        "error": "database preflight failed: db auth failed",
        "ingested": [],
        "coverage": [],
    }
    assert "database preflight failed" in caplog.text


def test_discover_incremental_and_gap_pass_with_mocked_lanes(monkeypatch):
    coverage_calls = {"count": 0}
    ingested = []

    def fake_tavily_search(competitor, topics=None, **kwargs):
        if topics:
            return [
                {
                    "title": "Targeted pricing",
                    "url": "https://snyk.io/targeted-pricing",
                    "snippet": "Targeted pricing",
                    "competitor": competitor,
                    "published": date(2026, 1, 3),
                }
            ]
        return [
            {
                "title": "Snyk news",
                "url": "https://snyk.io/news",
                "snippet": "News",
                "competitor": competitor,
                "published": date(2026, 1, 2),
            }
        ]

    def fake_web_search(competitor, **kwargs):
        topics = kwargs.get("topics") or []
        if "pricing packaging" in topics:
            return [
                {
                    "title": "Targeted web pricing",
                    "url": "https://example.com/targeted-pricing",
                    "snippet": "Targeted web pricing",
                    "competitor": competitor,
                    "published": None,
                }
            ]
        return [
            {
                "title": "Fresh feed item",
                "url": "https://snyk.io/feed/fresh",
                "snippet": "Fresh",
                "competitor": competitor,
                "published": date(2026, 1, 2),
            },
            {
                "title": "Old feed item",
                "url": "https://snyk.io/feed/old",
                "snippet": "Old",
                "competitor": competitor,
                "published": date(2025, 12, 31),
            },
        ]

    def fake_coverage_status():
        coverage_calls["count"] += 1
        if coverage_calls["count"] == 1:
            return [
                {
                    "competitor": "Snyk",
                    "axis": "business",
                    "dimension": "pricing_packaging",
                    "active_sources": 0,
                    "freshest_publish_date": None,
                }
            ]
        return []

    def fake_ingest(candidate):
        ingested.append(candidate)
        report = {
            "source_id": abs(hash(candidate["url"])) % 1000,
            "n_chunks": 1,
            "n_entities": 1,
            "n_edges": 0,
            "conflicts": [],
            "superseded": 0,
        }
        if "targeted" in candidate["url"]:
            report["superseded"] = 1
        return report

    monkeypatch.setattr(discover, "tracked_companies", lambda: ["Snyk"])
    monkeypatch.setattr(discover.connection, "healthcheck", lambda: 1)
    monkeypatch.setattr(discover.repository, "coverage_status", fake_coverage_status)
    monkeypatch.setattr(discover.tavily_lane, "search", fake_tavily_search)
    monkeypatch.setattr(discover.web_lane, "search", fake_web_search)
    monkeypatch.setattr(discover.context7_lane, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(discover.pipeline, "ingest_candidate", fake_ingest)

    report = discover.run()

    assert len(report["added"]) == 3
    assert len(report["updated"]) == 1
    assert report["skipped"] == []
    assert report["errors"] == []
    assert report["still_missing"] == []
    targeted = [candidate for candidate in ingested if "targeted" in candidate["url"]]
    assert targeted
    assert all(candidate["axis"] == "business" for candidate in targeted)
    assert all(candidate["dimension"] == "pricing_packaging" for candidate in targeted)


def test_discover_stops_early_when_database_preflight_fails(monkeypatch):
    def fail_healthcheck():
        raise RuntimeError("db auth failed")

    monkeypatch.setattr(discover.connection, "healthcheck", fail_healthcheck)

    report = discover.run()

    assert report["added"] == []
    assert report["updated"] == []
    assert report["skipped"] == []
    assert report["still_missing"] == []
    assert report["errors"] == [
        {
            "phase": "preflight",
            "error": "database preflight failed: db auth failed",
        }
    ]


def test_close_coverage_scope_dry_run_lists_unknown_gaps(monkeypatch):
    monkeypatch.setattr(close_coverage_scope.connection, "healthcheck", lambda: 1)
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "package_firewall",
                "state": "unknown",
            }
        ],
    )
    monkeypatch.setattr(
        close_coverage_scope.tavily_lane,
        "search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    report = close_coverage_scope.run(apply=False)

    assert report["mode"] == "dry-run"
    assert report["gaps"] == [
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "package_firewall",
        }
    ]
    assert "GitLab package firewall supported" in report["candidate_topics"][0]["topics"]


def test_close_coverage_scope_filters_gaps(monkeypatch):
    calls = {}

    monkeypatch.setattr(close_coverage_scope.connection, "healthcheck", lambda: 1)
    monkeypatch.setattr(
        close_coverage_scope,
        "config_get",
        lambda path, default=None: ["GitLab", "Snyk"]
        if path == "deep_map_now"
        else default,
    )

    def fake_dimension_status(**kwargs):
        calls.update(kwargs)
        assert kwargs == {
            "competitors": ["GitLab"],
            "axis": "technical",
            "dimensions": ["autofix_remediation"],
        }
        return [
            {
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "autofix_remediation",
                "state": "unknown",
            }
        ]

    monkeypatch.setattr(
        close_coverage_scope.repository,
        "dimension_coverage_status",
        fake_dimension_status,
    )

    report = close_coverage_scope.run(
        apply=False,
        competitor=["GitLab", "JFrog"],
        dimension="vulnerability_remediation",
        axis="technical",
        only_deep_map_now=True,
    )

    assert calls["competitors"] == ["GitLab"]
    assert report["filters"]["dimensions"] == ["autofix_remediation"]
    assert report["gaps"] == [
        {
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "autofix_remediation",
        }
    ]


def test_close_coverage_scope_apply_researches_and_ingests(monkeypatch):
    ingested = []

    monkeypatch.setattr(close_coverage_scope.connection, "healthcheck", lambda: 1)
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "ensure_dimension_coverage_tables",
        lambda: None,
    )
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "package_firewall",
                "state": "unknown",
            }
        ],
    )
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "refresh_dimension_coverage_status",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        close_coverage_scope,
        "config_get",
        lambda path, default=None: False
        if path == "ingestion.enable_context7_lane"
        else default,
    )
    monkeypatch.setattr(
        close_coverage_scope.tavily_lane,
        "search",
        lambda competitor, topics=None, **kwargs: [
            {
                "title": "Package firewall proposal",
                "url": "https://gitlab.com/gitlab-org/gitlab/-/issues/package-firewall",
                "snippet": "Proposal.",
                "competitor": competitor,
            }
        ],
    )

    def fake_ingest(candidate):
        ingested.append(candidate)
        return {"source_id": 1, "superseded": 0}

    monkeypatch.setattr(close_coverage_scope.pipeline, "ingest_candidate", fake_ingest)

    report = close_coverage_scope.run(
        apply=True,
        max_gaps=1,
        max_candidates_per_gap=1,
    )

    assert report["mode"] == "apply"
    assert len(report["added"]) == 1
    assert ingested[0]["axis"] == "technical"
    assert ingested[0]["dimension"] == "package_firewall"
    assert ingested[0]["evidence_state"] == "planned"
    assert ingested[0]["coverage_gap"] == {
        "competitor": "GitLab",
        "axis": "technical",
        "dimension": "package_firewall",
    }
    assert report["processed"][0]["ingested_source_ids"] == [1]


def test_close_coverage_scope_apply_skips_irrelevant_candidates(monkeypatch):
    monkeypatch.setattr(close_coverage_scope.connection, "healthcheck", lambda: 1)
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "ensure_dimension_coverage_tables",
        lambda: None,
    )
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "package_firewall",
                "state": "unknown",
            }
        ],
    )
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "refresh_dimension_coverage_status",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        close_coverage_scope,
        "_targeted_candidates",
        lambda *args, **kwargs: [{"url": "https://example.com", "title": "Noise"}],
    )
    monkeypatch.setattr(
        close_coverage_scope.coverage_verdict,
        "classify_candidate",
        lambda *args, **kwargs: {
            "state": "irrelevant",
            "confidence": 0.1,
            "evidence": "Noise",
            "reason": "does not answer gap",
            "source_trust": "third_party",
        },
    )
    monkeypatch.setattr(
        close_coverage_scope.pipeline,
        "ingest_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    report = close_coverage_scope.run(apply=True, max_gaps=1)

    assert report["added"] == []
    assert report["skipped"][0]["reason"] == "irrelevant"
    assert report["processed"][0]["verdicts"][0]["state"] == "irrelevant"


def test_close_coverage_scope_review_queue_for_risky_absent(monkeypatch):
    monkeypatch.setattr(close_coverage_scope.connection, "healthcheck", lambda: 1)
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "ensure_dimension_coverage_tables",
        lambda: None,
    )
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "dimension_coverage_status",
        lambda **kwargs: [
            {
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "package_firewall",
                "state": "unknown",
            }
        ],
    )
    monkeypatch.setattr(
        close_coverage_scope.repository,
        "refresh_dimension_coverage_status",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        close_coverage_scope,
        "_targeted_candidates",
        lambda *args, **kwargs: [
            {"url": "https://example.com/gitlab", "title": "Unsupported"}
        ],
    )
    monkeypatch.setattr(
        close_coverage_scope.coverage_verdict,
        "classify_candidate",
        lambda *args, **kwargs: {
            "state": "needs_review",
            "confidence": 0.86,
            "evidence": "Third-party says unsupported.",
            "reason": "explicit absent requires review",
            "source_trust": "third_party",
        },
    )
    monkeypatch.setattr(
        close_coverage_scope.pipeline,
        "ingest_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not called")),
    )

    report = close_coverage_scope.run(apply=True, max_gaps=1)

    assert report["review"][0]["reason"] == "explicit absent requires review"
    assert report["review"][0]["state"] == "needs_review"
    assert report["skipped"][0]["reason"] == "needs_review"
