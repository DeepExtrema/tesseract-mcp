# IR Benchmark Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reproducible IR evaluation suite (`benchmarks/`) that scores tesseract-mcp's retrieval pipeline on public BEIR datasets (SciFact, NFCorpus) with BM25/vector/hybrid ablations, plus a small-N multi-hop graph track, and injects the score table into the README.

**Architecture:** A top-level `benchmarks/` package (sibling to `src/`, excluded from the wheel) drives the real pipeline through a new pure-ranking seam `hybrid_rank()` extracted from `hybrid.hybrid_search()`. Datasets are downloaded via `ir_datasets`, materialized as throwaway markdown vaults, embedded once with the local bge-micro-v2 path, scored with `ranx`, and written to committed JSON results with git-SHA provenance that `report` renders into the README between markers.

**Tech Stack:** Python ≥3.11, `ranx` (metrics), `ir_datasets` (BEIR loaders), existing `rank-bm25` + `sentence-transformers` pipeline, pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-ir-benchmarks-design.md`

## Global Constraints

- All new dependencies go in a `[bench]` optional extra: `ranx`, `ir_datasets`. Core `dependencies` unchanged.
- `benchmarks/` is NOT shipped in the wheel (`[tool.hatch.build.targets.wheel] packages = ["src/tesseract_mcp"]` already scopes this — do not change it).
- Every file write uses explicit `encoding="utf-8"` (Windows default is not UTF-8).
- Default behavior of `hybrid_search` must be byte-for-byte unchanged: `mode="hybrid"`, `depth=50`. All 21 existing test files must keep passing.
- README benchmark numbers are only ever written by `python -m benchmarks report` between `<!-- bench:start -->` / `<!-- bench:end -->` markers — never by hand.
- GraphRAG is NOT run on BEIR corpora (LLM extraction cost; single-hop queries can't measure it). Graph numbers come only from the curated YAML track and are NOT in the headline README table.
- Repo venv is Python 3.14 (`.venv\Scripts\python.exe`). `ranx` depends on numba, which may not support 3.14. **Contingency (only if `pip install -e .[bench]` fails):** create a dedicated bench venv — `py -3.12 -m venv .venv-bench ; .venv-bench\Scripts\pip install -e .[bench] -e .[dev]` — and run all `python -m benchmarks ...` and bench tests from it. Document whichever python was used in the results `params`.
- Commands below are PowerShell. `PY` means the chosen python: `.venv\Scripts\python.exe` (or `.venv-bench\Scripts\python.exe` under the contingency).
- Working repo: `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`. Current branch: `codex/architecture-roadmap`.

---

### Task 1: `hybrid_rank` seam — `mode` and `depth` parameters

The ablation runner needs (a) single-signal modes and (b) top-100 lists (both ranked lists are currently hard-capped at 50, making Recall@100 impossible). Extract the pure ranking core so the benchmark can pass a precomputed corpus + vectors (no per-query file I/O) while exercising the exact production fusion code.

**Files:**
- Modify: `src/tesseract_mcp/hybrid.py`
- Test: `tests/test_hybrid.py` (append)

**Interfaces:**
- Consumes: existing `bm25.rank(corpus, query, limit)`, `_vector_rank`, `_substring_rank`, `rrf_fuse`, `iter_candidate_notes`, `get_note_vectors`.
- Produces: `hybrid_rank(corpus: dict[str, str], vectors: dict[str, list[float]], embedder: Embedder, query: str, *, mode: str = "hybrid", depth: int = 50, limit: int = 20) -> list[str]` (returns vault-relative note paths, best first). `hybrid_search` gains keyword-only `mode` and `depth` passthroughs with the same defaults. Task 3's runner calls `hybrid_rank` directly.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_hybrid.py`:

```python
def test_mode_bm25_excludes_semantic_only_match(vault, vault_dir):
    # Contractors.md matches "owe money" only semantically (no shared tokens);
    # a pure BM25 ablation must not return it.
    (vault_dir / "Contractors.md").write_text(
        "Outstanding invoices from contractors need review.\n", encoding="utf-8"
    )
    hits = hybrid_search(
        vault, vault.root, FakeSemanticEmbedder(), "who do I owe money to", mode="bm25"
    )
    assert "Contractors.md" not in [h.path for h in hits]


def test_mode_vector_finds_semantic_match_and_excludes_token_only(vault, vault_dir):
    (vault_dir / "Contractors.md").write_text(
        "Outstanding invoices from contractors need review.\n", encoding="utf-8"
    )
    (vault_dir / "Tokens.md").write_text(
        "I owe money to the bank.\n", encoding="utf-8"
    )
    hits = hybrid_search(
        vault, vault.root, FakeSemanticEmbedder(), "who do I owe money to", mode="vector"
    )
    paths = [h.path for h in hits]
    assert "Contractors.md" in paths
    # FakeSemanticEmbedder maps 'owe' notes into the query's region too, so
    # Tokens.md legitimately appears; the ablation contract is about ranking
    # signals, not exclusion here. The bm25-only inverse above is the strict one.


def test_mode_bm25_no_substring_fallback(vault):
    # Single-char query: BM25 token-matches nothing. In hybrid mode the
    # substring fallback kicks in; in a pure bm25 ablation it must not.
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "e", tags=["esg"], mode="bm25")
    assert hits == []


def test_depth_raises_per_list_cap(vault, vault_dir):
    for i in range(60):
        (vault_dir / f"Bulk{i:02d}.md").write_text(
            "shared keyword here\n", encoding="utf-8"
        )
    capped = hybrid_search(
        vault, vault.root, FakeEmbedder(), "shared", mode="bm25", limit=100
    )
    deep = hybrid_search(
        vault, vault.root, FakeEmbedder(), "shared", mode="bm25", depth=100, limit=100
    )
    assert len(capped) == 50   # default depth unchanged
    assert len(deep) == 60


def test_unknown_mode_raises(vault):
    with pytest.raises(ValueError):
        hybrid_search(vault, vault.root, FakeEmbedder(), "x", mode="fuzzy")


def test_hybrid_rank_direct_call():
    from tesseract_mcp.hybrid import hybrid_rank

    corpus = {"a.md": "solar panels on the roof", "b.md": "battery storage unit"}
    vectors = {"a.md": [1.0, 0.0], "b.md": [0.0, 1.0]}

    class QueryVecEmbedder:
        def embed_batch(self, texts):
            return [[1.0, 0.0] for _ in texts]

    ranked = hybrid_rank(corpus, vectors, QueryVecEmbedder(), "solar")
    assert ranked[0] == "a.md"
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hybrid.py -v`
Expected: the 6 new tests FAIL (`TypeError: hybrid_search() got an unexpected keyword argument 'mode'` / `ImportError: cannot import name 'hybrid_rank'`); the 9 pre-existing tests still PASS.

- [ ] **Step 3: Implement** — in `src/tesseract_mcp/hybrid.py`, replace the body of `hybrid_search` and add `hybrid_rank` above it (keep `rrf_fuse`, `_cosine`, `_vector_rank`, `_excerpt`, `_substring_rank` unchanged):

```python
def hybrid_rank(
    corpus: dict[str, str],
    vectors: dict[str, list[float]],
    embedder: Embedder,
    query: str,
    *,
    mode: str = "hybrid",
    depth: int = 50,
    limit: int = 20,
) -> list[str]:
    """Pure ranking core: rank a prebuilt corpus and fuse. `mode` disables
    one signal for ablations ("bm25" | "vector"); "hybrid" is production
    behavior. `depth` is the per-list cap before fusion (Recall@100 needs
    depth >= 100)."""
    if mode not in ("hybrid", "bm25", "vector"):
        raise ValueError(f"Unknown mode: {mode!r} (expected hybrid|bm25|vector)")
    candidate_paths = set(corpus.keys())
    ranked_lists: list[list[str]] = []

    bm25_ranked: list[str] = []
    if mode in ("hybrid", "bm25"):
        bm25_ranked = [p for p, _ in bm25_mod.rank(corpus, query, limit=depth)]
        ranked_lists.append(bm25_ranked)

    if mode in ("hybrid", "vector"):
        query_vec = embedder.embed_batch([query])[0]
        ranked_lists.append(_vector_rank(vectors, candidate_paths, query_vec, depth))

    if mode == "hybrid" and not bm25_ranked:
        # Fallback signal only: BM25 tokenizes [a-z0-9]+, so queries it cannot
        # token-match (e.g. single characters, punctuation-only) fall through
        # to substring matching. When BM25 has results, the alphabetically-
        # ordered substring list would just pollute the fusion. Ablation modes
        # stay pure single signals.
        ranked_lists.append(_substring_rank(corpus, query, limit=depth))

    return rrf_fuse(ranked_lists)[:limit]


def hybrid_search(
    vault: Vault,
    state_root: str | Path,
    embedder: Embedder,
    query: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 20,
    *,
    mode: str = "hybrid",
    depth: int = 50,
) -> list[Hit]:
    candidates = iter_candidate_notes(vault, tags, folder)
    if not candidates:
        return []
    corpus = dict(candidates)
    all_vectors = get_note_vectors(vault, state_root, embedder)
    vectors = {p: v for p, v in all_vectors.items() if p in corpus}
    fused = hybrid_rank(
        corpus, vectors, embedder, query, mode=mode, depth=depth, limit=limit
    )
    return [Hit(rel, _excerpt(corpus[rel], rel, query)) for rel in fused]
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS (new ones plus all pre-existing files — the refactor must not change default behavior).

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/hybrid.py tests/test_hybrid.py
git commit -m "feat(hybrid): extract hybrid_rank seam with mode/depth for IR ablations"
```

---

### Task 2: `benchmarks` package, dataset model, fixture loader, vault materializer

**Files:**
- Create: `benchmarks/__init__.py` (empty file)
- Create: `benchmarks/datasets.py`
- Create: `tests/fixtures/bench-dataset.json`
- Modify: `pyproject.toml` (add `[bench]` extra; add `"."` to pytest pythonpath)
- Test: `tests/test_bench_datasets.py`

**Interfaces:**
- Produces:
  - `BenchDataset` dataclass: `name: str`, `docs: dict[str, tuple[str, str]]` (doc_id → (title, text)), `queries: dict[str, str]`, `qrels: dict[str, dict[str, int]]`.
  - `load_fixture(path: Path) -> BenchDataset` — reads a JSON fixture (tests only).
  - `load_dataset(name: str) -> BenchDataset` — `"scifact" | "nfcorpus"` via `ir_datasets` (network; NOT unit-tested).
  - `materialize_vault(ds: BenchDataset, root: Path) -> dict[str, str]` — writes the throwaway vault, returns rel_path → doc_id; idempotent (returns saved map if `bench-map.json` exists).

- [ ] **Step 1: Update `pyproject.toml`** — change these two sections (leave everything else untouched):

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0"]
bench = ["ranx>=0.3", "ir_datasets>=0.5"]
```

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", "."]
```

- [ ] **Step 2: Install the bench extra**

Run: `.venv\Scripts\pip install -e .[bench]`
Expected: success. **If ranx/numba fails to resolve on Python 3.14**, apply the Global Constraints contingency (create `.venv-bench` with Python 3.12, install `.[bench]` and pytest there, use it as `PY` for all remaining bench steps) and note this in the final commit message of this task.

- [ ] **Step 3: Create the test fixture** `tests/fixtures/bench-dataset.json`:

```json
{
  "name": "fixture",
  "docs": {
    "d1": ["Solar Panels", "Rooftop solar panels convert sunlight into electricity with rising efficiency."],
    "d2": ["Battery Storage", "Grid-scale battery storage smooths supply and demand over hours."],
    "d3": ["Carbon Capture", "Direct air carbon capture removes CO2 from the atmosphere."],
    "d4": ["Wind Turbines", "Offshore wind turbines generate power from steady coastal winds."],
    "d5": ["Hydro Power", "Hydroelectric dams store potential energy in reservoirs."],
    "d6": ["Geothermal", "Geothermal plants tap heat from deep rock formations."],
    "d7": ["Nuclear Fission", "Fission reactors split uranium atoms to boil water."],
    "d8": ["Grid Transmission", "High-voltage lines move electricity across regions."],
    "d9": ["Energy Policy", "Subsidies and standards shape national energy markets."],
    "d10": ["Recycling", "Material recycling recovers metals from consumer waste."]
  },
  "queries": {
    "q1": "solar panel efficiency",
    "q2": "battery storage capacity",
    "q3": "carbon capture technology"
  },
  "qrels": {
    "q1": {"d1": 1},
    "q2": {"d2": 1},
    "q3": {"d3": 1}
  }
}
```

- [ ] **Step 4: Write the failing tests** — `tests/test_bench_datasets.py`:

```python
import json
from pathlib import Path

from tesseract_mcp.search import iter_candidate_notes, parse_frontmatter
from tesseract_mcp.vault import Vault

from benchmarks.datasets import BenchDataset, load_fixture, materialize_vault

FIXTURE = Path(__file__).parent / "fixtures" / "bench-dataset.json"


def test_load_fixture_shapes():
    ds = load_fixture(FIXTURE)
    assert ds.name == "fixture"
    assert len(ds.docs) == 10
    assert ds.docs["d1"][0] == "Solar Panels"
    assert ds.queries["q1"] == "solar panel efficiency"
    assert ds.qrels["q1"] == {"d1": 1}


def test_materialize_vault_writes_notes_and_map(tmp_path):
    ds = load_fixture(FIXTURE)
    rel_to_doc = materialize_vault(ds, tmp_path / "v")
    assert len(rel_to_doc) == 10
    assert set(rel_to_doc.values()) == set(ds.docs.keys())
    # notes are readable through the normal pipeline
    vault = Vault(tmp_path / "v")
    corpus = dict(iter_candidate_notes(vault))
    assert set(corpus.keys()) == set(rel_to_doc.keys())
    # doc_id round-trips via frontmatter too (human-inspectable)
    some_rel, some_doc = next(iter(sorted(rel_to_doc.items())))
    meta = parse_frontmatter(corpus[some_rel])
    assert meta["bench_doc_id"] == some_doc


def test_materialize_vault_is_idempotent(tmp_path):
    ds = load_fixture(FIXTURE)
    first = materialize_vault(ds, tmp_path / "v")
    marker = (tmp_path / "v" / "notes" / "doc-00000.md")
    before = marker.read_text(encoding="utf-8")
    second = materialize_vault(ds, tmp_path / "v")
    assert first == second
    assert marker.read_text(encoding="utf-8") == before
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `PY -m pytest tests/test_bench_datasets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks'`.

- [ ] **Step 6: Implement** — create empty `benchmarks/__init__.py`, then `benchmarks/datasets.py`:

```python
"""Benchmark datasets: BEIR subsets via ir_datasets, JSON fixtures for tests,
and materialization of a dataset into a throwaway markdown vault the real
retrieval pipeline can index."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MAP_FILE = "bench-map.json"
IRDS_KEYS = {"scifact": "beir/scifact/test", "nfcorpus": "beir/nfcorpus/test"}


@dataclass
class BenchDataset:
    name: str
    docs: dict[str, tuple[str, str]]      # doc_id -> (title, text)
    queries: dict[str, str]               # query_id -> text
    qrels: dict[str, dict[str, int]]      # query_id -> {doc_id: relevance}


def load_fixture(path: Path) -> BenchDataset:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return BenchDataset(
        name=data["name"],
        docs={k: (v[0], v[1]) for k, v in data["docs"].items()},
        queries=data["queries"],
        qrels={q: dict(d) for q, d in data["qrels"].items()},
    )


def load_dataset(name: str) -> BenchDataset:
    """Load a BEIR subset by short name. Downloads (checksum-verified by
    ir_datasets) into its cache on first use."""
    import ir_datasets  # deferred: [bench] extra only

    ds = ir_datasets.load(IRDS_KEYS[name])
    docs = {
        d.doc_id: ((getattr(d, "title", "") or ""), d.text) for d in ds.docs_iter()
    }
    queries = {q.query_id: q.text for q in ds.queries_iter()}
    qrels: dict[str, dict[str, int]] = {}
    for qr in ds.qrels_iter():
        qrels.setdefault(qr.query_id, {})[qr.doc_id] = qr.relevance
    return BenchDataset(name=name, docs=docs, queries=queries, qrels=qrels)


def materialize_vault(ds: BenchDataset, root: Path) -> dict[str, str]:
    """Write every doc as notes/doc-NNNNN.md (doc_id in frontmatter) and a
    bench-map.json (rel_path -> doc_id) at the vault root. Idempotent: if the
    map exists, the vault is assumed complete and the saved map is returned."""
    root = Path(root)
    map_path = root / MAP_FILE
    if map_path.exists():
        return json.loads(map_path.read_text(encoding="utf-8"))
    (root / ".obsidian").mkdir(parents=True, exist_ok=True)
    (root / "notes").mkdir(parents=True, exist_ok=True)
    rel_to_doc: dict[str, str] = {}
    for i, (doc_id, (title, text)) in enumerate(sorted(ds.docs.items())):
        rel = f"notes/doc-{i:05d}.md"
        content = (
            f"---\nbench_doc_id: {json.dumps(doc_id)}\n---\n\n"
            f"# {title or doc_id}\n\n{text}\n"
        )
        (root / rel).write_text(content, encoding="utf-8")
        rel_to_doc[rel] = doc_id
    map_path.write_text(json.dumps(rel_to_doc), encoding="utf-8")
    return rel_to_doc
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `PY -m pytest tests/test_bench_datasets.py -v`
Expected: 3 PASS. Also run `.venv\Scripts\python.exe -m pytest -q` — full suite still green (pythonpath change must not break anything).

- [ ] **Step 8: Commit**

```powershell
git add benchmarks/ tests/test_bench_datasets.py tests/fixtures/bench-dataset.json pyproject.toml
git commit -m "feat(bench): benchmarks package with dataset model, fixture loader, vault materializer"
```

---

### Task 3: Ablation runner

**Files:**
- Create: `benchmarks/runner.py`
- Test: `tests/test_bench_runner.py`

**Interfaces:**
- Consumes: `hybrid_rank` (Task 1), `BenchDataset` / `materialize_vault` (Task 2), `get_note_vectors`, `iter_candidate_notes`, `Vault`.
- Produces: `run_dataset(ds: BenchDataset, vault_root: Path, state_root: Path, embedder: Embedder, mode: str, top_k: int = 100) -> dict[str, dict[str, float]]` — query_id → {doc_id: score}, score = 1/rank (ranx consumes this shape). Module constant `TOP_K = 100`.

- [ ] **Step 1: Write the failing tests** — `tests/test_bench_runner.py`:

```python
from pathlib import Path

import pytest

from benchmarks.datasets import load_fixture
from benchmarks.runner import run_dataset

FIXTURE = Path(__file__).parent / "fixtures" / "bench-dataset.json"


class KeywordEmbedder:
    """Deterministic vectors keyed to the fixture's three topics."""

    VOCAB = ["solar", "battery", "carbon"]

    def embed_batch(self, texts):
        return [
            [1.0 if word in t.lower() else 0.0 for word in self.VOCAB]
            for t in texts
        ]


@pytest.fixture
def fixture_ds():
    return load_fixture(FIXTURE)


def _top_doc(run, qid):
    return max(run[qid].items(), key=lambda kv: kv[1])[0]


def test_bm25_mode_ranks_relevant_doc_first(fixture_ds, tmp_path):
    run = run_dataset(
        fixture_ds, tmp_path / "v", tmp_path / "s", KeywordEmbedder(), mode="bm25"
    )
    assert set(run.keys()) == {"q1", "q2", "q3"}
    assert _top_doc(run, "q1") == "d1"
    assert _top_doc(run, "q2") == "d2"


def test_vector_mode_ranks_relevant_doc_first(fixture_ds, tmp_path):
    run = run_dataset(
        fixture_ds, tmp_path / "v", tmp_path / "s", KeywordEmbedder(), mode="vector"
    )
    assert _top_doc(run, "q1") == "d1"


def test_hybrid_mode_ranks_relevant_doc_first(fixture_ds, tmp_path):
    run = run_dataset(
        fixture_ds, tmp_path / "v", tmp_path / "s", KeywordEmbedder(), mode="hybrid"
    )
    assert _top_doc(run, "q1") == "d1"


def test_scores_are_reciprocal_rank(fixture_ds, tmp_path):
    run = run_dataset(
        fixture_ds, tmp_path / "v", tmp_path / "s", KeywordEmbedder(), mode="bm25"
    )
    best = _top_doc(run, "q1")
    assert run["q1"][best] == 1.0  # 1/rank at rank 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PY -m pytest tests/test_bench_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.runner'`.

- [ ] **Step 3: Implement** — `benchmarks/runner.py`:

```python
"""Drive the real retrieval pipeline over a materialized benchmark vault.

Corpus and vectors are built ONCE per (dataset, mode) run — the per-query
work is pure ranking via hybrid_rank, so a full BEIR pass is minutes of
CPU, not hours of file I/O and re-embedding."""

from __future__ import annotations

from pathlib import Path

from tesseract_mcp.embeddings import Embedder, get_note_vectors
from tesseract_mcp.hybrid import hybrid_rank
from tesseract_mcp.search import iter_candidate_notes
from tesseract_mcp.vault import Vault

from .datasets import BenchDataset, materialize_vault

TOP_K = 100


def run_dataset(
    ds: BenchDataset,
    vault_root: Path,
    state_root: Path,
    embedder: Embedder,
    mode: str,
    top_k: int = TOP_K,
) -> dict[str, dict[str, float]]:
    """query_id -> {doc_id: 1/rank} for the top_k results per query."""
    vault_root = Path(vault_root)
    vault_root.mkdir(parents=True, exist_ok=True)
    rel_to_doc = materialize_vault(ds, vault_root)
    vault = Vault(vault_root)
    corpus = dict(iter_candidate_notes(vault))

    state_root = Path(state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    vectors = get_note_vectors(vault, state_root, embedder)
    missing = set(corpus) - set(vectors)
    if missing:
        # A silent gap would corrupt the vector ablation — fail loudly.
        raise RuntimeError(
            f"{len(missing)} notes lack embeddings, e.g. {sorted(missing)[:3]}"
        )

    run: dict[str, dict[str, float]] = {}
    for qid, qtext in ds.queries.items():
        ranked = hybrid_rank(
            corpus, vectors, embedder, qtext, mode=mode, depth=top_k, limit=top_k
        )
        scores: dict[str, float] = {}
        for rank, rel in enumerate(ranked, start=1):
            doc_id = rel_to_doc.get(rel)
            if doc_id is None:
                raise RuntimeError(f"Note {rel} has no doc-id mapping")
            scores[doc_id] = 1.0 / rank
        run[qid] = scores
    return run
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PY -m pytest tests/test_bench_runner.py tests/test_bench_datasets.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add benchmarks/runner.py tests/test_bench_runner.py
git commit -m "feat(bench): ablation runner driving hybrid_rank over materialized vaults"
```

---

### Task 4: Metrics via ranx

**Files:**
- Create: `benchmarks/metrics.py`
- Test: `tests/test_bench_metrics.py`

**Interfaces:**
- Consumes: qrels dict (Task 2 shape) and run dict (Task 3 shape).
- Produces: `score_run(qrels: dict[str, dict[str, int]], run: dict[str, dict[str, float]], metrics: tuple[str, ...] = DEFAULT_METRICS) -> dict[str, float]`; `DEFAULT_METRICS = ("ndcg@10", "recall@10", "recall@100", "mrr@10")`.

- [ ] **Step 1: Write the failing tests** — `tests/test_bench_metrics.py`. The expected values are hand-computable: with one relevant doc at rank 2, MRR@10 = 1/2 and nDCG@10 = (1/log2(3)) / 1 ≈ 0.6309.

```python
from benchmarks.metrics import DEFAULT_METRICS, score_run


def test_perfect_run_scores_one():
    qrels = {"q1": {"d1": 1}}
    run = {"q1": {"d1": 1.0, "d2": 0.5}}
    scores = score_run(qrels, run)
    assert set(scores.keys()) == set(DEFAULT_METRICS)
    assert scores["ndcg@10"] == 1.0
    assert scores["mrr@10"] == 1.0
    assert scores["recall@10"] == 1.0


def test_relevant_at_rank_two():
    qrels = {"q1": {"d1": 1}}
    run = {"q1": {"d2": 1.0, "d1": 0.5}}
    scores = score_run(qrels, run)
    assert scores["mrr@10"] == 0.5
    assert round(scores["ndcg@10"], 4) == 0.6309
    assert scores["recall@10"] == 1.0


def test_missed_doc_scores_zero():
    qrels = {"q1": {"d1": 1}}
    run = {"q1": {"d2": 1.0}}
    scores = score_run(qrels, run)
    assert scores["ndcg@10"] == 0.0
    assert scores["recall@100"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PY -m pytest tests/test_bench_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.metrics'`.

- [ ] **Step 3: Implement** — `benchmarks/metrics.py`:

```python
"""Scoring via ranx — an established IR evaluation library, not hand-rolled
math; that is what makes the published numbers citable."""

from __future__ import annotations

DEFAULT_METRICS = ("ndcg@10", "recall@10", "recall@100", "mrr@10")


def score_run(
    qrels: dict[str, dict[str, int]],
    run: dict[str, dict[str, float]],
    metrics: tuple[str, ...] = DEFAULT_METRICS,
) -> dict[str, float]:
    from ranx import Qrels, Run, evaluate  # deferred: [bench] extra only

    scored = evaluate(Qrels(qrels), Run(run), list(metrics))
    if isinstance(scored, float):  # ranx returns a bare float for one metric
        return {metrics[0]: scored}
    return {m: float(v) for m, v in scored.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PY -m pytest tests/test_bench_metrics.py -v`
Expected: 3 PASS. (First ranx import may take ~30s while numba JIT-compiles; that is normal.)

- [ ] **Step 5: Commit**

```powershell
git add benchmarks/metrics.py tests/test_bench_metrics.py
git commit -m "feat(bench): ranx metric scoring (ndcg/recall/mrr)"
```

---

### Task 5: Results persistence, README table, stale-SHA guard

**Files:**
- Create: `benchmarks/report.py`
- Create: `benchmarks/results/.gitkeep` (empty file, so the dir exists in git)
- Modify: `README.md` (add Benchmarks section with markers)
- Test: `tests/test_bench_report.py`

**Interfaces:**
- Consumes: metric dicts (Task 4).
- Produces:
  - `git_sha(repo_root: Path) -> str`
  - `save_results(results_dir: Path, dataset: str, mode: str, metrics: dict[str, float], params: dict, repo_root: Path) -> Path` — writes `<dataset>-<mode>-<sha10>.json` with provenance (`dataset`, `mode`, `metrics`, `params`, `git_sha`, `date`).
  - `latest_results(results_dir: Path) -> list[dict]` — newest result per (dataset, mode), sorted.
  - `render_table(rows: list[dict]) -> str` — markdown table; silently skips rows without `ndcg@10` (graph-track rows).
  - `inject_readme(readme: Path, table: str, rows: list[dict], repo_root: Path, force: bool = False) -> None` — replaces the block between `<!-- bench:start -->` / `<!-- bench:end -->`; raises RuntimeError if any row's `git_sha` != HEAD unless `force`.

- [ ] **Step 1: Write the failing tests** — `tests/test_bench_report.py`:

```python
import json
from pathlib import Path

import pytest

from benchmarks.report import (
    END,
    START,
    inject_readme,
    latest_results,
    render_table,
    save_results,
)

METRICS = {"ndcg@10": 0.5, "recall@10": 0.6, "recall@100": 0.9, "mrr@10": 0.4}


@pytest.fixture
def repo(tmp_path):
    """A tiny real git repo so git_sha/save_results/inject work end-to-end."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_save_and_load_roundtrip(repo, tmp_path):
    rdir = tmp_path / "results"
    p = save_results(rdir, "scifact", "hybrid", METRICS, {"top_k": 100}, repo)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["dataset"] == "scifact"
    assert data["metrics"]["ndcg@10"] == 0.5
    assert len(data["git_sha"]) == 40
    rows = latest_results(rdir)
    assert len(rows) == 1 and rows[0]["mode"] == "hybrid"


def test_render_table_skips_graph_rows():
    rows = [
        {"dataset": "scifact", "mode": "hybrid", "metrics": METRICS},
        {"dataset": "graph-eval", "mode": "related_notes",
         "metrics": {"hit_rate": 0.8, "mrr": 0.7}},
    ]
    table = render_table(rows)
    assert "scifact" in table
    assert "graph-eval" not in table
    assert "0.5000" in table


def test_inject_readme_replaces_between_markers(repo, tmp_path):
    rdir = tmp_path / "results"
    save_results(rdir, "scifact", "hybrid", METRICS, {}, repo)
    rows = latest_results(rdir)
    readme = repo / "README.md"
    readme.write_text(
        f"# Title\n\n{START}\nold\n{END}\n\ntail\n", encoding="utf-8"
    )
    inject_readme(readme, render_table(rows), rows, repo)
    text = readme.read_text(encoding="utf-8")
    assert "old" not in text
    assert "0.5000" in text
    assert text.startswith("# Title")
    assert text.rstrip().endswith("tail")


def test_inject_refuses_stale_sha(repo, tmp_path):
    rdir = tmp_path / "results"
    save_results(rdir, "scifact", "hybrid", METRICS, {}, repo)
    # advance HEAD so the saved sha goes stale
    import subprocess

    (repo / "y.txt").write_text("y", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "more"], cwd=repo, check=True)
    rows = latest_results(rdir)
    readme = repo / "README.md"
    readme.write_text(f"{START}\n{END}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="different commit"):
        inject_readme(readme, render_table(rows), rows, repo)
    inject_readme(readme, render_table(rows), rows, repo, force=True)  # override
    assert "0.5000" in readme.read_text(encoding="utf-8")


def test_inject_requires_markers(repo, tmp_path):
    readme = repo / "README.md"
    readme.write_text("no markers here\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="markers"):
        inject_readme(readme, "table", [], repo)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PY -m pytest tests/test_bench_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.report'`.

- [ ] **Step 3: Implement** — `benchmarks/report.py`:

```python
"""Results persistence with provenance, and README score-table injection.

Scores are never hand-edited: `python -m benchmarks report` regenerates the
block between the markers from the committed results JSON."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

START = "<!-- bench:start -->"
END = "<!-- bench:end -->"

MODE_LABELS = {"bm25": "BM25 only", "vector": "Vector only", "hybrid": "**Hybrid RRF**"}


def git_sha(repo_root: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def save_results(
    results_dir: Path,
    dataset: str,
    mode: str,
    metrics: dict[str, float],
    params: dict,
    repo_root: Path,
) -> Path:
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    sha = git_sha(repo_root)
    payload = {
        "dataset": dataset,
        "mode": mode,
        "metrics": metrics,
        "params": params,
        "git_sha": sha,
        "date": datetime.now().isoformat(timespec="seconds"),
    }
    path = results_dir / f"{dataset}-{mode}-{sha[:10]}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def latest_results(results_dir: Path) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    for p in sorted(Path(results_dir).glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        key = (data["dataset"], data["mode"])
        if key not in best or data["date"] > best[key]["date"]:
            best[key] = data
    return [best[k] for k in sorted(best)]


def render_table(rows: list[dict]) -> str:
    lines = [
        "| Dataset | Mode | nDCG@10 | Recall@10 | Recall@100 | MRR@10 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        m = r["metrics"]
        if "ndcg@10" not in m:  # graph-track rows live in docs, not this table
            continue
        lines.append(
            f"| {r['dataset']} | {MODE_LABELS.get(r['mode'], r['mode'])} "
            f"| {m['ndcg@10']:.4f} | {m['recall@10']:.4f} "
            f"| {m['recall@100']:.4f} | {m['mrr@10']:.4f} |"
        )
    return "\n".join(lines)


def inject_readme(
    readme: Path,
    table: str,
    rows: list[dict],
    repo_root: Path,
    force: bool = False,
) -> None:
    head = git_sha(repo_root)
    stale = [r for r in rows if r.get("git_sha") and r["git_sha"] != head]
    if stale and not force:
        raise RuntimeError(
            f"{len(stale)} result file(s) were produced at a different commit "
            "than HEAD; re-run the benchmark or pass --force."
        )
    text = Path(readme).read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise RuntimeError(f"README is missing {START}/{END} markers")
    pre, rest = text.split(START, 1)
    _, post = rest.split(END, 1)
    Path(readme).write_text(
        pre + START + "\n" + table + "\n" + END + post, encoding="utf-8"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PY -m pytest tests/test_bench_report.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Add the Benchmarks section to `README.md`** — insert after the `## What's inside` section (before `## Tools`), and create empty `benchmarks/results/.gitkeep`:

````markdown
## Benchmarks

Retrieval quality is measured on public BEIR datasets with their official
queries and relevance judgments, so every number below is reproducible and
directly comparable to published baselines. Embedder: `TaylorAI/bge-micro-v2`
(the exact model the production pipeline uses). Reproduce with:

```powershell
pip install -e .[bench]
python -m benchmarks run
python -m benchmarks report
```

<!-- bench:start -->
_No published results yet — run `python -m benchmarks run`._
<!-- bench:end -->

Baselines for context: the [BEIR paper](https://arxiv.org/abs/2104.08663)
reports BM25 nDCG@10 of 0.665 (SciFact) and 0.325 (NFCorpus). The graph
layer is evaluated separately — see
[Measuring the graph](docs/ARCHITECTURE.md#measuring-the-graph).
````

- [ ] **Step 6: Commit**

```powershell
git add benchmarks/report.py benchmarks/results/.gitkeep tests/test_bench_report.py README.md
git commit -m "feat(bench): results provenance, README score table with stale-SHA guard"
```

---

### Task 6: CLI (`python -m benchmarks`)

**Files:**
- Create: `benchmarks/__main__.py`
- Test: `tests/test_bench_cli.py`

**Interfaces:**
- Consumes: `load_dataset` (Task 2), `run_dataset`/`TOP_K` (Task 3), `score_run` (Task 4), `save_results`/`latest_results`/`render_table`/`inject_readme` (Task 5), `graphtrack.run_graph_eval` (Task 7 — wired here behind the `graph` subcommand; until Task 7 lands the import is deferred inside the command so `run`/`report` work standalone).
- Produces: `build_parser() -> argparse.ArgumentParser`; subcommands `run [--dataset scifact|nfcorpus|all] [--mode bm25|vector|hybrid|all]`, `report [--force]`, `graph [--vault PATH]`.

- [ ] **Step 1: Write the failing test** — `tests/test_bench_cli.py`:

```python
import pytest

from benchmarks.__main__ import build_parser


def test_run_defaults_to_all():
    args = build_parser().parse_args(["run"])
    assert args.cmd == "run"
    assert args.dataset == "all"
    assert args.mode == "all"


def test_run_rejects_unknown_dataset():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--dataset", "msmarco"])


def test_report_force_flag():
    args = build_parser().parse_args(["report", "--force"])
    assert args.force is True


def test_graph_vault_arg():
    args = build_parser().parse_args(["graph", "--vault", "C:/vaults/x"])
    assert args.vault == "C:/vaults/x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PY -m pytest tests/test_bench_cli.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `benchmarks.__main__`).

- [ ] **Step 3: Implement** — `benchmarks/__main__.py`:

```python
"""CLI: python -m benchmarks run|report|graph"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import datasets, metrics, report, runner

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).resolve().parent / "results"
BENCH_HOME = Path.home() / ".tesseract-mcp" / "benchmarks"

DATASETS = ("scifact", "nfcorpus")
MODES = ("bm25", "vector", "hybrid")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="benchmarks")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="run BEIR ablations")
    run_p.add_argument("--dataset", choices=DATASETS + ("all",), default="all")
    run_p.add_argument("--mode", choices=MODES + ("all",), default="all")

    rep_p = sub.add_parser("report", help="inject latest results into README")
    rep_p.add_argument("--force", action="store_true",
                       help="allow results from a different commit than HEAD")

    g_p = sub.add_parser("graph", help="small-N multi-hop graph eval (private vault)")
    g_p.add_argument("--vault", default=None,
                     help="vault root (default: TESSERACT_VAULT_PATH)")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.cmd == "run":
        from tesseract_mcp.embeddings import SentenceTransformerEmbedder

        names = DATASETS if args.dataset == "all" else (args.dataset,)
        modes = MODES if args.mode == "all" else (args.mode,)
        embedder = SentenceTransformerEmbedder()
        for name in names:
            ds = datasets.load_dataset(name)
            vault_root = BENCH_HOME / "vaults" / name
            state_root = BENCH_HOME / "state" / name
            for mode in modes:
                print(f"[{name}] mode={mode} ({len(ds.queries)} queries)...")
                run = runner.run_dataset(ds, vault_root, state_root, embedder, mode)
                scored = metrics.score_run(ds.qrels, run)
                path = report.save_results(
                    RESULTS_DIR, name, mode, scored,
                    {"embedder": "TaylorAI/bge-micro-v2", "top_k": runner.TOP_K},
                    REPO_ROOT,
                )
                pretty = {k: round(v, 4) for k, v in scored.items()}
                print(f"  {pretty}  -> {path.name}")

    elif args.cmd == "report":
        rows = report.latest_results(RESULTS_DIR)
        table = report.render_table(rows)
        beir_rows = [r for r in rows if "ndcg@10" in r["metrics"]]
        report.inject_readme(
            REPO_ROOT / "README.md", table, beir_rows, REPO_ROOT, force=args.force
        )
        print(table)
        print("README updated.")

    elif args.cmd == "graph":
        from . import graphtrack  # Task 7; deferred so run/report work before it lands

        graphtrack.run_graph_eval(args.vault, RESULTS_DIR, REPO_ROOT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PY -m pytest tests/test_bench_cli.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Smoke-test the CLI parser end-to-end**

Run: `PY -m benchmarks report`
Expected: prints the (empty-row) table header and "README updated." OR — if `benchmarks/results/` has only `.gitkeep` — prints just the two header lines and updates the README placeholder block to an empty table. Verify with `git diff README.md`, then `git checkout -- README.md` to discard the smoke-test edit.

- [ ] **Step 6: Commit**

```powershell
git add benchmarks/__main__.py tests/test_bench_cli.py
git commit -m "feat(bench): CLI with run/report/graph subcommands"
```

---

### Task 7: Graph track (small-N multi-hop eval) + ARCHITECTURE docs

**Files:**
- Create: `benchmarks/graphtrack.py`
- Create: `benchmarks/queries/graph-eval.yaml`
- Modify: `docs/ARCHITECTURE.md` (append a `## Measuring the graph` section)
- Test: `tests/test_bench_graphtrack.py`

**Interfaces:**
- Consumes: `cache.related_notes(db_path, vault, path, hops) -> list[dict]` (each dict has `"path"` and `"via"` keys), `indexer.db_path(vault_root)`, `Vault`, `report.save_results` (Task 5).
- Produces: `score_entry(expected: list[str], results: list[str]) -> tuple[float, float]` (hit_rate, reciprocal-rank of first expected hit); `run_graph_eval(vault_path: str | None, results_dir: Path, repo_root: Path) -> None` (called by the CLI's `graph` subcommand).

- [ ] **Step 1: Write the failing tests** — `tests/test_bench_graphtrack.py` (scoring is a pure function; the `related_notes` call itself is covered by `tests/test_graph.py`/`test_cache.py` and exercised manually in Task 8):

```python
from benchmarks.graphtrack import score_entry


def test_all_expected_found_first():
    hit_rate, rr = score_entry(["a.md", "b.md"], ["a.md", "b.md", "c.md"])
    assert hit_rate == 1.0
    assert rr == 1.0


def test_partial_hits_and_late_first_hit():
    hit_rate, rr = score_entry(["a.md", "b.md"], ["x.md", "b.md"])
    assert hit_rate == 0.5
    assert rr == 0.5  # first expected note found at rank 2


def test_no_hits():
    hit_rate, rr = score_entry(["a.md"], ["x.md", "y.md"])
    assert hit_rate == 0.0
    assert rr == 0.0


def test_empty_expected_scores_zero():
    hit_rate, rr = score_entry([], ["x.md"])
    assert hit_rate == 0.0
    assert rr == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PY -m pytest tests/test_bench_graphtrack.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmarks.graphtrack'`.

- [ ] **Step 3: Implement** — `benchmarks/graphtrack.py`:

```python
"""Small-N multi-hop graph evaluation against the live private vault.

BEIR cannot measure GraphRAG: its queries are single-hop and running LLM
entity extraction over thousands of docs per run is prohibitive. This track
scores related_notes() on a hand-curated multi-hop query set instead —
honest small-N numbers, reported in ARCHITECTURE.md with caveats, never in
the headline README table."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from tesseract_mcp import cache as cache_mod
from tesseract_mcp.indexer import db_path
from tesseract_mcp.vault import Vault

from .report import save_results

QUERIES_FILE = Path(__file__).resolve().parent / "queries" / "graph-eval.yaml"


def score_entry(expected: list[str], results: list[str]) -> tuple[float, float]:
    """(hit_rate, reciprocal rank of the first expected note)."""
    if not expected:
        return 0.0, 0.0
    expected_set = set(expected)
    hit_rate = len(expected_set & set(results)) / len(expected_set)
    rr = 0.0
    for rank, path in enumerate(results, start=1):
        if path in expected_set:
            rr = 1.0 / rank
            break
    return hit_rate, rr


def run_graph_eval(
    vault_path: str | None, results_dir: Path, repo_root: Path
) -> None:
    root = vault_path or os.environ.get("TESSERACT_VAULT_PATH")
    if not root:
        raise SystemExit("Pass --vault or set TESSERACT_VAULT_PATH")
    vault = Vault(root)
    db = db_path(vault.root)
    if not db.exists():
        raise SystemExit(f"No graph cache at {db} — run the index_brain tool first.")

    entries = yaml.safe_load(QUERIES_FILE.read_text(encoding="utf-8")) or []
    entries = [e for e in entries if not e.get("sample")]
    if not entries:
        raise SystemExit(
            "graph-eval.yaml has no curated entries yet (samples are skipped). "
            "Add 15-25 real multi-hop queries before publishing graph numbers."
        )

    hit_rates, rrs = [], []
    for e in entries:
        results = [
            r["path"]
            for r in cache_mod.related_notes(db, vault, e["seed"], hops=e.get("hops", 2))
        ]
        hr, rr = score_entry(e["expected"], results)
        hit_rates.append(hr)
        rrs.append(rr)
        print(f"  {e['name']}: hit_rate={hr:.2f} rr={rr:.2f}")

    summary = {
        "hit_rate": sum(hit_rates) / len(hit_rates),
        "mrr": sum(rrs) / len(rrs),
    }
    path = save_results(
        results_dir, "graph-eval", "related_notes", summary,
        {"n_queries": len(entries)}, repo_root,
    )
    print(f"graph-eval over {len(entries)} queries: "
          f"hit_rate={summary['hit_rate']:.3f} mrr={summary['mrr']:.3f} -> {path.name}")
```

- [ ] **Step 4: Create `benchmarks/queries/graph-eval.yaml`** — sample entries are skipped by the runner; the human curates real ones:

```yaml
# Hand-curated multi-hop queries against the PRIVATE Tesseract vault.
# Each entry:
#   name:     short label
#   seed:     vault-relative note path the traversal starts from
#   expected: notes that SHOULD be reachable through shared graph entities
#   hops:     traversal depth (default 2)
#   sample:   true marks an illustration; the runner skips it
#
# Curate 15-25 real entries (sample: false or omitted) before publishing
# graph numbers. Small-N caveat: these results characterize, they don't prove.
- name: sample-project-to-infrastructure
  sample: true
  seed: Projects/Sentinel ESG.md
  expected:
    - Claude/Concepts/CouchDB.md
  hops: 2
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PY -m pytest tests/test_bench_graphtrack.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Append to `docs/ARCHITECTURE.md`** (new top-level section at the end of the file):

```markdown
## Measuring the graph

The BEIR table in the README deliberately excludes GraphRAG. BEIR queries
are single-hop — "find the document about X" — which BM25 and vectors
already answer; the graph exists for multi-hop questions ("what connects
this project to that infrastructure?") that no public IR dataset with
qrels measures. Running LLM entity extraction over a 5K-doc corpus on
every benchmark run would also be slow and expensive.

So the graph is evaluated on its own track: a hand-curated set of
multi-hop queries against the real vault
(`benchmarks/queries/graph-eval.yaml`), each naming a seed note and the
notes that should be reachable through shared entities. `python -m
benchmarks graph` scores `related_notes` on hit-rate and MRR and writes
the result next to the BEIR numbers in `benchmarks/results/`.

Caveat, stated plainly: this is a small-N experiment (tens of queries,
one vault). It characterizes the graph's behavior; it does not prove
general retrieval gains. That honesty is the point — the number exists,
it has provenance, and it can only improve under scrutiny.
```

- [ ] **Step 7: Commit**

```powershell
git add benchmarks/graphtrack.py benchmarks/queries/graph-eval.yaml tests/test_bench_graphtrack.py docs/ARCHITECTURE.md
git commit -m "feat(bench): small-N multi-hop graph track with curated query set"
```

---

### Task 8: First real BEIR run — publish the numbers

This is the milestone task: real downloads, real embedding, real scores, committed results, README table live. No new code.

- [ ] **Step 1: Full suite green first**

Run: `.venv\Scripts\python.exe -m pytest -q` (and `PY -m pytest -q` if using the contingency venv)
Expected: everything PASSES.

- [ ] **Step 2: Run SciFact, all modes** (first run downloads the dataset via ir_datasets and embeds ~5K docs with bge-micro-v2 on CPU — expect 15-45 minutes for the embedding pass; subsequent runs reuse the cache and take minutes)

Run: `PY -m benchmarks run --dataset scifact`
Expected: three result lines printed (bm25, vector, hybrid), three JSON files in `benchmarks/results/`.

- [ ] **Step 3: Run NFCorpus, all modes**

Run: `PY -m benchmarks run --dataset nfcorpus`
Expected: three more result files.

- [ ] **Step 4: Sanity-check the numbers against literature.** BEIR reports BM25 nDCG@10 ≈ 0.665 on SciFact and ≈ 0.325 on NFCorpus. Our BM25L-over-markdown-notes variant should land in a plausible neighborhood (±0.15). If a score is wildly off (e.g. nDCG@10 < 0.2 on SciFact for bm25), STOP and debug the doc-ID mapping / materialization before publishing — don't rationalize it.

- [ ] **Step 5: Inject the table and commit results + README together** (same commit, so the stale-SHA guard stays meaningful going forward; the results were produced at the pre-commit HEAD, so use `--force` for this first injection and say so in the commit message)

Run: `PY -m benchmarks report --force`
Then:

```powershell
git add benchmarks/results/*.json README.md
git commit -m "bench: first published BEIR results (scifact, nfcorpus) [report --force: results predate this commit]"
```

- [ ] **Step 6: Record the outcome.** Whatever the numbers say — including hybrid losing to BM25 on scientific text, which is plausible with a small embedder — they go in the README as-is. If hybrid loses, add one honest sentence under the table, e.g.: "On these scientific-text corpora the bge-micro-v2 vector signal currently drags fusion below pure BM25 — a measured argument for a larger embedder, which this harness exists to evaluate."

---

## Verification (whole plan)

1. `.venv\Scripts\python.exe -m pytest -q` — full suite green, including all pre-existing tests (Task 1's refactor is behavior-preserving by default).
2. `PY -m benchmarks run --dataset scifact --mode bm25` twice — second run completes in minutes (embedding cache hit).
3. `git log --oneline benchmarks/results/` — every published number traces to a commit.
4. README table renders on GitHub; numbers match `benchmarks/results/*.json`.
5. `PY -m benchmarks graph` fails with the curation message until real YAML entries exist (expected).
