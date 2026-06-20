# Infrastructure Documentation

## Overview

CI Engine is JFrog's competitive-intelligence engine. It runs entirely on **Google Cloud Platform**
(project `jfrog-intel-rag`, region `europe-west1`) with a deliberately small, serverless-first
footprint. There is **no Terraform, Pulumi, Kubernetes, Helm, or `cloudbuild.yaml`** in the repo —
topology is defined by three Dockerfiles plus `gcloud` runbooks in `docs/deployment.md` and
`docs/operations.md`. Provisioning is performed by hand against documented `gcloud` commands.

The hosted footprint is **two Cloud Run services** that run as the same service account and share
three backing GCP services:

- `ci-ui` — the FastAPI/uvicorn editorial Report console + grounded chat
  (`docs/deployment.md:8`, entry `ci_engine.ui.app:create_app`, `Dockerfile:17`).
- `ci-mcp` — the read-only MCP retrieval/report tool server over streamable HTTP at `/mcp`
  (`docs/deployment.md:9`, entry `ci_engine.mcp.server`, `ops/Dockerfile.mcp:17`,
  `src/ci_engine/mcp/server.py:31`).

Shared backing services: **Cloud SQL** (`ci-db`/`ci`, Postgres + pgvector — the single source of
truth), **GCP Secret Manager**, and **Vertex AI** (embeddings). The acquisition/ingestion layer is
**not** a hosted service: it runs as local CLIs or one-off Cloud Run Jobs and is the only layer that
touches the public internet (`docs/deployment.md:32-34`). All Dockerfiles use `python:3.12-slim` +
`uv` and honor Cloud Run's injected `PORT` env var (`Dockerfile:1-17`).

## Components

### Compute

| Resource | Type | Source | Entry / CMD | Notes |
|----------|------|--------|-------------|-------|
| `ci-ui` | Cloud Run service | root `Dockerfile` (== `ops/Dockerfile.ui`) | `uvicorn --proxy-headers --forwarded-allow-ips='*' --factory ci_engine.ui.app:create_app --host 0.0.0.0 --port ${PORT}` (`Dockerfile:17`) | `--memory=2Gi --cpu=2 --timeout=600`, `--no-allow-unauthenticated`, scales to zero (`docs/deployment.md:142-148`, `docs/operations.md:611`) |
| `ci-mcp` | Cloud Run service | `ops/Dockerfile.mcp` | `python -m ci_engine.mcp.server` → binds `0.0.0.0:$PORT` (`ops/Dockerfile.mcp:17`, `src/ci_engine/mcp/server.py:29-31`) | Same memory/cpu/timeout; scales to zero unless persistent OpenClaw/MCP sessions require `--min-instances=1` (`docs/operations.md:611`) |
| `openclaw-gateway` | Compute Engine VM (zone `europe-west1-b`) | manual, outside repo | Docker Compose service `openclaw-gateway` on `127.0.0.1:18789` (`docs/deployment.md:216-218`, `docs/operations.md:289-291`) | Hosts OpenClaw Telegram bot; **not** part of the CI Engine images; repo carries only `ops/openclaw/{AGENTS.md,README.md}` |

Base image for all three Dockerfiles: `python:3.12-slim`, `pip install uv`, `uv pip install --system -e .`,
`EXPOSE 8080`, `ENV PORT=8080` (`Dockerfile:1-16`, `ops/Dockerfile.mcp:1-16`, `ops/Dockerfile.ui:1-16`).
Each image `COPY src` and `COPY reports`, so generated report artifacts and `config.yaml` are **baked
into the image at build time** — config or report changes require a rebuild + redeploy
(`Dockerfile:11-12`, `docs/deployment.md:43-45`, `docs/deployment.md:636-644`).

**Ingestion compute (not a service):** `deep_map`, `close_coverage_scope`, `heal_dimensions`,
`heal_coverage_status`, `synthesize.run`, and report generation run as local CLIs or one-off
Cloud Run Jobs against the same Cloud SQL instance (`docs/deployment.md:32-34`,
`docs/operations.md:84-118`, `docs/operations.md:336-372`). These are the only internet-touching
compute.

### Data Stores

| Resource | Engine | Source | Details |
|----------|--------|--------|---------|
| Cloud SQL `ci-db` / database `ci` | PostgreSQL + **pgvector** | `config.yaml:7-14`, `src/ci_engine/db/connection.py:21-26` | Single source of truth (corpus + relationship graph + lifecycle ledger). Connection name `jfrog-intel-rag:europe-west1:ci-db`. Driver `pg8000`. Schema applied manually via `gcloud sql connect ci-db --user=postgres` then `\i schema.sql` (`docs/operations.md:48-60`) |

- **Access path:** Cloud SQL Python Connector (`google.cloud.sql.connector.Connector`) with
  `refresh_strategy="lazy"` wrapped by a singleton SQLAlchemy engine `postgresql+pg8000://`
  (`src/ci_engine/db/connection.py:71-83`). `pool_pre_ping=True`; connector timeout from
  `database.connect_timeout_s: 15` (`config.yaml:14`, `connection.py:75`).
- **Vector type:** custom `VECTOR` subclass of `pgvector.sqlalchemy.VECTOR` returning Python lists
  (`connection.py:33-47`). Embeddings stored at **1536 dims** (`config.yaml:67-68`).
- **Tables** (`db/schema.sql`, per CLAUDE.md): `sources`, `chunks` (pgvector HNSW index on active
  rows), `entities`, `relationships`, `source_citations`, `source_healing_audit`,
  `dimension_coverage_assertions`, `dimension_coverage_status`, `dimension_coverage_audit`.
- **Cloud Run attachment:** services attach the instance with
  `--add-cloudsql-instances=jfrog-intel-rag:europe-west1:ci-db` (`docs/deployment.md:146,193`).
- No password is ever used — IAM auth only (see Security).

### Networking

- **Cloud Run ingress (both services):** deployed `--no-allow-unauthenticated`
  (`docs/deployment.md:148,195`). Public reachability is mediated by Cloud Run IAM invoker bindings.
- **`ci-ui` ingress:** Google SSO via **direct Cloud Run IAP** (`gcloud run services update ci-ui --iap`),
  granting the IAP service agent `service-<PROJECT_NUMBER>@gcp-sa-iap.iam.gserviceaccount.com`
  `roles/run.invoker`, and gating users through an IAP group binding
  (`roles/iap.httpsResourceAccessor`) (`docs/deployment.md:159-181`). Uvicorn launched with
  `--proxy-headers --forwarded-allow-ips='*'` to trust Cloud Run's proxy headers (`Dockerfile:17`).
- **`ci-mcp` ingress:** streamable HTTP at `/mcp`, binds `HOST = "0.0.0.0"`, `PORT` from env
  (`src/ci_engine/mcp/server.py:29-31`). Three layers of guard:
  - `MCP_SHARED_TOKEN` — bearer token enforced by `SharedTokenMiddleware`
    (`server.py:48-71`); set for any non-local deployment (`docs/deployment.md:201`).
  - `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` — comma-separated allowlists feeding MCP
    `TransportSecuritySettings` (host/Origin validation against DNS-rebinding); defaults to
    localhost-only when unset (`server.py:33-46,95-109`, `docs/deployment.md:202-203`).
  - Cloud Run IAM invoker bindings (`roles/run.invoker`) for callers that mint Google identity
    tokens, e.g. an OpenClaw runtime SA (`docs/deployment.md:245-255`).
- **Cloud SQL connectivity:** no VPC/private IP configured in code; connectivity is via the Cloud
  SQL Python Connector (secure tunnel) using the attached instance, not a direct network route
  (`connection.py:71-81`). UNVERIFIED whether public vs private IP — connector abstracts it.
- **OpenClaw VM access:** operator reaches the gateway control UI via SSH local port-forward
  `127.0.0.1:18789` (`docs/deployment.md:223-232`). Telegram ingress is outbound-only from the VM
  (OpenClaw channel runtime, long-poll `getUpdates`); **no public CI Engine webhook**
  (`docs/deployment.md:310-315`, `docs/operations.md:293-294`).
- **Artifact Registry:** Docker repo `europe-west1-docker.pkg.dev/jfrog-intel-rag/ci-engine` holds
  `ci-ui` and `ci-mcp` images (`docs/deployment.md:71-75,117`).
- Known live MCP URL example: `https://ci-mcp-v4vkevgy2a-ew.a.run.app/mcp` (`docs/deployment.md:274`).

### Storage

- **Container images:** Artifact Registry repo `ci-engine` in `europe-west1`
  (`docs/deployment.md:71-75`).
- **Report artifacts:** served from the on-disk `reports/<slug>/` tree baked into the `ci-ui` image
  by `COPY reports` (`Dockerfile:12`, `docs/deployment.md:636-644`). The `ReportArtifactStore` is an
  abstraction seam; a future revision may point it at a GCS bucket so reports publish without an
  image rebuild — **not currently implemented** (`docs/deployment.md:638-644`).
- **Provenance snapshots:** `raw_snapshots/` is a local, gitignored provenance artifact directory
  (CLAUDE.md repo map). UNVERIFIED whether it is persisted anywhere in the cloud — it appears local
  to ingestion runs only.
- **WeasyPrint/Fontconfig cache:** writable cache under `/tmp` set by the PDF renderer
  (`docs/operations.md:444`).
- No object-storage bucket, filesystem mount, or persistent volume is provisioned for the Cloud Run
  services; they are stateless apart from the DB.

### Security

- **Single runtime identity:** both Cloud Run services run as
  `ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com` (`docs/deployment.md:79,140,193`).
- **Cloud SQL IAM database auth (no password):** `enable_iam_auth: true`, IAM DB user
  `ci-engine-sa@jfrog-intel-rag.iam` of type `CLOUD_IAM_SERVICE_ACCOUNT`
  (`config.yaml:11-12`, `connection.py:58-59,100-102`).
- **Self-impersonation for the IAM DB token:** `database.impersonate_service_account` is set to the
  runtime SA itself (`config.yaml:13`). On Cloud Run (`K_SERVICE` present), `_connector_credentials`
  returns `None` so the ambient runtime SA is used directly; locally, ADC impersonates
  `ci-engine-sa` via `impersonated_credentials.Credentials` and `cloud-platform` scope
  (`connection.py:140-168`). Requires `roles/iam.serviceAccountTokenCreator` on itself
  (`docs/deployment.md:94-101`).
- **Runtime SA IAM roles** (`docs/deployment.md:82-91`): `roles/cloudsql.client`,
  `roles/cloudsql.instanceUser`, `roles/secretmanager.secretAccessor`, `roles/aiplatform.user`.
- **Secrets — GCP Secret Manager** (project `jfrog-intel-rag`): `anthropic-key`, `tavily-key`,
  `context7-key`, `telegram-token` (`config.yaml`/CLAUDE.md, `docs/operations.md:17-22`). Read via
  `secretmanager.SecretManagerServiceClient` at path
  `projects/{project}/secrets/{name}/versions/latest`, with an env-var fallback
  (`<NAME-UPPER-UNDERSCORE>`, e.g. `ANTHROPIC_KEY`) for tests/local
  (`src/ci_engine/secrets.py:4-24`). Optional injection as Cloud Run env vars via `--set-secrets`
  (`docs/deployment.md:155-156`). No secret is hardcoded or committed.
- **ADC everywhere:** Application Default Credentials power Secret Manager, the Cloud SQL connector,
  and Vertex AI embeddings — no static keys for GCP services (`secrets.py`, `connection.py`,
  `embed/gemini.py:34-39`).
- **MCP auth:** `MCP_SHARED_TOKEN` compared with `hmac`-safe logic in `SharedTokenMiddleware`
  (`server.py:3-4,48-71`) plus host/origin transport security and Cloud Run IAM.
- **Deployer IAM:** the human/CI deploy principal needs `roles/run.admin`,
  `roles/iam.serviceAccountUser`, and `roles/cloudbuild.builds.editor` (or
  `roles/artifactregistry.writer`) (`docs/deployment.md:107-110`).
- **Untrusted-source posture:** raw scraped content is treated as data, not instructions (CLAUDE.md);
  enforced in the acquisition/synthesis layer rather than infra.
- **OpenClaw secrets:** BotFather Telegram token lives in `~/openclaw/.env` (mode `600`) on the VM,
  outside this repo (`docs/deployment.md:336-356`, `docs/operations.md:320`).

### External Services

| Service | Reached by | Endpoint / model | Source |
|---------|-----------|------------------|--------|
| **Vertex AI** (`gemini-embedding-001`) | embed layer (used by ingestion + retrieval query embedding) | `genai.Client(vertexai=True, project=..., location="europe-west1")`, 1536 dims, task types `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` | `embed/gemini.py:11,33-39`, `config.yaml:66-70` |
| **Anthropic API** (Claude) | acquisition + report/chat generation | models per `config.yaml:17-64` (synthesis `claude-opus-4-8`, report/chat/web `claude-sonnet-4-6`, planner/splitter/relevance `claude-haiku-4-5`); key `anthropic-key`; SDK `anthropic>=0.40` | `config.yaml:16-64`, `pyproject.toml:6` |
| **Tavily** | acquisition lane + chat freshness checks | `tavily-python>=0.5`; key `tavily-key`; depths/results in `chat:`/`ingestion:` config | `pyproject.toml:8`, `config.yaml:116-126,137-148` |
| **Context7** | acquisition lane | MCP endpoint `https://mcp.context7.com/mcp`, content `https://context7.com{library_id}`; key `context7-key`; `crewai-tools[mcp]` | `src/ci_engine/acquire/context7_lane.py:17,261-262`, `pyproject.toml:6` |
| **Anthropic web research** | acquisition web lane | `web_research` model with `web_search_max_uses: 12` | `config.yaml:49-54` |
| **Telegram (Bot API)** | OpenClaw VM only (outbound) | BotFather bot; long-poll `getUpdates`; token `telegram-token`/`TELEGRAM_BOT_TOKEN`; CI Engine itself does **not** call Telegram | `docs/deployment.md:309-356`, `docs/operations.md:280-294` |

External APIs (Anthropic, Tavily, Context7, Anthropic web research) are only reached by the
**acquisition layer** — never at answer/report time, which reads only stored active evidence
(CLAUDE.md non-negotiables). Vertex AI is the exception: `embed_query` is called at retrieval time to
embed the query, but it produces a vector, not facts (`embed/gemini.py:90-92`).

## Relationships

Bidirectional map of who connects to what.

- **browser/SSO ⇄ `ci-ui`:** browser reaches `ci-ui` through Cloud Run IAP + Google group binding;
  `ci-ui` trusts proxy headers (`--forwarded-allow-ips='*'`) (`docs/deployment.md:19,159-181`,
  `Dockerfile:17`).
- **OpenClaw / MCP clients ⇄ `ci-mcp`:** clients call `/mcp` with `Bearer MCP_SHARED_TOKEN` and must
  pass host/origin allowlists + Cloud Run IAM; `ci-mcp` exposes read-only retrieval/report tools
  (`docs/deployment.md:22,257-262`, `server.py:29-71`).
- **`ci-ui` ⇄ Cloud SQL `ci-db`:** via Cloud SQL connector + IAM auth as `ci-engine-sa`; instance
  attached with `--add-cloudsql-instances` (`connection.py:50-83`, `docs/deployment.md:146`).
- **`ci-mcp` ⇄ Cloud SQL `ci-db`:** identical connector/IAM path; MCP tools read corpus, rollups,
  vector search (`server.py:26`, `connection.py:50-83`, `docs/deployment.md:193`).
- **`ci-ui` ⇄ Vertex AI:** chat embeds queries via `embed_query` for vector retrieval
  (`embed/gemini.py:90-92`).
- **`ci-mcp` ⇄ Vertex AI:** `search` tool embeds queries for retrieval (`server.py:17` → retriever
  → `embed_query`).
- **`ci-ui` / `ci-mcp` ⇄ Secret Manager:** read `anthropic-key`/`tavily-key`/`context7-key` via SA
  ADC (`secrets.py:16-24`); chat/report use Anthropic + bounded Tavily.
- **`ci-engine-sa` ⇄ itself (IAM):** self-impersonation mints the Cloud SQL IAM DB token; requires
  Token Creator on self (`connection.py:140-156`, `docs/deployment.md:94-101`).
- **Ingestion CLIs/Jobs ⇄ Cloud SQL + external APIs:** `deep_map`/scope-closure/healing write
  sources/chunks/embeddings/assertions to `ci-db` and reach Anthropic/Tavily/Context7/Vertex AI;
  the only internet-touching compute (`docs/deployment.md:32-34`, `docs/operations.md:84-118`).
- **`openclaw-gateway` VM ⇄ `ci-mcp`:** registers `ci-engine` MCP server at the `ci-mcp` URL with
  bearer token, MCP-first evidence policy (`docs/deployment.md:264-298`).
- **`openclaw-gateway` VM ⇄ Telegram + Anthropic:** receives Telegram messages (outbound long-poll)
  and answers using Claude Sonnet via OpenClaw provider auth (`docs/deployment.md:218-219,309-315`).
- **operator ⇄ `openclaw-gateway` VM:** SSH local port-forward to `127.0.0.1:18789`
  (`docs/deployment.md:223-232`).
- **Cloud Build ⇄ Artifact Registry ⇄ Cloud Run:** `gcloud builds submit` pushes `ci-ui`/`ci-mcp`
  images to Artifact Registry; `gcloud run deploy` pulls them into new revisions
  (`docs/deployment.md:117-131,142-148,578-584`).

**Dependency chain (answer path):** user → (`ci-ui` IAP | `ci-mcp` token) → retrieve() →
`embed_query` (Vertex AI) → pgvector search on active `chunks` (Cloud SQL) → optional Anthropic
answer synthesis → cited response. No external fact lookup at answer time.

## Environment Differences

The repo defines a **single production environment** in `europe-west1`; there is no dev/staging/prod
split in code or config. `config.yaml` carries one set of project IDs (`config.yaml:3-14`). The
meaningful variations are **runtime context**, not separate environments:

- **Cloud Run vs local:** `_running_on_cloud_run()` keys on `K_SERVICE`
  (`connection.py:167-168`). On Cloud Run the ambient runtime SA is used directly (no impersonation,
  `connection.py:144-145`); locally, ADC impersonates `ci-engine-sa` (`connection.py:147-156`).
- **Secrets source:** production reads Secret Manager; local/tests fall back to env vars
  (`ANTHROPIC_KEY`, `TAVILY_KEY`, …) so tests run network-free (`secrets.py:11-14`).
- **MCP transport security:** unset → localhost-only allowlists for local dev; production sets
  `MCP_SHARED_TOKEN`/`MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS` (`server.py:33-46,95-109`,
  `docs/deployment.md:196-203`).
- **UI bind:** local default `127.0.0.1:8090` (`config.yaml:127-129`); container binds
  `0.0.0.0:$PORT` (Cloud Run injects `PORT`, default 8080) (`Dockerfile:17`).
- **Reports/config:** baked into the image at build time, so each deployed revision is effectively a
  pinned config+reports snapshot — there is no per-environment config push (`docs/deployment.md:586-606`).
- **Image tags:** docs recommend immutable git-SHA tags over `:latest` for traceability
  (`docs/deployment.md:133-134`).

## Unverified Items

- **UNVERIFIED (Cloud SQL networking):** whether `ci-db` uses public or private IP / a VPC connector.
  Code only uses the Cloud SQL Python Connector, which abstracts the transport
  (`connection.py:71-81`); no VPC/private-IP flag appears in code or the deploy commands.
- **UNVERIFIED (Cloud Run autoscaling):** exact min/max instances and concurrency are not pinned in
  the standard deploy commands; docs note scale-to-zero and an optional `--min-instances=1` for
  persistent MCP sessions, but no enforced values exist in code (`docs/operations.md:611`,
  `docs/deployment.md:564-566`).
- **UNVERIFIED (Cloud Run Jobs):** ingestion "one-off Cloud Run Jobs" are described as a deployment
  option but no Job resource, manifest, or `gcloud run jobs` command is defined in the repo
  (`docs/deployment.md:32-34`).
- **UNVERIFIED (`raw_snapshots/` persistence):** the provenance snapshot dir is local + gitignored;
  no cloud persistence (e.g. GCS) is wired for it (CLAUDE.md repo map).
- **UNVERIFIED (GCS reports backend):** the GCS-bucket report store is documented as a future seam,
  not implemented (`docs/deployment.md:638-644`).
- **UNVERIFIED (OpenClaw VM provisioning):** the `openclaw-gateway` Compute Engine VM, its Docker
  Compose stack, and Telegram wiring are managed manually outside the repo; the repo holds only
  `ops/openclaw/{AGENTS.md,README.md}` (`docs/deployment.md:216-218`, `ops/` listing).
- **UNVERIFIED (`telegram-token` usage by hosted services):** the secret `telegram-token` exists in
  Secret Manager, but neither Cloud Run service calls Telegram; the live bot reads its token from the
  VM's `~/openclaw/.env`. The Secret Manager copy's consumer is unconfirmed
  (`docs/operations.md:17-21,320`).
- **UNVERIFIED (Cloud Build trigger):** builds are invoked manually via `gcloud builds submit`; no
  automated build trigger or `cloudbuild.yaml` exists in the repo (`docs/deployment.md:114-131`).
