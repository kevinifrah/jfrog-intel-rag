from __future__ import annotations

import argparse
import json
from typing import Any

from ci_engine.synthesize.pipeline import ingest_candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest one URL into the CI corpus")
    parser.add_argument("--url", required=True)
    parser.add_argument("--competitor", required=True)
    args = parser.parse_args(argv)

    report = ingest_candidate(
        {
            "url": args.url,
            "competitor": args.competitor,
        }
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=_json_default))
    return 0


def _json_default(value: Any) -> str:
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
