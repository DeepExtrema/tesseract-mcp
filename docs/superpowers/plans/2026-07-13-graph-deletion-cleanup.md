# Graph Deletion & Orphaned-Entity Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make graph state converge to the facts that still exist — retract mentions of deleted notes, propose retirement of unsupported entities, repair dangling relations and merge-stub chains, prune the consolidation caches — and land the two fixes deferred from sub-project 1 (F-backstop, F-cluster).

**Architecture:** A new `cleanup.py` module owns detection + repair: manifest-vs-scan diff finds deleted notes, a single markdown pass finds orphans (no mentions, no outbound, no inbound relations), a `merged_into` chain resolver (living in `graphstore.py`) drives dangling-relation rewrites and stub flattening. Mechanical repairs auto-apply in a new librarian `cleanup` step (between `cache` and `consolidate`); retiring an entity note is propose-only, applied via an explicit CLI. See `docs/superpowers/specs/2026-07-13-graph-deletion-cleanup-design.md`.

**Tech Stack:** Python 3, `pytest`, `yaml`, sqlite3 (existing cache). No new dependencies, no LLM calls anywhere in this sub-project.

## Global Constraints

- **No LLM calls.** Every cleanup operation is mechanical — derived from file existence and frontmatter.
- **Two safety classes.** Mechanical repairs (mention retraction, relation repair, stub flattening, cache pruning) auto-apply under the librarian's `apply=True`; **retiring an entity note is propose-only** — pending proposals in state, applied only via `python -m tesseract_mcp.cleanup <vault> --apply-retirements`.
- **Retirement = tombstone, never file deletion:** frontmatter gains `retired: <YYYY-MM-DD HH:MM>`; aliases stay in frontmatter, summary stays in the body.
- **The `retired` frontmatter key is a literal string** in every module (same idiom as `merged_into` — no shared constant).
- **Everything that skips `merged_into` also skips `retired`:** `consolidate.gather_entities`, `cache.rebuild`, `consolidate._resolve_dup_note`.
- **Tunable constants live in `cleanup.py`:** `MAX_RETRACTIONS_PER_SWEEP = 100`, `MAX_RELATION_FIXES_PER_SWEEP = 200`, `MAX_PENDING_RETIREMENTS = 200`. The redirect depth cap `REDIRECT_MAX_DEPTH = 5` lives in `graphstore.py` next to the resolver.
- **State persists only under `apply=True`** (librarian invariant, unchanged). Dry-run reports counts and writes nothing.
- **Vault write quarantine:** all cleanup writes are entity notes under `Claude/Graph/` — never pass `confirm_outside_claude`. In tests, human notes must be written with raw `Path.write_text`, NOT `vault.write` (which raises `VaultError` outside `Claude/`).
- **Tests must not download a model:** `test_librarian.py` has an autouse `_no_model_downloads` fixture; `conftest.py` isolates `TESSERACT_STATE_DIR` for every test.
- **TDD, DRY, YAGNI, frequent commits.** One failing test → minimal code → green → commit.

---

## File Structure

- **Create `src/tesseract_mcp/cleanup.py`** — deleted-note detection + retraction, orphan detection + retirement proposals, retirement apply (tombstone), dangling-relation repair, stub flattening, `checked_hash` pruning, CLI. One responsibility: "make graph state converge to the facts that still exist."
- **Create `tests/test_cleanup.py`.**
- **Modify `src/tesseract_mcp/graphstore.py`** — `resolve_redirect` module function (+ `REDIRECT_MAX_DEPTH`), stub-aware `find_entity_note`, retired-entity revival in `upsert_entity_ex`.
- **Modify `src/tesseract_mcp/consolidate.py`** — `gather_entities` / `_resolve_dup_note` skip `retired`.
- **Modify `src/tesseract_mcp/cache.py`** — `rebuild` skips `retired`.
- **Modify `src/tesseract_mcp/blocking.py`** — `prune_entity_vectors`; F-cluster balanced chunking in `_cluster_pairs`.
- **Modify `src/tesseract_mcp/librarian.py`** — `cleanup` step + report/summary/health wiring; F-backstop (`_backstop_due` absent-marker default + first-sweep stamp).
- **Modify `tests/test_consolidate.py`, `tests/test_cache.py`, `tests/test_graphstore.py`, `tests/test_blocking.py`, `tests/test_librarian.py`** — per task below.

---

## Task 1: Deleted-note detection + retraction (`cleanup.deleted_notes`, `cleanup.retract_deleted`)

**Files:**
- Create: `src/tesseract_mcp/cleanup.py`
- Create: `tests/test_cleanup.py`

**Interfaces:**
- Consumes: `indexer.load_manifest/save_manifest/scan_notes/db_path`, `cache.note_entity_paths`, `GraphStore.remove_mention(entity_rel_with_md, note_path) -> bool`.
- Produces:
  - `cleanup.deleted_notes(vault: Vault) -> list[str]` — sorted vault-relative `.md` paths tracked by the manifest (hashes ∪ failures) but absent from `scan_notes`.
  - `cleanup.retract_deleted(vault: Vault, limit: int = MAX_RETRACTIONS_PER_SWEEP) -> dict` — `{"retracted_notes": int, "removed_mentions": int, "remaining": int}`.
  - Constants: `MAX_RETRACTIONS_PER_SWEEP = 100`, `MAX_RELATION_FIXES_PER_SWEEP = 200`, `MAX_PENDING_RETIREMENTS = 200`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cleanup.py`:

```python
"""Tests for graph deletion & orphaned-entity cleanup."""

from datetime import datetime

from tesseract_mcp import cache, cleanup, consolidate, indexer
from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GraphStore
from tesseract_mcp.search import parse_frontmatter

NOW = datetime(2026, 7, 13, 12, 0, 0)


def _ent(name, etype="organization"):
    return {"name": name, "type": etype, "aliases": [], "summary": "S."}


def _index_note(vault, rel, entities, relations=()):
    """Simulate one indexer pass: write the human note with a raw Path write
    (vault.write refuses non-Claude paths), extract, track in the manifest."""
    p = vault.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {p.stem}\n\nBody.\n", encoding="utf-8")
    GraphStore(vault).apply(rel, Extraction(list(entities), list(relations)))
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"][rel] = "digest"
    indexer.save_manifest(manifest, vault.root)
    cache.rebuild(vault, indexer.db_path(vault.root))


def test_deleted_notes_lists_tracked_but_missing(vault):
    _index_note(vault, "Projects/Kept.md", [_ent("Acme")])
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"]["Projects/Gone.md"] = "digest"
    manifest["failures"]["Projects/Benched.md"] = {"error": "x", "attempts": 3}
    indexer.save_manifest(manifest, vault.root)
    assert cleanup.deleted_notes(vault) == [
        "Projects/Benched.md", "Projects/Gone.md"]


def test_retract_deleted_removes_mentions_and_manifest_entry(vault):
    _index_note(vault, "Projects/Doomed.md", [_ent("Acme")])
    entity_rel = "Claude/Graph/Organizations/Acme.md"
    assert "[[Projects/Doomed|" in vault.read(entity_rel)
    (vault.root / "Projects" / "Doomed.md").unlink()
    result = cleanup.retract_deleted(vault)
    assert result == {"retracted_notes": 1, "removed_mentions": 1,
                      "remaining": 0}
    assert "[[Projects/Doomed|" not in vault.read(entity_rel)
    manifest = indexer.load_manifest(vault.root)
    assert "Projects/Doomed.md" not in manifest["hashes"]


def test_retract_deleted_respects_cap(vault):
    for i in range(3):
        _index_note(vault, f"Projects/N{i}.md", [_ent(f"Org{i}")])
        (vault.root / "Projects" / f"N{i}.md").unlink()
    result = cleanup.retract_deleted(vault, limit=2)
    assert result["retracted_notes"] == 2 and result["remaining"] == 1


def test_retract_deleted_scans_markdown_when_db_missing(vault):
    _index_note(vault, "Projects/Doomed.md", [_ent("Acme")])
    (vault.root / "Projects" / "Doomed.md").unlink()
    indexer.db_path(vault.root).unlink()
    result = cleanup.retract_deleted(vault)
    assert result["removed_mentions"] == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_cleanup.py -v`
Expected: FAIL with `ImportError: cannot import name 'cleanup'` (module does not exist).

- [ ] **Step 3: Create `cleanup.py` with detection + retraction**

Create `src/tesseract_mcp/cleanup.py`:

```python
"""Graph deletion & orphaned-entity cleanup.

Make graph state converge to the facts that still exist: retract mentions of
deleted notes, propose retirement of unsupported entities, repair dangling
relations and merge-stub chains, prune consolidation caches. Mechanical
repairs auto-apply from the librarian sweep; retiring an entity note is
propose-only, applied via this module's CLI. See
docs/superpowers/specs/2026-07-13-graph-deletion-cleanup-design.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import yaml

from . import cache
from . import indexer
from .cache import _RELATION
from .consolidate import _entity_summary, _section_lines
from .graphstore import (
    GRAPH_ROOT,
    MENTIONS_HEADER,
    RELATIONS_HEADER,
    GraphStore,
)
from .search import parse_frontmatter
from .vault import Vault, VaultError

MAX_RETRACTIONS_PER_SWEEP = 100
MAX_RELATION_FIXES_PER_SWEEP = 200
MAX_PENDING_RETIREMENTS = 200


def deleted_notes(vault: Vault) -> list[str]:
    """Notes the manifest tracks (hashes or failure ledger) that no longer
    exist on disk. Organizer moves never appear here: mover.move_note
    transfers manifest entries on move."""
    manifest = indexer.load_manifest(vault.root)
    tracked = set(manifest["hashes"]) | set(manifest["failures"])
    return sorted(tracked - set(indexer.scan_notes(vault)))


def _mentioning_entities(vault: Vault, note_rel: str) -> list[str]:
    """Entity paths (no .md) whose notes hold a mention of note_rel. Prefers
    the cache; falls back to a markdown scan when the DB is missing."""
    db = indexer.db_path(vault.root)
    if db.exists():
        return cache.note_entity_paths(db, note_rel)
    target = note_rel[:-3] if note_rel.endswith(".md") else note_rel
    marker = f"[[{target}|"
    graph_dir = vault.resolve(GRAPH_ROOT)
    if not graph_dir.is_dir():
        return []
    return sorted(
        "/".join(p.relative_to(vault.root).parts)[:-3]
        for p in graph_dir.rglob("*.md")
        if marker in p.read_text(encoding="utf-8", errors="ignore")
    )


def retract_deleted(vault: Vault, limit: int = MAX_RETRACTIONS_PER_SWEEP) -> dict:
    """Retract mentions of deleted-but-tracked notes and prune their manifest
    entries, bounded per sweep."""
    deleted = deleted_notes(vault)
    todo = deleted[:limit]
    store = GraphStore(vault)
    removed = 0
    manifest = indexer.load_manifest(vault.root)
    for rel in todo:
        for entity_path in _mentioning_entities(vault, rel):
            try:
                if store.remove_mention(entity_path + ".md", rel):
                    removed += 1
            except VaultError:
                continue  # entity note deleted/renamed by hand
        manifest["hashes"].pop(rel, None)
        manifest["failures"].pop(rel, None)
    if todo:
        indexer.save_manifest(manifest, vault.root)
    return {"retracted_notes": len(todo), "removed_mentions": removed,
            "remaining": len(deleted) - len(todo)}
```

(`yaml`, `datetime`, `argparse`, `json`, `_entity_summary`, `_section_lines`, `MENTIONS_HEADER`, `RELATIONS_HEADER`, `_RELATION`, and `parse_frontmatter` are used by later tasks in this same module — importing them now keeps every later task additive.)

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_cleanup.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/cleanup.py tests/test_cleanup.py
git commit -m "feat(cleanup): deleted-note retraction — manifest diff + bounded mention removal"
```

---

## Task 2: Retired tombstones (`cleanup.retire_note`) + reader exclusions

**Files:**
- Modify: `src/tesseract_mcp/cleanup.py`
- Modify: `src/tesseract_mcp/consolidate.py` (`gather_entities`, `_resolve_dup_note`)
- Modify: `src/tesseract_mcp/cache.py` (`rebuild`)
- Test: `tests/test_cleanup.py`, `tests/test_consolidate.py`

**Interfaces:**
- Consumes: `consolidate._entity_summary(text) -> str` (Task 1 imports).
- Produces: `cleanup.retire_note(vault: Vault, rel: str, now: datetime, reason: str) -> None` — replaces the note at `rel` (vault-relative, with `.md`) with a tombstone: frontmatter gains `retired: "YYYY-MM-DD HH:MM"`, aliases preserved, summary preserved in the body, `Retired: {reason}.` line appended.
- Behavior change relied on by later tasks: `gather_entities`, `cache.rebuild`, `_resolve_dup_note` treat `retired` exactly like `merged_into`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cleanup.py`:

```python
def _retire(vault, rel):
    cleanup.retire_note(vault, rel, NOW,
                        reason="orphaned — no mentions or relations")


def test_retire_note_writes_tombstone_keeping_aliases_and_summary(vault):
    GraphStore(vault).upsert_entity(
        {"name": "Acme", "type": "organization",
         "aliases": ["ACME Inc"], "summary": "Maker of anvils."})
    rel = "Claude/Graph/Organizations/Acme.md"
    _retire(vault, rel)
    text = vault.read(rel)
    meta = parse_frontmatter(text)
    assert meta["retired"] == "2026-07-13 12:00"
    assert meta["aliases"] == ["ACME Inc"]
    assert "Maker of anvils." in text and "Retired:" in text


def test_gather_entities_skips_retired(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Acme"))
    store.upsert_entity(_ent("Zeta"))
    _retire(vault, "Claude/Graph/Organizations/Acme.md")
    assert {e["name"] for e in consolidate.gather_entities(vault)} == {"Zeta"}


def test_cache_rebuild_skips_retired(vault):
    GraphStore(vault).upsert_entity(_ent("Acme"))
    _retire(vault, "Claude/Graph/Organizations/Acme.md")
    cache.rebuild(vault, indexer.db_path(vault.root))
    assert cache.find_entity(indexer.db_path(vault.root), "Acme") == []
```

Add to `tests/test_consolidate.py` (next to the existing redirect-stub tests; `yaml`, `parse_frontmatter`, `entity_rel_path`, and `seed` already exist there):

```python
def test_resolve_dup_note_skips_retired(vault):
    seed(vault)
    rel = entity_rel_path("organization", "Oracle VM deploy")
    text = vault.read(rel)
    meta = parse_frontmatter(text)
    meta["retired"] = "2026-07-13 12:00"
    end = text.find("\n---", 3)
    fm = "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---"
    vault.write(rel, fm + text[end + 4:], overwrite=True)
    assert consolidate._resolve_dup_note(
        vault, "organization", "Oracle VM deploy") is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_cleanup.py tests/test_consolidate.py::test_resolve_dup_note_skips_retired -v`
Expected: FAIL — `AttributeError: module 'tesseract_mcp.cleanup' has no attribute 'retire_note'`, and the `_resolve_dup_note` test returns a path instead of `None`.

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/cleanup.py`:

```python
def retire_note(vault: Vault, rel: str, now: datetime, reason: str) -> None:
    """Replace an entity note with a retired tombstone. Aliases stay in the
    frontmatter and the summary stays in the body for audit/revival."""
    text = vault.read(rel)
    meta = parse_frontmatter(text)
    meta["retired"] = now.strftime("%Y-%m-%d %H:%M")
    summary = _entity_summary(text)
    stem = rel.rsplit("/", 1)[-1][:-3]
    body = (f"# {stem}\n\n"
            + (f"{summary}\n\n" if summary else "")
            + f"Retired: {reason}.\n")
    fm = "---\n" + yaml.safe_dump(meta, sort_keys=False,
                                  default_flow_style=None) + "---\n\n"
    vault.write(rel, fm + body, overwrite=True)
```

In `src/tesseract_mcp/consolidate.py`, `gather_entities` — extend the existing skip:

```python
        if meta.get("merged_into") or meta.get("retired"):
            continue
```

In `consolidate._resolve_dup_note`, BOTH stub checks become (keep the surrounding code identical):

```python
        meta = parse_frontmatter(path.read_text(encoding="utf-8"))
        if meta.get("merged_into") or meta.get("retired"):
            return None
```

and in the folder-glob loop:

```python
        meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        if meta.get("merged_into") or meta.get("retired"):
            return None
```

In `src/tesseract_mcp/cache.py`, `rebuild` — extend the existing skip:

```python
            if meta.get("merged_into") or meta.get("retired"):
                continue
```

- [ ] **Step 4: Run to confirm pass (new + existing)**

Run: `python -m pytest tests/test_cleanup.py tests/test_consolidate.py tests/test_cache.py -v`
Expected: PASS — new tests plus every existing consolidate/cache test (adding a skip condition for a key no existing fixture sets is backward-compatible).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/cleanup.py src/tesseract_mcp/consolidate.py src/tesseract_mcp/cache.py tests/test_cleanup.py tests/test_consolidate.py
git commit -m "feat(cleanup): retired tombstones — retire_note + reader exclusions"
```

---

## Task 3: Redirect resolution (`graphstore.resolve_redirect`) + dangling-relation repair (`cleanup.repair_relations`)

**Files:**
- Modify: `src/tesseract_mcp/graphstore.py` (add `REDIRECT_MAX_DEPTH`, `resolve_redirect`)
- Modify: `src/tesseract_mcp/cleanup.py`
- Test: `tests/test_cleanup.py`

**Interfaces:**
- Consumes: `cache._RELATION` (line regex: group 1 = relation name, group 2 = target path without `.md`).
- Produces:
  - `graphstore.REDIRECT_MAX_DEPTH = 5`
  - `graphstore.resolve_redirect(vault: Vault, path: str, max_depth: int = REDIRECT_MAX_DEPTH) -> str | None` — follows a `merged_into` chain from an entity path (no `.md`) to a live entity path; `None` on missing file, retired note, cycle, escape-the-vault path, or chain deeper than `max_depth`.
  - `cleanup._target_status(vault: Vault, path: str) -> tuple[str, str | None]` — `("live", None)` | `("stub", canonical_or_None)` | `("gone", None)`.
  - `cleanup.repair_relations(vault: Vault, limit: int = MAX_RELATION_FIXES_PER_SWEEP) -> dict` — `{"fixed": int, "removed": int}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cleanup.py` (also add `from tesseract_mcp.graphstore import resolve_redirect` to the imports at the top):

```python
def _stub(vault, folder, name, target_path):
    """A merge-redirect stub, exactly as consolidate._apply_one writes them."""
    rel = f"Claude/Graph/{folder}/{name}.md"
    stem = target_path.rsplit("/", 1)[-1]
    vault.write(rel,
                ("---\nentity: organization\n"
                 f"merged_into: {target_path}\n---\n\n"
                 f"# {name}\n\nMerged into [[{stem}]].\n"),
                overwrite=True)
    return rel[:-3]


def test_resolve_redirect_follows_chain_to_live(vault):
    GraphStore(vault).upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Mid", "Claude/Graph/Organizations/Canonical")
    _stub(vault, "Organizations", "Old", "Claude/Graph/Organizations/Mid")
    assert resolve_redirect(vault, "Claude/Graph/Organizations/Old") == \
        "Claude/Graph/Organizations/Canonical"


def test_resolve_redirect_none_on_cycle_and_missing(vault):
    _stub(vault, "Organizations", "A", "Claude/Graph/Organizations/B")
    _stub(vault, "Organizations", "B", "Claude/Graph/Organizations/A")
    assert resolve_redirect(vault, "Claude/Graph/Organizations/A") is None
    assert resolve_redirect(vault, "Claude/Graph/Organizations/Ghost") is None


def test_repair_relations_rewrites_stub_target(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Src"))
    store.upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Dup", "Claude/Graph/Organizations/Canonical")
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/Dup.md")
    result = cleanup.repair_relations(vault)
    text = vault.read("Claude/Graph/Organizations/Src.md")
    assert "- uses [[Claude/Graph/Organizations/Canonical|Canonical]]" in text
    assert "Dup" not in text
    assert result == {"fixed": 1, "removed": 0}


def test_repair_relations_removes_missing_target(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Src"))
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/Ghost.md")
    result = cleanup.repair_relations(vault)
    assert "Ghost" not in vault.read("Claude/Graph/Organizations/Src.md")
    assert result == {"fixed": 0, "removed": 1}


def test_repair_relations_dedupes_when_canonical_already_present(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Src"))
    store.upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Dup", "Claude/Graph/Organizations/Canonical")
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/Canonical.md")
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/Dup.md")
    result = cleanup.repair_relations(vault)
    text = vault.read("Claude/Graph/Organizations/Src.md")
    assert text.count("[[Claude/Graph/Organizations/Canonical|") == 1
    assert result == {"fixed": 0, "removed": 1}


def test_repair_relations_respects_cap(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Src"))
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/GhostA.md")
    store.add_relation("Claude/Graph/Organizations/Src.md", "cites",
                       "Claude/Graph/Organizations/GhostB.md")
    result = cleanup.repair_relations(vault, limit=1)
    assert result["fixed"] + result["removed"] == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_cleanup.py -k "redirect or repair" -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_redirect'`.

- [ ] **Step 3: Implement the resolver in `graphstore.py`**

In `src/tesseract_mcp/graphstore.py`: change the vault import to `from .vault import Vault, VaultError`, add below the header constants:

```python
REDIRECT_MAX_DEPTH = 5


def resolve_redirect(
    vault: Vault, path: str, max_depth: int = REDIRECT_MAX_DEPTH
) -> str | None:
    """Follow a merged_into chain from an entity path (no .md) to a live
    entity path. None on a dead end: missing file, retired note, cycle, or
    a chain deeper than max_depth."""
    seen: set[str] = set()
    current = path
    for _ in range(max_depth + 1):
        if current in seen:
            return None
        seen.add(current)
        try:
            p = vault.resolve(current + ".md")
        except VaultError:
            return None
        if not p.is_file():
            return None
        meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        if meta.get("retired"):
            return None
        nxt = meta.get("merged_into")
        if not nxt:
            return current
        current = str(nxt)
    return None
```

- [ ] **Step 4: Implement `_target_status` and `repair_relations` in `cleanup.py`**

Add `resolve_redirect` to the `graphstore` import block in `cleanup.py`, then append:

```python
def _target_status(vault: Vault, path: str) -> tuple[str, str | None]:
    """('live', None) | ('stub', canonical-or-None) | ('gone', None)."""
    try:
        p = vault.resolve(path + ".md")
    except VaultError:
        return "gone", None
    if not p.is_file():
        return "gone", None
    meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
    if meta.get("retired"):
        return "gone", None
    if meta.get("merged_into"):
        return "stub", resolve_redirect(vault, path)
    return "live", None


def repair_relations(
    vault: Vault, limit: int = MAX_RELATION_FIXES_PER_SWEEP
) -> dict:
    """Rewrite relation lines whose target is a merge stub to the final
    canonical; drop lines whose target is retired or missing. Bounded."""
    graph_dir = vault.resolve(GRAPH_ROOT)
    fixed = removed = 0
    if not graph_dir.is_dir():
        return {"fixed": 0, "removed": 0}
    for p in sorted(graph_dir.rglob("*.md")):
        if fixed + removed >= limit:
            break
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        if meta.get("merged_into") or meta.get("retired"):
            continue
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        changed = False
        for line in lines:
            m = _RELATION.match(line.strip())
            if not m or fixed + removed >= limit:
                out.append(line)
                continue
            status, canonical = _target_status(vault, m.group(2).strip())
            if status == "live":
                out.append(line)
                continue
            changed = True
            if status == "stub" and canonical:
                stem = canonical.rsplit("/", 1)[-1]
                new = f"- {m.group(1)} [[{canonical}|{stem}]]\n"
                if new in out or new in lines:
                    removed += 1  # canonical relation already present
                else:
                    out.append(new)
                    fixed += 1
            else:
                removed += 1
        if changed:
            rel = "/".join(p.relative_to(vault.root).parts)
            vault.write(rel, "".join(out), overwrite=True)
    return {"fixed": fixed, "removed": removed}
```

- [ ] **Step 5: Run to confirm pass**

Run: `python -m pytest tests/test_cleanup.py tests/test_graphstore.py -v`
Expected: PASS (all — the resolver is additive to graphstore).

- [ ] **Step 6: Commit**

```bash
git add src/tesseract_mcp/graphstore.py src/tesseract_mcp/cleanup.py tests/test_cleanup.py
git commit -m "feat(cleanup): dangling-relation repair via merge-redirect resolution"
```

---

## Task 4: Merge-stub chain flattening (`cleanup.flatten_stubs`)

**Files:**
- Modify: `src/tesseract_mcp/cleanup.py`
- Test: `tests/test_cleanup.py`

**Interfaces:**
- Consumes: `graphstore.resolve_redirect`, `cleanup._target_status`, `cleanup.retire_note` (Tasks 2–3).
- Produces: `cleanup.flatten_stubs(vault: Vault, now: datetime) -> dict` — `{"flattened": int, "retired_stubs": int}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cleanup.py`:

```python
def test_flatten_stubs_points_chain_at_final_canonical(vault):
    GraphStore(vault).upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Mid", "Claude/Graph/Organizations/Canonical")
    _stub(vault, "Organizations", "Old", "Claude/Graph/Organizations/Mid")
    result = cleanup.flatten_stubs(vault, NOW)
    assert result == {"flattened": 1, "retired_stubs": 0}
    meta = parse_frontmatter(vault.read("Claude/Graph/Organizations/Old.md"))
    assert meta["merged_into"] == "Claude/Graph/Organizations/Canonical"
    assert "[[Canonical]]" in vault.read("Claude/Graph/Organizations/Old.md")


def test_flatten_stubs_retires_dead_end_stub(vault):
    _stub(vault, "Organizations", "Old", "Claude/Graph/Organizations/Ghost")
    result = cleanup.flatten_stubs(vault, NOW)
    assert result == {"flattened": 0, "retired_stubs": 1}
    meta = parse_frontmatter(vault.read("Claude/Graph/Organizations/Old.md"))
    assert meta["retired"] == "2026-07-13 12:00"


def test_flatten_stubs_leaves_live_targets_alone(vault):
    GraphStore(vault).upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Old", "Claude/Graph/Organizations/Canonical")
    assert cleanup.flatten_stubs(vault, NOW) == {
        "flattened": 0, "retired_stubs": 0}
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_cleanup.py -k flatten -v`
Expected: FAIL (`no attribute 'flatten_stubs'`).

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/cleanup.py`:

```python
def flatten_stubs(vault: Vault, now: datetime) -> dict:
    """Point stub chains at the final canonical; retire dead-end stubs
    (target missing, retired, or a cycle)."""
    graph_dir = vault.resolve(GRAPH_ROOT)
    flattened = retired = 0
    if not graph_dir.is_dir():
        return {"flattened": 0, "retired_stubs": 0}
    for p in sorted(graph_dir.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        if not meta.get("merged_into") or meta.get("retired"):
            continue
        target = str(meta["merged_into"])
        status, _ = _target_status(vault, target)
        if status == "live":
            continue
        rel = "/".join(p.relative_to(vault.root).parts)
        final = resolve_redirect(vault, target)
        if final:
            meta["merged_into"] = final
            stem = final.rsplit("/", 1)[-1]
            fm = "---\n" + yaml.safe_dump(meta, sort_keys=False,
                                          default_flow_style=None) + "---\n\n"
            vault.write(rel, fm + f"# {p.stem}\n\nMerged into [[{stem}]].\n",
                        overwrite=True)
            flattened += 1
        else:
            retire_note(vault, rel, now, reason="merge redirect target gone")
            retired += 1
    return {"flattened": flattened, "retired_stubs": retired}
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_cleanup.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/cleanup.py tests/test_cleanup.py
git commit -m "feat(cleanup): merge-stub chain flattening + dead-end retirement"
```

---

## Task 5: Orphan detection, retirement proposals, CLI apply

**Files:**
- Modify: `src/tesseract_mcp/cleanup.py`
- Test: `tests/test_cleanup.py`

**Interfaces:**
- Consumes: `consolidate._section_lines(text, header) -> list[str]`, `cache._RELATION`, `cleanup.retire_note` (Task 2).
- Produces:
  - `cleanup.find_orphans(vault: Vault) -> list[dict]` — `[{path, name, type, reason}]`, live entities with no mentions, no outbound and no inbound relations. Single markdown pass, no DB dependency.
  - `cleanup.update_retirement_proposals(block: dict, orphans: list[dict], limit: int = MAX_PENDING_RETIREMENTS) -> list[dict]` — self-healing pending list; mutates and returns `block["pending_retirements"]`.
  - `cleanup.apply_retirements(vault: Vault, paths: list[str] | None = None, now: datetime | None = None) -> dict` — `{"retired": [paths]}`; recomputes orphanhood (never trusts stale state), tombstones, rebuilds the cache.
  - `cleanup.main()` — CLI: report by default, `--apply-retirements [--paths ...]` to retire.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cleanup.py`:

```python
def test_find_orphans_flags_unsupported_entity_only(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Lonely"))                    # nothing supports it
    store.apply("A.md", Extraction([_ent("Mentioned")], []))
    store.apply("B.md", Extraction(
        [_ent("Source")],
        [{"from": "Source", "from_type": "organization", "rel": "uses",
          "to": "Endpoint", "to_type": "organization", "evidence": ""}]))
    orphans = cleanup.find_orphans(vault)
    assert [o["path"] for o in orphans] == \
        ["Claude/Graph/Organizations/Lonely"]
    # Mentioned has a mention; Source has an outbound relation; Endpoint is
    # a relation-only entity supported by its inbound edge.


def test_find_orphans_skips_stubs_and_retired(vault):
    GraphStore(vault).upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Dup", "Claude/Graph/Organizations/Canonical")
    GraphStore(vault).upsert_entity(_ent("Tomb"))
    _retire(vault, "Claude/Graph/Organizations/Tomb.md")
    paths = {o["path"] for o in cleanup.find_orphans(vault)}
    assert "Claude/Graph/Organizations/Dup" not in paths
    assert "Claude/Graph/Organizations/Tomb" not in paths


def test_update_retirement_proposals_self_heals_and_caps(vault):
    block = {"pending_retirements": [
        {"path": "gone-now-supported", "name": "X", "type": "topic",
         "reason": "orphaned: no mentions or relations"}]}
    orphans = [{"path": f"o{i}", "name": f"o{i}", "type": "topic",
                "reason": "orphaned: no mentions or relations"}
               for i in range(3)]
    pending = cleanup.update_retirement_proposals(block, orphans, limit=2)
    assert [p["path"] for p in pending] == ["o0", "o1"]  # healed + capped
    assert block["pending_retirements"] == pending


def test_apply_retirements_tombstones_and_rebuilds(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Lonely"))
    store.apply("A.md", Extraction([_ent("Kept")], []))
    result = cleanup.apply_retirements(vault, now=NOW)
    assert result == {"retired": ["Claude/Graph/Organizations/Lonely"]}
    meta = parse_frontmatter(vault.read("Claude/Graph/Organizations/Lonely.md"))
    assert meta["retired"] == "2026-07-13 12:00"
    assert cache.find_entity(indexer.db_path(vault.root), "Lonely") == []


def test_apply_retirements_paths_filter(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("LonelyA"))
    store.upsert_entity(_ent("LonelyB"))
    result = cleanup.apply_retirements(
        vault, paths=["Claude/Graph/Organizations/LonelyA"], now=NOW)
    assert result == {"retired": ["Claude/Graph/Organizations/LonelyA"]}
    meta = parse_frontmatter(vault.read("Claude/Graph/Organizations/LonelyB.md"))
    assert "retired" not in meta
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_cleanup.py -k "orphan or retirement" -v`
Expected: FAIL (`no attribute 'find_orphans'`).

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/cleanup.py`:

```python
def find_orphans(vault: Vault) -> list[dict]:
    """Live entities with no mentions, no outbound and no inbound relations.
    One markdown pass — the notes are the source of truth, not the DB.
    Relation-only entities (endpoints created by graphstore.apply without a
    mention) are supported by their inbound edge and are NOT orphans."""
    graph_dir = vault.resolve(GRAPH_ROOT)
    if not graph_dir.is_dir():
        return []
    candidates: list[dict] = []
    inbound: set[str] = set()
    for p in sorted(graph_dir.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        rel_lines = _section_lines(text, RELATIONS_HEADER)
        for line in rel_lines:
            m = _RELATION.match(line.strip())
            if m:
                inbound.add(m.group(2).strip())
        if meta.get("merged_into") or meta.get("retired"):
            continue
        candidates.append(
            {"path": "/".join(p.relative_to(vault.root).parts)[:-3],
             "name": p.stem,
             "type": str(meta.get("entity") or "topic"),
             "supported": bool(
                 _section_lines(text, MENTIONS_HEADER) or rel_lines)})
    return [
        {"path": c["path"], "name": c["name"], "type": c["type"],
         "reason": "orphaned: no mentions or relations"}
        for c in candidates
        if not c["supported"] and c["path"] not in inbound
    ]


def update_retirement_proposals(
    block: dict, orphans: list[dict], limit: int = MAX_PENDING_RETIREMENTS
) -> list[dict]:
    """Self-healing pending list: drop entries no longer orphaned, add new
    orphans, cap the total. Mutates block["pending_retirements"]."""
    current = {o["path"] for o in orphans}
    pending = [p for p in (block.get("pending_retirements") or [])
               if p["path"] in current]
    have = {p["path"] for p in pending}
    for o in orphans:
        if len(pending) >= limit:
            break
        if o["path"] not in have:
            pending.append(o)
    block["pending_retirements"] = pending
    return pending


def apply_retirements(
    vault: Vault, paths: list[str] | None = None, now: datetime | None = None
) -> dict:
    """Retire CURRENTLY-orphaned entities (orphanhood recomputed — stale
    proposals are never trusted), optionally filtered to paths."""
    now = now or datetime.now()
    orphans = find_orphans(vault)
    if paths is not None:
        wanted = set(paths)
        orphans = [o for o in orphans if o["path"] in wanted]
    for o in orphans:
        retire_note(vault, o["path"] + ".md", now,
                    reason="orphaned — no mentions or relations")
    if orphans:
        cache.rebuild(vault, indexer.db_path(vault.root))
    return {"retired": sorted(o["path"] for o in orphans)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Graph deletion & orphaned-entity cleanup.")
    parser.add_argument("vault", help="Path to the Obsidian vault root")
    parser.add_argument("--apply-retirements", action="store_true",
                        help="retire currently-orphaned entities "
                             "(default: report only)")
    parser.add_argument("--paths", nargs="*", default=None,
                        help="restrict retirement to these entity paths")
    args = parser.parse_args()
    vault = Vault(args.vault)
    if args.apply_retirements:
        print(json.dumps(apply_retirements(vault, paths=args.paths), indent=2))
        return
    print(json.dumps({"deleted_notes": deleted_notes(vault),
                      "orphans": find_orphans(vault)}, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_cleanup.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/cleanup.py tests/test_cleanup.py
git commit -m "feat(cleanup): orphan detection, retirement proposals, CLI apply"
```

---

## Task 6: Stub-aware `find_entity_note` + retired-entity revival (`graphstore`)

**Files:**
- Modify: `src/tesseract_mcp/graphstore.py` (`find_entity_note`, `upsert_entity_ex`)
- Test: `tests/test_graphstore.py`

**Interfaces:**
- Consumes: `graphstore.resolve_redirect` (Task 3).
- Produces (behavior):
  - `find_entity_note` — when the stem/alias match is a merge stub, returns the chain's final canonical rel (`.md`); if the chain dead-ends, returns the stub rel unchanged. Retired notes are returned as-is (callers revive or skip).
  - `upsert_entity_ex` — when the found note is `retired`, revives it: fresh template from the incoming entity, stub's recorded aliases merged in, returns `(rel, True)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_graphstore.py` (it already imports `GraphStore` and uses the `vault` fixture; add `from tesseract_mcp.search import parse_frontmatter` if missing):

```python
def _org(name, aliases=(), summary="S."):
    return {"name": name, "type": "organization",
            "aliases": list(aliases), "summary": summary}


def _write_stub(vault, name, target_path):
    vault.write(f"Claude/Graph/Organizations/{name}.md",
                ("---\nentity: organization\n"
                 f"merged_into: {target_path}\n---\n\n"
                 f"# {name}\n\nMerged into [[X]].\n"),
                overwrite=True)


def test_find_entity_note_follows_merge_redirect(vault):
    store = GraphStore(vault)
    store.upsert_entity(_org("Canonical"))
    _write_stub(vault, "Dup", "Claude/Graph/Organizations/Canonical")
    assert store.find_entity_note("organization", "Dup") == \
        "Claude/Graph/Organizations/Canonical.md"


def test_find_entity_note_returns_stub_when_chain_dead_ends(vault):
    _write_stub(vault, "Dup", "Claude/Graph/Organizations/Ghost")
    assert GraphStore(vault).find_entity_note("organization", "Dup") == \
        "Claude/Graph/Organizations/Dup.md"


def test_upsert_revives_retired_entity(vault):
    store = GraphStore(vault)
    store.upsert_entity(_org("Acme", aliases=["ACME Inc"]))
    rel = "Claude/Graph/Organizations/Acme.md"
    text = vault.read(rel)
    meta = parse_frontmatter(text)
    meta["retired"] = "2026-07-13 12:00"
    end = text.find("\n---", 3)
    vault.write(rel, "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---"
                + text[end + 4:], overwrite=True)
    got, created = store.upsert_entity_ex(
        _org("Acme", aliases=["Acme Corp"], summary="Back again."))
    assert got == rel and created is True
    revived = vault.read(rel)
    revived_meta = parse_frontmatter(revived)
    assert "retired" not in revived_meta
    assert set(revived_meta["aliases"]) == {"Acme Corp", "ACME Inc"}
    assert "Back again." in revived and "## Mentions" in revived
```

(If `tests/test_graphstore.py` does not already import `yaml` or `parse_frontmatter`, add them at the top.)

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_graphstore.py -k "redirect or dead_ends or revives" -v`
Expected: FAIL — `find_entity_note` returns the stub for the redirect test; the revival test raises `VaultError` (overwrite of existing note) or returns `created is False`.

- [ ] **Step 3: Implement**

In `src/tesseract_mcp/graphstore.py`, replace `find_entity_note` with:

```python
    def find_entity_note(self, etype: str, name: str) -> str | None:
        folder = self.vault.resolve(f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}")
        if not folder.is_dir():
            return None
        needle = name.casefold()
        for p in sorted(folder.glob("*.md")):
            meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
            aliases = meta.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = [aliases]
            if (p.stem.casefold() != needle
                    and safe_filename(name).casefold() != p.stem.casefold()
                    and needle not in {str(a).casefold() for a in aliases}):
                continue
            rel = f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{p.name}"
            if meta.get("merged_into"):
                # a new mention of a merged name belongs on the canonical,
                # not the stub; a dead-end chain keeps the stub as-is
                canonical = resolve_redirect(self.vault, str(meta["merged_into"]))
                return (canonical + ".md") if canonical else rel
            return rel
        return None
```

In `upsert_entity_ex`, insert the revival branch right after the `existing is None` create path (i.e. before the alias-merge logic reads the note):

```python
        # merge new aliases (and a colliding display name) into frontmatter
        text = self.vault.read(existing)
        meta = parse_frontmatter(text)
        if meta.get("retired"):
            # revive: a retired entity that reappears in extraction comes
            # back as a fresh note, keeping the tombstone's recorded aliases
            revived = dict(ent)
            old = meta.get("aliases") or []
            if not isinstance(old, list):
                old = [old]
            known = {str(a).casefold() for a in (ent.get("aliases") or [])}
            known.add(ent["name"].casefold())
            revived["aliases"] = list(ent.get("aliases") or []) + [
                str(a) for a in old if str(a).casefold() not in known]
            self.vault.write(existing, _note_template(revived, now),
                             overwrite=True)
            return existing, True
```

(The existing function already reads `text` and `meta` — reorder so the read happens once and the retired check precedes the alias merge.)

- [ ] **Step 4: Run to confirm pass (new + full graphstore/consolidate/indexer suites)**

Run: `python -m pytest tests/test_graphstore.py tests/test_consolidate.py tests/test_indexer.py -v`
Expected: PASS. (`_apply_one` looks up canonicals that are live; `_resolve_dup_note` has its own stub logic — no existing expectations change.)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/graphstore.py tests/test_graphstore.py
git commit -m "feat(graphstore): stub-aware entity resolution + retired-entity revival"
```

---

## Task 7: Cache pruning (`blocking.prune_entity_vectors`, `cleanup.prune_checked_hash`)

**Files:**
- Modify: `src/tesseract_mcp/blocking.py`
- Modify: `src/tesseract_mcp/cleanup.py`
- Test: `tests/test_blocking.py`, `tests/test_cleanup.py`

**Interfaces:**
- Consumes: `blocking._load_entity_vectors` / `_save_entity_vectors` (existing, atomic).
- Produces:
  - `blocking.prune_entity_vectors(state_root: Path, live_paths: set[str]) -> int` — drops cached vectors for paths not in `live_paths`, returns count dropped; writes only when something changed.
  - `cleanup.prune_checked_hash(con: dict, live_paths: set[str]) -> int` — same for the consolidation state block's `checked_hash`; mutates `con`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_blocking.py` (the `FakeEmbedder` and `_ents` helpers already exist there):

```python
def test_prune_entity_vectors_drops_vanished_paths(tmp_path):
    blocking.compute_entity_vectors(_ents(), tmp_path, FakeEmbedder())
    live = {"Claude/Graph/Organizations/Acme"}  # Acme Corp vanished
    assert blocking.prune_entity_vectors(tmp_path, live) == 1
    cache = blocking._load_entity_vectors(tmp_path)
    assert set(cache) == live


def test_prune_entity_vectors_noop_when_all_live(tmp_path):
    blocking.compute_entity_vectors(_ents(), tmp_path, FakeEmbedder())
    live = {e["path"] for e in _ents()}
    assert blocking.prune_entity_vectors(tmp_path, live) == 0
```

Add to `tests/test_cleanup.py`:

```python
def test_prune_checked_hash_drops_vanished_paths():
    con = {"checked_hash": {"live": "h1", "gone": "h2"}}
    assert cleanup.prune_checked_hash(con, {"live"}) == 1
    assert con["checked_hash"] == {"live": "h1"}


def test_prune_checked_hash_handles_empty_block():
    assert cleanup.prune_checked_hash({}, {"anything"}) == 0
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_blocking.py -k prune -v && python -m pytest tests/test_cleanup.py -k prune -v`
Expected: FAIL (`no attribute 'prune_entity_vectors'` / `'prune_checked_hash'`).

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/blocking.py`:

```python
def prune_entity_vectors(state_root: Path, live_paths: set[str]) -> int:
    """Drop cached identity vectors for entities that no longer exist
    (deleted, merged, or retired). Returns the number of keys dropped."""
    cache = _load_entity_vectors(state_root)
    stale = [k for k in cache if k not in live_paths]
    for k in stale:
        del cache[k]
    if stale:
        _save_entity_vectors(state_root, cache)
    return len(stale)
```

Append to `src/tesseract_mcp/cleanup.py`:

```python
def prune_checked_hash(con: dict, live_paths: set[str]) -> int:
    """Drop consolidation checked_hash entries for vanished entities.
    Mutates con in place; the caller owns persisting the state."""
    checked = con.get("checked_hash") or {}
    stale = [k for k in checked if k not in live_paths]
    for k in stale:
        del checked[k]
    if stale:
        con["checked_hash"] = checked
    return len(stale)
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_blocking.py tests/test_cleanup.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/blocking.py src/tesseract_mcp/cleanup.py tests/test_blocking.py tests/test_cleanup.py
git commit -m "feat(cleanup): prune entity-vector cache and checked_hash for vanished entities"
```

---

## Task 8: F-backstop — the backstop clock starts at the first sweep

**Files:**
- Modify: `src/tesseract_mcp/librarian.py` (`_backstop_due`, `_consolidate_step`)
- Test: `tests/test_librarian.py`

**Interfaces:**
- Produces (behavior):
  - `_backstop_due({}, now) is False` — an absent `backstop_last_advance` marker means NOT due.
  - The first apply-mode `_consolidate_step` stamps `backstop_last_advance = now` even when the backstop did not run, so the first backstop cycle begins one full `BACKSTOP_MIN_INTERVAL_DAYS` after the first sweep.

- [ ] **Step 1: Update/replace the failing tests**

In `tests/test_librarian.py`, REPLACE `test_backstop_due_on_first_pass` with:

```python
def test_backstop_not_due_before_first_stamp():
    assert librarian._backstop_due({}, NOW) is False
```

Add (uses the existing `FakeExtractor`, `FakeConsolidator`, `FakeEmbedder`, `_entity_note` helpers):

```python
def test_first_apply_sweep_stamps_backstop_clock(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    con = librarian.load_state(vault)["consolidation"]
    assert con["backstop_last_advance"] == NOW.strftime(librarian.TS_FMT)


def test_backstop_cold_until_interval_then_reruns(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    _entity_note(vault_dir, "Organizations", "Acme Corp", "organization")
    fake = FakeConsolidator()
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW)
    calls_after_drain = fake.calls
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(),
                        now=NOW + timedelta(days=13))
    assert fake.calls == calls_after_drain      # would have re-run pre-fix
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(),
                        now=NOW + timedelta(days=14))
    assert fake.calls > calls_after_drain       # first backstop cycle
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_librarian.py -k backstop -v`
Expected: FAIL — `_backstop_due({}, NOW)` is currently `True`, and the +13d sweep re-runs the consolidator.

- [ ] **Step 3: Implement**

In `src/tesseract_mcp/librarian.py`, replace `_backstop_due`:

```python
def _backstop_due(con: dict, now: datetime) -> bool:
    """The rolling backstop re-check runs at most once per interval. An
    absent marker means the clock has not started (it is stamped on the
    first apply sweep), NOT that a full re-check is immediately owed — the
    cold-start unchecked drain already covers every entity."""
    last = con.get("backstop_last_advance")
    if not last:
        return False
    return (now - datetime.strptime(last, TS_FMT)).days >= BACKSTOP_MIN_INTERVAL_DAYS
```

In `_consolidate_step`, replace the marker-stamping line in the `if apply:` block:

```python
        if used_backstop or "backstop_last_advance" not in con:
            con["backstop_last_advance"] = now.strftime(TS_FMT)
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: PASS — including the untouched `test_backstop_not_due_before_interval` / `test_backstop_due_after_interval` (marker-present semantics unchanged) and `test_second_sweep_skips_when_all_checked_and_backstop_cold`.

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/librarian.py tests/test_librarian.py
git commit -m "fix(librarian): backstop clock starts at first sweep, not before it (F-backstop)"
```

---

## Task 9: F-cluster — balanced oversize-component splitting

**Files:**
- Modify: `src/tesseract_mcp/blocking.py` (`_cluster_pairs`)
- Test: `tests/test_blocking.py`

**Interfaces:**
- Produces (behavior): `_cluster_pairs` splits a component of size `n > max_cluster` into `k = ceil(n / max_cluster)` chunks whose sizes differ by at most one — no singleton chunks for real components, so `candidate_clusters`' `len >= 2` filter no longer strands members.

- [ ] **Step 1: Update/replace the failing tests**

In `tests/test_blocking.py`, REPLACE `test_cluster_pairs_splits_oversize` with:

```python
def test_cluster_pairs_splits_oversize_balanced():
    members = [f"n{i:02d}" for i in range(11)]
    pairs = {("n00", m) for m in members[1:]}  # star -> one component of 11
    clusters = blocking._cluster_pairs(pairs, max_cluster=10)
    assert sorted(len(c) for c in clusters) == [5, 6]
    assert sorted(x for c in clusters for x in c) == members  # nobody lost


def test_oversize_component_strands_no_member():
    ents = [{"path": f"n{i:02d}", "type": "topic"} for i in range(11)]
    vectors = {e["path"]: [1.0, 0.0] for e in ents}
    clusters = blocking.candidate_clusters(ents, ents, vectors, k=10)
    covered = {e["path"] for c in clusters for e in c}
    assert covered == {e["path"] for e in ents}
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_blocking.py -k "oversize" -v`
Expected: FAIL — current chunking yields sizes `[1, 10]` and the singleton is dropped by `candidate_clusters`.

- [ ] **Step 3: Implement**

In `blocking._cluster_pairs`, replace the final chunking loop:

```python
    clusters: list[list[str]] = []
    for members in groups.values():
        members.sort()
        if len(members) <= max_cluster:
            clusters.append(members)
            continue
        k = -(-len(members) // max_cluster)  # ceil: number of chunks
        base, extra = divmod(len(members), k)
        start = 0
        for i in range(k):
            size = base + (1 if i < extra else 0)
            clusters.append(members[start:start + size])
            start += size
    return clusters
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_blocking.py -v`
Expected: PASS (all — `test_cluster_pairs_unions_overlapping` and the rest are size-≤-cap cases, untouched).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/blocking.py tests/test_blocking.py
git commit -m "fix(blocking): balanced oversize-cluster split strands no member (F-cluster)"
```

---

## Task 10: Librarian `cleanup` step — orchestration, report, health

**Files:**
- Modify: `src/tesseract_mcp/librarian.py` (`_cleanup_step`, `run_sweep`, `_summarize_steps`, `format_report`, `run_health`)
- Test: `tests/test_librarian.py`

**Interfaces:**
- Consumes: `cleanup.retract_deleted`, `cleanup.repair_relations`, `cleanup.flatten_stubs`, `cleanup.find_orphans`, `cleanup.update_retirement_proposals`, `cleanup.prune_checked_hash`, `cleanup.deleted_notes` (Tasks 1–5, 7); `blocking.prune_entity_vectors` (Task 7); `consolidate_mod.gather_entities`; `cache.rebuild`; `indexer.db_path/state_dir`.
- Produces:
  - `librarian._cleanup_step(vault, state, now, apply) -> dict` with keys `applied`, `deleted_pending`, `orphans`, `retracted_notes`, `removed_mentions`, `fixed_relations`, `removed_relations`, `flattened_stubs`, `retired_stubs`, `pruned_cache_keys`, `pending_retirements`.
  - Step order: index → organize → cache → **cleanup** → consolidate.
  - `state["cleanup"] = {"pending_retirements": [...], "last_pass": ts}`.
  - Health gains `pending_retirements` (int).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_librarian.py`:

```python
def test_sweep_cleanup_retracts_deleted_note_and_proposes_orphan(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization",
                 mentions=["Projects/Doomed"])
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"]["Projects/Doomed.md"] = "digest"
    indexer.save_manifest(manifest, vault.root)
    cache.rebuild(vault, indexer.db_path(vault.root))
    result = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                 consolidator=FakeConsolidator(),
                                 embedder=FakeEmbedder(), now=NOW)
    step = result["steps"]["cleanup"]
    assert step["retracted_notes"] == 1 and step["removed_mentions"] == 1
    text = (vault_dir / "Claude" / "Graph" / "Organizations"
            / "Acme.md").read_text(encoding="utf-8")
    assert "Doomed" not in text
    # with its only mention gone, Acme is now proposed for retirement
    assert step["pending_retirements"] == 1
    pending = librarian.load_state(vault)["cleanup"]["pending_retirements"]
    assert pending[0]["path"] == "Claude/Graph/Organizations/Acme"


def test_cleanup_dry_run_writes_nothing(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization",
                 mentions=["Projects/Doomed"])
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"]["Projects/Doomed.md"] = "digest"
    indexer.save_manifest(manifest, vault.root)
    cache.rebuild(vault, indexer.db_path(vault.root))
    result = librarian.run_sweep(vault, consolidator=FakeConsolidator(),
                                 embedder=FakeEmbedder(), apply=False, now=NOW)
    step = result["steps"]["cleanup"]
    assert step["applied"] is False and step["deleted_pending"] == 1
    text = (vault_dir / "Claude" / "Graph" / "Organizations"
            / "Acme.md").read_text(encoding="utf-8")
    assert "Doomed" in text  # nothing was retracted


def test_sweep_prunes_stale_consolidation_keys(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    (vault_dir / "Claude" / "Graph" / "Organizations" / "Acme.md").unlink()
    result = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                 consolidator=FakeConsolidator(),
                                 embedder=FakeEmbedder(),
                                 now=NOW + timedelta(minutes=5))
    assert result["steps"]["cleanup"]["pruned_cache_keys"] == 2  # vector + hash
    assert librarian.load_state(vault)["consolidation"]["checked_hash"] == {}


def test_summarize_steps_includes_cleanup():
    steps = {"cleanup": {"applied": True, "deleted_pending": 0, "orphans": 1,
                         "retracted_notes": 2, "removed_mentions": 3,
                         "fixed_relations": 1, "removed_relations": 0,
                         "flattened_stubs": 0, "retired_stubs": 0,
                         "pruned_cache_keys": 4, "pending_retirements": 1}}
    out = librarian._summarize_steps(steps)
    assert out["cleanup"]["retracted_notes"] == 2
    assert out["cleanup"]["pending_retirements"] == 1


def test_health_counts_pending_retirements(vault):
    state = {"cleanup": {"pending_retirements": [{"path": "x"}]}}
    health = librarian.run_health(vault, state, None, None, {})
    assert health["pending_retirements"] == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_librarian.py -k "cleanup or pending_retirements or prunes" -v`
Expected: FAIL — `result["steps"]` has no `"cleanup"` key; `_summarize_steps`/`run_health` lack the fields.

- [ ] **Step 3: Implement the step**

In `src/tesseract_mcp/librarian.py`, add the import `from . import cleanup as cleanup_mod`, then add above `_step`:

```python
def _cleanup_step(vault: Vault, state: dict, now: datetime, apply: bool) -> dict:
    """Mechanical graph hygiene (auto-applied) + retirement proposals
    (propose-only; applied via the cleanup CLI). Runs before consolidate so
    the slice never spends budget on entities this sweep just unsupported."""
    if not apply:
        return {"applied": False,
                "deleted_pending": len(cleanup_mod.deleted_notes(vault)),
                "orphans": len(cleanup_mod.find_orphans(vault)),
                "retracted_notes": 0, "removed_mentions": 0,
                "fixed_relations": 0, "removed_relations": 0,
                "flattened_stubs": 0, "retired_stubs": 0,
                "pruned_cache_keys": 0, "pending_retirements": 0}
    ret = cleanup_mod.retract_deleted(vault)
    rel = cleanup_mod.repair_relations(vault)
    stubs = cleanup_mod.flatten_stubs(vault, now)
    if (ret["removed_mentions"] or rel["fixed"] or rel["removed"]
            or stubs["flattened"] or stubs["retired_stubs"]):
        cache.rebuild(vault, indexer.db_path(vault.root))
    orphans = cleanup_mod.find_orphans(vault)
    block = state.get("cleanup") or {}
    pending = cleanup_mod.update_retirement_proposals(block, orphans)
    block["last_pass"] = now.strftime(TS_FMT)
    state["cleanup"] = block
    live = {e["path"] for e in consolidate_mod.gather_entities(vault)}
    pruned = blocking.prune_entity_vectors(indexer.state_dir(vault.root), live)
    pruned += cleanup_mod.prune_checked_hash(
        state.get("consolidation") or {}, live)
    return {"applied": True,
            "deleted_pending": ret["remaining"], "orphans": len(orphans),
            "retracted_notes": ret["retracted_notes"],
            "removed_mentions": ret["removed_mentions"],
            "fixed_relations": rel["fixed"],
            "removed_relations": rel["removed"],
            "flattened_stubs": stubs["flattened"],
            "retired_stubs": stubs["retired_stubs"],
            "pruned_cache_keys": pruned,
            "pending_retirements": len(pending)}
```

In `run_sweep`, add between the `cache` and `consolidate` steps:

```python
    _step(result, "cleanup",
          lambda: _cleanup_step(vault, state, now, apply))
```

In `_summarize_steps`, add before the consolidate block:

```python
    cl = steps.get("cleanup")
    out["cleanup"] = cl if cl is None else {
        "retracted_notes": cl["retracted_notes"],
        "fixed_relations": cl["fixed_relations"],
        "removed_relations": cl["removed_relations"],
        "flattened_stubs": cl["flattened_stubs"],
        "retired_stubs": cl["retired_stubs"],
        "pruned_cache_keys": cl["pruned_cache_keys"],
        "pending_retirements": cl["pending_retirements"],
    }
```

In `format_report`, add after the cache lines and before the consolidate lines:

```python
    cl = steps.get("cleanup")
    if cl is None:
        lines.append("- cleanup: FAILED\n")
    elif not cl["applied"]:
        lines.append(f"- cleanup: {cl['deleted_pending']} deleted notes "
                     f"pending, {cl['orphans']} orphans (dry-run)\n")
    else:
        lines.append(f"- cleanup: retracted {cl['retracted_notes']} notes, "
                     f"fixed {cl['fixed_relations'] + cl['removed_relations']} "
                     f"relations, {cl['pending_retirements']} retirement "
                     f"proposals\n")
```

In `run_health`, add to the `checks` dict:

```python
        "pending_retirements": lambda: len(
            (state.get("cleanup") or {}).get("pending_retirements", [])),
```

and in `format_report`'s health line, append ` | pending_retirements {h.get('pending_retirements', 0)}` to the f-string before the trailing `\n`.

- [ ] **Step 4: Run the touched suites, then the whole suite**

Run: `python -m pytest tests/test_librarian.py tests/test_cleanup.py tests/test_blocking.py tests/test_consolidate.py -v`
Expected: PASS. Then: `python -m pytest -q`
Expected: PASS (no unrelated regressions; existing sweep integration tests only assert on their own steps).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/librarian.py tests/test_librarian.py
git commit -m "feat(librarian): cleanup step — retraction, repairs, proposals, pruning"
```

---

## Self-Review

**1. Spec coverage** (each spec section → task):

| Spec section | Task |
|---|---|
| §1 Deleted-note retraction (manifest diff, cap, DB fallback, manifest prune) | Task 1 |
| §2 Orphan definition (mentions + outbound + inbound), proposal flow, self-heal, cap | Task 5 |
| §2 Tombstone retirement, apply CLI, recompute-don't-trust | Tasks 2 (tombstone), 5 (apply + CLI) |
| §2 Revival on re-extraction; reader exclusions | Task 6 (revival), Task 2 (exclusions) |
| §3 Dangling-relation repair (stub rewrite, chain, dedupe, remove, cap) | Task 3 |
| §4 Stub-chain flattening + dead-end retirement | Task 4 |
| §4 Stub-aware `find_entity_note` | Task 6 |
| §4 Cache pruning (vectors + checked_hash; cursor untouched) | Task 7 |
| §5 Librarian step (order, rebuild-on-change, state, report, health, dry-run, error isolation) | Task 10 (`_step` wrapper + `VaultError` tolerance give isolation) |
| §6 F-backstop | Task 8 |
| §7 F-cluster | Task 9 |
| Constants (`cleanup.py` caps; `REDIRECT_MAX_DEPTH` in graphstore) | Tasks 1, 3 |
| Out of scope: relation provenance, stub deletion, auto-retire, decay | Untouched |

No gaps.

**2. Placeholder scan:** every code/test step shows real content; no "TBD"/"similar to Task N"/"handle edge cases". ✓

**3. Type consistency:**
- `retract_deleted -> {"retracted_notes", "removed_mentions", "remaining"}` — consumed with exactly these keys in Task 10. ✓
- `repair_relations -> {"fixed", "removed"}`; `flatten_stubs -> {"flattened", "retired_stubs"}` — same keys in Task 10's step dict mapping (`fixed_relations`/`removed_relations`/`flattened_stubs`/`retired_stubs`). ✓
- `find_orphans -> [{path, name, type, reason}]` feeds `update_retirement_proposals(block, orphans, limit)` feeds `pending_retirements` state list — same dict shape in Tasks 5 and 10. ✓
- `resolve_redirect(vault, path, max_depth) -> str | None` — same call shape in Tasks 3, 4 (via `_target_status`), 6. ✓
- `prune_entity_vectors(state_root, live_paths) -> int`, `prune_checked_hash(con, live_paths) -> int` — summed into `pruned_cache_keys` in Task 10. ✓
- Entity paths are vault-relative WITHOUT `.md` everywhere except `retire_note`/`remove_mention`/`find_entity_note` rels, which carry `.md` — each call site appends/strips explicitly as shown. ✓

Known accepted limitations (documented in the spec): relations asserted solely by a deleted note survive while both endpoints live (no provenance — out of scope); chunk boundaries can still separate a candidate pair (inherent to any size cap).
