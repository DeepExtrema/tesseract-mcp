# Graph deletion & orphaned-entity cleanup

Date: 2026-07-13
Status: draft — awaiting review
Branch: feat/graph-deletion-cleanup (stacked on feat/graph-scale-caretaker)
Sub-project: 2 of 2 of the graph-scale caretaker (sub-project 1 was scalable
entity consolidation — see
[2026-07-12-scalable-consolidation-design.md](2026-07-12-scalable-consolidation-design.md)).

## Problem

Deletion is a one-way door the graph never hears about. The indexer's only
retraction path (`indexer._retract_stale_mentions`) fires when a note re-enters
the pending queue — i.e. when it is EDITED. A DELETED note never re-enters
`scan_notes` output, so:

- Its mention lines stay in entity notes forever (`check_orphaned_entities`
  reports them as `(entity, missing_note)` rows — detection only, no remedy).
- Its manifest entry (`hashes`, `failures`) stays forever
  (`check_manifest_drift.deleted_but_tracked` — again detection only).
- Entities whose every supporting mention is gone persist as unsupported nodes.

Adjacent hygiene debt with the same root cause (graph state outliving the facts
that created it):

- Consolidation merges leave redirect stubs; sub-project 1's plan self-review
  explicitly defers to this spec the pruning of stale `checked_hash` keys and
  `entity_vectors.json` entries for merged/deleted entities.
- Relation lines pointing at entity notes that were merged (stub) or deleted
  dangle; `related_notes` graph walks and `find_entity` traverse them into
  nothing.
- Merge-stub chains (A→B where B was later merged into C) resolve in two hops
  or dead-end.

Two review findings deferred from sub-project 1's whole-branch review also land
here:

- **F-backstop — first-cycle timing.** `librarian._backstop_due` returns `True`
  when no `backstop_last_advance` marker exists, and the marker is only stamped
  when the backstop actually runs. So during the cold-start drain (every sweep's
  slice fully consumed by unchecked entities) the backstop stays permanently
  "due", and the first sweep with spare budget immediately re-checks entities
  adjudicated only days earlier — duplicate LLM spend, aggravated by the codex
  quota exhaustion (until 2026-08-10).
- **F-cluster — stranded members on oversize-component split.**
  `blocking._cluster_pairs` splits a union-find component into fixed-stride
  chunks of `MAX_CLUSTER`; an 11-member component becomes chunks of 10 and 1,
  and `candidate_clusters` then drops the singleton chunk — an entity that HAD
  candidate pairs is silently never adjudicated.

Scale today: ~1,300 entities, 1,680 mentions, 2,392 edges.

## Goal

After a note is deleted or an entity is merged, the graph converges — over
bounded caretaker sweeps — to only supported facts: mentions of deleted notes
are retracted, unsupported entities are proposed for retirement, dangling
relations and stub chains are repaired, and the consolidation caches stop
accumulating dead keys. Plus the two deferred consolidation fixes.

Non-goal: nothing in this spec issues LLM calls. Every cleanup operation is
mechanical (derived from file existence and frontmatter), so the only budget is
file I/O, which is bounded per sweep.

## Design

Two safety classes, mirroring the organizer/consolidation precedent:

- **Mechanical repairs auto-apply** (retract mentions of deleted notes, fix
  dangling relations, flatten stub chains, prune caches): they remove or repair
  agent-generated pointer lines derived from file existence — the same thing
  re-indexing an edited note already does automatically today. No human content
  is touched.
- **Destructive operations are propose-only** (retiring an entity note):
  surfaced as pending proposals, applied only via an explicit CLI flag after
  human review. Same posture as merge proposals.

### 1. Deleted-note retraction (mechanical, auto-apply)

Detect: `deleted = set(manifest["hashes"]) - set(scan_notes(vault))` — the
same computation as `check_manifest_drift.deleted_but_tracked`. Note this set
contains only TRUE deletions: `mover.move_note` transfers manifest entries and
rewrites path-qualified wikilinks on organizer moves, so moves never appear
here. (A hand-move outside the organizer appears as delete(old)+add(new), and
retract-old + index-new is exactly the right convergence for that too.)

For each deleted note, in path order, capped at `MAX_RETRACTIONS_PER_SWEEP`
(default 100) per sweep:

1. Look up the entities that note mentions via
   `cache.note_entity_paths(db, rel)` and call
   `store.remove_mention(entity_rel, rel)` on each — `VaultError`-tolerant,
   identical to `_retract_stale_mentions`. The DB rows survive the note's
   deletion because the cache is rebuilt from entity markdown, which still
   holds the mention lines. If the DB file is missing, fall back to scanning
   live entity notes for the `[[target|` marker.
2. Drop the note from `manifest["hashes"]` and `manifest["failures"]`.

The cap bounds a mass-deletion event (vault reorganization) to a few sweeps of
catch-up instead of one unbounded pass.

### 2. Orphaned-entity retirement (destructive, propose-only)

**Definition.** A live entity (not a merge stub, not retired) is orphaned iff,
after this sweep's retraction pass:

- zero mention lines in its `## Mentions` section, AND
- zero relation lines in its `## Relations` section, AND
- zero inbound relations from other live entities (DB `edges` where
  `dst_path` = the entity).

The relation clauses are load-bearing: `graphstore.apply` creates relation
ENDPOINTS with no mention line, so mention-count alone would mass-flag
legitimate relation-only entities.

**Proposal flow.** Detection is a single pass over live entities plus one DB
query for inbound edges. Each orphan becomes a proposal
`{path, name, type, reason}` in the librarian state's `cleanup` block
(`pending_retirements`, deduped by path, capped at `MAX_PENDING_RETIREMENTS`,
default 200). The list self-heals: every sweep, proposals whose entity regained
support (or vanished) are dropped. Surfaced in the sweep report and health
(`pending_retirements` count).

**Apply path (explicit, reviewed).** `python -m tesseract_mcp.cleanup <vault>
--apply-retirements [--paths P ...]` retires proposed entities. Retirement
writes a **tombstone stub** in place of the note — frontmatter gains
`retired: <timestamp>` (plus a body line "Retired: orphaned — no mentions or
relations") — rather than deleting the file:

- consistent with the merge-stub precedent (`merged_into`);
- preserves the note's aliases/summary in LiveSync history and in the stub
  itself for audit;
- keeps any stray inbound wiki-link resolvable.

Retirement cascades: inbound relation lines in other entities pointing at the
retired path are removed in the same operation (reusing §3's repair helper),
and the entity's `checked_hash` / `entity_vectors.json` keys are pruned (§4).

**Revival.** `vault.write` is `overwrite=False` on the entity-create path, so a
retired stub at the same path would crash re-creation. Therefore
`GraphStore.upsert_entity_ex` learns: if `find_entity_note` lands on a
`retired` note, revive it — overwrite with a fresh template from the incoming
entity, merging the stub's recorded aliases. A retired entity that shows up in
a new extraction simply comes back.

**Reader exclusions.** Everything that today skips `merged_into` also skips
`retired`: `consolidate.gather_entities`, `cache.rebuild`,
`consolidate._resolve_dup_note`. `GraphStore.find_entity_note` still FINDS the
note (revival needs that) — its callers decide.

### 3. Dangling-relation repair (mechanical, auto-apply)

A relation line whose `[[target|` path does not resolve to a live entity note:

- **target is a merge stub** → rewrite the line to the final canonical (follow
  `merged_into` chains, cycle-guarded, depth-capped at
  `REDIRECT_CHAIN_MAX_DEPTH = 5`), deduped against an already-present identical
  relation on the canonical target;
- **target is retired or missing** → remove the line.

Candidates come cheap from the DB (`edges.dst_path` not in `entities.path`),
verified against the filesystem before editing (the DB may be one rebuild
stale). Edits capped at `MAX_RELATION_FIXES_PER_SWEEP` (default 200).

### 4. Stub-chain flattening & cache pruning (mechanical, auto-apply)

- **Stub chains:** a stub whose `merged_into` target is itself a stub is
  rewritten to point at the end of the chain (cycle-guarded, same depth cap).
  A stub whose target is missing or retired is itself retired (it redirects to
  nothing). Stubs are otherwise kept indefinitely — they are cheap and preserve
  inbound links; deleting them is out of scope.
- **Cache pruning:** with `live = {paths from gather_entities}`:
  - `entity_vectors.json`: drop keys ∉ live (atomic rewrite via the existing
    `_save_entity_vectors`, only when something changed);
  - consolidation state `checked_hash`: drop keys ∉ live;
  - the consolidation `cursor` may name a vanished path — harmless by design
    (path cursor resumes via `bisect` over the sorted live set), left as-is.

  Runs every sweep; it is dict filtering, no I/O beyond one cache rewrite.

### 5. Librarian integration — the `cleanup` step

New module `src/tesseract_mcp/cleanup.py` (one responsibility: "make graph
state converge to the facts that still exist"), orchestrated as a new librarian
step between `cache` and `consolidate`:

- after `cache` so the DB exists for note→entity and inbound-edge lookups;
- before `consolidate` so the consolidation slice does not spend budget
  adjudicating entities that this sweep just orphaned or retired, and pruned
  `checked_hash` keys don't linger an extra sweep.

If the step modified any entity note or the manifest, it triggers
`cache.rebuild` at its end (same posture as the index step), so `consolidate`
and health run against fresh state.

- **State:** `state["cleanup"] = {"pending_retirements": [...], "last_pass"}`.
- **Step result:** `{retracted_notes, removed_mentions, fixed_relations,
  flattened_stubs, proposed_retirements, pruned_cache_keys}` — summarized in
  `_summarize_steps`, one report line:
  `- cleanup: retracted N notes, fixed M relations, K retirement proposals`.
- **Dry-run** (`apply=False`): counts only, no writes — same contract as the
  other steps.
- **Health:** `pending_retirements` count joins the health line. The existing
  `orphaned_entities` check (stale mention rows) stays as the regression
  tripwire — after this ships it should read 0 in steady state.
- **Errors:** the `_step` wrapper isolates the whole step; internally each
  per-note/per-entity edit is `VaultError`-tolerant so one unreadable file
  cannot abort the pass (same posture as `_retract_stale_mentions`).

### 6. F-backstop fix — first-cycle timing

`_backstop_due` inverts its absent-marker default: **no
`backstop_last_advance` marker → NOT due.** The apply path of
`_consolidate_step` stamps `backstop_last_advance = now` when the marker is
absent (first apply-mode sweep), starting the clock WITHOUT running the
backstop. Effect: the first backstop cycle begins `BACKSTOP_MIN_INTERVAL_DAYS`
after the first sweep — by which time the cold-start unchecked drain (which
covers every entity anyway) has finished — instead of immediately re-checking
entities adjudicated days earlier. Dry-runs never stamp (state persists only
under `apply=True`, unchanged).

### 7. F-cluster fix — balanced component splitting

`blocking._cluster_pairs` replaces fixed-stride chunking with **balanced
chunking**: a component of size `n > max_cluster` splits into
`k = ceil(n / max_cluster)` chunks whose sizes differ by at most one
(11 → 6+5, not 10+1; 21 → 7+7+7). With the default `MAX_CLUSTER = 10` no chunk
of a real component can be a singleton, so no member is silently dropped. The
`len >= 2` singleton filter in `candidate_clusters` remains as a guard for
degenerate `max_cluster` values. Known residual (accepted): chunk boundaries
can still separate a candidate pair — inherent to any size cap; balanced
splitting only fixes the stranded-member loss.

## Components / files

- **New `src/tesseract_mcp/cleanup.py`** — deleted-note detection + retraction,
  orphan detection + proposals, retirement apply (tombstone + cascade),
  dangling-relation repair, stub flattening, cache pruning, CLI (`main` with
  `--apply-retirements`, `--paths`). Constants: `MAX_RETRACTIONS_PER_SWEEP =
  100`, `MAX_RELATION_FIXES_PER_SWEEP = 200`, `MAX_PENDING_RETIREMENTS = 200`,
  `REDIRECT_CHAIN_MAX_DEPTH = 5`.
- **New `tests/test_cleanup.py`.**
- **Modify `src/tesseract_mcp/librarian.py`** — `cleanup` step (between cache
  and consolidate), `_summarize_steps` / `format_report` / health additions;
  F-backstop (`_backstop_due` absent-marker default + first-sweep stamp).
- **Modify `src/tesseract_mcp/blocking.py`** — F-cluster balanced chunking;
  `prune_entity_vectors(state_root, live_paths)` helper next to the cache it
  prunes.
- **Modify `src/tesseract_mcp/graphstore.py`** — revival of retired stubs in
  `upsert_entity_ex`.
- **Modify `src/tesseract_mcp/consolidate.py`** — `gather_entities` and
  `_resolve_dup_note` skip `retired` notes (one-line guards).
- **Modify `src/tesseract_mcp/cache.py`** — `rebuild` skips `retired` notes.
- **Modify `tests/test_librarian.py`, `tests/test_blocking.py`,
  `tests/test_consolidate.py`** — F-backstop/F-cluster semantics changes plus
  reader-exclusion coverage.

## Out of scope

- **Relation provenance** — which source note asserted an edge. Without it, a
  relation asserted solely by a deleted note survives as long as both endpoints
  live. Fixing this needs provenance-tagged relation lines or re-extraction,
  and ties into the roadmap ideas already on file (temporal/bi-temporal edges,
  write-provenance on graph mutations). Documented consequence, deliberate
  deferral.
- Deleting merge stubs or retired tombstones (kept as cheap redirect targets).
- Auto-applying retirements (propose-only, human-reviewed — same as merges).
- Salience/decay scoring of entities (separate roadmap item).
- Retuning consolidation similarity thresholds or prompts.
