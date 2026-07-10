# Search Eval Harness — Design

Retrieval quality for `search_brain` is currently judged by eye and by
unit tests with fake embedders. This design adds a measurement layer: a
golden-query evaluation harness that scores the real hybrid pipeline
(BM25L + vectors + RRF) with real embeddings, so every future ranking
change — chunk-level retrieval, field boosts, recency weighting, graph
fusion — is a measurable before/after instead of vibes.

Motivating incident: a reviewer once "fixed" a failing exact-equality
search test by adding an all-tokens filter that destroyed semantic
recall. The harness encodes the lesson structurally: ranked engines get
rank *metrics* with threshold floors, never exact-order assertions.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Scope of this round | Eval harness only; quality/perf/graph work are separate backlog items (tracked in vault `Claude/Tasks.md`, "Search backlog 1-4") |
| Corpus strategy | Both: a synthetic fixture vault committed to the repo (public repo — synthetic only, no personal content) AND a private live-vault golden set stored inside the vault |
| Live golden set location | `Claude/Evals.md` in the vault — a normal markdown note with a fenced ```yaml block; private by construction, LiveSyncs, editable in Obsidian |
| Embeddings in eval runs | Real `SentenceTransformerEmbedder` (bge-micro-v2) always — fake vectors cannot measure semantic recall; fakes stay in unit tests |
| Metrics | success@k and recall@k for k in {5, 10}, plus MRR; retrieval depth 20 |
| Gate mechanism | CLI scorecard (with per-vault history file) + one env-guarded pytest threshold test on the fixture corpus |
| CI | None exists in this repo; everything runs locally. Harness is CI-ready later if that changes |

## Architecture

```
evals/                          # committed benchmark assets (synthetic)
├── vault/                      # fixture mini-vault, ~20 notes
├── golden.yaml                 # ~16 golden queries for the fixture
└── README.md                   # schema docs, how to add queries, baseline table
src/tesseract_mcp/evals.py      # loader, metrics, runner, CLI
tests/test_evals.py             # unit tests + env-guarded threshold gate
C:\Vaults\<vault>\Claude\Evals.md   # private live golden set (not in repo)
```

`evals.py` calls the production path directly — `hybrid.hybrid_search`
with `indexer.state_dir(vault_root)` and a real embedder — so the number
measured is the number `search_brain` actually delivers. Nothing in the
retrieval code changes for evalability.

## Golden query schema

```yaml
- id: para-owe                      # unique per set
  query: who do I owe money to
  expect:                           # must-find; recall is computed on these
    - Areas/Finance/Invoices.md
  accept:                           # optional; also relevant, never punished
    - Claude/Sessions/2026-07-03 Budget review.md
  tags: [finance]                   # optional; passed to hybrid_search(tags=...)
  folder: Areas                     # optional; passed to hybrid_search(folder=...)
  note: paraphrase case, vector lane
```

Relevant set R = expect ∪ accept. The split exists because personal-vault
queries often have one *right* answer and several *fine* ones: recall@k
runs on `expect` only, while success@k and MRR treat all of R as relevant.

Loading: a `.yaml`/`.yml` golden file is parsed whole; a `.md` file has
the first fenced ```yaml block extracted and parsed. Validation rejects
duplicate ids, empty `expect`, and (in strict mode) any expect/accept
path that does not exist in the target vault.

## Fixture corpus design

~20 short synthetic notes mimicking the real vault's shape (Projects/,
Areas/, Notes/, Inbox/, Claude/Sessions, Claude/Concepts, Claude/Graph
entity notes, one deliberately long rambling note). Every lane the engine
claims gets queries:

- exact keyword (BM25 lane) — 3 queries
- paraphrase with zero content-word overlap (vector lane) — 3
- title match — 2
- tag-filtered and folder-filtered — 1 each
- untokenizable query (`%`) — exercises the substring fallback lane — 1
- "granularity traps": a focused note vs. the long rambling note that
  mentions the same topic in passing — 2 (these document the known
  whole-note-granularity weakness; the trap rows are the before/after
  scoreboard for the chunk-retrieval backlog item)
- entity-note retrieval by name — 2, plus mixed keyword/semantic — 1

## Metrics (locked definitions)

For each query, `hybrid_search` is called with `limit=20`.

- `first_rank` = 1-based rank of the first hit in R, else None
- `success@k` = share of queries where any hit in top-k is in R
- `recall@k` = mean over queries of |expect ∩ top-k| / |expect|
- `MRR` = mean of 1/first_rank, counting 0 when no hit in R
- Skipped queries (live mode, stale paths) are excluded from all
  aggregates and reported as a count.

## Runner CLI

```
python -m tesseract_mcp.evals                 # fixture corpus (default)
python -m tesseract_mcp.evals --live          # TESSERACT_VAULT_PATH + Claude/Evals.md
python -m tesseract_mcp.evals --vault P --golden F   # explicit
    --json         machine-readable output instead of the table
    --no-history   skip the history append
    --init-live    create Claude/Evals.md from a template if absent, then exit
```

Output: a per-query table (id, first-rank, recall@10, missing expects)
plus the aggregate line. Every scoring run appends one JSON line to
`state_dir(vault)/eval_history.jsonl` (timestamp, best-effort git SHA,
query count, skips, all aggregates, per-query misses) so ranking changes
trend over time. `TESSERACT_STATE_DIR` override is honored as everywhere
else.

## Strict vs. lenient validation

- **Fixture (and explicit --vault/--golden) runs are strict:** any
  expect/accept path missing from the corpus fails the run immediately
  (exit 2) listing the stale paths. Golden-set rot is loud.
- **Live runs are lenient:** a query whose expect paths are all missing
  is marked skipped and reported; the vault legitimately drifts.

Exit codes: 0 = scored run (regardless of scores — gating is pytest's
job), 2 = configuration/validation error.

## Pytest gate

- Fast unit tests (no model): metrics math, golden loading/validation,
  runner behavior with the existing FakeEmbedder pattern, CLI plumbing
  with a monkeypatched embedder factory.
- One model-backed threshold test, guarded by
  `TESSERACT_RUN_EVALS=1` (skipped otherwise): runs the fixture eval
  with the real embedder and asserts floors `success@10 >= 0.80` and
  `MRR >= 0.50`. Rule: if the measured baseline ever sits below a floor,
  fix the fixture or the golden set — never lower the floor to pass.

## Error handling

- Missing/unparseable golden file, no yaml fence in a .md golden, dup
  ids, empty expect → exit 2 with a message naming the file and ids.
- `--live` without `TESSERACT_VAULT_PATH` → exit 2.
- `--init-live` never overwrites an existing `Claude/Evals.md`.
- Model download failure surfaces as-is (sentence-transformers error);
  the harness adds no retry logic.

## Relationship to the IR benchmark harness (2026-07-09 spec)

`docs/superpowers/specs/2026-07-09-ir-benchmarks-design.md` (approved a
day before this one, discovered at integration time) defines a
complementary but distinct layer: BEIR public corpora (SciFact,
NFCorpus) run through the same pipeline with ranx metrics and ablation
modes, producing *publishable, literature-comparable* numbers for the
README. The two do not collide in files (`benchmarks/` + top-level
package vs. `evals/` + `src/tesseract_mcp/evals.py`) and serve different
questions:

| | IR benchmarks (2026-07-09) | This eval harness (2026-07-10) |
|---|---|---|
| Question | "Is this retrieval engine good, objectively?" | "Did my last change make *my vault's* search better or worse?" |
| Corpus | BEIR public datasets, thousands of docs | 20-note synthetic fixture + private live-vault set |
| Metrics | ranx nDCG@10, Recall@10/100, MRR@10 | hand-rolled success@k, recall@k, MRR |
| Cost per run | Minutes (large corpora) | Seconds |
| Role | Publishable scoreboard, ablations | Pre-commit regression gate, trend history |

If both ship, a later consolidation could have the benchmark runner
reuse this harness's golden-set loader for its private graph track
(`queries/graph-eval.yaml` is schema-compatible with our golden format
minus `tags`/`folder`). Deliberately not done now — neither spec blocks
the other.

## Non-goals

- nDCG / graded relevance judgments (binary expect/accept is enough for
  a solo vault; revisit if judgments ever feel too coarse)
- Query-log mining (no query logs exist)
- Evaluating `context_bundle`/graph traversal (separate backlog item 4)
- CI wiring (no CI in this repo today)
- Any change to ranking behavior itself (that's backlog item 2, measured
  by this harness once it exists)
