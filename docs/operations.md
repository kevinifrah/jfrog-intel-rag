# Operations

This document explains how to run CI Engine locally and safely operate the database-backed workflows.

## Infrastructure Defaults

Configured in `src/ci_engine/config.yaml`:

- GCP project: `jfrog-intel-rag`
- Region: `europe-west1`
- Cloud SQL instance: `ci-db`
- Cloud SQL connection name: `jfrog-intel-rag:europe-west1:ci-db`
- Database: `ci`
- DB driver: `pg8000`
- IAM DB user: `ci-engine-sa@jfrog-intel-rag.iam`
- Service account: `ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com`
- Secrets:
  - `anthropic-key`
  - `tavily-key`
  - `context7-key`
  - `telegram-token`

Never document or commit secret values.

## Local Setup

Install dependencies:

```bash
cd /Users/kevinifrah/jfrog-intel-rag
uv sync
```

Set GCP project and authenticate ADC:

```bash
gcloud config set project jfrog-intel-rag
gcloud auth application-default login
```

If your local identity needs to impersonate the CI service account, it must have permission to impersonate:

- `ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com`

The service account must be able to connect to Cloud SQL and use IAM DB auth.

## Apply Schema

Connect to Cloud SQL:

```bash
gcloud sql connect ci-db --user=postgres --database=ci --project=jfrog-intel-rag
```

Inside `psql`:

```sql
\i /Users/kevinifrah/jfrog-intel-rag/src/ci_engine/db/schema.sql
\q
```

Expected behavior:

- existing extension/table/index notices are okay
- `CREATE TABLE` lines may appear even when tables already exist because schema uses `IF NOT EXISTS`
- grants are re-applied

## Doctor

Run:

```bash
.venv/bin/python -m ci_engine.db.doctor
```

This prints:

- DB connection settings without secrets
- ADC summary
- healthcheck result
- actionable IAM error messages when auth fails

## Deep Map

Run full `deep_map_now`:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map
```

Run one company:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map --competitor JFrog
```

Limit candidate volume:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map \
  --competitor JFrog \
  --max-candidates-per-dimension 2
```

Deep map:

- preflights the DB
- gathers candidates by dimension
- ingests accepted evidence
- updates coverage
- prints a JSON report

## Ingest One URL

```bash
.venv/bin/python -m ci_engine.synthesize.run \
  --competitor JFrog \
  --url https://jfrog.com/platform
```

This is useful for targeted ingestion and debugging.

## Retrieval Examples

Snyk package firewall:

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

JFrog package firewall:

```bash
.venv/bin/python - <<'PY'
import json
from ci_engine.retrieve import retrieve

result = retrieve(
    "Does JFrog Curation block package downloads like a package firewall?",
    axis="technical",
    competitors=["JFrog"],
    dimensions=["package_firewall"],
)
print(json.dumps(result, indent=2, default=str))
PY
```

Product portfolio:

```bash
.venv/bin/python - <<'PY'
import json
from ci_engine.retrieve import retrieve

result = retrieve(
    "List the product portfolio and main products for JFrog and Sonatype",
    axis="technical",
    competitors=["JFrog", "Sonatype"],
    dimensions=["product_portfolio"],
)
print(json.dumps(result, indent=2, default=str))
PY
```

Multi-company reachability:

```bash
.venv/bin/python - <<'PY'
import json
from ci_engine.retrieve import retrieve

result = retrieve(
    "Which companies support reachability analysis for vulnerable open source dependencies?",
    axis="technical",
    competitors=["Snyk", "Sonatype", "GitLab", "JFrog"],
    dimensions=["reachability_analysis"],
)
print(json.dumps(result, indent=2, default=str))
PY
```

## MCP Server

Run locally:

```bash
.venv/bin/python -m ci_engine.mcp.server
```

Environment variables:

- `PORT` - defaults to `8080`
- `MCP_SHARED_TOKEN` - optional shared auth token
- `MCP_ALLOWED_HOSTS` - comma-separated allowed hosts
- `MCP_ALLOWED_ORIGINS` - comma-separated allowed origins

Tools:

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
- `get_report_registry`
- `search_report_sections`
- `search_answer_context`

## Report Console And Chat

Run locally:

```bash
.venv/bin/python -m ci_engine.ui
```

Default URL:

```text
http://127.0.0.1:8090
```

Configured in `src/ci_engine/config.yaml`:

- `ui.host` - defaults to `127.0.0.1`
- `ui.port` - defaults to `8090`
- `chat.report_root` - defaults to `reports`
- `chat.tavily_max_results` - defaults to `3`

The console:

- lists competitors with generated dossiers from `reports/<competitor-slug>/`
- embeds `report.html`
- downloads `report.pdf` when present
- explains unavailable PDFs in plain executive language when validation failed
- provides grounded Q&A scoped to the selected report by default

Chat retrieval:

- uses `search_answer_context` for fast DB-plus-report context
- combines vector retrieval, exact keyword/product-name evidence, generated report sections, and coverage checks
- can search report sections, scores, missing-data notes, and validation findings
- runs bounded Tavily checks automatically when freshness, gaps, product validation, or contradictions matter
- returns `not enough evidence` when retrieved evidence cannot support the answer

Chat model defaults:

- planner: `models.chat_planner.name`, currently `claude-haiku-4-5`
- answer writer: `models.chat_answer.name`, currently `claude-sonnet-4-6`
- fallback: `models.chat_fallback.name`, currently `claude-sonnet-4-6`

## Competitive Reports

Generate a full `JFrog vs Sonatype` report:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitor Sonatype \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

`--competitor` generates one report for one competitor.

Generate reports for all configured companies except JFrog:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --all-companies \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

`--all-companies` reads the full `companies:` list in `src/ci_engine/config.yaml` and excludes `JFrog` by default because every report is already `JFrog vs <competitor>`.

Batch mode is sequential. It runs one complete `JFrog vs <competitor>` report at a time, writes that competitor's artifacts, records pass/fail status, and then moves to the next competitor. A failed competitor does not stop the rest of the batch.

Customer dossiers do **not** include "Part 1 · Market & strategic context" (PESTEL, Porter's Five Forces, positioning map). Those market-wide frameworks are dropped via `report.customer_excluded_sections` and published instead as a separate report. After all competitors finish, any batch run (`--all-companies`, `--deep-map-now`, or `--competitors`) also generates one standalone **Market & Strategic Context** report at `reports/market/report.{json,html,pdf}` — a single market-wide analyst pass over all tracked competitors carrying the general PESTEL, Five Forces, and an all-competitor positioning map. Toggle it with `report.market_report.enabled` in `config.yaml`.

Generate reports only for the current `deep_map_now` focus list except JFrog:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --deep-map-now \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

`--deep-map-now` reads the configured `deep_map_now:` subset. It does not run deep map and it is not automatically triggered when deep map finishes.

To run ingestion first and then generate reports for the same focus list:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map

.venv/bin/python -m ci_engine.crews.report.run \
  --deep-map-now \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

Generate a comma-separated custom batch:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitors "Sonatype,Snyk,GitLab" \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

Default output:

```text
reports/sonatype/report.json
reports/sonatype/report.html
reports/sonatype/report.pdf
```

The full mode uses:

- DB source inventory
- batch section retrieval through MCP
- batch capability retrieval through MCP
- Tavily web enrichment and targeted validation
- CrewAI/Sonnet analyst sections
- Report Checker validation
- HTML/PDF rendering

The terminal shows progress for each major stage. CrewAI analyst crews run with `verbose=True`, so CrewAI execution details should appear alongside report progress lines. CrewAI memory and tracing remain disabled for report generation.

Fast DB-only smoke test:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitor Sonatype \
  --draft-mode deterministic \
  --formats json,html \
  --no-web \
  --out-dir /private/tmp/ci-report-smoke
```

Useful modes:

- `deterministic` - no live analyst generation; useful for fast retrieval/render smoke tests.
- `crew_strategy` - Strategy Analyst only.
- `crew_strategy_market` - strategy plus market/company sections.
- `crew_strategy_market_technical` - adds technical sections.
- `crew_strategy_market_technical_field` - adds buyer/field sections.
- `crew_strategy_market_product_technical_field` - adds product/feature analysis.
- `crew_strategy_market_product_technical_field_scoring` - full current dossier with scoring.

Use `crew_strategy_market_product_technical_field_scoring` for real report generation. The smaller modes are mainly for incremental testing and debugging.

PDF rendering uses WeasyPrint. If PDF output is skipped because the dependency is missing:

```bash
uv sync
```

The renderer sets a writable cache directory under `/tmp` for WeasyPrint/Fontconfig.

Report validation must pass before PDF rendering. JSON and HTML can still be written for review, but PDF status becomes `blocked` when `validation.passed=false`.

If validation fails, inspect:

- `validation.findings` in `report.json`
- `evidence_pack.gaps`
- `evidence_pack.metadata.section_batch_coverage`
- `evidence_pack.capability_matrix.rows`

Quick validation summary:

```bash
jq '{
  passed: .validation.passed,
  errors: ([.validation.findings[]? | select(.severity=="error")] | length),
  warnings: ([.validation.findings[]? | select(.severity=="warning")] | length)
}' reports/github/report.json
```

List blocking errors:

```bash
jq -r '.validation.findings[]? |
  select(.severity=="error") |
  "- [\(.code)] section=\(.section_id // "n/a") :: \(.message)"' \
  reports/github/report.json
```

Common blocking causes:

- `missing_db_evidence` - a critical section has no DB-backed evidence for JFrog or the competitor.
- `evidence_readiness_weak` - evidence readiness is too weak in a critical section.
- `unsupported_claim` or `broken_citation` - the draft has an uncited or invalidly cited claim.
- `unsupported_market_share_claim` - market share appears without market-share evidence.
- `product_feature_generation_failed` or `missing_product_feature_matrix` - the Product/Feature analyst output failed the strict product-analysis contract.

Critical sections are `executive_summary`, `market_context`, `product_feature_analysis`, and `technical_teardown`. Missing DB evidence in these sections is an error; missing Tavily validation is a warning.

## Heal Dimensions

Dry run:

```bash
.venv/bin/python -m ci_engine.db.heal_dimensions --dry-run
```

Apply:

```bash
.venv/bin/python -m ci_engine.db.heal_dimensions --apply
```

Optional smaller output:

```bash
.venv/bin/python -m ci_engine.db.heal_dimensions --dry-run --max-items 20
```

What it does:

- canonicalizes known dimension aliases
- marks known-bad URLs stale
- marks deterministic wrong-company docs stale
- records changes in `source_healing_audit`

What it does not do:

- delete sources
- delete chunks
- infer absence
- run broad LLM cleanup

## Backfill Coverage Status

Dry run:

```bash
.venv/bin/python -m ci_engine.db.heal_coverage_status --dry-run
```

Apply:

```bash
.venv/bin/python -m ci_engine.db.heal_coverage_status --apply
```

What it does:

- reads active sources/chunks
- derives coverage assertions
- upserts assertions
- refreshes status rollups
- reports status counts, unknowns, and conflicts

## Close Unknown Scope

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

Target one company/dimension:

```bash
.venv/bin/python -m ci_engine.synthesize.close_coverage_scope \
  --apply \
  --competitor Snyk \
  --axis technical \
  --dimension package_firewall \
  --state unknown \
  --max-gaps 1 \
  --max-candidates-per-gap 2 \
  --review-absent
```

Useful filters:

- `--competitor`
- `--dimension`
- `--axis`
- `--state unknown|planned|partial`
- `--only-deep-map-now`
- `--max-gaps`
- `--max-candidates-per-gap`
- `--review-absent`

Safe operating rule:

- always run dry-run first
- use small apply batches
- inspect `review`
- validate retrieval after apply

## Deployment

The hosted footprint is two Cloud Run services running as `ci-engine-sa`: the report console (UI)
and the MCP server. Both are built from the Dockerfiles in `ops/` and reach Cloud SQL, Secret
Manager, and Vertex AI exactly as the local CLIs do.

Build and roll out a new UI revision:

```bash
REPO=europe-west1-docker.pkg.dev/jfrog-intel-rag/ci-engine

gcloud builds submit --tag "$REPO/ci-ui:latest" .

gcloud run deploy ci-ui \
  --image="$REPO/ci-ui:latest" \
  --region=europe-west1 \
  --service-account=ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com \
  --add-cloudsql-instances=jfrog-intel-rag:europe-west1:ci-db \
  --allow-unauthenticated
```

Redeploy = rebuild the affected image, then `gcloud run deploy` again (new revision, prior flags
preserved). `config.yaml` and the `reports/` artifacts are baked into the image, so config changes
and newly generated dossiers need a rebuild + redeploy of the UI image.

Full guide — APIs, Artifact Registry, IAM roles, the MCP service env vars, local container runs, and
rollback — is in [deployment.md](deployment.md).

## Tests

Run all tests:

```bash
.venv/bin/python -m pytest
```

Focused tests:

```bash
.venv/bin/python -m pytest tests/test_retrieval.py
.venv/bin/python -m pytest tests/test_coverage_verdict.py
.venv/bin/python -m pytest tests/test_pipeline.py
.venv/bin/python -m pytest tests/test_heal_dimensions.py tests/test_heal_coverage_status.py
.venv/bin/python -m pytest tests/test_report_crew.py tests/test_mcp_server.py
.venv/bin/python -m pytest tests/test_chat.py tests/test_ui.py
```

## Troubleshooting

### IAM auth fails

Run:

```bash
.venv/bin/python -m ci_engine.db.doctor
```

Check:

- local ADC exists
- the ADC principal can impersonate `ci-engine-sa@jfrog-intel-rag.iam.gserviceaccount.com`
- the service account has Cloud SQL permissions
- the Cloud SQL IAM DB user exists

### Schema table or index is missing

Apply schema again:

```bash
gcloud sql connect ci-db --user=postgres --database=ci --project=jfrog-intel-rag
```

Inside `psql`:

```sql
\i /Users/kevinifrah/jfrog-intel-rag/src/ci_engine/db/schema.sql
\q
```

### Retrieval returns `unknown_coverage`

Interpretation:

- no safe present/partial/planned/absent classification exists
- the DB may have no evidence
- or the DB may have reviewed evidence that was out of scope

Next step:

- run targeted scope closure
- inspect assertions and audit rows
- do not treat unknown as absent

### Retrieval returns `partial_coverage`

Interpretation:

- evidence exists, but only for limited or scoped coverage

Next step:

- answer with the limitation
- run scope closure if broader coverage may exist

### Scope closure finds noisy candidates

Expected behavior:

- verdicts should classify noisy candidates as `irrelevant` or `needs_review`
- accepted candidates should be small and evidence-backed

If a false positive is accepted:

- add or tighten a deterministic guard in `coverage_verdict.py`
- update `coverage-verdict/SKILL.md`
- add a regression test
- correct the DB with an audit trail

### Duplicate content

Duplicate content can return:

```json
{"skipped": "duplicate content"}
```

This means the source/content hash already exists. The pipeline may still refresh citations or assertions, but it should not re-embed duplicate chunks.

### Embedding errors

Check:

- ADC has Vertex AI access
- `project.gcp_project_id` is correct
- region is supported
- embedding dimensionality matches schema and config

## Safe Rollout Checklist

Before mutation:

- run dry-run
- check candidate topics
- inspect expected affected gaps

During mutation:

- use small batches
- keep `--review-absent`
- watch errors and review queue

After mutation:

- run retrieval spot checks
- check coverage status counts
- run focused tests
- run full tests when code changed
