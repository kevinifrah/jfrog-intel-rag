---
name: report-framework-five-forces
description: Build a Porter's Five Forces industry-structure analysis from the EvidencePack. Used by the Market Analyst.
---

# Porter's Five Forces framework

You are assessing **industry structure** — how attractive and contestable this market is — not the two vendors
head to head. Run each force as a checklist, rate its intensity, and justify the rating from cited evidence.

Cover all five forces, using these exact keys:
- `competitive_rivalry` — How many credible players chase the same budget? Is the core capability commoditised,
  pushing differentiation to data quality, noise reduction, or platform breadth?
- `threat_of_new_entrants` — Is capital flowing to new entrants? What are the real barriers (proprietary data,
  enterprise trust) and are they impregnable?
- `threat_of_substitutes` — What free/native/adjacent tooling substitutes at the low end, and what defends
  against it?
- `buyer_power` — How hard do buyers negotiate and consolidate? Where is switching painful vs contestable?
- `supplier_power` — How much power do input suppliers (public vuln feeds, OSS ecosystems, cloud) hold? Note
  any twist that turns a commodity input into an asset.

For each force:
- `intensity` — rate `high`, `moderate`, or `low`. Be willing to spread the ratings; an all-"high" analysis
  signals you did not actually weigh the force.
- `rationale` — 1–2 sentences (≤ 40 words) explaining the rating with a concrete, cited reason. Name actual
  players or signals from the evidence, not abstractions.
- `evidence_ids` — cite the supporting evidence; leave empty only when the rating rests on visible market
  structure rather than a specific source, and soften the wording accordingly.

Close the picture in your section thesis (not in this array): state the **net structure** — where the durable
moats sit (typically proprietary data + enterprise trust + platform breadth) and what that means for JFrog.

Output: populate the `five_forces` array (one `FiveForce` per force). Never put evidence IDs in the rationale
text. Keep vendor-stated figures phrased as claims.
