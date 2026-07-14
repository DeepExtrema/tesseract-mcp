# Scalable Entity Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make graph consolidation do a bounded amount of work per sweep — a fixed cap on entities examined and a fixed cap on each LLM call — so it never again times out on a large graph and drains the existing ~1,235-entity backlog over successive sweeps.

**Architecture:** A new `blocking.py` module computes an identity vector per entity (name + aliases + summary), selects a bounded rolling slice (unchecked-first, path-cursor backstop), generates same-type kNN candidate clusters, and packs whole clusters into size-capped LLM batches. `consolidate.py` adjudicates each batch independently (a failing batch is skipped, not fatal). `librarian.py` threads a durable cursor + `checked_hash` map through the sweep and throttles only the backstop.

**Tech Stack:** Python 3, `sentence-transformers` (bge-micro-v2, via existing `embeddings.SentenceTransformerEmbedder`), `pytest`. No new dependencies.

## Global Constraints

- **Propose-only.** No auto-apply of merges. The apply path (`consolidate._apply_one`) and the pending-proposals review flow are UNCHANGED.
- **LLM output format is unchanged:** `{"merges": [{"type": str, "canonical": str, "duplicates": [str]}]}`. The existing per-merge validation in `consolidate.py` stays the type/name guardrail.
- **Reuse the embedding model** `TaylorAI/bge-micro-v2` via `embeddings.SentenceTransformerEmbedder` and a hash-keyed JSON fallback cache (mirror `embeddings.get_note_vectors`). Never introduce a second embedding model.
- **Entity identity = `name` + `aliases` + `summary`.** The summary is the entity note's BODY text between the `# {name}` H1 and the `## Mentions` header (NOT frontmatter — the note template writes it to the body).
- **Cursor is a path string, never an index** into the entity list (the list mutates under churn).
- **State persists only under `apply=True`** (`librarian.run_sweep` / `_consolidate_step`). A bare `consolidate` CLI dry-run stays stateless.
- **Atomic state writes** already handled by `librarian.save_state` (temp-file + `os.replace`); do not weaken it.
- **Tunable constants live in one place each:** blocking constants in `blocking.py`; the backstop cadence in `librarian.py`.
  - `SIM_THRESHOLD = 0.85`, `K_NEIGHBORS = 5`, `MAX_CLUSTER = 10`, `SLICE_SIZE = 200`, `MAX_ENTITIES_PER_CALL = 40` (all in `blocking.py`).
  - `BACKSTOP_MIN_INTERVAL_DAYS = 14` (in `librarian.py`, repurposes the old `CONSOLIDATE_MAX_AGE_DAYS`).
- **TDD, DRY, YAGNI, frequent commits.** One failing test → minimal code → green → commit.
- Tests must NOT download a model: pass a `FakeEmbedder` / `FakeBackend` double, or rely on the librarian test suite's autouse `_no_model_downloads` fixture.

---

## File Structure

- **Create `src/tesseract_mcp/blocking.py`** — bounded candidate selection for consolidation: entity identity text/hash, entity-vector cache, rolling-slice selection, same-type kNN candidate clustering (union-find), and cluster batching. One responsibility: "decide which small groups of entities to ask the LLM about, in bounded chunks."
- **Create `tests/test_blocking.py`** — unit tests for every `blocking` function (pure where possible; a vault/state_root only for the vector cache).
- **Modify `src/tesseract_mcp/consolidate.py`** — add `summary` to `gather_entities`; extract `_validate_merges`; add `adjudicate_batches` with per-batch error isolation; reroute `propose_merges` and `run`/`main` through bounded batches.
- **Modify `tests/test_consolidate.py`** — pass a `FakeEmbedder` to `run()`; add batch error-isolation tests. Keep existing assertions green.
- **Modify `src/tesseract_mcp/librarian.py`** — rewrite `_consolidate_step` (slice + cursor + `checked_hash` + backstop throttle + skipped-batch surfacing); replace `should_consolidate` with `_backstop_due`; swap the throttle constants; thread `embedder` into the step.
- **Modify `tests/test_librarian.py`** — replace the `should_consolidate` throttle tests and constant test; update the two consolidation integration tests to the new state shape; add slice/backstop tests.

---

## Task 1: Entity identity — summary in `gather_entities`, `identity_text`/`identity_hash`

**Files:**
- Modify: `src/tesseract_mcp/consolidate.py` (`gather_entities`, add `_entity_summary`)
- Create: `src/tesseract_mcp/blocking.py`
- Test: `tests/test_consolidate.py`, `tests/test_blocking.py`

**Interfaces:**
- Consumes: `consolidate.gather_entities(vault) -> list[dict]` (existing; each dict has `name`, `type`, `path`, `aliases`).
- Produces:
  - `gather_entities` dicts additionally carry `summary: str`.
  - `blocking.identity_text(entity: dict) -> str`
  - `blocking.identity_hash(entity: dict) -> str` (sha256 hexdigest of `identity_text`)

- [ ] **Step 1: Write the failing test for the summary field**

Add to `tests/test_consolidate.py` (the `seed` helper already writes `ORACLE_VM = {..., "summary": "Cloud VM."}` into the note body):

```python
def test_gather_entities_includes_body_summary(vault):
    seed(vault)
    got = {e["name"]: e for e in consolidate.gather_entities(vault)}
    assert got["Oracle VM"]["summary"] == "Cloud VM."
    assert got["Oracle VM deploy"]["summary"] == "Deploying it."
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_consolidate.py::test_gather_entities_includes_body_summary -v`
Expected: FAIL with `KeyError: 'summary'`.

- [ ] **Step 3: Add `_entity_summary` and the `summary` key**

In `src/tesseract_mcp/consolidate.py`, add the helper above `gather_entities` (note `MENTIONS_HEADER` is already imported from `.graphstore`):

```python
def _entity_summary(text: str) -> str:
    """Body text between the `# name` H1 and `## Mentions` — the note
    template writes the entity summary there (not frontmatter)."""
    end = text.find("\n---", 3)
    body = text[end + 4:] if end != -1 else text
    cut = body.find(MENTIONS_HEADER)
    if cut != -1:
        body = body[:cut]
    lines = [l for l in body.splitlines() if not l.startswith("# ")]
    return "\n".join(lines).strip()
```

Then in `gather_entities`, add `summary` to the appended dict:

```python
        out.append(
            {"name": p.stem, "type": str(meta.get("entity") or "topic"),
             "path": "/".join(p.relative_to(vault.root).parts)[:-3],
             "aliases": [str(a) for a in aliases],
             "summary": _entity_summary(text)}
        )
```

- [ ] **Step 4: Run it to confirm it passes**

Run: `python -m pytest tests/test_consolidate.py::test_gather_entities_includes_body_summary -v`
Expected: PASS. Also run `python -m pytest tests/test_consolidate.py -v` — all existing consolidate tests still pass (adding a key is backward-compatible).

- [ ] **Step 5: Write the failing test for identity text/hash**

Create `tests/test_blocking.py`:

```python
from tesseract_mcp import blocking


def test_identity_text_combines_name_aliases_summary():
    e = {"name": "Oracle VM", "type": "organization",
         "aliases": ["OVM"], "summary": "Cloud VM.", "path": "x"}
    assert blocking.identity_text(e) == "Oracle VM\nOVM\nCloud VM."


def test_identity_hash_changes_with_summary():
    a = {"name": "N", "aliases": [], "summary": "one", "path": "p"}
    b = {"name": "N", "aliases": [], "summary": "two", "path": "p"}
    assert blocking.identity_hash(a) != blocking.identity_hash(b)


def test_identity_hash_stable_for_same_identity():
    a = {"name": "N", "aliases": ["x"], "summary": "s", "path": "p"}
    b = {"name": "N", "aliases": ["x"], "summary": "s", "path": "OTHER"}
    assert blocking.identity_hash(a) == blocking.identity_hash(b)  # path is NOT identity
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `python -m pytest tests/test_blocking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesseract_mcp.blocking'`.

- [ ] **Step 7: Create `blocking.py` with identity functions**

Create `src/tesseract_mcp/blocking.py`:

```python
"""Bounded candidate selection for entity consolidation.

Given the graph's entities, decide which small same-type groups to ask the
LLM to dedupe, in size-capped batches, so consolidation work never scales
with total graph size. See
docs/superpowers/specs/2026-07-12-scalable-consolidation-design.md.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .embeddings import Embedder
from .hybrid import _cosine

SIM_THRESHOLD = 0.85
K_NEIGHBORS = 5
MAX_CLUSTER = 10
SLICE_SIZE = 200
MAX_ENTITIES_PER_CALL = 40

ENTITY_VECTOR_FILE = "entity_vectors.json"


def identity_text(entity: dict) -> str:
    aliases = ", ".join(entity.get("aliases") or [])
    return f"{entity['name']}\n{aliases}\n{entity.get('summary') or ''}".strip()


def identity_hash(entity: dict) -> str:
    return hashlib.sha256(identity_text(entity).encode("utf-8")).hexdigest()
```

- [ ] **Step 8: Run it to confirm it passes**

Run: `python -m pytest tests/test_blocking.py -v`
Expected: PASS (3 tests).

- [ ] **Step 9: Commit**

```bash
git add src/tesseract_mcp/consolidate.py src/tesseract_mcp/blocking.py tests/test_consolidate.py tests/test_blocking.py
git commit -m "feat(consolidate): entity identity text/hash + body summary in gather_entities"
```

---

## Task 2: Entity-vector cache (`blocking.compute_entity_vectors`)

**Files:**
- Modify: `src/tesseract_mcp/blocking.py`
- Test: `tests/test_blocking.py`

**Interfaces:**
- Consumes: `blocking.identity_text`, `blocking.identity_hash` (Task 1); `embeddings.Embedder` protocol (`embed_batch(list[str]) -> list[list[float]]`).
- Produces: `blocking.compute_entity_vectors(entities: list[dict], state_root: Path, embedder: Embedder) -> dict[str, list[float]]` — maps entity `path` → vector, hash-cached in `entity_vectors.json` under `state_root`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_blocking.py`:

```python
class FakeEmbedder:
    """Deterministic stand-in — records each batch, no model download."""

    def __init__(self):
        self.calls = []

    def embed_batch(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]


def _ents():
    return [
        {"name": "Acme", "type": "organization", "aliases": [], "summary": "a",
         "path": "Claude/Graph/Organizations/Acme"},
        {"name": "Acme Corp", "type": "organization", "aliases": [], "summary": "b",
         "path": "Claude/Graph/Organizations/Acme Corp"},
    ]


def test_compute_entity_vectors_returns_vector_per_entity(tmp_path):
    got = blocking.compute_entity_vectors(_ents(), tmp_path, FakeEmbedder())
    assert set(got) == {"Claude/Graph/Organizations/Acme",
                        "Claude/Graph/Organizations/Acme Corp"}


def test_unchanged_identity_is_a_cache_hit(tmp_path):
    emb = FakeEmbedder()
    blocking.compute_entity_vectors(_ents(), tmp_path, emb)
    first = len(emb.calls)
    blocking.compute_entity_vectors(_ents(), tmp_path, emb)
    assert len(emb.calls) == first  # nothing re-embedded


def test_changed_identity_is_reembedded(tmp_path):
    emb = FakeEmbedder()
    blocking.compute_entity_vectors(_ents(), tmp_path, emb)
    changed = _ents()
    changed[0]["summary"] = "DIFFERENT"
    blocking.compute_entity_vectors(changed, tmp_path, emb)
    assert emb.calls[-1] == ["Acme\n\nDIFFERENT"]  # only the changed one
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_blocking.py -k entity_vectors -v`
Expected: FAIL with `AttributeError: module 'tesseract_mcp.blocking' has no attribute 'compute_entity_vectors'`.

- [ ] **Step 3: Implement the cache (mirror `embeddings.get_note_vectors`)**

Append to `src/tesseract_mcp/blocking.py`:

```python
def _load_entity_vectors(state_root: Path) -> dict[str, dict]:
    p = Path(state_root) / ENTITY_VECTOR_FILE
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_entity_vectors(state_root: Path, cache: dict[str, dict]) -> None:
    p = Path(state_root) / ENTITY_VECTOR_FILE
    p.write_text(json.dumps(cache), encoding="utf-8")


def compute_entity_vectors(
    entities: list[dict], state_root: Path, embedder: Embedder
) -> dict[str, list[float]]:
    cache = _load_entity_vectors(state_root)
    result: dict[str, list[float]] = {}
    to_embed: list[dict] = []
    hashes: dict[str, str] = {}
    for e in entities:
        h = identity_hash(e)
        hashes[e["path"]] = h
        cached = cache.get(e["path"])
        if cached and cached["hash"] == h:
            result[e["path"]] = cached["vec"]
        else:
            to_embed.append(e)
    if to_embed:
        vecs = embedder.embed_batch([identity_text(e) for e in to_embed])
        for e, vec in zip(to_embed, vecs):
            result[e["path"]] = vec
            cache[e["path"]] = {"hash": hashes[e["path"]], "vec": vec}
        _save_entity_vectors(state_root, cache)
    return result
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_blocking.py -k entity_vectors -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/blocking.py tests/test_blocking.py
git commit -m "feat(blocking): hash-cached entity identity vectors"
```

---

## Task 3: Same-type kNN candidate pairs (`blocking._candidate_pairs`)

**Files:**
- Modify: `src/tesseract_mcp/blocking.py`
- Test: `tests/test_blocking.py`

**Interfaces:**
- Consumes: `blocking._cosine` (imported from `hybrid`).
- Produces: `blocking._candidate_pairs(slice_entities: list[dict], all_entities: list[dict], vectors: dict[str, list[float]], *, k: int, threshold: float) -> set[tuple[str, str]]` — unordered `(pathA, pathB)` pairs (each tuple sorted), same-type only, cosine ≥ threshold, top-k per slice entity.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_blocking.py`:

```python
def test_candidate_pairs_same_type_only():
    ents = [
        {"path": "p1", "type": "person"},
        {"path": "p2", "type": "person"},
        {"path": "o1", "type": "organization"},
    ]
    vectors = {"p1": [1.0, 0.0], "p2": [1.0, 0.01], "o1": [1.0, 0.0]}
    pairs = blocking._candidate_pairs(ents, ents, vectors, k=5, threshold=0.85)
    assert pairs == {("p1", "p2")}  # o1 identical direction but wrong type


def test_candidate_pairs_respects_threshold():
    ents = [{"path": "a", "type": "topic"}, {"path": "b", "type": "topic"}]
    vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0]}  # cosine 0.0
    assert blocking._candidate_pairs(ents, ents, vectors, k=5, threshold=0.85) == set()


def test_candidate_pairs_top_k_limit():
    ents = [{"path": f"p{i}", "type": "topic"} for i in range(6)]
    vectors = {f"p{i}": [1.0, i * 0.001] for i in range(6)}  # all near-parallel
    pairs = blocking._candidate_pairs([ents[0]], ents, vectors, k=2, threshold=0.85)
    assert len(pairs) == 2  # only p0's 2 nearest, not all 5
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_blocking.py -k candidate_pairs -v`
Expected: FAIL (`no attribute '_candidate_pairs'`).

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/blocking.py`:

```python
def _candidate_pairs(
    slice_entities: list[dict],
    all_entities: list[dict],
    vectors: dict[str, list[float]],
    *,
    k: int,
    threshold: float,
) -> set[tuple[str, str]]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in all_entities:
        by_type[e["type"]].append(e)
    pairs: set[tuple[str, str]] = set()
    for s in slice_entities:
        sv = vectors.get(s["path"])
        if sv is None:
            continue
        sims: list[tuple[float, str]] = []
        for other in by_type[s["type"]]:
            if other["path"] == s["path"]:
                continue
            ov = vectors.get(other["path"])
            if ov is None:
                continue
            c = _cosine(sv, ov)
            if c >= threshold:
                sims.append((c, other["path"]))
        sims.sort(reverse=True)
        for _, op in sims[:k]:
            pairs.add(tuple(sorted((s["path"], op))))
    return pairs
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_blocking.py -k candidate_pairs -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/blocking.py tests/test_blocking.py
git commit -m "feat(blocking): same-type kNN candidate pairs"
```

---

## Task 4: Union-find clustering + `candidate_clusters`

**Files:**
- Modify: `src/tesseract_mcp/blocking.py`
- Test: `tests/test_blocking.py`

**Interfaces:**
- Consumes: `blocking._candidate_pairs` (Task 3).
- Produces:
  - `blocking._cluster_pairs(pairs: set[tuple[str, str]], *, max_cluster: int) -> list[list[str]]` — union-find components, each sorted, split into ≤ `max_cluster` chunks.
  - `blocking.candidate_clusters(slice_entities, all_entities, vectors, *, k=K_NEIGHBORS, threshold=SIM_THRESHOLD, max_cluster=MAX_CLUSTER) -> list[list[dict]]` — clusters of entity dicts, singletons dropped.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_blocking.py`:

```python
def test_cluster_pairs_unions_overlapping():
    # a-b and b-c overlap on b -> one cluster {a,b,c}
    clusters = blocking._cluster_pairs({("a", "b"), ("b", "c")}, max_cluster=10)
    assert clusters == [["a", "b", "c"]]


def test_cluster_pairs_splits_oversize():
    members = [f"n{i:02d}" for i in range(11)]
    pairs = {("n00", m) for m in members[1:]}  # star -> one component of 11
    clusters = blocking._cluster_pairs(pairs, max_cluster=10)
    assert sorted(len(c) for c in clusters) == [1, 10]


def test_candidate_clusters_maps_to_entities_and_drops_singletons():
    ents = [
        {"path": "a", "type": "topic"}, {"path": "b", "type": "topic"},
        {"path": "lonely", "type": "topic"},
    ]
    vectors = {"a": [1.0, 0.0], "b": [1.0, 0.01], "lonely": [0.0, 1.0]}
    clusters = blocking.candidate_clusters(ents, ents, vectors)
    assert len(clusters) == 1
    assert {e["path"] for e in clusters[0]} == {"a", "b"}
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_blocking.py -k "cluster" -v`
Expected: FAIL (`no attribute '_cluster_pairs'`).

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/blocking.py`:

```python
def _cluster_pairs(pairs: set[tuple[str, str]], *, max_cluster: int) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in pairs:
        union(a, b)

    groups: dict[str, list[str]] = defaultdict(list)
    for node in parent:
        groups[find(node)].append(node)

    clusters: list[list[str]] = []
    for members in groups.values():
        members.sort()
        for i in range(0, len(members), max_cluster):
            clusters.append(members[i:i + max_cluster])
    return clusters


def candidate_clusters(
    slice_entities: list[dict],
    all_entities: list[dict],
    vectors: dict[str, list[float]],
    *,
    k: int = K_NEIGHBORS,
    threshold: float = SIM_THRESHOLD,
    max_cluster: int = MAX_CLUSTER,
) -> list[list[dict]]:
    pairs = _candidate_pairs(
        slice_entities, all_entities, vectors, k=k, threshold=threshold
    )
    path_clusters = _cluster_pairs(pairs, max_cluster=max_cluster)
    by_path = {e["path"]: e for e in all_entities}
    return [
        [by_path[p] for p in members]
        for members in path_clusters
        if len(members) >= 2
    ]
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_blocking.py -k "cluster" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/blocking.py tests/test_blocking.py
git commit -m "feat(blocking): union-find clustering + candidate_clusters"
```

---

## Task 5: Cluster batching (`blocking.batch_clusters`)

**Files:**
- Modify: `src/tesseract_mcp/blocking.py`
- Test: `tests/test_blocking.py`

**Interfaces:**
- Produces: `blocking.batch_clusters(clusters: list[list[dict]], *, max_entities_per_call: int = MAX_ENTITIES_PER_CALL) -> list[list[list[dict]]]` — a batch is a list of whole clusters; a cluster is never split across batches; each batch's total entity count ≤ cap (except an oversize single cluster, which stands alone).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_blocking.py`:

```python
def _cluster(n, tag):
    return [{"path": f"{tag}{i}", "type": "topic"} for i in range(n)]


def test_batch_packs_whole_clusters_under_cap():
    clusters = [_cluster(3, "a"), _cluster(4, "b"), _cluster(3, "c")]
    batches = blocking.batch_clusters(clusters, max_entities_per_call=8)
    # 3+4=7 fits; +3 would be 10>8 -> second batch
    assert [sum(len(c) for c in b) for b in batches] == [7, 3]


def test_batch_never_splits_a_cluster():
    clusters = [_cluster(6, "a"), _cluster(6, "b")]
    batches = blocking.batch_clusters(clusters, max_entities_per_call=8)
    # each batch holds whole clusters; 6+6=12>8 -> one cluster each
    assert [[len(c) for c in b] for b in batches] == [[6], [6]]
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_blocking.py -k batch -v`
Expected: FAIL (`no attribute 'batch_clusters'`).

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/blocking.py`:

```python
def batch_clusters(
    clusters: list[list[dict]], *, max_entities_per_call: int = MAX_ENTITIES_PER_CALL
) -> list[list[list[dict]]]:
    batches: list[list[list[dict]]] = []
    current: list[list[dict]] = []
    count = 0
    for cluster in clusters:
        if current and count + len(cluster) > max_entities_per_call:
            batches.append(current)
            current, count = [], 0
        current.append(cluster)
        count += len(cluster)
    if current:
        batches.append(current)
    return batches
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_blocking.py -k batch -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/blocking.py tests/test_blocking.py
git commit -m "feat(blocking): pack whole clusters into size-capped batches"
```

---

## Task 6: Rolling slice selection (`blocking.select_slice`)

**Files:**
- Modify: `src/tesseract_mcp/blocking.py`
- Test: `tests/test_blocking.py`

**Interfaces:**
- Consumes: `blocking.identity_hash` (Task 1).
- Produces: `blocking.select_slice(entities: list[dict], checked_hash: dict[str, str], cursor: str | None, slice_size: int, *, backstop_due: bool) -> tuple[list[dict], str | None, bool]` — returns `(slice_entities, new_cursor_path, used_backstop)`.
  - Priority 1: all entities whose `identity_hash != checked_hash[path]` (unchecked/changed), truncated to `slice_size`.
  - Priority 2 (only if `backstop_due` AND spare budget): fill from the first path lexicographically `> cursor`, wrapping to the start; advance `new_cursor` to the last backstop entity taken.
  - `new_cursor` is unchanged when the backstop does not run.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_blocking.py`:

```python
def _e(path, summary="s"):
    return {"name": path, "type": "topic", "aliases": [], "summary": summary,
            "path": path}


def test_slice_bounded_even_when_all_unchecked():
    ents = [_e(f"p{i:03d}") for i in range(500)]
    slice_, _, used = blocking.select_slice(ents, {}, None, 200, backstop_due=True)
    assert len(slice_) == 200 and used is False  # unchecked fills the whole budget


def test_slice_prioritizes_unchecked_over_backstop():
    ents = [_e("a"), _e("b"), _e("c")]
    checked = {e["path"]: blocking.identity_hash(e) for e in ents}
    checked["b"] = "STALE"  # b is unchecked/changed
    slice_, _, _ = blocking.select_slice(ents, checked, None, 1, backstop_due=True)
    assert [e["path"] for e in slice_] == ["b"]


def test_backstop_cursor_resumes_by_path_and_wraps():
    ents = [_e("a"), _e("b"), _e("c"), _e("d")]
    checked = {e["path"]: blocking.identity_hash(e) for e in ents}  # all checked
    slice_, cursor, used = blocking.select_slice(
        ents, checked, "b", 2, backstop_due=True)
    assert [e["path"] for e in slice_] == ["c", "d"] and cursor == "d" and used
    # next call wraps past the end back to the start
    slice2, cursor2, _ = blocking.select_slice(
        ents, checked, "d", 2, backstop_due=True)
    assert [e["path"] for e in slice2] == ["a", "b"] and cursor2 == "b"


def test_backstop_skipped_when_not_due():
    ents = [_e("a"), _e("b")]
    checked = {e["path"]: blocking.identity_hash(e) for e in ents}
    slice_, cursor, used = blocking.select_slice(
        ents, checked, "a", 5, backstop_due=False)
    assert slice_ == [] and cursor == "a" and used is False


def test_slice_is_churn_robust_no_double_cover():
    ents = [_e(p) for p in ["a", "c", "e"]]
    checked = {e["path"]: blocking.identity_hash(e) for e in ents}
    _, cursor, _ = blocking.select_slice(ents, checked, None, 1, backstop_due=True)
    assert cursor == "a"
    # 'b' is inserted before the next sweep; resume must land on 'c', not skip it
    ents2 = [_e(p) for p in ["a", "b", "c", "e"]]
    checked2 = {e["path"]: blocking.identity_hash(e) for e in ents2}
    slice2, _, _ = blocking.select_slice(ents2, checked2, "a", 1, backstop_due=True)
    assert [e["path"] for e in slice2] == ["b"]  # first path > "a"
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_blocking.py -k slice -v`
Expected: FAIL (`no attribute 'select_slice'`).

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/blocking.py`:

```python
def _resume_after(by_path: list[dict], cursor: str | None) -> list[dict]:
    if cursor is None:
        return list(by_path)
    paths = [e["path"] for e in by_path]
    idx = bisect.bisect_right(paths, cursor)  # first index with path > cursor
    return by_path[idx:] + by_path[:idx]


def select_slice(
    entities: list[dict],
    checked_hash: dict[str, str],
    cursor: str | None,
    slice_size: int,
    *,
    backstop_due: bool,
) -> tuple[list[dict], str | None, bool]:
    by_path = sorted(entities, key=lambda e: e["path"])
    slice_ = [e for e in by_path if checked_hash.get(e["path"]) != identity_hash(e)]
    slice_ = slice_[:slice_size]
    new_cursor = cursor
    used_backstop = False
    if backstop_due and len(slice_) < slice_size:
        chosen = {e["path"] for e in slice_}
        for e in _resume_after(by_path, cursor):
            if len(slice_) >= slice_size:
                break
            if e["path"] in chosen:
                continue
            slice_.append(e)
            chosen.add(e["path"])
            new_cursor = e["path"]
            used_backstop = True
    return slice_, new_cursor, used_backstop
```

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_blocking.py -k slice -v`
Expected: PASS (5 tests). Then run the whole module: `python -m pytest tests/test_blocking.py -v`.

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/blocking.py tests/test_blocking.py
git commit -m "feat(blocking): path-cursor rolling slice (unchecked-first + backstop)"
```

---

## Task 7: Batched adjudication with error isolation (`consolidate.adjudicate_batches`)

**Files:**
- Modify: `src/tesseract_mcp/consolidate.py` (extract `_validate_merges`; add `adjudicate_batches`; rewrite `propose_merges`)
- Test: `tests/test_consolidate.py`

**Interfaces:**
- Consumes: `backend.complete_json(prompt: str) -> dict` (raises `ExtractorError`/`Exception` on timeout).
- Produces:
  - `consolidate._validate_merges(raw: dict, known: set[tuple[str, str]]) -> list[dict]`
  - `consolidate.adjudicate_batches(backend, batches: list[list[list[dict]]], all_entities: list[dict]) -> tuple[list[dict], int]` — returns `(merges, skipped_batch_count)`; a batch whose call raises is skipped and counted; merges deduped across batches.
  - `consolidate.propose_merges(backend, entities: list[dict]) -> list[dict]` — unchanged signature/behavior (now a one-batch wrapper).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_consolidate.py` (a backend double that raises on the batch containing a named entity):

```python
from tesseract_mcp.extractor import ExtractorError


class FlakyBackend:
    """Raises on any prompt mentioning `boom_name`; else returns `reply`."""

    def __init__(self, reply, boom_name):
        self.reply = reply
        self.boom_name = boom_name
        self.calls = 0

    def complete_json(self, prompt):
        self.calls += 1
        if self.boom_name in prompt:
            raise ExtractorError("claude timed out after 120s")
        return self.reply


def _ent(name, etype="organization"):
    return {"name": name, "type": etype, "aliases": [], "summary": name}


def test_adjudicate_isolates_a_failing_batch():
    good = [_ent("Acme"), _ent("Acme Corp")]
    bad = [_ent("Zeta"), _ent("Zeta Inc")]
    all_ents = good + bad
    batches = [[good], [bad]]  # one cluster per batch
    reply = {"merges": [{"type": "organization", "canonical": "Acme",
                         "duplicates": ["Acme Corp"]}]}
    backend = FlakyBackend(reply, boom_name="Zeta")
    merges, skipped = consolidate.adjudicate_batches(backend, batches, all_ents)
    assert skipped == 1
    assert merges == [{"type": "organization", "canonical": "Acme",
                       "duplicates": ["Acme Corp"]}]


def test_adjudicate_dedupes_merges_across_batches():
    ents = [_ent("Acme"), _ent("Acme Corp")]
    reply = {"merges": [{"type": "organization", "canonical": "Acme",
                         "duplicates": ["Acme Corp"]}]}
    batches = [[ents], [ents]]  # same reply twice
    merges, skipped = consolidate.adjudicate_batches(
        FakeBackend(reply), batches, ents)
    assert skipped == 0 and len(merges) == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_consolidate.py -k adjudicate -v`
Expected: FAIL (`no attribute 'adjudicate_batches'`).

- [ ] **Step 3: Extract `_validate_merges` and add `adjudicate_batches`; rewrite `propose_merges`**

In `src/tesseract_mcp/consolidate.py`, replace the current `propose_merges` body with a validation helper, a batched adjudicator, and a thin wrapper:

```python
def _validate_merges(raw: dict, known: set[tuple[str, str]]) -> list[dict]:
    out = []
    for m in raw.get("merges") or []:
        etype = str(m.get("type") or "").lower()
        canonical = str(m.get("canonical") or "").strip()
        dups = [str(d).strip() for d in (m.get("duplicates") or []) if str(d).strip()]
        dups = [d for d in dups if d.casefold() != canonical.casefold()]
        if not canonical or not dups or etype not in TYPE_FOLDERS:
            continue
        if (etype, canonical.casefold()) not in known:
            continue
        if any((etype, d.casefold()) not in known for d in dups):
            continue
        out.append({"type": etype, "canonical": canonical, "duplicates": dups})
    return out


def _listing(entities: list[dict]) -> str:
    return "\n".join(
        f"{e['type']} | {e['name']} | {', '.join(e['aliases']) or '-'}"
        for e in entities
    )


def adjudicate_batches(
    backend, batches: list[list[list[dict]]], all_entities: list[dict]
) -> tuple[list[dict], int]:
    """Run one LLM call per batch, isolating failures. A batch is a list of
    clusters; a cluster is a list of entity dicts. Returns (merges, skipped)."""
    known = {(e["type"], e["name"].casefold()) for e in all_entities}
    merges: list[dict] = []
    seen: set[tuple] = set()
    skipped = 0
    for batch in batches:
        entities = [e for cluster in batch for e in cluster]
        try:
            raw = backend.complete_json(PROMPT.format(listing=_listing(entities)))
        except Exception:  # noqa: BLE001 — one bad batch must not fail the step
            skipped += 1
            continue
        for m in _validate_merges(raw, known):
            key = (m["type"], m["canonical"].casefold(),
                   tuple(sorted(d.casefold() for d in m["duplicates"])))
            if key in seen:
                continue
            seen.add(key)
            merges.append(m)
    return merges, skipped


def propose_merges(backend, entities: list[dict]) -> list[dict]:
    if not entities:
        return []
    merges, _ = adjudicate_batches(backend, [[entities]], entities)
    return merges
```

- [ ] **Step 4: Run to confirm pass (new + existing)**

Run: `python -m pytest tests/test_consolidate.py -v`
Expected: PASS — new adjudicate tests AND the existing `test_propose_merges_validates` (now a one-batch path).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/consolidate.py tests/test_consolidate.py
git commit -m "feat(consolidate): per-batch adjudication with error isolation"
```

---

## Task 8: Route `consolidate.run`/`main` through bounded batches (CLI)

**Files:**
- Modify: `src/tesseract_mcp/consolidate.py` (`run`, `main`)
- Test: `tests/test_consolidate.py`

**Interfaces:**
- Consumes: `blocking.compute_entity_vectors`, `blocking.candidate_clusters`, `blocking.batch_clusters` (Tasks 2/4/5); `indexer.state_dir`; `embeddings.SentenceTransformerEmbedder`.
- Produces: `consolidate.run(vault, backend, apply: bool = False, embedder=None) -> dict` — proposes merges via bounded batching over the whole graph (no single giant call); result dict gains `"skipped_batches": int`.

- [ ] **Step 1: Update existing `run()` tests to pass a `FakeEmbedder`, and add a coverage test**

In `tests/test_consolidate.py`, add a `FakeEmbedder` (parallel vectors so same-type entities cluster) near the top:

```python
class FakeEmbedder:
    def embed_batch(self, texts):
        return [[float(len(t)), 1.0] for t in texts]
```

Update the four `consolidate.run(...)` call sites to pass `embedder=FakeEmbedder()`:
- `test_dry_run_changes_nothing`
- `test_apply_merges_mentions_relations_aliases_and_redirects`
- `test_apply_merge_finds_dup_by_filename_when_canonical_has_alias`
- `test_cache_rebuild_skips_redirect_stubs`

Example (apply the same `embedder=` addition to each):

```python
def test_dry_run_changes_nothing(vault):
    seed(vault)
    result = consolidate.run(vault, FakeBackend(MERGE), apply=False,
                             embedder=FakeEmbedder())
    assert result["proposed"] and result["applied"] is False
    assert "Merged into" not in vault.read(
        entity_rel_path("organization", "Oracle VM deploy"))
```

Add a new test asserting the skipped counter is surfaced:

```python
def test_run_reports_skipped_batches(vault):
    seed(vault)
    result = consolidate.run(vault, FakeBackend(MERGE), apply=False,
                             embedder=FakeEmbedder())
    assert result["skipped_batches"] == 0
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_consolidate.py -k "run or dry_run or apply or cache_rebuild" -v`
Expected: FAIL — `run()` does not yet accept `embedder=` / has no `skipped_batches` key.

- [ ] **Step 3: Rewrite `run()` and update `main()`**

In `src/tesseract_mcp/consolidate.py`, adjust imports at the top: add `from . import blocking` and `from . import embeddings as embeddings_mod`, and widen the existing `from .indexer import db_path` to `from .indexer import db_path, state_dir` (the module only imports `db_path` today, so `state_dir` must be added explicitly). Then replace `run`:

```python
def run(vault: Vault, backend, apply: bool = False, embedder=None) -> dict:
    entities = gather_entities(vault)
    result = {"entities": len(entities), "proposed": [], "applied": False,
              "merged_entities": 0, "skipped_batches": 0}
    if not entities:
        return result
    if embedder is None:
        embedder = embeddings_mod.SentenceTransformerEmbedder()
    state_root = state_dir(vault.root)
    vectors = blocking.compute_entity_vectors(entities, state_root, embedder)
    clusters = blocking.candidate_clusters(entities, entities, vectors)
    batches = blocking.batch_clusters(clusters)
    merges, skipped = adjudicate_batches(backend, batches, entities)
    result["proposed"] = merges
    result["skipped_batches"] = skipped
    if apply and merges:
        store = GraphStore(vault)
        now = datetime.now()
        for m in merges:
            _apply_one(vault, store, m, now)
            result["merged_entities"] += len(m["duplicates"])
        result["applied"] = True
        cache.rebuild(vault, db_path())
    return result
```

`main()` needs no change (it calls `run(Vault(...), consolidation_extractor(...), apply=...)`; the default `embedder=None` builds the real model for CLI use).

- [ ] **Step 4: Run to confirm pass**

Run: `python -m pytest tests/test_consolidate.py -v`
Expected: PASS (all, including the four updated `run()` tests and the new skipped-batches test).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/consolidate.py tests/test_consolidate.py
git commit -m "feat(consolidate): CLI run uses bounded candidate batches (no giant call)"
```

---

## Task 9: Librarian slice orchestration — cursor, `checked_hash`, backstop throttle

**Files:**
- Modify: `src/tesseract_mcp/librarian.py` (constants, remove `should_consolidate`, add `_backstop_due`, rewrite `_consolidate_step`, thread `embedder`, surface skipped batches)
- Test: `tests/test_librarian.py`

**Interfaces:**
- Consumes: `blocking.compute_entity_vectors`, `blocking.select_slice`, `blocking.candidate_clusters`, `blocking.batch_clusters`, `blocking.identity_hash`, `blocking.SLICE_SIZE` (Tasks 1–6); `consolidate.adjudicate_batches`, `consolidate.gather_entities` (Tasks 1/7); `indexer.state_dir`.
- Produces:
  - `librarian.BACKSTOP_MIN_INTERVAL_DAYS = 14` (replaces `CONSOLIDATE_MAX_AGE_DAYS`; `CONSOLIDATE_MIN_NEW_ENTITIES` removed).
  - `librarian._backstop_due(con: dict, now: datetime) -> bool`
  - `librarian._consolidate_step(vault, state, consolidator, now, apply, embedder) -> dict` with keys `ran`, `reason`, `proposed`, `skipped_batches`.
  - Consolidation state block gains `cursor` (path str), `checked_hash` (`{path: hash}`), `backstop_last_advance` (timestamp str).

- [ ] **Step 1: Write failing tests for constants + backstop gate**

In `tests/test_librarian.py`, replace `test_constants_match_spec` and the six `should_consolidate` tests (`test_first_pass_runs_when_entities_exist`, `test_no_entities_never_runs`, `test_14_new_entities_skips`, `test_15_new_entities_runs`, `test_age_trigger_requires_a_new_entity`, `test_age_trigger_fires_at_14_days_with_one_new_entity`) with:

```python
from tesseract_mcp import blocking


def test_constants_match_spec():
    assert librarian.BACKSTOP_MIN_INTERVAL_DAYS == 14
    assert blocking.SLICE_SIZE == 200
    assert blocking.MAX_ENTITIES_PER_CALL == 40


def test_backstop_due_on_first_pass():
    assert librarian._backstop_due({}, NOW) is True


def test_backstop_not_due_before_interval():
    con = {"backstop_last_advance": NOW.strftime(librarian.TS_FMT)}
    assert librarian._backstop_due(con, NOW + timedelta(days=13)) is False


def test_backstop_due_after_interval():
    con = {"backstop_last_advance": NOW.strftime(librarian.TS_FMT)}
    assert librarian._backstop_due(con, NOW + timedelta(days=14)) is True
```

Also delete the now-obsolete `_throttle_state` helper (only the removed tests used it).

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_librarian.py -k "constants or backstop" -v`
Expected: FAIL (`AttributeError: ... BACKSTOP_MIN_INTERVAL_DAYS` / `_backstop_due`).

- [ ] **Step 3: Swap constants, remove `should_consolidate`, add `_backstop_due`**

In `src/tesseract_mcp/librarian.py`:
- Add import: `from . import blocking`.
- Replace the two constant lines (29–30):

```python
BACKSTOP_MIN_INTERVAL_DAYS = 14
```

- Delete the entire `should_consolidate` function (lines 59–78) and add:

```python
def _backstop_due(con: dict, now: datetime) -> bool:
    """The rolling backstop re-check runs at most once per interval; the
    unchecked/changed path runs every sweep regardless."""
    last = con.get("backstop_last_advance")
    if not last:
        return True
    return (now - datetime.strptime(last, TS_FMT)).days >= BACKSTOP_MIN_INTERVAL_DAYS
```

- [ ] **Step 4: Run to confirm the gate tests pass**

Run: `python -m pytest tests/test_librarian.py -k "constants or backstop" -v`
Expected: PASS (4 tests). `should_consolidate` tests are gone.

- [ ] **Step 5: Write failing tests for the rewritten step**

Add to `tests/test_librarian.py` (uses the existing `_entity_note` helper and `FakeConsolidator`):

```python
def test_consolidation_first_pass_records_cursor_and_checked(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    _entity_note(vault_dir, "Organizations", "Acme Corp", "organization")
    fake = FakeConsolidator(merges=[{"type": "organization", "canonical": "Acme",
                                     "duplicates": ["Acme Corp"]}])
    result = librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                                 embedder=FakeEmbedder(), now=NOW)
    step = result["steps"]["consolidate"]
    assert step["ran"] and step["proposed"] == [
        {"type": "organization", "canonical": "Acme", "duplicates": ["Acme Corp"]}]
    con = librarian.load_state(vault)["consolidation"]
    assert set(con["checked_hash"]) == {
        "Claude/Graph/Organizations/Acme",
        "Claude/Graph/Organizations/Acme Corp"}
    assert con["pending_proposals"] == step["proposed"]


def test_second_sweep_skips_when_all_checked_and_backstop_cold(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    fake = FakeConsolidator()
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW)
    calls_after_first = fake.calls
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW)  # nothing new, backstop not due
    assert fake.calls == calls_after_first


def test_changed_entity_reenters_slice(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    fake = FakeConsolidator()
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW)
    # edit the entity body (identity changes) -> it becomes unchecked again
    note = vault_dir / "Claude" / "Graph" / "Organizations" / "Acme.md"
    note.write_text(note.read_text(encoding="utf-8").replace("Summary.", "New summary."),
                    encoding="utf-8")
    before = fake.calls
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW + timedelta(minutes=1))
    assert fake.calls > before  # changed identity re-adjudicated
```

- [ ] **Step 6: Run to confirm failure**

Run: `python -m pytest tests/test_librarian.py -k "first_pass_records or second_sweep_skips or changed_entity" -v`
Expected: FAIL — `_consolidate_step` still uses the old `should_consolidate` shape (no `checked_hash` in state).

- [ ] **Step 7: Rewrite `_consolidate_step` and thread `embedder`**

In `src/tesseract_mcp/librarian.py`, replace `_consolidate_step` (lines ~229–245) with:

```python
def _consolidate_step(
    vault: Vault, state: dict, consolidator, now: datetime, apply: bool, embedder
) -> dict:
    entities = consolidate_mod.gather_entities(vault)
    if not entities:
        return {"ran": False, "reason": "no entities", "proposed": [],
                "skipped_batches": 0}
    con = state.get("consolidation") or {}
    checked_hash = dict(con.get("checked_hash") or {})
    cursor = con.get("cursor")
    backstop_due = _backstop_due(con, now)
    state_root = indexer.state_dir(vault.root)
    vectors = blocking.compute_entity_vectors(entities, state_root, embedder)
    slice_, new_cursor, used_backstop = blocking.select_slice(
        entities, checked_hash, cursor, blocking.SLICE_SIZE, backstop_due=backstop_due)
    if not slice_:
        return {"ran": False, "reason": "nothing to check", "proposed": [],
                "skipped_batches": 0}
    if consolidator is None:
        consolidator = extractor_mod.consolidation_extractor()
    clusters = blocking.candidate_clusters(slice_, entities, vectors)
    batches = blocking.batch_clusters(clusters)
    proposed, skipped = consolidate_mod.adjudicate_batches(
        consolidator, batches, entities)
    reason = f"backstop ({len(slice_)})" if used_backstop else f"{len(slice_)} unchecked"
    if apply:
        for e in slice_:
            checked_hash[e["path"]] = blocking.identity_hash(e)
        con["checked_hash"] = checked_hash
        con["cursor"] = new_cursor
        con["pending_proposals"] = proposed
        con["last_pass"] = now.strftime(TS_FMT)
        if used_backstop:
            con["backstop_last_advance"] = now.strftime(TS_FMT)
        state["consolidation"] = con
    return {"ran": True, "reason": reason, "proposed": proposed,
            "skipped_batches": skipped}
```

Update the call site in `run_sweep` (line ~390) to pass `embedder`:

```python
    _step(result, "consolidate",
          lambda: _consolidate_step(vault, state, consolidator, now, apply, embedder))
```

Note: `embedder` is already resolved earlier in `run_sweep` (line ~383) before the organize step, so it is in scope here.

- [ ] **Step 8: Run to confirm the step tests pass**

Run: `python -m pytest tests/test_librarian.py -k "first_pass_records or second_sweep_skips or changed_entity" -v`
Expected: PASS (3 tests).

- [ ] **Step 9: Surface skipped batches in the step summary**

Add a failing test:

```python
def test_summarize_steps_includes_skipped_batches():
    steps = {"consolidate": {"ran": True, "reason": "3 unchecked",
                             "proposed": [], "skipped_batches": 2}}
    out = librarian._summarize_steps(steps)
    assert out["consolidate"]["skipped_batches"] == 2
```

Run it (FAIL), then in `_summarize_steps` (lines ~269–270) include the field:

```python
    con = steps.get("consolidate")
    out["consolidate"] = con if con is None else {
        "ran": con["ran"], "reason": con["reason"],
        "proposed": len(con["proposed"]),
        "skipped_batches": con.get("skipped_batches", 0),
    }
```

(Match the surrounding dict's existing keys; keep whatever keys it already emits, adding `skipped_batches`.) Re-run to PASS.

- [ ] **Step 10: Run the full librarian + consolidate + blocking suites**

Run: `python -m pytest tests/test_librarian.py tests/test_consolidate.py tests/test_blocking.py -v`
Expected: PASS. Then run the whole suite: `python -m pytest -q`.
Expected: PASS (no unrelated regressions).

- [ ] **Step 11: Commit**

```bash
git add src/tesseract_mcp/librarian.py tests/test_librarian.py
git commit -m "feat(librarian): bounded consolidation slice — cursor, checked_hash, backstop throttle"
```

---

## Self-Review

**1. Spec coverage** (each spec section → task):

| Spec section | Task |
|---|---|
| §1 Entity identity vectors (name+aliases+summary) | Task 1 (identity + summary), Task 2 (vectors) |
| §2 Two indices: vectors vs `checked_hash` coverage | Task 2 (`entity_vectors.json`), Task 9 (`checked_hash` in state) |
| §3 Same-type kNN + union-find + `MAX_CLUSTER` | Tasks 3 & 4 |
| §4 Rolling slice, cursor-as-path, persistence caveat | Task 6 (`select_slice`), Task 9 (persistence under `apply`) |
| §5 Bounded LLM calls, error isolation, never-split-cluster | Task 5 (batching), Task 7 (adjudication) |
| §6 Eager unchecked / throttled backstop | Task 9 (`_backstop_due`, `select_slice(backstop_due=...)`) |
| §7 Durable partial progress + skipped-batch recording + tests | Task 7 (skip count), Task 9 (surfacing), all tasks (tests) |
| Components: `blocking.py`, `consolidate.py`, `librarian.py`, state fields, constants | Tasks 1–9 |
| Out of scope: deleted-note cleanup, auto-apply, model/prompt retuning | Untouched (apply path & prompt unchanged) |

No gaps.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N"/"write tests for the above" — every code and test step shows the actual content. ✓

**3. Type consistency:**
- `compute_entity_vectors(entities, state_root, embedder) -> dict[path, vec]` — same call shape in Task 2, Task 8, Task 9. ✓
- `candidate_clusters(slice, all, vectors) -> list[list[dict]]` feeds `batch_clusters(clusters) -> list[list[list[dict]]]` feeds `adjudicate_batches(backend, batches, all_entities)` — batch nesting (batch → clusters → entities) is consistent across Tasks 4/5/7/8/9. ✓
- `select_slice(...) -> (slice, new_cursor, used_backstop)` — the three-tuple is unpacked identically in Task 6 tests and Task 9. ✓
- `adjudicate_batches(...) -> (merges, skipped)` — two-tuple unpacked in Tasks 7, 8, 9. ✓
- Constant home: blocking constants referenced as `blocking.SLICE_SIZE` from librarian (Tasks 9); `BACKSTOP_MIN_INTERVAL_DAYS` lives in librarian. ✓

One known, accepted limitation (deferred to spec 2, deleted-note cleanup): merged/deleted entities leave stale `checked_hash` / `entity_vectors.json` keys. These are inert (extra dict keys, never read for missing paths) and are cleaned up when sub-project 2 retracts vanished entities.
