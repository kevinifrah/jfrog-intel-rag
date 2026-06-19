---
name: report-framework-pestel
description: Build a PESTEL macro-environment analysis for the software-supply-chain-security market from the EvidencePack. Used by the Market Analyst.
---

# PESTEL framework

You are running a PESTEL analysis: the macro forces shaping the market both vendors compete in. PESTEL is the
first lens in the analyst sequence (PESTEL → Five Forces → SWOT → strategy), so frame forces that the later
lenses build on, not vendor-specific product claims.

Produce one factor per axis: **Political, Economic, Social, Technological, Environmental, Legal.**

For each axis write two short parts:
- `factor` — the single most decision-relevant force in this market right now, in one line (≤ 20 words). Anchor
  it to a concrete, dated, cited signal when one exists (a regulation date, an attack-volume figure, an
  adoption statistic). Vendor-stated numbers are phrased as claims.
- `implication` — the "so-what" for how this market is won (≤ 16 words): which capability or buyer it rewards.

Discipline of a top analyst:
- Be specific and current; a PESTEL full of generic truisms ("technology is changing fast") is worthless.
  Name the actual driver (e.g. a named regulation and its enforcement date, a quantified attack trend).
- Be honest about the **least material** axis. Set `material: false` for any axis that is not a real driver
  today (often Environmental) and say so in one line rather than inventing significance.
- Tie every factual factor to cited `evidence_ids`. If an axis has no supporting evidence, still name the
  force but keep `evidence_ids` empty and lower the certainty in wording; never fabricate a statistic.
- Lead with the implication mindset: each factor should obviously feed the Five Forces and strategy that follow.

Output: populate the `pestel` array (one `PestelFactor` per axis). Put evidence IDs only in `evidence_ids`,
never in `factor` or `implication` text.
