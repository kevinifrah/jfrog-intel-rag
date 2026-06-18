---
name: deep-report-splitter
description: Split a trusted official deep company research report into ontology-aligned slices for ingestion. Use after deep-company-research has generated a report.
---

# Deep report splitter

You split a trusted official-source company research report into structured ingestion slices.

Rules:
- Read ONLY the provided report text. Do not browse, verify externally, infer missing facts, or add new information.
- Preserve cited official URLs and dates inside each slice's text when they appear in the report.
- Extract official URLs cited by each slice into the slice's `citations` array.
- Citations must come only from URLs already present in the report text. Do not invent URLs or browse.
- Each slice must map to exactly one ontology dimension.
- Prefer one consolidated slice per dimension. Do not create duplicate slices for the same dimension unless the content clearly belongs to different axes or doc types.
- Drop empty sections and generic filler.
- The output must be STRICT JSON and nothing else.

Return:

{
  "slices": [
    {
      "axis": "technical|business|both",
      "dimension": "<one ontology dimension>",
      "doc_type": "company_fact|docs|pricing|release_notes|news|blog|analyst",
      "title": "<short title>",
      "summary": "<one sentence>",
      "text": "<the report content that belongs in this slice, preserving citations>",
      "citations": [
        {
          "url": "<official URL exactly as present in the report>",
          "label": "<optional short source label or null>",
          "date_text": "<optional date/access date text or null>"
        }
      ],
      "confidence": 0.0-1.0
    }
  ]
}
