---
created: 2026-07-05
agent: claude
tags: [meta, constitution]
---

# The Claude/ Constitution

Rules for every AI agent writing to this vault. Read this before writing.

## Ownership

- Everything under `Claude/` is agent territory: write freely, following the
  structure below.
- Everything OUTSIDE `Claude/` belongs to Taimoor. Read freely; write only
  when explicitly asked, and confirm the exact path first.

## Structure

- `Claude/Inbox/` — quick captures. One note per day (`YYYY-MM-DD.md`),
  timestamped bullets.
- `Claude/Sessions/` — one note per significant work session:
  `YYYY-MM-DD <short title>.md`. Record what was done, learned, and decided.
  Make titles distinctive; a duplicate title on the same day is auto-numbered
  (`<title> 2.md`) rather than overwritten.
- `Claude/Concepts/` — evergreen topic notes, one concept per note. Before
  creating a new concept, `search_brain` for the topic first — extend the
  existing note rather than fragmenting knowledge across near-duplicates.
  Concept names are case-insensitive (`ESG Ratings` and `esg ratings` are the
  same note). Extend under dated `## Update` headings; never silently rewrite
  history.
- `Claude/Index.md` — map of contents. Append a line per new session note.
- `Claude/Tasks.md` — actionable follow-ups as `- [ ]` checkboxes (Obsidian
  Tasks format, due dates as `📅 YYYY-MM-DD`). Add tasks here instead of
  burying them in prose.

## Note format

- YAML frontmatter on every agent note: `created`, `agent`, `project`, `tags`.
- Use `[[wikilinks]]` to connect related notes — an unlinked note is a lost
  memory.
- Write for the reader who has no session context: full sentences, no
  transcript dumps.

## Conflict etiquette

- Never resolve LiveSync conflicts by deleting someone else's content.
- When extending an existing note, append; don't reorder or rewrite what a
  human wrote.
