# Tesseract Vault — Agent Guide

This vault is the shared mind database for Taimoor and his AI agents
(Claude Code, Claudian, Codex, and future agents). Markdown is the source of
truth; Self-hosted LiveSync replicates it across machines.

## Read this first

- The rules for HOW agents write here live in [[Claude/README]] — the
  constitution. Read it before writing anything.
- Everything OUTSIDE `Claude/` belongs to Taimoor: read freely, write only
  when explicitly asked.

## Routing rules — where things live

- `Claude/Sessions/` — agent work logs (what was done, learned, decided).
- `Claude/Concepts/` — evergreen topic notes; extend, don't duplicate.
- `Claude/Inbox/` — quick transient captures (prunable).
- `Claude/Tasks.md` — actionable follow-ups (Obsidian Tasks checkboxes).
- `Claude/Decisions.md` — append-only decision log.
- `Claude/Graph/` — the semantic entity graph (People, Organizations,
  Domains, Topics, Projects, Sources). Query it before manual exploration.

<!-- Add one routing line per new top-level content folder as it lands, e.g.:
- `Interviews/` — user-conducted interviews (read-only for agents)
- `Resources/` — reference material imported from Notion
-->

## Tools

When the tesseract MCP is available, prefer its tools over raw file access:
`search_brain` (full-text), `query_notes` (frontmatter/Dataview-style),
`find_entity` / `related_notes` / `graph_stats` (semantic graph),
`log_session`, `capture`, `upsert_concept`, `add_task`, `write_note`.
