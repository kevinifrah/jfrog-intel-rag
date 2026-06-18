from datetime import date

from ci_engine.db import repository


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, stmt, params):
        return _FakeResult(
            [
                {
                    "competitor": "Snyk",
                    "dimension": "sbom_generation",
                    "active_sources": 2,
                    "freshest_publish_date": date(2026, 2, 3),
                }
            ]
        )


class _FakeEngine:
    def connect(self):
        return _FakeConnection()


def test_coverage_status_expands_configured_dimensions(monkeypatch):
    def fake_config_get(path, default=None):
        if path == "ontology":
            return {
                "technical": ["sbom_generation"],
                "business": ["pricing_packaging"],
            }
        return default

    monkeypatch.setattr(repository, "config_get", fake_config_get)
    monkeypatch.setattr(repository, "tracked_companies", lambda: ["Snyk"])
    monkeypatch.setattr(repository, "get_engine", lambda: _FakeEngine())

    status = repository.coverage_status()

    assert status == [
        {
            "competitor": "Snyk",
            "axis": "technical",
            "dimension": "sbom_generation",
            "active_sources": 2,
            "freshest_publish_date": date(2026, 2, 3),
        },
        {
            "competitor": "Snyk",
            "axis": "business",
            "dimension": "pricing_packaging",
            "active_sources": 0,
            "freshest_publish_date": None,
        },
    ]
