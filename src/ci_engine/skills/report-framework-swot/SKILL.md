---
name: report-framework-swot
description: Build an evidence-based, decision-feeding SWOT of the competitor (seen from JFrog) from the EvidencePack. Used by the Strategy Analyst.
---

# SWOT framework

You are building a SWOT of **the competitor, viewed from JFrog's vantage** — set `vantage` to name this
explicitly (e.g. "Sonatype, from JFrog's perspective"). A SWOT is only useful if it feeds a decision, so every
item must connect to an action the rest of the dossier can use (buyer-fit, implications, battlecard).

Fill four quadrants, each item one specific, evidence-relative line (≤ 22 words):
- `strengths` — what the competitor genuinely does better, stated concretely and commercially (not "good
  product"). These are the things JFrog must respect or counter.
- `weaknesses` — where the competitor is exposed *relative to JFrog or the market*, specific and fair.
- `opportunities` — external tailwinds (regulation, market shifts, new fronts) the competitor can ride.
- `threats` — external pressures on the competitor (JFrog's momentum, specialist entrants, commoditisation).

Discipline of a top analyst:
- Strengths/weaknesses are **internal and relative**; opportunities/threats are **external**. Do not file an
  internal capability as an "opportunity."
- Each item is specific and falsifiable, tied to cited `evidence_ids`. No generic filler.
- Be adversarially honest: real strengths in Strengths, real exposure in Weaknesses — do not soften the
  competitor to flatter JFrog.
- Keep it tight: 3–5 items per quadrant. A long undifferentiated list is not analysis.

Output: populate the `swot` object. Put evidence IDs only in each item's `evidence_ids`, never in the text.
