from ci_engine import dimension_coverage


def test_infer_state_requires_explicit_negative_for_absent():
    assert dimension_coverage.infer_state(text="No mention of package firewall.")[0] == "present"
    assert (
        dimension_coverage.infer_state(
            text="Package firewall is not currently supported."
        )[0]
        == "absent"
    )
    assert (
        dimension_coverage.infer_state(
            text=(
                "Package firewall is documented with current support. "
                + "This long compiled summary includes unrelated wording about "
                + "an unsupported marketing claim. "
                + "Current support is otherwise documented. "
            )
            * 8,
            dimension="package_firewall",
        )[0]
        == "present"
    )


def test_infer_state_detects_planned_and_partial():
    assert (
        dimension_coverage.infer_state(
            text="Package firewall support is on the roadmap and in beta."
        )[0]
        == "planned"
    )
    assert (
        dimension_coverage.infer_state(
            text="The feature only supports selected ecosystems."
        )[0]
        == "partial"
    )


def test_rollup_prefers_strongest_current_evidence_and_marks_conflict():
    rollup = dimension_coverage.rollup_assertions(
        [
            {
                "source_id": 1,
                "state": "absent",
                "confidence": 0.9,
            },
            {
                "source_id": 2,
                "state": "planned",
                "confidence": 0.7,
            },
            {
                "source_id": 3,
                "state": "present",
                "confidence": 0.6,
            },
        ]
    )

    assert rollup["state"] == "present"
    assert rollup["strongest_source_id"] == 3
    assert rollup["conflict"] is True
    assert rollup["states"] == {"absent": 1, "planned": 1, "present": 1}


def test_source_assertions_use_candidate_dimension_as_authority():
    assertions = dimension_coverage.source_assertions(
        source_id=10,
        meta={
            "competitor": "GitLab",
            "axis": "technical",
            "dimension": "autofix_remediation",
            "title": "GitLab Duo remediation",
            "url": "https://about.gitlab.com/blog/remediate",
        },
        synthesis={
            "compiled": "GitLab Duo can remediate vulnerabilities.",
            "coverage_assertions": [
                {
                    "dimension": "vulnerability_management",
                    "state": "present",
                    "confidence": 0.9,
                    "claim": "GitLab Duo can remediate vulnerabilities.",
                    "reason": "docs",
                }
            ],
        },
    )

    assert assertions[0]["dimension"] == "autofix_remediation"
    assert assertions[0]["axis"] == "technical"
    assert assertions[0]["state"] == "present"
