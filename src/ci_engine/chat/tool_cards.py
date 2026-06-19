from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCard:
    name: str
    purpose: str
    best_for: str
    required_args: tuple[str, ...]
    optional_args: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()

    def as_prompt_line(self) -> str:
        required = ", ".join(self.required_args) or "none"
        optional = ", ".join(self.optional_args) or "none"
        failures = "; ".join(self.failure_modes) or "no special failure modes"
        return (
            f"- {self.name}: {self.purpose} Best for: {self.best_for} "
            f"Required args: {required}. Optional args: {optional}. "
            f"Failure modes: {failures}."
        )


CHAT_TOOL_CARDS: tuple[ToolCard, ...] = (
    ToolCard(
        name="search_answer_context",
        purpose="Fast DB-plus-report retrieval for chat answers.",
        best_for="default Q&A, product capability, market, technical, and comparison questions",
        required_args=("query",),
        optional_args=("competitors", "dimensions", "include_reports", "max_items"),
        failure_modes=("may return missing evidence when DB coverage is weak",),
    ),
    ToolCard(
        name="search_report_sections",
        purpose="Search generated report sections, scores, missing-data notes, and validation findings.",
        best_for="questions about a selected report, PDF blockers, scores, findings, or previous analysis",
        required_args=("query",),
        optional_args=("competitors", "sections", "max_items", "report_root"),
        failure_modes=("only sees generated report artifacts on disk",),
    ),
    ToolCard(
        name="get_report_registry",
        purpose="List generated reports and artifact status.",
        best_for="available reports, generated time, validation passed/failed, PDF availability",
        required_args=(),
        optional_args=("report_root",),
        failure_modes=("missing files appear as unavailable artifacts",),
    ),
    ToolCard(
        name="search",
        purpose="Narrow DB retrieval over active cited chunks.",
        best_for="precise lookup when company and dimensions are already known",
        required_args=("query",),
        optional_args=("axis", "competitors", "dimensions"),
        failure_modes=("vector search can miss exact facts without good query terms",),
    ),
    ToolCard(
        name="compare_dimension",
        purpose="Side-by-side DB evidence for one ontology dimension.",
        best_for="focused JFrog-vs-competitor comparisons such as SBOM or artifact management",
        required_args=("names", "dimension"),
        failure_modes=("single-dimension only",),
    ),
    ToolCard(
        name="coverage_matrix",
        purpose="Coverage, freshness, confidence, and conflict status by company and dimension.",
        best_for="explaining whether the corpus is strong enough to answer",
        required_args=(),
        optional_args=("competitors", "dimensions"),
        failure_modes=("coverage status is not the answer content itself",),
    ),
    ToolCard(
        name="source_inventory",
        purpose="List available active sources by company and dimension.",
        best_for="auditing source availability or deciding whether data exists",
        required_args=(),
        optional_args=("competitors", "dimensions", "limit"),
        failure_modes=("does not include full chunk text",),
    ),
    ToolCard(
        name="get_source_detail",
        purpose="Fetch metadata and chunks for known source IDs.",
        best_for="drilling into citations after source IDs are known",
        required_args=("source_ids",),
        failure_modes=("not useful without valid numeric source IDs",),
    ),
)


def tool_cards_prompt() -> str:
    return "Available read-only retrieval tools:\n" + "\n".join(
        card.as_prompt_line() for card in CHAT_TOOL_CARDS
    )


def tool_card_names() -> set[str]:
    return {card.name for card in CHAT_TOOL_CARDS}
