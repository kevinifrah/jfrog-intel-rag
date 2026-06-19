# Report Generator

The report generator builds validated competitive dossiers for `JFrog vs <competitor>`.

Current primary target:

- `JFrog vs Sonatype`

Current output formats:

- `report.json`
- `report.html`
- `report.pdf`

The report is designed to be a strategic and technical competitive-intelligence dossier, not a source dump. Analyst agents synthesize from a frozen evidence artifact, the checker validates claims before rendering, and the renderer produces executive-readable HTML/PDF with clean citations.

## Core Flow

```text
competitor
  |
  v
DB source inventory
  |
  v
Batch DB section retrieval through MCP
  |
  v
Batch capability evidence matrix through MCP
  |
  v
Tavily broad enrichment and targeted validation
  |
  v
Frozen EvidencePack
  |
  v
CrewAI/Sonnet analyst sections
  |
  v
Report Checker
  |
  v
HTML + JSON + PDF rendering
```

## EvidencePack

`EvidencePack` is the frozen artifact for one report run.

It includes:

- DB evidence items.
- Tavily web evidence items.
- source metadata and citations.
- source inventory summaries.
- evidence readiness scores.
- product catalog.
- capability evidence matrix.
- evidence gaps and validation notes.

Every evidence item includes:

- source type: `db` or `tavily`
- company
- report section
- URL, title, publisher
- retrieved/published dates where available
- quote or summary
- confidence
- source/chunk IDs for audit
- metadata such as source kind, dimension, capability ID, and retrieval mode

Analyst agents must write only from the frozen `EvidencePack`. This makes report generation reproducible and auditable.

## Retrieval Design

The report generator uses DB evidence first.

### Section Evidence

`build_report_section_evidence` retrieves broad section evidence in one MCP batch call.

It covers:

- executive summary
- company snapshot
- market context
- product and feature analysis
- technical teardown
- supply-chain security
- buyer fit
- scoring
- field battlecard

The tool reads active DB chunks for JFrog and the competitor, filters them by section dimensions and axis, ranks by section term match, source quality, freshness, and chunk ID, then emits section-scoped `EvidenceItem` objects.

The EvidencePack records:

- `section_retrieval_mode`
- `section_batch_coverage`

### Capability Evidence

`build_capability_evidence_matrix` retrieves product/capability evidence in one MCP batch call.

Canonical capability rows include:

- artifact repository and package formats
- software composition analysis
- SBOM generation and export
- open source package curation
- repository firewall and package admission
- malicious package detection
- policy, license, and governance controls
- reachability analysis
- CVE contextual prioritization
- CI/CD and IDE integrations
- architecture and deployment model
- AI, MLOps, and model/package governance

The capability matrix compares JFrog and the competitor with:

- product names
- capability statements
- status
- confidence
- evidence IDs
- search attempts
- readout

Important rule: missing evidence for one side does not automatically become an advantage for the other side. It remains an evidence gap or an unclear/needs-review row until enough evidence exists.

### Tavily Web Validation

Tavily is used after DB retrieval for:

- broad public enrichment
- targeted checks for weak, stale, missing, surprising, or contradictory claims
- capability-level validation

Tavily findings remain inside the run-specific `EvidencePack`. They do not need to be inserted into the permanent DB during early report testing.

Web findings are classified as:

- `confirms_db`
- `updates_db`
- `contradicts_db`
- `fills_gap`
- `adds_context`
- `insufficient`
- `irrelevant`

Contradictions must be resolved before rendering.

## CrewAI Agents

Report code lives in `src/ci_engine/crews/report/`.

Every agent loads its instructions from `src/ci_engine/skills/*/SKILL.md`.

Current report skills:

- `neutral-ci-contract`
- `report-db-retrieval`
- `report-evidence-quality`
- `report-extensive-web-search`
- `report-targeted-validation`
- `report-evidence-pack-builder`
- `report-strategy-analyst`
- `report-market-analyst`
- `report-product-feature-analyst`
- `report-technical-analyst`
- `report-buyer-field-analyst`
- `report-scoring-agent`
- `report-checker`
- `report-editor-auditor`

The production report mode runs these analyst sections:

- Strategy Analyst
- Market Analyst
- Product/Feature Analyst
- Technical Analyst
- Buyer/Field Analyst
- Scoring Agent
- Report Checker

The report model is configured in `src/ci_engine/config.yaml`:

- `models.report.name`: `claude-sonnet-4-6`

## Neutrality Rules

The report must be useful to JFrog by being neutral and precise.

Each major section should surface:

- where JFrog is strong
- where JFrog is weak or pressured
- competitor strengths
- uncertainty and evidence gaps
- action implications

The checker blocks or flags:

- unsupported claims
- broken citations
- unresolved contradictions
- uncited scores
- one-sided superiority claims
- raw implementation artifacts in executive-facing prose
- source-list prose such as "current section uses"
- raw evidence IDs in the rendered narrative

## Output Files

Default output location:

```text
reports/<competitor-slug>/
```

Files:

- `report.json` - EvidencePack, draft, validation report, scores, and metadata.
- `report.html` - polished web dossier.
- `report.pdf` - PDF version generated from validated HTML.

PDF rendering uses WeasyPrint. The project dependency is declared as:

```text
weasyprint>=69.0
```

If PDF rendering fails because WeasyPrint is missing, run:

```bash
uv sync
```

## Commands

Full Sonatype report:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitor Sonatype \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

DB-only smoke test:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitor Sonatype \
  --draft-mode deterministic \
  --formats json,html \
  --no-web \
  --out-dir /private/tmp/ci-report-smoke
```

Render PDF from an existing validated `report.json`:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

from ci_engine.crews.report.renderer import write_report_artifacts
from ci_engine.crews.report.schemas import EvidencePack, ReportDraft, ValidationReport

data = json.loads(Path("reports/sonatype/report.json").read_text())
pack = EvidencePack.model_validate(data["evidence_pack"])
draft = ReportDraft.model_validate(data["draft"])
validation = ValidationReport.model_validate(data["validation"])

write_report_artifacts(
    pack,
    draft,
    validation,
    out_dir=Path("reports/sonatype"),
    formats=("pdf",),
)
PY
```

## Latest Sonatype Artifact Status

At the time of this documentation update, the generated `JFrog vs Sonatype` dossier uses:

- section retrieval: `batch`
- capability retrieval: `batch`
- DB evidence: `184`
- Tavily evidence: `179`
- total evidence: `363`
- validation: passed
- scores: `8`

Remaining evidence-gap warnings are around:

- JFrog package firewall/admission evidence
- JFrog reachability analysis evidence

These warnings are intentional. They keep the report neutral by preventing missing evidence from becoming a fake win/loss conclusion.

## Tests

Run all tests:

```bash
.venv/bin/python -m pytest
```

Focused report tests:

```bash
.venv/bin/python -m pytest tests/test_report_crew.py tests/test_mcp_server.py
```

Key tested behaviors:

- skill loading
- EvidencePack creation
- batch section retrieval
- batch capability retrieval
- Tavily evidence classification
- citation validation
- neutral capability readouts
- checker failures
- HTML/PDF rendering
