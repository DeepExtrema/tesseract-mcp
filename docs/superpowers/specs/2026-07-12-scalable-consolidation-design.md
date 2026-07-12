# Scalable entity consolidation

Date: 2026-07-12
Status: approved
Branch: feat/graph-scale-caretaker
Sub-project: 1 of 2 (the second is deleted-note / orphaned-entity cleanup — its
own spec).

## Problem

`consolidate.propose_merges` ([src/tesseract_mcp/consolidate.py:69](../../../src/tesseract_mcp/consolidate.py))
builds ONE prompt listing every entity and makes a SINGLE LLM call. At 1,235
entities that call times out (`ExtractorError: claude timed out after 120s`, seen
in the 2026-07-12 sweep); at 10k+ it will not fit a context window. The work is
O(n) in graph size, and one failed call fails the whole consolidate step.

Additional standing problem this addresses: ~1,235 existing entities have never
been successfully consolidated (the pass has failed every sweep), so there is a
backlog to clear, not just churn to keep up with.

## Goal

Bounded consolidation work per sweep — a fixed cap on entities examined and a
fixed cap on each LLM call — independent of total graph size. Consolidation can
never again time out from looking at the whole graph at once.

## Design

### 1. Entity identity vectors (blocking signal)

Embed each entity's IDENTITY, not its full note: `name` + `aliases` + `summary`
(from the entity note's frontmatter). Do NOT embed the full entity note — its
mention/relation lines describe which notes reference the entity, so full-note
vectors cluster by shared context (two different people named in the same note
look similar), which is wrong for dedup.

- Reuse the bge-micro-v2 model via `embeddings.SentenceTransformerEmbedder` and a
  hash-keyed fallback cache (same pattern as `embeddings.get_note_vectors`), in a
  DEDICATED entity-vector cache under the state dir (e.g.
  `entity_vectors.json`), keyed by entity path, valued `{hash, vec}` where
  `hash = sha256(identity_text)`.

### 2. Two indices: vectors (all entities) vs consolidation coverage

Keep two distinct things straight:

- **Entity-vector index** (`entity_vectors.json`, `{path: {hash, vec}}`): the
  kNN blocking signal. Must cover ALL entities (we need everyone's vector to find
  anyone's neighbors). Embedding is the cheap local bge-micro-v2 model, hash-
  cached, so recomputing changed vectors and holding all of them is inexpensive.
- **Consolidation coverage** (`checked_hash` per entity, in the consolidation
  state): the identity hash at the entity's last consolidation adjudication. An
  entity is "unchecked" (never adjudicated, or its identity changed since) when
  its current identity hash ≠ its `checked_hash`. This is SEPARATE from the
  vector cache: embedding an entity for kNN does NOT mark it consolidation-
  checked. On cold start no entity has a `checked_hash`, so all are unchecked —
  but the slice budget (§4) still bounds how many are adjudicated per sweep.

### 3. Candidate generation — same-type kNN blocking

For each entity in the slice, find its top-k nearest neighbors (default
`K_NEIGHBORS = 5`) OF THE SAME ENTITY TYPE with cosine ≥ `SIM_THRESHOLD`
(default `0.85`, eval-tunable). Same-type is a hard filter (never merge a person
into an org). Union overlapping candidate pairs into small clusters (union-find)
so a 3-way variant set is handled together and no pair is asked about twice.
Cap cluster size at `MAX_CLUSTER = 10`; split anything larger.

Cosine reuse: the `_cosine` helper in `hybrid.py` (or numpy) over same-type
vectors. Same-type restriction keeps the neighbor scan small.

### 4. Rolling slice — cold-start + backstop in one

Each sweep adjudicates a bounded slice of at most `SLICE_SIZE` (default `200`)
entities TOTAL — this is the hard per-sweep cap that makes the whole thing
bounded. The slice is filled in priority order:

1. **Unchecked/changed entities first** (identity hash ≠ `checked_hash`) — new
   entities and ones whose identity changed since last adjudication, so dups are
   caught soon after ingest.
2. **Backstop fill**: if the slice isn't full, add entities from a rolling cursor
   advancing through a stable entity ordering (sorted entity path), wrapping at
   the end — re-checking already-checked entities to catch dup pairs where
   neither side recently changed.

If more than `SLICE_SIZE` entities are unchecked (cold start: all 1,235), take
`SLICE_SIZE` of them this sweep in cursor order; the rest stay unchecked and are
drained over the next sweeps (~ceil(1235/200) ≈ 7 sweeps). After a slice entity
is adjudicated (whether or not it produced a merge), set its `checked_hash` to
its current identity hash so it leaves the unchecked set. The cursor and
`checked_hash` map persist in the consolidation state.

### 5. Bounded LLM adjudication (the timeout fix)

Batch candidate clusters into LLM calls sized to a hard cap
(`MAX_ENTITIES_PER_CALL = 40` entities of listing per call). Each call is small
and fast — no single call approaches 120s. Calls are INDEPENDENT: a batch that
errors or times out is skipped, recorded, and the rest proceed — a bad batch no
longer fails the whole consolidate step (current failure mode).

Output is the SAME format as today — `{"merges": [{"type","canonical","duplicates"}]}`
— so `_coerce`/validation in `propose_merges` and the downstream apply path
(`_apply_one`, pending-proposals review) are UNCHANGED. The per-batch prompt is
the existing `PROMPT` with a smaller `listing`.

### 6. Throttle change

Because work is bounded per sweep, consolidation runs a slice EVERY sweep
(advancing the cursor) rather than the current all-or-nothing
`should_consolidate` gate. Relax it to "run a bounded slice if any entities
exist"; dirty entities are always included regardless of the cursor position.
`CONSOLIDATE_MIN_NEW_ENTITIES` / `CONSOLIDATE_MAX_AGE_DAYS` gating is removed for
the slice path (the rolling cursor is the new pacing mechanism).

### 7. Error handling & testing

Partial progress is durable: the cursor advances past the processed slice and
proposals accumulate even if some batches fail. `sweep_errors["consolidate"]`
records skipped-batch counts instead of a single fatal error.

Tests:
- candidate generation returns same-type only, respects `SIM_THRESHOLD` and
  `K_NEIGHBORS`;
- union-find clusters overlapping candidate pairs; clusters split at
  `MAX_CLUSTER`;
- slice is bounded to ≤ `SLICE_SIZE` even when ALL entities are unchecked (cold
  start with an empty `checked_hash` map);
- unchecked/changed entities are prioritized into the slice ahead of backstop
  fill;
- cursor advances and wraps; over ceil(n/SLICE_SIZE) sweeps every entity is
  adjudicated (backlog drains);
- after adjudication a slice entity's `checked_hash` is set to its identity hash
  (leaves the unchecked set); a later identity change re-adds it;
- each LLM call's listing ≤ `MAX_ENTITIES_PER_CALL`;
- a timing-out/erroring batch is skipped while others proceed and the consolidate
  step still succeeds (returns proposals, records the skip);
- entity-vector cache: unchanged identity → cache hit (no re-embed); changed
  identity → re-embed.

## Components / files

- New `src/tesseract_mcp/blocking.py`: entity-vector cache, unchecked-set +
  rolling-cursor slice selection, same-type kNN candidate generation, union-find
  clustering, batching to `MAX_ENTITIES_PER_CALL`.
- Modify `src/tesseract_mcp/consolidate.py`: `propose_merges` consumes
  pre-built bounded batches from `blocking.py` and loops LLM calls with
  per-batch error isolation; `run`/`main` wire the slice + cursor.
- Modify `src/tesseract_mcp/librarian.py`: `_consolidate_step` passes/advances
  the cursor and `checked_hash` map, relaxed throttle; `sweep_errors` records
  skipped batches.
- Consolidation state (in `librarian_state.json`'s `consolidation` block) gains:
  `cursor` (int index into the sorted entity list), `checked_hash` (`{path:
  hash}`). Entity vectors live in their own `entity_vectors.json` under the
  state dir.
- Tunable constants (module-level, one place): `SIM_THRESHOLD=0.85`,
  `K_NEIGHBORS=5`, `MAX_CLUSTER=10`, `SLICE_SIZE=200`, `MAX_ENTITIES_PER_CALL=40`.

## Out of scope

- Deleted-note / orphaned-entity cleanup (separate spec 2).
- Auto-applying merges (still propose-only; review flow unchanged).
- Retuning the embedding model or the merge prompt wording.
