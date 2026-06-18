from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from ci_engine.db import repository
from ci_engine.ontology import is_canonical_dimension, normalize_dimension


_GENERIC_BAD_URL_PATTERNS = (
    re.compile(r"productboard\.com/product-management-prompts-library/winloss-analysis-framework", re.I),
    re.compile(r"productmarketingalliance\.com/.+win-loss", re.I),
    re.compile(r"corporatevisions\.com/.+win-loss-analysis", re.I),
    re.compile(r"klue\.com/blog/win-loss-analysis", re.I),
    re.compile(r"sayprimer\.com/blog/b2b-marketing-free-templates-customer-profile", re.I),
    re.compile(r"lennysnewsletter\.com/p/how-to-identify-your-ideal-customer", re.I),
    re.compile(r"customerfocus\.substack\.com/p/what-exactly-is-an-icp", re.I),
    re.compile(r"mehdeeka\.substack\.com/p/ideal-customer-profile", re.I),
    re.compile(r"kixie\.com/.+ideal-customer-profiles", re.I),
    re.compile(r"instagram\.com/reel/", re.I),
)

_KNOWN_BAD_URLS = {
    "https://docs.endorlabs.com/integrations/package-firewall",
    "https://docs.veracode.com/r/Connect_package_firewall_to_package_ecosystems",
    "https://ewserver.di.unimi.it/gitlab/help/user/profile/index.md",
    "https://docs.gitlab.com/user/project/codeowners",
    "https://www.reddit.com/r/gitlab/comments/1asdcnb/how_does_your_organization_manage_permissions_in",
    "https://medium.com/@shengyuchen/i-slept-well-last-night-and-took-a-huge-gulp-of-americano-this-morning-2d66b129ae3b",
    "https://armkeil.blob.core.windows.net/developer/Files/pdf/white-paper/orchestrating-applications-at-the-edge.pdf",
}

_COMPETITOR_HOST_HINTS = {
    "Aqua Security": ("aqua",),
    "Black Duck": ("blackduck", "synopsys"),
    "Checkmarx": ("checkmarx",),
    "Endor Labs": ("endorlabs",),
    "GitHub": ("github",),
    "GitLab": ("gitlab",),
    "JFrog": ("jfrog",),
    "Mend": ("mend", "whitesourcesoftware"),
    "Snyk": ("snyk",),
    "Sonatype": ("sonatype",),
}


def build_report(rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    source_rows = list(rows) if rows is not None else repository.healing_source_rows()
    dimension_updates: list[dict[str, Any]] = []
    status_updates: list[dict[str, Any]] = []
    unmapped_dimensions: list[dict[str, Any]] = []

    for row in source_rows:
        dimension_update = _dimension_update(row)
        if dimension_update is not None:
            dimension_updates.append(dimension_update)
        elif row.get("dimension") and not is_canonical_dimension(str(row["dimension"])):
            unmapped_dimensions.append(_row_summary(row))

        stale_reason = _stale_reason(row)
        if stale_reason is not None:
            status_updates.append(
                {
                    **_row_summary(row),
                    "old_status": row.get("status"),
                    "new_status": "stale",
                    "reason": stale_reason,
                }
            )

    return {
        "summary": {
            "sources_scanned": len(source_rows),
            "dimension_updates": len(dimension_updates),
            "status_updates": len(status_updates),
            "unmapped_dimensions": len(unmapped_dimensions),
        },
        "dimension_updates": dimension_updates,
        "status_updates": status_updates,
        "unmapped_dimensions": unmapped_dimensions,
    }


def apply_report(report: Mapping[str, Any]) -> dict[str, Any]:
    applied_dimensions = 0
    applied_statuses = 0
    repository.ensure_source_healing_audit()

    for item in report.get("dimension_updates", []):
        result = repository.update_source_dimension(
            int(item["source_id"]),
            str(item["new_dimension"]),
            reason=str(item["reason"]),
            details=_details(item),
            ensure_audit=False,
        )
        if result.get("changed"):
            applied_dimensions += 1

    for item in report.get("status_updates", []):
        result = repository.mark_source_status(
            int(item["source_id"]),
            "stale",
            reason=str(item["reason"]),
            details=_details(item),
            ensure_audit=False,
        )
        if result.get("changed"):
            applied_statuses += 1

    return {
        "applied_dimension_updates": applied_dimensions,
        "applied_status_updates": applied_statuses,
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Heal source dimensions and stale obvious bad sources.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Apply audited changes. Omit for dry-run.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the healing report without changing the database.",
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


def _dimension_update(row: Mapping[str, Any]) -> dict[str, Any] | None:
    old_dimension = row.get("dimension")
    new_dimension = normalize_dimension(
        str(old_dimension) if old_dimension is not None else None,
        axis=_axis_for_normalization(row.get("axis")),
        title=_text_or_none(row.get("title")),
        url=_text_or_none(row.get("url")),
        text=_text_or_none(row.get("chunk_text")),
    )
    if not new_dimension or new_dimension == old_dimension:
        return None
    if not is_canonical_dimension(new_dimension):
        return None

    return {
        **_row_summary(row),
        "old_dimension": old_dimension,
        "new_dimension": new_dimension,
        "reason": "canonical_dimension_alias",
    }


def _stale_reason(row: Mapping[str, Any]) -> str | None:
    url = str(row.get("url") or "")
    if url in _KNOWN_BAD_URLS:
        return "known_bad_url"
    if any(pattern.search(url) for pattern in _GENERIC_BAD_URL_PATTERNS):
        return "generic_non_evidence_url"
    if _is_wrong_company_owned_docs(row):
        return "wrong_company_owned_docs"
    return None


def _is_wrong_company_owned_docs(row: Mapping[str, Any]) -> bool:
    url = str(row.get("url") or "")
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if not host:
        return False
    if host == "github.com" or host.endswith(".github.com"):
        return False

    owner = _host_owner(host)
    competitor = str(row.get("competitor") or "")
    if owner is None or owner == competitor:
        return False

    path = urlparse(url).path.lower()
    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "dimension", "chunk_text")
    ).lower()
    if any(token in path for token in ("/compar", "alternative", "versus", "vs-")):
        return False
    if any(token in text for token in ("comparison", "alternative", "versus", " vs ")):
        return False
    return row.get("source_kind") in {"docs", "unknown", "vendor_site"}


def _host_owner(host: str) -> str | None:
    for competitor, hints in _COMPETITOR_HOST_HINTS.items():
        if any(hint in host for hint in hints):
            return competitor
    return None


def _axis_for_normalization(axis: Any) -> str | None:
    axis_text = str(axis or "").strip()
    if axis_text in {"technical", "business"}:
        return axis_text
    return None


def _row_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": int(row["source_id"]),
        "competitor": row.get("competitor"),
        "axis": row.get("axis"),
        "dimension": row.get("dimension"),
        "url": row.get("url"),
        "title": row.get("title"),
    }


def _details(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("competitor", "axis", "url", "title")
        if item.get(key) is not None
    }


def _trim_report(report: Mapping[str, Any], *, max_items: int) -> dict[str, Any]:
    trimmed = {"summary": report.get("summary", {})}
    for key in ("dimension_updates", "status_updates", "unmapped_dimensions"):
        values = list(_as_iterable(report.get(key)))
        trimmed[key] = values[:max_items]
        if len(values) > max_items:
            trimmed[f"{key}_truncated"] = len(values) - max_items
    return trimmed


def _as_iterable(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        return value
    return []


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


if __name__ == "__main__":
    main()
