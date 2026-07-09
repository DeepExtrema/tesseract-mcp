# Architecture

How tesseract-mcp turns a plain-markdown Obsidian vault into a searchable,
graph-connected, self-organizing memory for AI agents. The vault is always
the single source of truth; everything the server builds on the side is a
disposable cache.

## 1. System overview

```mermaid
flowchart TB
    subgraph surface["MCP surface"]
        server[server.py — 20 tools]
    end
    subgraph retrieval["Retrieval"]
        hybrid[hybrid.py — RRF fusion]
        bm25[bm25.py]
        emb[embeddings.py]
        sca[sc_adapter.py — Smart Connections reader]
        search[search.py — candidates, filters]
    end
    subgraph graphlayer["Graph"]
        indexer[indexer.py — incremental index]
        extractor[extractor.py — codex/claude backends]
        graph[graph.py — traversal]
        gstore[graphstore.py — SQLite mirror]
        consolidate[consolidate.py — dedupe]
    end
    subgraph vaultio["Vault IO"]
        vault[vault.py — quarantine enforcement]
        notes[notes.py]
        tasks[tasks.py]
        cache[cache.py]
    end
    subgraph provorg["Provision and organize"]
        provision[provision.py]
        conventions[conventions.py]
        organize[organize.py — CLI]
        organizer[organizer.py — neighbor vote]
        mover[mover.py — link-rewriting moves]
    end
    server --> hybrid --> bm25 & emb & search
    emb --> sca
    server --> graph --> gstore
    server --> indexer --> extractor & gstore
    server --> vault
    hybrid --> vault
    organizer --> mover --> vault
```

## 2. The retrieval pipeline

`search_brain` runs a hybrid retrieval pipeline in `hybrid.py`:

1. **Candidate set.** `search.iter_candidate_notes` scans the vault,
   applying optional tag/folder filters and skipping non-content
   directories (`SKIP_DIRS`: `.obsidian`, `.trash`, `.git`).
2. **BM25 ranking.** The candidate corpus is ranked by keyword relevance
   (top 50). The index is rebuilt fresh per query — rank-bm25 has no
   incremental-update API, and a personal vault is cheap to re-tokenize.
   The BM25L variant is used rather than Okapi, whose Robertson IDF
   yields zero scores on terms appearing in most of a small corpus.
3. **Vector ranking.** The query is embedded and scored by cosine
   similarity against every candidate's note vector; the top 50
   positive-similarity paths form the second ranked list.
4. **Fusion.** The two lists are merged with Reciprocal Rank Fusion
   (k=60): each item scores `1/(k + rank)` summed across lists. RRF fuses
   by rank position rather than raw score, so BM25 scores and cosine
   similarities never need to be normalized against each other.
5. **Substring fallback.** A third, substring-matched list joins the
   fusion **only when BM25 returns nothing**. BM25 tokenizes `[a-z0-9]+`,
   so queries it cannot token-match (single characters, punctuation-only)
   fall through to substring matching. As the code comment puts it: when
   BM25 has results, the alphabetically-ordered substring list would just
   pollute the fusion.

**Vector source.** `embeddings.py` prefers Smart Connections' own
embeddings, read directly from the plugin's on-disk `.smart-env` store by
`sc_adapter.py`, whenever they are fresh for a note. Stale or missing
entries get a locally computed fallback vector, cached by content hash in
`fallback_embeddings.json`. The invariant: vectors from different models
live in unrelated spaces, so the fallback model is pinned to exactly the
model Smart Connections uses (TaylorAI/bge-micro-v2 via
sentence-transformers) — mixing models would silently corrupt similarity
ranking.

**Freshness.** Fallback embeddings are precomputed at the end of each
incremental indexing run (`indexer.run_index` with
`precompute_embeddings=True`), so search never blocks on embedding
staleness checks.

## 3. The semantic graph

Entity notes are **real markdown** under `Claude/Graph/` — `People/`,
`Organizations/`, `Domains/`, `Topics/`, `Projects/`, `Sources/`
(`graphstore.py`). They appear in Obsidian's graph view, sync through
LiveSync like any other note, and are mirrored into a rebuildable SQLite
cache under `~/.tesseract-mcp/` (`cache.py`) for fast traversal.

<!-- SCREENSHOT: entity-note -->

- **Extraction.** `indexer.py` keeps a hash-diff manifest and processes
  only new/changed notes. Each changed note goes through an LLM extraction
  pass (`extractor.py`) that emits typed entities and relations; the
  backend is selected by `TESSERACT_EXTRACTOR=codex|claude` (default
  `codex`).
- **Traversal.** `related_notes` walks shared-entity chains between notes
  (a `hops` parameter bounds the walk) and reports the connecting chain.
  `context_bundle` composes hybrid search, the entities of each hit, and
  related notes in a single call.
- **Consolidation.** `consolidate.py` merges alias/duplicate entities
  (name variants of the same thing) into canonical ones — dry-run by
  default, both as the `consolidate_graph` tool and as
  `python -m tesseract_mcp.consolidate <vault> [--apply]`.

## 4. The write contract

The quarantine is enforced in `vault.py`, in code rather than by
convention: no path may escape the vault root, and writes outside the
`Claude/` subtree require `confirm_outside_claude=True`, which callers may
only pass when the user explicitly asked for the write.

The human-readable rules — the constitution — live in the vault itself at
`Claude/README.md`. Connecting MCP clients receive orientation through the
server's MCP `instructions`, and the `onboard` tool returns the full guide
(constitution, routing rules, cheat-sheet, graph status).

## 5. The organizer

`organizer.py` files notes into the vault's existing folder taxonomy:

- **Taxonomy discovery.** The existing top-level folders are the frozen
  taxonomy. Hard exclusions: all dot-directories (config/tooling, never
  topical), the `Claude/` subtree and other non-topical folders, and the
  vault-root agent guides (`CLAUDE.md`, `AGENTS.md`, `README.md`) that
  agents read from the root.
- **Classification.** A cosine-weighted K-nearest-neighbor vote (K=10)
  among already-organized notes. If the winning folder's share of
  similarity mass is ≥ 0.7, the note moves; below that, a proposal is
  queued for the human in `Claude/Organizer.md`.
- **Safe moves.** `mover.py` rewrites path-qualified wikilinks and
  transfers manifest state so inbound links stay resolvable. Every move is
  journaled (JSONL under `~/.tesseract-mcp/`) with a human-readable mirror
  in `Claude/Organizer.md`; `undo_move` reverts a journaled move.
- **CLI.** `python -m tesseract_mcp.organize <vault> [--dry-run]`. The
  first run against a real vault MUST be `--dry-run` and human-reviewed;
  the flagless form is the scheduled autonomous path.

## 6. Sync & storage

The markdown vault is the single source of truth. The SQLite graph mirror
and embedding caches live under `~/.tesseract-mcp/` (keyed per vault) and
are disposable: delete them and `index_brain` rebuilds everything from the
markdown.

Self-hosted LiveSync (CouchDB) replicates the vault — including
`Claude/Graph/` — to every machine, so agents on different hosts share one
mind. Server infrastructure (compose file, Caddyfile) lives in `server/`;
see [server/DEPLOY.md](../server/DEPLOY.md).

## 7. Module map

| Module | Responsibility |
|---|---|
| `server.py` | FastMCP server exposing the Tesseract vault to Claude |
| `vault.py` | Filesystem access to the Obsidian vault with safety rules (path containment, Claude/ write quarantine) |
| `search.py` | Full-text search across the vault (candidate scan, filters, frontmatter parsing) |
| `hybrid.py` | Hybrid retrieval: BM25 + vector similarity, fused via Reciprocal Rank Fusion |
| `bm25.py` | In-memory BM25 keyword ranking over vault notes, rebuilt fresh per query |
| `embeddings.py` | Vector source for hybrid search: Smart Connections embeddings when fresh, same-model cached fallback otherwise |
| `sc_adapter.py` | Reads Smart Connections' local embeddings directly from disk (`.smart-env` ajson format) |
| `indexer.py` | Incremental vault indexing: hash-diff manifest → extract → store → cache |
| `extractor.py` | LLM entity extraction via pluggable CLI backends (codex / claude) |
| `graphstore.py` | Markdown-native graph store: entity notes under `Claude/Graph/` |
| `graph.py` | Vault metadata queries: frontmatter, wikilink backlinks, recent files |
| `cache.py` | Derived SQLite cache over the `Claude/Graph` markdown (rebuildable anytime) |
| `consolidate.py` | LLM-driven consolidation of duplicate graph entities |
| `notes.py` | Structured note operations for the `Claude/` subtree (sessions, concepts, inbox) |
| `tasks.py` | Task operations compatible with the Obsidian Tasks plugin format |
| `organizer.py` | Autonomous vault organizer: cosine-weighted K-nearest-neighbor folder vote |
| `organize.py` | CLI sweep for the organizer (`python -m tesseract_mcp.organize`) |
| `mover.py` | Moves a vault note while keeping every inbound link resolvable |
| `provision.py` | Provisions a fresh Obsidian vault: pinned plugins, settings, conventions tree |
| `conventions.py` | Installs the `Claude/` conventions tree into a vault (idempotent) |
