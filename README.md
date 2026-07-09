# tesseract-mcp

**A persistent, shared mind for AI agents, built on an Obsidian vault.**

Every Claude session — on any machine — reads from and writes to the same
knowledge base: a plain-markdown Obsidian vault replicated by Self-hosted
LiveSync (CouchDB). This MCP server is how agents search it, extend it, and
keep it organized.

![The vault's semantic graph in Obsidian](docs/assets/hero-graph.png)

## How it works

```mermaid
flowchart LR
    subgraph Agent side
        C[Claude or any MCP client]
    end
    C <-->|MCP tools| S[tesseract-mcp server]
    S <-->|read/write markdown| V[(Obsidian vault)]
    S <-->|rebuildable caches| Q[(SQLite + embeddings in ~/.tesseract-mcp/)]
    V <-->|LiveSync| DB[(CouchDB)]
    DB <-->|LiveSync| M2[Vault on other machines]
```

The vault's markdown is the single source of truth. Everything the server
computes — the search index, embeddings, the graph cache — lives under
`~/.tesseract-mcp/` and is rebuildable from the vault on demand, so it never
has to travel through LiveSync itself.

## What's inside

### Hybrid search
BM25 keyword ranking and embedding cosine similarity, fused with Reciprocal
Rank Fusion — rank-based fusion means the two score spaces never need to be
normalized against each other. Vectors reuse Obsidian's Smart Connections
embeddings when fresh, with a same-model local fallback (bge-micro-v2) so the
similarity space is never mixed.

### A semantic knowledge graph (GraphRAG)
An LLM pass extracts people, organizations, domains, topics, projects and
sources from notes into real markdown entity notes under `Claude/Graph/` —
visible in Obsidian's graph, synced like everything else, and mirrored into
SQLite for traversal. `related_notes` walks entity chains between notes;
`context_bundle` composes hybrid search and graph context in one call.

### A write contract agents can't break
Agents write freely only under `Claude/` (sessions, concepts, inbox, tasks,
decisions, graph). Everything else is the human's: readable always, writable
only with explicit confirmation — enforced in code, not by convention. The
human-readable rules live in the vault as a constitution.

### An autonomous organizer
New notes are filed into the existing folder taxonomy by embedding
neighbor-vote (≥0.7 agreement moves the note; less queues a human proposal).
Every move is journaled and reversible.

### One-command vault provisioning
`python -m tesseract_mcp.provision <path-to-vault>` installs a pinned plugin
set, seeds settings (embed model pinned to what the search stack reads), and
installs the agent conventions tree. `--check` reports version drift.

## Tools

| | Tool | Purpose |
|---|---|---|
| **Orient** | `onboard` | Call first in a new session — constitution, routing, cheat-sheet, graph status |
| **Retrieve** | `search_brain` | Hybrid search (BM25 + vector, RRF-fused), optional tag/folder filters |
| | `context_bundle` | One call: search hits + their graph entities + related notes |
| | `read_note` | Read any note |
| | `query_notes` | Query notes by frontmatter metadata |
| | `get_backlinks` | Notes whose `[[wikilinks]]` point at a note |
| | `list_recent` | Recently modified notes |
| | `list_tasks` | Checkbox tasks across the vault |
| **Write** | `log_session` | Session log into `Claude/Sessions/` |
| | `capture` | Quick thought into `Claude/Inbox/` |
| | `upsert_concept` | Evergreen notes in `Claude/Concepts/` |
| | `write_note` | General write — quarantined to `Claude/` unless confirmed |
| | `add_task` | Checkbox task in `Claude/Tasks.md` (Obsidian Tasks format) |
| **Graph** | `index_brain` | Extract entities from new/changed notes |
| | `find_entity` | Look up entities by name/alias |
| | `related_notes` | GraphRAG: notes connected via shared entities, with the chain |
| | `graph_stats` | Entity/edge/mention counts |
| | `consolidate_graph` | Merge duplicate entities (dry-run default) |
| **Organize** | `organize_vault` | Autonomous filing sweep (dry-run default) |
| | `undo_move` | Revert a journaled move |

## Quickstart

```powershell
git clone <repo> ; cd tesseract-mcp
python -m venv .venv
.venv\Scripts\pip install -e .

# Provision a fresh vault (plugins, settings, conventions)
python -m tesseract_mcp.provision <path-to-vault>

# Register the curated MCP server set (tesseract + web/paper ingest)
$env:TESSERACT_VAULT_PATH = "<path-to-vault>"
.venv\Scripts\python -m tesseract_mcp.mcp_sync
```

The manifest lives in `mcp-servers.json`; sync is additive-only (existing
entries are never modified or removed).

Then open the vault once in Obsidian (disable Restricted Mode, complete
LiveSync setup) and run the `index_brain` tool.

## Going deeper

- [Architecture deep dive](docs/ARCHITECTURE.md) — retrieval pipeline,
  graph design, module map.
- [Server deployment](server/DEPLOY.md) — CouchDB + Caddy for LiveSync.
