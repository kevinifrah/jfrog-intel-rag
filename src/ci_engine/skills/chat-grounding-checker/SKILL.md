---
name: chat-grounding-checker
description: Validate chat answers against retrieved evidence before returning them.
---

# Chat Grounding Checker

Block answers that look confident but are not grounded.

## Failure Conditions
- Factual claims without citations.
- Citation IDs that are not in the supplied evidence list.
- Unsupported superiority claims such as "best", "leader", or "more complete" without evidence.
- Hidden contradictions that should be disclosed.
- Invented financials, market share, customer counts, roadmap items, prices, or product support.
- Source-snippet summaries that do not answer the user's actual question.
- Backend-facing language such as chunk IDs, report IDs, tool names, raw validation codes, source paths, tags, or keyword artifacts in the answer prose.

## Repair Behavior
- Remove unsupported claims.
- Lower confidence when evidence is vendor-stated, stale, sparse, or contradictory.
- Return `not enough evidence` when the evidence cannot answer the question.
- Keep the answer concise unless the user asks for depth.
- Preserve an executive narrative voice when repairing; do not fall back to bullet-point source dumps.
