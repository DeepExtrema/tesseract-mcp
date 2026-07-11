## Learned User Preferences

- Executes implementation plans via subagent-driven-development: fresh subagent per task, spec compliance review first, then code quality review.
- Prefers slow, step-by-step deployment walkthroughs when doing something for the first time.
- For Oracle Cloud VM creation: use On-demand capacity (not Dedicated host), disable Confidential computing on the instance, and pair A1 Flex with an aarch64 Ubuntu image (e.g. Ubuntu 24.04 Minimal aarch64).
- Oracle image list "BM Confidential computing" in the Security column does not mean every image requires confidential compute; pick aarch64 Ubuntu for A1 and turn off confidential compute only if the instance form shows an incompatibility error.
- MCP live sync (`python -m tesseract_mcp.mcp_sync` without `--check`) and live-vault styling/provision require explicit user consent; agents dry-run with `--check` and throwaway vaults only.
- Skill sync (`python -m tesseract_mcp.skill_sync` without `--check`) to the real `~/.claude/skills` requires explicit user consent; agents run `--check` (optionally with a scratch `--dest`) only.
- Obsidian screenshots from the live vault need human review of every image before commit (LiveSync banner and personal content).

## Learned Workspace Facts

- `tesseract-mcp` repo: `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`, base branch `codex/architecture-roadmap`; run Python with `.venv\Scripts\python`; tests with `.venv\Scripts\python -m pytest -q`.
- Live Tesseract vault: `C:\Vaults\Tesseract`; markdown is source of truth; agent writes quarantined to `Claude/`.
- Sync deployment: Oracle Always Free A1 VM running `tesseract-mcp/server/` stack (CouchDB + Caddy + DuckDNS); Obsidian Self-hosted LiveSync on the vault.
- Semantic graph markdown in `Claude/Graph/` is authoritative; derived cache at `~/.tesseract-mcp/graph.db`; run `--rebuild-only` when graph markdown changed without note re-indexing.
- Vault installer (`scripts/install_conventions.py`) is idempotent and never overwrites existing files.
- `mcp-servers.json` at repo root is the curated MCP manifest; `tesseract_mcp.mcp_sync` additively registers servers into `~/.claude.json` via `claude mcp add` (never modify or remove existing entries).
- `vault-template/plugins.json` pins Obsidian plugins; `provision.py` auto-enables every successfully installed pin (`[s.id for s in specs if s.id not in errors]`).
- On Windows, `mcp_sync` resolves the `claude` CLI via `shutil.which` (injectable in tests) so npm's `claude.cmd` shim is found.
- Obsidian plugin pins must use release tags whose manifest version matches the tag (avoids provision idempotency drift and LiveSync churn).
- (Migrated 2026-07-09 from the retired Sentinal-ESG workspace, where early tesseract work happened.)

## MCP server rule: no lazy heavy imports in tool bodies

Never import C-extension chains (numpy, torch, sentence_transformers)
inside an MCP tool body or anything a tool calls lazily. On Python 3.14 +
Windows, the first such import inside the FastMCP worker thread stalls
until the next stdin message arrives — the server appears to hang forever
on single requests (root-caused 2026-07-11; see the audit session log in
the vault). Eager-import at server startup in the main thread instead:
`server._warm_start()` exists for exactly this. Verify with
`python scripts/probe_server.py`.
