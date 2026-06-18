from ci_engine.acquire import company_profile_lane


def test_company_profile_lane_tags_source_map_and_tavily_candidates(monkeypatch):
    monkeypatch.setattr(
        company_profile_lane.web_lane,
        "discover_must_follow_sources",
        lambda competitor, **kwargs: [
            {
                "kind": "vendor_site",
                "url": "https://snyk.io/",
                "reason": "official home page",
            },
            {
                "kind": "docs",
                "url": "https://docs.snyk.io/",
                "reason": "official docs",
            },
        ],
    )
    monkeypatch.setattr(
        company_profile_lane.web_lane,
        "collect",
        lambda competitor, records, limit_per_feed=5: [
            {
                "title": record["reason"],
                "url": record["url"],
                "snippet": record["reason"],
                "text": record["reason"],
                "competitor": competitor,
                "published": None,
                "source_kind": record["kind"],
                "source_reason": record["reason"],
            }
            for record in records
        ],
    )
    monkeypatch.setattr(
        company_profile_lane.tavily_lane,
        "search",
        lambda competitor, topics=None, **kwargs: [
            {
                "title": "Snyk product portfolio",
                "url": "https://snyk.io/product",
                "snippet": "Product portfolio",
                "text": "Product portfolio",
                "competitor": competitor,
                "published": None,
                "source_kind": "vendor_site",
            }
        ],
    )

    candidates = company_profile_lane.search("Snyk")

    assert [candidate["dimension"] for candidate in candidates] == [
        "company_profile",
        "product_portfolio",
        "company_profile",
    ]
    assert candidates[0]["axis"] == "business"
    assert candidates[1]["axis"] == "both"
    assert candidates[1]["doc_type"] == "docs"
