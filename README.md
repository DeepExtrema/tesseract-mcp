# tesseract-mcp

MCP server exposing the Tesseract Obsidian vault ("the mind database") to
Claude. Operates directly on the vault filesystem; Self-hosted LiveSync
replicates changes to all machines via CouchDB.

## Install

    python -m venv .venv
    .venv\Scripts\pip install -e .

## Register with Claude Code

    claude mcp add --scope user tesseract `
      -e TESSERACT_VAULT_PATH=C:\Vaults\Tesseract `
      -- C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\.venv\Scripts\tesseract-mcp.exe

## Tools

| Tool | Purpose |
|---|---|
| `search_brain` | Full-text search, optional tag/folder filters |
| `read_note` | Read any note |
| `log_session` | Session log into `Claude/Sessions/` + index update |
| `capture` | Quick thought into `Claude/Inbox/` |
| `upsert_concept` | Evergreen notes in `Claude/Concepts/` |
| `write_note` | General write — quarantined to `Claude/` unless explicitly confirmed |

## The contract

Agents write proactively **only inside `Claude/`**. Everything else is
read-only unless the user explicitly asks. The quarantine is enforced in
code (`vault.py`), and the human-readable rules live in the vault at
`Claude/README.md`.

Server infrastructure (CouchDB + Caddy for LiveSync) lives in `server/`.
