import json

from ci_engine.db import heal_dimensions


def test_build_report_canonicalizes_aliases_and_marks_known_bad_urls():
    report = heal_dimensions.build_report(
        [
            {
                "source_id": 1,
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "vulnerability_remediation",
                "url": "https://about.gitlab.com/blog/remediate",
                "title": "Remediate with Duo",
                "status": "active",
                "source_kind": "blog",
                "chunk_text": "GitLab Duo remediates vulnerabilities.",
            },
            {
                "source_id": 2,
                "competitor": "GitLab",
                "axis": "business",
                "dimension": "target_segments_icp",
                "url": "https://www.sayprimer.com/blog/b2b-marketing-free-templates-customer-profile",
                "title": "Generic ICP template",
                "status": "active",
                "source_kind": "blog",
                "chunk_text": "Generic B2B marketing advice.",
            },
            {
                "source_id": 3,
                "competitor": "GitLab",
                "axis": "technical",
                "dimension": "custom_unknown_bucket",
                "url": "https://about.gitlab.com/custom",
                "title": "Custom",
                "status": "active",
                "source_kind": "blog",
                "chunk_text": "Custom content.",
            },
        ]
    )

    assert report["summary"] == {
        "sources_scanned": 3,
        "dimension_updates": 1,
        "status_updates": 1,
        "unmapped_dimensions": 1,
    }
    assert report["dimension_updates"][0]["new_dimension"] == "autofix_remediation"
    assert report["status_updates"][0]["reason"] == "generic_non_evidence_url"
    assert report["unmapped_dimensions"][0]["dimension"] == "custom_unknown_bucket"


def test_build_report_does_not_auto_stale_github_hosted_sources():
    report = heal_dimensions.build_report(
        [
            {
                "source_id": 10,
                "competitor": "Snyk",
                "axis": "technical",
                "dimension": "product_capabilities",
                "url": "https://github.com/snyk/user-docs/blob/main/docs/example.md",
                "title": "Snyk user docs",
                "status": "active",
                "source_kind": "unknown",
                "chunk_text": "Snyk documentation mirrored on GitHub.",
            }
        ]
    )

    assert report["status_updates"] == []


def test_apply_report_uses_audited_repository_operations(monkeypatch):
    calls = {"ensure": 0, "dimensions": [], "statuses": []}

    monkeypatch.setattr(
        heal_dimensions.repository,
        "ensure_source_healing_audit",
        lambda: calls.update({"ensure": calls["ensure"] + 1}),
    )
    monkeypatch.setattr(
        heal_dimensions.repository,
        "update_source_dimension",
        lambda source_id, new_dimension, **kwargs: calls["dimensions"].append(
            (source_id, new_dimension, kwargs)
        )
        or {"changed": True},
    )
    monkeypatch.setattr(
        heal_dimensions.repository,
        "mark_source_status",
        lambda source_id, new_status, **kwargs: calls["statuses"].append(
            (source_id, new_status, kwargs)
        )
        or {"changed": True},
    )

    result = heal_dimensions.apply_report(
        {
            "dimension_updates": [
                {
                    "source_id": 1,
                    "new_dimension": "sbom_generation",
                    "reason": "canonical_dimension_alias",
                    "competitor": "Sonatype",
                    "axis": "technical",
                    "url": "https://help.sonatype.com/sbom",
                    "title": "SBOM",
                }
            ],
            "status_updates": [
                {
                    "source_id": 2,
                    "reason": "known_bad_url",
                    "competitor": "GitLab",
                    "axis": "business",
                    "url": "https://example.com/bad",
                    "title": "Bad",
                }
            ],
        }
    )

    assert result == {
        "applied_dimension_updates": 1,
        "applied_status_updates": 1,
    }
    assert calls["ensure"] == 1
    assert calls["dimensions"][0][0:2] == (1, "sbom_generation")
    assert calls["statuses"][0][0:2] == (2, "stale")


def test_main_accepts_explicit_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(
        heal_dimensions,
        "build_report",
        lambda: {
            "summary": {"sources_scanned": 0},
            "dimension_updates": [],
            "status_updates": [],
            "unmapped_dimensions": [],
        },
    )
    monkeypatch.setattr(
        heal_dimensions,
        "apply_report",
        lambda report: (_ for _ in ()).throw(AssertionError("should not apply")),
    )

    heal_dimensions.main(["--dry-run"])

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry-run"
