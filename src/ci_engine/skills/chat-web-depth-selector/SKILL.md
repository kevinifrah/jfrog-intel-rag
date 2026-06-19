---
name: chat-web-depth-selector
description: Decide between Tavily ultra-fast and fast web checks for chat.
---

# Chat Web Depth Selector

Use Tavily automatically when it materially improves answer freshness, completeness, or confidence.

## Depth Policy
- `ultra-fast`: freshness checks, simple public confirmation, recent news, availability/status questions, or "is this still true?"
- `fast`: product/capability gaps, vendor documentation checks, technical feature validation, contradictory evidence, or high-impact buyer guidance.
- Do not use `advanced` in chat v1.
- If `ultra-fast` returns weak or no credible evidence and web evidence is required, retry once with `fast`.
- If a web call fails because a depth is unsupported, retry once with `fast`.

## Cost Policy
- Prefer no web only when MCP/report evidence is clearly enough and the question is not time-sensitive or gap-sensitive.
- Prefer `ultra-fast` unless missing or consequential product evidence needs stronger relevance.
- Record the selected depth in answer metadata.
