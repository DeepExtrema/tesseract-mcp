# Tesseract Roadmap — The Entire Thing

**Date:** 2026-07-11
**Status:** Living document — update the milestone board as things ship
**Scope:** North-star, principles, sub-project inventory, build order, and
interfaces. Detailed designs live in the per-milestone specs indexed below.

## North-star

The vault is Taimoor's **personal API** — the persistent state store for his
life and work — and agents are stateless, interchangeable workers against it.
Onboarding any agent, present or future, is: register the MCP server, read
the constitution, discover contracts (`onboard`, `sheet_schema`). Agents
commoditize; the vault compounds. The moat is ten years of compiled, cited,
schema'd knowledge — not whichever model is fashionable.

Source inspiration: Andrej Karpathy's "LLM Knowledge Bases"
(x.com/karpathy/status/2039805659525644595). Load-bearing idea adopted
throughout: **query outputs are writes** — the knowledge base compounds from
asking questions, not only from ingesting.

### The six layers

```
5  Workers    agents with roles — the applier (Cowork), researcher, librarian
4  Rituals    /recall  /digest  /resume  /connections
3  Records    sheets: jobs now, projects/subscriptions later
2  Knowledge  graph entities, concepts, Decisions.md
1  Memory     sessions, answers, captures
0  Substrate  markdown + LiveSync + quarantine — must never break
```

Each layer only depends downward. A milestone belongs to exactly one layer.

## Principles

1. **Markdown is the only source of truth.** All derived state (index,
   embeddings, graph SQLite) is rebuildable and lives outside the vault.
2. **Contracts enforced in code, server-side.** Quarantine, sheet schemas,
   filing rules — never by prompt folklore. Client-side rules drift;
   server-side rules hold for every agent automatically.
3. **Query outputs are writes.** Answers, digests, resumes file back into the
   vault and become retrieval sources.
4. **Consent-gated rollouts.** Live-vault changes (constitution edits,
   conventions installs, scheduled jobs, MCP registrations) happen only with
   explicit approval, milestone by milestone.
5. **Measure before believing.** Retrieval changes are accepted only when the
   eval scorecard (evals/golden.yaml) says they help.
6. **YAGNI at every layer.** Briefs stay briefs until their build slot
   arrives.

## Non-goals

- Not Jira / not a collaborative multi-user tool.
- No server-side LLM synthesis (the calling agent synthesizes; the server is
  deterministic).
- No agent writes outside `Claude/` and granted sheet islands.
- No UI of our own — Obsidian is the viewer; Claude Code / Cowork is the
  cockpit.
- No automatic edits to human views (`Tracker.base`, human notes).

## Milestone board

| # | Milestone | Layer | Spec | Status |
|---|---|---|---|---|
| M0 | Ops hardening | substrate | [2026-07-11-ops-hardening-design.md](2026-07-11-ops-hardening-design.md) | spec ready |
| M1 | Sheets v1 (jobs) | records | [2026-07-11-structured-sheets-design.md](2026-07-11-structured-sheets-design.md) | spec approved |
| M2 | Cowork onboarding | workers | [2026-07-11-cowork-onboarding-design.md](2026-07-11-cowork-onboarding-design.md) | spec ready |
| M3 | Discipline hooks | rituals | [2026-07-11-discipline-hooks-design.md](2026-07-11-discipline-hooks-design.md) | spec ready |
| M4 | Ingest loop | memory | [briefs](2026-07-11-usage-layer-briefs.md#m4-ingest-loop) | brief |
| M5 | Projects sheet | records | [briefs](2026-07-11-usage-layer-briefs.md#m5-projects-sheet) | brief |
| M6 | Retrieval upgrade | substrate | [briefs](2026-07-11-usage-layer-briefs.md#m6-retrieval-upgrade) | brief, evidence-gated |
| M7 | Generic agent onboarding | workers | [briefs](2026-07-11-usage-layer-briefs.md#m7-generic-agent-onboarding) | brief |

**Build order: M0 → M1 → M2 (the job pipeline goes live) → M3 → M4/M5 → M7,
with M6 triggered by evidence, not calendar.** Decided 2026-07-11: the job
pipeline is the forcing function — one real process fully agent-operated
proves the whole stack and pays off immediately (active job hunt).

Dependencies:

- M0 unblocks everything — the live server cannot search until the
  eager-import fix ships (2026-07-11 audit).
- M1 → M2 is the critical path to the payoff. The M1 acceptance test *is*
  the M2 acceptance test, run from Cowork.
- M3 depends only on `recall_bundle` (shipped) + a small CLI; ordered after
  M2 by choice, not dependency.
- M5 reuses M1's mechanism (one `_schema.md`, zero new server code expected).
- M6 trigger: golden-set scores degrade, or the corpus passes ~2k notes,
  whichever first. Until then bge-micro-v2 stays (0.94 success@10 measured).
- M7 generalizes what M2 proves.

## Interfaces between milestones

The contracts that make the milestones composable — pinned here so later
specs can't drift:

- **Hooks (M3) consume `recall_bundle` (shipped) via a package CLI** — hooks
  run shell commands, not MCP calls. New: `python -m tesseract_mcp.recall`
  gains a CLI (`--context [--project <name>] [--budget N]`) printing a
  compact context block.
- **`/digest` (M3) consumes `sheet_query` (M1)** for the follow-ups-due
  section. Filters: `next_follow_up lte today`, `status nin
  [Rejected, Ghosted, Withdrawn]`.
- **Ingest (M4) produces `Claude/Inbox/` notes with `source:` frontmatter;
  the Librarian (shipped) indexes and the organizer files them.** Ingest
  never writes outside `Claude/`.
- **Onboarding (M2, M7) consumes `onboard` + `sheet_schema` + the
  constitution.** No client-side contract copies; discovery is server-side.
- **All writes, from every milestone, pass the three write classes** (M1):
  `Claude/` free · sheet folders schema-validated via `sheet_upsert` ·
  everything else confirm-gated.
- **Caretakers respect sheet islands** (M1): folders containing `_schema.md`
  are invisible to the organizer/mover, indexed by search/graph.

## Document set and maintenance

- This roadmap: update the milestone board row when a milestone ships;
  append decisions that change ordering. Everything else is history —
  session logs and Decisions.md in the vault carry the narrative.
- Specs (`docs/superpowers/specs/`): one per milestone, written at build
  proximity (full spec when its slot nears; brief until then). Existing
  specs (recall harness 2026-07-10, sheets 2026-07-11) slot in unchanged.
- Plans (`docs/superpowers/plans/`): one per spec, TDD, written by the
  writing-plans flow when implementation starts.
- The audit that grounds M0: session log
  `Claude/Sessions/2026-07-11 Full-system audit...` in the vault.
