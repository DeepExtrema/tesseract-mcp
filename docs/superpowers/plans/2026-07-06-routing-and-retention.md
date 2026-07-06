# Routing Rules + Retention Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Level-1 routing layer (vault-root `CLAUDE.md`/`AGENTS.md` agent guides), a retention rule in the constitution, and an append-only `Claude/Decisions.md` log — all shipped through the idempotent conventions installer.

**Architecture:** One new repo template (`vault/root-guide.md`) is copied by `scripts/install_conventions.py` to the vault root under two names (`CLAUDE.md` for Claude Code/Claudian auto-load, `AGENTS.md` for Codex). The constitution gains a `## Retention` section and a `Decisions.md` structure bullet. The installer also seeds `Claude/Decisions.md`. Everything stays install-if-missing (idempotent, never overwrites).

**Tech Stack:** Python 3.11 stdlib only; pytest. Repo: `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp` (branch `codex/architecture-roadmap`, suite currently **128 passed**; run everything with `.venv\Scripts\python`).

**Spec:** `docs/superpowers/specs/2026-07-06-routing-and-retention-design.md`

---

## File structure

```
vault/
├── constitution.md        # MODIFY: Retention section + Decisions.md bullet
└── root-guide.md          # CREATE: template for vault-root CLAUDE.md/AGENTS.md
scripts/
└── install_conventions.py # MODIFY: install root guides + Decisions.md seed
tests/
└── test_install_conventions.py  # MODIFY: counts 5→8, new assertions
```

Current `scripts/install_conventions.py` (for reference — the whole file today):

```python
"""Install the Claude/ conventions tree into an Obsidian vault.

Usage: python scripts/install_conventions.py C:\\Vaults\\Tesseract
Idempotent: never overwrites anything that already exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONSTITUTION = REPO_ROOT / "vault" / "constitution.md"

INDEX_SEED = "# Index\n\nMap of agent-written notes. Session entries append below.\n\n"


def install(vault_root: Path) -> list[str]:
    """Create missing pieces of the Claude/ tree. Returns what was created."""
    vault_root = Path(vault_root)
    claude = vault_root / "Claude"
    created: list[str] = []

    for folder in (claude / "Inbox", claude / "Sessions", claude / "Concepts"):
        if not folder.is_dir():
            folder.mkdir(parents=True)
            created.append(str(folder.relative_to(vault_root)))

    readme = claude / "README.md"
    if not readme.exists():
        readme.write_text(
            CONSTITUTION.read_text(encoding="utf-8"), encoding="utf-8"
        )
        created.append("Claude/README.md")

    index = claude / "Index.md"
    if not index.exists():
        index.write_text(INDEX_SEED, encoding="utf-8")
        created.append("Claude/Index.md")

    return created


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/install_conventions.py <vault-path>")
    root = Path(sys.argv[1])
    if not root.is_dir():
        sys.exit(f"Vault not found: {root}")
    made = install(root)
    print("Created:", ", ".join(made) if made else "nothing (already installed)")
```

---

### Task R1: Root-guide template + installer + tests

**Files:**
- Create: `vault/root-guide.md`
- Modify: `scripts/install_conventions.py`
- Modify: `tests/test_install_conventions.py`

- [ ] **Step 1: Create `vault/root-guide.md`** (exact content):

```markdown
# Tesseract Vault — Agent Guide

This vault is the shared mind database for Taimoor and his AI agents
(Claude Code, Claudian, Codex, and future agents). Markdown is the source of
truth; Self-hosted LiveSync replicates it across machines.

## Read this first

- The rules for HOW agents write here live in [[Claude/README]] — the
  constitution. Read it before writing anything.
- Everything OUTSIDE `Claude/` belongs to Taimoor: read freely, write only
  when explicitly asked.

## Routing rules — where things live

- `Claude/Sessions/` — agent work logs (what was done, learned, decided).
- `Claude/Concepts/` — evergreen topic notes; extend, don't duplicate.
- `Claude/Inbox/` — quick transient captures (prunable).
- `Claude/Tasks.md` — actionable follow-ups (Obsidian Tasks checkboxes).
- `Claude/Decisions.md` — append-only decision log.
- `Claude/Graph/` — the semantic entity graph (People, Organizations,
  Domains, Topics, Projects, Sources). Query it before manual exploration.

<!-- Add one routing line per new top-level content folder as it lands, e.g.:
- `Interviews/` — user-conducted interviews (read-only for agents)
- `Resources/` — reference material imported from Notion
-->

## Tools

When the tesseract MCP is available, prefer its tools over raw file access:
`search_brain` (full-text), `query_notes` (frontmatter/Dataview-style),
`find_entity` / `related_notes` / `graph_stats` (semantic graph),
`log_session`, `capture`, `upsert_concept`, `add_task`, `write_note`.
```

- [ ] **Step 2: Add failing tests to `tests/test_install_conventions.py`**

Replace `test_installs_structure` with (count 5 → 8, three new assertions) and add the two new tests below it:

```python
def test_installs_structure(tmp_path):
    created = install(tmp_path)
    assert (tmp_path / "Claude" / "README.md").is_file()
    assert (tmp_path / "Claude" / "Inbox").is_dir()
    assert (tmp_path / "Claude" / "Sessions").is_dir()
    assert (tmp_path / "Claude" / "Concepts").is_dir()
    assert (tmp_path / "Claude" / "Index.md").is_file()
    assert (tmp_path / "Claude" / "Decisions.md").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    assert "Constitution" in (tmp_path / "Claude" / "README.md").read_text(
        encoding="utf-8"
    )
    assert len(created) == 8


def test_root_guides_identical_and_routed(tmp_path):
    install(tmp_path)
    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    agents_md = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert claude_md == agents_md
    assert "Routing rules" in claude_md
    assert "Claude/README" in claude_md  # points at the constitution
    assert "search_brain" in claude_md   # tool guidance present


def test_decisions_seed_is_append_only_log(tmp_path):
    install(tmp_path)
    body = (tmp_path / "Claude" / "Decisions.md").read_text(encoding="utf-8")
    assert body.startswith("---\n")       # frontmatter
    assert "# Decisions" in body
    assert "append" in body.lower()
```

The existing `test_idempotent_does_not_clobber` stays byte-identical — it must still pass (second `install()` returns `[]`, including for the three new artifacts).

- [ ] **Step 3: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/test_install_conventions.py -v`
Expected: `test_installs_structure` FAILS (`len(created) == 8` — actual 5; `CLAUDE.md` missing); both new tests FAIL (files missing).

- [ ] **Step 4: Update `scripts/install_conventions.py`**

Add after the `CONSTITUTION` constant:

```python
ROOT_GUIDE = REPO_ROOT / "vault" / "root-guide.md"

DECISIONS_SEED = (
    "---\ncreated: 2026-07-06\nagent: claude\ntags: [decisions]\n---\n\n"
    "# Decisions\n\n"
    "Append-only log. One line per decision:\n"
    "`- YYYY-MM-DD — <decision> ([[session note]])`\n\n"
)
```

In `install()`, after the `index` block and before `return created`, add:

```python
    decisions = claude / "Decisions.md"
    if not decisions.exists():
        decisions.write_text(DECISIONS_SEED, encoding="utf-8")
        created.append("Claude/Decisions.md")

    guide_text = ROOT_GUIDE.read_text(encoding="utf-8")
    for name in ("CLAUDE.md", "AGENTS.md"):
        guide = vault_root / name
        if not guide.exists():
            guide.write_text(guide_text, encoding="utf-8")
            created.append(name)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_install_conventions.py -v`
Expected: 4 PASS (updated + 2 new + untouched idempotency test). Full suite: `.venv\Scripts\python -m pytest -q` → **131 passed** (128 + 3 net-new: two new tests plus... verify the actual count — 128 baseline, `test_installs_structure` modified in place, 2 tests added → expect **130 passed**; report actual).

- [ ] **Step 6: Commit**

```powershell
git add vault/root-guide.md scripts/install_conventions.py tests/test_install_conventions.py
git commit -m "feat: vault-root agent guides and decision log via installer"
```
Trailer: `Co-Authored-By: <the implementing model's standard trailer>`

---

### Task R2: Constitution — Retention section + Decisions bullet

**Files:**
- Modify: `vault/constitution.md`

- [ ] **Step 1: Add the Decisions bullet to `## Structure`**

Insert AFTER the `Claude/Tasks.md` bullet (before the `Claude/Graph/` bullet):

```markdown
- `Claude/Decisions.md` — append-only decision log, one line per decision:
  `- YYYY-MM-DD — <decision> ([[session note]])`. Append here whenever a
  session makes a real decision (and still narrate it in the session note).
```

- [ ] **Step 2: Add a `## Retention` section**

Insert AFTER the `## Structure` section (before `## Note format`):

```markdown
## Retention

- Two kinds of memory: **context** (evergreen — Sessions, Concepts,
  Decisions) and **connections** (transient — Inbox captures, passing
  references). Keep context; let connections expire.
- The test before promoting anything to a Concept or Decision: *will having
  this memory still be useful in a year?* If not, it is noise — leave it in
  the Inbox.
- `Claude/Inbox/` is prunable at any time. Graduate anything worth keeping
  into Concepts, Tasks, or Decisions before pruning.
```

- [ ] **Step 3: Verify nothing broke**

Run: `.venv\Scripts\python -m pytest -q`
Expected: same count as after R1 (the installer test only checks for the substring "Constitution"). Also verify the constitution still parses as YAML frontmatter + markdown:
`.venv\Scripts\python -c "from tesseract_mcp.search import parse_frontmatter; t=open('vault/constitution.md',encoding='utf-8').read(); m=parse_frontmatter(t); print('fm ok', m.get('tags'))"`
Expected: `fm ok ['meta', 'constitution']`

- [ ] **Step 4: Commit**

```powershell
git add vault/constitution.md
git commit -m "docs: retention policy and decision-log rules in constitution"
```

---

### Task R3: Live application to the real vault (controller/human-supervised)

No subagent required — these are deliberate writes to the live vault at `C:\Vaults\Tesseract`.

- [ ] Run the installer against the real vault:
  `.venv\Scripts\python scripts/install_conventions.py C:\Vaults\Tesseract`
  Expected output: `Created: Claude/Decisions.md, CLAUDE.md, AGENTS.md` (the rest already exists).
- [ ] Sync the updated constitution (the installer never overwrites, so this is a deliberate copy):
  `Copy-Item vault/constitution.md C:\Vaults\Tesseract\Claude\README.md -Force`
- [ ] Seed the first real decision entries into `C:\Vaults\Tesseract\Claude\Decisions.md` (append, preserving the seed header):
  ```
  - 2026-07-05 — Tesseract vault is the shared mind database; synced via LiveSync/CouchDB on Oracle Free VM ([[2026-07-05 Mind database setup]])
  - 2026-07-05 — Agent writes quarantined to Claude/ (enforced in code)
  - 2026-07-06 — Semantic graph: LLM extraction via codex CLI, markdown-native entities, no embeddings
  - 2026-07-06 — MindNexus rename DEFERRED until after Oracle deploy; Codex owns architecture docs, Claude Code owns MCP engine
  - 2026-07-06 — Removed redundant memory MCPs (ruflo suite, claude-subconscious, goodmem disabled)
  ```
- [ ] Verify Obsidian shows the root CLAUDE.md/AGENTS.md and Decisions.md; verify `search_brain("routing rules")` finds the root guide.
- [ ] Log a session note (`log_session`) recording this upgrade so Codex reads current truth from the vault.

---

## Self-review notes

- Spec coverage: root guides both names (R1), retention section (R2), decisions log seeded + constitution bullet (R1+R2), installer idempotent counts + tests (R1), live application + first entries (R3). Out-of-scope items untouched. No gaps.
- Placeholder scan: clean; all content verbatim.
- Consistency: `install()` return strings use vault-relative names matching existing style (`Claude/...`, root names bare). Test count expectation flagged inline for the implementer to report actual (128 baseline + 2 new = 130).
- Note for implementer: the repo's commit-trailer convention follows whichever model implements; earlier commits used the Claude Fable 5 trailer — use your own standard trailer.
