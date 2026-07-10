# Graph Styling (Extended Graph + Graph Styler + Translucent BG) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tasks 3–4 touch the user's LIVE vault and MUST run in the main session with the stated confirmations.

**Goal:** Pin the picked styling plugins (Extended Graph, Graph Styler, Translucent BG, Style Settings) in the provisioner template, apply them plus the agreed graph quick-wins to the live Tesseract vault, leaving the graph screenshot-ready.

**Architecture:** Pure reuse of the existing provisioner machinery (`vault-template/plugins.json` pins + `provision.py` installer). No new code paths except a `graph.json` backup step during live apply. The `themes.json` capability from the spec is DEFERRED (transparency is plugin-based) — do not build it.

**Tech Stack:** Existing `tesseract_mcp.provision` (Python), Obsidian community plugins from GitHub releases, JSON edits to the live vault's `.obsidian/graph.json`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-09-graph-styling-design.md` — follow exactly, including the DEFERRED status of theme machinery.
- **Back up `.obsidian/graph.json` before any styling plugin is enabled or graph.json edited** (community graph plugins ride an undocumented core API and can reset it).
- Live-vault application must state out loud, before proceeding, that LiveSync may propagate `.obsidian/` changes to all machines — proceed only after the user acknowledges.
- All plugin pins are exact release tags; installed via the existing pinned-plugin mechanism (same as current entries in `vault-template/plugins.json`).
- The LiveSync warning banner remains out of scope; never attempt to fix or interact with it beyond dismissing dialogs during verification.
- Quick-wins (from the user's graph review, session of 2026-07-09): distinct hue per color group (Job Search amber, Customer Discovery teal, Parallax-Hermes blue, Space magenta), a violet group for `path:"Claude/Graph"` ABOVE a broader `path:"Claude"` group (first-match-wins ordering), orphans toggled off.

---

### Task 1: Pin the four plugins in the template

**Files:**
- Modify: `vault-template/plugins.json`
- Test: existing provisioner tests (`tests/` — locate with `grep -l plugins.json tests/`)

**Interfaces:**
- Consumes: the existing `plugins.json` entry schema (read the file first and copy its exact field structure — id, repo, version/tag, asset naming — for the new entries).
- Produces: four new pinned entries with these plugin ids and repos:
  - `extended-graph` — repo `ElsaTam/obsidian-extended-graph`
  - `graph-styler` — repo `moonweave/obsidian-graph-styler`
  - `translucent-bg` — repo (resolve exact id/repo in Step 1)
  - `obsidian-style-settings` — repo `mgmeyers/obsidian-style-settings` (skip if already pinned)

- [ ] **Step 1: Resolve exact ids, repos, and latest release tags**

For each plugin, fetch the manifest to get the true plugin `id` and pin the latest release tag:

```powershell
# Example for Extended Graph; repeat per plugin
curl -s https://raw.githubusercontent.com/ElsaTam/obsidian-extended-graph/HEAD/manifest.json
gh release list --repo ElsaTam/obsidian-extended-graph --limit 3
```

For Translucent BG: find its repo via the community-plugins registry: `curl -s https://raw.githubusercontent.com/obsidianmd/obsidian-releases/HEAD/community-plugins.json | python -c "import json,sys; print([p for p in json.load(sys.stdin) if 'translucent' in p['id'].lower() or 'translucent' in p['name'].lower()])"`. Record id + repo + latest tag for all four.

- [ ] **Step 2: Write the failing check**

Run the provisioner's check against a throwaway vault BEFORE editing, to capture the baseline:

```powershell
mkdir $env:TEMP\styling-test-vault
.venv\Scripts\python -m tesseract_mcp.provision $env:TEMP\styling-test-vault --check
```

Expected: the four plugins are NOT listed (missing from pins).

- [ ] **Step 3: Add the four entries to `vault-template/plugins.json`**

Copy the exact schema of an existing entry (open the file; match field names precisely). One entry per plugin with the id/repo/tag recorded in Step 1.

- [ ] **Step 4: Verify by provisioning the throwaway vault**

```powershell
.venv\Scripts\python -m tesseract_mcp.provision $env:TEMP\styling-test-vault
.venv\Scripts\python -m tesseract_mcp.provision $env:TEMP\styling-test-vault --check
```

Expected: all four plugins install (dirs under `.obsidian/plugins/<id>/` containing `main.js` + `manifest.json`), check reports `ok` for each. Then run the repo test suite: `.venv\Scripts\python -m pytest -q` — no regressions.

- [ ] **Step 5: Commit**

```powershell
git add vault-template/plugins.json
git commit -m "feat(provision): pin Extended Graph, Graph Styler, Translucent BG, Style Settings"
```

---

### Task 2: Enable-list and settings templates

**Files:**
- Modify: whatever `vault-template/` file enables plugins (locate: `grep -rl community-plugins vault-template/ src/tesseract_mcp/provision.py src/tesseract_mcp/conventions.py`) — follow the existing pattern for enabling pinned plugins.

**Interfaces:**
- Consumes: Task 1's plugin ids.
- Produces: provisioned vaults come up with the four plugins enabled (subject to the user disabling Restricted Mode manually, as today). No plugin-settings templates are shipped in v1 — Extended Graph and Graph Styler both write their own defaults on first launch, and prescribing settings blind (without seeing them in Obsidian) violates the absent-only philosophy. Settings become template candidates AFTER Task 4's visual tuning, as a follow-up.

- [ ] **Step 1: Add the four ids to the enable mechanism** (same file/pattern the existing pinned plugins use).

- [ ] **Step 2: Re-provision the throwaway vault and verify**

```powershell
.venv\Scripts\python -m tesseract_mcp.provision $env:TEMP\styling-test-vault --check
```

Expected: check passes; the enabled-plugins file in the throwaway vault lists the four ids. Clean up: `Remove-Item -Recurse -Force $env:TEMP\styling-test-vault`.

- [ ] **Step 3: Commit**

```powershell
git add vault-template/
git commit -m "feat(provision): enable styling plugins in provisioned vaults"
```

---

### Task 3: Apply to the live vault (MAIN SESSION ONLY — user confirmations)

**Files:**
- Live vault: `C:\Vaults\Tesseract\.obsidian\` (backup + provision)

- [ ] **Step 1: State the LiveSync propagation notice and get explicit user acknowledgment**

Tell the user: applying styling to the live vault may replicate `.obsidian/` changes to every machine via LiveSync. Wait for their go-ahead. Do not proceed without it.

- [ ] **Step 2: Back up graph.json and the plugin state**

```powershell
Copy-Item C:\Vaults\Tesseract\.obsidian\graph.json `
  C:\Vaults\Tesseract\.obsidian\graph.json.bak-2026-07-09 -ErrorAction Stop
if (-not (Test-Path C:\Vaults\Tesseract\.obsidian\graph.json.bak-2026-07-09)) {
  throw "graph.json backup was not created — STOP, do not proceed"
}
Copy-Item C:\Vaults\Tesseract\.obsidian\community-plugins.json `
  C:\Vaults\Tesseract\.obsidian\community-plugins.json.bak-2026-07-09 -ErrorAction Stop
```

Both backups must exist before any provisioning or graph edits. If either copy fails, abort the procedure.

- [ ] **Step 3: Run the provisioner against the live vault**

```powershell
.venv\Scripts\python -m tesseract_mcp.provision C:\Vaults\Tesseract --check   # review what would change FIRST
.venv\Scripts\python -m tesseract_mcp.provision C:\Vaults\Tesseract
```

The provisioner is absent-only for settings, so existing config is safe; the four plugins install into `.obsidian/plugins/`.

- [ ] **Step 4: Apply the graph quick-wins to `C:\Vaults\Tesseract\.obsidian\graph.json`**

Edit the `colorGroups` array (backup already taken in Step 2). Target state — order matters, first match wins:

```json
"colorGroups": [
  { "query": "path:\"Claude/Graph\"", "color": { "a": 1, "rgb": 9133302 } },
  { "query": "path:\"Claude\"",        "color": { "a": 1, "rgb": 11167205 } },
  { "query": "path:\"Job Search\"",    "color": { "a": 1, "rgb": 16096803 } },
  { "query": "path:\"04 - Customer Discovery\"", "color": { "a": 1, "rgb": 1356457 } },
  { "query": "path:\"03 - Parallax-Hermes Initiative\"", "color": { "a": 1, "rgb": 3900150 } },
  { "query": "path:\"02 - Space (Primary)\"", "color": { "a": 1, "rgb": 14221931 } }
]
```

(rgb ints, each verified as `int("RRGGBB", 16)`: violet #8B5CF6=9133302, light-violet #AA65E5=11167205, amber #F59E23=16096803, teal #14B2A9=1356457, blue #3B82F6=3900150, magenta #D9026B=14221931. Preserve every existing key in graph.json other than `colorGroups`; also set `"showOrphans": false`.)

NOTE: match the existing `03 - Parallax-Hermes…` / `02 - Space…` folder names EXACTLY as they appear in the vault (list top-level dirs first; the sidebar truncated them).

- [ ] **Step 5: Verify in Obsidian (user's desktop)**

Open Obsidian → graph view: six distinctly-hued groups, `Claude/Graph` violet, orphans hidden. Enable the four new plugins if Restricted Mode prompts. Confirm graph.json was not reset by Extended Graph's first launch (if reset: restore from `.bak` and re-apply Step 4 after the plugin has initialized).

---

### Task 4: Visual tuning + screenshot handoff

- [ ] **Step 1: With the user, pick a Graph Styler preset** (or hand-tune Extended Graph) until the graph looks right. Adjust Translucent BG tint/opacity via Style Settings.

- [ ] **Step 2: Record final state** — if plugin settings emerged that should ship to future vaults, capture them as a settings-template follow-up task (do not silently add templates now).

- [ ] **Step 3: Hand off to the documentation plan's Task 3** (`docs/superpowers/plans/2026-07-09-readme-architecture-docs.md`) — the hero screenshot is taken now, with the styled graph, under that plan's redaction rules and human review gate.

- [ ] **Step 4: Commit anything repo-side that changed** (template tweaks only; the live vault is not in this git repo).

```powershell
git add vault-template/
git commit -m "feat(provision): styling follow-ups from live-vault tuning"
```
