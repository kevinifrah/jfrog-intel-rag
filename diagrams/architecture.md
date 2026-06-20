# Software Architecture Documentation

## Overview

The **CI Engine** is a grounded, citation-strict **RAG** (retrieval-augmented
generation) system that builds and serves a competitive-intelligence knowledge
base about JFrog and its competitors in the software-supply-chain-security space.
The system is built around one hard architectural rule: **data flows in a single
direction**. An *acquisition* layer is the only code permitted to touch the
internet; everything downstream (retrieval, chat, reports) reads exclusively from
the Cloud SQL database. Every emitted fact must cite a stored source whose
freshness status is `active`; if no active evidence exists, the system says
"no recent data found" rather than guessing.

Architecturally the codebase is a single Python package (`src/ci_engine/`) that
exposes **three runtime entry points** — a deep-map/ingestion CLI, a read-only
MCP server (FastMCP over streamable HTTP), and a FastAPI web console — plus
two batch CLIs (report generator, healing tools). All model instructions live as
`SKILL.md` assets under `skills/`, and all tunables live in `config.yaml`. The
dominant patterns are: **one-directional ETL into a vector store**, a
**read-only tool surface (MCP)** that every reader shares, and an
**evidence-pack + multi-agent-draft + validator** pipeline for report generation.

## System Context

```
            ┌─────────────────────── INTERNET (acquisition only) ───────────────────────┐
            │  Anthropic web_search · Tavily · Context7 · direct HTTP fetch/RSS          │
            └───────────────┬──────────────────────────────────────────────┬────────────┘
                            │ writes (one direction)                        │ (report-time Tavily validation)
                            ▼                                               │
   configured companies ─► acquire/ ─► synthesize/ ─► db/ (Cloud SQL + pgvector) ◄── read-only
   + ontology (config)      lanes      pipeline        SINGLE SOURCE OF TRUTH      │
                                                          ▲     ▲     ▲            │
                                       ┌──────────────────┘     │     └────────────┤
                                       │ read-only retrieve()   │ read-only        │
                                  retrieve/  ───────────────►  mcp/server.py  ◄────┘
                                                                 (tool surface)
                                                                 ▲          ▲
                                                                 │          │
                                              chat/ (planner→executor→answer)   crews/report/ (evidence→draft→check→render)
                                                                 ▲          ▲
                                                                 └─ ui/app.py (FastAPI console: chat + report viewer)
```

External dependencies: **GCP** (Cloud SQL `ci-db`/`ci` with IAM auth + pgvector,
Vertex AI for embeddings, Secret Manager for keys), **Anthropic** (synthesis,
web research, planning, chat, report agents), **Tavily** and **Context7**
(acquisition lanes + report validation), and **CrewAI** (optional agent runtime
scaffold for the report crew).

## Layers / Services

### Core / shared kernel
Stateless utilities every layer imports. `config.py` loads the single
`config.yaml` (`get(path, default)`, `tracked_companies()`); **no model name,
threshold, chunk size, or competitor list is hardcoded in `src/`**. `ontology.py`
is the canonical technical/business dimension registry with alias expansion,
axis lookup, and normalization. `dimension_coverage.py` defines coverage states
(`present`/`partial`/`planned`/`absent`/`unknown`), source-level assertion
extraction, rollup precedence, and `missing_reason_for_state`. `llm_json.py`
parses JSON objects out of model output. `secrets.py` reads GCP Secret Manager
via ADC. `skills/__init__.py` provides `load_skill()` / `compose()` to assemble
`SKILL.md` prompts (the `grounding-contract` skill is `compose()`d in front of
every grounded generator).

### Acquisition layer (`acquire/`) — the ONLY internet-touching code
Each lane returns normalized *candidate* dicts (url, text, title, published,
axis, dimension, source_kind, citations). `web_lane.py` is the primary lane: it
calls the Anthropic Messages API with the `web_search` tool to generate a deep
company-research markdown report, then uses a cheaper "report splitter" model to
slice the report into ontology-scoped, cited candidates (source_kind
`official_llm_research_report`). It also exposes raw HTTP/RSS fetch + trafilatura
extraction and citation parsing. `tavily_lane.py` and `context7_lane.py` are
topic-scoped lanes (Context7 is technical-axis only). `company_profile_lane.py`
gathers company profiles; `relevance.py` scores a candidate against the requested
axis/dimension (`relevance-rubric` skill); `snapshots.py` writes provenance
artifacts (`raw_snapshots/`) and provides `fetch_html`/`write_snapshot`/`slugify`.

### Synthesis / ingestion layer (`synthesize/`)
Turns candidates into stored, embedded, cited evidence. `deep_map.py` is the
orchestrating CLI: it iterates configured companies × ontology dimensions
(`deep_map_now` for the demo), runs a DB preflight, calls the lanes to gather
candidates, dedupes/limits them, and feeds each into the pipeline. `pipeline.py`
(`ingest_candidate`) is the heart of the write path: resolve text → hash →
relevance gate (trusted-report / scoped-verdict / scored) → `compiler.synthesize`
(the synthesis model compiles raw text into structured, cited evidence) →
`upsert_source` → insert citations → store coverage assertions + refresh rollups
→ `chunk_text` → Vertex embeddings → `insert_chunks` → upsert entities/
relationships → **supersede older sources for the same competitor+dimension**
(non-destructive freshness). `compiler.py` runs the `ingest-synthesis` skill;
`coverage_verdict.py` / `close_coverage_scope.py` decide what still needs
research; `discover.py` and `run.py` ingest single URLs.

### Data layer (`db/`) — single source of truth
`connection.py` builds a singleton SQLAlchemy engine over the Cloud SQL Python
Connector with **IAM auth** (pg8000, no password) and, locally, service-account
impersonation; on Cloud Run (`K_SERVICE`) it uses the attached SA directly.
`schema.sql` defines `sources`, `chunks` (pgvector HNSW index on active rows),
`entities`, `relationships`, `source_citations`, the three
`dimension_coverage_*` tables, and `source_healing_audit`. `repository.py` is the
data-access façade: `vector_search`, `active_chunks`, `coverage_status`,
`dimension_coverage_status`, `graph_related_source_ids`, upserts, `supersede_older`,
audits. `doctor.py` is connectivity diagnostics; `heal_dimensions.py` and
`heal_coverage_status.py` are non-destructive maintenance CLIs (mark `stale`,
write audit rows — never delete).

### Embedding layer (`embed/gemini.py`)
Vertex AI `gemini-embedding-001` @ 1536 dims via ADC (no key), with distinct
`RETRIEVAL_DOCUMENT` (ingest) and `RETRIEVAL_QUERY` (search) task types and
batched document embedding. Used by the pipeline (write) and `retrieve` (read).

### Retrieval layer (`retrieve/__init__.py`) — read-only
`retrieve(query, axis, competitors, dimensions)` embeds the query, expands
dimension aliases, vector-searches **active** chunks (per-competitor quota when
multiple competitors are requested, then dedupe + rank by similarity), and
returns cited chunks plus a `missing` list (only for explicitly requested
dimensions, with reasons derived from coverage rollups). It never writes and
never browses.

### Tool surface (`mcp/server.py`) — the shared read-only API
A `FastMCP` app (streamable HTTP at `/mcp`, optional `MCP_SHARED_TOKEN` bearer
auth, DNS-rebinding host/origin protection) that wraps `retrieve` + `repository`
into ~18 tools. Beyond the documented core (`search`, `get_competitor`,
`compare_competitors`, `latest_updates`, `coverage_status`) the code also serves
`coverage_matrix`, `find_evidence_gaps`, `compare_dimension`, `get_source_detail`,
`source_inventory`, and report-builder tools (`build_report_evidence_pack`,
`build_report_section_evidence`, `build_capability_evidence_matrix`) and chat
tools (`search_answer_context`, `search_report_sections`, `get_report_registry`).
This is the single read API that both chat and reports consume.

### Chat service (`chat/`)
A plan → execute → answer pipeline. `planner.py` produces a `ChatRetrievalPlan`
(rewritten question, companies, dimensions, tool calls, web-check, answer style)
either deterministically or via the `chat_planner` model (`chat-query-planner` +
`chat-mcp-tool-use` + `chat-web-depth-selector` skills, JSON-schema-constrained),
then `strengthen_plan` augments it with heuristics. `retrieval.py`
(`McpChatExecutor`) executes only **allow-listed** MCP tools in-process and
converts results to `ChatEvidenceItem`s. `web.py` optionally adds a fresh Tavily
check; `answer.py` runs the `chat-answer-writer` + `chat-grounding-checker`
skills to write a grounded answer with findings. `report_store.py` reads
generated report artifacts; `service.run_chat` orchestrates the whole thing.

### Report generator (`crews/report/`) — evidence → draft → check → render
A batch pipeline (CLI `run.py` → `workflow.generate_report`). `evidence.py`
builds a **frozen `EvidencePack`** from the DB (via the MCP read functions /
`LocalMcpReportClient`) and — when `include_web=True` — adds **report-time Tavily
validation** evidence (a deliberate exception to the acquisition-only rule, used
to validate/refresh, tagged `source="tavily"`). `workflow.build_report_draft`
starts from deterministic analyst sections (`analysts.py`) and progressively
swaps in LLM-written sections per `draft_mode`: Strategy, Market, Product/Feature,
Technical, Buyer/Field, Scoring analysts (`strategy.py`, `market.py`,
`product_feature.py`, `technical.py`, `buyer_field.py`, `scoring.py`), each
composing `grounding-contract` + `neutral-ci-contract` + its analyst skill.
`checker.py` validates the draft against the pack; `renderer.py` writes
pdf/html/json. `crew.py` is the optional CrewAI runtime scaffold;
`capabilities.py`/`inventory.py`/`readiness.py`/`sections.py`/`schemas.py`/
`scoring.py` supply the supporting domain models. `market_report.py` publishes a
standalone market report once per batch.

### Web console (`ui/app.py`)
FastAPI app (factory `create_app`) serving the console: `GET /` (Jinja
template), `GET /api/reports`, `GET /reports/{slug}/html|pdf`, `POST /api/chat`
(delegates to `chat.service.run_chat`), and `WS /ws/chat` (streams chat events).
It reads report artifacts via `ReportArtifactStore` and never touches the DB
directly.

### Skills (`skills/<name>/SKILL.md`)
~30 prompt assets — the only place model instructions live. Grouped into
grounding contracts (`grounding-contract`, `neutral-ci-contract`), acquisition
(`deep-company-research`, `deep-report-splitter`, `relevance-rubric`,
`coverage-verdict`), synthesis (`ingest-synthesis`), chat (`chat-*`), and the
report analyst/framework/checker family (`report-*`).

## Data Flow

### Flow 1 — Acquisition → Ingestion (the one-directional write path)
`deep_map.run` iterates `companies × (axis, dimension)`. For each it calls the
acquisition lanes (`web_lane.search`, `tavily_lane.search`, `context7_lane.search`)
— the only place the network is hit for corpus building. Candidates are deduped
and passed to `pipeline.ingest_candidate`, which: fetches/cleans text, content-
hashes for dedup, applies the relevance gate, runs `compiler.synthesize` (LLM →
structured cited evidence + entities + relationships + conflicts), upserts the
`sources` row and `source_citations`, stores `dimension_coverage_assertions` and
refreshes `dimension_coverage_status` rollups, chunks the compiled text, embeds
with Vertex (`RETRIEVAL_DOCUMENT`), inserts `chunks`, upserts the entity/
relationship graph, and finally `supersede_older` marks prior sources for that
competitor+dimension as superseded (freshness, non-destructive). Nothing
downstream ever writes here.

### Flow 2 — Chat / retrieval answer (read path)
`ui POST /api/chat` (or `WS /ws/chat`) → `chat.service.run_chat`. The planner
builds a tool plan; `McpChatExecutor` runs allow-listed MCP tools in-process —
chiefly `search_answer_context`, which calls `retrieve()` → `gemini.embed_query`
(`RETRIEVAL_QUERY`) → `repository.vector_search` over **active** chunks, blended
with keyword and generated-report hits. Optionally a fresh `web.py` Tavily check
is added. Evidence is ranked/trimmed, then `answer.write_answer` produces a
grounded answer (with a grounding-checker pass) that cites only stored/active
sources; gaps surface as explicit `missing` reasons. Pure read — no corpus write.

### Flow 3 — Report generation (batch read + render)
`crews/report/run.main` → `workflow.generate_report`. `evidence.build_evidence_
pack_for_competitor` pulls DB evidence through the MCP read functions
(`build_report_section_evidence`, `build_capability_evidence_matrix`,
`source_inventory`, coverage), optionally augments with report-time Tavily
validation, and **freezes** an `EvidencePack`. `build_report_draft` layers
LLM analyst sections per `draft_mode` over the deterministic skeleton; `checker.
check_report` validates claims against the frozen pack; `renderer` writes
pdf/html/json into `reports/<slug>/`, which the UI then serves. Generated reports
become a secondary read source for chat via `report_store`.

## API Contracts

- **MCP tools** (`/mcp`, streamable HTTP, JSON responses): `search(query, axis?,
  competitors?, dimensions?)`, `get_competitor(name, axis?)`,
  `compare_competitors(names, dimension?)`, `compare_dimension(names, dimension)`,
  `latest_updates(competitor?, days=7)`, `coverage_status()`,
  `coverage_matrix(...)`, `find_evidence_gaps(...)`, `get_source_detail(source_ids)`,
  `source_inventory(...)`, `build_report_evidence_pack(...)`,
  `build_report_section_evidence(...)`, `build_capability_evidence_matrix(...)`,
  `search_answer_context(...)`, `search_report_sections(...)`,
  `get_report_registry(...)`. Every chunk-bearing response carries url, title,
  publish_date, axis, dimension, source_kind, citations, and a `missing` array.
- **HTTP (FastAPI console)**: `GET /`, `GET /api/reports`,
  `GET /reports/{slug}/html`, `GET /reports/{slug}/pdf` (409 if validation-blocked),
  `POST /api/chat` (`ChatRequest` → `ChatAnswer`), `WS /ws/chat`.
- **CLIs**: `python -m ci_engine.synthesize.deep_map [--competitor] [--max-candidates-per-dimension]`;
  `python -m ci_engine.crews.report.run [--competitor|--competitors|--all-companies|--deep-map-now] [--draft-mode] [--formats] [--no-web]`;
  `python -m ci_engine.db.doctor`; `heal_dimensions` / `heal_coverage_status`
  (`--dry-run` then `--apply`).
- **Internal Python contracts**: candidate dict (acquisition→pipeline), the
  `EvidencePack`/`EvidenceItem`/`EvidenceGap` Pydantic models
  (MCP→report), `ChatRetrievalPlan`/`ChatEvidenceItem`/`ChatAnswer` (chat),
  and the `ReportMcpClient` Protocol (report ↔ MCP read functions).

## Deployment Mapping

- **MCP server** → `ops/Dockerfile.mcp` → `python -m ci_engine.mcp.server`
  (uvicorn, port `$PORT`, default 8080). Cloud Run service; auth via
  `MCP_SHARED_TOKEN`; uses the attached `ci-engine-sa` SA directly.
- **UI console** → `ops/Dockerfile.ui` → uvicorn `--factory ci_engine.ui.app:create_app`
  (port `$PORT`, default UI 8090 locally). Cloud Run service; calls chat in-process.
- **Cloud SQL** `jfrog-intel-rag:europe-west1:ci-db`, database `ci`, IAM auth
  (no password), pgvector — accessed by every service via `db/connection.py`.
- **Vertex AI** (`europe-west1`) for `gemini-embedding-001` via ADC.
- **Secret Manager** (project `jfrog-intel-rag`): `anthropic-key`, `tavily-key`,
  `telegram-token`, `context7-key`.
- **Batch jobs** (deep-map ingestion, report generation, healing) run as CLIs /
  jobs with the same SA and config; `ops/openclaw/` carries an additional agent
  deployment asset (Telegram/GCP, per recent commits).
- **Local dev**: `uv sync`; ADC + SA impersonation for Cloud SQL; `db.doctor`
  for connectivity.

## Technical Debt / Concerns

- **Single-direction rule has one sanctioned exception**: `crews/report/evidence.py`
  performs **report-time Tavily web validation** when `include_web=True`. This is
  intentional (validate/refresh before publishing) and tagged `source="tavily"`,
  but it means "only acquisition touches the internet" is true for *corpus
  building*, not for the report path — worth stating explicitly so the invariant
  isn't over-trusted. `chat/web.py` is a similar read-time Tavily call.
- **MCP tool sprawl vs. docs**: the server exposes ~18 tools; CLAUDE.md/docs list
  only the original 5. The extra report/chat builder tools are load-bearing and
  should be documented as part of the contract.
- **In-process MCP coupling**: chat (`retrieval.py`) and reports (`evidence.py`)
  import `ci_engine.mcp.server` functions directly rather than over HTTP. This is
  fast and avoids a network hop, but couples three "services" into one process and
  means the HTTP transport/auth path is exercised only by external clients.
- **Heuristic-heavy planner/ranking**: `chat/planner.py`, `chat/service.py`, and
  the MCP keyword scorers embed long hand-tuned term lists and JFrog/competitor-
  specific phrases (artifactory, xray, nexus, …). These work for the demo corpus
  but are brittle and partially duplicate the ontology — candidates for moving
  into `config.yaml`/`ontology.py`.
- **`freshness/` is an empty placeholder**: freshness/lifecycle logic actually
  lives in `config.yaml` + `repository.supersede_older` + the `heal_*` CLIs and
  coverage rollups. The empty package can mislead readers into expecting a layer
  that doesn't exist as code.
- **CrewAI scaffold is half-wired**: `crews/report/crew.py` builds a CrewAI
  skeleton, but the production report path is the deterministic/sequential
  `workflow.py` orchestrator. The CrewAI integration is an aspirational seam, not
  the live execution path.
- **Compiled coverage normalization is duplicated**: `_missing` logic exists in
  both `retrieve/__init__.py` and `mcp/server.py` with slightly different rules,
  a possible source of inconsistent `missing` reasons between the two read entry
  points.
```
