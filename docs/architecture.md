# Architecture

CI Engine is a grounded RAG pipeline with explicit ingestion, storage, coverage rollups, and read-only retrieval.

## High-Level Flow

```text
config.yaml
  companies + ontology + models + thresholds
        |
        v
Deep map / targeted scope closure
        |
        v
Acquisition lanes
        |
        v
Relevance scoring and verdict classification
        |
        v
Ingestion synthesis
        |
        v
Sources, citations, chunks, embeddings, graph, assertions
        |
        v
Coverage rollups
        |
        v
Read-only retrieval and MCP tools
        |
        v
Competitive report EvidencePack + CrewAI dossier generation
```

## Config And Ontology

`src/ci_engine/config.yaml` is the single source of tunable configuration.

It defines:

- GCP project and database settings
- model names and model parameters
- embedding model and dimensionality
- retrieval settings
- chunking settings
- ingestion thresholds and lane toggles
- freshness settings
- tracked companies
- `deep_map_now`
- technical and business ontology dimensions

`src/ci_engine/ontology.py` canonicalizes dimensions and maps aliases to the configured ontology.

Important design rule:

- Deep-map and scope-closure candidates are stamped with canonical axis/dimension.
- That candidate dimension is authoritative.
- LLM synthesis can enrich metadata, but should not drift the source into an unrelated dimension.

## Acquisition Lanes

Acquisition finds candidate evidence for permanent ingestion and is the normal web-facing layer.

Competitive report generation has a bounded exception: it can use Tavily for run-scoped enrichment and validation after DB retrieval. Those web findings are frozen into the report EvidencePack; normal retrieval still does not browse.

### Web Research Lane

`acquire/web_lane.py` uses Anthropic web search to generate a deep company research report. The report is stored as a raw snapshot, then split into ontology-scoped candidates.

The lane is useful for broad company coverage and business context.

### Tavily Lane

`acquire/tavily_lane.py` runs topic-specific search queries through Tavily. It stores raw content when available and snapshots fetched HTML otherwise.

The lane is useful for targeted technical and business discovery.

### Context7 Lane

`acquire/context7_lane.py` resolves a library/product docs identifier and queries technical documentation through Context7 MCP tools.

The lane is useful for technical docs, APIs, SDKs, and product documentation.

### Direct Web Fetch

`web_lane.fetch` and snapshot helpers fetch and extract text from individual URLs.

## Relevance And Verdicts

Candidates are filtered before expensive synthesis.

### Relevance Scoring

`acquire/relevance.py` sends candidate metadata and content excerpts to the relevance model using the `relevance-rubric` skill.

The model returns:

- relevance
- score
- axis
- dimension
- doc type
- reason

Candidates below `ingestion.relevance_threshold` are skipped.

### Coverage Verdicts

`synthesize/coverage_verdict.py` is used by scope closure. It classifies a candidate against an exact competitor + axis + dimension gap.

Verdict states:

- `present`
- `partial`
- `planned`
- `explicit_absent`
- `irrelevant`
- `still_unknown`
- `needs_review`

Accepted verdicts can be ingested. Irrelevant or still-unknown candidates are skipped. With `--review-absent`, explicit absence is sent to review instead of being applied automatically.

Verdicts use deterministic guards first, then an LLM fallback. Guards prevent known false positives such as:

- package advisory pages for a package named `firewall`
- partner ecosystem pages pretending to be technical ecosystem support
- vendor-tool rollout docs pretending to be software distribution
- AI ROI dashboards pretending to be technical impact analysis
- internal model validation pretending to be AI model scanning
- generic CI/CD edge examples pretending to be edge-node delivery coverage

## Ingestion Pipeline

`synthesize/pipeline.py` ingests one candidate.

Steps:

1. Fetch or read candidate text.
2. Compute content hash.
3. Attach metadata from fetch.
4. Score relevance or accept trusted scoped verdicts.
5. Normalize source metadata and canonical dimension.
6. Store or reuse source row.
7. Store source citations.
8. Synthesize compiled evidence.
9. Store coverage assertions.
10. Chunk compiled evidence.
11. Embed chunks.
12. Store chunks.
13. Upsert entities and relationships.
14. Supersede older sources where applicable.
15. Refresh coverage status rollups.

Duplicate content is skipped for chunking and embedding but can still refresh citations and coverage assertions.

## Synthesis

`synthesize/compiler.py` uses the `ingest-synthesis` skill and the synthesis model. It converts raw source text into:

- compiled evidence text
- facts
- coverage assertions
- entities
- relationships
- conflicts

The compiled text is what becomes retrievable chunks.

## Chunking And Embedding

Chunks are paragraph-aware:

- configured by `chunking.chunk_size`
- configured by `chunking.chunk_overlap`

Embeddings are created by `embed/gemini.py` using Vertex AI:

- model: `gemini-embedding-001`
- dimensions: 1536
- document task: `RETRIEVAL_DOCUMENT`
- query task: `RETRIEVAL_QUERY`

Embeddings are stored in Postgres with pgvector.

## Database Design

The schema lives in `src/ci_engine/db/schema.sql`.

### `sources`

One row per ingested source.

Important fields:

- competitor
- axis
- doc_type
- dimension
- url
- title
- publish_date
- fetched_at
- content_hash
- status
- source_kind
- raw_path

Status values:

- `active`
- `stale`
- `superseded`
- `dead`

### `chunks`

One row per compiled evidence chunk.

Important fields:

- source_id
- competitor
- axis
- doc_type
- publish_date
- status
- chunk_text
- embedding

Only active chunks are used by retrieval.

### `entities` And `relationships`

The graph layer stores products, features, competitors, integrations, and relationships between them.

Relationships keep source provenance through `source_id`.

### `source_citations`

Stores official URLs cited by a source, especially sliced AI research reports.

### `source_healing_audit`

Records source metadata/status healing:

- status updates
- dimension updates
- reason
- details
- timestamp

### `dimension_coverage_assertions`

Source-level evidence about a company/dimension.

Fields include:

- source_id
- competitor
- axis
- dimension
- state
- confidence
- claim
- reason
- details

### `dimension_coverage_status`

Rollup per competitor + axis + dimension.

Fields include:

- state
- confidence
- active_assertions
- strongest_source_id
- conflict
- states
- updated_at

### `dimension_coverage_audit`

Records assertion/status changes and rollup refreshes.

## Coverage Rollups

Coverage states are:

- `present`
- `partial`
- `planned`
- `absent`
- `unknown`

Rollup precedence:

1. present
2. partial
3. planned
4. absent
5. unknown

All assertions are kept. If positive and absent evidence coexist, the rollup marks `conflict = true`.

## Retrieval

`retrieve.retrieve` is read-only.

Inputs:

- query
- optional axis
- optional competitors
- optional dimensions

Steps:

1. Clean and validate query.
2. Normalize requested dimensions.
3. Expand dimension aliases.
4. Embed query.
5. Run vector search over active chunks.
6. For multi-company searches, fetch a small quota per company and merge.
7. Deduplicate and rank chunks.
8. Add missing coverage only for requested dimensions.

Vector search filters:

- active chunks
- active sources
- competitor
- axis
- dimension
- optional doc/source filters in repository functions

Dimension filtering uses both:

- source dimension
- matching active coverage assertions

This lets retrieval find evidence even when a source supports multiple dimensions.

## Missing Reasons

Retrieval may return:

- `unknown_coverage` - no reliable coverage classification exists.
- `known_absent` - reliable evidence says the company does not support the capability.
- `planned_only` - only roadmap/beta/future evidence exists.
- `partial_coverage` - only limited/scoped support exists.
- `no_matching_chunks` - coverage exists, but vector search did not return matching chunks for this query.

Missing is reported only when dimensions are explicitly requested.

## MCP Server

`mcp/server.py` exposes retrieval through MCP tools:

- `search`
- `get_competitor`
- `compare_competitors`
- `latest_updates`
- `coverage_status`
- `coverage_matrix`
- `find_evidence_gaps`
- `compare_dimension`
- `get_source_detail`
- `build_report_section_evidence`
- `build_capability_evidence_matrix`
- `build_report_evidence_pack`
- `source_inventory`

The server uses the same DB-backed retrieval principles. It does not browse at answer time.

Report-specific MCP tools provide batch retrieval for dossiers:

- `build_report_section_evidence` retrieves section-scoped DB evidence for all report sections in one structured pass.
- `build_capability_evidence_matrix` retrieves capability-level product evidence for JFrog and one competitor in one structured pass.
- `source_inventory` gives the report run an evidence census before analysis.

These tools reduce repeated semantic query calls and make EvidencePack construction faster and more auditable.

## Competitive Report Generator

The report generator lives in `src/ci_engine/crews/report/`.

It produces `JSON`, `HTML`, and `PDF` dossiers for `JFrog vs <competitor>`.

High-level flow:

1. Build a source inventory from active DB evidence.
2. Retrieve broad section evidence through `build_report_section_evidence`.
3. Retrieve capability/product evidence through `build_capability_evidence_matrix`.
4. Use Tavily for broad web enrichment and targeted validation.
5. Freeze an `EvidencePack`.
6. Run CrewAI/Sonnet analyst sections.
7. Run the Report Checker.
8. Render HTML/PDF only after validation passes.

The system fails closed: unsupported claims, unresolved contradictions, broken citations, or weak critical evidence block or flag rendering rather than producing a confident unreliable report.

See [Report Generator](report-generator.md) for the detailed flow and commands.

## Healing And Backfill

### Dimension Healing

`db/heal_dimensions.py`:

- canonicalizes aliases
- marks known-bad URLs stale
- flags wrong-company owned docs
- writes audit records
- does not delete chunks or sources

### Coverage Backfill

`db/heal_coverage_status.py`:

- reads active source/chunk rows
- generates assertions
- upserts assertions
- refreshes all rollups
- returns validation counts, unknown rows, and conflicts

### Scope Closure

`synthesize/close_coverage_scope.py`:

- targets unknown, planned, or partial gaps
- searches via acquisition lanes
- classifies each candidate with coverage verdicts
- ingests accepted evidence
- skips irrelevant or weak candidates
- reports review items
- refreshes rollups

It does not mark absence from silence.
