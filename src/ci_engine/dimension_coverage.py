from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ci_engine.ontology import normalize_dimension

COVERAGE_STATES = ("present", "partial", "planned", "absent", "unknown")
STATE_PRECEDENCE = {
    "present": 5,
    "partial": 4,
    "planned": 3,
    "absent": 2,
    "unknown": 1,
}

_ABSENT_RE = re.compile(
    r"\b("
    r"not supported|unsupported|does not support|do not support|doesn't support|"
    r"is not supported|isn't supported|not available|unavailable|not offered|"
    r"does not offer|no support for|cannot scan|can't scan|cannot detect|"
    r"not currently supported|not provided"
    r")\b",
    re.I,
)
_PLANNED_RE = re.compile(
    r"\b("
    r"planned|roadmap|proposal|proposed|coming soon|feature request|open issue|"
    r"epic|future support|not yet available|not yet supported|preview|beta|"
    r"private beta|public beta|early access"
    r")\b",
    re.I,
)
_PARTIAL_RE = re.compile(
    r"\b("
    r"partial|partially|limited|limited support|only supports|supports only|"
    r"selected ecosystems|subset|not all|some package managers|some ecosystems|"
    r"limited availability|available for .* only"
    r")\b",
    re.I,
)
_VENDOR_MARKETING_RE = re.compile(r"\b(marketing|vendor-stated|vendor stated)\b", re.I)


def normalize_state(value: Any) -> str | None:
    state = str(value or "").strip().lower()
    if state in COVERAGE_STATES:
        return state
    return None


def infer_state(
    *,
    text: str | None = None,
    title: str | None = None,
    url: str | None = None,
    doc_type: str | None = None,
    source_kind: str | None = None,
    dimension: str | None = None,
) -> tuple[str, float, str]:
    """Infer a conservative coverage state from evidence text.

    This never infers absence from silence. `absent` requires explicit negative
    wording in the evidence.
    """
    evidence = " ".join(
        part
        for part in (title or "", url or "", text or "")
        if str(part or "").strip()
    )
    if _has_state_signal(_ABSENT_RE, evidence, title=title, url=url, dimension=dimension):
        return "absent", 0.86, "explicit_negative_evidence"
    if _has_state_signal(_PLANNED_RE, evidence, title=title, url=url, dimension=dimension):
        return "planned", 0.78, "roadmap_or_prerelease_evidence"
    if _has_state_signal(_PARTIAL_RE, evidence, title=title, url=url, dimension=dimension):
        return "partial", 0.76, "limited_scope_evidence"

    source = str(source_kind or "").lower()
    doc = str(doc_type or "").lower()
    confidence = 0.82
    if source in {"docs", "vendor_site"} or doc in {"docs", "release_notes"}:
        confidence = 0.9
    elif doc in {"pricing", "company_fact"}:
        confidence = 0.84
    elif _VENDOR_MARKETING_RE.search(evidence):
        confidence = 0.68
    return "present", confidence, "positive_source_dimension_evidence"


def source_assertions(
    *,
    source_id: int,
    meta: Mapping[str, Any],
    synthesis: Mapping[str, Any] | None = None,
    text: str | None = None,
) -> list[dict[str, Any]]:
    synthesis = synthesis or {}
    default_axis = _clean_axis(meta.get("axis")) or _clean_axis(synthesis.get("axis"))
    default_dimension = normalize_dimension(
        meta.get("dimension"),
        axis=default_axis,
        title=meta.get("title"),
        url=meta.get("url"),
        text=synthesis.get("compiled") or text,
    )
    base = {
        "source_id": int(source_id),
        "competitor": meta.get("competitor"),
        "axis": default_axis or "both",
        "dimension": default_dimension,
    }

    assertions = _assertions_from_model(synthesis.get("coverage_assertions"), base, meta)
    if assertions:
        return assertions

    facts = synthesis.get("facts")
    if isinstance(facts, list):
        assertions = _assertions_from_facts(facts, base, meta, synthesis)
        if assertions:
            return assertions

    claim = _first_claim(synthesis.get("compiled") or text or meta.get("title"))
    return [_fallback_assertion(base, meta, synthesis, claim)]


def source_row_assertions(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    meta = {
        "competitor": row.get("competitor"),
        "axis": row.get("axis"),
        "dimension": row.get("dimension"),
        "title": row.get("title"),
        "url": row.get("url"),
        "doc_type": row.get("doc_type"),
        "source_kind": row.get("source_kind"),
    }
    return source_assertions(
        source_id=int(row["source_id"]),
        meta=meta,
        synthesis={
            "compiled": row.get("chunk_text") or row.get("title") or "",
            "axis": row.get("axis"),
        },
        text=row.get("chunk_text"),
    )


def rollup_assertions(assertions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not assertions:
        return {
            "state": "unknown",
            "confidence": 0.0,
            "active_assertions": 0,
            "strongest_source_id": None,
            "conflict": False,
            "states": {},
        }

    state_counts: dict[str, int] = {}
    normalized: list[dict[str, Any]] = []
    for assertion in assertions:
        state = normalize_state(assertion.get("state")) or "unknown"
        state_counts[state] = state_counts.get(state, 0) + 1
        normalized.append({**dict(assertion), "state": state})

    strongest = max(
        normalized,
        key=lambda item: (
            STATE_PRECEDENCE.get(str(item.get("state")), 0),
            float(item.get("confidence") or 0.0),
        ),
    )
    positive_states = {state for state in state_counts if state in {"present", "partial", "planned"}}
    conflict = "absent" in state_counts and bool(positive_states)
    return {
        "state": strongest["state"],
        "confidence": float(strongest.get("confidence") or 0.0),
        "active_assertions": len(normalized),
        "strongest_source_id": strongest.get("source_id"),
        "conflict": conflict,
        "states": state_counts,
    }


def missing_reason_for_state(state: str | None) -> str:
    if state == "absent":
        return "known_absent"
    if state == "planned":
        return "planned_only"
    if state == "partial":
        return "partial_coverage"
    return "unknown_coverage"


def _assertions_from_model(
    raw_assertions: Any,
    base: Mapping[str, Any],
    meta: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(raw_assertions, list):
        return []
    assertions: list[dict[str, Any]] = []
    for item in raw_assertions:
        if not isinstance(item, dict):
            continue
        assertion = _normalize_assertion(item, base, meta)
        if assertion is not None:
            assertions.append(assertion)
    return _dedupe_assertions(assertions)


def _assertions_from_facts(
    facts: list[Any],
    base: Mapping[str, Any],
    meta: Mapping[str, Any],
    synthesis: Mapping[str, Any],
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        claim = _first_claim(fact.get("claim"))
        if not claim:
            continue
        item = {
            "dimension": fact.get("dimension"),
            "state": None,
            "confidence": fact.get("confidence"),
            "claim": claim,
            "reason": "fact_state_inference",
        }
        assertion = _normalize_assertion(item, base, meta, synthesis=synthesis)
        if assertion is not None:
            assertions.append(assertion)
    return _dedupe_assertions(assertions)


def _fallback_assertion(
    base: Mapping[str, Any],
    meta: Mapping[str, Any],
    synthesis: Mapping[str, Any],
    claim: str | None,
) -> dict[str, Any]:
    state, confidence, reason = infer_state(
        text=" ".join(
            str(part or "")
            for part in (claim, synthesis.get("compiled"))
            if str(part or "").strip()
        ),
        title=_text(meta.get("title")),
        url=_text(meta.get("url")),
        doc_type=_text(meta.get("doc_type")),
        source_kind=_text(meta.get("source_kind")),
        dimension=_text(base.get("dimension")),
    )
    state = normalize_state(meta.get("evidence_state")) or state
    return {
        **dict(base),
        "state": state,
        "confidence": confidence,
        "claim": claim or "Source provides evidence for this dimension.",
        "reason": reason,
    }


def _normalize_assertion(
    item: Mapping[str, Any],
    base: Mapping[str, Any],
    meta: Mapping[str, Any],
    *,
    synthesis: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    axis = _clean_axis(item.get("axis")) or _text(base.get("axis")) or "both"
    dimension = _text(base.get("dimension"))
    if not dimension:
        dimension = normalize_dimension(
            item.get("dimension"),
            axis=axis if axis in {"technical", "business"} else None,
            title=meta.get("title"),
            url=meta.get("url"),
            text=item.get("claim") or (synthesis or {}).get("compiled"),
        )
    if not dimension:
        return None

    claim = _first_claim(item.get("claim")) or _first_claim((synthesis or {}).get("compiled"))
    if not claim:
        claim = "Source provides evidence for this dimension."

    state = normalize_state(item.get("state")) or normalize_state(meta.get("evidence_state"))
    inferred_state, inferred_confidence, inferred_reason = infer_state(
        text=" ".join(str(part or "") for part in (claim, item.get("reason"))),
        title=_text(meta.get("title")),
        url=_text(meta.get("url")),
        doc_type=_text(meta.get("doc_type")),
        source_kind=_text(meta.get("source_kind")),
        dimension=dimension,
    )
    state = state or inferred_state
    confidence = _confidence(item.get("confidence"), default=inferred_confidence)
    return {
        "source_id": int(base["source_id"]),
        "competitor": _text(base.get("competitor")) or _text(meta.get("competitor")),
        "axis": axis,
        "dimension": dimension,
        "state": state,
        "confidence": confidence,
        "claim": claim,
        "reason": _text(item.get("reason")) or inferred_reason,
    }


def _confidence(value: Any, *, default: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(confidence, 1.0))


def _has_state_signal(
    pattern: re.Pattern[str],
    evidence: str,
    *,
    title: str | None,
    url: str | None,
    dimension: str | None,
) -> bool:
    if not pattern.search(evidence):
        return False
    if pattern is _ABSENT_RE and len(evidence) > 300:
        return False
    if len(evidence) <= 300 or not dimension:
        return True

    terms = _dimension_terms(dimension)
    if not terms:
        return False

    context = f"{title or ''} {url or ''}".lower()
    context_mentions_dimension = _mentions_dimension(context, terms, dimension)
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", evidence):
        if not pattern.search(sentence):
            continue
        lowered = sentence.lower()
        if _mentions_dimension(lowered, terms, dimension):
            return True
        if context_mentions_dimension and re.search(
            r"\b(this|the)\s+(feature|capability|support|product)\b",
            lowered,
        ):
            return True
    return False


def _mentions_dimension(text: str, terms: set[str], dimension: str) -> bool:
    phrase = dimension.replace("_", " ").lower()
    if phrase and phrase in text:
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


def _dimension_terms(dimension: str | None) -> set[str]:
    generic = {
        "analysis",
        "capabilities",
        "capability",
        "company",
        "contextual",
        "detection",
        "generation",
        "management",
        "model",
        "portfolio",
        "product",
        "security",
        "software",
        "support",
        "supported",
    }
    return {
        term
        for term in re.split(r"[_\W]+", str(dimension or "").lower())
        if len(term) > 2 and term not in generic
    }


def _dedupe_assertions(assertions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for assertion in assertions:
        key = (
            assertion.get("source_id"),
            assertion.get("competitor"),
            assertion.get("axis"),
            assertion.get("dimension"),
            assertion.get("state"),
            assertion.get("claim"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(assertion))
    return deduped


def _first_claim(value: Any, limit: int = 600) -> str | None:
    text = _text(value)
    if not text:
        return None
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _clean_axis(value: Any) -> str | None:
    axis = _text(value)
    if axis in {"technical", "business", "both"}:
        return axis
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "COVERAGE_STATES",
    "STATE_PRECEDENCE",
    "infer_state",
    "missing_reason_for_state",
    "normalize_state",
    "rollup_assertions",
    "source_assertions",
    "source_row_assertions",
]
