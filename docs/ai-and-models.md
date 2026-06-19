# AI And Models

CI Engine uses AI to acquire, classify, and synthesize evidence before it is stored. It does not use AI memory as the source of truth during normal retrieval.

Model names and parameters live in `src/ci_engine/config.yaml`.

## Boundary: AI Is Not The Runtime Source Of Truth

At answer/retrieval time:

- retrieval embeds the query
- retrieval searches active chunks in Cloud SQL
- retrieval returns stored evidence and missing coverage
- retrieval does not browse the web
- retrieval does not ask an LLM to invent facts

AI is used earlier in the pipeline:

- generating research reports
- splitting reports into source candidates
- scoring relevance
- synthesizing raw source text into compiled evidence
- classifying targeted coverage gaps
- embedding documents and queries
- generating validated competitive report sections from a frozen EvidencePack

The database remains the primary source of truth. Report generation may attach run-scoped Tavily evidence for enrichment and validation, but that evidence is frozen into the EvidencePack and cited like any other report evidence.

## AI-Managed Components

### Web Research Report Generation

Config:

- `models.web_research.name`: `claude-sonnet-4-6`
- temperature: `0.0`
- max tokens: `12000`
- timeout: `180`
- web search max uses: `12`

Code:

- `src/ci_engine/acquire/web_lane.py`
- skill: `src/ci_engine/skills/deep-company-research/SKILL.md`

Purpose:

- generate a broad company research report
- use Anthropic web search during acquisition
- save the report as a snapshot
- provide raw material for report splitting

### Report Splitting

Config:

- `models.report_splitter.name`: `claude-haiku-4-5`
- temperature: `0.0`
- max tokens: `12000`
- timeout: `180`

Code:

- `src/ci_engine/acquire/web_lane.py`
- skill: `src/ci_engine/skills/deep-report-splitter/SKILL.md`

Purpose:

- split a broad research report into ontology-scoped slices
- attach dimension, axis, doc type, title, summary, and citations
- create candidates for ingestion

### Relevance Scoring

Config:

- `models.relevance.name`: `claude-haiku-4-5`
- temperature: `0.0`
- thinking: `none`
- threshold: `ingestion.relevance_threshold`, currently `0.6`

Code:

- `src/ci_engine/acquire/relevance.py`
- skill: `src/ci_engine/skills/relevance-rubric/SKILL.md`

Purpose:

- decide whether a candidate is relevant enough to ingest
- classify candidate axis, dimension, doc type, and reason
- reduce noisy or off-topic ingestion

### Ingestion Synthesis

Config:

- `models.synthesis.name`: `claude-opus-4-8`
- temperature: `0.2`
- thinking: `high`
- max tokens: `12000`
- timeout: `120`

Code:

- `src/ci_engine/synthesize/compiler.py`
- skill: `src/ci_engine/skills/ingest-synthesis/SKILL.md`

Purpose:

- transform raw source text into compiled evidence
- extract facts
- create coverage assertions
- create entities and relationships
- identify conflicts

The compiled evidence is chunked and embedded for retrieval.

### Coverage Verdict Fallback

Config:

- uses `models.relevance.name`, currently `claude-haiku-4-5`
- timeout uses `ingestion.llm_timeout_s`, currently `30`

Code:

- `src/ci_engine/synthesize/coverage_verdict.py`
- skill: `src/ci_engine/skills/coverage-verdict/SKILL.md`

Purpose:

- classify a candidate against an exact competitor + axis + dimension gap
- return one verdict state:
  - `present`
  - `partial`
  - `planned`
  - `explicit_absent`
  - `irrelevant`
  - `still_unknown`
  - `needs_review`

Deterministic guards run before the LLM fallback.

### Embeddings

Config:

- `embedding.model`: `gemini-embedding-001`
- `embedding.dimensions`: `1536`
- `embedding.doc_task_type`: `RETRIEVAL_DOCUMENT`
- `embedding.query_task_type`: `RETRIEVAL_QUERY`

Code:

- `src/ci_engine/embed/gemini.py`

Purpose:

- embed compiled evidence chunks for pgvector search
- embed user queries for retrieval

Embeddings are created through Vertex AI, using ADC rather than a committed API key.

### Competitive Report Agents

Config:

- `models.report.name`: `claude-sonnet-4-6`
- temperature: `0.3`
- thinking: `high`
- max tokens: report modules default to `6000` unless overridden
- timeout: report modules default to `180` seconds unless overridden

Code:

- `src/ci_engine/crews/report/`
- skills in `src/ci_engine/skills/report-*/SKILL.md`
- shared neutrality skill: `src/ci_engine/skills/neutral-ci-contract/SKILL.md`
- shared grounding skill: `src/ci_engine/skills/grounding-contract/SKILL.md`

Purpose:

- synthesize executive competitive-intelligence analysis from a frozen EvidencePack
- produce market, product/feature, technical, buyer/field, and scoring sections
- keep all claims cited to EvidencePack IDs
- surface JFrog strengths, JFrog weaknesses, competitor strengths, uncertainty, and action implications
- fail closed when claims are unsupported or output is invalid

The report generator can use Tavily during EvidencePack construction for public web enrichment and targeted validation. Tavily findings are captured as report-run evidence and do not become permanent DB evidence unless explicitly ingested later.

CrewAI runtime settings for report agents:

- `verbose=True` so CrewAI agent/task execution is visible in the terminal.
- `memory=False` so generated analysis cannot depend on prior CrewAI memory.
- `tracing=False` so CrewAI observability traces are not emitted by default.
- no `output_log_file` by default; terminal progress and `report.json` are the audit path.

## Deterministic Components

The following are controlled by code and config, not by model judgment.

### Config Loading

`config.py` loads `config.yaml`. Tunables should live in config, not in scattered code.

### Ontology Normalization

`ontology.py` maps aliases and observed labels to canonical dimensions.

Examples:

- `vulnerability_remediation` to `autofix_remediation`
- `secret_detection` to `secrets_detection`
- `container_scanning` to `container_image_scanning`
- `sbom_support` to `sbom_generation`
- `pricing` to `pricing_packaging`
- `partnerships` to `partnerships_ecosystem`

Candidate dimensions from deep map and scope closure remain authoritative.

### Coverage State Rules

`dimension_coverage.py` controls:

- valid states
- deterministic absent/planned/partial phrase detection
- missing reasons
- assertion extraction fallback
- rollup precedence
- conflict marking

It never infers `absent` from silence.

### DB Schema And Audits

`db/schema.sql` and `db/repository.py` control:

- source storage
- chunk storage
- vector indexes
- citations
- graph relationships
- healing audits
- coverage assertions
- coverage rollups
- coverage audits

### Stale Marking

`heal_dimensions.py` marks stale only for:

- known-bad URLs
- configured generic non-evidence URL patterns
- deterministic wrong-company docs

It does not use broad LLM cleanup.

### Conservative Verdict Guards

`coverage_verdict.py` includes deterministic guards for known false positives.

Examples:

- package advisory for npm package `firewall` is not package-firewall coverage
- partner ecosystem pages do not prove technical supported ecosystems
- vendor's own tool rollout does not prove customer software distribution
- AI ROI/productivity analytics do not prove security impact analysis
- internal model validation does not prove AI model scanning
- third-party edge CI/CD examples do not prove target-company edge delivery coverage

### Retrieval Filters

`retrieve/__init__.py` and repository vector search deterministically control:

- active-only source and chunk filtering
- dimension alias expansion
- assertion-backed dimension matching
- multi-company balancing
- `top_k` enforcement
- missing reason generation

## Source Trust

`coverage_verdict.source_trust` classifies source trust as:

- `official` when the URL host matches the target competitor or source kind is trusted official research
- `third_party` otherwise

Trust affects absence handling. Explicit absence from weak or third-party evidence should go to review rather than being applied blindly.

## Prompt Assets

Prompts are loaded through `ci_engine.skills`.

Current skills:

- `coverage-verdict`
- `deep-company-research`
- `deep-report-splitter`
- `grounding-contract`
- `ingest-synthesis`
- `neutral-ci-contract`
- `relevance-rubric`
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

To change model behavior, edit these skill files and update tests. Do not hide prompt behavior in Python strings.

## Model Output Parsing

`llm_json.py` extracts JSON objects from model responses. Callers validate required fields and normalize values before writing to the DB.

This keeps the system tolerant of fenced JSON or surrounding text while still enforcing strict structured data.

## Failure Philosophy

If AI output is weak, invalid, low-confidence, or out of scope:

- skip it
- classify it as `needs_review`
- keep coverage `unknown`
- preserve audit details

Do not guess. Do not delete evidence. Do not mark absence from missing data.
