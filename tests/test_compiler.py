import json
from datetime import date
from types import SimpleNamespace

from ci_engine.synthesize import compiler


class _FakeMessages:
    def __init__(self, text: str):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class _FakeClient:
    def __init__(self, text: str):
        self.messages = _FakeMessages(text)


def test_synthesize_parses_json_wrapped_in_text(monkeypatch):
    monkeypatch.setattr(
        compiler,
        "_client",
        lambda: _FakeClient(
            """
            Here is the JSON:
            {
              "compiled": "JFrog supports SBOM generation.",
              "facts": [],
              "entities": [],
              "relationships": [],
              "conflicts": [],
              "axis": "technical"
            }
            """
        ),
    )

    result = compiler.synthesize(
        "JFrog supports SBOM generation.",
        {"competitor": "JFrog"},
    )

    assert result["compiled"] == "JFrog supports SBOM generation."
    assert result["facts"] == []
    assert result["relationships"] == []


def test_synthesize_uses_effort_config_without_temperature(monkeypatch):
    fake_client = _FakeClient('{"compiled":"ok"}')
    monkeypatch.setattr(compiler, "_client", lambda: fake_client)

    compiler.synthesize("raw", {"competitor": "JFrog"})

    call = fake_client.messages.calls[0]
    assert call["model"] == "claude-opus-4-8"
    assert call["max_tokens"] == 12000
    assert "temperature" not in call
    assert call["timeout"] == 120.0
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "high"}


def test_synthesize_serializes_date_metadata(monkeypatch):
    fake_client = _FakeClient('{"compiled":"ok"}')
    monkeypatch.setattr(compiler, "_client", lambda: fake_client)

    compiler.synthesize("raw", {"publish_date": date(2026, 1, 2)})

    payload = json.loads(fake_client.messages.calls[0]["messages"][0]["content"])
    assert payload["meta"]["publish_date"] == "2026-01-02"
