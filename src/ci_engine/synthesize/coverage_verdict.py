from __future__ import annotations

import json
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from ci_engine.config import get as config_get
from ci_engine.dimension_coverage import infer_state, normalize_state
from ci_engine.llm_json import parse_json_object
from ci_engine.secrets import get_secret
from ci_engine.skills import load_skill

VERDICT_STATES = (
    "present",
    "partial",
    "planned",
    "explicit_absent",
    "irrelevant",
    "still_unknown",
    "needs_review",
)
ACCEPTED_VERDICTS = {"present", "partial", "planned", "explicit_absent"}

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


def classify_candidate(
    candidate: dict[str, Any],
    gap: dict[str, Any],
    *,
    review_absent: bool = False,
) -> dict[str, Any]:
    deterministic = _dimension_guard_verdict(candidate, gap) or _deterministic_verdict(
        candidate,
        gap,
    )
    verdict = deterministic or _llm_verdict(candidate, gap)
    return normalize_verdict(
        verdict,
        candidate=candidate,
        gap=gap,
        review_absent=review_absent,
    )


def normalize_verdict(
    verdict: dict[str, Any],
    *,
    candidate: dict[str, Any],
    gap: dict[str, Any],
    review_absent: bool = False,
) -> dict[str, Any]:
    state = _clean_state(verdict.get("state"))
    confidence = _confidence(verdict.get("confidence"))
    evidence = _text(verdict.get("evidence")) or _candidate_excerpt(candidate)
    reason = _text(verdict.get("reason")) or "classified evidence"
    trust = source_trust(candidate, str(gap.get("competitor") or ""))

    if state == "explicit_absent":
        if review_absent or trust != "official" or confidence < 0.8:
            state = "needs_review"
            reason = "explicit absent requires review"
    elif state in {"present", "partial", "planned"} and confidence < 0.45:
        state = "needs_review"
        reason = "low confidence positive evidence"

    return {
        "state": state,
        "confidence": confidence,
        "evidence": evidence,
        "reason": reason,
        "url": candidate.get("url"),
        "title": candidate.get("title"),
        "source_kind": candidate.get("source_kind"),
        "source_trust": trust,
    }


def should_ingest(verdict: dict[str, Any]) -> bool:
    return str(verdict.get("state")) in ACCEPTED_VERDICTS


def evidence_state(verdict: dict[str, Any]) -> str | None:
    state = str(verdict.get("state") or "")
    if state == "explicit_absent":
        return "absent"
    return normalize_state(state)


def candidate_with_verdict(
    candidate: dict[str, Any],
    gap: dict[str, Any],
    verdict: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(candidate)
    updated["axis"] = gap.get("axis")
    updated["dimension"] = gap.get("dimension")
    updated["evidence_state"] = evidence_state(verdict)
    updated["coverage_gap"] = {
        "competitor": gap.get("competitor"),
        "axis": gap.get("axis"),
        "dimension": gap.get("dimension"),
    }
    updated["coverage_verdict"] = verdict
    return updated


def source_trust(candidate: dict[str, Any], competitor: str) -> str:
    url = str(candidate.get("url") or "")
    source_kind = str(candidate.get("source_kind") or "").lower()
    if source_kind == "official_llm_research_report":
        return "official"
    if _is_official_host(url, competitor):
        return "official"
    if source_kind in {"docs", "release_notes", "pricing", "vendor_site"} and _is_official_host(url, competitor):
        return "official"
    return "third_party"


def _deterministic_verdict(
    candidate: dict[str, Any],
    gap: dict[str, Any],
) -> dict[str, Any] | None:
    evidence = _candidate_evidence(candidate)
    if not evidence:
        return {
            "state": "still_unknown",
            "confidence": 0.0,
            "evidence": "",
            "reason": "no candidate evidence",
        }

    state, confidence, reason = infer_state(
        text=evidence,
        title=_text(candidate.get("title")),
        url=_text(candidate.get("url")),
        doc_type=_text(candidate.get("doc_type")),
        source_kind=_text(candidate.get("source_kind")),
        dimension=_text(gap.get("dimension")),
    )
    if state == "absent":
        return {
            "state": "explicit_absent",
            "confidence": confidence,
            "evidence": _candidate_excerpt(candidate),
            "reason": reason,
        }
    if state in {"planned", "partial"}:
        return {
            "state": state,
            "confidence": confidence,
            "evidence": _candidate_excerpt(candidate),
            "reason": reason,
        }
    return None


def _dimension_guard_verdict(
    candidate: dict[str, Any],
    gap: dict[str, Any],
) -> dict[str, Any] | None:
    dimension = _text(gap.get("dimension"))
    evidence = _candidate_evidence(candidate).lower()
    if dimension == "supported_ecosystems" and _is_supported_package_ecosystem_evidence(
        candidate,
        gap,
        evidence,
    ):
        return {
            "state": "present",
            "confidence": 0.9,
            "evidence": _candidate_excerpt(candidate),
            "reason": "source lists supported package or artifact ecosystems",
        }
    if dimension == "supported_ecosystems" and _is_partner_ecosystem_page(evidence):
        return {
            "state": "irrelevant",
            "confidence": 0.95,
            "evidence": _candidate_excerpt(candidate),
            "reason": "partner ecosystem is business evidence, not technical ecosystem support",
        }
    if dimension == "package_firewall" and _is_jfrog_curation_package_firewall(
        candidate,
        gap,
        evidence,
    ):
        return {
            "state": "present",
            "confidence": 0.9,
            "evidence": _candidate_excerpt(candidate),
            "reason": "JFrog Curation blocks or controls package downloads",
        }
    if dimension == "package_firewall" and _is_package_named_firewall_advisory(evidence):
        return {
            "state": "irrelevant",
            "confidence": 0.95,
            "evidence": _candidate_excerpt(candidate),
            "reason": "package advisory for package named firewall is not package-firewall coverage",
        }
    if dimension == "package_firewall" and _is_third_party_package_firewall_integration(
        candidate,
        gap,
        evidence,
    ):
        return {
            "state": "irrelevant",
            "confidence": 0.95,
            "evidence": _candidate_excerpt(candidate),
            "reason": "third-party firewall integration is not target company coverage",
        }
    if dimension == "software_distribution" and _is_vendor_tool_rollout_documentation(evidence):
        return {
            "state": "irrelevant",
            "confidence": 0.95,
            "evidence": _candidate_excerpt(candidate),
            "reason": "vendor-tool rollout is not customer software distribution coverage",
        }
    if dimension == "impact_analysis" and _is_non_security_impact_analytics(evidence):
        return {
            "state": "irrelevant",
            "confidence": 0.95,
            "evidence": _candidate_excerpt(candidate),
            "reason": "business or AI ROI analytics are not technical impact analysis",
        }
    if dimension == "ai_model_scanning" and _is_not_ai_model_scanning(evidence):
        return {
            "state": "irrelevant",
            "confidence": 0.95,
            "evidence": _candidate_excerpt(candidate),
            "reason": "source is not evidence of AI model security scanning",
        }
    if dimension == "edge_node_delivery" and _is_not_edge_node_delivery(evidence):
        return {
            "state": "irrelevant",
            "confidence": 0.95,
            "evidence": _candidate_excerpt(candidate),
            "reason": "source does not prove edge-node delivery coverage",
        }
    return None


def _is_partner_ecosystem_page(evidence: str) -> bool:
    return any(
        marker in evidence
        for marker in (
            "/partners",
            "partner ecosystem",
            "cloud alliances",
            "technology partners",
            "consulting partners",
        )
    )


def _is_supported_package_ecosystem_evidence(
    candidate: dict[str, Any],
    gap: dict[str, Any],
    evidence: str,
) -> bool:
    if source_trust(candidate, str(gap.get("competitor") or "")) != "official":
        return False
    if not any(
        marker in evidence
        for marker in (
            "package format",
            "package formats",
            "package type",
            "package types",
            "package technology type",
            "package technology types",
            "artifact type",
            "artifact types",
            "package manager",
            "package managers",
        )
    ):
        return False
    ecosystem_terms = {
        "npm",
        "maven",
        "pypi",
        "nuget",
        "docker",
        "go",
        "helm",
        "cargo",
        "debian",
        "rpm",
        "terraform",
        "composer",
        "conan",
        "gradle",
    }
    if any(term in evidence for term in ecosystem_terms):
        return True
    return "native support" in evidence or "supports 40+" in evidence


def _is_jfrog_curation_package_firewall(
    candidate: dict[str, Any],
    gap: dict[str, Any],
    evidence: str,
) -> bool:
    if str(gap.get("competitor") or "") != "JFrog":
        return False
    if source_trust(candidate, "JFrog") != "official":
        return False
    if "curation" not in evidence:
        return False
    return any(
        marker in evidence
        for marker in (
            "block download",
            "block downloads",
            "block packages",
            "block open-source packages",
            "allow, block, or flag",
            "controls which open-source packages enter",
            "before they reach developers",
            "prevent packages",
            "package download",
        )
    )


def _is_package_named_firewall_advisory(evidence: str) -> bool:
    if "package firewall" in evidence:
        return False
    if "/package/" not in evidence or "firewall" not in evidence:
        return False
    advisory_markers = (
        "package health score",
        "latest version",
        "latest non-vulnerable version",
        "licenses:",
        "published:",
        "# firewall",
    )
    return any(marker in evidence for marker in advisory_markers)


def _is_third_party_package_firewall_integration(
    candidate: dict[str, Any],
    gap: dict[str, Any],
    evidence: str,
) -> bool:
    if source_trust(candidate, str(gap.get("competitor") or "")) == "official":
        return False
    if "package firewall" not in evidence:
        return False
    return any(
        marker in evidence
        for marker in (
            "artifact repositories",
            "artifact repository",
            "artifactory",
            "connect package firewall",
            "integrations/package-firewall",
            "remote source",
            "uses the package firewall",
        )
    )


def _is_vendor_tool_rollout_documentation(evidence: str) -> bool:
    rollout_markers = (
        "distribution at scale",
        "distribution team",
        "mdm",
        "jamf",
        "intune",
        "installer",
        "install script",
        "installing and maintaining",
        "deployment targets",
        "managed utility",
        "omnibus packages",
        "helm charts",
        "operators",
        "aws marketplace",
    )
    vendor_tool_markers = (
        "snyk studio",
        "evo by snyk",
        "coding assistant",
        "claude code",
        "codex cli",
        "cursor",
        "gemini cli",
        "secure at inception directives",
        "gitlab is packaged and deployed",
        "installing and maintaining gitlab",
        "gitlab components",
        "omnibus gitlab",
        "distribution:build",
        "distribution:deploy",
    )
    release_distribution_markers = (
        "release bundle",
        "artifact distribution",
        "software release distribution",
        "distribute artifacts",
        "release pipeline",
        "edge node",
    )
    return (
        any(marker in evidence for marker in rollout_markers)
        and any(marker in evidence for marker in vendor_tool_markers)
        and not any(marker in evidence for marker in release_distribution_markers)
    )


def _is_non_security_impact_analytics(evidence: str) -> bool:
    return any(
        marker in evidence
        for marker in (
            "business impact analysis",
            "ai impact analytics dashboard",
            "roi of ai",
            "code suggestions usage rate",
            "cycle time",
            "lead time",
            "deployment frequency",
        )
    )


def _is_not_ai_model_scanning(evidence: str) -> bool:
    if any(
        marker in evidence
        for marker in (
            "ai model validation",
            "model validation framework",
            "model evaluation",
            "evaluates ai models for its ai-powered features",
        )
    ):
        return True
    if any(
        marker in evidence
        for marker in (
            "ai-powered managed security services",
            "ai code quality security",
            "scans code continuously",
            "code scanning",
        )
    ) and not any(
        marker in evidence
        for marker in (
            "model artifact",
            "model artifacts",
            "model registry",
            "hugging face",
            "ml model",
            "ai model vulnerability",
            "ai model risk",
        )
    ):
        return True
    return False


def _is_not_edge_node_delivery(evidence: str) -> bool:
    if any(
        marker in evidence
        for marker in (
            "project cassini",
            "k3s and gitlab",
            "third-party documentation that references gitlab as a component",
            "rancher k3s",
            "arm white paper",
        )
    ):
        return True
    return (
        "gitlab delivery stage" in evidence
        and "saas" in evidence
        and "self-managed" in evidence
        and "dedicated" in evidence
    )


def _llm_verdict(candidate: dict[str, Any], gap: dict[str, Any]) -> dict[str, Any]:
    response = _client().messages.create(
        model=str(config_get("models.relevance.name", "claude-haiku-4-5")),
        system=load_skill("coverage-verdict"),
        messages=[
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "gap": {
                            "competitor": gap.get("competitor"),
                            "axis": gap.get("axis"),
                            "dimension": gap.get("dimension"),
                        },
                        "candidate": {
                            "title": candidate.get("title"),
                            "url": candidate.get("url"),
                            "snippet": candidate.get("snippet"),
                            "text_excerpt": _text_excerpt(candidate.get("text")),
                            "source_kind": candidate.get("source_kind"),
                        },
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    default=str,
                ),
            }
        ],
        max_tokens=1000,
        temperature=0.0,
        timeout=float(config_get("ingestion.llm_timeout_s", 30)),
    )
    return _parse_verdict(_response_text(response))


def _parse_verdict(text: str) -> dict[str, Any]:
    parsed = parse_json_object(text, label="coverage verdict model")
    parsed["state"] = _clean_state(parsed.get("state"))
    parsed["confidence"] = _confidence(parsed.get("confidence"))
    parsed["evidence"] = _text(parsed.get("evidence")) or ""
    parsed["reason"] = _text(parsed.get("reason")) or "classified evidence"
    return parsed


def _clean_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    if state not in VERDICT_STATES:
        raise ValueError(f"coverage verdict has invalid state: {state or value!r}")
    return state


@lru_cache(maxsize=1)
def _client() -> Any:
    from anthropic import Anthropic  # noqa: PLC0415

    return Anthropic(api_key=get_secret("anthropic-key"), max_retries=0)


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
        raise ValueError("coverage verdict model returned no text")
    return text


def _candidate_evidence(candidate: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _text(candidate.get("title")),
            _text(candidate.get("url")),
            _text(candidate.get("snippet")),
            _text(candidate.get("text")),
        )
        if part
    )


def _candidate_excerpt(candidate: dict[str, Any], limit: int = 800) -> str:
    text = _candidate_evidence(candidate)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _text_excerpt(value: Any, limit: int = 4000) -> str:
    text = _text(value) or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))


def _is_official_host(url: str, competitor: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if not host:
        return False
    hints = _COMPETITOR_HOST_HINTS.get(competitor, ())
    return any(hint in host for hint in hints)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "ACCEPTED_VERDICTS",
    "VERDICT_STATES",
    "candidate_with_verdict",
    "classify_candidate",
    "evidence_state",
    "normalize_verdict",
    "should_ingest",
    "source_trust",
]
