# CI Engine

CI Engine is a competitive-intelligence RAG system for the software supply chain security market. It builds a cited, queryable knowledge base about JFrog and competitors across technical capabilities and business signals.

The system is designed for evidence-backed answers. It does not use model memory at answer time. Acquisition and ingestion may use AI to find, classify, and synthesize source material, but retrieval reads only active evidence stored in Cloud SQL.

## What This Project Is For

CI Engine helps answer questions such as:

- What products does each competitor offer?
- Does a competitor cover a capability such as package firewall, SBOM generation, reachability analysis, or runtime security?
- Is coverage current, partial, planned, absent, or still unknown?
- What market positioning, pricing, ICP, customer, analyst, partnership, funding, M&A, or win/loss signals exist?
- Which claims are backed by official sources, and which come from third-party evidence?
- Where does JFrog lead, match, lag, or still need more evidence?

The core business use case is competitive intelligence for JFrog across software supply chain security, DevSecOps, artifact management, SCA, package security, MLOps, and AI supply chain.

## Core Principles

- **The database is the primary source of truth.** Reports, chat, MCP tools, and retrieval read from Cloud SQL first.
- **Web access is explicit and bounded.** Acquisition can browse for ingestible evidence, and report generation can use Tavily for run-scoped enrichment/validation. Retrieval does not browse.
- **Every answer should be grounded in active stored evidence.** Stored chunks carry source URLs and provenance.
- **Unknown is intentional.** Unknown means the system cannot safely say present, partial, planned, or absent.
- **Absent requires explicit negative evidence.** No evidence is never enough to say a company does not cover something.
- **Prompts live in skills.** Model instructions are stored in `src/ci_engine/skills/*/SKILL.md`, not hardcoded in application code.
- **Tunable behavior lives in config.** Models, ontology, thresholds, companies, chunking, and retrieval settings live in `src/ci_engine/config.yaml`.
- **Healing is non-destructive and auditable.** The system marks stale or corrects metadata only with audit records.

## Documentation Map

- [Repo Structure](docs/repo-structure.md) - where code lives and where to change behavior.
- [Business Context](docs/business-context.md) - what the system means for competitive intelligence.
- [Architecture](docs/architecture.md) - data flow, DB schema, ontology, retrieval, and coverage status design.
- [AI and Models](docs/ai-and-models.md) - what AI manages, which models are used, and what is deterministic.
- [Operations](docs/operations.md) - setup, commands, safe rollout, and troubleshooting.
- [Report Generator](docs/report-generator.md) - CrewAI competitive dossiers, EvidencePack flow, validation, and PDF/HTML/JSON outputs.
- [Chat and Report Console](docs/chat-and-report-console.md) - local report viewer UI and grounded MCP-backed Q&A chat.

## Repository Map

Top-level:

- `src/ci_engine/` - application code.
- `tests/` - unit and integration-style tests.
- `raw_snapshots/` - fetched/source snapshots for provenance.
- `ops/` - operational or deployment assets.
- `CLAUDE.md` - operating constitution and non-negotiable design rules.
- `pyproject.toml` and `uv.lock` - packaging and dependency management.
- `deep_map.log` - local run log. It is not the source of truth.

Important packages:

- `acquire/` - web, Tavily, Context7, company-profile, and snapshot acquisition lanes.
- `db/` - Cloud SQL connection, schema, repository functions, healing, and backfill CLIs.
- `embed/` - Vertex AI / Gemini embedding client.
- `mcp/` - MCP retrieval server and tools.
- `crews/report/` - CrewAI-backed competitive report generator, schemas, analysts, checker, and renderer.
- `chat/` - skill-guided chat planning, MCP execution, Tavily web checks, answer writing, and grounding validation.
- `ui/` - FastAPI report console with editorial dossier reader, PDF download, and chat drawer.
- `retrieve/` - read-only retrieval API.
- `skills/` - model prompts and instruction assets.
- `synthesize/` - deep map, ingestion pipeline, synthesis, scope closure, and verdict logic.
- `ontology.py` - canonical dimension and alias normalization.
- `dimension_coverage.py` - coverage states, inference, missing reasons, and rollups.
- `config.py` and `config.yaml` - config loader and single source of tunable config.

## System At A Glance

```text
Configured companies + ontology
        |
        v
Deep map / scope closure
        |
        v
Acquisition lanes
  - Anthropic web research
  - Tavily search
  - Context7 docs
  - Direct web fetch/snapshots
        |
        v
Relevance and coverage verdicts
        |
        v
Synthesis into compiled evidence
        |
        v
Chunks + embeddings + entities + relationships
        |
        v
Cloud SQL / pgvector
        |
        v
Read-only retrieval + MCP tools
        |
        v
EvidencePack + CrewAI competitive reports
        |
        v
Editorial report console + grounded MCP chat
```

## Quickstart

Install dependencies:

```bash
cd /Users/kevinifrah/jfrog-intel-rag
uv sync
```

Configure GCP application-default credentials:

```bash
gcloud config set project jfrog-intel-rag
gcloud auth application-default login
```

Apply or refresh the schema:

```bash
gcloud sql connect ci-db --user=postgres --database=ci --project=jfrog-intel-rag
```

Inside `psql`:

```sql
\i /Users/kevinifrah/jfrog-intel-rag/src/ci_engine/db/schema.sql
\q
```

Check DB connectivity:

```bash
.venv/bin/python -m ci_engine.db.doctor
```

Run tests:

```bash
.venv/bin/python -m pytest
```

## Main Workflows

### Deep Map

Run the full configured deep map for `deep_map_now` companies:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map
```

Run one company:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map --competitor JFrog
```

Limit candidate volume during testing:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map \
  --competitor JFrog \
  --max-candidates-per-dimension 2
```

`deep_map_now` is configured in `src/ci_engine/config.yaml` and currently includes:

- JFrog
- Snyk
- Sonatype
- GitLab

### Retrieve Evidence

Use the read-only retrieval method:

```bash
.venv/bin/python - <<'PY'
import json
from ci_engine.retrieve import retrieve

result = retrieve(
    "Does Snyk have a package firewall product that blocks package downloads?",
    axis="technical",
    competitors=["Snyk"],
    dimensions=["package_firewall"],
)

print(json.dumps(result, indent=2, default=str))
PY
```

Examples:

```bash
.venv/bin/python - <<'PY'
import json
from ci_engine.retrieve import retrieve

cases = [
    ("What products does Sonatype offer?", "technical", ["Sonatype"], ["product_portfolio"]),
    ("Does JFrog Curation block package downloads?", "technical", ["JFrog"], ["package_firewall"]),
    ("Where is GitLab partial or unknown?", "technical", ["GitLab"], ["recursive_deep_scanning"]),
    ("Which JFrog products are deprecated or sunset?", "technical", ["JFrog"], ["product_portfolio"]),
]

for query, axis, competitors, dimensions in cases:
    result = retrieve(query, axis=axis, competitors=competitors, dimensions=dimensions)
    print("\\n==", query, "==")
    print(json.dumps(result, indent=2, default=str))
PY
```

### Run The MCP Server

```bash
.venv/bin/python -m ci_engine.mcp.server
```

The server exposes tools including:

- `search`
- `get_competitor`
- `compare_competitors`
- `latest_updates`
- `coverage_status`
- `coverage_matrix`
- `find_evidence_gaps`
- `get_source_detail`
- `compare_dimension`
- `build_report_section_evidence`
- `build_capability_evidence_matrix`
- `build_report_evidence_pack`
- `source_inventory`
- `get_report_registry`
- `search_report_sections`
- `search_answer_context`

`search` accepts `dimensions`, so callers can scope retrieval to exact ontology dimensions.

### Run The Report Console And Chat

Start the local UI:

```bash
.venv/bin/python -m ci_engine.ui
```

Open:

```text
http://127.0.0.1:8090
```

The console is an editorial two-pane reader: a typeset competitor index on the left and the full dossier in an iframe on the right. Clicking a competitor loads its report; the reader header shows the dossier title, status, and a PDF download link. A fixed "Ask" button (bottom-right circle) opens the chat drawer.

The chat is grounded in read-only MCP retrieval and selected report artifacts. It uses Sonnet for answer writing, automatically runs bounded Tavily validation when needed, strengthens comparison and weakness questions with balanced retrieval, and fails closed with `not enough evidence` when retrieval cannot support an answer. The clear button in the chat drawer header resets the conversation.

### Generate A Competitive Report

Generate the full `JFrog vs Snyk` dossier with DB evidence, Tavily validation, CrewAI/Sonnet analysis, HTML, JSON, and PDF:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitor Snyk \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

`--competitor` generates one report for one competitor.

Generate reports for every configured company except JFrog:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --all-companies \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

Generate reports for Snyk, Sonatype, and GitHub in one batch:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitors "Snyk,Sonatype,GitHub" \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

Generate reports only for the current `deep_map_now` focus list except JFrog:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --deep-map-now \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

`--deep-map-now` reads the `deep_map_now:` subset in `config.yaml`. It does not run deep map and it is not triggered automatically when deep map finishes. To deep-map first and then report on the same focus list, run:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map

.venv/bin/python -m ci_engine.crews.report.run \
  --deep-map-now \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

Outputs are written to `reports/<competitor-slug>/`:

- `report.json` - frozen `EvidencePack`, report draft, scores, and validation report.
- `report.html` - editorial-grade dossier with numbered citations, structured frameworks, and typeset layout.
- `report.pdf` - PDF rendering of the validated HTML report.

The report generator fails closed. JSON and HTML are written for review, but PDF rendering is blocked when validation fails.

**Report sections and frameworks produced:**

Each dossier includes:
- **Executive Summary** — strategic thesis, JFrog strengths, competitor strengths, risks, and recommended actions (SWOT, confidence tiering).
- **Company Snapshot** — business-level position for JFrog and the competitor.
- **Market And Strategic Context** — market thesis, buyer segments, GTM, ecosystem signals, market risks (PESTEL, Five Forces, positioning map).
- **Product And Feature Analysis** — product catalog, capability matrix, JFrog advantages, competitor advantages, JFrog limitations, parity gaps, buyer implications.
- **Technical Teardown** — architectural thesis, platform capabilities, architecture implications, AI/artifact governance, supply chain security comparison.
- **Buyer Fit Matrix** — buyer scenarios where JFrog wins vs loses, qualify-out signals.
- **Field Battlecard** — battlecard thesis, objection handling, discovery questions, field actions.
- **Scoring** — weighted buyer scorecards across three archetypes (security/OSS-led, balanced, platform/consolidation-led).

**Framework consistency across reports:**

The positioning map uses canonical fixed axes across all dossiers:
- X: *Supply-chain coverage breadth* (single ecosystem → universal repository + full SDLC)
- Y: *Security specialization depth* (platform with security add-ons → purpose-built security toolchain)

This means positioning maps from different reports are directly comparable. The Five Forces also uses a consistent baseline intensity for the software supply chain security market, adjusted only where evidence supports.

**Draft modes:**

`--draft-mode` controls how much of the report is generated by the CrewAI/LLM analyst pipeline:

- `deterministic` - fast smoke mode with no live analyst generation.
- `crew_strategy` - Strategy Analyst only.
- `crew_strategy_market` - adds company and market sections.
- `crew_strategy_market_technical` - adds technical sections.
- `crew_strategy_market_technical_field` - adds buyer and field sections.
- `crew_strategy_market_product_technical_field` - adds product/feature analysis.
- `crew_strategy_market_product_technical_field_scoring` - full dossier with scoring. Use this for production reports.

For a fast DB-only smoke test:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitor Sonatype \
  --draft-mode deterministic \
  --formats json,html \
  --no-web \
  --out-dir /private/tmp/ci-report-smoke
```

### Heal Dimensions

Dry run:

```bash
.venv/bin/python -m ci_engine.db.heal_dimensions --dry-run
```

Apply:

```bash
.venv/bin/python -m ci_engine.db.heal_dimensions --apply
```

This canonicalizes source dimensions and marks only known-bad sources stale. It does not delete sources or chunks.

### Backfill Coverage Status

Dry run:

```bash
.venv/bin/python -m ci_engine.db.heal_coverage_status --dry-run
```

Apply:

```bash
.venv/bin/python -m ci_engine.db.heal_coverage_status --apply
```

This creates or refreshes coverage assertions and rollups from existing active sources.

### Close Unknown Scope Gaps

Dry run:

```bash
.venv/bin/python -m ci_engine.synthesize.close_coverage_scope \
  --dry-run --only-deep-map-now --max-gaps 20
```

Small apply batch:

```bash
.venv/bin/python -m ci_engine.synthesize.close_coverage_scope \
  --apply --only-deep-map-now --max-gaps 2 --max-candidates-per-gap 1 --review-absent
```

Targeted apply:

```bash
.venv/bin/python -m ci_engine.synthesize.close_coverage_scope \
  --apply \
  --competitor Snyk \
  --axis technical \
  --dimension package_firewall \
  --max-gaps 1 \
  --max-candidates-per-gap 2 \
  --review-absent
```

Scope closure researches unknown, planned, or partial gaps, classifies candidates, and ingests only accepted evidence. It never marks absence from no results.

## Coverage States

Coverage is stored as assertions and rolled up per competitor, axis, and dimension.

- `present` - reliable current evidence supports coverage.
- `partial` - evidence supports limited or scoped coverage.
- `planned` - roadmap, beta, proposal, or coming-soon evidence.
- `absent` - reliable evidence explicitly says the company does not support or offer the capability.
- `unknown` - evidence is missing, weak, ambiguous, or out of scope.

Two important kinds of unknown:

- **Unknown data** - no reliable evidence has been found or stored.
- **Unknown scope** - evidence exists, but it does not answer the exact dimension.

Retrieval reports missing reasons:

- `unknown_coverage`
- `known_absent`
- `planned_only`
- `partial_coverage`
- `no_matching_chunks`

## What Is Managed By AI

AI is used during acquisition and ingestion, not as the source of truth at answer time.

Current model configuration is in `src/ci_engine/config.yaml`:

- Web research report generation: `claude-sonnet-4-6`
- Competitive report agents: `claude-sonnet-4-6`
- Chat planner: `claude-haiku-4-5`
- Chat answer writer: `claude-sonnet-4-6`
- Chat fallback: `claude-sonnet-4-6`
- Report splitting: `claude-haiku-4-5`
- Relevance scoring: `claude-haiku-4-5`
- Ingestion synthesis: `claude-opus-4-8`
- Coverage verdict fallback: `claude-haiku-4-5`
- Embeddings: `gemini-embedding-001`, 1536 dimensions, via Vertex AI

Deterministic code handles:

- Config loading
- Ontology normalization
- Dimension alias expansion
- DB schema, writes, and audits
- Stale marking rules
- Rollup precedence
- Retrieval filters
- Conservative verdict guards
- EvidencePack schema validation
- Report checker gates
- Chat grounding checker gates
- HTML/PDF/JSON rendering

See [AI and Models](docs/ai-and-models.md) for details.

## What We Implemented And Why

### Ontology Normalization

Problem: ingestion could drift into non-canonical dimension labels such as `vulnerability_remediation`, `sbom_support`, or `market_position`.

Implementation:

- Canonical dimensions are loaded from `config.yaml`.
- Aliases are normalized in `ontology.py`.
- Candidate dimensions from deep map and scope closure are authoritative.
- LLM synthesis may enrich metadata, but cannot replace a supplied canonical candidate dimension.

Why: retrieval filters and coverage rollups only work reliably when dimensions stay within the configured ontology.

### DB Healing

Problem: existing rows had dimension drift and some accepted sources were false positives.

Implementation:

- `heal_dimensions` canonicalizes dimensions.
- Known-bad or deterministic wrong-scope sources can be marked `stale`.
- Source/chunk content is preserved.
- `source_healing_audit` records every metadata/status change.

Why: healing should preserve evidence and provenance, not delete important data.

### Coverage Status

Problem: missing information was ambiguous. It could mean no data, partial support, roadmap support, explicit non-support, or weak evidence.

Implementation:

- Source-level assertions live in `dimension_coverage_assertions`.
- Rollups live in `dimension_coverage_status`.
- Audit rows live in `dimension_coverage_audit`.
- States are `present`, `partial`, `planned`, `absent`, and `unknown`.

Why: retrieval and business analysis need to distinguish true non-coverage from missing or scoped evidence.

### AI-Assisted Scope Closure

Problem: unknown gaps need targeted research, but the system must not guess or mark absence from silence.

Implementation:

- `close_coverage_scope` finds unknown, planned, or partial gaps.
- Acquisition lanes search targeted topics for each gap.
- `coverage_verdict` classifies candidates as `present`, `partial`, `planned`, `explicit_absent`, `irrelevant`, `still_unknown`, or `needs_review`.
- Accepted candidates are stamped with canonical axis, dimension, and evidence state.
- `--review-absent` sends risky absence claims to review instead of ingesting them.

Why: coverage can be closed safely while preserving auditability and avoiding false negatives.

### Retrieval Improvements

Problem: retrieval could miss dimension aliases, over-return from one company, or report noisy missing coverage.

Implementation:

- Dimension filters expand through aliases.
- Vector search considers assertion-backed dimensions.
- Multi-company retrieval uses a per-company quota before merging.
- Retrieval returns at most configured `top_k`.
- Missing coverage is reported only for explicitly requested dimensions.
- Missing reasons use coverage state.

Why: scoped searches need high precision, broad searches need balance, and missing output must be meaningful.

### MCP Search Dimensions

Problem: MCP callers needed to scope search to ontology dimensions.

Implementation:

- MCP `search` accepts `dimensions: list[str] | None`.
- MCP tools return cited chunks and missing coverage.

Why: external clients can ask targeted questions without guessing internal filters.

### Editorial Competitive Dossiers

Problem: a raw list of evidence sources is not a usable executive competitive-intelligence report.

Implementation:

- `src/ci_engine/crews/report/` builds `JFrog vs <competitor>` dossiers.
- `EvidencePack` freezes DB evidence, Tavily validation, product catalog, capability matrix, gaps, and confidence metadata.
- MCP `build_report_evidence_pack` batches broad report section retrieval.
- CrewAI/Sonnet analyst agents synthesize seven sections: Strategy, Market, Product/Feature, Technical, Buyer/Field, Scoring, and Editor/Auditor.
- Each section uses a dedicated skill in `src/ci_engine/skills/report-*/SKILL.md` with explicit format instructions, evidence rules, and writing discipline.
- **Analyst frameworks:** Strategy produces SWOT and confidence tiering; Market produces PESTEL, Five Forces, and a positioning map with canonical cross-report axes; Product produces a capability matrix with cited rows.
- `Report Checker` validates citations, neutrality, evidence coverage, contradictions, and raw artifact leakage before rendering.
- The renderer writes `report.json`, `report.html` (typeset single-column dossier with numbered references, SVG positioning map, SWOT grid, PESTEL grid, Five Forces rows, capability matrix, buyer scorecards), and `report.pdf`.

Why: reports need neutral, C-level-readable analysis that is structurally comparable across competitors, while preserving auditability and failing closed when evidence is weak.

### Report Console UI

Problem: the report viewer was a generic SaaS dashboard that fought the editorial quality of the dossier.

Implementation:

- The console (`src/ci_engine/ui/`) is an editorial two-pane environment: typeset competitor index (left) + full dossier iframe reader (right).
- A fixed circle "Ask" button opens an off-canvas chat drawer with a clear button.
- No dropdown, no shadow cards, no colored pills — warm paper canvas matching the dossier.
- Chat is grounded in read-only MCP retrieval; answer writing uses Sonnet for richer synthesis.

Why: the chrome around the document should support reading the document, not distract from it.

## Configuration

`src/ci_engine/config.yaml` controls:

- GCP project and region
- Cloud SQL connection settings
- Model names and model parameters
- Embedding model and dimensions
- Retrieval `top_k`, similarity threshold, and graph settings
- Chunk size and overlap
- Ingestion thresholds and lane toggles
- Freshness policy
- Tracked companies
- `deep_map_now`
- Technical and business ontology dimensions

Do not hardcode tunables in source code when they belong in config.

## Infrastructure

Configured defaults:

- GCP project: `jfrog-intel-rag`
- Region: `europe-west1`
- Cloud SQL instance: `ci-db`
- Database: `ci`
- IAM DB user: `ci-engine-sa@jfrog-intel-rag.iam`
- Service account: `ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com`
- Secrets: `anthropic-key`, `tavily-key`, `context7-key`, `telegram-token`

Secret values must never be committed or documented.

## Testing

Run all tests:

```bash
.venv/bin/python -m pytest
```

Run focused tests:

```bash
.venv/bin/python -m pytest tests/test_retrieval.py
.venv/bin/python -m pytest tests/test_coverage_verdict.py
.venv/bin/python -m pytest tests/test_pipeline.py
.venv/bin/python -m pytest tests/test_report_crew.py tests/test_mcp_server.py
.venv/bin/python -m pytest tests/test_ui.py tests/test_chat.py
```

## Safety Notes

- Do not delete sources to fix coverage. Mark stale with audit when needed.
- Do not run large scope-closure apply batches before reviewing dry-run output.
- Do not treat `unknown` as `absent`.
- Do not add prompt strings in Python modules. Use `skills/`.
- Do not add model names outside `config.yaml`.
- Do not add retrieval-time web access.
- Do not let report agents write claims outside the frozen EvidencePack.
