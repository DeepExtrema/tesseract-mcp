---
name: rounds
description: Use when Taimoor asks for a vault health check, hygiene sweep, or lint pass — "how's the vault", "run rounds", "anything drifting?", "check the caretakers" — or before trusting the vault after a long gap. Inspects Librarian, graph, organizer, and task health read-only and reports findings worst-first.
---

# Rounds — caretaker inspection, read-only

Requires the `tesseract` MCP server. Rounds INSPECT and REPORT; they
never fix. Every `apply=True` is Taimoor's call, made after reading the
report — the consent gate is the design, not a formality.

## Steps

1. `librarian_status()` — last sweep timestamp (older than 48h opens
   the report with `⚠ stale sweep`), per-step failures, health checks
   (stale embeddings, manifest drift, orphaned entities, cache
   consistency), pending proposal counts.
2. `graph_stats()` for scale, then `consolidate_graph(apply=False)` —
   duplicate-entity merge candidates: report the count and the top 3
   proposed merges verbatim.
3. `organize_vault(apply=False)` — filing proposals: count plus 3
   notable examples (`note → proposed folder`).
4. `list_tasks()` — open-task count; flag likely-dead tasks (past-due
   dates, obvious duplicates) by quoting them, nothing more.
5. Compose the rounds report in chat, worst first:
   - 🔴 **Broken** — failed sweeps, health-check failures, tool errors.
   - 🟡 **Drifting** — stale sweep, duplicate entities, proposal
     backlog, dead tasks.
   - 🟢 **Healthy** — one line, counts only.
   Each finding is one line carrying its evidence. A section with
   nothing to report says "none" — the eye learns the layout.
6. For each 🔴/🟡 finding, offer a follow-up; `add_task` only the ones
   Taimoor blesses. Applying merges/moves happens only if Taimoor
   explicitly says so, and then via the same tools with `apply=True`.
7. Chat-only output — a health report expires in days; filing it would
   teach search to retrieve dead state. If Taimoor wants a record, fold
   it into the day's `log_session` instead.
