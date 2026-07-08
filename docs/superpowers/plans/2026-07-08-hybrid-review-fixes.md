# Hybrid Search Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the two findings from the post-implementation review of `feature/hybrid-search-graphrag`: (1) the `_query_tokens_match` filter kills semantic recall for multi-word queries, and (2) the alphabetical substring signal pollutes ranking on every query instead of only serving as a fallback.

**Architecture:** Both fixes are confined to `src/tesseract_mcp/hybrid.py` plus test updates. Fix 1 deletes the post-fusion all-tokens-required filter and rewrites the one server test whose exact-equality assertion motivated it (ranked engines return ranked lists; assert rank, not exclusivity). Fix 2 gates the `_substring_rank` third RRF signal so it only fires when BM25 returns nothing (the single-character-query case it was added for).

**Tech Stack:** Python 3.11+, pytest. No new dependencies.

## Global Constraints

- Work happens in the existing worktree: `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\.worktrees\hybrid-search-graphrag` on branch `feature/hybrid-search-graphrag`. Do NOT create a new worktree or branch.
- Run tests with the worktree's own venv: `.venv\Scripts\python.exe -m pytest` (the worktree has its own `.venv` with `rank-bm25` and `sentence-transformers` installed; the main repo's venv does not).
- `search_brain`'s tool name and parameter signature (`query, tags=None, folder=None, limit=20`) do not change.
- The spec's core semantic-recall requirement (spec: 2026-07-08-hybrid-search-graphrag-design.md): a query sharing zero literal tokens with a note MUST still be able to surface that note via vector similarity. Fix 1 restores this; no future "relevance tightening" may reintroduce a literal-token requirement on multi-word queries.
- The full suite (182 tests as of `d8343be`) must pass after each task.

---

## Task 1: Remove the all-tokens filter; restore semantic recall

**Files:**
- Modify: `src/tesseract_mcp/hybrid.py` (delete `_query_tokens_match`, lines 73-78, and its use in `hybrid_search`, lines 103-107)
- Modify: `tests/test_server.py:40-47` (`test_search_brain_returns_dicts`)
- Test: `tests/test_hybrid.py` (new regression test)

**Interfaces:**
- Consumes: `hybrid_search(vault, state_root, embedder, query, tags=None, folder=None, limit=20) -> list[Hit]` (existing), `rrf_fuse(ranked_lists, k=60) -> list[str]` (existing), `search.Hit` dataclass with `.path` and `.excerpt` (existing).
- Produces: no signature changes — behavior change only: multi-token queries no longer require every token to appear literally in a result note.

- [ ] **Step 1: Write the failing regression test**

Add to `tests/test_hybrid.py`:

```python
class FakeSemanticEmbedder:
    """Embeds 'owe money' queries and 'invoice' notes into the same region
    of vector space despite zero shared tokens — the paraphrase case the
    vector half of hybrid search exists for."""

    def embed_batch(self, texts):
        out = []
        for t in texts:
            lower = t.lower()
            if "owe" in lower or "invoice" in lower:
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out


def test_hybrid_search_semantic_match_without_shared_tokens(vault, vault_dir):
    (vault_dir / "Contractors.md").write_text(
        "Outstanding invoices from contractors need review.\n", encoding="utf-8"
    )
    hits = hybrid_search(
        vault, vault.root, FakeSemanticEmbedder(), "who do I owe money to"
    )
    assert "Contractors.md" in [h.path for h in hits]
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the worktree root `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\.worktrees\hybrid-search-graphrag`):

`.venv\Scripts\python.exe -m pytest tests/test_hybrid.py::test_hybrid_search_semantic_match_without_shared_tokens -v`

Expected: FAIL — `Contractors.md` ranks first by cosine similarity but is then dropped by `_query_tokens_match` because the note does not literally contain "who", "do", "i", "owe", "money", and "to".

- [ ] **Step 3: Delete the filter**

In `src/tesseract_mcp/hybrid.py`, delete the `_query_tokens_match` function entirely:

```python
# DELETE these lines (73-78):
def _query_tokens_match(text: str, query: str) -> bool:
    tokens = bm25_mod.tokenize(query)
    if len(tokens) <= 1:
        return True
    lower = text.lower()
    return all(tok in lower for tok in tokens)
```

And replace the end of `hybrid_search` (current lines 102-107):

```python
    substring_ranked = _substring_rank(corpus, query, limit=50)
    # Third RRF signal: BM25 tokenizes [a-z0-9]+ only, so single-char queries
    # like "e" (test_hybrid_search_respects_tag_filter) need substring fallback.
    fused = rrf_fuse([bm25_ranked, vector_ranked, substring_ranked])
    filtered = [rel for rel in fused if _query_tokens_match(corpus[rel], query)][:limit]
    return [Hit(rel, _excerpt(corpus[rel], rel, query)) for rel in filtered]
```

with:

```python
    substring_ranked = _substring_rank(corpus, query, limit=50)
    # Third RRF signal: BM25 tokenizes [a-z0-9]+ only, so single-char queries
    # like "e" (test_hybrid_search_respects_tag_filter) need substring fallback.
    fused = rrf_fuse([bm25_ranked, vector_ranked, substring_ranked])[:limit]
    return [Hit(rel, _excerpt(corpus[rel], rel, query)) for rel in fused]
```

(Task 2 modifies this same block again — that is expected; the tasks are ordered.)

- [ ] **Step 4: Run the regression test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hybrid.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Fix the server test that motivated the filter**

In `tests/test_server.py`, replace `test_search_brain_returns_dicts` (lines 40-47):

```python
def test_search_brain_returns_dicts():
    hits = server.search_brain("ingestion pipeline")
    assert hits == [
        {
            "path": "Projects/Sentinel ESG.md",
            "excerpt": "ESG incident ingestion pipeline with CouchDB-free architecture.",
        }
    ]
```

with a rank-based assertion (a ranked-retrieval engine may legitimately return
weaker hits below the best one; what matters is that the right note wins):

```python
def test_search_brain_returns_dicts():
    hits = server.search_brain("ingestion pipeline")
    assert hits[0] == {
        "path": "Projects/Sentinel ESG.md",
        "excerpt": "ESG incident ingestion pipeline with CouchDB-free architecture.",
    }
```

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS — 183 tests (182 prior + 1 new). If `test_search_brain_returns_dicts` fails with `Projects/Sentinel ESG.md` NOT ranked first for "ingestion pipeline", that is a real fusion-quality regression to investigate — do not weaken the assertion further.

- [ ] **Step 7: Commit**

```bash
git add src/tesseract_mcp/hybrid.py tests/test_hybrid.py tests/test_server.py
git commit -m "fix(search): restore semantic recall for multi-token queries

The _query_tokens_match post-filter required every query token to
appear literally in a note, which discarded vector-only paraphrase
matches — the case the semantic half of hybrid search exists for.
The server test that motivated it now asserts rank, not exclusivity."
```

---

## Task 2: Gate the substring signal to the BM25-empty fallback case

**Files:**
- Modify: `src/tesseract_mcp/hybrid.py` (`hybrid_search` fusion block)
- Test: `tests/test_hybrid.py` (new test)

**Interfaces:**
- Consumes: `_substring_rank(corpus, query, limit) -> list[str]` (existing, unchanged), `rrf_fuse(ranked_lists, k=60) -> list[str]` (existing, unchanged).
- Produces: no signature changes — behavior change only: `_substring_rank` participates in fusion only when BM25 returned zero results.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_hybrid.py` (uses the existing `FakeEmbedder` class already defined at the top of this file):

```python
def test_substring_signal_only_when_bm25_empty(vault, vault_dir):
    # "aaa.md" contains the query only inside another word (substring match,
    # not a token match); "zzz.md" contains it as a real token. When BM25 has
    # results, the alphabetical substring signal must stay out of fusion, so
    # aaa.md must not appear at all.
    (vault_dir / "aaa.md").write_text(
        "an important announcement was made\n", encoding="utf-8"
    )
    (vault_dir / "zzz.md").write_text(
        "the port of hamburg is busy\n", encoding="utf-8"
    )
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "port")
    paths = [h.path for h in hits]
    assert "zzz.md" in paths       # real BM25 token match
    assert "aaa.md" not in paths   # substring-only; signal gated off


def test_substring_fallback_still_works_when_bm25_empty(vault):
    # Single-character query: BM25's [a-z0-9]+ tokenizer yields no token
    # matches, so the substring fallback must still return results.
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "e", tags=["esg"])
    assert [h.path for h in hits] == ["Projects/Sentinel ESG.md"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hybrid.py::test_substring_signal_only_when_bm25_empty -v`
Expected: FAIL — `aaa.md` currently appears in the results because `_substring_rank` matches "port" inside "important" and is fused on every query.

(`test_substring_fallback_still_works_when_bm25_empty` should already PASS — it pins the behavior Task 2 must not break.)

- [ ] **Step 3: Gate the signal**

In `src/tesseract_mcp/hybrid.py`, replace the fusion block at the end of `hybrid_search` (as left by Task 1):

```python
    substring_ranked = _substring_rank(corpus, query, limit=50)
    # Third RRF signal: BM25 tokenizes [a-z0-9]+ only, so single-char queries
    # like "e" (test_hybrid_search_respects_tag_filter) need substring fallback.
    fused = rrf_fuse([bm25_ranked, vector_ranked, substring_ranked])[:limit]
    return [Hit(rel, _excerpt(corpus[rel], rel, query)) for rel in fused]
```

with:

```python
    ranked_lists = [bm25_ranked, vector_ranked]
    if not bm25_ranked:
        # Fallback signal only: BM25 tokenizes [a-z0-9]+, so queries it cannot
        # token-match (e.g. single characters, punctuation-only) fall through
        # to substring matching. When BM25 has results, the alphabetically-
        # ordered substring list would just pollute the fusion.
        ranked_lists.append(_substring_rank(corpus, query, limit=50))
    fused = rrf_fuse(ranked_lists)[:limit]
    return [Hit(rel, _excerpt(corpus[rel], rel, query)) for rel in fused]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_hybrid.py -v`
Expected: PASS — both new tests and all existing ones.

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS — 185 tests. Pay attention to `test_search_brain_limit` in `tests/test_server.py` (query `"e"`, real embedder): it must still pass, via the vector signal and/or the now-gated substring fallback.

- [ ] **Step 6: Commit**

```bash
git add src/tesseract_mcp/hybrid.py tests/test_hybrid.py
git commit -m "fix(search): use substring ranking only as BM25-empty fallback

The alphabetically-ordered substring list was fused with equal RRF
weight on every query, systematically boosting alphabetically-early
notes. It now participates only when BM25 token-matching yields
nothing — the single-character-query case it was added for."
```

---

## Self-Review Notes

**Review-finding coverage:**
- Finding 1 (all-tokens filter kills semantic recall) → Task 1: filter deleted, regression test pins the paraphrase case, motivating server test converted from exclusivity to rank assertion.
- Finding 2 (alphabetical substring signal pollutes every query) → Task 2: signal gated to the BM25-empty case, with a test for both the gating and the preserved fallback.
- Finding 3 (BM25L swap) → no task; review approved it as-is.
- Minor notes (model load in indexer tests, no chunking for long notes, RRF tie order) → intentionally no tasks; review classified them as no-action-required.

**Interaction between tasks:** Task 1's Step 3 and Task 2's Step 3 edit the same fusion block; Task 2's "replace" text matches exactly what Task 1 leaves behind, so the tasks compose in order. An implementer doing Task 2 without Task 1 will find the old block doesn't match — that is intentional ordering enforcement.

**Type consistency:** no new public names introduced; `hybrid_search`, `_substring_rank`, `rrf_fuse` signatures untouched throughout.
