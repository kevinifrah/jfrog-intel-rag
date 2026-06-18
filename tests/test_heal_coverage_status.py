from ci_engine.db import heal_coverage_status


def test_build_report_creates_assertions_from_existing_sources():
    report = heal_coverage_status.build_report(
        [
            {
                "source_id": 1,
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "package_firewall",
                "url": "https://gitlab.com/gitlab-org/gitlab/-/issues/1",
                "title": "Package firewall proposal",
                "doc_type": "blog",
                "source_kind": "blog",
                "chunk_text": "Package firewall support is proposed on the roadmap.",
            },
            {
                "source_id": 2,
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "sbom_generation",
                "url": "https://docs.snyk.io/sbom",
                "title": "Snyk SBOM",
                "doc_type": "docs",
                "source_kind": "docs",
                "chunk_text": "Snyk exports CycloneDX SBOMs.",
            },
        ]
    )

    assert report["summary"]["sources_scanned"] == 2
    assert report["summary"]["assertions"] == 2
    assert report["summary"]["states"] == {"planned": 1, "present": 1}
    assert report["assertions"][0]["state"] == "planned"
    assert report["assertions"][1]["state"] == "present"


def test_apply_report_upserts_assertions_and_refreshes_rollups(monkeypatch):
    calls = {"ensure": 0, "insert": [], "refresh": 0}

    monkeypatch.setattr(
        heal_coverage_status.repository,
        "ensure_dimension_coverage_tables",
        lambda: calls.update({"ensure": calls["ensure"] + 1}),
    )
    monkeypatch.setattr(
        heal_coverage_status.repository,
        "insert_dimension_coverage_assertions",
        lambda source_id, assertions, **kwargs: calls["insert"].append(
            (source_id, assertions, kwargs)
        )
        or len(assertions),
    )
    monkeypatch.setattr(
        heal_coverage_status.repository,
        "refresh_all_dimension_coverage_statuses",
        lambda **kwargs: calls.update({"refresh": calls["refresh"] + 1})
        or [
            {
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "sbom_generation",
                "state": "present",
                "conflict": False,
            }
        ],
    )

    result = heal_coverage_status.apply_report(
        {
            "assertions": [
                {
                    "source_id": 1,
                    "competitor": "Snyk",
                    "axis": "technical",
                    "dimension": "sbom_generation",
                    "state": "present",
                    "confidence": 0.9,
                    "claim": "Snyk exports SBOMs.",
                    "reason": "docs",
                }
            ]
        }
    )

    assert calls["ensure"] == 1
    assert calls["insert"][0][0] == 1
    assert calls["insert"][0][2]["reason"] == "coverage_backfill"
    assert calls["refresh"] == 1
    assert result["assertions_upserted"] == 1
    assert result["validation"]["status_counts"] == {"present": 1}
