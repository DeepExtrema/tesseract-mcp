# Vault Provisioner — Design Spec

**Date:** 2026-07-08
**Status:** Approved by Taimoor (brainstorming session, 2026-07-08)
**Builds on:** tesseract-mcp v0.4 (hybrid search — see 2026-07-08-hybrid-search-graphrag-design.md), `scripts/install_conventions.py`

## Purpose

One command turns a fresh Obsidian vault into a working Tesseract mind
database: installs the curated community-plugin set at pinned versions,
enables them, applies opinionated settings where the vault has none, and
installs the Claude/ conventions tree. Exists for the planned company vault
(and any future machine/vault) so provisioning is minutes of downloads, not
an afternoon of clicking through Obsidian's plugin browser.

Obsidian has no CLI/API for installing community plugins; a plugin on disk
is just `manifest.json` + `main.js` (+ optional `styles.css`) in
`.obsidian/plugins/<id>/` pulled from the plugin's GitHub release, plus an
entry in `community-plugins.json`. The provisioner does exactly that.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Plugin set | Tiers 1+2 (9 plugins): infrastructure (Smart Connections, Tasks, LiveSync, Text Extractor) + knowledge-work UX (Dataview, Omnisearch, Tag Wrangler, Kanban, Advanced Tables, Importer). Tier 3 (cosmetic/personal: Beautitab, Iconize, Claudian, Terminal, …) is NOT provisioned — personal taste stays personal, especially on a company vault |
| Approach | Manifest-driven downloader: `vault-template/plugins.json` pins id + GitHub repo + version; assets downloaded from GitHub releases at provision time. Rejected: golden-image (blobs in git, staleness, redistribution gray zone) and offline-cache hybrid (speculative until a network posture actually demands it) |
| Delivery | Operator CLI (`python -m tesseract_mcp.provision <vault>`), NOT an MCP tool — agents should not trigger vault provisioning |
| Settings | Overlays applied only when the plugin has no existing `data.json` — re-provisioning never clobbers human tweaks. Only plugins needing opinionated settings get an overlay (Smart Connections: embed model MUST be pinned to `TaylorAI/bge-micro-v2` or `sc_adapter.py` reads nothing) |
| LiveSync | Half-provisioned by design: plugin installed + enabled, but server URI and E2E passphrase are secrets that never live in the repo — completing setup stays human (Setup-URI flow) |
| Conventions | Provisioning ends by running the existing conventions installer (constitution, agent guides, Claude/ tree). Its `install()` moves into the package (`tesseract_mcp/conventions.py`); `scripts/install_conventions.py` stays as a thin wrapper |

## Pinned plugin set (initial versions = today's live vault)

| id | repo | version |
|---|---|---|
| smart-connections | brianpetro/obsidian-smart-connections | 4.5.3 |
| obsidian-tasks-plugin | obsidian-tasks-group/obsidian-tasks | 8.2.2 |
| obsidian-livesync | vrtmrz/obsidian-livesync | 0.25.79 |
| text-extractor | scambier/obsidian-text-extractor | 0.7.0 |
| dataview | blacksmithgu/obsidian-dataview | 0.5.68 |
| omnisearch | scambier/obsidian-omnisearch | 1.29.3 |
| tag-wrangler | pjeby/tag-wrangler | 0.6.4 |
| obsidian-kanban | mgmeyers/obsidian-kanban | 2.0.51 |
| table-editor-obsidian | tgrosinger/advanced-tables-obsidian | 0.23.2 |
| obsidian-importer | obsidianmd/obsidian-importer | 1.8.12 |

(10 rows: "Tiers 1+2 (9 plugins)" undercounted — Advanced Tables and
Importer are both in tier 2; the set is 10. Upgrades are a one-line pin bump
plus re-run.)

## Repo layout

```
vault-template/
  plugins.json                      # [{"id", "repo", "version"}] — the table above
  settings/
    smart-connections/data.json     # only if SC needs .obsidian-side settings
    smart-env/smart_env.json        # template for <vault>/.smart-env/smart_env.json
                                    #   (embed model pinned to TaylorAI/bge-micro-v2)
src/tesseract_mcp/
  conventions.py                    # install() moved from scripts/
  provision.py                      # everything below
```

## Provision flow (idempotent)

1. Validate vault root exists; create `.obsidian/` if missing
2. Per plugin, skipped when the installed `manifest.json` version already
   equals the pin: download `manifest.json` from
   `https://github.com/{repo}/releases/download/{version}/{filename}`,
   **verify its `id` field matches the expected id** (guards against repo
   hijack/typo — refuse on mismatch), then `main.js` (required) and
   `styles.css` (optional, some plugins ship none), write into
   `.obsidian/plugins/<id>/`
3. Merge ids into `community-plugins.json` — never remove entries the user
   added themselves
4. Apply settings overlays (absent-only rule); write
   `.smart-env/smart_env.json` from template if absent
5. Run the conventions installer
6. Print the human-remaining checklist: open Obsidian once and turn off
   Restricted Mode (one manual click, unavoidable on a brand-new vault),
   complete LiveSync via Setup-URI, then run `index_brain`

`--check` mode: report pinned vs installed version per plugin
(ok / drift / missing) without touching anything.

Failure isolation: one plugin's download failure records an error and
continues with the rest; failed plugins are not enabled.

## Error handling

- Network/HTTP errors other than 404 → `ProvisionError` with URL and cause
- 404 on `manifest.json` or `main.js` → `ProvisionError` (release/pin wrong);
  404 on `styles.css` → fine, plugin has no stylesheet
- Manifest id mismatch → `ProvisionError`, plugin not written at all
- All network access behind an injectable `fetch(url) -> bytes | None`
  callable (same injection pattern as `CliExtractor`'s `runner`) — tests
  never touch the network

## Non-goals

- Plugin removal/pruning (provisioner adds and repairs only)
- LiveSync secret handling of any kind
- Auto-update daemon (upgrade = bump pin, re-run)
- sha256 lockfile of release assets (future hardening if supply-chain rigor
  is ever needed; the id-match check is v1's guard)
- Tier 3 / personal plugins
- Turning off Obsidian Restricted Mode programmatically (stored in
  app-internal state; one human click on first open)

## Testing considerations

- Fake fetcher fixture serving canned release assets from a dict keyed by
  URL; assert zero real network calls
- Fresh-vault install: all files land, community-plugins.json written,
  overlays applied, conventions installed
- Idempotency: second run with same pins = all "ok", no rewrites
- Merge-not-clobber: pre-existing user plugin id in community-plugins.json
  survives provisioning
- Absent-only overlays: pre-existing data.json is not overwritten
- Manifest id mismatch → error recorded, plugin dir absent, id not enabled
- Missing styles.css (fetcher returns None for it) → plugin still installs
- `--check` reports ok/drift/missing correctly
- Moved conventions module: existing `tests/test_install_conventions.py`
  keeps passing against the thin wrapper
