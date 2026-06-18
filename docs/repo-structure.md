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

Only acquisition should touch the internet. Retrieval should not browse.

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

- `server.py` defines `search`, `get_competitor`, `compare_competitors`, `latest_updates`, and `coverage_status`.
- The server uses streamable HTTP at `/mcp`.
- Optional `MCP_SHARED_TOKEN` protects deployed/local HTTP access.
- Host/origin validation is enabled through MCP transport security settings.

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
- Change schema: `src/ci_engine/db/schema.sql`, then apply it to Cloud SQL.

## What Not To Change Casually

- Do not hardcode model names outside `config.yaml`.
- Do not write prompt strings directly in Python modules.
- Do not delete sources/chunks to heal coverage.
- Do not mark `absent` from missing search results.
- Do not add web access to retrieval.
- Do not bypass audit tables for DB healing/status changes.

