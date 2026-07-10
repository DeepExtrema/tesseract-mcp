# The Librarian — Design Spec

**Date:** 2026-07-09
**Status:** Approved by Taimoor (brainstorming session, 2026-07-09)
**Builds on:** tesseract-mcp v0.6 (hybrid search, semantic graph, auto-organizer, provisioner)

## Purpose

One caretaker loop that manages the databases and files the system already
produces. Today the moving parts are federated — the indexer owns the
manifest, the organizer owns moves, `cache.py` owns the SQLite mirror,
`consolidate.py` owns entity dedupe — each with its own entry point and no
shared supervisor or unified health view. The Librarian is a thin orchestrator
over those existing modules: one scheduled sweep, one human-readable report,
zero new infrastructure. Karpathy-minimal by explicit decision: no new
databases, no queues, no daemons.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Scope | Thin caretaker loop over existing modules (chosen over building the event-feeder system first; that becomes a later, much smaller project) |
| Consolidation | Included, but **throttled and dry-run only** — proposals surface in the report, human applies |
| Surfaces | Scheduled CLI sweep (replaces the separate indexer/organizer schedules) + read-only `librarian_status` MCP tool |
| LLM models | Extraction (high-volume, per-note): **Haiku** ($1/$5 per MTok). Consolidation (occasional, judgment-heavy): **Sonnet** ($3/$15). Env vars `TESSERACT_EXTRACT_MODEL` (default `haiku`) and `TESSERACT_CONSOLIDATE_MODEL` (default `sonnet`), passed as `--model` to the `claude` CLI backend; ignored by the `codex` backend |

## Shape

- New module `librarian.py` + CLI entry point
  `python -m tesseract_mcp.librarian <vault> [--dry-run]`.
- Becomes the **single scheduled task**, replacing the separate indexer and
  organizer Task Scheduler entries.
- Owns no indexing/organizing/caching logic — calls the existing modules in
  order and records what happened.
- Private state (last sweep, consolidation throttle counters) in one JSON
  file, `librarian_state.json`, in the per-vault state dir alongside the
  manifest and embedding cache.

## The sweep pipeline

Steps run in order; each is individually wrapped so one failure doesn't abort
the rest.

1. **Index** — `indexer.run` (hash-diff → extract new/changed → store →
   cache). First, because every later step reads what it produces: fresh
   embeddings and fresh entities. This also warms the embedding cache so the
   organizer's vote is nearly free.
2. **Organize** — the existing neighbor-vote filing pass, applying moves
   (journal/undo/proposals rails unchanged).
3. **Cache rebuild** — guarantee the SQLite graph cache was rebuilt exactly
   once this sweep (the organizer already rebuilds when it moved anything;
   the Librarian ensures it happened, not per-step).
4. **Consolidation (throttled, dry-run)** — see below.
5. **Health checks** — read-only inspections (below).
6. **Report** — dated section appended to `Claude/Librarian.md` + machine
   state to `librarian_state.json`.

`--dry-run` propagates down: index runs read-only checks only, organize
reports without moving, consolidation proposes without the throttle counting
it as done, and the report is printed to stdout instead of written — a
dry-run sweep touches nothing on disk.

## Consolidation throttle

- State file tracks entity count at the last consolidation pass.
- Trigger: **≥ 15 new entities** since that pass, **or** ≥ 14 days elapsed
  with at least one new entity.
- Dry-run only; merge proposals land in the report. Applying stays the
  existing manual `consolidate --apply` path, unchanged.
- Throttle resets only after a pass actually runs (a `--dry-run` sweep does
  not reset it).
- Throttle state lives in the Librarian, not `consolidate.py` — existing
  modules stay untouched and independently invocable.

## Health checks (v1, all read-only)

- **Stale embeddings** — count of notes edited since their cached vector was
  computed (these make searches pay inline embedding cost).
- **Manifest drift** — manifest entries whose files no longer exist, and
  vault notes missing from the manifest.
- **Orphaned entities** — entity notes under `Claude/Graph/` whose mention
  links point at notes that no longer exist.
- **Cache consistency** — entity/relation counts in `graph.db` vs. counts
  derived from the `Claude/Graph/` markdown; a mismatch is reported with a
  "rebuild needed" note.
- **Pending proposals** — count of unresolved organizer proposals +
  consolidation proposals awaiting the human.
- **Sweep errors** — any step that threw this sweep, with the message.

Health checks never modify anything; they run even after earlier step
failures.

## Report

- Each sweep appends a dated `## Sweep YYYY-MM-DD HH:MM` section to
  `Claude/Librarian.md`: per-step one-liners (indexed N, moved N, proposals
  N, consolidation ran / skipped-why, each health check ✓/⚠ with numbers).
- The file is trimmed to the most recent **30 sweeps** so it stays readable
  in Obsidian.
- The same data is written verbatim to `librarian_state.json`.

## MCP tool

`librarian_status()` — read-only; returns the parsed contents of
`librarian_state.json` (last sweep time, per-step results, health summary,
pending-proposal counts). If no sweep has ever run, returns a clear
"no sweep yet" result rather than erroring.

## Error handling

Every pipeline step is wrapped individually; a failure records the error,
skips to the next step, and the CLI exits non-zero at the end if anything
failed — Task Scheduler sees the failure while the report still captures
everything that did run.

## Model configuration

The extractor already shells out to the `codex` or `claude` CLI
(`TESSERACT_EXTRACTOR`). Two new env vars, read where the CLI command is
built:

- `TESSERACT_EXTRACT_MODEL` — default `haiku`; passed as `--model` on
  extraction calls.
- `TESSERACT_CONSOLIDATE_MODEL` — default `sonnet`; passed as `--model` on
  consolidation calls.

Rationale: extraction is high-volume structured extraction from a single
note (Haiku's sweet spot; Opus would pay 5× for judgment the task doesn't
need); consolidation is judgment over the whole entity list but runs at most
biweekly and is human-gated, so Sonnet suffices — bump to `opus` only if
merge proposals prove noisy in practice. These env vars apply only to the
`claude` backend; `codex` ignores them.

## Non-goals (v1)

- Auto-applying consolidation merges
- Fixing health findings automatically (report-only)
- Vault-wide broken-wikilink scanning
- Configurable throttle/trim constants (15 entities / 14 days / 30 sweeps
  are constants in code)
- Any new storage beyond the one JSON state file
- External data feeders (the deep-tech events idea is a separate, later
  project that writes markdown into the vault and inherits the Librarian's
  supervision for free)

## Testing considerations

All against temp vaults with the deterministic embedder fixture (no model
downloads):

- Pipeline ordering: index runs before organize.
- Failure isolation: organize step raising → health checks still run, report
  records the error, CLI exit code non-zero.
- Throttle math: 14 new entities → consolidation skipped; 15 → runs; the
  14-day date trigger; `--dry-run` does not reset the throttle.
- Each health check with a seeded defect: deleted file for manifest drift,
  removed target note for orphaned entities, hand-edited `graph.db` for
  cache consistency, edited note for stale embeddings.
- Report trimming at 30 sweep sections; sections remain well-formed after
  trim.
- `librarian_status` on a fresh state dir returns the "no sweep yet" shape.
- `--dry-run` sweep leaves the filesystem snapshot identical before/after —
  no state file, no report, no moves, no cache writes.
- Model env vars: extraction command line carries `--model haiku` by
  default and respects overrides; consolidation carries `--model sonnet`.
