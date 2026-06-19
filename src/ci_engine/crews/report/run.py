from __future__ import annotations

import argparse
import json
from pathlib import Path

from ci_engine.crews.report.workflow import generate_report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a JFrog competitive dossier.")
    parser.add_argument("--competitor", default="Sonatype")
    parser.add_argument("--focus", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--formats", default="pdf,html,json")
    parser.add_argument(
        "--draft-mode",
        choices=(
            "deterministic",
            "crew_strategy",
            "crew_strategy_market",
            "crew_strategy_market_technical",
            "crew_strategy_market_technical_field",
            "crew_strategy_market_product_technical_field",
            "crew_strategy_market_product_technical_field_scoring",
        ),
        default="deterministic",
    )
    parser.add_argument("--no-web", action="store_true")
    args = parser.parse_args(argv)

    out_dir = args.out_dir
    if out_dir is None:
        slug = args.competitor.lower().replace(" ", "-")
        out_dir = str(Path("reports") / slug)

    result = generate_report(
        args.competitor,
        focus=args.focus,
        out_dir=out_dir,
        formats=tuple(part.strip() for part in args.formats.split(",") if part.strip()),
        include_web=not args.no_web,
        draft_mode=args.draft_mode,
    )
    print(
        json.dumps(
            {
                "passed": result.validation.passed,
                "evidence_count": len(result.evidence_pack.items),
                "gap_count": len(result.evidence_pack.gaps),
                "renders": [render.model_dump(mode="json") for render in result.renders],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
