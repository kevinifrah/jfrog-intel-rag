# Deployment

This document describes how to package and deploy CI Engine, and how to redeploy after a
code, config, or report change.

CI Engine ships as two container images that run on **Cloud Run**:

- **Report console (UI)** — the FastAPI editorial reader + grounded chat (`ci_engine.ui.app:create_app`).
- **MCP server** — the read-only retrieval/report tools over streamable HTTP at `/mcp` (`ci_engine.mcp.server`).

Both images read from the same backing services:

- **Cloud SQL** (`ci-db`, database `ci`) over the Cloud SQL Python Connector with IAM auth — the single source of truth.
- **GCP Secret Manager** — `anthropic-key`, `tavily-key`, `context7-key`, `telegram-token`.
- **Vertex AI** — `gemini-embedding-001` embeddings via ADC.

```text
                          ┌─────────────────────┐
   browser + SSO ────────▶│ Cloud Run: ci-ui    │──┐
                          └─────────────────────┘  │
                          ┌─────────────────────┐  │   Cloud SQL (ci-db / ci)
   OpenClaw/MCP clients ─▶│ Cloud Run: ci-mcp   │──┼──▶ Secret Manager
                          └─────────────────────┘  │   Vertex AI (embeddings)
                                                    │
                               both run as ci-engine-sa
```

OpenClaw itself is managed manually outside this repo. Configure OpenClaw to use the deployed
`ci-mcp` service as its MCP tool server; OpenClaw owns Telegram pairing, channel auth, and any
conversation/session state.

> Acquisition/ingestion (deep map, scope closure, healing) is **not** a long-running service. Run those
> as local CLIs or one-off Cloud Run Jobs against the same Cloud SQL instance — they are the only
> internet-touching layer and are never exposed by the UI or MCP services.

## Build inputs

| Image | Dockerfile | Entry command |
|-------|-----------|----------------|
| UI    | [ops/Dockerfile.ui](../ops/Dockerfile.ui) (mirrored by root [Dockerfile](../Dockerfile)) | `uvicorn --factory ci_engine.ui.app:create_app --host 0.0.0.0 --port $PORT` |
| MCP   | [ops/Dockerfile.mcp](../ops/Dockerfile.mcp) | `python -m ci_engine.mcp.server` (binds `0.0.0.0:$PORT`) |

Both Dockerfiles honor the `PORT` env var that Cloud Run injects. They `COPY src` and `COPY reports`,
so **generated report artifacts are baked into the UI image at build time** (see
[Reports are baked into the image](#reports-are-baked-into-the-image)).

## One-time project setup

Set the working project and region:

```bash
gcloud config set project jfrog-intel-rag
gcloud config set run/region europe-west1
```

Enable the required APIs:

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com
```

Create an Artifact Registry repository for the images (once):

```bash
gcloud artifacts repositories create ci-engine \
  --repository-format=docker \
  --location=europe-west1 \
  --description="CI Engine container images"
```

### Runtime service-account roles

Both services run as `ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com`. Grant it:

```bash
SA=ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com

for ROLE in \
  roles/cloudsql.client \
  roles/cloudsql.instanceUser \
  roles/secretmanager.secretAccessor \
  roles/aiplatform.user ; do
  gcloud projects add-iam-policy-binding jfrog-intel-rag \
    --member="serviceAccount:${SA}" --role="${ROLE}"
done
```

`config.yaml` sets `database.impersonate_service_account: ci-engine-sa@…`, so the runtime SA
impersonates **itself** to mint the IAM DB token. Grant it Token Creator on itself:

```bash
gcloud iam service-accounts add-iam-policy-binding "${SA}" \
  --member="serviceAccount:${SA}" \
  --role="roles/iam.serviceAccountTokenCreator"
```

The Cloud SQL instance must already have `ci-engine-sa` as a `CLOUD_IAM_SERVICE_ACCOUNT`
database user (see [operations.md](operations.md)).

### Deployer roles

The human or CI principal running the deploy needs `roles/run.admin`,
`roles/iam.serviceAccountUser` (to deploy *as* `ci-engine-sa`), and `roles/cloudbuild.builds.editor`
(or `roles/artifactregistry.writer` if pushing images directly).

## Build the images

Using Cloud Build (no local Docker required):

```bash
REPO=europe-west1-docker.pkg.dev/jfrog-intel-rag/ci-engine

# UI image (root Dockerfile == ops/Dockerfile.ui)
gcloud builds submit --tag "${REPO}/ci-ui:latest" .

# MCP image (explicit Dockerfile)
gcloud builds submit --tag "${REPO}/ci-mcp:latest" --config=- . <<'YAML'
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "ops/Dockerfile.mcp", "-t",
           "europe-west1-docker.pkg.dev/jfrog-intel-rag/ci-engine/ci-mcp:latest", "."]
images:
  - europe-west1-docker.pkg.dev/jfrog-intel-rag/ci-engine/ci-mcp:latest
YAML
```

Prefer immutable tags for traceability — substitute `:latest` with a short git SHA, e.g.
`:$(git rev-parse --short HEAD)`, and deploy that exact tag.

## Deploy the UI service

```bash
REPO=europe-west1-docker.pkg.dev/jfrog-intel-rag/ci-engine
SA=ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com

gcloud run deploy ci-ui \
  --image="${REPO}/ci-ui:latest" \
  --region=europe-west1 \
  --service-account="${SA}" \
  --add-cloudsql-instances=jfrog-intel-rag:europe-west1:ci-db \
  --memory=2Gi --cpu=2 --timeout=600 \
  --no-allow-unauthenticated
```

Secrets are read straight from Secret Manager via the runtime SA's ADC, so no key needs to be
passed. If you prefer to inject them as env vars instead (the app falls back to
`ANTHROPIC_KEY`, `TAVILY_KEY`, etc. when present), add:

```bash
  --set-secrets=ANTHROPIC_KEY=anthropic-key:latest,TAVILY_KEY=tavily-key:latest,CONTEXT7_KEY=context7-key:latest
```

Enable Google SSO with direct Cloud Run IAP:

```bash
PROJECT_ID=jfrog-intel-rag
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
IAP_SA="service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com"

gcloud run services update ci-ui \
  --region=europe-west1 \
  --iap

gcloud run services add-iam-policy-binding ci-ui \
  --region=europe-west1 \
  --member="serviceAccount:${IAP_SA}" \
  --role="roles/run.invoker"

gcloud iap web add-iam-policy-binding \
  --resource-type=cloud-run \
  --service=ci-ui \
  --region=europe-west1 \
  --member="group:<allowed-google-group@example.com>" \
  --role="roles/iap.httpsResourceAccessor"
```

Test the console in a private browser window with an allowed Google account, then with an
unapproved account. OpenClaw must not depend on `ci-ui` or browser SSO; it should use `ci-mcp`.

## Deploy the MCP service

```bash
gcloud run deploy ci-mcp \
  --image="${REPO}/ci-mcp:latest" \
  --region=europe-west1 \
  --service-account="${SA}" \
  --add-cloudsql-instances=jfrog-intel-rag:europe-west1:ci-db \
  --memory=2Gi --cpu=2 --timeout=600 \
  --no-allow-unauthenticated \
  --set-env-vars=MCP_SHARED_TOKEN=<token>,MCP_ALLOWED_HOSTS=<host>,MCP_ALLOWED_ORIGINS=<origin>
```

MCP env vars (see [operations.md](operations.md#mcp-server)):

- `MCP_SHARED_TOKEN` — shared bearer token required on `/mcp` (set this for any non-local deployment).
- `MCP_ALLOWED_HOSTS` — comma-separated allowed hosts (include the Cloud Run hostname).
- `MCP_ALLOWED_ORIGINS` — comma-separated allowed origins.

Prefer `--no-allow-unauthenticated` plus Cloud Run IAM invoker bindings, and keep
`MCP_SHARED_TOKEN` set as defense in depth.

## Manual OpenClaw setup against MCP

OpenClaw setup remains manual. CI Engine does not run a Python Telegram adapter and does not add
Telegram-specific DB tables. OpenClaw should be the channel/agent layer, and `ci-mcp` should be its
read-only evidence tool server.

Current target shape:

- Gateway host: Compute Engine VM `openclaw-gateway` in `europe-west1-b`.
- Runtime: Docker Compose running the OpenClaw Gateway on `127.0.0.1:18789`.
- Operator access: SSH local port forward to the Gateway control UI.
- Model: Anthropic Claude Sonnet through OpenClaw provider auth.
- Evidence flow: `ci-mcp` first; web search/fetch only to validate freshness, resolve
  contradictions, or cover evidence gaps.

Open the local control UI through an SSH tunnel:

```bash
gcloud compute ssh openclaw-gateway \
  --project=jfrog-intel-rag \
  --zone=europe-west1-b \
  -- -N -L 18789:127.0.0.1:18789 -o ServerAliveInterval=30 -o ServerAliveCountMax=3
```

Then browse to `http://127.0.0.1:18789`.

### Prepare `ci-mcp` for OpenClaw

Keep `MCP_SHARED_TOKEN` set on `ci-mcp`. Include the OpenClaw Gateway host/origin in the MCP
transport allowlists:

```bash
gcloud run services update ci-mcp \
  --region=europe-west1 \
  --update-env-vars=MCP_ALLOWED_HOSTS=<ci-mcp-host>,<openclaw-host>,MCP_ALLOWED_ORIGINS=<openclaw-origin>
```

If OpenClaw calls `ci-mcp` over Cloud Run IAM, grant the OpenClaw runtime service account
`roles/run.invoker` on `ci-mcp`. If OpenClaw cannot mint Google identity tokens, keep the service
reachable according to your network design and require `MCP_SHARED_TOKEN` as the application-layer
guard.

```bash
gcloud run services add-iam-policy-binding ci-mcp \
  --region=europe-west1 \
  --member="serviceAccount:<openclaw-runtime-sa>" \
  --role="roles/run.invoker"
```

OpenClaw MCP endpoint:

```text
https://<ci-mcp-url>/mcp
Authorization: Bearer <MCP_SHARED_TOKEN>
```

Register the MCP server from inside the OpenClaw container. Retrieve the token from Secret Manager
outside the VM, paste it into the prompt below, and do not write the secret value into docs:

```bash
cd ~/openclaw

read -rsp "Paste MCP token: " MCP_SHARED_TOKEN; echo

MCP_CONFIG="$(MCP_SHARED_TOKEN="$MCP_SHARED_TOKEN" python3 -c 'import json,os; print(json.dumps({
  "url": "https://ci-mcp-v4vkevgy2a-ew.a.run.app/mcp",
  "transport": "streamable-http",
  "headers": {
    "Authorization": "Bearer " + os.environ["MCP_SHARED_TOKEN"]
  },
  "timeout": 60,
  "connectTimeout": 15,
  "supportsParallelToolCalls": True
}))')"

docker compose exec -T openclaw-gateway \
  openclaw mcp set ci-engine "$MCP_CONFIG"

unset MCP_SHARED_TOKEN MCP_CONFIG
```

Filter OpenClaw's MCP exposure to the chat-relevant tools:

```bash
docker compose exec -T openclaw-gateway \
  openclaw mcp tools ci-engine \
  --include 'search_answer_context,search,get_competitor,compare_competitors,compare_dimension,coverage_matrix,coverage_status,latest_updates,get_report_registry,search_report_sections,source_inventory,get_source_detail,find_evidence_gaps'

docker compose exec -T openclaw-gateway openclaw mcp doctor ci-engine --probe
docker compose exec -T openclaw-gateway openclaw mcp reload
```

Useful tool entrypoints for assistant behavior:

- `search_answer_context` - broad answer context across DB evidence plus optional report artifacts
- `search_report_sections` - report-specific context
- `get_report_registry` - available report slugs and metadata
- `search`, `compare_dimension`, `coverage_matrix`, `source_inventory`, `get_source_detail` - targeted follow-up tools
- `latest_updates`, `find_evidence_gaps`, `coverage_status` - freshness and gap checks before
  web validation

### Manual Telegram BotFather setup

For the current VM/Docker Gateway, Telegram does not require a public Cloud Run webhook. OpenClaw
can receive Telegram messages through its channel runtime from the VM, so the VM only needs outbound
internet access plus the BotFather token in OpenClaw configuration.

1. In Telegram, message `@BotFather`.
2. Run `/newbot`, choose a display name and username, and give the token to OpenClaw's Telegram
   channel configuration. Do not commit or document the token value.
3. Run `/setcommands` in BotFather using the commands OpenClaw should expose. At minimum:

   ```text
   start - Start the CI assistant
   help - Show help
   ```

4. For groups, run `/setprivacy` and keep privacy enabled unless every group message should reach
   OpenClaw.
5. Complete OpenClaw's Telegram DM pairing/allowlist flow manually in the OpenClaw Gateway.

### Manual OpenClaw Gateway setup

1. Install/run OpenClaw using its official local Node flow, Docker flow, or Compute Engine Gateway
   flow.
2. Open the OpenClaw Gateway control UI.
3. Add/configure the Telegram channel with the BotFather token.
4. Add/configure the CI Engine MCP server:

   ```text
   URL: https://<ci-mcp-url>/mcp
   Auth: Bearer <MCP_SHARED_TOKEN>
   ```

5. Configure OpenClaw instructions so answers are grounded in MCP results from `ci-mcp` and do not
   rely on model memory for competitive facts. The versioned OpenClaw mission prompt lives at
   [ops/openclaw/AGENTS.md](../ops/openclaw/AGENTS.md).
6. Tell OpenClaw to start with `search_answer_context` for ordinary questions, then use the
   targeted MCP tools for follow-up evidence. When MCP evidence is missing, stale, contradictory,
   or high impact, let OpenClaw validate and cover gaps with web search/fetch and label those
   findings as external validation.
7. Test the same question in `ci-ui` and OpenClaw/Telegram. Expect the same evidence base and MCP
   tools, though wording may differ because OpenClaw is now the answer writer.

Install the versioned mission prompt into the Gateway workspace:

```bash
# From a local checkout.
gcloud compute scp ops/openclaw/AGENTS.md \
  openclaw-gateway:~/openclaw/AGENTS.md \
  --project=jfrog-intel-rag \
  --zone=europe-west1-b

# On the VM.
cd ~/openclaw
docker compose cp AGENTS.md openclaw-gateway:/home/node/.openclaw/workspace/AGENTS.md
docker compose exec -u root -T openclaw-gateway \
  chown node:node /home/node/.openclaw/workspace/AGENTS.md

docker compose exec -T openclaw-gateway openclaw config patch --stdin <<'JSON5'
{
  agents: {
    defaults: {
      workspace: "/home/node/.openclaw/workspace",
      skipBootstrap: true,
      contextInjection: "always"
    }
  }
}
JSON5

docker compose restart openclaw-gateway
```

If OpenClaw tool policy is tightened, keep both `ci-engine__*` MCP tools and web validation tools
available. In sandboxed sessions, OpenClaw-managed MCP tools may also require `bundle-mcp` or
`group:plugins` in the sandbox tool allowlist.

If the full OpenClaw Gateway runs on Cloud Run, use `--min-instances=1 --max-instances=1
--no-cpu-throttling`. If it needs durable local state, prefer OpenClaw's official Compute
Engine/Docker deployment instead of Cloud Run.

## Redeploy

### After a code change

Rebuild the affected image(s) and roll out a new revision:

```bash
REPO=europe-west1-docker.pkg.dev/jfrog-intel-rag/ci-engine
TAG=$(git rev-parse --short HEAD)

gcloud builds submit --tag "${REPO}/ci-ui:${TAG}" .
gcloud run deploy ci-ui --image="${REPO}/ci-ui:${TAG}" --region=europe-west1
```

`gcloud run deploy` with an existing service keeps all previously set flags (service account,
Cloud SQL, secrets, env) unless you override them, and shifts 100% traffic to the new revision.
Do the same with `ops/Dockerfile.mcp` → `ci-mcp` when MCP code changes.

### After a config-only change

`config.yaml` is baked into the image, so a config edit still requires a rebuild + redeploy of the
affected service(s). There is no separate config push.

### After generating new reports

The UI image bakes in `reports/`. New or regenerated dossiers are **not** visible to a running
service until you rebuild and redeploy the UI image:

```bash
# regenerate, then:
gcloud builds submit --tag "${REPO}/ci-ui:${TAG}" .
gcloud run deploy ci-ui --image="${REPO}/ci-ui:${TAG}" --region=europe-west1
```

> Reminder: a batch run (`--all-companies` / `--deep-map-now` / `--competitors`) drops "Part 1 ·
> Market & strategic context" from every customer dossier and also writes the standalone
> `reports/market/` report. Regenerate the batch before rebuilding so the image carries both.

See [Reports are baked into the image](#reports-are-baked-into-the-image) for the longer-term option.

### Rollback

List revisions and shift traffic back to a known-good one:

```bash
gcloud run revisions list --service=ci-ui --region=europe-west1
gcloud run services update-traffic ci-ui --region=europe-west1 --to-revisions=<REVISION>=100
```

## Run a container locally

Useful for verifying the image before deploying. Mount ADC and pass the project so Secret Manager
and the Cloud SQL connector work:

```bash
docker build -f ops/Dockerfile.ui -t ci-ui:dev .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e GOOGLE_CLOUD_PROJECT=jfrog-intel-rag \
  -v "$HOME/.config/gcloud:/root/.config/gcloud:ro" \
  ci-ui:dev
# open http://127.0.0.1:8080
```

For a network-free smoke test, set the secret env-var fallbacks
(`ANTHROPIC_KEY`, `TAVILY_KEY`, …) instead of mounting ADC; DB-backed pages will still need Cloud SQL access.

## Reports are baked into the image

Today the UI serves report artifacts straight from the on-disk `reports/<slug>/` tree that the
Dockerfile copies in (`ReportArtifactStore`). That keeps deployment simple but couples report
content to image builds: **publishing new dossiers means rebuilding and redeploying the UI image.**

The store is an abstraction seam. A future revision can point it at a GCS bucket (or Cloud SQL
metadata + object storage) so reports publish without an image rebuild; see the storage decision in
[chat-and-report-console.md](chat-and-report-console.md#storage-decision). Until then, treat report
regeneration as a deploy step.

## Verify a deployment

```bash
# UI is serving
curl -fsS "$(gcloud run services describe ci-ui --region=europe-west1 --format='value(status.url)')/" >/dev/null && echo ok

# DB connectivity from your shell (same SA path the services use)
.venv/bin/python -m ci_engine.db.doctor
```

For the MCP service, call `/mcp` with the `MCP_SHARED_TOKEN` bearer header and confirm a tool list
response. If DB calls fail, re-check the runtime SA roles and the Cloud SQL IAM database user as
described in [operations.md](operations.md#troubleshooting).
