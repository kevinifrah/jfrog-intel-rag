# Business Context

CI Engine exists to support evidence-backed competitive intelligence for JFrog.

The competitive domain is software supply chain security and adjacent platform markets: DevSecOps, artifact management, software composition analysis, package security, SBOM management, MLOps, AI supply chain, CI/CD integrations, runtime visibility, governance, and enterprise go-to-market motion.

## Why The System Exists

Competitive intelligence often mixes three different things:

- real product capabilities
- vendor or analyst positioning
- missing or ambiguous evidence

CI Engine separates these by storing cited sources, classifying them against a controlled ontology, and rolling evidence into explicit coverage states.

The business goal is not simply to collect links. The goal is to help JFrog teams understand where competitors have proven coverage, where coverage is limited or planned, where evidence is missing, and where explicit non-coverage is documented.

## Primary Business Questions

The system is designed to answer questions such as:

- What products does each competitor offer?
- Which competitors cover a capability such as package firewall, SBOM generation, reachability analysis, runtime security, or ML model registry?
- Which capabilities are present, partial, planned, absent, or unknown?
- Where does JFrog lead, match, lag, or need more evidence?
- What market positioning is each company using?
- Which customer segments or ICPs are targeted?
- What pricing and packaging signals exist?
- Which partnerships and ecosystems matter?
- What funding, ownership, M&A, and leadership signals exist?
- Which analyst reports or customer proof points support positioning?
- Which win/loss signals exist, and which are only generic market commentary?

## Competitor Universe

The full tracked competitor list is configured in `src/ci_engine/config.yaml`:

- JFrog
- Sonatype
- Snyk
- Black Duck
- Endor Labs
- Checkmarx
- Mend
- GitLab
- GitHub
- Aqua Security

The current deep-map focus is `deep_map_now`:

- JFrog
- Snyk
- Sonatype
- GitLab

These are the companies that the deep-map workflow prioritizes for full ontology coverage.

## Technical And Business Coverage

The ontology has two axes.

### Technical Axis

Technical dimensions describe product and capability coverage:

- product portfolio
- SCA and vulnerability management
- reachability and impact analysis
- secrets and code security
- supply chain security
- package firewall and malicious package detection
- SBOM generation
- container, IaC, and runtime security
- license and policy governance
- artifact management and distribution
- release lifecycle management
- MLOps model registry
- CI/CD and IDE integrations
- supported ecosystems
- AI features

Technical answers should be interpreted as product/capability evidence, not market positioning.

### Business Axis

Business dimensions describe company and market signals:

- company profile
- market positioning
- target segments / ICP
- pricing and packaging
- go-to-market motion
- partnerships and ecosystem
- funding and ownership
- customers and case studies
- analyst positioning
- mergers and acquisitions
- leadership and strategy signals
- win/loss signals

Business answers should be interpreted as market evidence. They may come from official company pages, press releases, analyst sources, credible third parties, customer stories, or financial/ownership documents.

## Coverage State Interpretation

Coverage states are deliberately conservative.

- `present` means current evidence supports that the company covers the dimension.
- `partial` means evidence supports limited, scoped, indirect, or incomplete coverage.
- `planned` means roadmap, beta, proposal, preview, issue, or coming-soon evidence.
- `absent` means reliable evidence explicitly says the company does not support or offer the capability.
- `unknown` means the system cannot safely classify the dimension as present, partial, planned, or absent.

Unknown does not mean absent.

## Unknown Data Versus Unknown Scope

There are two practical forms of `unknown`.

### Unknown Data

The system has not found reliable evidence for the dimension.

Typical signal:

- coverage state is `unknown`
- `active_assertions` is 0

Business interpretation:

- the question still needs targeted research
- do not use it as proof of non-coverage

### Unknown Scope

The system found evidence, but it does not answer the exact dimension.

Examples:

- a page mentions "firewall" but is about an npm package named `firewall`, not package-firewall product coverage
- a page mentions "AI impact" but discusses productivity ROI, not vulnerability impact analysis
- a delivery-team page describes shipping the vendor's own platform, not customer artifact distribution

Typical signal:

- coverage state is `unknown`
- `active_assertions` is greater than 0
- assertion/audit details explain why the evidence did not close the gap

Business interpretation:

- the topic has been reviewed
- evidence was insufficient or out of scope
- more targeted research may still be useful

## Absent Is High-Risk

`absent` is only valid when there is explicit negative evidence.

Acceptable absent evidence may include:

- official docs saying a feature is unsupported
- official pricing/product docs clearly excluding a capability
- reliable cited research that explicitly states non-support

The system never infers absence from:

- no search results
- missing product pages
- weak third-party commentary
- model intuition
- lack of evidence in the DB

## Source Trust

The system stores and retrieves multiple source types.

Official sources are strongest:

- vendor docs
- product pages
- release notes
- pricing pages
- official blogs
- official press releases

Third-party sources can be useful but need more caution:

- analyst pages
- credible news
- customer case studies
- partner pages
- public filings
- market summaries

Vendor marketing is allowed, but claims are treated carefully. A marketing page can prove that a product exists or that a company claims a capability; it should not be over-read as independent validation.

## Practical Examples

### "Does Snyk have package firewall?"

If retrieval returns `unknown_coverage`, the correct answer is:

> I do not have reliable stored evidence that Snyk offers package-firewall coverage. This is unknown, not confirmed absent.

### "What products does Sonatype offer?"

Use `product_portfolio` retrieval. The answer should list products only from stored active evidence and cite sources.

### "Which JFrog products are deprecated?"

Use retrieval against JFrog product portfolio or release-lifecycle evidence. Only products with explicit sunset/deprecation evidence should be labeled deprecated. Historical products without explicit sunset evidence should be described as historical or not currently in the retrieved portfolio, not confirmed deprecated.

### "Where is GitLab partial?"

A `partial_coverage` missing reason means some evidence exists but the system does not consider it full coverage. For example, transitive dependency scanning can support partial recursive/deep scanning without proving full recursive deep scanning breadth.

## Business Caveats

- Evidence freshness matters. Old claims may be stale.
- Official product pages are stronger for product existence than third-party summaries.
- Third-party examples can prove integration patterns, but not necessarily native vendor capability.
- Marketplace or partner pages do not automatically prove technical support.
- Internal vendor engineering pages can explain architecture, but may not prove customer-facing product coverage.
- A single source can support multiple dimensions, but each dimension should still be evaluated on its own scope.

