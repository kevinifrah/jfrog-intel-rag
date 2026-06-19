---
name: report-framework-positioning-map
description: Build a strategic-group / competitive positioning map from the EvidencePack. Used by the Market Analyst.
---

# Strategic-group positioning map

You are placing the field on a two-axis map to show **strategic groups** — clusters of vendors that compete
the same way — and where JFrog and the competitor sit within them.

Method (how strategists actually build these):
1. **Choose two strategically meaningful axes.** Pick the two dimensions that best separate the groups in this
   market (for example: security depth vs platform breadth, or specialist-overlay vs repository-plus-platform).
   Do not use price unless price genuinely separates the players. Give each axis a label plus a low-end and
   high-end label.
2. **Place 3–7 of the most relevant players**, not the whole field. Always include JFrog and the named
   competitor and mark them `is_focus: true`. Position each on a 0–100 scale per axis where the placement
   reflects cited evidence; assign a `group` label to players that cluster together.
3. **State the judgment honestly.** The axes and coordinates are an analytical interpretation, not measured
   data — say so in the `narrative` so no reader mistakes it for a benchmark.

In the `narrative` (≤ 3 sentences): name the strategic group JFrog and the competitor share and why that makes
them direct rivals; describe the intra-group dynamic (who extends which way); and call out the single most
asymmetric move on the board (a reach one side can make that the other cannot reciprocate).

Discipline:
- Coordinates must be defensible from evidence; cite `evidence_ids` on the focus players at minimum.
- Keep player names exact. Do not invent competitors not supported by the evidence or configured field.
- Neither axis is inherently "better" — breadth can mean lock-in; specialism can be the winning counter.

Output: populate `positioning_map` (axes labels + `players`). Keep evidence IDs out of labels and narrative.
