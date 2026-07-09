# Autonomous Vault Organizer — Design Spec

**Date:** 2026-07-08
**Status:** Approved by Taimoor (brainstorming session, 2026-07-08)
**Builds on:** tesseract-mcp v0.5 (hybrid search + provisioner; embeddings via `sc_adapter.py`/`embeddings.py`)

## Purpose

A fully autonomous librarian for the vault's human topical tree: notes that
land in the wrong place (vault root, misfiled in a topical folder) are moved
to the right folder automatically, decided by embedding neighbor vote —
"file a note where its semantic neighbors live" — using the Smart
Connections vector infrastructure hybrid search already reads.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Autonomy | Fully autonomous (Taimoor's explicit choice over propose-and-approve), WITH mandatory rails: move journal + undo, confidence gate routing ambiguity to a proposals queue, hard exclusions, documented constitution amendment |
| Taxonomy | Frozen: the existing top-level folders ARE the taxonomy, discovered at runtime. The organizer never creates/renames folders |
| Classifier | Embedding neighbor vote (K=10 nearest labeled notes, cosine-weighted folder vote). No LLM calls in v1; low confidence → proposals queue, and each human-resolved proposal becomes a labeled neighbor that sharpens future votes |
| Threshold | Vote share ≥ 0.7 → auto-move; below → proposals. Constant in code, not config, for v1 |

## Scope

**Candidates:** `.md` files at the vault root, and files inside taxonomy
folders whose vote strongly disagrees with their current top-level folder.

**Never touched (hard exclusions):** everything under `Claude/` (functional
structure, not topical), `00 - Maps of Content`, `.obsidian`, `.smart-env`,
`.trash`, `.space`, `copilot`, non-markdown files, and any note with
`organize: false` in frontmatter.

**Duplicate-stem guard:** if another note anywhere in the vault shares the
candidate's filename stem, the move is routed to proposals instead of
executed — bare `[[Stem]]` links would become ambiguous.

## Classification

- Vectors from `embeddings.get_note_vectors` (Smart Connections fresh
  vectors + same-model fallback — existing machinery, nothing new).
- Labeled set = notes currently inside taxonomy folders.
- For a candidate: top K=10 labeled neighbors by cosine similarity; each
  votes for its top-level folder, weighted by similarity; `share` =
  winning folder's weight / total weight.
- Root note: `share ≥ 0.7` → move; else proposal.
- Already-filed note: moved only when the vote picks a DIFFERENT folder
  with `share ≥ 0.7`; low-confidence disagreement leaves it alone (no
  proposal spam for settled notes).

## The move engine

Moving a note = atomically:
1. Rewrite path-qualified inbound wikilinks vault-wide: every
   `[[old/path` occurrence (immediately followed by `]]`, `|`, or `#` — the
   lookahead guard prevents prefix collisions like `[[Note 2` matching
   `[[Note`) becomes `[[new/path`. This includes the path-qualified links
   inside `Claude/Graph/` entity notes — they are inbound links like any
   other. Bare `[[Stem]]` links are untouched: the stem doesn't change and
   the duplicate-stem guard already ensured uniqueness.
2. Move the file (`os.replace` on vault-resolved paths).
3. Transfer the note's hash key in the indexer manifest old→new so the
   move doesn't trigger a spurious re-extraction.
4. Record a journal entry (see below).
5. Once per sweep (not per move): rebuild the SQLite graph cache.

Links FROM the moved note are unaffected (wikilinks are vault-absolute or
stem-based, never relative).

## Journal, undo, proposals

- **Authoritative journal:** JSONL at `state_dir(vault)/organizer_journal.jsonl`
  — one entry per move: `{ts, from, to, share, neighbors, rewrites}` where
  `rewrites` lists every file whose links were rewritten (undo needs this).
- **Human-readable mirror:** each move also appends a line to
  `Claude/Organizer.md`; each sweep with proposals appends a dated
  `### Proposals <date>` block there listing note → suggested folder →
  share → top neighbors.
- **Undo:** `undo_move(path)` finds the newest journal entry with
  `to == path` not already undone, moves the file back, reverses the link
  rewrites (new→old, same lookahead guard), transfers the manifest key
  back, and appends an undo entry to the journal.

## Surfaces

- CLI: `python -m tesseract_mcp.organize <vault> [--dry-run]` — the
  scheduled-sweep entry point (same Task Scheduler pattern as the indexer).
  `--dry-run` prints the full report without touching anything.
- MCP tools: `organize_vault(apply: bool = False)` (dry-run by default for
  on-demand use — autonomy lives in the scheduled sweep, not in casual tool
  calls) and `undo_move(path: str)`.
- Report shape (CLI and tool): `{moved: [...], proposals: [...],
  skipped: [...], cache_rebuilt: bool}`.

## Constitution amendment

`vault/constitution.md` (repo source) gains an `## Organizer` section
documenting the standing permission: the organizer may move notes within
the human topical tree autonomously, under the exclusions and rails above,
per Taimoor's 2026-07-08 decision. Because the conventions installer never
overwrites an existing `Claude/README.md`, updating the LIVE vault's copy
is a listed post-merge human step (or an explicitly-confirmed write).

## First-run requirement

The first live run MUST be `--dry-run`, reviewed by Taimoor. Rationale: the
2026-07-05 Notion import (309 files) may have left mixed-content folders,
and impure folders vote. The dry-run report doubles as an audit of that
import. This is an operational rule, recorded here and in the README.

## Non-goals

- LLM classification fallback (add later only if the proposals queue is
  annoying in practice)
- Creating/renaming/deleting folders; organizing subfolder placement WITHIN
  a top-level area (v1 files to the top-level folder root)
- Moving anything under Claude/ or attachments/non-markdown files
- Configurable K/threshold (constants until real usage argues otherwise)
- Rewriting bare-stem links (duplicate-stem guard makes it unnecessary)

## Testing considerations

- Deterministic cluster embedder fixture (keyword → axis vectors), no model
  downloads
- Taxonomy discovery: exclusions honored; folder added by human later is
  picked up
- Classifier: clear majority → move decision; split vote → proposal;
  already-filed note with agreeing vote → skip; disagreeing high-confidence
  vote → move
- Move engine: path-qualified links rewritten (including in Claude/Graph
  notes); prefix-collision guard (`[[Note 2` survives moving `Note`);
  bare-stem links untouched; manifest key transferred; duplicate stem →
  proposal, file not moved
- Journal/undo: undo restores file location, link targets, and manifest
  key exactly; double-undo is a no-op with a clear message
- `organize: false` frontmatter and every excluded dir honored
- Dry-run touches nothing (filesystem snapshot identical before/after)
