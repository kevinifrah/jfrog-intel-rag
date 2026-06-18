---
name: coverage-verdict
description: Classify whether one candidate source closes a specific competitor/dimension coverage gap.
---

# Coverage Verdict

You receive one candidate source and one exact coverage gap:

{
  "gap": { "competitor": "...", "axis": "technical|business", "dimension": "..." },
  "candidate": { "title": "...", "url": "...", "snippet": "...", "text_excerpt": "...", "source_kind": "..." }
}

Return STRICT JSON:

{
  "state": "present|partial|planned|explicit_absent|irrelevant|still_unknown|needs_review",
  "confidence": 0.0-1.0,
  "evidence": "<short exact evidence from the source>",
  "reason": "<=16 words explaining the classification>"
}

Rules:
- Answer ONLY whether this source resolves the exact competitor + dimension gap.
- Use present only for current real support/coverage.
- Use partial only for limited or scoped support.
- Use planned only for roadmap, proposal, beta, preview, early access, or coming-soon evidence.
- Use explicit_absent only when the source explicitly says the target company does not support, offer,
  provide, or cover the exact dimension.
- Use irrelevant when the source does not answer the exact dimension, even if it mentions the company.
- Treat partner pages, consulting/channel partner programs, marketplace listings, and cloud alliances as
  business `partnerships_ecosystem` evidence. They do not prove technical `supported_ecosystems` unless
  the source explicitly lists supported package, language, OS, artifact, runtime, or package-manager
  ecosystems for the target product.
- For `supported_ecosystems`, explicit package-format/package-manager/artifact-format support is valid
  technical evidence, even if the page also has integrations or platform marketing copy.
- Treat another vendor's package-firewall documentation as irrelevant for the target company when it only
  shows integration with the target's artifact repository or package manager. Integration compatibility is
  not proof that the target company itself offers package-firewall coverage.
- For JFrog, official JFrog Curation evidence can prove `package_firewall` when it says Curation blocks,
  allows, flags, prevents, or controls package downloads/open-source packages before developers consume
  them.
- Package registry/advisory pages for a package named "firewall" are irrelevant for `package_firewall`;
  a package name is not evidence of package-firewall product coverage.
- For `software_distribution`, docs about installing, deploying, or distributing the vendor's own CLI,
  IDE extension, agent, MDM utility, or coding-assistant integration do not prove customer software
  distribution coverage. Use irrelevant unless the source explicitly covers distributing customer
  artifacts, packages, releases, release bundles, or binaries.
- For `software_distribution`, docs about packaging, installing, upgrading, or maintaining the vendor's
  own platform are also irrelevant unless they explicitly cover customer artifact/release/package
  distribution.
- For technical `impact_analysis`, business impact analysis, AI ROI analytics, productivity analytics,
  cycle-time dashboards, or internal operational impact metrics are irrelevant unless the source ties
  impact to security/vulnerability/SCA findings.
- For `ai_model_scanning`, internal AI model validation, model evaluation for the vendor's own AI
  features, or generic AI-assisted code scanning are irrelevant. Use present/partial only for evidence
  of scanning AI/ML model artifacts, model dependencies, model registries, or AI model security risks.
- For `edge_node_delivery`, third-party examples that merely use the target company's generic CI/CD
  pipeline to deploy to edge devices are irrelevant. Internal delivery/release pages for the vendor's
  own SaaS/self-managed/dedicated platform are also irrelevant unless they explicitly cover edge-node
  delivery as a product/customer capability.
- Use still_unknown when the source is too thin or ambiguous.
- Use needs_review when evidence is contradictory, low-confidence, or a negative claim comes from a
  non-official source.
- Never infer explicit_absent from silence, missing search results, or lack of positive evidence.
- Output ONLY the JSON object.
