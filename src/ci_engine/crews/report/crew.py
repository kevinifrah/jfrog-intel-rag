from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ci_engine.skills import compose, load_skill

REPORT_AGENT_SKILLS = {
    "db_retrieval": "report-db-retrieval",
    "evidence_quality": "report-evidence-quality",
    "extensive_web_search": "report-extensive-web-search",
    "targeted_validation": "report-targeted-validation",
    "evidence_pack_builder": "report-evidence-pack-builder",
    "strategy_analyst": "report-strategy-analyst",
    "market_analyst": "report-market-analyst",
    "product_feature_analyst": "report-product-feature-analyst",
    "technical_analyst": "report-technical-analyst",
    "buyer_field_analyst": "report-buyer-field-analyst",
    "scoring_agent": "report-scoring-agent",
    "report_checker": "report-checker",
    "editor_auditor": "report-editor-auditor",
}

GROUNDED_AGENT_KEYS = {
    "strategy_analyst",
    "market_analyst",
    "product_feature_analyst",
    "technical_analyst",
    "buyer_field_analyst",
    "scoring_agent",
    "report_checker",
    "editor_auditor",
}


def load_agent_skill(agent_key: str) -> str:
    skill_name = REPORT_AGENT_SKILLS[agent_key]
    if agent_key in GROUNDED_AGENT_KEYS:
        return compose("grounding-contract", "neutral-ci-contract", skill_name)
    return load_skill(skill_name)


def assert_report_skills_available() -> None:
    for agent_key in REPORT_AGENT_SKILLS:
        body = load_agent_skill(agent_key)
        if not body.strip():
            raise RuntimeError(f"Empty report skill for agent {agent_key}")


def create_report_crew() -> Any:
    """Create a CrewAI skeleton for the report workflow.

    The deterministic report workflow is used for tests. This factory provides
    the real CrewAI integration point and keeps CrewAI imports lazy so normal
    unit tests do not initialize CrewAI storage.
    """

    _ensure_crewai_storage()
    from crewai import Agent, Crew, Process, Task  # noqa: PLC0415

    agents = {
        key: Agent(
            role=key.replace("_", " ").title(),
            goal=f"Execute the {key} step for the JFrog competitive report.",
            backstory=load_agent_skill(key),
            verbose=True,
        )
        for key in REPORT_AGENT_SKILLS
    }
    tasks = [
        Task(
            description=(
                "Run the competitive report step using only the inputs supplied "
                "by the orchestrator and return the required structured output."
            ),
            expected_output="Structured JSON matching the report step contract.",
            agent=agent,
        )
        for agent in agents.values()
    ]
    return Crew(
        agents=list(agents.values()),
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
    )


def _ensure_crewai_storage() -> None:
    base = Path(os.environ.get("CREWAI_STORAGE_BASE", "/tmp/ci-engine-crewai"))
    base.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CREWAI_STORAGE_DIR", "ci-engine-crewai")
    os.environ.setdefault("CREWAI_TESTING", "true")
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    try:
        import appdirs  # noqa: PLC0415

        appdirs.user_data_dir = lambda appname=None, appauthor=None, *args, **kwargs: str(
            base / "data" / (appname or "crewai")
        )
        appdirs.user_cache_dir = lambda appname=None, appauthor=None, *args, **kwargs: str(
            base / "cache" / (appname or "crewai")
        )
    except Exception:
        pass
    try:
        from crewai_core.token_manager import TokenManager  # noqa: PLC0415

        credentials_dir = base / "credentials"
        credentials_dir.mkdir(parents=True, exist_ok=True)
        TokenManager._get_secure_storage_path = staticmethod(lambda: credentials_dir)
    except Exception:
        pass


__all__ = [
    "REPORT_AGENT_SKILLS",
    "assert_report_skills_available",
    "create_report_crew",
    "load_agent_skill",
]
