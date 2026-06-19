---
name: report-confidence-tiering
description: Build the methodology and confidence appendix that grades the dossier's evidence into tiers. Used by the Strategy Analyst.
---

# Confidence tiering (methodology appendix)

You are writing the methodology note that tells a reader **how much to trust each kind of claim** in the
dossier. This protects credibility: an executive should see at a glance which figures are hard and which are
soft.

Sort the dossier's claims into these tiers (use the exact `tier` keys), one short paragraph each (≤ 45 words):
- `high` — primary sources: regulatory filings, official company financials/SEC filings, vendor product
  documentation for capabilities, independently reported analyst placements.
- `medium` — third-party or point-in-time estimates: private-company financials "as reported", market-size
  ranges that vary across analysts, funding/ARR estimates. Note that ranges are given rather than single
  numbers when analysts disagree.
- `vendor_claim` — vendor marketing figures (false-positive counts, "% of CVEs not exploitable",
  packages-blocked, hours-reclaimed). State that these are attributed, not independently verified.
- `author_judgment` — analytical interpretation: positioning-map axes, SWOT severities, weighting choices.
  Make clear these are judgments, not data.

Then fill `spot_check` (1–3 lines): the specific softest numbers a reader should verify before an executive
review (typically private-company ARR/valuation and the market-size figure).

Discipline:
- Be concrete about *which* claims fall in each tier in this report, not generic definitions.
- Do not inflate confidence; if the strongest evidence is vendor-stated, say so plainly.

Output: populate `confidence_tiering` (`tiers` + `spot_check`). This is methodology prose; it carries no
inline citations.
