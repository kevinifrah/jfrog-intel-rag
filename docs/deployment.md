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
   browser ───────────▶│ Cloud Run: ci-ui    │──┐
                       └─────────────────────┘  │
                       ┌─────────────────────┐  │   Cloud SQL (ci-db / ci)
   MCP clients ───────▶│ Cloud Run: ci-mcp   │──┼──▶ Secret Manager
                       └─────────────────────┘  │   Vertex AI (embeddings)
                                                 │
                            both run as ci-engine-sa
```

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
  --allow-unauthenticated
```

Secrets are read straight from Secret Manager via the runtime SA's ADC, so no key needs to be
passed. If you prefer to inject them as env vars instead (the app falls back to
`ANTHROPIC_KEY`, `TAVILY_KEY`, etc. when present), add:

```bash
  --set-secrets=ANTHROPIC_KEY=anthropic-key:latest,TAVILY_KEY=tavily-key:latest,CONTEXT7_KEY=context7-key:latest
```

> Drop `--allow-unauthenticated` if the console should be private; front it with IAP or require
> authenticated invokers instead.

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
