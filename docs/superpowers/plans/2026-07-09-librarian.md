# The Librarian Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One caretaker loop (`librarian.py`) that runs index → organize → cache → throttled dry-run consolidation → health checks → report, as a single scheduled CLI plus a read-only `librarian_status` MCP tool.

**Architecture:** The Librarian is a thin orchestrator over existing modules (`indexer`, `organizer`, `cache`, `consolidate`, `embeddings`). It owns one JSON state file (`librarian_state.json` in the per-vault state dir), appends a human-readable report to `Claude/Librarian.md`, and never contains indexing/organizing/caching logic of its own. Model selection for the two LLM-backed steps is added to `CliExtractor` (claude backend only).

**Tech Stack:** Python 3.11+, stdlib only (no new dependencies), pytest, FastMCP (existing).

## Global Constraints

- **No new dependencies.** Stdlib + existing deps only.
- **Constants in code, not config:** `CONSOLIDATE_MIN_NEW_ENTITIES = 15`, `CONSOLIDATE_MAX_AGE_DAYS = 14`, `REPORT_MAX_SWEEPS = 30`.
- **Env vars:** `TESSERACT_EXTRACT_MODEL` (default `"haiku"`), `TESSERACT_CONSOLIDATE_MODEL` (default `"sonnet"`) — passed as `--model` to the `claude` CLI backend only; the `codex` backend ignores them.
- **State file:** `librarian_state.json` in `indexer.state_dir(vault.root)`. No other new storage.
- **Report note:** `Claude/Librarian.md`, trimmed to the most recent 30 `## Sweep` sections.
- **Timestamps:** `"%Y-%m-%d %H:%M:%S"` (matches organizer).
- **Dry-run writes nothing:** no state file, no report note, no moves, no throttle reset. (The embeddings fallback cache in the state dir is a read-through cache and exempt.)
- **Tests must not download models or invoke real CLIs** — use fakes (`ClusterEmbedder`, `FakeExtractor`, `FakeConsolidator` patterns below). Windows dev box: run tests with `python -m pytest`.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/tesseract_mcp/extractor.py` | Modify | `model` param on `CliExtractor`; `extraction_extractor()` / `consolidation_extractor()` factories reading the env vars |
| `src/tesseract_mcp/indexer.py` | Modify | `main()` uses `extraction_extractor` |
| `src/tesseract_mcp/consolidate.py` | Modify | `main()` uses `consolidation_extractor` |
| `src/tesseract_mcp/embeddings.py` | Modify | New read-only `stale_notes(vault, state_root)` |
| `src/tesseract_mcp/librarian.py` | Create | State, throttle, health checks, sweep pipeline, report, CLI |
| `src/tesseract_mcp/server.py` | Modify | `_make_extractor`/`_make_consolidator` use factories; new `librarian_status` tool; onboard cheat-sheet line |
| `tests/test_extractor.py` | Modify | Model-flag + factory tests |
| `tests/test_embeddings.py` | Modify | `stale_notes` tests |
| `tests/test_librarian.py` | Create | All librarian tests |
| `tests/test_server.py` | Modify | Re-point one monkeypatch; `librarian_status` tests |
| `README.md`, `docs/ARCHITECTURE.md` | Modify | Librarian section + module-map rows |

---

### Task 1: Model selection for CLI extractor backends

**Files:**
- Modify: `src/tesseract_mcp/extractor.py`
- Modify: `src/tesseract_mcp/indexer.py:151-173` (main)
- Modify: `src/tesseract_mcp/consolidate.py:179-187` (main)
- Modify: `src/tesseract_mcp/server.py:261-262` (`_make_extractor`), `server.py:302-306` (`consolidate_graph`)
- Modify: `tests/test_server.py:171` (monkeypatch target)
- Test: `tests/test_extractor.py`

**Interfaces:**
- Consumes: existing `CliExtractor(backend, timeout, runner, which)`.
- Produces: `CliExtractor(backend=None, timeout=120, runner=subprocess.run, which=shutil.which, model: str | None = None)` with attribute `.model`; module functions `extraction_extractor(backend: str | None = None) -> CliExtractor` and `consolidation_extractor(backend: str | None = None) -> CliExtractor`. Server helper `_make_consolidator()`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_extractor.py`)

```python
class _Proc:
    def __init__(self, stdout='{"entities": [], "relations": []}'):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _capture_runner(captured):
    def run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _Proc()
    return run


def test_claude_backend_gets_model_flag():
    captured = {}
    ex = CliExtractor(backend="claude", model="haiku",
                      runner=_capture_runner(captured),
                      which=lambda e: "C:/bin/claude.exe")
    ex.extract("n.md", "some text")
    cmd = captured["cmd"]
    assert cmd[-2:] == ["--model", "haiku"]


def test_codex_backend_ignores_model():
    captured = {}
    ex = CliExtractor(backend="codex", model="haiku",
                      runner=_capture_runner(captured),
                      which=lambda e: "C:/bin/codex.exe")
    ex.extract("n.md", "some text")
    assert "--model" not in captured["cmd"]


def test_no_model_no_flag():
    captured = {}
    ex = CliExtractor(backend="claude",
                      runner=_capture_runner(captured),
                      which=lambda e: "C:/bin/claude.exe")
    ex.extract("n.md", "some text")
    assert "--model" not in captured["cmd"]


def test_factory_defaults(monkeypatch):
    monkeypatch.delenv("TESSERACT_EXTRACT_MODEL", raising=False)
    monkeypatch.delenv("TESSERACT_CONSOLIDATE_MODEL", raising=False)
    monkeypatch.setenv("TESSERACT_EXTRACTOR", "claude")
    assert extraction_extractor().model == "haiku"
    assert consolidation_extractor().model == "sonnet"


def test_factory_env_overrides(monkeypatch):
    monkeypatch.setenv("TESSERACT_EXTRACTOR", "claude")
    monkeypatch.setenv("TESSERACT_EXTRACT_MODEL", "sonnet")
    monkeypatch.setenv("TESSERACT_CONSOLIDATE_MODEL", "opus")
    assert extraction_extractor().model == "sonnet"
    assert consolidation_extractor().model == "opus"
```

Also extend the file's import line to include the two factories, e.g. `from tesseract_mcp.extractor import CliExtractor, ExtractorError, extraction_extractor, consolidation_extractor` (adapt to the file's existing import style).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_extractor.py -v`
Expected: FAIL — `ImportError: cannot import name 'extraction_extractor'`

- [ ] **Step 3: Implement in `extractor.py`**

In `CliExtractor.__init__`, add the `model` parameter and store it:

```python
    def __init__(
        self,
        backend: str | None = None,
        timeout: int = 120,
        runner=subprocess.run,
        which=shutil.which,
        model: str | None = None,
    ):
        self.backend = backend or os.environ.get("TESSERACT_EXTRACTOR", "codex")
        if self.backend not in self.COMMANDS:
            raise ExtractorError(f"Unknown backend: {self.backend}")
        self.timeout = timeout
        self._run = runner
        self._which = which
        self.model = model
```

Restructure `_resolve_cmd` so all three branches share the model suffix:

```python
    def _resolve_cmd(self) -> list[str]:
        exe, *args = self.COMMANDS[self.backend]
        resolved = self._which(exe)
        if not resolved:
            raise ExtractorError(f"{self.backend} CLI not found on PATH")
        low = resolved.lower()
        if low.endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c", resolved, *args]
        elif low.endswith(".ps1"):
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", resolved, *args]
        else:
            cmd = [resolved, *args]
        if self.model and self.backend == "claude":
            cmd += ["--model", self.model]
        return cmd
```

Add module-level factories (after the class):

```python
def extraction_extractor(backend: str | None = None) -> CliExtractor:
    """Backend for per-note entity extraction (high-volume → cheap model)."""
    return CliExtractor(
        backend=backend,
        model=os.environ.get("TESSERACT_EXTRACT_MODEL", "haiku"),
    )


def consolidation_extractor(backend: str | None = None) -> CliExtractor:
    """Backend for entity dedupe (judgment-heavy, rare → mid-tier model)."""
    return CliExtractor(
        backend=backend,
        model=os.environ.get("TESSERACT_CONSOLIDATE_MODEL", "sonnet"),
    )
```

- [ ] **Step 4: Update call sites**

`indexer.py`: change the import `from .extractor import CliExtractor, ExtractorError` to `from .extractor import ExtractorError, extraction_extractor`, and in `main()` replace `CliExtractor(backend=args.backend)` with `extraction_extractor(backend=args.backend)`.

`consolidate.py`: change `from .extractor import CliExtractor` to `from .extractor import consolidation_extractor`, and in `main()` replace `CliExtractor(backend=args.backend)` with `consolidation_extractor(backend=args.backend)`.

`server.py`: replace the `_make_extractor` definition with:

```python
def _make_extractor():
    return extraction_extractor()


def _make_consolidator():
    return consolidation_extractor()
```

Change the server import `from .extractor import CliExtractor` to `from .extractor import consolidation_extractor, extraction_extractor`, and in `consolidate_graph` replace `_make_extractor()` with `_make_consolidator()`.

`tests/test_server.py:171`: this test fakes the consolidation backend — change `monkeypatch.setattr(server, "_make_extractor", lambda: FakeBackend())` to `monkeypatch.setattr(server, "_make_consolidator", lambda: FakeBackend())`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_extractor.py tests/test_server.py tests/test_indexer.py tests/test_consolidate.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add src/tesseract_mcp/extractor.py src/tesseract_mcp/indexer.py src/tesseract_mcp/consolidate.py src/tesseract_mcp/server.py tests/test_extractor.py tests/test_server.py
git commit -m "feat(extractor): per-purpose model selection (haiku extract, sonnet consolidate)"
```

---

### Task 2: `embeddings.stale_notes` — read-only staleness scan

**Files:**
- Modify: `src/tesseract_mcp/embeddings.py`
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Consumes: existing private helpers `_scan_note_texts`, `_load_fallback_cache`, `sc_adapter.load_note_vectors`.
- Produces: `stale_notes(vault: Vault, state_root: Path) -> list[str]` — rel paths of notes whose next search pays an inline embedding. Never writes.

- [ ] **Step 1: Write the failing test** (append to `tests/test_embeddings.py`; it already defines `FakeEmbedder` and imports `get_note_vectors` — extend the import with `stale_notes`)

```python
def test_stale_notes_lists_only_uncached_edits(vault, vault_dir, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    get_note_vectors(vault, state, FakeEmbedder())  # warm the fallback cache
    assert stale_notes(vault, state) == []

    (vault_dir / "Daily.md").write_text("edited content\n", encoding="utf-8")
    assert stale_notes(vault, state) == ["Daily.md"]


def test_stale_notes_does_not_write(vault, vault_dir, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    stale_notes(vault, state)
    assert not (state / "fallback_embeddings.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_embeddings.py -v`
Expected: FAIL — `ImportError: cannot import name 'stale_notes'`

- [ ] **Step 3: Implement** (append to `embeddings.py`)

```python
def stale_notes(vault: Vault, state_root: Path) -> list[str]:
    """Rel paths of notes with no fresh Smart Connections vector AND no
    matching fallback-cache entry — the notes a search would embed inline.
    Read-only: never computes or caches anything."""
    sc_vectors = sc_adapter.load_note_vectors(vault)
    note_texts = _scan_note_texts(vault)
    fallback_cache = _load_fallback_cache(state_root)
    stale: list[str] = []
    for rel, text in note_texts.items():
        sc_entry = sc_vectors.get(rel)
        if sc_entry and sc_entry["fresh"]:
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = fallback_cache.get(rel)
        if cached and cached["hash"] == content_hash:
            continue
        stale.append(rel)
    return stale
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_embeddings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/embeddings.py tests/test_embeddings.py
git commit -m "feat(embeddings): read-only stale_notes scan for librarian health"
```

---

### Task 3: Librarian state + consolidation throttle

**Files:**
- Create: `src/tesseract_mcp/librarian.py`
- Test: `tests/test_librarian.py` (create)

**Interfaces:**
- Consumes: `indexer.state_dir(vault.root)`.
- Produces (used by every later task):
  - `TS_FMT = "%Y-%m-%d %H:%M:%S"`, `STATE_FILE = "librarian_state.json"`, `CONSOLIDATE_MIN_NEW_ENTITIES = 15`, `CONSOLIDATE_MAX_AGE_DAYS = 14`
  - `state_path(vault: Vault) -> Path`
  - `load_state(vault: Vault) -> dict` — default `{"last_sweep": None, "steps": {}, "health": {}, "errors": {}, "consolidation": {}}`
  - `save_state(vault: Vault, state: dict) -> None`
  - `should_consolidate(state: dict, current_entities: int, now: datetime) -> tuple[bool, str]`

- [ ] **Step 1: Write the failing tests** — create `tests/test_librarian.py`:

```python
"""Tests for the Librarian caretaker loop."""

from datetime import datetime, timedelta

import pytest

from tesseract_mcp import librarian
from tesseract_mcp.vault import Vault

NOW = datetime(2026, 7, 9, 12, 0, 0)


class FakeEmbedder:
    """Deterministic stand-in — no model download in tests."""

    def embed_batch(self, texts):
        return [[float(len(t)), 0.0] for t in texts]


@pytest.fixture(autouse=True)
def _no_model_downloads(monkeypatch):
    from tesseract_mcp import embeddings as embeddings_mod

    monkeypatch.setattr(embeddings_mod, "SentenceTransformerEmbedder", FakeEmbedder)


def _throttle_state(baseline: int, last_pass: datetime) -> dict:
    return {"consolidation": {"entities_at_last_pass": baseline,
                              "last_pass": last_pass.strftime(librarian.TS_FMT),
                              "pending_proposals": []}}


def test_constants_match_spec():
    assert librarian.CONSOLIDATE_MIN_NEW_ENTITIES == 15
    assert librarian.CONSOLIDATE_MAX_AGE_DAYS == 14


def test_load_state_default_when_missing(vault):
    state = librarian.load_state(vault)
    assert state["last_sweep"] is None
    assert state["consolidation"] == {}


def test_state_roundtrip(vault):
    state = librarian.load_state(vault)
    state["last_sweep"] = "2026-07-09 12:00:00"
    librarian.save_state(vault, state)
    assert librarian.load_state(vault)["last_sweep"] == "2026-07-09 12:00:00"


def test_first_pass_runs_when_entities_exist():
    due, reason = librarian.should_consolidate({"consolidation": {}}, 3, NOW)
    assert due
    assert reason == "first pass"


def test_no_entities_never_runs():
    due, _ = librarian.should_consolidate({"consolidation": {}}, 0, NOW)
    assert not due


def test_14_new_entities_skips():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 24, NOW + timedelta(days=1))
    assert not due


def test_15_new_entities_runs():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 25, NOW + timedelta(days=1))
    assert due


def test_age_trigger_requires_a_new_entity():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 10, NOW + timedelta(days=20))
    assert not due


def test_age_trigger_fires_at_14_days_with_one_new_entity():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 11, NOW + timedelta(days=14))
    assert due
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tesseract_mcp.librarian'`

- [ ] **Step 3: Create `src/tesseract_mcp/librarian.py`**

```python
"""The Librarian: one caretaker sweep over the vault's databases and files.

Thin orchestrator — index, organize, cache, throttled dry-run consolidation,
health checks, report. Owns no indexing/organizing logic of its own; see
docs/superpowers/specs/2026-07-09-librarian-design.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import indexer
from .vault import Vault

TS_FMT = "%Y-%m-%d %H:%M:%S"
STATE_FILE = "librarian_state.json"
CONSOLIDATE_MIN_NEW_ENTITIES = 15
CONSOLIDATE_MAX_AGE_DAYS = 14


def state_path(vault: Vault) -> Path:
    return indexer.state_dir(vault.root) / STATE_FILE


def load_state(vault: Vault) -> dict:
    p = state_path(vault)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"last_sweep": None, "steps": {}, "health": {},
            "errors": {}, "consolidation": {}}


def save_state(vault: Vault, state: dict) -> None:
    state_path(vault).write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_consolidate(
    state: dict, current_entities: int, now: datetime
) -> tuple[bool, str]:
    """Throttle: ≥15 new entities since the last pass, or ≥14 days with at
    least one. First pass runs as soon as any entity exists."""
    last = state.get("consolidation") or {}
    baseline = last.get("entities_at_last_pass")
    if baseline is None:
        if current_entities > 0:
            return True, "first pass"
        return False, "no entities yet"
    new = max(0, current_entities - baseline)
    if new >= CONSOLIDATE_MIN_NEW_ENTITIES:
        return True, f"{new} new entities (threshold {CONSOLIDATE_MIN_NEW_ENTITIES})"
    last_pass = datetime.strptime(last["last_pass"], TS_FMT)
    age_days = (now - last_pass).days
    if new >= 1 and age_days >= CONSOLIDATE_MAX_AGE_DAYS:
        return True, f"{age_days} days since last pass with {new} new entities"
    return False, (f"{new} new entities since last pass; "
                   f"threshold {CONSOLIDATE_MIN_NEW_ENTITIES}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/librarian.py tests/test_librarian.py
git commit -m "feat(librarian): state file and consolidation throttle"
```

---

### Task 4: Health checks

**Files:**
- Modify: `src/tesseract_mcp/librarian.py`
- Test: `tests/test_librarian.py`

**Interfaces:**
- Consumes: `indexer.load_manifest/scan_notes/db_path/state_dir`, `consolidate.gather_entities`, `embeddings.stale_notes`, sqlite over `graph.db` (`mentions(entity_path, note_path, evidence)` — `note_path` stored WITHOUT `.md`; `entities` table; both exclude `merged_into` stubs, matching `gather_entities`).
- Produces:
  - `check_manifest_drift(vault) -> dict` — `{"deleted_but_tracked": [rel...], "present_but_untracked": [rel...]}`
  - `check_orphaned_entities(vault) -> list[dict]` — `[{"entity": entity_path, "missing_note": note_path}]`; `[]` when db missing
  - `check_cache_consistency(vault) -> dict` — `{"db_entities": int | None, "md_entities": int, "consistent": bool}`
  - `count_pending_proposals(state, organize_report, consolidate_result) -> int`
  - `run_health(vault, state, organize_report, consolidate_result, errors) -> dict` — keys `stale_embeddings` (int), `manifest_drift`, `orphaned_entities`, `cache_consistency`, `pending_proposals`, `sweep_errors`; a failing check yields `{"error": "..."}` in its slot, others still run

- [ ] **Step 1: Write the failing tests** (append to `tests/test_librarian.py`)

```python
from tesseract_mcp import cache, indexer


def _entity_note(vault_dir, folder, name, etype, mentions=()):
    p = vault_dir / "Claude" / "Graph" / folder / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"---\nentity: {etype}\n---\n\n# {name}\n\nSummary.\n"]
    if mentions:
        lines.append("\n## Mentions\n")
        for note_path in mentions:
            stem = note_path.rsplit("/", 1)[-1]
            lines.append(f"- [[{note_path}|{stem}]] — evidence\n")
    p.write_text("".join(lines), encoding="utf-8")


def test_manifest_drift_detects_both_directions(vault):
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"]["Ghost.md"] = "deadbeef"
    indexer.save_manifest(manifest, vault.root)
    drift = librarian.check_manifest_drift(vault)
    assert "Ghost.md" in drift["deleted_but_tracked"]
    assert "Daily.md" in drift["present_but_untracked"]


def test_orphaned_entities_detects_missing_note(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization",
                 mentions=["Projects/Gone"])
    cache.rebuild(vault, indexer.db_path(vault.root))
    orphans = librarian.check_orphaned_entities(vault)
    assert orphans == [{"entity": "Claude/Graph/Organizations/Acme",
                        "missing_note": "Projects/Gone"}]


def test_orphaned_entities_clean_when_note_exists(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization",
                 mentions=["Projects/Sentinel ESG"])
    cache.rebuild(vault, indexer.db_path(vault.root))
    assert librarian.check_orphaned_entities(vault) == []


def test_orphaned_entities_empty_without_db(vault):
    assert librarian.check_orphaned_entities(vault) == []


def test_cache_consistency_flags_mismatch(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    cache.rebuild(vault, indexer.db_path(vault.root))
    assert librarian.check_cache_consistency(vault)["consistent"] is True
    _entity_note(vault_dir, "Topics", "Orbit", "topic")  # note added, no rebuild
    result = librarian.check_cache_consistency(vault)
    assert result == {"db_entities": 1, "md_entities": 2, "consistent": False}


def test_pending_proposals_counts_state_and_report():
    state = {"consolidation": {"pending_proposals": [{"canonical": "A"}]}}
    organize_report = {"proposals": [1, 2]}
    assert librarian.count_pending_proposals(state, organize_report, None) == 3
    ran = {"ran": True, "reason": "first pass", "proposed": [1]}
    assert librarian.count_pending_proposals(state, organize_report, ran) == 3


def test_run_health_survives_check_failure(vault, monkeypatch):
    def boom(v):
        raise RuntimeError("kaput")

    monkeypatch.setattr(librarian, "check_manifest_drift", boom)
    health = librarian.run_health(vault, {}, None, None, {})
    assert health["manifest_drift"] == {"error": "RuntimeError: kaput"}
    assert "orphaned_entities" in health
    assert health["stale_embeddings"] >= 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: new tests FAIL — `AttributeError: module ... has no attribute 'check_manifest_drift'`

- [ ] **Step 3: Implement** (append to `librarian.py`; extend the imports at the top to)

```python
import sqlite3

from . import consolidate as consolidate_mod
from . import embeddings as embeddings_mod
from . import indexer
```

```python
def check_manifest_drift(vault: Vault) -> dict:
    manifest = indexer.load_manifest(vault.root)
    current = indexer.scan_notes(vault)
    tracked, present = set(manifest["hashes"]), set(current)
    return {"deleted_but_tracked": sorted(tracked - present),
            "present_but_untracked": sorted(present - tracked)}


def check_orphaned_entities(vault: Vault) -> list[dict]:
    db = indexer.db_path(vault.root)
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT DISTINCT entity_path, note_path FROM mentions"
    ).fetchall()
    con.close()
    return [
        {"entity": entity_path, "missing_note": note_path}
        for entity_path, note_path in rows
        if not (vault.root / (note_path + ".md")).is_file()
    ]


def check_cache_consistency(vault: Vault) -> dict:
    md_count = len(consolidate_mod.gather_entities(vault))
    db = indexer.db_path(vault.root)
    if not db.exists():
        return {"db_entities": None, "md_entities": md_count,
                "consistent": md_count == 0}
    con = sqlite3.connect(db)
    db_count = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    con.close()
    return {"db_entities": db_count, "md_entities": md_count,
            "consistent": db_count == md_count}


def count_pending_proposals(
    state: dict, organize_report: dict | None, consolidate_result: dict | None
) -> int:
    n = len((organize_report or {}).get("proposals", []))
    if consolidate_result and consolidate_result.get("ran"):
        n += len(consolidate_result.get("proposed", []))
    else:
        n += len((state.get("consolidation") or {}).get("pending_proposals", []))
    return n


def run_health(
    vault: Vault,
    state: dict,
    organize_report: dict | None,
    consolidate_result: dict | None,
    errors: dict,
) -> dict:
    checks = {
        "stale_embeddings": lambda: len(
            embeddings_mod.stale_notes(vault, indexer.state_dir(vault.root))),
        "manifest_drift": lambda: check_manifest_drift(vault),
        "orphaned_entities": lambda: check_orphaned_entities(vault),
        "cache_consistency": lambda: check_cache_consistency(vault),
        "pending_proposals": lambda: count_pending_proposals(
            state, organize_report, consolidate_result),
        "sweep_errors": lambda: dict(errors),
    }
    out: dict = {}
    for name, fn in checks.items():
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — health must never kill the sweep
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    return out
```

Note: `run_health` calls module-level `check_manifest_drift(vault)` etc. by name (not local references) so tests can monkeypatch them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/librarian.py tests/test_librarian.py
git commit -m "feat(librarian): read-only health checks"
```

---

### Task 5: The sweep pipeline

**Files:**
- Modify: `src/tesseract_mcp/librarian.py`
- Test: `tests/test_librarian.py`

**Interfaces:**
- Consumes: `indexer.run(vault, extractor, ...) -> counts dict` (keys: processed, entities_created, entities_merged, mentions_added, relations_added, mentions_retracted, failed, skipped, remaining), `organizer.run_sweep(vault, embedder, apply) -> {"moved", "proposals", "skipped", "cache_rebuilt"}`, `consolidate.gather_entities` / `propose_merges`, `cache.rebuild`, factories from Task 1, throttle/health from Tasks 3–4.
- Produces:
  - `run_sweep(vault, extractor=None, consolidator=None, embedder=None, apply=True, now=None) -> dict` with shape `{"steps": {"index", "organize", "cache", "consolidate"}, "health": {...}, "errors": {step: "Type: msg"}, "applied": bool}`; a failed step's slot is `None`.
  - `_drain_index(vault, extractor) -> dict` (summed counts), `_index_preview(vault) -> {"pending": int}`, `MAX_INDEX_ROUNDS = 40`.
  - `cache` step dict: `{"rebuilt": bool, "by": "index" | "organize" | "librarian" | "none"}`.
  - `consolidate` step dict: `{"ran": bool, "reason": str, "proposed": list}`.
  - Persists state on `apply=True` (steps summary, health, errors, `last_sweep`; consolidation baseline only when the pass ran). Writes NOTHING on `apply=False`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_librarian.py` — that file already contains `NOW`, `FakeEmbedder`, the autouse `_no_model_downloads` fixture, and `_entity_note` from Tasks 3–4; reuse them, do not redefine)

```python
from tesseract_mcp.extractor import Extraction


class FakeExtractor:
    def extract(self, path, content):
        return Extraction(entities=[], relations=[])


class FakeConsolidator:
    def __init__(self, merges=None):
        self.merges = merges or []
        self.calls = 0

    def complete_json(self, prompt):
        self.calls += 1
        return {"merges": self.merges}


def _counts(**over):
    base = {"processed": 0, "entities_created": 0, "entities_merged": 0,
            "mentions_added": 0, "relations_added": 0,
            "mentions_retracted": 0, "failed": 0, "skipped": 0, "remaining": 0}
    base.update(over)
    return base


def _org_report(**over):
    base = {"moved": [], "proposals": [], "skipped": [], "cache_rebuilt": False}
    base.update(over)
    return base


def test_pipeline_runs_index_before_organize(vault, monkeypatch):
    calls = []
    monkeypatch.setattr(librarian.indexer, "run",
                        lambda v, e, **k: (calls.append("index"), _counts())[1])
    monkeypatch.setattr(librarian.organizer_mod, "run_sweep",
                        lambda v, emb, apply: (calls.append("organize"),
                                               _org_report())[1])
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    assert calls == ["index", "organize"]


def test_drain_index_loops_until_no_remaining(vault, monkeypatch):
    seq = [_counts(processed=25, remaining=5), _counts(processed=5)]
    monkeypatch.setattr(librarian.indexer, "run", lambda v, e, **k: seq.pop(0))
    totals = librarian._drain_index(vault, FakeExtractor())
    assert totals["processed"] == 30
    assert totals["remaining"] == 0


def test_step_failure_is_isolated(vault, monkeypatch):
    def boom(v, emb, apply):
        raise RuntimeError("organize kaput")

    monkeypatch.setattr(librarian.organizer_mod, "run_sweep", boom)
    result = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                 consolidator=FakeConsolidator(),
                                 embedder=FakeEmbedder(), now=NOW)
    assert result["errors"]["organize"] == "RuntimeError: organize kaput"
    assert result["steps"]["organize"] is None
    assert result["steps"]["consolidate"] is not None   # later steps still ran
    assert result["health"]["sweep_errors"]["organize"]


def test_consolidation_first_pass_sets_baseline(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    _entity_note(vault_dir, "Organizations", "Acme Corp", "organization")
    fake = FakeConsolidator(merges=[{"type": "organization",
                                     "canonical": "Acme",
                                     "duplicates": ["Acme Corp"]}])
    result = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                 consolidator=fake,
                                 embedder=FakeEmbedder(), now=NOW)
    step = result["steps"]["consolidate"]
    assert step["ran"] and step["reason"] == "first pass"
    assert step["proposed"] == [{"type": "organization", "canonical": "Acme",
                                 "duplicates": ["Acme Corp"]}]
    state = librarian.load_state(vault)
    assert state["consolidation"]["entities_at_last_pass"] == 2
    assert state["consolidation"]["pending_proposals"] == step["proposed"]


def test_consolidation_skipped_below_threshold(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    fake = FakeConsolidator()
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW)
    assert fake.calls == 1  # first pass ran
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW)
    assert fake.calls == 1  # second sweep: 0 new entities → throttled


def test_apply_sweep_saves_state(vault):
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    state = librarian.load_state(vault)
    assert state["last_sweep"] == NOW.strftime(librarian.TS_FMT)
    assert "index" in state["steps"]
    assert "stale_embeddings" in state["health"]


def test_dry_run_touches_nothing(vault, vault_dir):
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)  # warm caches first
    snapshot = {p: p.read_bytes()
                for p in sorted(vault_dir.rglob("*")) if p.is_file()}
    state_before = librarian.load_state(vault)

    result = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                 consolidator=FakeConsolidator(),
                                 embedder=FakeEmbedder(), apply=False, now=NOW)
    assert result["applied"] is False
    assert result["steps"]["index"] == {"pending": 0}
    after = {p: p.read_bytes()
             for p in sorted(vault_dir.rglob("*")) if p.is_file()}
    assert after == snapshot
    assert librarian.load_state(vault) == state_before  # no throttle reset
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: new tests FAIL — `AttributeError: ... no attribute 'run_sweep'`

- [ ] **Step 3: Implement** (append to `librarian.py`; extend imports with)

```python
from . import cache
from . import extractor as extractor_mod
from . import organizer as organizer_mod
```

```python
MAX_INDEX_ROUNDS = 40


def _drain_index(vault: Vault, extractor) -> dict:
    """Run indexer batches until nothing remains (bounded)."""
    totals: dict | None = None
    for _ in range(MAX_INDEX_ROUNDS):
        counts = indexer.run(vault, extractor)
        if totals is None:
            totals = dict(counts)
        else:
            for key, val in counts.items():
                if key not in ("remaining", "skipped"):
                    totals[key] += val
            totals["remaining"] = counts["remaining"]
            totals["skipped"] = counts["skipped"]
        if counts["remaining"] == 0:
            break
    return totals or {}


def _index_preview(vault: Vault) -> dict:
    """Dry-run index: count pending notes without extracting or writing."""
    manifest = indexer.load_manifest(vault.root)
    current = indexer.scan_notes(vault)
    pending = [
        rel for rel, digest in current.items()
        if manifest["hashes"].get(rel) != digest or manifest["failures"].get(rel)
    ]
    return {"pending": len(pending)}


def _ensure_cache(vault: Vault, result: dict) -> dict:
    idx = result["steps"].get("index") or {}
    org = result["steps"].get("organize") or {}
    if idx.get("processed"):
        return {"rebuilt": True, "by": "index"}
    if org.get("cache_rebuilt"):
        return {"rebuilt": True, "by": "organize"}
    db = indexer.db_path(vault.root)
    if not db.exists():
        if not result["applied"]:
            return {"rebuilt": False, "by": "none"}
        cache.rebuild(vault, db)
        return {"rebuilt": True, "by": "librarian"}
    return {"rebuilt": False, "by": "none"}


def _consolidate_step(
    vault: Vault, state: dict, consolidator, now: datetime, apply: bool
) -> dict:
    entities = consolidate_mod.gather_entities(vault)
    due, reason = should_consolidate(state, len(entities), now)
    if not due:
        return {"ran": False, "reason": reason, "proposed": []}
    if consolidator is None:
        consolidator = extractor_mod.consolidation_extractor()
    proposed = consolidate_mod.propose_merges(consolidator, entities)
    if apply:
        state["consolidation"] = {
            "entities_at_last_pass": len(entities),
            "last_pass": now.strftime(TS_FMT),
            "pending_proposals": proposed,
        }
    return {"ran": True, "reason": reason, "proposed": proposed}


def _step(result: dict, name: str, fn) -> None:
    try:
        result["steps"][name] = fn()
    except Exception as e:  # noqa: BLE001 — one step must not kill the sweep
        result["steps"][name] = None
        result["errors"][name] = f"{type(e).__name__}: {e}"


def _summarize_steps(steps: dict) -> dict:
    out: dict = {}
    idx = steps.get("index")
    out["index"] = idx if idx is None else {
        k: idx[k] for k in ("processed", "failed", "remaining", "pending")
        if k in idx
    }
    org = steps.get("organize")
    out["organize"] = org if org is None else {
        "moved": len(org["moved"]), "proposals": len(org["proposals"]),
        "skipped": len(org["skipped"]),
    }
    out["cache"] = steps.get("cache")
    con = steps.get("consolidate")
    out["consolidate"] = con if con is None else {
        "ran": con["ran"], "reason": con["reason"],
        "proposed": len(con["proposed"]),
    }
    return out


def run_sweep(
    vault: Vault,
    extractor=None,
    consolidator=None,
    embedder=None,
    apply: bool = True,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    state = load_state(vault)
    result: dict = {"steps": {}, "health": {}, "errors": {}, "applied": apply}

    if apply:
        if extractor is None:
            extractor = extractor_mod.extraction_extractor()
        _step(result, "index", lambda: _drain_index(vault, extractor))
    else:
        _step(result, "index", lambda: _index_preview(vault))

    if embedder is None:
        embedder = embeddings_mod.SentenceTransformerEmbedder()
    _step(result, "organize",
          lambda: organizer_mod.run_sweep(vault, embedder, apply=apply))

    _step(result, "cache", lambda: _ensure_cache(vault, result))

    _step(result, "consolidate",
          lambda: _consolidate_step(vault, state, consolidator, now, apply))

    result["health"] = run_health(
        vault, state, result["steps"].get("organize"),
        result["steps"].get("consolidate"), result["errors"],
    )

    if apply:
        state["last_sweep"] = now.strftime(TS_FMT)
        state["steps"] = _summarize_steps(result["steps"])
        state["health"] = result["health"]
        state["errors"] = dict(result["errors"])
        save_state(vault, state)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/librarian.py tests/test_librarian.py
git commit -m "feat(librarian): sweep pipeline with step isolation and dry-run"
```

---

### Task 6: Report — format, append, trim

**Files:**
- Modify: `src/tesseract_mcp/librarian.py`
- Test: `tests/test_librarian.py`

**Interfaces:**
- Consumes: `run_sweep` result dict (Task 5), `vault.read/write/append` (Claude/ paths need no confirm flag).
- Produces: `LIBRARIAN_NOTE = "Claude/Librarian.md"`, `REPORT_MAX_SWEEPS = 30`, `format_report(result: dict, now: datetime) -> str` (section starting `## Sweep YYYY-MM-DD HH:MM`), `write_report(vault, section: str) -> None`. `run_sweep` gains one line: on `apply=True`, after `save_state`, `write_report(vault, format_report(result, now))`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_librarian.py` — reuse `NOW`, `_counts`, `_org_report`, `FakeExtractor`, `FakeConsolidator`, `FakeEmbedder` already defined by Tasks 3–5 in this file)

```python
def test_format_report_covers_all_steps():
    result = {
        "applied": True,
        "steps": {
            "index": _counts(processed=3, failed=1),
            "organize": _org_report(moved=[{"from": "A.md"}],
                                    proposals=[1, 2], skipped=[1]),
            "cache": {"rebuilt": True, "by": "index"},
            "consolidate": {"ran": False, "reason": "2 new entities since last pass; threshold 15", "proposed": []},
        },
        "health": {
            "stale_embeddings": 0,
            "manifest_drift": {"deleted_but_tracked": [], "present_but_untracked": []},
            "orphaned_entities": [{"entity": "E", "missing_note": "N"}],
            "cache_consistency": {"db_entities": 1, "md_entities": 1, "consistent": True},
            "pending_proposals": 2,
            "sweep_errors": {},
        },
        "errors": {},
    }
    text = librarian.format_report(result, NOW)
    assert text.startswith("## Sweep 2026-07-09 12:00\n")
    assert "- index: processed 3, failed 1, remaining 0\n" in text
    assert "- organize: moved 1, proposals 2, skipped 1\n" in text
    assert "- cache: rebuilt (index)\n" in text
    assert "- consolidate: skipped (2 new entities since last pass; threshold 15)\n" in text
    assert "orphaned_entities 1 ⚠" in text
    assert "stale_embeddings 0 ✓" in text
    assert "- errors: none\n" in text


def test_format_report_failed_step_and_errors():
    result = {"applied": True,
              "steps": {"index": None, "organize": _org_report(),
                        "cache": {"rebuilt": False, "by": "none"},
                        "consolidate": {"ran": True, "reason": "first pass",
                                        "proposed": [1]}},
              "health": {"stale_embeddings": 0, "manifest_drift": {},
                         "orphaned_entities": [], "cache_consistency":
                         {"consistent": True}, "pending_proposals": 1,
                         "sweep_errors": {"index": "RuntimeError: x"}},
              "errors": {"index": "RuntimeError: x"}}
    text = librarian.format_report(result, NOW)
    assert "- index: FAILED\n" in text
    assert "- consolidate: ran (first pass) — 1 merge proposals\n" in text
    assert "- errors: index: RuntimeError: x\n" in text


def test_write_report_seeds_and_appends(vault):
    librarian.write_report(vault, "## Sweep 2026-07-09 12:00\n- x\n")
    text = vault.read(librarian.LIBRARIAN_NOTE)
    assert text.startswith("# Librarian")
    assert "## Sweep 2026-07-09 12:00" in text


def test_report_trims_to_max_sweeps(vault):
    for i in range(33):
        librarian.write_report(vault, f"## Sweep 2026-07-09 12:{i:02d}\n- x\n")
    text = vault.read(librarian.LIBRARIAN_NOTE)
    assert text.count("## Sweep") == librarian.REPORT_MAX_SWEEPS
    assert "12:02" not in text
    assert "12:03" in text
    assert "12:32" in text


def test_apply_sweep_writes_report(vault):
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    text = vault.read(librarian.LIBRARIAN_NOTE)
    assert "## Sweep 2026-07-09 12:00" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: new tests FAIL — `AttributeError: ... no attribute 'format_report'`

- [ ] **Step 3: Implement** (append to `librarian.py`; add `from .vault import Vault, VaultError` to imports)

```python
LIBRARIAN_NOTE = "Claude/Librarian.md"
REPORT_MAX_SWEEPS = 30
_NOTE_SEED = ("# Librarian\n\nCaretaker sweep reports (newest last). "
              "See constitution.\n")


def format_report(result: dict, now: datetime) -> str:
    steps = result["steps"]
    lines = [f"## Sweep {now.strftime('%Y-%m-%d %H:%M')}\n"]

    idx = steps.get("index")
    if idx is None:
        lines.append("- index: FAILED\n")
    elif "pending" in idx:
        lines.append(f"- index: {idx['pending']} pending (dry-run)\n")
    else:
        lines.append(f"- index: processed {idx['processed']}, "
                     f"failed {idx['failed']}, remaining {idx['remaining']}\n")

    org = steps.get("organize")
    if org is None:
        lines.append("- organize: FAILED\n")
    else:
        lines.append(f"- organize: moved {len(org['moved'])}, "
                     f"proposals {len(org['proposals'])}, "
                     f"skipped {len(org['skipped'])}\n")

    cch = steps.get("cache")
    if cch is None:
        lines.append("- cache: FAILED\n")
    elif cch["rebuilt"]:
        lines.append(f"- cache: rebuilt ({cch['by']})\n")
    else:
        lines.append("- cache: fresh, no rebuild needed\n")

    con = steps.get("consolidate")
    if con is None:
        lines.append("- consolidate: FAILED\n")
    elif con["ran"]:
        lines.append(f"- consolidate: ran ({con['reason']}) — "
                     f"{len(con['proposed'])} merge proposals\n")
    else:
        lines.append(f"- consolidate: skipped ({con['reason']})\n")

    h = result["health"]

    def mark(ok: bool) -> str:
        return "✓" if ok else "⚠"

    stale = h.get("stale_embeddings", -1)
    stale_n = stale if isinstance(stale, int) else -1
    drift = h.get("manifest_drift", {})
    drift_n = (len(drift.get("deleted_but_tracked", []))
               + len(drift.get("present_but_untracked", []))
               if isinstance(drift, dict) and "error" not in drift else -1)
    orphans = h.get("orphaned_entities", [])
    orph_n = len(orphans) if isinstance(orphans, list) else -1
    cc = h.get("cache_consistency", {})
    consistent = isinstance(cc, dict) and cc.get("consistent", False)
    lines.append(
        f"- health: stale_embeddings {stale_n} {mark(stale_n == 0)} | "
        f"manifest_drift {drift_n} {mark(drift_n == 0)} | "
        f"orphaned_entities {orph_n} {mark(orph_n == 0)} | "
        f"cache_consistency {mark(consistent)} | "
        f"pending_proposals {h.get('pending_proposals', 0)}\n")

    errs = result["errors"]
    if errs:
        lines.append("- errors: "
                     + ", ".join(f"{k}: {v}" for k, v in errs.items()) + "\n")
    else:
        lines.append("- errors: none\n")
    return "".join(lines)


def write_report(vault: Vault, section: str) -> None:
    try:
        text = vault.read(LIBRARIAN_NOTE)
    except VaultError:
        text = _NOTE_SEED
    text = text.rstrip("\n") + "\n\n" + section
    header, *sweeps = text.split("\n## Sweep ")
    if len(sweeps) > REPORT_MAX_SWEEPS:
        sweeps = sweeps[-REPORT_MAX_SWEEPS:]
    text = header + "".join("\n## Sweep " + s for s in sweeps)
    if not text.endswith("\n"):
        text += "\n"
    vault.write(LIBRARIAN_NOTE, text, overwrite=True)
```

And in `run_sweep`, immediately after `save_state(vault, state)` add:

```python
        write_report(vault, format_report(result, now))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: PASS (including the Task 5 dry-run test — report is only written on apply)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/librarian.py tests/test_librarian.py
git commit -m "feat(librarian): human-readable sweep report in Claude/Librarian.md"
```

---

### Task 7: CLI entry point

**Files:**
- Modify: `src/tesseract_mcp/librarian.py`
- Test: `tests/test_librarian.py`

**Interfaces:**
- Consumes: `run_sweep`, `format_report`.
- Produces: `main() -> None`; `python -m tesseract_mcp.librarian <vault> [--dry-run]`; prints JSON result (dry-run also prints the formatted report first); `SystemExit(1)` when `result["errors"]` is non-empty. Module gains the `if __name__ == "__main__": main()` guard.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_librarian.py` — `librarian.organizer_mod` and the autouse embedder fake already exist in this file from earlier tasks)

```python
import sys


def test_cli_dry_run_prints_and_exits_zero(vault_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["librarian", str(vault_dir), "--dry-run"])
    librarian.main()
    out = capsys.readouterr().out
    assert "## Sweep" in out
    assert '"applied": false' in out


def test_cli_exits_nonzero_on_step_failure(vault_dir, monkeypatch):
    def boom(v, emb, apply):
        raise RuntimeError("kaput")

    monkeypatch.setattr(librarian.organizer_mod, "run_sweep", boom)
    monkeypatch.setattr(sys, "argv", ["librarian", str(vault_dir), "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        librarian.main()
    assert exc.value.code == 1
```

(Both use `--dry-run`, so no extractor/consolidator CLI is ever invoked; the autouse fixture already fakes the embedder class.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: new tests FAIL — `AttributeError: ... no attribute 'main'`

- [ ] **Step 3: Implement** (append to `librarian.py`; add `import argparse` to imports)

```python
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Librarian caretaker sweep: index, organize, cache, "
                    "consolidation proposals, health report.")
    parser.add_argument("vault", help="Path to the Obsidian vault root")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report without writing anything")
    args = parser.parse_args()
    result = run_sweep(Vault(args.vault), apply=not args.dry_run)
    if args.dry_run:
        print(format_report(result, datetime.now()))
    print(json.dumps(result, indent=2, default=str))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_librarian.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/librarian.py tests/test_librarian.py
git commit -m "feat(librarian): CLI sweep entry point with dry-run and exit codes"
```

---

### Task 8: `librarian_status` MCP tool

**Files:**
- Modify: `src/tesseract_mcp/librarian.py` (add `status`)
- Modify: `src/tesseract_mcp/server.py` (tool + onboard cheat-sheet line)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `state_path`, `load_state`.
- Produces: `librarian.status(vault: Vault) -> dict` — parsed state file, or `{"status": "no sweep yet"}` when missing; server tool `librarian_status() -> dict`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_server.py`, following the file's existing pattern for pointing the server at a vault — the existing tests set `TESSERACT_VAULT_PATH` via fixture/monkeypatch and reset `srv._vault`; mirror that. Reference shape:)

```python
def test_librarian_status_before_first_sweep(vault_dir, monkeypatch):
    import tesseract_mcp.server as srv

    monkeypatch.setenv("TESSERACT_VAULT_PATH", str(vault_dir))
    srv._vault = None
    try:
        assert server.librarian_status() == {"status": "no sweep yet"}
    finally:
        srv._vault = None


def test_librarian_status_after_sweep(vault_dir, monkeypatch):
    import tesseract_mcp.server as srv
    from tesseract_mcp import librarian
    from tesseract_mcp.vault import Vault

    monkeypatch.setenv("TESSERACT_VAULT_PATH", str(vault_dir))
    srv._vault = None
    try:
        state = librarian.load_state(Vault(vault_dir))
        state["last_sweep"] = "2026-07-09 12:00:00"
        librarian.save_state(Vault(vault_dir), state)
        assert server.librarian_status()["last_sweep"] == "2026-07-09 12:00:00"
    finally:
        srv._vault = None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py -v`
Expected: new tests FAIL — `AttributeError: module ... has no attribute 'librarian_status'`

- [ ] **Step 3: Implement**

Append to `librarian.py`:

```python
def status(vault: Vault) -> dict:
    """Read-only view of the last sweep for the librarian_status tool."""
    if not state_path(vault).exists():
        return {"status": "no sweep yet"}
    return load_state(vault)
```

In `server.py`: add `librarian as librarian_mod` to the existing `from . import ...` line, and register the tool next to `organize_vault`:

```python
@mcp.tool()
def librarian_status() -> dict:
    """Last Librarian caretaker sweep: per-step results, health checks
    (stale embeddings, manifest drift, orphaned entities, cache consistency),
    and pending proposal counts. Read-only — the sweep itself runs on a
    schedule via `python -m tesseract_mcp.librarian`."""
    return librarian_mod.status(get_vault())
```

In `onboard()`'s `tools` list add the line:

```python
        "librarian_status() — last caretaker sweep + health report",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py tests/test_librarian.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/librarian.py src/tesseract_mcp/server.py tests/test_server.py
git commit -m "feat(server): read-only librarian_status tool"
```

---

### Task 9: Docs, full suite, wrap-up

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`**

1. Module map table — add one row, alphabetically placed:

```markdown
| `librarian.py` | Caretaker sweep: orchestrates index → organize → cache → throttled consolidation proposals → health checks; reports to `Claude/Librarian.md`. |
```

2. Add a section after "## 5. The organizer" titled `## 6. The Librarian` (renumber the later sections) covering, in prose matching the doc's voice: the single scheduled entry point `python -m tesseract_mcp.librarian <vault> [--dry-run]` replacing the separate indexer/organizer schedules; step isolation and non-zero exit on failure; the 15-new-entities-or-14-days consolidation throttle (dry-run proposals only); the six health checks; `librarian_state.json` + the 30-sweep report trim; and the model env vars `TESSERACT_EXTRACT_MODEL` (default `haiku`) / `TESSERACT_CONSOLIDATE_MODEL` (default `sonnet`), claude backend only.

3. In the mermaid diagram's "Provision and organize" subgraph, add:

```
        librarian[librarian.py — caretaker sweep]
```

and the edges:

```
    librarian --> indexer
    librarian --> organizer
    librarian --> consolidate
```

- [ ] **Step 2: Update `README.md`**

Add a short subsection where the organizer's scheduled-run instructions live: the Librarian is now the single scheduled task (`python -m tesseract_mcp.librarian <vault>`); first run against a real vault MUST be `--dry-run` and human-reviewed (same operational rule as the organizer); reports land in `Claude/Librarian.md`; `librarian_status` MCP tool reads the last sweep. Mention the two model env vars and their defaults.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest -q`
Expected: ALL PASS, no warnings introduced by new code

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ARCHITECTURE.md
git commit -m "docs: Librarian caretaker loop (architecture section, README ops notes)"
```
