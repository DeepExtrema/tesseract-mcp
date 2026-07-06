# Tesseract Semantic Graph — Design Spec

**Date:** 2026-07-05
**Status:** Approved by Taimoor (brainstorming session, 2026-07-05)
**Builds on:** tesseract-mcp v0.2 (see 2026-07-05-tesseract-mind-database-design.md)

## Purpose

A GitNexus-style GraphRAG layer over the Tesseract vault: LLM-extracted typed
entities and relationships, materialized as markdown inside the vault (visible
to Obsidian, synced by LiveSync, editable by hand) and mirrored into a fast
local query cache for agent retrieval. Serves both consumers: agents get
multi-hop retrieval ("notes connected to X via shared entities"), the human
gets a living graph view/backlinks without their own notes ever being edited.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Consumers | Both agents and human (agent index + Obsidian-visible materialization) |
| Connection intelligence | LLM entity extraction only (typed GraphRAG); no embeddings |
| Extraction backend | Pluggable CLI subprocess: `codex exec` default (ChatGPT Plus quota), `claude -p` fallback |
| Graph storage | Markdown-native in `Claude/Graph/` (source of truth) + derived SQLite cache outside the vault |
| Trigger | On-command (`index_brain` tool / CLI) + optional scheduled sweep calling the same entry point |
| User notes | Never modified; connections surface via backlinks from entity notes |

## Graph model

- **Entity types (fixed):** `person`, `organization`, `domain`, `topic`,
  `project`, `source`.
- **Relation vocabulary (fixed):** `mentions`, `works_at`, `part_of`,
  `operates_in`, `about`, `related_to`. Unknown relations from the extractor
  are coerced to `related_to`.
- **Entity note:** `Claude/Graph/<TypePlural>/<Safe Name>.md`

  ```markdown
  ---
  created: <ts>
  agent: claude
  entity: organization
  aliases: [MSFT]
  tags: [graph/organization]
  ---

  # Microsoft

  One-line summary from extraction.

  ## Mentions

  - [[2026-07-01 Interview with Jane]] — discussed pilot deployment

  ## Relations

  - operates_in [[Supply Chain]]
  ```

- **Identity:** case-insensitive `(type, name-or-alias)`; new aliases merge
  into the existing note's frontmatter. Names pass through `notes.safe_filename`.
- **Idempotency:** re-indexing a note never duplicates Mentions/Relations
  bullets; human edits to entity notes are preserved (appends only, never
  rewrites).

## Extraction pipeline (`extractor.py`)

- Prompt: given note path + content, return STRICT JSON:
  `{"entities": [{"name", "type", "aliases", "summary"}],
    "relations": [{"from", "from_type", "rel", "to", "to_type", "evidence"}]}`
  Types/relations outside the fixed sets are coerced (unknown entity type →
  `topic`). Every extracted entity implies a `mentions` edge from the source
  note.
- Backends (subprocess, 120 s timeout):
  - `codex`: `codex exec <prompt>` (default)
  - `claude`: `claude -p <prompt>`
  Selected via env var `TESSERACT_EXTRACTOR` (default `codex`); content is
  passed inside the prompt.
- JSON parse failure → one retry with a repair instruction → on second
  failure, record the note as failed in the manifest and continue. No partial
  writes.

## Incremental indexing (`indexer.py`)

- State dir: `%USERPROFILE%\.tesseract-mcp\` (outside the vault — LiveSync
  must never sync machine-local state): `manifest.json` (sha256 per note,
  failures, last run) and `graph.db`.
- A run: hash-diff vault notes → extract new/changed only → merge into
  `Claude/Graph/` → rebuild cache. Skips `SKIP_DIRS`, `Claude/Graph/` itself
  (no feedback loops), and an ignore list (default `["copilot"]`).
- CLI entry point (`python -m tesseract_mcp.indexer <vault>`) for scheduled
  sweeps (Claude Code scheduled agent or Task Scheduler); the MCP tool and
  the CLI share the same code path. Batch cap per invocation (default 25
  notes) so MCP calls return in bounded time; the tool reports remaining
  count.

## Query cache (SQLite, stdlib only)

- `graph.db` tables: `entities(name, type, path, summary)`,
  `edges(src, rel, dst, evidence)`, `mentions(entity, note_path, evidence)`.
- Rebuilt wholesale by parsing `Claude/Graph/*.md` after each index run;
  atomic replace (write temp, swap). Deletable anytime; rebuildable on any
  machine from the synced vault.

## New MCP tools (11 → 15)

| Tool | Behavior |
|---|---|
| `index_brain(force=False)` | Incremental extraction + cache rebuild. Returns processed/created/merged/failed/remaining counts. `force=True` re-indexes everything. |
| `find_entity(query, type=None)` | Case-insensitive name/alias match; returns entities with type, summary, relations, mention count. |
| `related_notes(path, hops=2)` | Notes reachable from `path` through shared entities within `hops`; each result carries its connecting chain (e.g. `via Acme Corp (operates_in) Supply Chain`). |
| `graph_stats()` | Entity/edge/mention counts by type, orphan notes (indexed but zero entities), failed notes, last index time. |

## Error handling

- Extractor failures skip-and-record; `index_brain` reports them, never
  aborts the batch.
- Cache rebuild atomic; a crashed run leaves the previous cache intact.
- Tools answering from a missing/stale cache say so ("run index_brain").
- All vault writes go through the existing `Vault` quarantine (everything
  lands under `Claude/Graph/`).

## Testing

- `FakeExtractor` backend (canned JSON) makes the pipeline deterministic:
  unit tests for entity-note merging/idempotency, alias folding, manifest
  diffing, cache rebuild, all four tools; one end-to-end test (fixture vault →
  fake extraction → entity notes → cache → `related_notes` chain).
- Real codex/claude backends implement the same interface; smoke-tested
  manually against a single note (not in CI).

## Out of scope (YAGNI)

Embeddings/vector search, community detection/clustering, custom graph UI,
automatic edits to human notes, real-time per-write indexing.
