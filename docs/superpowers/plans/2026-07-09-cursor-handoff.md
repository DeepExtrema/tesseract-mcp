# Cursor Handoff: MCP Server Bundle + Graph Styling Implementation Plan

> **For the Cursor agent:** This is your top-level work order. You have zero
> prior context; everything you need is in this file and the two plan files
> it directs you to. Execute tasks in the exact order given, checking off
> (`- [ ]`) steps as you complete them. Do NOT improvise beyond the plans.

**Goal:** Implement (1) the curated MCP server manifest + additive sync tool and (2) the repo-side half of the graph styling work, exactly as specified in the two committed plan documents, leaving the human-in-the-loop tasks untouched.

**Architecture:** Two pre-written, fully-detailed plans live in this repo. This document is the orchestration layer: environment facts, execution order, hard rules, and stop-points. Every code block, test, command, and commit message you need is already in those plans — follow them verbatim.

**Tech Stack:** Python 3.11 (stdlib-only for new code), pytest, existing `tesseract_mcp` package idioms, Obsidian community-plugin pinning via `vault-template/plugins.json`.

## Environment briefing (read once, trust it)

- **Machine:** Windows 11. Shell commands in the plans are PowerShell unless shown otherwise.
- **Repo root:** `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`
- **Python:** project venv at `.venv\` — always `.venv\Scripts\python`, never system python. Run tests as `.venv\Scripts\python -m pytest -q` (pytest config in `pyproject.toml`, `pythonpath=["src"]`).
- **Package layout:** `src/tesseract_mcp/` — read `provision.py` and `organize.py` before writing new module code; new code must match their idioms (module docstring stating the contract, pure functions, argparse `main(argv)` + `if __name__ == "__main__"` guard, `--check`-style dry modes).
- **Base branch:** `codex/architecture-roadmap` (also the repo default). Do all work on a new branch `feat/mcp-sync-and-styling-pins` created from it:
  `git checkout -b feat/mcp-sync-and-styling-pins codex/architecture-roadmap`
- **Commits:** use the exact commit messages the plans specify, one commit per plan-task as instructed. Do not squash. Do not push unless the human asks.
- **Do not touch:** `.gitignore` (has uncommitted user changes), `.cursor/`, `vault/`, anything under `.worktrees/`, and the user's live vault at `C:\Vaults\Tesseract` (that path appears in plan tasks you are NOT executing).

## Hard rules (violations = stop and ask the human)

1. **Additive-only invariant:** the sync tool must NEVER modify or remove an existing entry in `~/.claude.json`. The plans encode this in tests — if a test seems to force you to violate it, the test is right and your implementation is wrong.
2. **Never edit `~/.claude.json` directly** — not in code (the tool shells out to `claude mcp add`), not by hand.
3. **Network steps** (PyPI version lookup, `uvx ... --help`, GitHub release/manifest fetches) are expected and allowed; if one fails, retry once, then record the failure in your report and continue with what's blocked marked BLOCKED — do not invent versions or pins.
4. **Stop-points below are absolute.** They exist because those steps change the user's machine state (registered MCP servers, live Obsidian vault) and need the human present.

---

### Task 1: Execute the MCP bundle plan, Tasks 1–3

**Files:** as specified in `docs/superpowers/plans/2026-07-09-mcp-server-bundle.md`
(creates `mcp-servers.json`, `src/tesseract_mcp/mcp_sync.py`, `tests/test_mcp_sync.py`)

**Interfaces:**
- Consumes: nothing (fresh module).
- Produces: `ServerSpec`, `load_manifest`, `resolve`, `read_config`, `classify`, `Classification`, `build_add_command`, `run_sync` — exact signatures are in the plan's per-task Interfaces blocks.

- [ ] **Step 1:** Open `docs/superpowers/plans/2026-07-09-mcp-server-bundle.md`. Read its Global Constraints section fully.
- [ ] **Step 2:** Execute its **Task 1** exactly (manifest + loader + placeholder resolution): write the failing tests, run to see them fail, implement, run to green, commit with the given message.
- [ ] **Step 3:** Execute its **Task 2** exactly (config reading + classification).
- [ ] **Step 4:** Execute its **Task 3** exactly (registration commands + sync orchestration + additive-only invariant tests).
- [ ] **Step 5:** Run the full suite: `.venv\Scripts\python -m pytest -q`. Expected: all tests pass, zero regressions. If any pre-existing test breaks, stop and report — do not "fix" unrelated tests.

### Task 2: Execute the MCP bundle plan, Task 4 — EXCEPT the live run

**Interfaces:**
- Consumes: `run_sync` from Task 1.
- Produces: `main(argv: list[str] | None = None) -> int` in `mcp_sync.py`; real version pins in `mcp-servers.json`; updated `README.md` quickstart + `docs/ARCHITECTURE.md` module map.

- [ ] **Step 1:** Execute bundle-plan **Task 4 Steps 1–5**: verify the real `arxiv-mcp-server` pin from PyPI (replace `PIN_ME`), verify both pinned packages launch via `uvx ... --help`, add the CLI test + `main()`, full suite green.
- [ ] **Step 2:** Execute bundle-plan **Task 4 Step 7** (README + ARCHITECTURE doc updates as written).
- [ ] **Step 3:** Run `.venv\Scripts\python -m tesseract_mcp.mcp_sync --check` and record its output in your report. Expected: `tesseract` present-or-DRIFTED, `fetch` MISSING, `arxiv` MISSING, exit code 1. **Do NOT run the tool without `--check`.**
- [ ] **Step 4:** Commit per bundle-plan Task 4 Step 8's message.

**STOP-POINT A: do NOT execute bundle-plan Task 4 Step 6 (the live `mcp_sync` run that registers servers into the user's Claude Code config). That step is reserved for the human.**

### Task 3: Execute the graph styling plan, Tasks 1–2 only

**Files:** as specified in `docs/superpowers/plans/2026-07-09-graph-styling.md`
(modifies `vault-template/plugins.json` and the plugin-enable mechanism)

**Interfaces:**
- Consumes: existing provisioner (`tesseract_mcp.provision`) — read how current pinned plugins are declared and enabled BEFORE editing; copy the schema exactly.
- Produces: four new pinned + enabled plugins (`extended-graph`, `graph-styler`, the Translucent BG plugin id resolved from the community registry, `obsidian-style-settings` if not already pinned).

- [ ] **Step 1:** Open `docs/superpowers/plans/2026-07-09-graph-styling.md`. Read its Global Constraints.
- [ ] **Step 2:** Execute its **Task 1** exactly (resolve exact plugin ids/repos/release tags from GitHub + the community registry, add pins, verify by provisioning a throwaway vault under `$env:TEMP`, full test suite green, commit).
- [ ] **Step 3:** Execute its **Task 2** exactly (enable-list, re-provision throwaway vault, cleanup, commit).

**STOP-POINT B: do NOT execute styling-plan Tasks 3–4 (live vault `C:\Vaults\Tesseract` — backup, provision, graph.json color groups, visual tuning). Those require the human and their Claude session.**

### Task 4: Final verification and report

- [ ] **Step 1:** `.venv\Scripts\python -m pytest -q` — everything green.
- [ ] **Step 2:** `git log --oneline codex/architecture-roadmap..HEAD` — expect roughly 7 commits matching the plans' messages (4 bundle + 2 styling + any doc commit).
- [ ] **Step 3:** Write a completion report as the final message, containing: per-task outcome (done / blocked+why), the recorded `--check` output from Task 2 Step 3, the resolved plugin pins (id@tag for all four), any deviations, and the two stop-points confirmed untouched.

---

## Definition of done (for the human reviewing Cursor's work)

- `mcp-servers.json` exists with three fully-pinned servers (no `PIN_ME`).
- `python -m tesseract_mcp.mcp_sync --check` runs and correctly classifies; nothing was registered (config untouched).
- `tests/test_mcp_sync.py` has ~16 tests, all passing; whole suite green.
- Four styling plugins pinned + enabled in `vault-template/`, proven by a throwaway-vault provision.
- README quickstart uses `mcp_sync`; ARCHITECTURE module map has the `mcp_sync.py` row.
- Live machine state unchanged: no new servers in `claude mcp list`, live vault untouched.

## What remains after Cursor finishes (human + Claude session)

1. Bundle-plan Task 4 Step 6 — live sync run registering `fetch` + `arxiv` (Stop-point A).
2. Styling-plan Tasks 3–4 — live-vault apply with `graph.json` backup, LiveSync acknowledgment, color quick-wins, visual tuning (Stop-point B).
3. Merge `feat/mcp-sync-and-styling-pins` back into `codex/architecture-roadmap`.
