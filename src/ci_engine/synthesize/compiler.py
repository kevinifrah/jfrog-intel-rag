from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from ci_engine.config import get as config_get
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret
from ci_engine.skills import load_skill


def _required_config(path: str) -> Any:
    value = config_get(path)
    if value is None:
        raise RuntimeError(f"missing required config value: {path}")
    return value


@lru_cache(maxsize=1)
def _client() -> Any:
    from anthropic import Anthropic  # noqa: PLC0415

    return Anthropic(api_key=get_secret("anthropic-key"), max_retries=0)


def synthesize(raw_text: str, meta: dict[str, Any]) -> dict[str, Any]:
    response = _client().messages.create(
        **_message_params(raw_text, meta),
    )
    return _parse_response(_response_text(response))


def _message_params(raw_text: str, meta: dict[str, Any]) -> dict[str, Any]:
    model = str(_required_config("models.synthesis.name"))
    params: dict[str, Any] = {
        "model": model,
        "system": load_skill("ingest-synthesis"),
        "messages": [
            {
                "role": "user",
                "content": _user_message(raw_text, meta),
            }
        ],
        "max_tokens": int(config_get("models.synthesis.max_tokens", 4096)),
        "timeout": float(
            config_get(
                "models.synthesis.timeout_s",
                config_get("ingestion.llm_timeout_s", 30),
            )
        ),
    }

    thinking = str(config_get("models.synthesis.thinking", "none")).lower()
    if _uses_effort(model):
        if thinking != "none":
            params["thinking"] = {"type": "adaptive"}
            params["output_config"] = {"effort": _effort(thinking)}
    else:
        params["temperature"] = float(config_get("models.synthesis.temperature", 0.2))

    return params


def _uses_effort(model: str) -> bool:
    return "opus-4-8" in model or "sonnet-4-5" in model


def _effort(thinking: str) -> str:
    if thinking in {"low", "medium", "high"}:
        return thinking
    return "high"


def _user_message(raw_text: str, meta: dict[str, Any]) -> str:
    payload = {
        "meta": meta,
        "raw_text": raw_text,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text is not None:
            parts.append(str(text))

    text = "".join(parts).strip()
    if not text:
        raise ValueError("synthesis model returned no text")
    return text


def _parse_response(text: str) -> dict[str, Any]:
    parsed = parse_json_object(text, label="synthesis model")
    if not isinstance(parsed.get("compiled"), str):
        raise ValueError("synthesis model response is missing compiled text")

    parsed.setdefault("facts", [])
    parsed.setdefault("coverage_assertions", [])
    parsed.setdefault("entities", [])
    parsed.setdefault("relationships", [])
    parsed.setdefault("conflicts", [])
    return parsed


__all__ = ["synthesize"]
