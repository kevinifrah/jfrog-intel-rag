from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ci_engine import dimension_coverage
from ci_engine.db import repository


def build_report(rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    source_rows = (
        list(rows)
        if rows is not None
        else repository.healing_source_rows(statuses=("active",))
    )
    assertions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in source_rows:
        row_assertions = dimension_coverage.source_row_assertions(row)
        valid_assertions = [
            assertion
            for assertion in row_assertions
            if assertion.get("competitor")
            and assertion.get("axis")
            and assertion.get("dimension")
            and assertion.get("state")
            and assertion.get("claim")
        ]
        if not valid_assertions:
            skipped.append(_row_summary(row, reason="no_dimension_assertion"))
            continue
        assertions.extend(valid_assertions)

    return {
        "summary": {
            "sources_scanned": len(source_rows),
            "assertions": len(assertions),
            "skipped": len(skipped),
            "states": _state_counts(assertions),
        },
        "assertions": assertions,
        "skipped": skipped,
    }


def apply_report(report: Mapping[str, Any]) -> dict[str, Any]:
    repository.ensure_dimension_coverage_tables()
    inserted = 0
    by_source: dict[int, list[dict[str, Any]]] = {}
    for assertion in report.get("assertions", []):
        source_id = int(assertion["source_id"])
        by_source.setdefault(source_id, []).append(dict(assertion))

    for source_id, assertions in by_source.items():
        inserted += repository.insert_dimension_coverage_assertions(
            source_id,
            assertions,
            reason="coverage_backfill",
            ensure_tables=False,
        )

    statuses = repository.refresh_all_dimension_coverage_statuses(
        reason="coverage_backfill",
    )
    return {
        "assertions_upserted": inserted,
        "status_rows_refreshed": len(statuses),
        "validation": validation_report(statuses),
    }


def validation_report(statuses: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    rows = list(statuses) if statuses is not None else repository.dimension_coverage_status()
    unknown = [dict(row) for row in rows if row.get("state") == "unknown"]
    conflicts = [dict(row) for row in rows if bool(row.get("conflict"))]
    return {
        "status_counts": _state_counts(rows),
        "unknown": unknown,
        "conflicts": conflicts,
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backfill dimension coverage assertions and rollups.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply audited coverage assertion/status changes. Omit for dry-run.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the backfill report without changing the database.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=50,
        help="Maximum detailed rows per section to print.",
    )
    args = parser.parse_args(argv)

    report = build_report()
    output = _trim_report(report, max_items=max(args.max_items, 0))
    output["mode"] = "apply" if args.apply else "dry-run"
    if args.apply:
        output["applied"] = apply_report(report)

    print(json.dumps(output, indent=2, sort_keys=True, default=str))


def _row_summary(row: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "source_id": int(row["source_id"]),
        "competitor": row.get("competitor"),
        "axis": row.get("axis"),
        "dimension": row.get("dimension"),
        "url": row.get("url"),
        "title": row.get("title"),
        "reason": reason,
    }


def _state_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _trim_report(report: Mapping[str, Any], *, max_items: int) -> dict[str, Any]:
    trimmed: dict[str, Any] = {"summary": report.get("summary", {})}
    for key in ("assertions", "skipped"):
        values = list(_as_iterable(report.get(key)))
        trimmed[key] = values[:max_items]
        if len(values) > max_items:
            trimmed[f"{key}_truncated"] = len(values) - max_items
    return trimmed


def _as_iterable(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        return value
    return []


if __name__ == "__main__":
    main()
