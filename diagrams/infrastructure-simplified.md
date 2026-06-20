# Infrastructure Overview (Simplified)

## System Summary

CI Engine is JFrog's competitive-intelligence engine, running on Google Cloud Platform
(project `jfrog-intel-rag`, region `europe-west1`) with a small, serverless-first footprint.
Two serverless services answer questions from a single Postgres data store; one direction of
data flow keeps internet access confined to the acquisition layer. A separately-managed VM
runs the OpenClaw Telegram bot that talks to the engine as a normal client.

## Major Components

### Console (Cloud Run + FastAPI)
- **Purpose**: The editorial report console and grounded chat that human analysts use.
- **Contains**: The `ci-ui` Cloud Run service (uvicorn/FastAPI), fronted by Google SSO; reads only stored evidence from the database.

### MCP Server (Cloud Run)
- **Purpose**: A read-only retrieval and report tool server exposing the corpus to programmatic clients (including OpenClaw).
- **Contains**: The `ci-mcp` Cloud Run service serving MCP tools (`search`, `get_competitor`, `compare_competitors`, etc.) over HTTP.

### Database (Cloud SQL Postgres + pgvector)
- **Purpose**: The single source of truth: corpus, relationship graph, and lifecycle ledger that both services read from.
- **Contains**: Cloud SQL instance `ci-db` / database `ci`, with pgvector for embedding similarity search.

### Embeddings (Vertex AI gemini-embedding-001)
- **Purpose**: Turns text and queries into vectors for similarity search at ingest and at answer time.
- **Contains**: Vertex AI `gemini-embedding-001` (1536 dims), the only GCP AI service called on the answer path.

### Secrets (GCP Secret Manager)
- **Purpose**: Holds all credentials so nothing is hardcoded or committed.
- **Contains**: `anthropic-key`, `tavily-key`, `context7-key`, `telegram-token`, read via the runtime service account.

### Acquisition APIs (Anthropic / Tavily / Context7)
- **Purpose**: External sources the engine researches to build the knowledge base; reached only by the acquisition layer, never at answer time.
- **Contains**: Anthropic (Claude models + web research), Tavily search, and Context7, called by ingestion CLIs/jobs.

### Telegram Bot VM (Compute Engine + OpenClaw)
- **Purpose**: A chat-based front door over Telegram that queries the engine as an MCP client.
- **Contains**: The manually-managed `openclaw-gateway` Compute Engine VM running OpenClaw; outside the engine's container images.

## Data Flow

1. **Acquisition (internet-facing, one-time/scheduled):** Ingestion jobs research the external
   Acquisition APIs, embed the results via Vertex AI, and write cited evidence into the Database.
   This is the only layer that touches the public internet.
2. **Answering (read-only):** A request enters through the Console (analyst via SSO) or the
   MCP Server (programmatic client / Telegram Bot VM). The query is embedded by Vertex AI, matched
   against active rows in the Database via pgvector, and returned as a cited answer.
3. **Supporting:** Both services authenticate to the Database with the runtime identity, pull
   credentials from Secret Manager, and never call external sources at answer time.

## Key Boundaries

| Boundary | Inside | Outside |
|----------|--------|---------|
| Internet access | Acquisition layer (ingestion jobs) reaching Anthropic / Tavily / Context7 | Console and MCP Server at answer time (read stored evidence only) |
| Source of truth | Cloud SQL `ci-db` (corpus, graph, ledger) | Any per-service local state or cache |
| GCP-hosted engine | Console, MCP Server, Database, Embeddings, Secret Manager | Telegram Bot VM (manually managed, separate from engine images) |
| Container images | Console and MCP Server (built and deployed from the repo) | OpenClaw VM stack (provisioned by hand, outside the repo) |
