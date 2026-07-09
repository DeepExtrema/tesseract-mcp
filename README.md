# tesseract-mcp

MCP server exposing the Tesseract Obsidian vault ("the mind database") to
Claude. Operates directly on the vault filesystem; Self-hosted LiveSync
replicates changes to all machines via CouchDB.

## Install

    python -m venv .venv
    .venv\Scripts\pip install -e .

## Provision a new vault

    python -m tesseract_mcp.provision C:\Path\To\NewVault

Installs the curated plugin set (pinned in `vault-template/plugins.json`),
enables them, seeds Smart Connections settings (embed model pinned to the
one `sc_adapter` reads), and installs the Claude/ conventions tree. Then:
open the vault once in Obsidian and turn off Restricted Mode, complete
LiveSync via Setup-URI, and run `index_brain`.

    python -m tesseract_mcp.provision C:\Path\To\Vault --check

reports pinned vs installed versions (ok / drift / missing). Upgrading a
plugin = bump its pin in `vault-template/plugins.json`, re-run provision.

## Autonomous organizer

    python -m tesseract_mcp.organize C:\Vaults\Tesseract --dry-run   # ALWAYS first
    python -m tesseract_mcp.organize C:\Vaults\Tesseract             # scheduled sweep

Files notes into the existing top-level folders by embedding neighbor vote
(share ≥ 0.7 moves; below queues a proposal in `Claude/Organizer.md`).
Every move is journaled and reversible (`undo_move` tool). The FIRST run
against a real vault must be --dry-run and human-reviewed. MCP tools:
`organize_vault(apply?)` (dry-run default) and `undo_move(path)`.

## Register with Claude Code

    claude mcp add --scope user tesseract `
      -e TESSERACT_VAULT_PATH=C:\Vaults\Tesseract `
      -- C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\.venv\Scripts\tesseract-mcp.exe

## Tools

| Tool | Purpose |
|---|---|
| `onboard` | Call first in a new session — constitution, routing, cheat-sheet, graph status |
| `search_brain` | Hybrid search (BM25 + vector, fused) — optional tag/folder filters |
| `context_bundle` | One call: hybrid search hits + their graph entities + related notes |
| `read_note` | Read any note |
| `log_session` | Session log into `Claude/Sessions/` + index update |
| `capture` | Quick thought into `Claude/Inbox/` |
| `upsert_concept` | Evergreen notes in `Claude/Concepts/` |
| `write_note` | General write — quarantined to `Claude/` unless explicitly confirmed |
| `add_task` | Add a checkbox task to `Claude/Tasks.md` in Obsidian Tasks-plugin format, optional due date |
| `list_tasks` | List checkbox tasks across the vault (open only by default) |
| `query_notes` | Query notes by frontmatter metadata (Dataview-style) |
| `get_backlinks` | List notes whose `[[wikilinks]]` point at a given note |
| `list_recent` | Most recently modified notes, newest first |
| `index_brain` | Extract entities from new/changed notes into the semantic graph |
| `find_entity` | Look up graph entities (people, orgs, domains, topics…) by name/alias |
| `related_notes` | GraphRAG: notes connected via shared entities, with the connecting chain |
| `graph_stats` | Entity/edge/mention counts for the graph |
| `consolidate_graph` | Merge duplicate graph entities (dry-run by default) |

## The contract

Agents write proactively **only inside `Claude/`**. Everything else is
read-only unless the user explicitly asks. The quarantine is enforced in
code (`vault.py`), and the human-readable rules live in the vault at
`Claude/README.md`.
Connecting clients receive orientation via MCP `instructions`; call `onboard` for the full guide.

## The semantic graph

`Claude/Graph/` holds LLM-extracted entity notes (People/, Organizations/,
Domains/, Topics/, Projects/, Sources/) whose wikilinks connect source notes
into a typed knowledge graph — visible in Obsidian, synced by LiveSync,
queried through a rebuildable SQLite cache in `~/.tesseract-mcp/`. Index on
demand with the `index_brain` tool or `python -m tesseract_mcp.indexer
<vault>` (extraction backend: TESSERACT_EXTRACTOR=codex|claude).

**Scheduled sweep:** to keep the graph fresh automatically, point Windows Task
Scheduler (or a Claude Code scheduled agent) at
`python -m tesseract_mcp.indexer C:\Vaults\Tesseract --backend codex` on a
nightly cadence. It only processes new/changed notes, so repeat runs are cheap.

To merge duplicate entities (name variants of the same thing), run
`python -m tesseract_mcp.consolidate <vault> [--apply]` — dry-run by default;
pass `--apply` to merge into canonical entities.

Server infrastructure (CouchDB + Caddy for LiveSync) lives in `server/`.
