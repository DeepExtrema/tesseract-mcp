# Curated MCP Server Bundle — Design

**Date:** 2026-07-09
**Status:** Approved
**Build order:** This sub-project is built FIRST, before the graph styling
sub-project (`2026-07-09-graph-styling-design.md`).

## Goal

Make this repo the manifest of the user's MCP stack: a pinned list of the
MCP servers every machine should have registered in Claude Code, plus a
sync command that makes reality match — additively and safely. The same
philosophy as `vault-template/plugins.json`, applied to MCP servers.

## Background / constraints

- Current user-scope Claude Code config (`~/.claude.json` → `mcpServers`)
  contains exactly one server: `tesseract`, pointed at this clone's venv.
- Other servers the user encounters (GitHub, Notion, context7…) arrive via
  Claude Code plugins and claude.ai connectors, which are configured
  elsewhere and are NOT managed by this tool. The manifest manages only
  servers registered via `claude mcp` (stdio/http entries in
  `~/.claude.json`).
- Sync policy decision: **additive only.** The tool never removes and never
  modifies an existing config entry. Drift and extras are reported, with
  exact remediation commands printed for the human to run.

## Components

### 1. Manifest: `mcp-servers.json` (repo root)

One entry per server. Schema:

```json
{
  "servers": [
    {
      "name": "tesseract",
      "transport": "stdio",
      "command": "{REPO}\\.venv\\Scripts\\tesseract-mcp.exe",
      "args": [],
      "env": { "TESSERACT_VAULT_PATH": "{VAULT}" },
      "why": "The mind database — persistent shared memory for agents."
    }
  ]
}
```

- Placeholders: `{REPO}` = absolute path of this clone (resolved at sync
  time from the package location); `{VAULT}` = vault path (resolved from
  `TESSERACT_VAULT_PATH` env or `--vault` flag; sync fails with a clear
  message if neither is set and a manifest entry needs it).
- `transport: "http"` entries use `url` instead of `command`/`args`.
- The manifest replaces the hand-written `claude mcp add` block as the
  source of truth for registering tesseract itself.

### 2. Sync module: `src/tesseract_mcp/mcp_sync.py`

CLI: `python -m tesseract_mcp.mcp_sync [--check] [--vault <path>]`.

Behavior:
1. Read `~/.claude.json` (read-only). Parse failure → abort with message,
   zero writes.
2. Classify each manifest entry against config: **present** (all of
   command/url, args, env match after placeholder resolution), **drifted**
   (registered but any field differs), or **missing**.
3. Report all three classes, plus **extras** (config servers not in the
   manifest) as informational only.
4. `--check`: stop after the report (exit code 1 if anything is missing or
   drifted, 0 if clean — usable in scripts).
5. Default mode: register each missing server by shelling out to
   `claude mcp add --scope user …`; echo each command and its result.
   Drifted/extra entries are never touched; the report prints the exact
   `claude mcp remove`/`claude mcp add` pair a human would run to fix
   drift.

Error handling:
- `claude` CLI not on PATH → actionable message ("install Claude Code or
  run the printed commands manually"), print the commands, exit non-zero,
  no partial state.
- A failed `claude mcp add` for one server does not abort the rest; the
  summary lists per-server outcomes.

### 3. Starter set — researched, gated

The implementation plan's first task is research, not code: survey current,
well-maintained MCP servers fitting the user's workflow (Obsidian vault +
software development + job search) — candidate space includes filesystem,
fetch/web, GitHub, and library-docs servers — and present a shortlist with
maintenance signals and trade-offs, including whether duplicating
plugin-provided servers (e.g. GitHub) is worth it for plugin-independence.

**Nothing enters the manifest without the user's explicit pick.** The
manifest ships initially with `tesseract` plus only the picked servers.

## Testing

pytest, same style as the provisioner tests:
- Fake `~/.claude.json` fixtures + stubbed `subprocess` for the `claude`
  CLI.
- Cases: missing → registered; drifted (command/args/env each) → reported,
  untouched; extra → reported, untouched; placeholder resolution ({REPO},
  {VAULT}); unparseable config → abort before any subprocess call; absent
  claude CLI → commands printed, non-zero exit.
- The additive-only invariant is asserted directly: after any sync run
  against any fixture, every pre-existing config entry is byte-identical.

## Documentation

README quickstart's `claude mcp add` block is replaced by
`python -m tesseract_mcp.mcp_sync`; ARCHITECTURE.md module map gains one
row. (Small, folded into the implementation plan's final task.)
