from __future__ import annotations

import argparse
import inspect
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ci_engine.config import get as config_get
from ci_engine.config import tracked_companies
from ci_engine.crews.report.workflow import generate_report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a JFrog competitive dossier.")
    parser.add_argument("--competitor", default="Sonatype")
    parser.add_argument(
        "--competitors",
        default=None,
        help="Comma-separated competitors to generate. JFrog is excluded unless --include-jfrog is set.",
    )
    parser.add_argument(
        "--all-companies",
        action="store_true",
        help="Generate reports for all configured companies except JFrog by default.",
    )
    parser.add_argument(
        "--deep-map-now",
        action="store_true",
        help="Generate reports for config.yaml deep_map_now companies except JFrog by default.",
    )
    parser.add_argument(
        "--include-jfrog",
        action="store_true",
        help="Do not remove JFrog from config-driven batch selections.",
    )
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

    competitors = _selected_competitors(args)
    formats = tuple(part.strip() for part in args.formats.split(",") if part.strip())
    multiple = len(competitors) > 1
    summaries: list[dict[str, Any]] = []
    _progress(
        "selected "
        f"{len(competitors)} competitor(s): {', '.join(competitors)}; "
        f"draft_mode={args.draft_mode}; formats={','.join(formats)}"
    )

    for index, competitor in enumerate(competitors, start=1):
        try:
            _progress(f"[{competitor}] starting report {index}/{len(competitors)}")
            generate_kwargs = {
                "focus": args.focus,
                "out_dir": _out_dir_for(args.out_dir, competitor, multiple=multiple),
                "formats": formats,
                "include_web": not args.no_web,
                "draft_mode": args.draft_mode,
            }
            if "progress" in inspect.signature(generate_report).parameters:
                generate_kwargs["progress"] = _progress
            result = generate_report(competitor, **generate_kwargs)
            summaries.append(_result_summary(competitor, result))
            _progress(
                f"[{competitor}] completed: "
                f"passed={result.validation.passed}; "
                f"evidence={len(result.evidence_pack.items)}; "
                f"gaps={len(result.evidence_pack.gaps)}; "
                f"renders={len(result.renders)}"
            )
        except Exception as exc:
            _progress(f"[{competitor}] failed: {exc}")
            summaries.append(
                {
                    "competitor": competitor,
                    "passed": False,
                    "error": str(exc),
                    "evidence_count": 0,
                    "gap_count": 0,
                    "renders": [],
                }
            )

    market_summary = _maybe_generate_market_report(args, multiple=multiple)
    if market_summary is not None:
        summaries.append(market_summary)

    if not multiple:
        print(json.dumps(_single_summary(summaries[0]), indent=2, sort_keys=True))
        if summaries[0].get("error"):
            raise SystemExit(1)
        return

    print(
        json.dumps(
            {
                "passed": all(bool(summary.get("passed")) for summary in summaries),
                "count": len(summaries),
                "failed": [
                    summary["competitor"]
                    for summary in summaries
                    if not summary.get("passed")
                ],
                "reports": summaries,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if any(not summary.get("passed") for summary in summaries):
        raise SystemExit(1)


def _maybe_generate_market_report(
    args: argparse.Namespace,
    *,
    multiple: bool,
) -> dict[str, Any] | None:
    """On a batch run, also publish the standalone Market & Strategic Context report.

    Part 1 (market context) is dropped from every customer dossier; its market-wide
    view is published once per batch run as a separate report instead.
    """
    from ci_engine.crews.report.market_report import (  # noqa: PLC0415
        generate_market_report,
        market_report_enabled,
        market_report_slug,
        market_report_title,
    )

    if not multiple or not market_report_enabled():
        return None

    slug = market_report_slug()
    out_dir = _out_dir_for(args.out_dir, slug, multiple=True)
    formats = tuple(part.strip() for part in args.formats.split(",") if part.strip())
    title = market_report_title()
    try:
        _progress(f"[{title}] starting standalone market report")
        result = generate_market_report(
            out_dir=out_dir,
            formats=formats,
            progress=_progress,
        )
        _progress(
            f"[{title}] completed: "
            f"passed={result.validation.passed}; "
            f"evidence={len(result.evidence_pack.items)}; "
            f"renders={len(result.renders)}"
        )
        return {
            "competitor": title,
            "passed": result.validation.passed,
            "evidence_count": len(result.evidence_pack.items),
            "gap_count": len(result.evidence_pack.gaps),
            "renders": [render.model_dump(mode="json") for render in result.renders],
        }
    except Exception as exc:
        _progress(f"[{title}] failed: {exc}")
        return {
            "competitor": title,
            "passed": False,
            "error": str(exc),
            "evidence_count": 0,
            "gap_count": 0,
            "renders": [],
        }


def _selected_competitors(args: argparse.Namespace) -> list[str]:
    selector_count = sum(
        bool(value)
        for value in (
            args.competitors,
            args.all_companies,
            args.deep_map_now,
        )
    )
    if selector_count > 1:
        raise SystemExit("Use only one of --competitors, --all-companies, or --deep-map-now.")
    if args.all_companies:
        competitors = tracked_companies()
    elif args.deep_map_now:
        competitors = list(config_get("deep_map_now", []))
    elif args.competitors:
        competitors = [
            part.strip()
            for part in str(args.competitors).split(",")
            if part.strip()
        ]
    else:
        competitors = [args.competitor]

    if not args.include_jfrog:
        competitors = [
            competitor
            for competitor in competitors
            if competitor.strip().lower() != "jfrog"
        ]
    competitors = _dedupe_competitors(competitors)
    if not competitors:
        raise SystemExit("No competitors selected for report generation.")
    return competitors


def _dedupe_competitors(competitors: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for competitor in competitors:
        cleaned = competitor.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _out_dir_for(out_dir: str | None, competitor: str, *, multiple: bool) -> str:
    slug = competitor.lower().replace(" ", "-")
    if out_dir is None:
        return str(Path("reports") / slug)
    if multiple:
        return str(Path(out_dir) / slug)
    return out_dir


def _result_summary(competitor: str, result: Any) -> dict[str, Any]:
    return {
        "competitor": competitor,
        "passed": result.validation.passed,
        "evidence_count": len(result.evidence_pack.items),
        "gap_count": len(result.evidence_pack.gaps),
        "renders": [render.model_dump(mode="json") for render in result.renders],
    }


def _single_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in summary.items()
        if key != "competitor"
    }


def _progress(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[report {timestamp}] {message}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
