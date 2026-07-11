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
- `Claude/Decisions.md` — append-only decision log, one line per decision:
  `- YYYY-MM-DD — <decision> ([[session note]])`. Append here whenever a
  session makes a real decision (and still narrate it in the session note).
- `Claude/Graph/` — the semantic graph: entity notes (People, Organizations,
  Domains, Topics, Projects, Sources) maintained by `index_brain`. Fix wrong
  facts by editing entity notes directly; the graph is markdown. Prefer
  `related_notes`/`find_entity` when gathering context for a topic.
- `Claude/Answers/` — rendered answers from `/recall` queries, one note per
  question (`YYYY-MM-DD <question slug>.md`, frontmatter `type: answer` and
  `question:`). Every claim cites its source note as a `[[wikilink]]`;
  model-knowledge additions are labeled *(not from the vault)*. Past answers
  are legitimate retrieval sources — that is the point.
  `/resume --save` milestone snapshots (`type: resume`) also live here.
- `Claude/Digests/` — one review digest per run (`YYYY-MM-DD.md`, frontmatter
  `type: digest`) written by `/digest`: librarian health, captures to triage,
  tasks, recent changes, pending proposals, new graph activity, suggested
  questions. Rerunning the same day replaces that day's digest.

## Retention

- Two kinds of memory: **context** (evergreen — Sessions, Concepts,
  Decisions) and **connections** (transient — Inbox captures, passing
  references). Keep context; let connections expire.
- The test before promoting anything to a Concept or Decision: *will having
  this memory still be useful in a year?* If not, it is noise — leave it in
  the Inbox.
- `Claude/Inbox/` is prunable at any time. Graduate anything worth keeping
  into Concepts, Tasks, or Decisions before pruning.
- Recall filing rule: **file what compounds, skip what expires.** Answers and
  digests are filed — they gain value as retrieval sources. Resume briefings
  and unblessed connection lists are not — filing expiring state teaches
  search to retrieve stale context.

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

## Organizer

Standing permission (granted by Taimoor, 2026-07-08): the organizer may
move notes within the human topical tree autonomously — vault-root notes
and misfiled notes in topical folders — filing each where its semantic
neighbors live (K=10 cosine vote, share ≥ 0.7). It NEVER touches Claude/,
00 - Maps of Content, dotfolders, non-markdown files, or notes with
`organize: false` frontmatter; it never creates or renames folders; on any
duplicate filename stem it proposes instead of moving. Every move is
journaled to Claude/Organizer.md and reversible via the undo_move tool.
Low-confidence classifications queue as proposals in the same note —
resolving them teaches future votes.
