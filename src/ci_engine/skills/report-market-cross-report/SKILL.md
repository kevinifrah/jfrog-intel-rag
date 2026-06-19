---
name: report-market-cross-report
description: Canonical market framework anchors — shared positioning axes, market description, and Five Forces baselines used across ALL competitor dossiers to ensure cross-report comparability.
---

# Cross-report market context — canonical framework anchors

This skill defines the shared analytical foundation that **every** Market Analyst section must use,
regardless of which competitor the dossier covers. A reader opening three different dossiers should
see positioning maps with the same axes — enabling direct, side-by-side comparison across reports.

---

## Canonical positioning map axes

**Use these exact axes in every report. Do not invent new axes.**

| Field | Value |
|-------|-------|
| `x_axis_label` | `Supply-chain coverage breadth` |
| `x_low_label` | `Single ecosystem / one workflow` |
| `x_high_label` | `Universal repository + full SDLC` |
| `y_axis_label` | `Security specialization depth` |
| `y_low_label` | `Platform with security add-ons` |
| `y_high_label` | `Purpose-built security toolchain` |

**What the axes mean:**
- **X (breadth):** How wide is the vendor's supply-chain coverage? Low = one language ecosystem or one
  pipeline stage. High = universal binary management across package formats, multiple registries, CI/CD
  integration, and end-to-end software delivery controls.
- **Y (depth):** Is security the product's centre of gravity or an add-on? Low = general DevOps or
  developer platform where security is adjacent. High = purpose-built toolchain where OSS governance,
  vulnerability intelligence, and policy enforcement are the primary product.

**Why these axes for this market:** The software supply chain security space separates vendors on exactly
these two tensions. Broad repository and SDLC coverage rewards consolidation buyers; security depth rewards
security-led evaluations. Keeping axes fixed across all dossiers lets the reader compare competitive
positions across Snyk, Sonatype, GitLab, GitHub, and others on a single frame.

**Placement guidance:**
- Always include JFrog and the named competitor, both marked `is_focus: true`.
- Add 2–4 contextual players that appear in the evidence (e.g. GitHub, GitLab, Sonatype, Checkmarx, Endor
  Labs, Socket) — only where cited evidence supports their inclusion.
- Coordinates (0–100 scale) are analytical judgements, not measured data; say so in the `narrative`.
- Do not place a player unless the EvidencePack contains cited evidence for that placement.

---

## Canonical market description

The software supply chain security market sits at the convergence of artifact management,
open-source governance, and DevSecOps automation. It is driven by:
- **Regulatory pressure:** government SBOM mandates, EU Cyber Resilience Act, US Executive Order 14028.
- **Enterprise risk appetite** around open-source dependency exposure and AI/ML artifact supply chains.
- **Platform consolidation pressure:** buyers want fewer vendors covering more of the SDLC.

**Core competitive tensions — carry these forward in every market context thesis:**
1. Repository platform breadth and universality vs security-first depth.
2. Developer-native experience (shift-left) vs governance, policy enforcement, and compliance.
3. Platform consolidation buyers vs best-of-breed security buyers.

These tensions define the strategic group dynamics for every competitor in this space.
Name them explicitly in the section thesis so the read is grounded in structure, not individual features.

---

## Five Forces baseline for this market

The intensities below are the **starting point for every report**. Adjust only if your EvidencePack
contains strong cited evidence that the market has shifted for this specific comparison. If you adjust,
state the reason and cite it.

| Force | Baseline intensity | Key factor to address in rationale |
|-------|--------------------|-------------------------------------|
| `competitive_rivalry` | **High** | Multiple credible players (JFrog, Snyk, Sonatype, GitHub, GitLab, Checkmarx, Endor Labs) chase the same security and DevSecOps budget; core SCA capability is commoditising, pushing differentiation to data quality, firewall control points, and platform breadth. |
| `threat_of_new_entrants` | **Moderate** | Capital still flows to new entrants (Endor Labs, Socket, etc.), but enterprise trust, proprietary vulnerability research, and deep CI/CD integration create a real barrier to displacement at scale. |
| `threat_of_substitutes` | **Moderate** | Free cloud-native tooling (GitHub Dependabot, GitLab scanners) substitutes credibly at the low end; it does not match enterprise-grade repository firewall, federated deployment, or SBOM governance. |
| `buyer_power` | **High** | Platform consolidation pressure is real; large enterprises run multi-vendor evaluations. Switching is painful once a registry is embedded, but security tooling can be swapped — creating asymmetric buyer leverage. |
| `supplier_power` | **Low** | Public vulnerability feeds (NVD, OSV) are commodity inputs. Proprietary malware research and reachability analysis are differentiation assets, not supply-side constraints. Cloud hosting commoditises infrastructure. |

In the prose thesis (not in the `five_forces` array): state the **net market structure** — where the
durable moats sit (typically proprietary vulnerability data + enterprise trust + platform breadth) and
what that means for JFrog's structural position in this specific comparison.
