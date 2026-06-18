import json
import logging
from types import SimpleNamespace

from ci_engine.acquire import relevance
from ci_engine.skills import load_skill


class _FakeMessages:
    def __init__(self, text: str):
        self._text = text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class _FakeClient:
    def __init__(self, text: str):
        self.messages = _FakeMessages(text)


def test_score_calls_relevance_model_and_parses_json(monkeypatch):
    fake_client = _FakeClient(
        json.dumps(
            {
                "relevant": True,
                "score": 0.92,
                "axis": "technical",
                "doc_type": "docs",
                "dimension": "software_composition_analysis",
                "reason": "specific product documentation",
            }
        )
    )
    monkeypatch.setattr(relevance, "_client", lambda: fake_client)

    candidate = {
        "title": "JFrog Xray documentation",
        "snippet": "Scan dependencies and generate SBOMs.",
        "url": "https://jfrog.com/help/r/jfrog-security-documentation",
        "competitor": "JFrog",
        "ignored": "not sent",
    }

    result = relevance.score(candidate)

    assert result["relevant"] is True
    assert result["score"] == 0.92

    call = fake_client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    assert call["system"] == load_skill("relevance-rubric")
    assert call["temperature"] == 0.0
    assert call["timeout"] == 30.0

    message = call["messages"][0]
    assert message["role"] == "user"
    assert json.loads(message["content"]) == {
        "title": "JFrog Xray documentation",
        "snippet": "Scan dependencies and generate SBOMs.",
        "url": "https://jfrog.com/help/r/jfrog-security-documentation",
        "competitor": "JFrog",
        "source_kind": None,
        "source_reason": None,
        "axis_hint": None,
        "dimension_hint": None,
        "content_excerpt": None,
        "content_length": None,
    }


def test_score_sends_bounded_content_excerpt(monkeypatch):
    fake_client = _FakeClient(
        json.dumps(
            {
                "relevant": True,
                "score": 0.91,
                "axis": "business",
                "doc_type": "news",
                "dimension": "market_positioning",
                "reason": "newsletter contains facts",
            }
        )
    )
    monkeypatch.setattr(relevance, "_client", lambda: fake_client)
    monkeypatch.setattr(relevance, "_content_limit", lambda: 20)

    content = "JFrog launched a new package security feature for enterprise teams."
    result = relevance.score(
        {
            "title": "Newsletter discussing JFrog",
            "snippet": "JFrog launched a new package security feature.",
            "url": "https://newsletter.example.com/jfrog-feature",
            "competitor": "JFrog",
            "text": content,
        }
    )

    assert result["relevant"] is True
    payload = json.loads(fake_client.messages.calls[0]["messages"][0]["content"])
    assert payload["content_excerpt"] == "JFrog launched a new"
    assert payload["content_length"] == len(content)


def test_score_discards_below_threshold_and_logs_implausible_host(
    monkeypatch,
    caplog,
):
    fake_client = _FakeClient(
        json.dumps(
            {
                "relevant": True,
                "score": 0.2,
                "axis": "business",
                "doc_type": "news",
                "dimension": "market_positioning",
                "reason": "weakly related article",
            }
        )
    )
    monkeypatch.setattr(relevance, "_client", lambda: fake_client)
    caplog.set_level(logging.WARNING, logger=relevance.__name__)

    result = relevance.score(
        {
            "title": "Unrelated round-up",
            "snippet": "Mentions a competitor in passing.",
            "url": "https://example.invalid/round-up",
            "competitor": "Snyk",
        }
    )

    assert result["relevant"] is False
    assert result["score"] == 0.2
    assert "not obviously related" in caplog.text


def test_context7_host_is_treated_as_known_docs_host(monkeypatch, caplog):
    fake_client = _FakeClient(
        json.dumps(
            {
                "relevant": True,
                "score": 0.91,
                "axis": "technical",
                "doc_type": "docs",
                "dimension": "sbom_generation",
                "reason": "specific docs",
            }
        )
    )
    monkeypatch.setattr(relevance, "_client", lambda: fake_client)
    caplog.set_level(logging.WARNING, logger=relevance.__name__)

    result = relevance.score(
        {
            "title": "JFrog SBOM docs",
            "snippet": "Generate and export SBOMs.",
            "url": "https://context7.com/websites/jfrog",
            "competitor": "JFrog",
        }
    )

    assert result["relevant"] is True
    assert "not obviously related" not in caplog.text


def test_score_parses_json_wrapped_in_markdown_fence(monkeypatch):
    fake_client = _FakeClient(
        """
        ```json
        {
          "relevant": true,
          "score": 0.88,
          "axis": "technical",
          "doc_type": "docs",
          "dimension": "sbom_generation",
          "reason": "specific docs"
        }
        ```
        """
    )
    monkeypatch.setattr(relevance, "_client", lambda: fake_client)

    result = relevance.score(
        {
            "title": "JFrog SBOM documentation",
            "snippet": "Generate SBOMs.",
            "url": "https://jfrog.com/help/r/jfrog-security-documentation",
            "competitor": "JFrog",
        }
    )

    assert result["relevant"] is True
    assert result["score"] == 0.88
