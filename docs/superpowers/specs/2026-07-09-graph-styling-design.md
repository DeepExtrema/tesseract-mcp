# Graph Styling: Styled Graph + Transparent Theme — Design

**Date:** 2026-07-09
**Status:** Approved
**Build order:** Built SECOND, after the MCP server bundle
(`2026-07-09-mcp-server-bundle-design.md`). Its visual output feeds the
README screenshot task (Task 3 of the documentation overhaul plan).

## Goal

Make the vault's graph view and overall appearance presentation-quality:
a graph-styling plugin (node color/shape/image control) and a
transparency-capable theme, pinned in the provisioner template so every
provisioned vault gets them, and applied to the live Tesseract vault.

## Decisions already made

- **Research and propose:** the user has no specific plugins/themes in
  mind; the implementation plan starts with a research task presenting
  2–3 candidates per category (graph-styling plugin; transparent theme or
  translucency plugin) with screenshots and maintenance signals. **The
  user picks; nothing is pinned unpicked.**
- **Target: template + live vault.** Chosen items are pinned in
  `vault-template/` AND applied to `C:\Vaults\Tesseract` so the styled
  graph is visible immediately.

## Components

### 1. Plugin pinning (existing machinery)

The chosen graph-styling plugin is added to `vault-template/plugins.json`
with a pinned version, exactly like the existing pinned plugins. Any
settings needed to make the graph look right ship as a settings template
in `vault-template/` (absent-only overlay, existing provisioner
behavior).

### 2. Theme support (new provisioner capability)

Themes are not plugins; the provisioner gains a parallel mechanism:
- `vault-template/themes.json`: pinned theme name + version + GitHub
  release source (mirrors `plugins.json` structure).
- Installer downloads the theme into `.obsidian/themes/<name>/`
  (`theme.css` + `manifest.json`).
- `appearance.json` gets `cssTheme: "<name>"` **absent-only**: if the
  vault already has a theme set, the provisioner does not override it.
- `provision --check` extends to report theme pin drift (ok / drift /
  missing), same vocabulary as plugins.

### 3. Live vault application

Run the extended provisioner against the live vault. Two deliberate
carve-outs from normal absent-only behavior:
- The live vault's `appearance.json` may already set a theme. Applying
  the new theme there is the ONE place the tool asks for explicit
  confirmation before overriding (`--set-theme` flag or interactive
  prompt; never silent).
- If LiveSync replicates `.obsidian/`, styling changes propagate to every
  machine. The apply step states this out loud before proceeding; it is
  not treated as an error.

## Error handling

- Theme download failure → report and continue with other work; vault is
  never left with a half-installed theme (write to a temp dir, move into
  place atomically).
- Unknown/renamed release asset in the pinned theme → clear drift message
  in `--check` rather than a crash.

## Testing

- Provisioner tests extended: theme install happy path (temp vault
  fixture, stubbed download), absent-only `cssTheme`, `--check` drift
  vocabulary for themes, atomic install (no partial theme dir on
  simulated failure).
- Visual verification by eye in Obsidian on the live vault — this is also
  the capture moment for the documentation overhaul's pending screenshot
  task (the hero graph shot should be taken AFTER styling lands).

## Out of scope

- Fixing the LiveSync warning (tracked separately by the user).
- Any redesign of graph *content* (entity extraction, tags, folders) —
  this sub-project is purely visual.
