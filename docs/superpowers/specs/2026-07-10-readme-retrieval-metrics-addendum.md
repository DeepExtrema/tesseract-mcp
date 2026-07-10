# README Additions: Retrieval Diagram + Architecture + Measured Metrics — Design Addendum

**Date:** 2026-07-10
**Status:** Approved
**Extends:** `2026-07-09-readme-architecture-docs-design.md` (the shipped
docs overhaul). Depends on the search eval harness
(`2026-07-10-search-eval-harness-design.md`) being implemented — its
scorecard supplies the numbers.

## Goal

Three additions to `README.md`, keeping its ~2-screen product-page shape:

1. **"How retrieval works" section** — one compact Mermaid flowchart of
   the production pipeline: query → candidate filter (tags/folder) →
   BM25L top-50 ∥ vector cosine top-50 (Smart Connections embeddings,
   pinned bge-micro-v2 fallback) → RRF fusion (k=60) → ranked hits; the
   substring ranker drawn as a dashed fallback edge labeled "only when
   BM25 is empty." At most three sentences of prose; depth remains in
   `docs/ARCHITECTURE.md` §2.
2. **Architecture diagram refresh** — upgrade the existing "How it
   works" Mermaid to include the caretaker layer that now exists
   (Librarian + organizer beside the retrieval path). Total diagrams in
   README after this change: the system flow, the retrieval pipeline —
   no third diagram.
3. **"Measured retrieval quality" section** — a table of REAL numbers
   from the harness run on the committed synthetic fixture corpus:
   success@5, success@10, recall@5, recall@10, MRR. One methodology
   sentence (golden queries against the production `hybrid_search` path
   with real embeddings; corpus is synthetic because the repo is
   public) and a link to `evals/README.md`. Hard rule: the section
   ships only with numbers from an actual green harness run — no
   aspirational placeholders.

## Non-goals

- No changes to `docs/ARCHITECTURE.md` beyond what the eval-harness
  plan itself specifies (module map row, harness section) — this
  addendum must not duplicate them.
- No re-litigation of the shipped README structure.

## Verification

- Both Mermaid blocks parse (GitHub-flavored rendering check).
- Every number in the metrics table traces to a scorecard run recorded
  in the implementing session.
- Full test suite green after the harness lands; README claims match
  `server.py` tool registrations as before.
