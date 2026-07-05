# Tesseract Mind Database — Design Spec

**Date:** 2026-07-05
**Status:** Approved by Taimoor (brainstorming session, 2026-07-05)

## Purpose

Turn the existing Obsidian vault **Tesseract** into a shared "mind database": a
collective knowledge base that Taimoor and Claude (and, later, other agents) can
both read and write. Synchronization across machines is handled by the
Self-hosted LiveSync Obsidian plugin backed by a self-hosted CouchDB server.
Claude interacts with the vault through a purpose-built MCP server
(**tesseract-mcp**).

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Who shares the brain | Taimoor + Claude now; other agents possible later |
| Claude's access path | Custom MCP server (Approach 3), filesystem-based |
| Devices | This PC + other computers (roaming) |
| Server hosting | Oracle Cloud Always Free VM (heavy usage expected; 200 GB storage) |
| Vault | Existing `Tesseract` vault is the brain |
| OneDrive conflict | Move vault out of OneDrive entirely → `C:\Vaults\Tesseract` |
| HTTPS | Free DuckDNS hostname + Caddy with Let's Encrypt |
| Write policy | Contract: proactive writes only inside `Claude/` subtree; anything outside requires explicit user instruction |

## Architecture

```
┌─────────────── Oracle Always Free VM (ARM, 2 OCPU / 12 GB) ───────────────┐
│  Docker:                                                                   │
│    caddy  ──reverse-proxy + Let's Encrypt──►  couchdb:5984                 │
│  Firewall: only 80/443 open. DuckDNS hostname, e.g.                        │
│  taimoor-brain.duckdns.org                                                 │
└────────────────────────────────────────────────────────────────────────────┘
                ▲ HTTPS (E2E-encrypted payloads)
                │
   ┌────────────┴────────────┐
   │ PC (this machine)       │   other computers join later via Setup URI
   │  Obsidian + LiveSync    │
   │  Vault: C:\Vaults\      │
   │         Tesseract       │
   │  tesseract-mcp (Python) │ ◄── Claude Code sessions (any project)
   └─────────────────────────┘
```

- **CouchDB** official Docker image, single node, data on a named Docker
  volume. Admin user + dedicated sync user. Configured per LiveSync
  requirements (CORS, max request size, etc.).
- **Caddy** terminates TLS for the DuckDNS hostname; CouchDB is never exposed
  directly.
- **LiveSync end-to-end encryption is enabled** — the server stores only
  ciphertext.
- **Periodic database cleanup** enabled in LiveSync to control CouchDB
  revision bloat.
- A **DuckDNS updater** (cron or container) keeps the hostname pointed at the
  VM's IP.

## Vault migration

1. Close Obsidian. Copy `C:\Users\Taimoor\OneDrive\Documents\Tesseract` →
   `C:\Vaults\Tesseract`.
2. Open the vault from the new location (plugins/settings live in `.obsidian/`
   inside the vault and survive the move).
3. Keep the OneDrive copy frozen as a backup for ~2 weeks, then delete it.
   Never let OneDrive and LiveSync sync the same live folder.
4. Configure LiveSync against the CouchDB server, perform initial upload,
   verify, then generate a **Setup URI** for enrolling other machines.

## The brain contract (vault conventions)

```
Tesseract/
├── (Taimoor's existing notes — agents read freely, write only when told)
└── Claude/
    ├── README.md          ← the "constitution": rules agents must follow
    ├── Inbox/             ← quick captures ("remember this")
    ├── Sessions/          ← one note per work session (what we did/learned/decided)
    ├── Concepts/          ← evergreen topic notes, updated over time
    └── Index.md           ← auto-maintained map of contents
```

- Agent-written notes carry YAML frontmatter: `created`, `agent`, `project`,
  `tags`.
- Notes use `[[wikilinks]]` to connect into the Obsidian graph.
- Session note filenames: `YYYY-MM-DD <short-title>.md`.
- The constitution (`Claude/README.md`) is the canonical statement of these
  rules; agents read it before writing.

## tesseract-mcp server

Python + FastMCP, operating directly on the vault filesystem (no dependency on
Obsidian running; LiveSync reconciles offline changes at next launch). Vault
path supplied via environment variable `TESSERACT_VAULT_PATH`. Registered with
Claude Code via `claude mcp add`.

### Tools

| Tool | Behavior |
|---|---|
| `search_brain(query, tags?, folder?)` | Full-text search across all `.md` files; optional filtering by frontmatter tags or subfolder. Returns path + matching excerpt per hit. |
| `read_note(path)` | Return note content. Path validated to stay inside the vault. |
| `log_session(title, content, project, tags)` | Create `Claude/Sessions/YYYY-MM-DD <title>.md` with frontmatter; append an entry to `Claude/Index.md`. |
| `capture(content)` | Append a timestamped bullet to the current inbox note in `Claude/Inbox/`. |
| `upsert_concept(name, content)` | Create or extend `Claude/Concepts/<name>.md` (append under a dated heading when the note exists). |
| `write_note(path, content, confirm_outside_claude=False)` | General write. **Refuses paths outside `Claude/` unless `confirm_outside_claude=True`** — the quarantine is enforced in code. Refuses to overwrite an existing file unless an explicit `overwrite=True` flag is set. |

### Error handling

- All paths normalized and validated against the vault root (no `..` escapes).
- Quarantine comparison uses platform filesystem semantics (`os.path.normcase`:
  case-insensitive on Windows, case-sensitive on POSIX), so case-variant paths
  cannot dodge the `Claude/` check.
- Writes/appends targeting an existing directory raise a clean `VaultError`
  instead of leaking raw OS errors.
- Writes never silently overwrite; explicit flags required.
- Missing vault path / unreadable vault produces a clear startup error.
- Tool errors return actionable messages (what failed, what to pass instead).

### Testing

pytest suite against a temporary fixture vault covering: search (content,
tags, folder filter), path validation/escape attempts, quarantine enforcement,
frontmatter generation, index updating, overwrite protection.

## Rollout order

1. **Server:** Oracle VM provisioned (manual signup by Taimoor) → Docker →
   CouchDB + Caddy + DuckDNS via docker-compose kept in this repo.
2. **Vault:** move out of OneDrive → LiveSync configured → initial sync →
   Setup URI generated and stored safely.
3. **Conventions:** `Claude/` structure + constitution written into the vault.
4. **MCP:** tesseract-mcp built (TDD), tested, registered with Claude Code.
5. **Proof:** second computer joins via Setup URI; end-to-end loop verified
   (edit on one machine appears on the other; Claude can search/write via MCP).

## Explicitly human-only steps

Oracle account signup, VM provisioning approval, DuckDNS account creation,
entering the Setup URI on additional devices, and any Obsidian GUI
confirmation dialogs. Everything else is scriptable and lives in this repo.

## Out of scope (YAGNI)

- Multi-user sharing with other people (no per-user auth design yet).
- Embedding/semantic search — full-text is enough to start.
- Automated ingestion pipelines (RSS, web clipper, etc.).
- Custom Obsidian plugin work.
