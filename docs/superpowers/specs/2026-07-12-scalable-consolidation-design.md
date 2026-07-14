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
2. **Backstop fill**: if the slice isn't full AND the backstop cadence gate has
   elapsed (§6), add entities by advancing a rolling cursor through the stable
   lexicographic entity-path ordering — re-checking already-checked entities to
   catch dup pairs where neither side recently changed.

**Cursor is a PATH, not an index.** Store `cursor` as the last-visited entity
path string. Each sweep the backstop resumes from the first entity path
lexicographically `> cursor` and wraps to the start after the last path. Do NOT
store an integer index into the sorted list: entities are created and merged
(deleted) between sweeps, so the list mutates every sweep and a positional index
would silently skip or re-cover entities, breaking the completeness guarantee.
Anchoring to a path value advances through a stable total order regardless of
churn.

If more than `SLICE_SIZE` entities are unchecked (cold start: all 1,235), take
`SLICE_SIZE` of them this sweep in path order; the rest stay unchecked and are
drained over the next sweeps (~ceil(1235/200) ≈ 7 sweeps). After a slice entity
is adjudicated (whether or not it produced a merge), set its `checked_hash` to
its current identity hash so it leaves the unchecked set.

**Persistence caveat.** `cursor` + `checked_hash` are written with the rest of
the consolidation state, which today only persists under `apply=True`
([librarian.py:239](../../../src/tesseract_mcp/librarian.py)). So the slice
advances across the librarian's apply-mode sweeps; a bare `consolidate` CLI
dry-run stays stateless and recomputes the slice from scratch each run. The plan
must state this explicitly so no one expects a dry-run to advance the cursor.

### 5. Bounded LLM adjudication (the timeout fix)

Batch candidate clusters into LLM calls sized to a hard cap
(`MAX_ENTITIES_PER_CALL = 40` entities of listing per call). Each call is small
and fast — no single call approaches 120s. Calls are INDEPENDENT: a batch that
errors or times out is skipped, recorded, and the rest proceed — a bad batch no
longer fails the whole consolidate step (current failure mode).

**Batching invariant — pack whole clusters, never split one.** A single call
takes whole clusters up to the `MAX_ENTITIES_PER_CALL` cap and never bisects a
cluster: all variants of one candidate group must be adjudicated in the same
prompt or the model cannot see them together. Since `MAX_CLUSTER = 10 < 40` a
cluster always fits. We do NOT additionally require a call to be single-type:
the existing `known`-set validation in `propose_merges`
([consolidate.py:86](../../../src/tesseract_mcp/consolidate.py)) already rejects
any merge whose canonical/duplicates are not all the same declared type, so a
mixed-type or multi-cluster batch is safe — at worst a multi-cluster prompt
catches a kNN-missed dup as a bonus.

Output is the SAME format as today — `{"merges": [{"type","canonical","duplicates"}]}`
— so `_coerce`/validation in `propose_merges` and the downstream apply path
(`_apply_one`, pending-proposals review) are UNCHANGED. The per-batch prompt is
the existing `PROMPT` with a smaller `listing`.

### 6. Throttle change

The current all-or-nothing `should_consolidate` gate is replaced by two
independently-paced paths, so steady-state LLM spend stays low when the graph is
quiet:

- **Unchecked/changed path — eager.** Runs every sweep whenever any unchecked
  entity exists (bounded by `SLICE_SIZE`). This is the churn response; it is
  cheap when little changed and catches new dups fast. It supersedes the old
  `CONSOLIDATE_MIN_NEW_ENTITIES` new-entity threshold.
- **Backstop path — throttled.** The rolling-cursor re-check of already-checked
  entities runs only when (a) the slice has spare budget after the unchecked
  fill AND (b) `BACKSTOP_MIN_INTERVAL` has elapsed since the last backstop
  advance. Repurpose the existing age-based idea (`CONSOLIDATE_MAX_AGE_DAYS`) as
  this cadence constant rather than deleting all gating — otherwise the backstop
  would issue LLM calls every sweep forever even on a static graph. Rationale is
  sharpened by the current codex/ChatGPT quota exhaustion (until 2026-08-10): an
  always-on backstop is a standing cost.

The backstop's last-advance marker (timestamp or sweep counter) lives in the
consolidation state alongside `cursor`.

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
- cursor is a path string: backstop resumes from the first path `> cursor` and
  wraps at the end; over ceil(n/SLICE_SIZE) sweeps every entity is adjudicated
  (backlog drains);
- cursor is churn-robust: inserting and deleting entities between sweeps causes
  no skipped or double-covered entities (the failure a positional index would
  produce);
- backstop cadence: the backstop is skipped when `BACKSTOP_MIN_INTERVAL` has not
  elapsed, even with spare slice budget; the unchecked/changed path still runs;
- after adjudication a slice entity's `checked_hash` is set to its identity hash
  (leaves the unchecked set); a later identity change re-adds it;
- batching packs whole clusters and never splits a cluster across calls; each
  call's listing ≤ `MAX_ENTITIES_PER_CALL`;
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
  `cursor` (last-visited entity **path string**, not an index), `checked_hash`
  (`{path: hash}`), and `backstop_last_advance` (timestamp/sweep counter for the
  cadence gate). Persisted under the existing `apply=True` path
  ([librarian.py:239](../../../src/tesseract_mcp/librarian.py)). Entity vectors
  live in their own `entity_vectors.json` under the state dir.
- Tunable constants (module-level, one place): `SIM_THRESHOLD=0.85`,
  `K_NEIGHBORS=5`, `MAX_CLUSTER=10`, `SLICE_SIZE=200`, `MAX_ENTITIES_PER_CALL=40`,
  `BACKSTOP_MIN_INTERVAL` (backstop cadence; repurposes the old
  `CONSOLIDATE_MAX_AGE_DAYS` idea).

## Out of scope

- Deleted-note / orphaned-entity cleanup (separate spec 2).
- Auto-applying merges (still propose-only; review flow unchanged).
- Retuning the embedding model or the merge prompt wording.
