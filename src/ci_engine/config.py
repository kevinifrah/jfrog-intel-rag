from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

with _CONFIG_PATH.open() as _f:
    CONFIG: dict = yaml.safe_load(_f)


def get(path: str, default: Any = None) -> Any:
    """Read a dotted path from CONFIG, e.g. get('retrieval.top_k')."""
    node = CONFIG
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def tracked_companies() -> list[str]:
    """Return company names tracked by the corpus.

    `competitors` is kept as a legacy fallback for older config files.
    """
    return list(get("companies", get("competitors", [])))
