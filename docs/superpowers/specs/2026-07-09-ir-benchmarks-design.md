# IR Benchmark Harness — Design

**Date:** 2026-07-09
**Status:** Approved (brainstorm with Taimoor)
**Scope:** A reproducible information-retrieval evaluation suite for
tesseract-mcp's retrieval pipeline, with published scores in the README.

## Motivation

The README and ARCHITECTURE.md describe the hybrid BM25 + vector + RRF
pipeline and the GraphRAG layer qualitatively. To present this project as a
serious, scientifically grounded open-source retrieval system (bar set by
GitNexus-style evidence-first READMEs), every claim about retrieval quality
must trace to a number a stranger can reproduce with one command.

## Decisions made during brainstorm

1. **Order:** benchmark harness first; the IR-perspective docs rewrite is a
   separate follow-up cycle that consumes these numbers. The job-tracker
   structured memory bank is a third, independent cycle.
2. **Corpus:** public BEIR subsets (SciFact ~5K docs, NFCorpus ~3.6K docs)
   for published, literature-comparable numbers; the same harness also runs
   against the private vault for personal (unpublished) numbers.
3. **Home:** inside the tesseract-mcp repo, top-level `benchmarks/` package
   (sibling to `src/`, excluded from the wheel).
4. **Approach:** lean classical-IR harness (Approach A). GraphRAG is
   measured on a separate small-N multi-hop track, NOT on BEIR — BEIR
   queries are single-hop and cannot detect multi-hop graph value, and LLM
   entity extraction over 5K docs is prohibitively slow/costly per run.

## Architecture

```text
benchmarks/
  __main__.py    # CLI: run | report | graph
  datasets.py    # download BEIR SciFact + NFCorpus, materialize as markdown vaults
  runner.py      # run ablations (bm25 | vector | hybrid) through the real pipeline
  metrics.py     # thin wrapper over ranx: nDCG@10, Recall@10/@100, MRR@10
  report.py      # results JSON -> markdown table -> inject into README between markers
  queries/graph-eval.yaml   # multi-hop query set (private-vault graph track) — gitignored, lives outside the repo
  results/       # committed JSON for the public BEIR tracks only (dataset, mode, git SHA, date)
```

### datasets.py

- Downloads BEIR datasets via `ir_datasets` (`beir/scifact`,
  `beir/nfcorpus` — ships docs, queries, and qrels together) into
  `~/.tesseract-mcp/benchmarks/` — cached, never committed.
- Materializes each document as a markdown note in a throwaway vault:
  title as H1, body text, and the BEIR `doc_id` in frontmatter (the ID
  round-trip key). Vault built once per dataset and reused.
- Downloads are checksum-verified against the dataset source.

### runner.py

- Imports `hybrid.hybrid_search` directly — bypasses FastMCP so the
  measurement covers the retrieval engine, not MCP plumbing.
- **Required pipeline seam (small refactor in `src/tesseract_mcp/hybrid.py`):**
  - `mode: Literal["hybrid", "bm25", "vector"] = "hybrid"` — disables one
    ranked list before RRF for ablations. Default preserves current
    behavior; production callers unchanged.
  - `depth: int = 50` — the current hard-coded per-list cap of 50 makes
    Recall@100 impossible; benchmarks pass `depth>=100`.
- Embeddings computed by the local bge-micro-v2 path (`embeddings.py`
  fallback), batch-embedded once per dataset and cached under the state
  root — first run is slow, re-runs are minutes, results deterministic,
  no Obsidian required.
- For each query: run each mode, collect top-100 ranked note paths, map
  back to BEIR doc IDs via frontmatter.

### metrics.py

- `ranx` computes nDCG@10 (BEIR-standard headline), Recall@10, Recall@100,
  MRR@10 against the official qrels. Established library, not hand-rolled
  math — that is what makes the numbers citable.

### report.py

- Writes `results/<dataset>-<mode>-<gitsha>.json` with full provenance:
  git SHA, embed model name, dataset version, date, harness parameters.
- `python -m benchmarks report` regenerates the README table between
  `<!-- bench:start -->` / `<!-- bench:end -->` markers. Scores are never
  hand-edited. The table caption states the embedder, the exact reproduce
  command, and links published BEIR baselines for context.
- Stale-number guard: `report` refuses to inject if the newest results'
  git SHA does not match HEAD, unless `--force`.

### Graph track (small-N, separate)

- `queries/graph-eval.yaml`: ~15–25 hand-written multi-hop queries against
  the real private vault, each with an expected note set.
- `python -m benchmarks graph` scores `related_notes` / `context_bundle`
  on hit-rate and MRR, writing to the same results/ format.
- Reported in ARCHITECTURE.md under a "Measuring the graph" section with
  explicit small-N caveats — NOT in the headline README table.
- **Privacy:** query text, note names, and expected note paths are private
  vault metadata. `graph-eval.yaml` and raw graph-track results stay OUT of
  git (gitignored; stored in the vault or state dir). Anything committed
  from this track carries only opaque IDs and aggregate metrics.

## CLI

```console
python -m benchmarks run    [--dataset scifact|nfcorpus|all] [--mode bm25|vector|hybrid|all]
python -m benchmarks report [--force]
python -m benchmarks graph
```

## Honesty policy

Published numbers are whatever the harness produces. If hybrid loses to
BM25-only on a dataset (plausible: small embedders often underperform on
scientific text), that is a documented finding — and the justification for
any future embedder upgrade, measured by this same harness.

## Error handling

- Missing embedding for any note fails the run loudly (a silent gap would
  corrupt the vector ablation).
- Interrupted downloads are resumable/verifiable by checksum.
- `report` stale-SHA guard as above.

## Dependencies

New `[bench]` extra in `pyproject.toml`: `ranx`, `ir_datasets`.
Core install unaffected.

## Testing

- Unit tests with a tiny fixture dataset (10 docs, 3 queries, known qrels)
  where expected metric values are hand-computable. Covers: dataset
  materialization, doc-ID round-trip, mode ablation seam, metric wiring,
  README marker injection, stale-SHA guard.
- Real BEIR runs are manual (or opt-in CI), not part of default pytest.

## Out of scope (future cycles)

1. The full IR-perspective README/docs rewrite (consumes these numbers).
2. LLM-as-judge end-to-end evaluation layer.
3. Job-tracker structured-data memory bank.
