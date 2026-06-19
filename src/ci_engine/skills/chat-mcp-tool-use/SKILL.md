---
name: chat-mcp-tool-use
description: Choose chat-facing MCP tools and arguments accurately.
---

# Chat MCP Tool Use

Use MCP tools as evidence retrievers, not as answer writers.

## Tool Selection
- `search_answer_context`: default chat retrieval tool for fast answers from DB plus reports.
- `search_report_sections`: use for report-specific findings, validation blockers, scores, section narratives, or generated report content.
- `get_report_registry`: use for available reports, generated time, validation status, and PDF availability.
- `search`: use for a narrow DB lookup when the planner knows the exact query, company, and dimensions.
- `compare_dimension`: use for a single ontology dimension comparison between companies.
- `coverage_matrix`: use to explain corpus coverage, gaps, freshness, or confidence.
- `source_inventory`: use to inspect available sources for a company/dimension.
- `get_source_detail`: use only after source IDs are known and the user asks for detail or citations.

## Argument Rules
- Keep `query` natural but specific.
- Include `competitors` whenever the question names companies.
- Include `dimensions` only when the question maps clearly to ontology dimensions.
- Keep `max_items` low for chat, usually 6-10.
- Never pass raw user text as a source ID, file path, or SQL-like value.
