# Architecture Overview (Simplified)

## System Summary

The **CI Engine** is a grounded, citation-strict **RAG** (retrieval-augmented
generation) system that builds and serves a competitive-intelligence knowledge
base about JFrog and its competitors in software-supply-chain-security.

It is built on one hard rule: **data flows in a single direction**. A single
*acquisition* layer is the only code allowed to touch the internet. Everything
it learns is compiled into structured, cited evidence and written into one
database — the single source of truth. Every reader (chat, reports) consumes
that database through one read-only tool surface. Nothing downstream ever
writes back, and no fact is emitted without an active, stored citation; if no
active evidence exists, the system says "no recent data found" rather than
guessing.

## Major Components

### 1. Configuration & Ontology (config.yaml)
- **Purpose**: Declares what to research and how the system behaves — the
  competitor list, technical/business dimensions, model assignments, and every
  tunable threshold.
- **Contains**: The single `config.yaml` plus the canonical dimension/alias
  ontology that scopes acquisition and labels all stored evidence.

### 2. Acquisition (Anthropic web research + Tavily + Context7)
- **Purpose**: The only internet-touching layer — finds, fetches, and scores
  candidate source material for each company and dimension.
- **Contains**: Anthropic web-research lane (Claude `web_search`), Tavily and
  Context7 topic lanes, direct HTTP/RSS fetch, relevance scoring, and
  provenance snapshots.

### 3. Synthesis / Ingestion Pipeline (Claude + Vertex AI)
- **Purpose**: Turns raw candidates into stored, embedded, cited evidence — the
  one-directional write path.
- **Contains**: LLM compilation of raw text into structured cited claims
  (Synthesis via Claude), chunking, embedding (Vertex AI
  `gemini-embedding-001`), and extraction of entities, relationships, and
  coverage assertions — with non-destructive freshness (older sources are
  superseded, never deleted).

### 4. Knowledge Base (Cloud SQL Postgres + pgvector)
- **Purpose**: The single source of truth — the only place facts live and the
  only thing readers read.
- **Contains**: Sources, chunks (pgvector HNSW vector index), entities,
  relationships, citations, and the coverage/freshness ledger, all under
  Cloud SQL IAM auth.

### 5. Retrieval + MCP Tool Surface (read-only)
- **Purpose**: The single shared read-only API that every consumer uses to query
  the knowledge base.
- **Contains**: Vector + keyword retrieval over **active** chunks (embedding
  queries via Vertex AI), exposed as the MCP tool set (`search`,
  `get_competitor`, `compare_competitors`, `latest_updates`, `coverage_status`,
  plus report/chat builder tools). Never writes, never browses.

### 6. Chat Console (Claude)
- **Purpose**: Answers grounded competitive-intelligence questions on demand.
- **Contains**: A plan -> execute -> answer pipeline (Claude) that calls MCP
  tools, ranks evidence, and writes citation-checked answers; surfaces gaps as
  explicit "missing" reasons.

### 7. Report Generator (CrewAI)
- **Purpose**: Produces full, cited competitive-intelligence reports in batch.
- **Contains**: A frozen evidence pack built from the MCP read tools, multiple
  LLM analyst sections (Claude), a grounding validator, and pdf/html/json
  rendering (CrewAI scaffold over a deterministic workflow).

## Data Flow

The system is strictly one-directional for corpus building:

```
Configuration & Ontology (config.yaml)
        │  what to research
        ▼
Acquisition  (Anthropic web research · Tavily · Context7)   ◄── ONLY internet access
        │  candidates
        ▼
Synthesis / Ingestion Pipeline  (Claude → chunks + embeddings via Vertex AI
        │                         + entities + relationships + coverage assertions)
        │  writes (one direction)
        ▼
Knowledge Base  (Cloud SQL Postgres + pgvector)  ── SINGLE SOURCE OF TRUTH
        ▲
        │  read-only
        ▼
Retrieval + MCP Tool Surface  (read-only)
        │                         │
        ▼                         ▼
Chat Console (Claude)      Report Generator (CrewAI)
```

Acquisition is the only arrow pointing at the internet; every other reader pulls
exclusively from the Knowledge Base through the MCP tool surface.

## Key Interactions

| From | To | What |
|------|----|------|
| Configuration & Ontology | Acquisition | Companies + dimensions defining what to research |
| Acquisition | Synthesis / Ingestion | Cited candidate source material (the only internet-sourced data) |
| Synthesis / Ingestion | Knowledge Base | Writes compiled evidence: chunks + embeddings + entities + relationships + coverage assertions |
| Vertex AI (Embeddings) | Synthesis / Ingestion + Retrieval | Document embeddings on write, query embeddings on read |
| Knowledge Base | Retrieval + MCP | Read-only vector + keyword search over active chunks |
| Retrieval + MCP | Chat Console | Evidence for grounded, cited answers |
| Retrieval + MCP | Report Generator | Frozen evidence pack for cited report sections |
