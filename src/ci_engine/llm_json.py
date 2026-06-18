from __future__ import annotations

import json
import re
from typing import Any

_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def parse_json_object(text: str, *, label: str) -> dict[str, Any]:
    text = text.strip()
    candidates = [text]

    match = _FENCED_JSON_RE.search(text)
    if match:
        candidates.insert(0, match.group(1).strip())

    object_text = _first_json_object(text)
    if object_text is not None:
        candidates.append(object_text)

    errors: list[str] = []
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(parsed, dict):
            raise ValueError(f"{label} response must be a JSON object")
        return parsed

    excerpt = text[:300].replace("\n", "\\n")
    raise ValueError(
        f"{label} response was not valid JSON object; excerpt={excerpt!r}; "
        f"errors={errors}"
    )


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


__all__ = ["parse_json_object"]
