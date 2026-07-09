# Documentation Overhaul: README + ARCHITECTURE — Design

**Date:** 2026-07-09
**Status:** Approved
**Scope decision:** This effort covers documentation only. Two related ideas
raised in the same brainstorm — packaging the repo as an installable bootstrap
that includes the depended-on MCP servers, and adding graph styling (styled
graph plugin + transparent theme) to the provisioner's pinned set — were
explicitly deferred to their own future specs.

## Goal

Replace the current terse operator README with public/portfolio-quality
documentation: a product-style `README.md` front page plus a
`docs/ARCHITECTURE.md` deep dive, with Mermaid diagrams and curated Obsidian
screenshots. The docs must accurately describe the system as built —
especially the information-retrieval upgrades (hybrid search, semantic graph
/ GraphRAG) — verified against code, not just restated from old specs.

## Audience & tone

Primary audience: a stranger on GitHub (hiring manager, fellow engineer).
Product-style narrative: pitch first, features as headliners, quickstart with
generic paths. Machine-specific paths (e.g. `C:\Vaults\Tesseract`, the
absolute venv path in the `claude mcp add` command) are replaced with
placeholders like `<path-to-vault>`; a single example may remain in a
clearly-marked "example" register block.

## Deliverables

1. `README.md` — full rewrite.
2. `docs/ARCHITECTURE.md` — new.
3. `docs/assets/` — screenshots captured from the live Obsidian vault via
   computer use, each reviewed by the user before commit.

## README.md structure (~2 screens)

1. **Title + one-line pitch.** "A persistent, shared mind for AI agents,
   built on an Obsidian vault." One short paragraph: every Claude session
   reads/writes the same knowledge base, replicated across machines.
2. **Hero screenshot.** Vault graph view (ideally the `Claude/Graph/` entity
   cluster), framed per the redaction rules below.
3. **How it works — one Mermaid diagram.** Claude ⇄ MCP server ⇄ vault
   filesystem ⇄ LiveSync/CouchDB ⇄ other machines; SQLite cache
   (`~/.tesseract-mcp/`) shown as a rebuildable sidecar.
4. **Feature tour.** Short sections, IR upgrades first:
   - Hybrid search: BM25 + vector similarity, score fusion, tag/folder
     filters.
   - Semantic graph + GraphRAG: LLM-extracted entities as real markdown
     notes, `related_notes` traversal, `context_bundle` composition.
   - The write contract: `Claude/` quarantine enforced in code, the vault
     constitution.
   - Autonomous organizer: embedding neighbor-vote filing, journaled
     reversible moves, proposals queue.
   - Vault provisioner: one command, pinned plugins, drift check.
5. **Tools reference.** Existing table retained, regrouped by purpose:
   retrieve / write / graph / maintain.
6. **Quickstart.** Install → provision → register with Claude Code, generic
   paths.
7. **Pointers** to `docs/ARCHITECTURE.md` and `server/DEPLOY.md`.

## docs/ARCHITECTURE.md structure

1. **System overview.** Larger Mermaid component diagram placing every module
   in `src/tesseract_mcp/` in a layer: MCP surface (`server.py`) → retrieval
   (`search.py`, `hybrid.py`, `bm25.py`, `embeddings.py`, `sc_adapter.py`) →
   graph (`graph.py`, `graphstore.py`, `indexer.py`, `extractor.py`,
   `consolidate.py`) → vault I/O (`vault.py`, `notes.py`, `tasks.py`,
   `cache.py`) → provisioning/organizing (`provision.py`, `conventions.py`,
   `organize.py`, `organizer.py`, `mover.py`).
2. **The retrieval pipeline** (centerpiece). Query flow through BM25 and
   vector search; score fusion; substring ranking used only as BM25-empty
   fallback; semantic recall for multi-token queries; embedding freshness
   precomputed during incremental indexing; where Smart Connections
   embeddings (`sc_adapter.py`) fit vs. sentence-transformers.
3. **The semantic graph.** Extraction backends (codex|claude), entity notes
   under `Claude/Graph/` (People/, Organizations/, Domains/, Topics/,
   Projects/, Sources/), typed wikilinks, SQLite mirror, GraphRAG traversal
   in `related_notes` / `context_bundle`, consolidation of duplicates.
4. **The write contract.** Quarantine enforcement in `vault.py`
   (`confirm_outside_claude`), the constitution at `Claude/README.md`, why
   agents write freely only under `Claude/`.
5. **The organizer.** Taxonomy discovery, hard exclusions (dot-directories,
   vault-root agent guides), cosine-weighted neighbor vote with the 0.7
   share threshold, journal + human-readable mirror, undo.
6. **Sync & storage.** Vault markdown as the single source of truth; SQLite
   as a rebuildable cache; LiveSync/CouchDB replication; what happens when
   the cache is deleted.
7. **Module map.** One line per file in `src/tesseract_mcp/`.

Content is sourced from the code first; existing specs under
`docs/superpowers/specs/` serve as background, but every documented claim is
verified against the current implementation.

## Screenshots

Captured by the agent via computer use on the user's desktop; the user
reviews every image before it is committed.

Shot list:
1. Graph view — whole vault or `Claude/Graph/` cluster (hero).
2. An entity note in `Claude/Graph/` showing typed wikilinks.
3. Optional: the organizer proposals note (`Claude/Organizer.md`).

Framing/redaction rules (hard requirements):
- The unresolved LiveSync warning banner must not appear.
- No `Job Search/` or other personal-content notes readable in frame.
- No sync-status popups or notices.
- If a shot cannot be framed cleanly, ship a placeholder slot instead of the
  image.

## Error handling / risks

- README makes no claims about unfinished work (the LiveSync warning is not
  mentioned; server setup defers to `server/DEPLOY.md`).
- Screenshot leakage risk mitigated by the framing rules + per-image user
  review.
- Doc drift risk: diagrams are Mermaid-in-git so they are editable alongside
  code changes.

## Verification

- Mermaid blocks rendered (GitHub-flavored) to confirm they parse.
- Every quickstart command checked against the actual entry points
  (`pyproject.toml` scripts, `python -m tesseract_mcp.provision`,
  `python -m tesseract_mcp.organize`, `python -m tesseract_mcp.indexer`).
- Tool table cross-checked against the tools actually registered in
  `server.py`.
- Final text and all screenshots reviewed by the user.
