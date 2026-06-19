# Chat And Report Console

The report console is a local FastAPI UI for viewing generated competitive reports and asking grounded Q&A against the evidence base.

V1 keeps report artifacts on the filesystem:

```text
reports/<competitor-slug>/
  report.json
  report.html
  report.pdf
```

The UI reads those artifacts through `ReportArtifactStore`. HTML and PDF are not stored as Postgres blobs. The database remains focused on evidence, chunks, sources, coverage, embeddings, and retrieval.

## Run

```bash
.venv/bin/python -m ci_engine.ui
```

Default URL:

```text
http://127.0.0.1:8090
```

Host and port live in `src/ci_engine/config.yaml`:

- `ui.host`
- `ui.port`

## UI Layout

The console has three working areas:

- left: competitor selector
- center: selected HTML report viewer
- right: executive Q&A scoped to the selected competitor/report by default

PDF behavior:

- available PDF: the download button links to `reports/<slug>/report.pdf`
- unavailable PDF: the UI shows a plain-language explanation of what evidence still needs work
- missing PDF: the download button is disabled

## API Routes

- `GET /` - report console
- `GET /api/reports` - report registry
- `GET /reports/{slug}/html` - embedded generated HTML report
- `GET /reports/{slug}/pdf` - PDF download, or blocked/missing status
- `POST /api/chat` - non-streamed grounded chat
- `WS /ws/chat` - streamed chat events

## Chat Flow

1. The UI sends `ChatRequest` with question, selected competitor, selected report slug, and max evidence count.
2. `chat-query-planner` converts the question into a strict retrieval plan.
3. A deterministic strengthening pass expands comparison, weakness, product, and security questions into balanced retrieval across both companies.
4. The MCP executor runs approved read-only tools.
5. Tavily web validation runs automatically when evidence is missing, stale, contradictory, product-specific, or high impact.
6. `chat-answer-writer` writes a concise narrative answer from retrieved evidence.
7. `chat-grounding-checker` validates citations and fail-closed behavior.

If evidence is missing or weak, chat returns `not enough evidence` rather than inventing.

## Chat Skills

Chat instructions live in `src/ci_engine/skills/`:

- `chat-query-planner`
- `chat-mcp-tool-use`
- `chat-answer-writer`
- `chat-web-depth-selector`
- `chat-grounding-checker`

The full skills guide planner and answer behavior. Compact tool cards in `src/ci_engine/chat/tool_cards.py` teach tool use without pasting long skill text into every MCP description.

## Chat-Facing MCP Tools

New read-only MCP tools:

- `get_report_registry` - list generated reports, validation status, generated time, and PDF availability
- `search_report_sections` - search report sections, scores, missing-data notes, and validation findings
- `search_answer_context` - one fast chat retrieval call over vector evidence, keyword evidence, and optional report artifacts

Existing tools remain available for targeted follow-up:

- `search`
- `compare_dimension`
- `coverage_matrix`
- `source_inventory`
- `get_source_detail`

## Web Policy

Chat web checks use Tavily without writing snapshots or mutating the DB. The user is not asked to choose whether web enrichment runs; the planner decides.

Depth policy:

- `ultra-fast` for simple freshness checks, current/recent questions, and public confirmation
- `fast` for product/capability gaps, technical validation, or contradictory evidence
- no `advanced` in chat v1

If `ultra-fast` returns weak evidence or fails and web evidence is required, chat retries once with `fast`.

## Answer And Source Policy

Answers are written for CEO/CTO readers:

- direct answer first
- short explanatory narrative
- implication for JFrog when relevant
- bullets only for natural lists or explicit comparisons

Source links are shown only when useful, such as web-validated answers, low-confidence answers, contradictions, or explicit source requests. Source labels are human-readable, not raw IDs or backend section names.

## Model Defaults

Configured in `src/ci_engine/config.yaml`:

- `models.chat_planner.name`: `claude-haiku-4-5`
- `models.chat_answer.name`: `claude-haiku-4-5`
- `models.chat_fallback.name`: `claude-sonnet-4-6`

Haiku is the default for speed and cost. Sonnet fallback is used when the plan marks a question as complex, or when grounding fails and a stronger reasoning pass is justified.

## Storage Decision

V1 uses filesystem reports through `ReportArtifactStore`.

Reasons:

- generated reports already exist under `reports/`
- HTML/PDF are artifacts, not primary evidence
- DB remains optimized for evidence and retrieval
- the store abstraction allows a future move to GCS/S3 plus DB metadata

## Tests

Focused tests:

```bash
.venv/bin/python -m pytest tests/test_chat.py tests/test_ui.py tests/test_mcp_server.py
```

Full baseline:

```bash
.venv/bin/python -m pytest
```
