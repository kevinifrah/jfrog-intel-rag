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
- `grounding-contract`
- `report-db-retrieval`
- `report-evidence-quality`
- `report-extensive-web-search`
- `report-targeted-validation`
- `report-evidence-pack-builder`
- `report-market-cross-report` — composed first by the Market Analyst; enforces canonical positioning-map axes and Five Forces baselines across all dossiers
- `report-framework-pestel` — PESTEL factor instructions
- `report-framework-five-forces` — Porter's Five Forces instructions
- `report-framework-positioning-map` — positioning map layout instructions
- `report-framework-swot` — SWOT instructions
- `report-confidence-tiering` — claim confidence-tier definitions
- `report-strategy-analyst`
- `report-market-analyst`
- `report-market-overview` — market-wide analyst for the standalone Market & Strategic Context report
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

The Market Analyst still runs for each customer dossier so the Report Checker can validate the
`market_context` section, but that section is excluded from the rendered customer report (see
[Market Frameworks And The Customer/Market Split](#market-frameworks-and-the-customermarket-split)).
On batch runs the standalone market report adds one more pass — the **Market Overview Analyst**
(`report-market-overview`) — over evidence from all tracked competitors.

The report model is configured in `src/ci_engine/config.yaml`:

- `models.report.name`: `claude-sonnet-4-6`

CrewAI runs inside a Python-controlled report workflow. Each analyst is executed as a dedicated CrewAI single-agent crew, in sequence, against the frozen `EvidencePack`. This is intentional: it keeps the evidence boundary explicit and lets the Report Checker gate every section before PDF rendering.

Report agents currently use:

- `verbose=True` - CrewAI execution details are printed in the terminal.
- `memory=False` - CrewAI memory is disabled so prior runs cannot leak into the current report.
- `tracing=False` - CrewAI tracing integrations are disabled.
- no `output_log_file` - CrewAI does not write a dedicated execution log file unless this is added later.

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

## Validation And PDF Gate

`check_report(evidence_pack, draft)` is the quality gate between generated analysis and the executive PDF.

Validation runs after the frozen `EvidencePack` is built and after the analyst draft is assembled.

The validator checks:

- EvidencePack identity: the draft must reference the same frozen pack that was used to generate it.
- Citation integrity: section, claim, and score evidence IDs must exist in the frozen pack.
- Claim support: every normal claim needs cited evidence; missing-data claims must explicitly say `no recent data found`.
- Critical-section coverage: `executive_summary`, `market_context`, `product_feature_analysis`, and `technical_teardown` must have enough evidence for both JFrog and the selected competitor.
- DB grounding: missing DB-backed evidence in a critical section is an error; missing DB-backed evidence in a non-critical section is a warning.
- Web validation: missing Tavily validation is a warning when web search is enabled.
- Contradictions: Tavily evidence classified as `contradicts_db` is an error until resolved.
- Readiness: weak readiness in a critical section is an error.
- Mode-specific contracts: Strategy, Market, Product/Feature, Technical, Buyer/Field, and Scoring sections must satisfy their schema and neutrality requirements.
- Presentation hygiene: raw internal IDs, source paths, keyword artifacts, and source-list prose are blocked from executive-facing narrative.

Evidence thresholds:

- Critical sections require at least `2` evidence items per company.
- Non-critical sections require at least `1` evidence item per company.

Validation result:

- `validation.passed=true` means no error-level findings were produced. Warnings can still exist.
- `validation.passed=false` means at least one error exists.

Rendering behavior:

- JSON is written for audit and debugging.
- HTML can still be written for review.
- PDF is blocked when `validation.passed=false`.

This is why a report can have `report.json` and `report.html` but a PDF status of `blocked`.

Common blocker codes:

- `missing_db_evidence` - a section lacks DB-backed evidence for the competitor or JFrog.
- `evidence_readiness_weak` - a critical section is too weak to certify.
- `broken_citation` or `broken_section_citation` - the draft cites evidence IDs not present in the pack.
- `unsupported_claim` - a claim has no evidence citation.
- `unresolved_web_contradiction` - Tavily evidence contradicts DB evidence and needs resolution.
- `product_feature_generation_failed` - the Product/Feature analyst returned output that failed strict JSON or CI-synthesis checks.
- `missing_product_feature_matrix` - Product/Feature mode lacks the required cited capability matrix.
- `unsupported_market_share_claim` - a claim asserts a specific market-share figure without cited evidence. Claims that explicitly acknowledge the absence of verified data (e.g. "no independently verified data available", "carries material uncertainty") are accepted and do not trigger this error.

## Market Frameworks And The Customer/Market Split

There are four analyst visual frameworks: PESTEL, Porter's Five Forces, the strategic-group
positioning map ("mapping C"), and an executive SWOT. As of the current configuration, the three
*market-wide* frameworks (PESTEL, Five Forces, positioning map) **do not appear in customer
dossiers** — they belong to a separate market report. Only SWOT (in the Executive Summary) stays in
the customer dossier.

This is enforced two ways in `src/ci_engine/config.yaml`:

```yaml
report:
  frameworks:              # per-competitor customer dossiers
    pestel: false
    positioning_map: false
    five_forces: false
    swot: true
  customer_excluded_sections:
    - market_context       # "Part 1 · Market & strategic context" dropped from customer reports
  market_report:           # the standalone Market & Strategic Context report
    enabled: true
    slug: market
    title: "Market & Strategic Context"
    max_companies: 12
    frameworks:
      pestel: true
      five_forces: true
      positioning_map: true
```

- `report.frameworks` toggles whether each framework is **generated and rendered** for a per-competitor
  dossier. A disabled framework is not generated (the analyst prompt drops the framework skill and
  leaves the field empty) and not rendered (the renderer skips it even if an older `report.json` still
  carries the metadata).
- `report.customer_excluded_sections` removes whole sections from the customer-facing HTML/PDF and the
  table of contents. `market_context` is excluded, so even the market narrative (not just the visuals)
  is absent from customer dossiers. The section is still generated so the Report Checker can validate
  it; it is dropped at render time.
- `report.market_report` configures the standalone market report (below), whose frameworks are forced
  on regardless of the per-competitor toggles.

### Standalone Market & Strategic Context Report

On any batch run (`--all-companies`, `--deep-map-now`, or `--competitors`), after the per-competitor
reports finish, `crews/report/run.py` calls `crews/report/market_report.py` to produce one
market-wide report at `reports/market/report.{json,html,pdf}`.

- It collects evidence across all tracked competitors (capped by `market_report.max_companies`),
  runs a single market-wide analyst pass driven by `skills/report-market-overview/SKILL.md`, and
  builds a `MarketOverviewAnalysis` (market thesis, dynamics, risks, plus PESTEL, Five Forces, and a
  positioning map plotting the whole field — no single competitor focus).
- It is rendered with the same dossier template but a market header ("Market & Strategic Context",
  not "JFrog vs …") and no Executive Summary, and with frameworks forced on.
- It is disabled by setting `report.market_report.enabled: false`.

## Cross-Report Consistency

The market-level content that does not change between competitors is standardized so the market
report (and any dossier that re-enables a framework) stays directly comparable across runs.

### Canonical Positioning Map Axes

All reports use the same X and Y axis labels:

- `x_axis_label`: "Supply-chain coverage breadth"
  - low: "Single ecosystem / one workflow"
  - high: "Universal repository + full SDLC"
- `y_axis_label`: "Security specialization depth"
  - low: "Platform with security add-ons"
  - high: "Purpose-built security toolchain"

These are defined in `report-market-cross-report/SKILL.md` and enforced by `market.py`. Do not invent different axes in a skill or prompt.

### Canonical PESTEL Factor Text

The `factor` text for each PESTEL dimension describes the market-level driver and is identical across all reports. Only the `implication` text (the competitor-specific impact) is written per-report.

Canonical factor descriptions:

- **Political**: US Executive Order 14028, EU Cyber Resilience Act, and government SBOM mandates create regulatory tailwinds for software supply chain security investment.
- **Economic**: Platform consolidation pressure drives enterprises to reduce vendor count; buyers evaluate security tooling as part of broader DevSecOps platform decisions.
- **Social**: Developer-centric security culture (shift-left) is mainstream; OSS adoption normalises dependency risk and drives demand for integrated governance tooling.
- **Technological**: AI/ML model deployment and agentic pipelines expand the software artifact supply chain, creating new governance and security requirements beyond traditional SCA.
- **Environmental**: Environmental factors are not a material driver in the software supply chain security market.
- **Legal**: EU Cyber Resilience Act and US EO 14028 impose software transparency and SBOM obligations on vendors and buyers, accelerating compliance-led purchasing.

### Five Forces Baselines

Market-level intensity baselines are fixed across all dossiers:

- competitive rivalry: high
- threat of new entrants: moderate
- threat of substitutes: moderate
- buyer power: high
- supplier power: low

These baselines come from `report-market-cross-report/SKILL.md`. Per-competitor driver descriptions and implications remain analyst-written.

## Output Files

Default output location:

```text
reports/<competitor-slug>/
```

Files:

- `report.json` - EvidencePack, draft, scores inside the draft, validation report, and metadata.
- `report.html` - polished web dossier.
- `report.pdf` - PDF version generated from validated HTML.

On batch runs the standalone market report is written to `reports/market/` (slug from
`report.market_report.slug`) with the same three file names.

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

`--competitor` generates one report for one competitor.

All configured competitors except JFrog:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --all-companies \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

`--all-companies` reads `companies:` from `src/ci_engine/config.yaml` and excludes `JFrog` by default.

Batch mode is sequential. It completes the full flow for one competitor before moving to the next competitor. A failure for one competitor is captured in the final summary and does not prevent later competitors from running. After the competitors finish, the batch also generates the standalone Market & Strategic Context report at `reports/market/` (unless `report.market_report.enabled` is `false`).

Current `deep_map_now` focus list except JFrog:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --deep-map-now \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

`--deep-map-now` reads `deep_map_now:` from `config.yaml`. This is only a report selector. It does not run the deep-map ingestion process, and deep map does not automatically trigger report generation.

To deep-map first and then report on the same focus list:

```bash
.venv/bin/python -m ci_engine.synthesize.deep_map

.venv/bin/python -m ci_engine.crews.report.run \
  --deep-map-now \
  --draft-mode crew_strategy_market_product_technical_field_scoring \
  --formats json,html,pdf
```

Custom comma-separated batch:

```bash
.venv/bin/python -m ci_engine.crews.report.run \
  --competitors "Sonatype,Snyk,GitLab" \
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

## Draft Modes

`--draft-mode` controls how much of the report is generated by the CrewAI/LLM analyst pipeline.

Use this for real reports:

```bash
--draft-mode crew_strategy_market_product_technical_field_scoring
```

Available modes:

- `deterministic` - fast smoke mode with no live analyst generation.
- `crew_strategy` - Strategy Analyst only.
- `crew_strategy_market` - Strategy plus company and market sections.
- `crew_strategy_market_technical` - adds technical sections.
- `crew_strategy_market_technical_field` - adds buyer and field sections.
- `crew_strategy_market_product_technical_field` - adds product/feature analysis.
- `crew_strategy_market_product_technical_field_scoring` - full current dossier with scoring.

The smaller modes exist for incremental testing, validation, and debugging. Production-quality dossiers should use the full scoring mode.

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
