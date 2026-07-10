# Recall Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the human-facing recall loop over the Tesseract vault — one read-only `recall_bundle` MCP tool, four Claude Code skills (`/recall`, `/digest`, `/resume`, `/connections`), vault conventions (`Claude/Answers/`, `Claude/Digests/`), and an additive skill installer.

**Architecture:** Skills own all LLM composition and rendering; the server gains exactly one deterministic tool (`recall_bundle` in a new `recall.py` module) that packages digest/resume raw material with per-section degradation. Answers and digests are filed inside the `Claude/` quarantine so the existing Librarian indexes them — past answers become retrieval sources (the compounding loop). Spec: `docs/superpowers/specs/2026-07-10-recall-harness-design.md`.

**Tech Stack:** Python 3 (existing package layout under `src/tesseract_mcp/`), FastMCP, pytest, plain-markdown Claude Code skills.

## Global Constraints

- Windows repo at `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`; branch `codex/architecture-roadmap`.
- Run Python as `.venv\Scripts\python`; tests as `.venv\Scripts\python -m pytest -q` (PowerShell).
- Tests must never touch the live vault (`C:\Vaults\Tesseract`), the real `~/.tesseract-mcp`, or the real `~/.claude/skills`. `tests/conftest.py` already isolates `TESSERACT_STATE_DIR`; skill-sync tests must pass explicit tmp `src`/`dest`.
- All agent vault writes land under `Claude/` only; nothing in this plan touches the `confirm_outside_claude` path.
- `skill_sync` is additive by default (never modifies existing entries without `--force`) — same philosophy as `mcp_sync`. Agents run it only with `--check`; a real sync to `~/.claude/skills` requires explicit user consent (AGENTS.md rule).
- Commit style: `type(scope): message` (see `git log`).
- New MCP tool docstrings follow the existing style: purpose first sentence, behavioral details after.
- Exact spec values: digest default window **7 days**; `since` format **YYYY-MM-DD**; answer notes `Claude/Answers/YYYY-MM-DD <question slug>.md` with frontmatter `type: answer` + `question:`; digest notes `Claude/Digests/YYYY-MM-DD.md` with `type: digest`; filing rule "file what compounds, skip what expires".

---

### Task 1: `recall.py` — digest bundle

**Files:**
- Create: `src/tesseract_mcp/recall.py`
- Test: `tests/test_recall.py`

**Interfaces:**
- Consumes: `graph._vault_files(vault, folder=None)` (yields `(Path, rel_str)`), `librarian.status(vault) -> dict`, `tasks.list_tasks(vault, include_done=False, folder=None) -> list[dict]` with keys `path`/`text`/`done`, `Vault` from `.vault`.
- Produces: `digest_bundle(vault: Vault, since: str | None = None, now: datetime | None = None) -> dict` with keys `mode`, `generated`, `since`, and sections `librarian`, `recent_notes`, `inbox_captures`, `tasks`, `proposals`, `new_entities` — every section a dict carrying `status: "ok" | "error"`. Also `_section(fn) -> dict` and `_notes_since(vault, cutoff, folder=None) -> list[dict]` reused by Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recall.py`:

```python
"""Tests for the recall bundle module (digest + resume raw material)."""

import os
from datetime import datetime

import pytest

from tesseract_mcp import recall
from tesseract_mcp.vault import VaultError


def test_digest_sections_present(vault):
    bundle = recall.digest_bundle(vault)
    assert bundle["mode"] == "digest"
    assert set(bundle) == {
        "mode", "generated", "since", "librarian", "recent_notes",
        "inbox_captures", "tasks", "proposals", "new_entities",
    }
    for name in ("librarian", "recent_notes", "inbox_captures",
                 "tasks", "proposals", "new_entities"):
        assert bundle[name]["status"] == "ok"


def test_digest_includes_fresh_notes(vault, vault_dir):
    (vault_dir / "Claude" / "Inbox" / "2026-07-10.md").write_text(
        "- 09:00 a fresh thought\n", encoding="utf-8"
    )
    bundle = recall.digest_bundle(vault)
    paths = [n["path"] for n in bundle["inbox_captures"]["notes"]]
    assert "Claude/Inbox/2026-07-10.md" in paths


def test_digest_since_filters_old_notes(vault, vault_dir):
    old = vault_dir / "Claude" / "Inbox" / "2020-01-01.md"
    old.write_text("- 09:00 ancient thought\n", encoding="utf-8")
    stamp = datetime(2020, 1, 2).timestamp()
    os.utime(old, (stamp, stamp))
    bundle = recall.digest_bundle(vault, since="2026-01-01")
    assert bundle["since"] == "2026-01-01"
    paths = [n["path"] for n in bundle["inbox_captures"]["notes"]]
    assert "Claude/Inbox/2020-01-01.md" not in paths


def test_digest_rejects_bad_since(vault):
    with pytest.raises(VaultError, match="YYYY-MM-DD"):
        recall.digest_bundle(vault, since="last tuesday")


def test_digest_tasks_split_open_and_recently_done(vault, vault_dir):
    (vault_dir / "Claude" / "Tasks.md").write_text(
        "# Tasks\n\n- [ ] open item\n- [x] finished item\n", encoding="utf-8"
    )
    bundle = recall.digest_bundle(vault)
    tasks = bundle["tasks"]
    assert [t["text"] for t in tasks["open"]] == ["open item"]
    # Tasks.md was just written, so it counts as changed since the cutoff
    assert [t["text"] for t in tasks["done_recently"]] == ["finished item"]


def test_digest_section_degrades_without_killing_bundle(vault, monkeypatch):
    def boom(v):
        raise RuntimeError("state file exploded")

    monkeypatch.setattr(recall.librarian_mod, "status", boom)
    bundle = recall.digest_bundle(vault)
    assert bundle["librarian"]["status"] == "error"
    assert "RuntimeError" in bundle["librarian"]["error"]
    assert bundle["recent_notes"]["status"] == "ok"


def test_digest_proposals_default_zero_without_sweep(vault):
    bundle = recall.digest_bundle(vault)
    assert bundle["proposals"]["pending"] == 0
    assert bundle["proposals"]["detail_note"] == "Claude/Organizer.md"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_recall.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tesseract_mcp.recall'` (or ImportError at collection).

- [ ] **Step 3: Write the implementation**

Create `src/tesseract_mcp/recall.py`:

```python
"""Read-only bundle composition for the recall harness (digest/resume).

Deterministic packaging of what the /digest and /resume skills render —
no LLM calls, no writes. Each section degrades independently: a failure
becomes {"status": "error", ...} instead of killing the bundle.
Spec: docs/superpowers/specs/2026-07-10-recall-harness-design.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import librarian as librarian_mod
from . import tasks as tasks_mod
from .graph import _vault_files
from .vault import Vault, VaultError

DIGEST_DEFAULT_DAYS = 7
TS_FMT = "%Y-%m-%d %H:%M"
ORGANIZER_NOTE = "Claude/Organizer.md"


def _section(fn) -> dict:
    """Run one bundle section; failures degrade to a status payload."""
    try:
        out = fn()
        out["status"] = "ok"
        return out
    except Exception as e:  # noqa: BLE001 — a section must not kill the bundle
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


def _notes_since(
    vault: Vault, cutoff: datetime, folder: str | None = None
) -> list[dict]:
    out = []
    for path, rel in _vault_files(vault, folder):
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if mtime >= cutoff:
            out.append({"path": rel, "modified": mtime.strftime(TS_FMT)})
    out.sort(key=lambda n: n["modified"], reverse=True)
    return out


def digest_bundle(
    vault: Vault, since: str | None = None, now: datetime | None = None
) -> dict:
    now = now or datetime.now()
    if since:
        try:
            cutoff = datetime.strptime(since, "%Y-%m-%d")
        except ValueError as e:
            raise VaultError(f"since must be YYYY-MM-DD: {since!r}") from e
    else:
        cutoff = now - timedelta(days=DIGEST_DEFAULT_DAYS)

    def _tasks() -> dict:
        all_tasks = tasks_mod.list_tasks(vault, include_done=True)
        changed = {n["path"] for n in _notes_since(vault, cutoff)}
        return {
            "open": [t for t in all_tasks if not t["done"]],
            "done_recently": [
                t for t in all_tasks if t["done"] and t["path"] in changed
            ],
        }

    def _proposals() -> dict:
        state = librarian_mod.status(vault)
        pending = (state.get("health") or {}).get("pending_proposals", 0)
        return {"pending": pending, "detail_note": ORGANIZER_NOTE}

    return {
        "mode": "digest",
        "generated": now.strftime(TS_FMT),
        "since": cutoff.strftime("%Y-%m-%d"),
        "librarian": _section(lambda: {"report": librarian_mod.status(vault)}),
        "recent_notes": _section(lambda: {"notes": _notes_since(vault, cutoff)}),
        "inbox_captures": _section(
            lambda: {"notes": _notes_since(vault, cutoff, folder="Claude/Inbox")}
        ),
        "tasks": _section(_tasks),
        "proposals": _section(_proposals),
        "new_entities": _section(
            lambda: {"notes": _notes_since(vault, cutoff, folder="Claude/Graph")}
        ),
    }
```

Notes for the implementer:
- `_vault_files` is package-internal reuse (same pattern as `cache.py` importing `parse_frontmatter` from `search.py`); it already applies `SKIP_DIRS` so dotfolders are excluded.
- `_vault_files(vault, "Claude/Inbox")` on a missing folder yields nothing (pathlib glob swallows the missing dir) — no special-casing needed.
- `librarian.status` returns `{"status": "no sweep yet"}` when no sweep has run; `_proposals` handles that because `state.get("health")` is `None` → `{}` → `pending` defaults to 0.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_recall.py -q`
Expected: 7 passed.

- [ ] **Step 5: Run the whole suite (no regressions)**

Run: `.venv\Scripts\python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/tesseract_mcp/recall.py tests/test_recall.py
git commit -m "feat(recall): digest bundle - deterministic review raw material"
```

---

### Task 2: `recall.py` — resume bundle

**Files:**
- Modify: `src/tesseract_mcp/recall.py` (append functions)
- Test: `tests/test_recall.py` (append tests)

**Interfaces:**
- Consumes: `_section`/`_notes_since`/`_vault_files`/`TS_FMT` from Task 1, `search.parse_frontmatter(text) -> dict`, `cache.find_entity(db_path, query, type=None) -> list[dict]` (keys `name`,`type`,`path`,`summary`,`aliases`,`relations`,`mention_count`), `indexer.db_path(vault.root) -> Path`, `tasks.list_tasks`.
- Produces: `resume_bundle(vault: Vault, project: str, limit: int = 10) -> dict` with keys `mode`, `project` and sections `sessions`, `decisions`, `tasks`, `entities` (each with `status`). Session entries: `{"path", "created", "excerpt"}` sorted newest first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recall.py`:

```python
def _write_session(vault_dir, name, project, created, body):
    (vault_dir / "Claude" / "Sessions" / name).write_text(
        f"---\ncreated: {created}\nagent: claude\n"
        f"project: {project}\ntags: []\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_resume_matches_project_substring_newest_first(vault, vault_dir):
    _write_session(vault_dir, "2026-07-01 Graph work.md",
                   "tesseract-mcp", "2026-07-01 10:00", "Built the graph.")
    _write_session(vault_dir, "2026-07-09 Evals.md",
                   "tesseract-mcp", "2026-07-09 10:00", "Shipped evals.")
    _write_session(vault_dir, "2026-07-05 Other.md",
                   "sentinel", "2026-07-05 10:00", "Unrelated work.")
    bundle = recall.resume_bundle(vault, "tesseract")
    assert bundle["mode"] == "resume"
    assert bundle["project"] == "tesseract"
    notes = bundle["sessions"]["notes"]
    assert [n["path"] for n in notes] == [
        "Claude/Sessions/2026-07-09 Evals.md",
        "Claude/Sessions/2026-07-01 Graph work.md",
    ]
    assert "Shipped evals." in notes[0]["excerpt"]
    assert "---" not in notes[0]["excerpt"]  # frontmatter stripped


def test_resume_respects_limit(vault, vault_dir):
    for day in range(1, 5):
        _write_session(vault_dir, f"2026-07-0{day} S{day}.md",
                       "tesseract", f"2026-07-0{day} 10:00", f"Work {day}.")
    bundle = recall.resume_bundle(vault, "tesseract", limit=2)
    assert len(bundle["sessions"]["notes"]) == 2


def test_resume_decisions_and_tasks_filter_by_project(vault, vault_dir):
    (vault_dir / "Claude" / "Decisions.md").write_text(
        "# Decisions\n\n"
        "- 2026-07-08 — hybrid search ships in tesseract ([[x]])\n"
        "- 2026-07-09 — sentinel retired\n",
        encoding="utf-8",
    )
    (vault_dir / "Claude" / "Tasks.md").write_text(
        "# Tasks\n\n- [ ] tune tesseract eval gate\n"
        "- [ ] water plants\n- [x] tesseract done thing\n",
        encoding="utf-8",
    )
    bundle = recall.resume_bundle(vault, "Tesseract")  # case-insensitive
    assert bundle["decisions"]["lines"] == [
        "- 2026-07-08 — hybrid search ships in tesseract ([[x]])"
    ]
    assert [t["text"] for t in bundle["tasks"]["tasks"]] == [
        "tune tesseract eval gate"
    ]


def test_resume_decisions_missing_file_is_empty_not_error(vault):
    bundle = recall.resume_bundle(vault, "tesseract")
    assert bundle["decisions"]["status"] == "ok"
    assert bundle["decisions"]["lines"] == []


def test_resume_entities_without_graph_cache(vault):
    bundle = recall.resume_bundle(vault, "tesseract")
    assert bundle["entities"]["status"] == "ok"
    assert bundle["entities"]["entities"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_recall.py -q`
Expected: new tests FAIL with `AttributeError: module 'tesseract_mcp.recall' has no attribute 'resume_bundle'`; Task 1 tests still pass.

- [ ] **Step 3: Write the implementation**

Append to `src/tesseract_mcp/recall.py` (and extend the imports at the top of the file to include the two new ones shown here):

```python
from .indexer import db_path
from .search import parse_frontmatter
from .cache import find_entity
```

```python
DECISIONS_NOTE = "Claude/Decisions.md"


def _body_excerpt(text: str, limit: int = 400) -> str:
    """First `limit` chars of the note body, frontmatter stripped."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return " ".join(text.split())[:limit]


def _session_notes(vault: Vault, project: str, limit: int) -> list[dict]:
    q = project.casefold()
    sessions = []
    for path, rel in _vault_files(vault, "Claude/Sessions"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        hay = f"{meta.get('project', '')} {path.stem}".casefold()
        if q not in hay:
            continue
        created = str(meta.get("created") or "")
        if not created:
            created = datetime.fromtimestamp(path.stat().st_mtime).strftime(TS_FMT)
        sessions.append(
            {"path": rel, "created": created, "excerpt": _body_excerpt(text)}
        )
    sessions.sort(key=lambda s: s["created"], reverse=True)
    return sessions[:limit]


def resume_bundle(vault: Vault, project: str, limit: int = 10) -> dict:
    q = project.casefold()

    def _decisions() -> dict:
        target = vault.resolve(DECISIONS_NOTE)
        if not target.is_file():
            return {"lines": []}
        lines = [
            ln for ln in target.read_text(encoding="utf-8").splitlines()
            if ln.startswith("- ") and q in ln.casefold()
        ]
        return {"lines": lines}

    def _open_tasks() -> dict:
        tasks = tasks_mod.list_tasks(vault)
        return {"tasks": [t for t in tasks if q in t["text"].casefold()]}

    def _entities() -> dict:
        db = db_path(vault.root)
        if not db.exists():
            return {"entities": [], "note": "graph cache not built"}
        found = find_entity(db, project)
        return {
            "entities": [
                {"name": e["name"], "type": e["type"],
                 "path": e["path"], "summary": e["summary"]}
                for e in found
            ]
        }

    return {
        "mode": "resume",
        "project": project,
        "sessions": _section(
            lambda: {"notes": _session_notes(vault, project, limit)}
        ),
        "decisions": _section(_decisions),
        "tasks": _section(_open_tasks),
        "entities": _section(_entities),
    }
```

Note: matching is deliberately a case-folded substring over frontmatter `project` AND the filename stem — "tesseract" matches sessions logged under project "tesseract-mcp". `created` values are `YYYY-MM-DD HH:MM` strings, so lexicographic sort is chronological.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_recall.py -q`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/recall.py tests/test_recall.py
git commit -m "feat(recall): resume bundle - project memory raw material"
```

---

### Task 3: `recall_bundle` MCP tool

**Files:**
- Modify: `src/tesseract_mcp/server.py` (new tool + onboard cheat-sheet line)
- Test: `tests/test_server.py` (registration set + behavior tests)

**Interfaces:**
- Consumes: `recall.digest_bundle(vault, since=None)`, `recall.resume_bundle(vault, project)` from Tasks 1–2; `get_vault()`, `VaultError` already in `server.py`.
- Produces: MCP tool `recall_bundle(mode: str, project: str | None = None, since: str | None = None) -> dict`. Skills in Tasks 6–8 call it by exactly this name and signature.

- [ ] **Step 1: Write the failing tests**

In `tests/test_server.py`, add `"recall_bundle"` to the expected set in `test_all_tools_registered` (the set literal currently ends with `"organize_vault", "undo_move", "librarian_status",` — add the new name alongside them). Then append:

```python
def test_recall_bundle_digest_via_server():
    bundle = server.recall_bundle("digest")
    assert bundle["mode"] == "digest"
    assert bundle["recent_notes"]["status"] == "ok"


def test_recall_bundle_resume_requires_project():
    with pytest.raises(VaultError, match="requires project"):
        server.recall_bundle("resume")


def test_recall_bundle_rejects_unknown_mode():
    with pytest.raises(VaultError, match="digest"):
        server.recall_bundle("weekly")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_server.py -q`
Expected: FAIL — registration set mismatch and `AttributeError: module 'tesseract_mcp.server' has no attribute 'recall_bundle'`.

- [ ] **Step 3: Write the implementation**

In `src/tesseract_mcp/server.py`:

1. Extend the package import line (line 9) to also import `recall as recall_mod`.
2. Add the tool after `librarian_status` (keeps Organize-family grouping intact):

```python
@mcp.tool()
def recall_bundle(
    mode: str, project: str | None = None, since: str | None = None
) -> dict:
    """Raw material for the recall skills in one read-only call — no LLM.
    mode='digest': everything changed since `since` (YYYY-MM-DD, default 7
    days back): recent notes, inbox captures, open/recently-done tasks,
    librarian health, pending proposals, new graph entities. mode='resume':
    sessions, decisions, open tasks, and graph entities matching `project`
    (case-insensitive substring). Sections degrade independently — a failed
    section reports {"status": "error"} instead of failing the bundle."""
    vault = get_vault()
    if mode == "digest":
        return recall_mod.digest_bundle(vault, since=since)
    if mode == "resume":
        if not project:
            raise VaultError("mode='resume' requires project")
        return recall_mod.resume_bundle(vault, project)
    raise VaultError(f"mode must be 'digest' or 'resume', got {mode!r}")
```

3. In `onboard()`, add one line to the `tools` list after the `context_bundle` entry:

```python
        "recall_bundle(mode, project?, since?) — digest/resume raw material for the recall skills",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_server.py tests/test_recall.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/server.py tests/test_server.py
git commit -m "feat(server): recall_bundle MCP tool"
```

---

### Task 4: Vault conventions — `Claude/Answers/`, `Claude/Digests/`, constitution rules

**Files:**
- Modify: `src/tesseract_mcp/conventions.py:32` (folder tuple)
- Modify: `vault/constitution.md` (Structure + Retention sections)
- Test: `tests/test_install_conventions.py` (append tests)

**Interfaces:**
- Consumes: `conventions.install(vault_root: Path) -> list[str]` (idempotent, never overwrites).
- Produces: `install()` now also creates `Claude/Answers/` and `Claude/Digests/`; the repo constitution documents both folders and the filing rule. `provision.py` inherits this automatically (it calls `conventions.install`).

- [ ] **Step 1: Write the failing tests**

`tests/test_install_conventions.py` imports the installer as
`from install_conventions import install` (a scripts-dir shim over
`tesseract_mcp.conventions`) — match that style. Append:

```python
def test_install_creates_recall_folders(tmp_path):
    install(tmp_path)
    assert (tmp_path / "Claude" / "Answers").is_dir()
    assert (tmp_path / "Claude" / "Digests").is_dir()


def test_constitution_documents_recall_conventions(tmp_path):
    install(tmp_path)
    text = (tmp_path / "Claude" / "README.md").read_text(encoding="utf-8")
    assert "Claude/Answers/" in text
    assert "Claude/Digests/" in text
    assert "file what compounds" in text.lower()
```

Also update the EXISTING `test_installs_structure`: its final assertion is
`assert len(created) == 8` — two new folders make it **10**:

```python
    assert len(created) == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_install_conventions.py -q`
Expected: the two new tests FAIL (folders not created; constitution lacks the text), and `test_installs_structure` FAILS on `len(created) == 10` until Step 3 lands.

- [ ] **Step 3: Implement — conventions.py**

In `src/tesseract_mcp/conventions.py`, change the folder loop:

```python
    for folder in (
        claude / "Inbox",
        claude / "Sessions",
        claude / "Concepts",
        claude / "Answers",
        claude / "Digests",
    ):
```

- [ ] **Step 4: Implement — constitution.md**

In `vault/constitution.md`, insert two bullets in `## Structure` immediately after the `Claude/Graph/` bullet (which ends with "...gathering context for a topic."):

```markdown
- `Claude/Answers/` — rendered answers from `/recall` queries, one note per
  question (`YYYY-MM-DD <question slug>.md`, frontmatter `type: answer` and
  `question:`). Every claim cites its source note as a `[[wikilink]]`;
  model-knowledge additions are labeled *(not from the vault)*. Past answers
  are legitimate retrieval sources — that is the point.
- `Claude/Digests/` — one review digest per run (`YYYY-MM-DD.md`, frontmatter
  `type: digest`) written by `/digest`: librarian health, captures to triage,
  tasks, recent changes, pending proposals, new graph activity, suggested
  questions. Rerunning the same day replaces that day's digest.
```

And append one bullet to the `## Retention` list (after the `Claude/Inbox/` prunable bullet):

```markdown
- Recall filing rule: **file what compounds, skip what expires.** Answers and
  digests are filed — they gain value as retrieval sources. Resume briefings
  and unblessed connection lists are not — filing expiring state teaches
  search to retrieve stale context.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_install_conventions.py tests/test_provision.py -q`
Expected: all pass (provision tests confirm no regression from the folder change).

- [ ] **Step 6: Commit**

```powershell
git add src/tesseract_mcp/conventions.py vault/constitution.md tests/test_install_conventions.py
git commit -m "feat(conventions): Claude/Answers + Claude/Digests + recall filing rules"
```

---

### Task 5: `skill_sync` — additive installer for repo skills

**Files:**
- Create: `src/tesseract_mcp/skill_sync.py`
- Test: `tests/test_skill_sync.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure stdlib: `argparse`, `json`, `shutil`, `pathlib`).
- Produces: `sync(src: Path = REPO_SKILLS, dest: Path | None = None, force: bool = False, check: bool = False) -> dict` returning `{"installed": [...], "updated": [...], "up_to_date": [...], "drift": [...]}`; CLI `python -m tesseract_mcp.skill_sync [--check] [--force] [--dest PATH]`. Tasks 6–8 verify their skill files with `--check --dest`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skill_sync.py`:

```python
"""skill_sync: additive by default, --force to update, --check writes nothing."""

from tesseract_mcp import skill_sync


def _make_skill(base, name, body="---\nname: x\ndescription: d\n---\nbody\n"):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def test_installs_missing_skill(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall")
    result = skill_sync.sync(src=src, dest=dest)
    assert result["installed"] == ["recall"]
    assert (dest / "recall" / "SKILL.md").is_file()


def test_never_touches_existing_without_force(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall", "new upstream content\n")
    _make_skill(dest, "recall", "user edited this\n")
    result = skill_sync.sync(src=src, dest=dest)
    assert result["drift"] == ["recall"]
    text = (dest / "recall" / "SKILL.md").read_text(encoding="utf-8")
    assert text == "user edited this\n"


def test_force_overwrites_drifted_skill(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall", "new upstream content\n")
    _make_skill(dest, "recall", "user edited this\n")
    result = skill_sync.sync(src=src, dest=dest, force=True)
    assert result["updated"] == ["recall"]
    text = (dest / "recall" / "SKILL.md").read_text(encoding="utf-8")
    assert text == "new upstream content\n"


def test_identical_skill_reports_up_to_date(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall", "same\n")
    _make_skill(dest, "recall", "same\n")
    result = skill_sync.sync(src=src, dest=dest)
    assert result["up_to_date"] == ["recall"]


def test_check_reports_without_writing(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall")
    result = skill_sync.sync(src=src, dest=dest, check=True)
    assert result["installed"] == ["recall"]
    assert not (dest / "recall").exists()


def test_check_plus_force_still_writes_nothing(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall", "new\n")
    _make_skill(dest, "recall", "old\n")
    result = skill_sync.sync(src=src, dest=dest, force=True, check=True)
    assert result["drift"] == ["recall"]
    assert (dest / "recall" / "SKILL.md").read_text(encoding="utf-8") == "old\n"


def test_ignores_dirs_without_skill_md(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    (src / "not-a-skill").mkdir(parents=True)
    result = skill_sync.sync(src=src, dest=dest)
    assert result == {"installed": [], "updated": [], "up_to_date": [], "drift": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_skill_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tesseract_mcp.skill_sync'`.

- [ ] **Step 3: Write the implementation**

Create `src/tesseract_mcp/skill_sync.py`:

```python
"""Sync the repo's skills/ into the personal Claude Code skills directory.

Additive by default, mirroring mcp_sync's philosophy: an existing skill is
NEVER modified unless --force. --check reports without writing (exit 1 when
anything is pending, for use as a drift probe).
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

REPO_SKILLS = Path(__file__).resolve().parent.parent.parent / "skills"


def default_dest() -> Path:
    return Path.home() / ".claude" / "skills"


def _same(a: Path, b: Path) -> bool:
    a_files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    b_files = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    if a_files != b_files:
        return False
    return all((a / f).read_bytes() == (b / f).read_bytes() for f in a_files)


def sync(
    src: Path = REPO_SKILLS,
    dest: Path | None = None,
    force: bool = False,
    check: bool = False,
) -> dict:
    src = Path(src)
    dest = Path(dest) if dest else default_dest()
    result: dict = {"installed": [], "updated": [], "up_to_date": [], "drift": []}
    if not src.is_dir():
        return result
    for skill_dir in sorted(p for p in src.iterdir() if (p / "SKILL.md").is_file()):
        name = skill_dir.name
        target = dest / name
        if not target.exists():
            if not check:
                shutil.copytree(skill_dir, target)
            result["installed"].append(name)
        elif _same(skill_dir, target):
            result["up_to_date"].append(name)
        elif force and not check:
            shutil.rmtree(target)
            shutil.copytree(skill_dir, target)
            result["updated"].append(name)
        else:
            result["drift"].append(name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install repo skills/ into ~/.claude/skills (additive)."
    )
    parser.add_argument("--check", action="store_true",
                        help="report only; write nothing (exit 1 if pending)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite skills that drifted from the repo")
    parser.add_argument("--dest", default=None,
                        help="target skills dir (default ~/.claude/skills)")
    args = parser.parse_args()
    result = sync(dest=args.dest, force=args.force, check=args.check)
    print(json.dumps(result, indent=2))
    if args.check and (result["installed"] or result["drift"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_skill_sync.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/skill_sync.py tests/test_skill_sync.py
git commit -m "feat(skill-sync): additive sync of repo skills into ~/.claude/skills"
```

---

### Task 6: `/recall` skill

**Files:**
- Create: `skills/recall/SKILL.md`
- Test: `tests/test_skills.py` (new — frontmatter lint for all repo skills)

**Interfaces:**
- Consumes: MCP tools `context_bundle`, `read_note`, `write_note`, `capture`, `add_task` (existing) — referenced by name inside the skill prose.
- Produces: the `/recall` skill file; `tests/test_skills.py` with `EXPECTED` set that Tasks 7–8 extend.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills.py`:

```python
"""Lint the repo's Claude Code skills: frontmatter present and well-formed."""

from pathlib import Path

import yaml

SKILLS = Path(__file__).resolve().parent.parent / "skills"
EXPECTED = {"recall"}


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} missing frontmatter"
    end = text.index("\n---", 4)
    return yaml.safe_load(text[4:end])


def test_expected_skills_exist():
    found = {p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()}
    assert found == EXPECTED


def test_frontmatter_names_match_dirs_and_descriptions_are_real():
    for name in sorted(EXPECTED):
        meta = _frontmatter(SKILLS / name / "SKILL.md")
        assert meta["name"] == name
        assert len(meta["description"]) > 40, f"{name}: description too thin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_skills.py -q`
Expected: FAIL — `skills/` does not exist yet.

- [ ] **Step 3: Write the skill**

Create `skills/recall/SKILL.md`:

```markdown
---
name: recall
description: Use when Taimoor asks what he or the vault knows about a topic, wants a researched answer from the mind database, or says "recall X" / "what do we know about X". Searches the Tesseract vault, synthesizes a cited answer, and files it into Claude/Answers/ so knowledge compounds.
---

# Recall — researched Q&A over the Tesseract vault

Answer the question from the vault, cite every claim, file the answer.
Requires the `tesseract` MCP server.

## Contract (non-negotiable)

1. **Citation-or-label.** Every factual claim either cites its source note
   as a `[[wikilink]]`, or is explicitly marked *(not from the vault)*.
2. **Thin retrieval → say so.** If the vault has little on the topic,
   report that honestly and STOP. Never pad an answer with model knowledge
   dressed up as vault knowledge. No answer note is filed in that case.
3. Always end with a **"What the vault doesn't know"** section.

## Steps

1. `context_bundle(question, limit=10)` — hybrid hits + graph entities +
   related notes in one call.
2. Judge coverage: are at least 2 hits genuinely about the question? If
   not: tell Taimoor the vault has almost nothing on this, list the nearest
   misses, offer to `capture` the question or `add_task` a research
   follow-up, and stop here.
3. `read_note` the top 3–5 relevant hits IN FULL — the bundle's excerpts
   locate notes, they are not enough to synthesize from. Follow one or two
   `related_notes` chains if they add real context.
4. Compose the answer with exactly this structure:
   - `## Answer` — synthesized, every claim carrying a `[[wikilink]]`.
   - `## Sources` — bullet list of every cited note.
   - `## What the vault doesn't know` — gaps, contradictions, staleness.
5. Offer to file each gap as a task (`add_task`) or a capture — the gaps
   are tomorrow's ingest queue.
6. File the answer with `write_note`:
   - Path: `Claude/Answers/YYYY-MM-DD <question slug>.md` — slug is the
     question compressed to at most 6 words with the characters
     `\ / : * ? " < > | [ ] # ^` stripped. If the path already exists,
     suffix ` 2`, ` 3`, … (same rule as session notes).
   - Frontmatter (YAML):
     `created: YYYY-MM-DD HH:MM`, `agent: claude`, `project: <if obvious,
     else "">`, `tags: [answer]`, `type: answer`,
     `question: "<the exact question asked>"`.
   - Body: the three sections from step 4.
7. Show the full answer in chat as well — the note is for the vault, the
   chat reply is for now.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_skills.py -q`
Expected: 2 passed.

- [ ] **Step 5: Verify skill_sync sees it**

Run: `.venv\Scripts\python -m tesseract_mcp.skill_sync --check --dest "$env:TEMP\skills-check"`
Expected: JSON with `"installed": ["recall"]`, exit code 1 (pending install to a scratch dir — proves discovery works without touching the real `~/.claude/skills`).

- [ ] **Step 6: Commit**

```powershell
git add skills/recall/SKILL.md tests/test_skills.py
git commit -m "feat(skills): /recall - cited Q&A filed into Claude/Answers"
```

---

### Task 7: `/digest` skill

**Files:**
- Create: `skills/digest/SKILL.md`
- Modify: `tests/test_skills.py` (extend `EXPECTED`)

**Interfaces:**
- Consumes: MCP tools `recall_bundle` (Task 3 signature: `recall_bundle(mode, project?, since?)`), `query_notes`, `write_note`.
- Produces: the `/digest` skill file.

- [ ] **Step 1: Extend the lint test (failing)**

In `tests/test_skills.py`, change:

```python
EXPECTED = {"recall", "digest"}
```

Run: `.venv\Scripts\python -m pytest tests/test_skills.py -q`
Expected: FAIL — `digest` missing.

- [ ] **Step 2: Write the skill**

Create `skills/digest/SKILL.md`:

```markdown
---
name: digest
description: Use when Taimoor asks for his digest, review, "what's new in the vault", or a morning/weekly catch-up. Composes a review note from the Tesseract vault — recent changes, captures, tasks, librarian health, suggested questions — and files it to Claude/Digests/.
---

# Digest — the vault review ritual

Requires the `tesseract` MCP server.

## Steps

1. Find the newest note in `Claude/Digests/` (`query_notes` with
   `folder="Claude/Digests"`, or list the folder). Its date (from the
   filename `YYYY-MM-DD.md`) is `since`. If the folder is empty, omit
   `since` — the bundle defaults to 7 days back.
2. `recall_bundle(mode="digest", since="<YYYY-MM-DD>")`.
3. Compose the digest with EXACTLY these sections in this order. An empty
   section says "none" rather than disappearing — the eye learns the
   layout. A bundle section with `status: "error"` renders as
   `⚠ <section>: unavailable (<error>)` — never silently dropped.

   `## Health` — Librarian last-sweep timestamp and a one-line health
   summary from the `librarian` section. If the last sweep is older than
   48 hours, open the line with `⚠ stale sweep`.

   `## Captures to triage` — `inbox_captures` notes, each `[[wikilinked]]`.

   `## Tasks` — open tasks (count, then list), then `done_recently`.

   `## Recent changes` — `recent_notes` grouped by top-level folder,
   `[[wikilinked]]`. Skip the digest/answer notes this harness itself
   wrote if they dominate the list.

   `## Proposals pending` — the `proposals` count plus a pointer to
   `[[Organizer]]` and `[[Librarian]]`.

   `## New graph activity` — `new_entities` notes (entity names are the
   filename stems), `[[wikilinked]]`.

   `## Suggested questions` — 2–3 questions the vault is NEWLY equipped
   to answer, inferred from recent changes and new entities. Write each as
   a one-liner Taimoor can paste straight into `/recall`.

4. Write the note with `write_note` to `Claude/Digests/YYYY-MM-DD.md`
   (today's date), `overwrite=True` — rerunning the same day replaces that
   day's digest. Frontmatter: `created: YYYY-MM-DD HH:MM`, `agent: claude`,
   `project: ""`, `tags: [digest]`, `type: digest`.
5. Show the digest in chat too.
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_skills.py -q`
Expected: 2 passed.

- [ ] **Step 4: Commit**

```powershell
git add skills/digest/SKILL.md tests/test_skills.py
git commit -m "feat(skills): /digest - vault review ritual"
```

---

### Task 8: `/resume` and `/connections` skills

**Files:**
- Create: `skills/resume/SKILL.md`
- Create: `skills/connections/SKILL.md`
- Modify: `tests/test_skills.py` (extend `EXPECTED` to the full set)

**Interfaces:**
- Consumes: MCP tools `recall_bundle`, `read_note`, `write_note`, `query_notes`, `find_entity`, `search_brain`, `related_notes`, `list_recent`, `get_backlinks`, `capture`.
- Produces: the final two skill files; lint test covers all four.

- [ ] **Step 1: Extend the lint test (failing)**

In `tests/test_skills.py`, change:

```python
EXPECTED = {"recall", "digest", "resume", "connections"}
```

Run: `.venv\Scripts\python -m pytest tests/test_skills.py -q`
Expected: FAIL — `resume` and `connections` missing.

- [ ] **Step 2: Write the /resume skill**

Create `skills/resume/SKILL.md`:

```markdown
---
name: resume
description: Use when Taimoor asks where he left off, what the state of a project is, or to pick a project back up — "resume tesseract", "where was I on X", "what's the state of Y". Composes a briefing from the Tesseract vault's sessions, decisions, and tasks. Chat-only unless --save.
---

# Resume — project memory briefing

Requires the `tesseract` MCP server.

## Steps

1. Project = the argument. If missing, ask which project — offer candidates
   from `query_notes(folder="Claude/Sessions")` frontmatter `project`
   values.
2. `recall_bundle(mode="resume", project="<project>")`.
3. `read_note` the 1–2 newest session notes IN FULL — the bundle's excerpts
   locate them; the full text carries the actual state.
4. Compose the briefing in chat:
   - **Last state** — what the most recent session ended with.
   - **Open threads** — unresolved items across the recent sessions.
   - **Decisions in force** — the bundle's matching `Decisions.md` lines.
   - **Next actions** — the bundle's matching open tasks.
5. Do NOT file the briefing. Filing rule: file what compounds, skip what
   expires — a resume brief is stale in days, and filing it teaches search
   to retrieve dead state.
6. Exception: if the arguments contain `--save`, write a milestone snapshot
   with `write_note` to `Claude/Answers/YYYY-MM-DD Resume <project>.md`,
   frontmatter `created: YYYY-MM-DD HH:MM`, `agent: claude`,
   `project: <project>`, `tags: [resume]`, `type: resume`.
```

- [ ] **Step 3: Write the /connections skill**

Create `skills/connections/SKILL.md`:

```markdown
---
name: connections
description: Use when Taimoor asks what connects to a topic, wants serendipity from the vault — "show me something I forgot", "what links to X", "anything related to what I'm doing?" — or after finishing work on a topic. Walks the Tesseract entity graph for non-obvious links.
---

# Connections — graph serendipity

Requires the `tesseract` MCP server. Chat-only output; nothing is filed
unless Taimoor blesses a connection.

## Steps

1. Seed selection:
   - With a topic argument: `find_entity(topic)` for seed entities and
     `search_brain(topic, limit=3)` for seed notes.
   - Without: `list_recent(10)` and take the 2 newest notes under
     `Claude/Sessions/` or `Claude/Answers/` as seed notes.
2. For each seed note: `related_notes(path, hops=2)`.
3. Rank by SURPRISE, not relevance:
   - Prefer results whose `via` chain passes through 2+ entities — one-hop
     neighbors are usually already known.
   - Drop results the seed note already links directly (check the seed's
     own `[[wikilinks]]` via `read_note`, or `get_backlinks`) — those are
     memory, not serendipity.
4. Present the top 3–5 in chat, each as one line:
   `[[note]] — via <the "via" chain>` plus one sentence on why it might
   matter right now.
5. For each connection Taimoor calls interesting, file exactly one
   capture: `capture("<seed> ↔ <note>: <why it matters>")`.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_skills.py -q`
Expected: 2 passed.

- [ ] **Step 5: Full suite**

Run: `.venv\Scripts\python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add skills/resume/SKILL.md skills/connections/SKILL.md tests/test_skills.py
git commit -m "feat(skills): /resume + /connections"
```

---

### Task 9: Docs — README recall harness section + tool row

**Files:**
- Modify: `README.md` (Tools table + new "What's inside" subsection)

**Interfaces:**
- Consumes: names/commands from Tasks 3, 5, 6–8 (`recall_bundle`, `python -m tesseract_mcp.skill_sync`, the four skill names).
- Produces: user-facing documentation; no code.

- [ ] **Step 1: Add the tool row**

In `README.md`, in the Tools table's **Retrieve** group, add directly under the `context_bundle` row:

```markdown
| | `recall_bundle` | Digest/resume raw material for the recall skills — one read-only call |
```

- [ ] **Step 2: Add the harness section**

Add a new subsection in "What's inside", after "### One-command vault provisioning":

```markdown
### The recall harness
Four Claude Code skills turn the vault into a memory you can question:
`/recall` (researched answers, every claim cited as a `[[wikilink]]`),
`/digest` (the review ritual), `/resume` (project briefings), and
`/connections` (graph serendipity). `/recall` files every answer into
`Claude/Answers/`, where the Librarian indexes it like any note — so
answers become retrieval sources and the vault compounds from asking
questions, not just ingesting. Skills live in [`skills/`](skills/) and
install with `python -m tesseract_mcp.skill_sync` (additive; `--check`
reports drift; existing skills are never modified without `--force`).
```

- [ ] **Step 3: Verify and commit**

Run: `.venv\Scripts\python -m pytest -q`
Expected: all pass (docs-only change; run confirms clean tree).

```powershell
git add README.md
git commit -m "docs(readme): recall harness section + recall_bundle tool row"
```

---

## Post-merge rollout (manual, consent-gated — NOT part of task execution)

These steps touch the live vault and the real skills directory; per AGENTS.md
they require Taimoor's explicit go-ahead in an interactive session:

1. `.venv\Scripts\python scripts\install_conventions.py C:\Vaults\Tesseract`
   — creates `Claude/Answers/` + `Claude/Digests/` (installer never
   overwrites existing files).
2. Update the LIVE constitution `C:\Vaults\Tesseract\Claude\README.md` with
   the Task 4 bullets — the installer will not overwrite the existing file,
   so this is a deliberate edit (inside `Claude/`, so agent-writable once
   Taimoor says go).
3. `.venv\Scripts\python -m tesseract_mcp.skill_sync` — installs the four
   skills into the real `~/.claude/skills`. Agents may only run `--check`.
4. Run `/recall` and `/digest` manually for about a week to iterate on
   format before any scheduling.
5. Then schedule the daily digest (Claude Code scheduled agent / cron) and
   consider the citation-rate eval over golden queries (deferred by spec).
