# Repo Structure

This document explains where the important parts of CI Engine live and where to make changes safely.

## Top-Level Layout

```text
.
├── CLAUDE.md
├── README.md
├── docs/
├── ops/
├── pyproject.toml
├── raw_snapshots/
├── src/
├── tests/
└── uv.lock
```

- `CLAUDE.md` - operating constitution. It defines core rules such as grounded answers, config as source of truth, and prompt storage in skills.
- `README.md` - project entry point.
- `docs/` - deeper documentation for architecture, business context, AI/model behavior, operations, and repo structure.
- `ops/` - operational or deployment assets if present.
- `pyproject.toml` - package metadata and dependencies.
- `uv.lock` - locked Python dependency graph.
- `raw_snapshots/` - saved source snapshots for provenance. These are evidence artifacts, not the query-time source of truth.
- `src/` - application code.
- `tests/` - unit and integration-style tests.
- `deep_map.log` - local run log. It is useful for debugging but not canonical knowledge.

## Application Package

The application package is `src/ci_engine/`.

```text
src/ci_engine/
├── acquire/
├── chat/
├── crews/
├── db/
├── embed/
├── mcp/
├── retrieve/
├── skills/
├── synthesize/
├── config.py
├── config.yaml
├── dimension_coverage.py
├── llm_json.py
├── ontology.py
└── secrets.py
```

## Package Responsibilities

### `acquire/`

Acquisition lanes collect candidate evidence before ingestion.

- `web_lane.py` generates official-style deep company research reports with Anthropic web search, splits reports into ontology-scoped candidates, fetches pages, extracts text, and normalizes citations.
- `tavily_lane.py` searches the web through Tavily and snapshots raw results.
- `context7_lane.py` queries Context7 documentation through its MCP API.
- `company_profile_lane.py` supports company-profile acquisition.
- `snapshots.py` writes raw source snapshots used for provenance.
- `relevance.py` asks the relevance model whether a candidate should be ingested.

Acquisition is the normal internet-facing layer for permanent DB evidence. Competitive reports may also use Tavily for run-scoped validation/enrichment. Retrieval should not browse.

### `db/`

Database and maintenance code.

- `connection.py` builds the Cloud SQL SQLAlchemy engine using IAM authentication and optional service-account impersonation.
- `schema.sql` defines tables, indexes, grants, and pgvector setup.
- `repository.py` contains DB reads/writes, vector search, active chunks, coverage rollups, source updates, and audit writes.
- `doctor.py` prints DB connection and ADC diagnostics.
- `heal_dimensions.py` canonicalizes source dimensions and marks known-bad sources stale.
- `heal_coverage_status.py` backfills source-level coverage assertions and rollup statuses.

DB changes should preserve content and provenance. Use audit tables for metadata/status changes.

### `embed/`

Embedding code.

- `gemini.py` calls Vertex AI with `gemini-embedding-001`.
- Document embeddings use `RETRIEVAL_DOCUMENT`.
- Query embeddings use `RETRIEVAL_QUERY`.
- Embedding dimensionality is controlled by `config.yaml` and currently set to 1536.

### `mcp/`

MCP server exposing retrieval tools.

- `server.py` defines read-only retrieval and report-support tools, including `search`, `get_competitor`, `compare_competitors`, `latest_updates`, `coverage_status`, `coverage_matrix`, `find_evidence_gaps`, `get_source_detail`, `source_inventory`, `build_report_section_evidence`, `build_capability_evidence_matrix`, and `build_report_evidence_pack`.
- The server uses streamable HTTP at `/mcp`.
- Optional `MCP_SHARED_TOKEN` protects deployed/local HTTP access.
- Host/origin validation is enabled through MCP transport security settings.

Report-support MCP tools are DB-backed and do not browse. They batch evidence retrieval for report sections and product capabilities.

Chat-facing MCP tools are also read-only:

- `get_report_registry` lists generated reports, validation status, generated time, and PDF availability.
- `search_report_sections` searches generated report sections, scores, missing-data notes, and validation findings.
- `search_answer_context` provides one fast answer-context call over DB evidence plus optional report artifacts.

### `chat/`

Skill-guided chat orchestration.

- `schemas.py` defines `ChatRequest`, retrieval plans, evidence items, web checks, answers, and grounding findings.
- `planner.py` builds a strict retrieval plan with Haiku by default and a deterministic fallback for testability.
- `retrieval.py` executes approved read-only MCP calls and normalizes results.
- `web.py` performs automatic bounded Tavily `ultra-fast` or `fast` checks without writing snapshots or mutating the DB.
- `answer.py` writes concise grounded answers and validates citations.
- `report_store.py` abstracts filesystem report artifacts under `reports/<slug>/`.
- `service.py` orchestrates planner, strengthened retrieval, autonomous web enrichment, answer writer, and grounding metadata.

### `ui/`

FastAPI report console and chat UI.

- `app.py` defines `GET /`, report artifact APIs, PDF download behavior, `POST /api/chat`, and `WS /ws/chat`.
- `templates/console.html.j2` is the report viewer shell.
- `static/app.js` handles report selection and chat interaction.
- `static/styles.css` contains the light executive UI styling.

Run locally with:

```bash
.venv/bin/python -m ci_engine.ui
```

### `crews/`

CrewAI workflows and report-generation code.

Current report package:

- `crews/report/run.py` - command-line report entry point.
- `crews/report/workflow.py` - EvidencePack, analyst draft, checker, and render orchestration.
- `crews/report/evidence.py` - DB/Tavily evidence collection, batch MCP adapters, EvidencePack creation.
- `crews/report/capabilities.py` - product catalog and capability evidence matrix logic.
- `crews/report/strategy.py`, `market.py`, `product_feature.py`, `technical.py`, `buyer_field.py`, `scoring.py` - analyst prompt inputs, live model calls, parsers, and section conversion.
- `crews/report/checker.py` - validation rules that block unsupported or unsafe report output.
- `crews/report/renderer.py` - HTML, JSON, and PDF artifact rendering.
- `crews/report/templates/` - Jinja templates for report presentation.
- `crews/report/schemas.py` - Pydantic contracts for evidence, sections, scores, validation, and render results.

Generated reports are written under `reports/<competitor-slug>/`.

### `retrieve/`

Read-only retrieval API.

- Embeds the query.
- Expands dimension aliases.
- Calls repository vector search over active chunks.
- Balances multi-company retrieval with per-company quotas.
- Returns chunks and `missing` coverage only for explicitly requested dimensions.

This layer should not write to the database or use the web.

### `skills/`

Packaged model instructions.

Current skills include:

- `deep-company-research`
- `deep-report-splitter`
- `ingest-synthesis`
- `relevance-rubric`
- `coverage-verdict`
- `grounding-contract`
- `neutral-ci-contract`
- `chat-query-planner`
- `chat-mcp-tool-use`
- `chat-answer-writer`
- `chat-web-depth-selector`
- `chat-grounding-checker`
- `report-db-retrieval`
- `report-evidence-quality`
- `report-extensive-web-search`
- `report-targeted-validation`
- `report-evidence-pack-builder`
- `report-market-cross-report`
- `report-framework-pestel`
- `report-framework-five-forces`
- `report-framework-positioning-map`
- `report-framework-swot`
- `report-confidence-tiering`
- `report-strategy-analyst`
- `report-market-analyst`
- `report-product-feature-analyst`
- `report-technical-analyst`
- `report-buyer-field-analyst`
- `report-scoring-agent`
- `report-checker`
- `report-editor-auditor`

Prompts should be changed here, not in Python code.

### `synthesize/`

Ingestion and synthesis workflows.

- `deep_map.py` iterates configured companies and ontology dimensions, gathers candidates, and ingests them.
- `pipeline.py` ingests one candidate into sources, citations, chunks, embeddings, entities, relationships, and coverage assertions.
- `compiler.py` uses the synthesis model to compile raw text into structured evidence.
- `coverage_verdict.py` classifies candidate evidence for targeted coverage gaps.
- `close_coverage_scope.py` researches unknown, planned, or partial gaps and ingests accepted evidence.
- `discover.py` contains discovery helpers.
- `run.py` ingests a single URL from the command line.

### Core Modules

- `config.py` loads `config.yaml`.
- `config.yaml` is the single source of tunable config.
- `ontology.py` contains canonical dimensions, aliases, axis lookup, and normalization logic.
- `dimension_coverage.py` defines coverage states, inference rules, missing reasons, assertion extraction, and rollup precedence.
- `llm_json.py` parses JSON objects from model responses.
- `secrets.py` reads secrets from GCP Secret Manager.

## Tests

`tests/` covers:

- configuration
- database connection behavior
- repository behavior
- ontology normalization
- dimension coverage rollups
- retrieval
- MCP server formatting/tools
- acquisition lanes
- relevance and synthesis parsing
- healing/backfill CLIs
- coverage verdict guards

Run all tests:

```bash
.venv/bin/python -m pytest
```

## Where To Change Things

- Add or remove tracked competitors: `src/ci_engine/config.yaml`.
- Add ontology dimensions: `config.yaml`, then update `ontology.py` aliases/normalization if needed.
- Change model choices or thresholds: `config.yaml`.
- Change AI instructions: `src/ci_engine/skills/*/SKILL.md`.
- Change ingestion behavior: `src/ci_engine/synthesize/pipeline.py`.
- Change deep-map collection behavior: `src/ci_engine/synthesize/deep_map.py` and acquisition lanes.
- Change scope-closure behavior: `src/ci_engine/synthesize/close_coverage_scope.py` and `coverage_verdict.py`.
- Change retrieval behavior: `src/ci_engine/retrieve/__init__.py` and repository vector-search functions.
- Change report retrieval behavior: `src/ci_engine/mcp/server.py` and `src/ci_engine/crews/report/evidence.py`.
- Change report analysis behavior: `src/ci_engine/skills/report-*/SKILL.md` and the matching `src/ci_engine/crews/report/*.py` parser/section module.
- Change report presentation: `src/ci_engine/crews/report/templates/` and `src/ci_engine/crews/report/renderer.py`.
- Change schema: `src/ci_engine/db/schema.sql`, then apply it to Cloud SQL.

## What Not To Change Casually

- Do not hardcode model names outside `config.yaml`.
- Do not write prompt strings directly in Python modules.
- Do not delete sources/chunks to heal coverage.
- Do not mark `absent` from missing search results.
- Do not add web access to retrieval.
- Do not bypass audit tables for DB healing/status changes.
