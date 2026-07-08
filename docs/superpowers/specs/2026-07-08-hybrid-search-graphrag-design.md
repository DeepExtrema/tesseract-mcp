# Hybrid Search & Relational Context — Design Spec

**Date:** 2026-07-08
**Status:** Approved by Taimoor (brainstorming session, 2026-07-08)
**Builds on:** tesseract-mcp v0.3 (semantic graph — see 2026-07-05-semantic-graph-design.md)

## Purpose

Turn `search_brain` from a naive substring scan into a real hybrid retrieval
engine (BM25 keyword ranking + vector/semantic similarity, fused), and add a
single `context_bundle` tool that returns ranked search hits plus their
connected entities and graph-related notes in one call — the "precompute so
one call returns complete context" principle behind GitNexus-style code
intelligence tools, applied to the note graph instead of a codebase.

This is the retrieval half of "git nexus equivalent for the entire brain."
Git-style commit history and entity-graph community/cluster detection were
both considered and explicitly deferred — see Non-goals.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Scope | Retrieval hub only ("git" in the reference product refers to indexing git repos, not version control) |
| Vector embedding source | Reuse Smart Connections' local embeddings (already installed, `.smart-env/multi/*.ajson`, `TaylorAI/bge-micro-v2`), with a same-model local fallback for notes it hasn't embedded yet |
| BM25 | New, in-process (Smart Connections' sibling plugin Omnisearch keeps its index in Obsidian's Electron IndexedDB only — not file-accessible, so BM25 can't be reused, only reimplemented) |
| Fusion | Reciprocal Rank Fusion (RRF) — merges rank positions, not raw scores, so BM25 and cosine similarity don't need to be on comparable scales |
| Tool surface | `search_brain` keeps its name/signature, engine swapped underneath; new `context_bundle` tool added |
| Multi-vault forward-compat | Key `state_dir()` by a hash of `TESSERACT_VAULT_PATH` instead of a fixed folder, so a future second (e.g. company vs. personal) vault doesn't collide with this one — no other multi-vault work in scope |
| Git-style history | Deferred — see Non-goals |
| Community/cluster detection | Deferred — see Non-goals |

## Architecture

```
Vault .md files ──────────────┐              Smart Connections
                               │              .smart-env/multi/*.ajson
                               ▼                       │
                        BM25 index (new)                │
                               │              Vector index (new adapter)
                               │                       │
                               └────────┬──────────────┘
                                        ▼
                          Reciprocal Rank Fusion (new)
                                        │
                    ┌───────────────────┼────────────────────┐
                    ▼                   ▼                    ▼
             search_brain         context_bundle       related_notes
          (same tool, new         (new — hits +        (unchanged —
           engine underneath)      entities + graph)    existing graph.db)
```

## Components

### 1. Smart Connections adapter (`sc_adapter.py`, new)

Reads `.smart-env/multi/*.ajson` directly. These files are genuinely
append-only — inspecting a real note in the vault showed 9 entries across 10
lines for one note (1 whole-note embedding + 8 chunk-level embeddings),
meaning the same key can recur as a note is re-embedded over time. The
adapter must:

- Parse line-by-line (each line is a `"key": {...}` fragment, not a single
  JSON document)
- Keep only the **last occurrence per key** (most recent write wins)
- Compare each entry's `last_embed.hash` against the note's current content
  hash (reusing the same sha256 approach `indexer.scan_notes` already uses)
  to classify each note as `fresh` (Smart Connections embedded current
  content) or `stale`/`missing`

### 2. Fallback embedder

Fires only for notes the adapter marks `stale`/`missing` — expected mainly
for notes written via MCP tools (`write_note`, `log_session`, `capture`)
while Obsidian wasn't open, since Smart Connections has no headless mode and
only re-embeds while the Obsidian process is running.

**Hard constraint:** must load the identical model Smart Connections uses —
`TaylorAI/bge-micro-v2`, confirmed in `.smart-env/smart_env.json` — via
`sentence-transformers`, run locally. Vectors from a different model live in
an unrelated space; mixing them into one similarity ranking would silently
produce meaningless results, not an obvious error.

Fallback vectors are cached in tesseract-mcp's own state (not written back
into `.smart-env/`, which stays Smart Connections' own territory) and marked
with a provenance flag (`self_embedded: true`) so it's inspectable which
notes are plugin-fresh vs. self-embedded — useful for debugging and for a
future "how much of the vault is Smart Connections actually keeping fresh"
health check.

### 3. BM25 index (`bm25.py`, new)

In-process Python implementation (e.g. `rank-bm25`) over the same note
corpus. Rebuilt incrementally using the hash-diff manifest `indexer.py`
already maintains — no second sync mechanism, no new "what changed" logic.

### 4. Fusion

Reciprocal Rank Fusion combines the BM25-ranked list and vector-ranked list
into one ordering by rank position (`1 / (k + rank)`, summed per document
across both lists), which avoids having to normalize/weight BM25 scores
against cosine similarities on incomparable scales.

### 5. Tool changes

- **`search_brain(query, tags?, folder?, limit?)`** — signature unchanged;
  implementation becomes BM25 + vector + RRF instead of the current
  `q in line.lower()` scan. Existing callers get ranked results for free.
- **`context_bundle(query_or_path, hops?)`** (new) — runs hybrid search (or,
  if given a note path, starts from that note directly), then for the top
  hits: looks up their mentioned entities (reusing `cache.note_entity_paths`)
  and graph-connected notes within N hops (reusing `cache.related_notes`).
  Returns one combined payload: ranked hits, the entities involved, and the
  graph neighborhood — instead of an agent chaining `search_brain` →
  `find_entity` → `related_notes` across three round trips.
- `find_entity`, `related_notes`, `graph_stats`, `consolidate_graph` —
  unchanged.

### 6. Indexing pipeline changes

`indexer.py`'s existing incremental run (hash-diff manifest, batches,
failure backoff — see `run()`) gains two steps per changed note, alongside
today's entity extraction:

1. Refresh/insert the note's BM25 entry
2. Check Smart Connections freshness via the adapter; self-embed on
   stale/missing

Same manifest, same `remaining`/`skipped` counters already exposed today.

### 7. Vault-scoped state directory

`indexer.state_dir()` currently returns a fixed `~/.tesseract-mcp/`
regardless of which vault `TESSERACT_VAULT_PATH` points at. Change it to
include a short hash of the resolved vault path in the folder name (e.g.
`~/.tesseract-mcp/<vault-hash>/`), so a second vault (e.g. a future
company/personal split) gets its own manifest, graph.db, BM25 index, and
fallback-embedding cache automatically, with no migration needed later.
Existing single-vault users are unaffected beyond a one-time reindex (the
old flat `~/.tesseract-mcp/` state simply stops being read).

## Non-goals (explicitly deferred, not rejected)

- **Git-style commit history/diffing.** Real value (diff, blame, revert) but
  a separate engineering problem from retrieval, and it interacts badly with
  the vault's existing dual state: obsidian-git is installed but currently
  dormant (no active `.git` repo in the vault), and it would run alongside
  obsidian-livesync's CouchDB-based sync — two independent conflict-
  resolution systems on the same files is a known source of friction.
  Piggybacking on CouchDB's native revision history was considered and ruled
  out: LiveSync end-to-end encrypts note content before it reaches CouchDB
  (confirmed in `server/DEPLOY.md`), so the server only ever sees ciphertext
  revisions — there's no shortcut there, plain git would be the simpler path
  if this is picked up later.
- **Entity graph community/cluster detection** (Leiden/Louvain clustering +
  LLM-summarized clusters, as in Microsoft's GraphRAG). Valuable for
  high-level "what are the main themes in my vault" queries, and cheap to
  add later on top of the existing entity graph (same
  `extractor.complete_json` mechanism already used for entity extraction) —
  but additive, not a prerequisite for hybrid search or `context_bundle`.
- **Multi-vault (company vs. personal) separation beyond the state-dir fix.**
  No routing, no access control, no second vault stood up in this round —
  just the one forward-compatible change (#7 above) so it doesn't become a
  migration later.
- **Cloud embedding APIs.** Everything stays local (Smart Connections'
  bge-micro-v2 or the same model as fallback), consistent with this being a
  personal/company knowledge vault.

## Testing considerations

- `sc_adapter.py`: parse a real multi-entry `.ajson` fixture, assert
  last-occurrence-wins and correct fresh/stale classification against a
  content-hash mismatch.
- Fallback embedder: assert it's only invoked for `stale`/`missing` notes,
  and that its output uses the same model identifier as Smart Connections'
  recorded `embed_model.transformers.model_key`.
- `bm25.py`: standard ranking sanity checks (exact term match ranks above
  partial, rare terms outrank common terms) plus incremental rebuild only
  touching changed notes.
- Fusion: known small fixture where BM25-only and vector-only would each
  rank differently, assert RRF output order.
- `context_bundle`: assert it composes `search_brain` + existing
  `cache.note_entity_paths` + `cache.related_notes` without duplicating
  their logic.
- `state_dir()`: assert two different `TESSERACT_VAULT_PATH` values resolve
  to two different state directories.
