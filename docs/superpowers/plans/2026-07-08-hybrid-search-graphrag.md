# Hybrid Search & Relational Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `search_brain`'s substring scan with real hybrid retrieval (BM25 + reused Smart Connections vectors, fused with RRF), and add a `context_bundle` tool that returns ranked hits plus their graph context in one call.

**Architecture:** Three new small modules — `bm25.py` (keyword ranking), `sc_adapter.py` (reads Smart Connections' `.smart-env/multi/*.ajson` embeddings) and `embeddings.py` (same-model local fallback for notes Smart Connections hasn't embedded) — feed a `hybrid.py` fusion layer via Reciprocal Rank Fusion. `server.py`'s `search_brain` calls the new fusion layer instead of `search.search()`; a new `context_bundle` tool composes it with the existing entity-graph cache. `indexer.py` gains a vault-hash-keyed state directory and an embedding-freshness step in its existing incremental run.

**Tech Stack:** Python 3.11+, `rank-bm25`, `sentence-transformers` (loads `TaylorAI/bge-micro-v2` locally — same model Smart Connections uses), `numpy` for cosine similarity, pytest.

## Global Constraints

- Fallback embeddings MUST use `TaylorAI/bge-micro-v2` — the exact model Smart Connections uses (confirmed in `.smart-env/smart_env.json`) — never a different model, or the vector space is incomparable.
- No cloud embedding APIs — everything local (spec: 2026-07-08-hybrid-search-graphrag-design.md).
- `search_brain`'s tool name and parameter signature (`query, tags=None, folder=None, limit=20`) do not change.
- Every test file gets `TESSERACT_STATE_DIR` isolation already via `tests/conftest.py`'s autouse `_isolated_machine_state` fixture and per-file autouse fixtures — new tests must not touch the real `~/.tesseract-mcp`.
- BM25 and vector ranking are computed **in-memory per query** from the current vault scan, not persisted to disk — `rank-bm25` has no incremental-update API and a personal vault (hundreds to low-thousands of notes) is cheap to re-rank per call. This is a deliberate simplification of the design spec's "rebuilt incrementally" language: the *embedding* step is what's expensive and incremental (Task 4), ranking itself is not.

---

## Task 1: Vault-scoped state directory

**Files:**
- Modify: `src/tesseract_mcp/indexer.py:22-34` (`state_dir`, `db_path`, `_manifest_path`)
- Modify: `src/tesseract_mcp/indexer.py` (all internal callers of the above three functions)
- Modify: `src/tesseract_mcp/server.py` (calls to `indexer.db_path()`)
- Test: `tests/test_indexer.py`

**Interfaces:**
- Produces: `state_dir(vault_root: str | Path | None = None) -> Path`, `db_path(vault_root: str | Path | None = None) -> Path` — both now accept an optional vault root; when `TESSERACT_STATE_DIR` is not set, the returned path is `~/.tesseract-mcp/<12-char-sha256-of-resolved-vault-root>/`. When `vault_root` is omitted and no override env var is set, falls back to `TESSERACT_VAULT_PATH`; raises `VaultError` if neither is available.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indexer.py` (add `from pathlib import Path` to the existing imports, and `from tesseract_mcp.vault import VaultError`):

```python
def test_state_dir_keyed_by_vault_root(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSERACT_STATE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    vault_a.mkdir()
    vault_b.mkdir()
    dir_a = indexer.state_dir(vault_a)
    dir_b = indexer.state_dir(vault_b)
    assert dir_a != dir_b
    assert dir_a.parent == dir_b.parent  # both live under ~/.tesseract-mcp
    assert indexer.state_dir(vault_a) == dir_a  # stable for the same root


def test_state_dir_falls_back_to_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSERACT_STATE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault_root = tmp_path / "vault-c"
    vault_root.mkdir()
    monkeypatch.setenv("TESSERACT_VAULT_PATH", str(vault_root))
    assert indexer.state_dir() == indexer.state_dir(vault_root)


def test_state_dir_requires_vault_root_or_env(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSERACT_STATE_DIR", raising=False)
    monkeypatch.delenv("TESSERACT_VAULT_PATH", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(VaultError, match="TESSERACT_VAULT_PATH"):
        indexer.state_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indexer.py -k state_dir -v`
Expected: FAIL — `state_dir()` currently takes no arguments (`TypeError`), and doesn't raise `VaultError`.

- [ ] **Step 3: Implement the vault-hash keying**

In `src/tesseract_mcp/indexer.py`, replace lines 1-34 (imports through `db_path`) with:

```python
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from . import cache
from .extractor import CliExtractor, ExtractorError
from .graphstore import GRAPH_ROOT, GraphStore
from .search import SKIP_DIRS
from .vault import Vault, VaultError

DEFAULT_IGNORE = ("copilot",)
DEFAULT_BATCH = 25
MAX_ATTEMPTS = 3


def state_dir(vault_root: str | Path | None = None) -> Path:
    override = os.environ.get("TESSERACT_STATE_DIR")
    if override:
        d = Path(override)
    else:
        root = vault_root or os.environ.get("TESSERACT_VAULT_PATH")
        if not root:
            raise VaultError(
                "Cannot determine state directory: pass vault_root or set "
                "TESSERACT_VAULT_PATH."
            )
        digest = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
        d = Path.home() / ".tesseract-mcp" / digest
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(vault_root: str | Path | None = None) -> Path:
    return state_dir(vault_root) / "manifest.json"


def db_path(vault_root: str | Path | None = None) -> Path:
    return state_dir(vault_root) / "graph.db"
```

Update `load_manifest` / `save_manifest` to accept and forward the same optional parameter:

```python
def load_manifest(vault_root: str | Path | None = None) -> dict:
    p = _manifest_path(vault_root)
    if p.exists():
        manifest = json.loads(p.read_text(encoding="utf-8"))
    else:
        manifest = {"hashes": {}, "failures": {}}
    for rel, val in list(manifest.get("failures", {}).items()):
        if isinstance(val, str):
            manifest["failures"][rel] = {"error": val, "attempts": 1}
    return manifest


def save_manifest(manifest: dict, vault_root: str | Path | None = None) -> None:
    _manifest_path(vault_root).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
```

In `run()`, update the three call sites to pass `vault.root`:

```python
    manifest = load_manifest(vault.root)
    ...
    save_manifest(manifest, vault.root)
    if counts["processed"] or not db_path(vault.root).exists():
        cache.rebuild(vault, db_path(vault.root))
```

In `_retract_stale_mentions(vault, store, rel)`, update:

```python
def _retract_stale_mentions(vault: Vault, store: GraphStore, rel: str) -> int:
    db = db_path(vault.root)
    if not db.exists():
        return 0
    ...
```

In `main()`, update the rebuild-only branch:

```python
    if args.rebuild_only:
        cache.rebuild(Vault(args.vault), db_path(args.vault))
        print(json.dumps({"rebuilt": True, "db": str(db_path(args.vault))}))
        return
```

In `src/tesseract_mcp/server.py`, update `_graph_db()` and `onboard()`'s two calls to `indexer.db_path()`:

```python
def _graph_db():
    db = indexer.db_path(get_vault().root)
    if not db.exists():
        raise VaultError("Graph cache not built yet — run index_brain first.")
    return db
```

and in `onboard()`:

```python
    db = indexer.db_path(get_vault().root)
```

- [ ] **Step 4: Run all tests to verify nothing broke**

Run: `pytest tests/ -v`
Expected: PASS — all existing tests set `TESSERACT_STATE_DIR` via autouse fixtures, so the override branch is taken and zero-arg calls to `state_dir()`/`db_path()` in existing tests remain valid.

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/indexer.py src/tesseract_mcp/server.py tests/test_indexer.py
git commit -m "feat(state): key state_dir by vault root hash

A second vault (e.g. a future company/personal split) no longer
collides with this one's manifest/graph.db in ~/.tesseract-mcp."
```

---

## Task 2: BM25 keyword ranking module

**Files:**
- Create: `src/tesseract_mcp/bm25.py`
- Modify: `pyproject.toml` (add `rank-bm25` dependency)
- Test: `tests/test_bm25.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `tokenize(text: str) -> list[str]`, `rank(corpus: dict[str, str], query: str, limit: int = 50) -> list[tuple[str, float]]` — `corpus` maps note path to its raw text; returns `(path, score)` pairs sorted by descending score, zero-score entries excluded. Later tasks (Task 6) consume `rank()`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, change the `dependencies` list:

```toml
dependencies = [
    "mcp>=1.2.0",
    "pyyaml>=6.0",
    "rank-bm25>=0.2.2",
]
```

Run: `.venv\Scripts\pip install -e .`
Expected: `rank-bm25` installs successfully.

- [ ] **Step 2: Write the failing test**

Create `tests/test_bm25.py`:

```python
from tesseract_mcp.bm25 import rank, tokenize


def test_tokenize_lowercases_and_splits():
    assert tokenize("Sentinel-ESG Pipeline!") == ["sentinel", "esg", "pipeline"]


def test_rank_favors_exact_term_over_no_match():
    corpus = {
        "a.md": "the sentinel esg pipeline ingests incident data",
        "b.md": "an unrelated note about weather patterns",
    }
    results = rank(corpus, "sentinel pipeline")
    assert [p for p, _ in results] == ["a.md"]


def test_rank_favors_rare_term_match():
    corpus = {
        "common.md": "the the the the the project project",
        "rare.md": "zephyr appears exactly once here",
    }
    results = rank(corpus, "zephyr")
    assert results and results[0][0] == "rare.md"


def test_rank_empty_corpus_returns_empty():
    assert rank({}, "anything") == []


def test_rank_no_match_returns_empty():
    corpus = {"a.md": "completely unrelated content"}
    assert rank(corpus, "zzzznomatch") == []


def test_rank_respects_limit():
    corpus = {f"n{i}.md": "shared keyword appears here" for i in range(10)}
    assert len(rank(corpus, "shared", limit=3)) == 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_bm25.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesseract_mcp.bm25'`

- [ ] **Step 4: Implement**

Create `src/tesseract_mcp/bm25.py`:

```python
"""In-memory BM25 keyword ranking over vault notes.

Rebuilt fresh per query from the current vault scan rather than persisted:
rank-bm25 has no incremental-update API, and a personal vault (hundreds to
low-thousands of notes) is cheap to re-tokenize and re-rank on every call.
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def rank(corpus: dict[str, str], query: str, limit: int = 50) -> list[tuple[str, float]]:
    if not corpus:
        return []
    paths = list(corpus.keys())
    tokenized_docs = [tokenize(corpus[p]) for p in paths]
    bm25 = BM25Okapi(tokenized_docs)
    scores = bm25.get_scores(tokenize(query))
    ranked = sorted(zip(paths, scores), key=lambda pair: pair[1], reverse=True)
    return [(p, s) for p, s in ranked if s > 0][:limit]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_bm25.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/tesseract_mcp/bm25.py tests/test_bm25.py
git commit -m "feat(search): add in-memory BM25 ranking module"
```

---

## Task 3: Smart Connections embedding adapter

**Files:**
- Create: `src/tesseract_mcp/sc_adapter.py`
- Test: `tests/test_sc_adapter.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `SMART_ENV_DIR = ".smart-env"`, `MODEL_KEY = "TaylorAI/bge-micro-v2"`, `load_note_vectors(vault: Vault, model_key: str = MODEL_KEY) -> dict[str, dict]` — returns `{note_path: {"vec": list[float], "fresh": bool}}` for every note Smart Connections has ever embedded, where `fresh` is `True` only if the embedding's `last_embed.at` timestamp is at or after the note file's current mtime. Task 4 consumes this to decide which notes need fallback embedding.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sc_adapter.py`:

```python
import json
import time

from tesseract_mcp.sc_adapter import load_note_vectors


def _write_ajson(vault_dir, filename, entries):
    env_dir = vault_dir / ".smart-env" / "multi"
    env_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in entries:
        lines.append(f'"{key}": {json.dumps(value)},')
    (env_dir / filename).write_text("\n".join(lines), encoding="utf-8")


def test_loads_vector_for_fresh_note(vault, vault_dir):
    note = vault_dir / "Daily.md"
    note.write_text("hello", encoding="utf-8")
    future_ms = int((note.stat().st_mtime + 3600) * 1000)  # embedded after edit
    _write_ajson(
        vault_dir,
        "Daily_md.ajson",
        [(
            "smart_sources:Daily.md",
            {
                "path": "Daily.md",
                "last_embed": {"hash": "abc123", "at": future_ms},
                "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [0.1, 0.2, 0.3]}},
            },
        )],
    )
    got = load_note_vectors(vault)
    assert got["Daily.md"]["vec"] == [0.1, 0.2, 0.3]
    assert got["Daily.md"]["fresh"] is True


def test_marks_stale_when_edited_after_embedding(vault, vault_dir):
    note = vault_dir / "Daily.md"
    note.write_text("hello", encoding="utf-8")
    past_ms = int((note.stat().st_mtime - 3600) * 1000)  # embedded before edit
    _write_ajson(
        vault_dir,
        "Daily_md.ajson",
        [(
            "smart_sources:Daily.md",
            {
                "path": "Daily.md",
                "last_embed": {"hash": "abc123", "at": past_ms},
                "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [0.1, 0.2, 0.3]}},
            },
        )],
    )
    got = load_note_vectors(vault)
    assert got["Daily.md"]["fresh"] is False


def test_last_occurrence_wins_for_duplicate_keys(vault, vault_dir):
    note = vault_dir / "Daily.md"
    note.write_text("hello", encoding="utf-8")
    future_ms = int((note.stat().st_mtime + 3600) * 1000)
    _write_ajson(
        vault_dir,
        "Daily_md.ajson",
        [
            (
                "smart_sources:Daily.md",
                {
                    "path": "Daily.md",
                    "last_embed": {"hash": "old", "at": future_ms},
                    "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [1.0, 0.0]}},
                },
            ),
            (
                "smart_sources:Daily.md",
                {
                    "path": "Daily.md",
                    "last_embed": {"hash": "new", "at": future_ms},
                    "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [0.0, 1.0]}},
                },
            ),
        ],
    )
    got = load_note_vectors(vault)
    assert got["Daily.md"]["vec"] == [0.0, 1.0]


def test_no_smart_env_dir_returns_empty(vault):
    assert load_note_vectors(vault) == {}


def test_ignores_block_level_entries(vault, vault_dir):
    note = vault_dir / "Daily.md"
    note.write_text("hello", encoding="utf-8")
    future_ms = int((note.stat().st_mtime + 3600) * 1000)
    _write_ajson(
        vault_dir,
        "Daily_md.ajson",
        [
            (
                "smart_blocks:Daily.md#chunk0",
                {"path": "Daily.md#chunk0", "last_embed": {"at": future_ms},
                 "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [9.9]}}},
            ),
            (
                "smart_sources:Daily.md",
                {"path": "Daily.md", "last_embed": {"at": future_ms},
                 "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [0.1, 0.2]}}},
            ),
        ],
    )
    got = load_note_vectors(vault)
    assert list(got.keys()) == ["Daily.md"]
    assert got["Daily.md"]["vec"] == [0.1, 0.2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sc_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesseract_mcp.sc_adapter'`

- [ ] **Step 3: Implement**

Create `src/tesseract_mcp/sc_adapter.py`:

```python
"""Reads Smart Connections' local embeddings directly from disk.

.smart-env/multi/*.ajson is Smart Connections' own "append-only JSON"
format: one `"key": {...},` fragment per line, with later lines for the
same key superseding earlier ones. Wrapping the stripped lines in braces
and parsing as one JSON object gives last-occurrence-wins for free, since
Python's dict construction from duplicate keys keeps the last value.

Only whole-note entries (`smart_sources:<path>`) are used — block-level
`smart_blocks:<path>#chunk` entries are Smart Connections' finer-grained
index and are out of scope for note-level ranking.
"""

from __future__ import annotations

import json
from pathlib import Path

from .vault import Vault

SMART_ENV_DIR = ".smart-env"
MODEL_KEY = "TaylorAI/bge-micro-v2"
_SOURCE_PREFIX = "smart_sources:"


def _parse_ajson_file(path: Path) -> dict[str, dict]:
    lines = [
        line.strip().rstrip(",")
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    ]
    if not lines:
        return {}
    blob = "{" + ",".join(lines) + "}"
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return {}


def load_note_vectors(vault: Vault, model_key: str = MODEL_KEY) -> dict[str, dict]:
    multi_dir = vault.root / SMART_ENV_DIR / "multi"
    if not multi_dir.is_dir():
        return {}
    results: dict[str, dict] = {}
    for ajson_file in sorted(multi_dir.glob("*.ajson")):
        entries = _parse_ajson_file(ajson_file)
        for key, entry in entries.items():
            if not key.startswith(_SOURCE_PREFIX):
                continue
            note_path = entry.get("path")
            embeddings = entry.get("embeddings") or {}
            model_entry = embeddings.get(model_key)
            if not note_path or not model_entry or "vec" not in model_entry:
                continue
            note_file = vault.root / note_path
            if not note_file.is_file():
                continue
            embedded_at_ms = (entry.get("last_embed") or {}).get("at", 0)
            mtime_ms = note_file.stat().st_mtime * 1000
            results[note_path] = {
                "vec": model_entry["vec"],
                "fresh": embedded_at_ms >= mtime_ms,
            }
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sc_adapter.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/sc_adapter.py tests/test_sc_adapter.py
git commit -m "feat(search): read Smart Connections embeddings from .smart-env"
```

---

## Task 4: Fallback local embedder

**Files:**
- Create: `src/tesseract_mcp/embeddings.py`
- Modify: `pyproject.toml` (add `sentence-transformers`, `numpy`)
- Test: `tests/test_embeddings.py`

**Interfaces:**
- Consumes: `sc_adapter.load_note_vectors(vault) -> dict[str, dict]` (Task 3), `Vault` (existing).
- Produces: `class Embedder` (protocol: `embed_batch(texts: list[str]) -> list[list[float]]`), `class SentenceTransformerEmbedder(Embedder)` (default, loads `TaylorAI/bge-micro-v2`), `get_note_vectors(vault: Vault, state_root: Path, embedder: Embedder) -> dict[str, list[float]]` — merges Smart Connections' fresh vectors with a locally-cached fallback for stale/missing notes, returns `{note_path: vec}` for every indexable note. Task 6 (`hybrid.py`) and Task 5 (indexer pipeline) consume `get_note_vectors`.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`:

```toml
dependencies = [
    "mcp>=1.2.0",
    "pyyaml>=6.0",
    "rank-bm25>=0.2.2",
    "sentence-transformers>=3.0.0",
    "numpy>=1.26.0",
]
```

Run: `.venv\Scripts\pip install -e .`
Expected: installs successfully (this pulls in `torch` as a transitive dependency — expect a larger download the first time).

- [ ] **Step 2: Write the failing test**

Create `tests/test_embeddings.py`:

```python
import json

import pytest

from tesseract_mcp.embeddings import get_note_vectors
from tesseract_mcp.sc_adapter import MODEL_KEY


class FakeEmbedder:
    """Deterministic stand-in — no model download in tests."""

    def __init__(self):
        self.calls = []

    def embed_batch(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 0.0] for t in texts]


def _write_sc_vector(vault_dir, note_rel, vec, fresh=True):
    note = vault_dir / note_rel
    env_dir = vault_dir / ".smart-env" / "multi"
    env_dir.mkdir(parents=True, exist_ok=True)
    offset = 3600 if fresh else -3600
    at_ms = int((note.stat().st_mtime + offset) * 1000)
    entry = {
        "path": note_rel,
        "last_embed": {"at": at_ms},
        "embeddings": {MODEL_KEY: {"vec": vec}},
    }
    (env_dir / f"{note_rel.replace('/', '_')}.ajson").write_text(
        f'"smart_sources:{note_rel}": {json.dumps(entry)},', encoding="utf-8"
    )


def test_uses_smart_connections_vector_when_fresh(vault, vault_dir):
    _write_sc_vector(vault_dir, "Daily.md", [1.0, 2.0], fresh=True)
    embedder = FakeEmbedder()
    got = get_note_vectors(vault, vault.root, embedder)
    assert got["Daily.md"] == [1.0, 2.0]
    assert embedder.calls == []  # never fell back for this note


def test_falls_back_when_stale(vault, vault_dir):
    _write_sc_vector(vault_dir, "Daily.md", [1.0, 2.0], fresh=False)
    embedder = FakeEmbedder()
    got = get_note_vectors(vault, vault.root, embedder)
    assert got["Daily.md"] != [1.0, 2.0]
    assert embedder.calls  # fallback was used


def test_falls_back_when_missing(vault):
    embedder = FakeEmbedder()
    got = get_note_vectors(vault, vault.root, embedder)
    assert "Daily.md" in got
    assert embedder.calls


def test_fallback_cached_across_calls(vault):
    embedder = FakeEmbedder()
    get_note_vectors(vault, vault.root, embedder)
    call_count_after_first = len(embedder.calls)
    get_note_vectors(vault, vault.root, embedder)
    assert len(embedder.calls) == call_count_after_first  # no re-embedding
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_embeddings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesseract_mcp.embeddings'`

- [ ] **Step 4: Implement**

Create `src/tesseract_mcp/embeddings.py`:

```python
"""Vector source for hybrid search: Smart Connections' embeddings where
fresh, a same-model local fallback (cached) where stale or missing.

The fallback MUST use the identical model Smart Connections uses
(TaylorAI/bge-micro-v2) — vectors from a different model live in an
unrelated space and would silently corrupt similarity ranking if mixed in.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from . import sc_adapter
from .search import SKIP_DIRS
from .vault import Vault

FALLBACK_CACHE_FILE = "fallback_embeddings.json"


class Embedder(Protocol):
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_key: str = sc_adapter.MODEL_KEY):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_key)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, convert_to_numpy=False).tolist()


def _scan_note_texts(vault: Vault) -> dict[str, str]:
    texts: dict[str, str] = {}
    for path in sorted(vault.root.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        texts["/".join(rel_parts)] = path.read_text(encoding="utf-8", errors="ignore")
    return texts


def _load_fallback_cache(state_root: Path) -> dict[str, dict]:
    p = Path(state_root) / FALLBACK_CACHE_FILE
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_fallback_cache(state_root: Path, cache: dict[str, dict]) -> None:
    p = Path(state_root) / FALLBACK_CACHE_FILE
    p.write_text(json.dumps(cache), encoding="utf-8")


def get_note_vectors(vault: Vault, state_root: Path, embedder: Embedder) -> dict[str, list[float]]:
    sc_vectors = sc_adapter.load_note_vectors(vault)
    note_texts = _scan_note_texts(vault)
    fallback_cache = _load_fallback_cache(state_root)

    result: dict[str, list[float]] = {}
    to_embed: list[str] = []
    for rel, text in note_texts.items():
        sc_entry = sc_vectors.get(rel)
        if sc_entry and sc_entry["fresh"]:
            result[rel] = sc_entry["vec"]
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = fallback_cache.get(rel)
        if cached and cached["hash"] == content_hash:
            result[rel] = cached["vec"]
            continue
        to_embed.append(rel)

    if to_embed:
        vecs = embedder.embed_batch([note_texts[rel] for rel in to_embed])
        for rel, vec in zip(to_embed, vecs):
            result[rel] = vec
            fallback_cache[rel] = {
                "hash": hashlib.sha256(note_texts[rel].encode("utf-8")).hexdigest(),
                "vec": vec,
            }
        _save_fallback_cache(state_root, fallback_cache)

    return result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_embeddings.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/tesseract_mcp/embeddings.py tests/test_embeddings.py
git commit -m "feat(search): same-model local fallback for stale/missing embeddings"
```

---

## Task 5: Reciprocal Rank Fusion + hybrid search

**Files:**
- Create: `src/tesseract_mcp/hybrid.py`
- Modify: `src/tesseract_mcp/search.py` (extract shared candidate-gathering helper)
- Test: `tests/test_hybrid.py`

**Interfaces:**
- Consumes: `bm25.rank(corpus, query, limit) -> list[tuple[str, float]]` (Task 2), `embeddings.get_note_vectors(vault, state_root, embedder) -> dict[str, list[float]]` (Task 4), `Embedder` protocol (Task 4).
- Produces: `rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> list[str]`, `hybrid_search(vault, state_root, embedder, query, tags=None, folder=None, limit=20) -> list[Hit]` (reuses `search.Hit`). Task 6 (server wiring) and Task 7 (`context_bundle`) consume `hybrid_search`.

- [ ] **Step 1: Extract shared candidate-gathering from search.py**

In `src/tesseract_mcp/search.py`, replace the `search()` function (lines 41-70) — keep everything above it (`Hit`, `parse_frontmatter`, `_frontmatter_tags`, `SKIP_DIRS`) unchanged, add a new helper above `search()`, and have `search()` call it:

```python
def iter_candidate_notes(
    vault: Vault, tags: list[str] | None = None, folder: str | None = None
) -> list[tuple[str, str]]:
    """(rel_path, text) for every note passing the tag/folder filters."""
    base = vault.resolve(folder) if folder else vault.root
    out: list[tuple[str, str]] = []
    for path in sorted(base.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if tags and not {t.casefold() for t in tags} <= {
            t.casefold() for t in _frontmatter_tags(text)
        }:
            continue
        out.append(("/".join(rel_parts), text))
    return out


def search(
    vault: Vault,
    query: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 20,
) -> list[Hit]:
    q = query.lower()
    hits: list[Hit] = []
    for rel, text in iter_candidate_notes(vault, tags, folder):
        stem = rel.rsplit("/", 1)[-1][:-3]
        if q in stem.lower():
            hits.append(Hit(rel, "(title match)"))
        else:
            for line in text.splitlines():
                if q in line.lower():
                    hits.append(Hit(rel, line.strip()))
                    break
        if len(hits) >= limit:
            break
    return hits
```

- [ ] **Step 2: Run existing search tests to verify the refactor didn't break anything**

Run: `pytest tests/test_search.py -v`
Expected: PASS — all 11 existing tests still pass unchanged (behavior of `search()` is identical, only its internals were extracted).

- [ ] **Step 3: Write the failing hybrid tests**

Create `tests/test_hybrid.py`:

```python
import pytest

from tesseract_mcp.hybrid import hybrid_search, rrf_fuse


class FakeEmbedder:
    """Maps note text to a deterministic vector by simple keyword presence,
    so 'semantic' matches can be tested without a real model."""

    VOCAB = ["logistics", "cooking", "finance"]

    def embed_batch(self, texts):
        return [
            [1.0 if word in t.lower() else 0.0 for word in self.VOCAB]
            for t in texts
        ]


def test_rrf_fuse_prefers_items_ranked_high_in_both_lists():
    a = ["x.md", "y.md", "z.md"]
    b = ["y.md", "x.md", "z.md"]
    fused = rrf_fuse([a, b])
    assert fused[0] in ("x.md", "y.md")  # both near top of both lists
    assert fused[-1] == "z.md"           # last in both lists


def test_rrf_fuse_includes_item_only_in_one_list():
    a = ["x.md"]
    b = []
    assert rrf_fuse([a, b]) == ["x.md"]


def test_rrf_fuse_empty_lists_returns_empty():
    assert rrf_fuse([[], []]) == []


def test_hybrid_search_exact_keyword_match(vault, vault_dir):
    (vault_dir / "Logistics.md").write_text(
        "This note is about supply chain logistics operations.\n",
        encoding="utf-8",
    )
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "logistics")
    assert "Logistics.md" in [h.path for h in hits]


def test_hybrid_search_respects_tag_filter(vault):
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "e", tags=["esg"])
    assert [h.path for h in hits] == ["Projects/Sentinel ESG.md"]


def test_hybrid_search_respects_limit(vault, vault_dir):
    for i in range(5):
        (vault_dir / f"Note{i}.md").write_text("shared keyword here", encoding="utf-8")
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "shared", limit=2)
    assert len(hits) == 2


def test_hybrid_search_no_match_returns_empty(vault):
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "zzzznomatch")
    assert hits == []
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_hybrid.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesseract_mcp.hybrid'`

- [ ] **Step 5: Implement**

Create `src/tesseract_mcp/hybrid.py`:

```python
"""Hybrid retrieval: BM25 keyword ranking + vector similarity, fused via
Reciprocal Rank Fusion. RRF merges by rank position rather than raw score,
so BM25 scores and cosine similarities never need to be normalized against
each other.
"""

from __future__ import annotations

from pathlib import Path

from . import bm25 as bm25_mod
from .embeddings import Embedder, get_note_vectors
from .search import Hit, iter_candidate_notes
from .vault import Vault

RRF_K = 60


def rrf_fuse(ranked_lists: list[list[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return [item for item, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _vector_rank(
    vectors: dict[str, list[float]], candidate_paths: set[str], query_vec: list[float], limit: int
) -> list[str]:
    scored = [
        (path, _cosine(vec, query_vec))
        for path, vec in vectors.items()
        if path in candidate_paths
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [p for p, s in scored if s > 0][:limit]


def _excerpt(text: str, rel: str, query: str) -> str:
    stem = rel.rsplit("/", 1)[-1][:-3]
    q = query.lower()
    if q in stem.lower():
        return "(title match)"
    for line in text.splitlines():
        if q in line.lower():
            return line.strip()
    return text.strip().splitlines()[0][:120] if text.strip() else ""


def hybrid_search(
    vault: Vault,
    state_root: str | Path,
    embedder: Embedder,
    query: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 20,
) -> list[Hit]:
    candidates = iter_candidate_notes(vault, tags, folder)
    if not candidates:
        return []
    corpus = dict(candidates)
    candidate_paths = set(corpus.keys())

    bm25_ranked = [p for p, _ in bm25_mod.rank(corpus, query, limit=50)]

    all_vectors = get_note_vectors(vault, state_root, embedder)
    query_vec = embedder.embed_batch([query])[0]
    vector_ranked = _vector_rank(all_vectors, candidate_paths, query_vec, limit=50)

    fused = rrf_fuse([bm25_ranked, vector_ranked])[:limit]
    return [Hit(rel, _excerpt(corpus[rel], rel, query)) for rel in fused]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_hybrid.py -v`
Expected: PASS (7 passed)

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS — all tests, including the untouched `test_search.py`.

- [ ] **Step 8: Commit**

```bash
git add src/tesseract_mcp/search.py src/tesseract_mcp/hybrid.py tests/test_hybrid.py
git commit -m "feat(search): fuse BM25 and vector ranking via RRF"
```

---

## Task 6: Wire hybrid search into search_brain

**Files:**
- Modify: `src/tesseract_mcp/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `hybrid.hybrid_search(vault, state_root, embedder, query, tags, folder, limit) -> list[Hit]` (Task 5), `embeddings.SentenceTransformerEmbedder` (Task 4), `indexer.state_dir(vault_root)` (Task 1).
- Produces: `search_brain` tool behavior change only — signature unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py` (after `test_search_brain_limit`):

```python
def test_search_brain_uses_hybrid_engine(monkeypatch):
    from tesseract_mcp import hybrid as hybrid_mod
    from tesseract_mcp.search import Hit

    called = {}

    def fake_hybrid_search(vault, state_root, embedder, query, tags=None, folder=None, limit=20):
        called["query"] = query
        called["limit"] = limit
        return [Hit("Fake.md", "fake excerpt")]

    monkeypatch.setattr(hybrid_mod, "hybrid_search", fake_hybrid_search)
    result = server.search_brain("anything", limit=5)
    assert called["query"] == "anything"
    assert called["limit"] == 5
    assert result == [{"path": "Fake.md", "excerpt": "fake excerpt"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server.py -k hybrid_engine -v`
Expected: FAIL — `search_brain` still imports and calls `search_mod.search`, not `hybrid_mod.hybrid_search`, so the monkeypatched fake is never invoked and `called` stays empty (`KeyError`).

- [ ] **Step 3: Implement**

In `src/tesseract_mcp/server.py`, update the import line and add a lazily-constructed shared embedder:

```python
from . import cache as cache_mod, consolidate as consolidate_mod, graph, hybrid, indexer, notes, tasks as tasks_mod
from .embeddings import SentenceTransformerEmbedder
from .extractor import CliExtractor
from .vault import Vault, VaultError
```

(Note: `search_mod` import is dropped — `search.py` is still used internally by `hybrid.py`, just no longer imported directly here.)

Add near `_vault`:

```python
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformerEmbedder()
    return _embedder
```

Replace the `search_brain` tool body:

```python
@mcp.tool()
def search_brain(
    query: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Hybrid full-text + semantic search across the whole vault (BM25 +
    vector similarity, fused). Optionally filter by frontmatter tags or
    restrict to a subfolder. Returns path + excerpt, ranked by relevance."""
    vault = get_vault()
    hits = hybrid.hybrid_search(
        vault, indexer.state_dir(vault.root), _get_embedder(),
        query, tags=tags, folder=folder, limit=limit,
    )
    return [{"path": h.path, "excerpt": h.excerpt} for h in hits]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_server.py -k hybrid_engine -v`
Expected: PASS

- [ ] **Step 5: Run the full server test file**

Run: `pytest tests/test_server.py -v`
Expected: PASS — including `test_search_brain_returns_dicts` and `test_search_brain_limit`, which exercise the real (non-mocked) hybrid engine end to end. These trivial single-match fixture cases should rank identically to before; if either fails, that's a real regression to investigate before continuing, not a test to loosen.

- [ ] **Step 6: Commit**

```bash
git add src/tesseract_mcp/server.py tests/test_server.py
git commit -m "feat(search): wire search_brain to the hybrid retrieval engine"
```

---

## Task 7: Embedding-freshness step in the incremental indexer

**Files:**
- Modify: `src/tesseract_mcp/indexer.py` (`run()`)
- Test: `tests/test_indexer.py`

**Interfaces:**
- Consumes: `embeddings.get_note_vectors(vault, state_root, embedder) -> dict[str, list[float]]` (Task 4).
- Produces: `run()` gains a `precompute_embeddings: bool = True` parameter (default on in production, disabled in tests that don't care about embeddings, to avoid every existing indexer test needing a `FakeEmbedder`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indexer.py`:

```python
def test_run_precomputes_embeddings_by_default(vault, monkeypatch):
    from tesseract_mcp import embeddings as embeddings_mod

    calls = []

    class FakeEmbedder:
        def embed_batch(self, texts):
            calls.append(list(texts))
            return [[0.0] for _ in texts]

    monkeypatch.setattr(embeddings_mod, "SentenceTransformerEmbedder", FakeEmbedder)
    indexer.run(vault, FakeExtractor())
    assert calls  # embeddings were computed for the vault's notes


def test_run_can_skip_embeddings(vault, monkeypatch):
    from tesseract_mcp import embeddings as embeddings_mod

    calls = []

    class FakeEmbedder:
        def embed_batch(self, texts):
            calls.append(list(texts))
            return [[0.0] for _ in texts]

    monkeypatch.setattr(embeddings_mod, "SentenceTransformerEmbedder", FakeEmbedder)
    indexer.run(vault, FakeExtractor(), precompute_embeddings=False)
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_indexer.py -k precompute -v`
Expected: FAIL — `run()` doesn't accept `precompute_embeddings` (`TypeError`), and never touches `embeddings_mod`.

- [ ] **Step 3: Implement**

In `src/tesseract_mcp/indexer.py`, add the import and update `run()`'s signature and body:

```python
from . import embeddings as embeddings_mod
```

Change the `run()` signature:

```python
def run(
    vault: Vault,
    extractor,
    batch: int = DEFAULT_BATCH,
    force: bool = False,
    ignore: tuple[str, ...] = DEFAULT_IGNORE,
    precompute_embeddings: bool = True,
) -> dict:
```

At the end of `run()`, right before `return counts`, add:

```python
    if precompute_embeddings:
        embedder = embeddings_mod.SentenceTransformerEmbedder()
        embeddings_mod.get_note_vectors(vault, state_dir(vault.root), embedder)

    return counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_indexer.py -k precompute -v`
Expected: PASS

- [ ] **Step 5: Run the full indexer test file**

Run: `pytest tests/test_indexer.py -v`
Expected: PASS — every other existing test in this file calls `indexer.run(vault, fx)` without disabling `precompute_embeddings`, so they now also exercise the real `SentenceTransformerEmbedder` unless already monkeypatched. Since `tests/conftest.py`'s `vault` fixture only has 2-3 tiny notes, this is a small, one-time-per-test-process model load — acceptable, but if test run time noticeably increases, that is expected (the model loads once per process on most pytest runs since imports are cached, not once per test).

- [ ] **Step 6: Commit**

```bash
git add src/tesseract_mcp/indexer.py tests/test_indexer.py
git commit -m "feat(index): precompute embedding freshness during incremental indexing"
```

---

## Task 8: `context_bundle` MCP tool

**Files:**
- Modify: `src/tesseract_mcp/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `hybrid.hybrid_search` (Task 5), `cache_mod.note_entity_paths(db, note_path) -> list[str]` (existing, `cache.py:97`), `cache_mod.related_notes(db, vault, path, hops) -> list[dict]` (existing, `cache.py:141`), `cache_mod.find_entity(db, query, type) -> list[dict]` (existing, `cache.py:112`).
- Produces: new `context_bundle(query: str, hops: int = 2, limit: int = 10) -> dict` MCP tool.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
def test_context_bundle_composes_search_and_graph(monkeypatch):
    from tesseract_mcp.extractor import Extraction

    class FakeExtractor:
        def extract(self, path, content):
            if "Sentinel" in path:
                return Extraction(
                    [{"name": "Acme Corp", "type": "organization", "aliases": [], "summary": "Co."}],
                    [],
                )
            return Extraction()

    monkeypatch.setattr(server, "_make_extractor", lambda: FakeExtractor())
    server.index_brain()

    bundle = server.context_bundle("ingestion pipeline")
    assert bundle["hits"]
    assert bundle["hits"][0]["path"] == "Projects/Sentinel ESG.md"
    assert any(e["name"] == "Acme Corp" for e in bundle["entities"])
    assert isinstance(bundle["related_notes"], list)


def test_context_bundle_without_graph_still_returns_hits():
    bundle = server.context_bundle("ingestion pipeline")
    assert bundle["hits"]
    assert bundle["entities"] == []
    assert bundle["related_notes"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server.py -k context_bundle -v`
Expected: FAIL with `AttributeError: module 'tesseract_mcp.server' has no attribute 'context_bundle'`

- [ ] **Step 3: Implement**

In `src/tesseract_mcp/server.py`, add after the `search_brain` tool:

```python
@mcp.tool()
def context_bundle(query: str, hops: int = 2, limit: int = 10) -> dict:
    """One-call GraphRAG context: hybrid-search hits for the query, the
    graph entities those hits mention, and notes connected to those entities
    within N hops — instead of chaining search_brain, find_entity, and
    related_notes across separate calls."""
    vault = get_vault()
    hits = hybrid.hybrid_search(
        vault, indexer.state_dir(vault.root), _get_embedder(), query, limit=limit,
    )
    result_hits = [{"path": h.path, "excerpt": h.excerpt} for h in hits]

    db = indexer.db_path(vault.root)
    if not db.exists():
        return {"hits": result_hits, "entities": [], "related_notes": []}

    entity_paths: set[str] = set()
    related: list[dict] = []
    seen_related: set[str] = set()
    for h in hits:
        for entity_path in cache_mod.note_entity_paths(db, h.path):
            entity_paths.add(entity_path)
        for r in cache_mod.related_notes(db, vault, h.path, hops=hops):
            if r["path"] not in seen_related:
                seen_related.add(r["path"])
                related.append(r)

    entities = []
    for entity_path in sorted(entity_paths):
        name = entity_path.rsplit("/", 1)[-1]
        found = cache_mod.find_entity(db, name)
        entities.extend(f for f in found if f["path"][:-3] == entity_path)

    return {"hits": result_hits, "entities": entities, "related_notes": related}
```

Add `"context_bundle(query, hops?, limit?) — one call: hybrid hits + entities + related notes"` to the `tools` list inside `onboard()`.

Update `test_all_tools_registered` in `tests/test_server.py` to include the new tool:

```python
def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "search_brain", "read_note", "log_session", "capture",
        "upsert_concept", "write_note", "add_task", "list_tasks",
        "query_notes", "get_backlinks", "list_recent",
        "index_brain", "find_entity", "related_notes", "graph_stats",
        "consolidate_graph", "onboard", "context_bundle",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_server.py -k context_bundle -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS — all tests across the whole project.

- [ ] **Step 6: Update README.md tool table**

In `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\README.md`, update the `search_brain` row's description and add a `context_bundle` row to the tools table:

```markdown
| `search_brain` | Hybrid search (BM25 + vector, fused) — optional tag/folder filters |
| `context_bundle` | One call: hybrid search hits + their graph entities + related notes |
```

- [ ] **Step 7: Commit**

```bash
git add src/tesseract_mcp/server.py tests/test_server.py README.md
git commit -m "feat(graph): add context_bundle tool composing search + entity graph"
```

---

## Self-Review Notes

**Spec coverage:**
- BM25 keyword ranking → Task 2
- Reuse Smart Connections embeddings, append-only parsing, last-write-wins → Task 3
- Same-model local fallback for stale/missing → Task 4
- RRF fusion → Task 5
- `search_brain` signature unchanged, engine swapped → Task 6
- `context_bundle` composing search + graph → Task 8
- Indexing pipeline gains freshness/fallback step → Task 7
- Vault-scoped state directory → Task 1
- Non-goals (git history, community detection, multi-vault beyond the state-dir fix, cloud embeddings) → intentionally no tasks; nothing in this plan touches them

**Deviation from spec, noted explicitly:** the spec's decisions table says BM25 is "rebuilt incrementally using the hash-diff manifest." Task 2's implementation builds BM25 in-memory per query instead, because `rank-bm25` has no incremental-update API and persisting a serialized index is unnecessary complexity at personal-vault scale — the *outcome* (ranked BM25 results, incrementally-maintained embeddings) matches the spec; only the BM25 mechanism is simpler than originally sketched. Flagged here rather than silently diverging.
