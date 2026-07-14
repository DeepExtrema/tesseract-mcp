---
name: decide
description: Use when Taimoor states or asks to record a real decision — "we're going with X", "log this decision", "decide between A and B" — or when a session produces a choice that future sessions must honor. Checks Claude/Decisions.md for conflicts, then appends the decision with rationale and supersession markers.
---

# Decide — the decision ledger ritual

Requires the `tesseract` MCP server. `Claude/Decisions.md` is
APPEND-ONLY: history is the point. Existing lines are never edited,
reworded, or deleted — supersession is expressed by the new entry.

## Steps

1. `read_note("Claude/Decisions.md")` in full, and
   `search_brain("<decision topic>", limit=5)` for related sessions and
   answers — a decision made blind to its predecessors is how the ledger
   contradicts itself.
2. If a prior entry conflicts with or is replaced by the new decision,
   quote it verbatim in chat and ask Taimoor to confirm the
   supersession. Do not file a conflicting decision without that call —
   two live contradictory entries poison every future recall.
3. Append the new entry: rewrite the file with
   `write_note(overwrite=True)`, keeping all existing content
   byte-identical and adding one entry at the end. Match the file's
   existing entry format; if in doubt:
   `- YYYY-MM-DD <decision> — <one-line rationale>` with
   ` (supersedes YYYY-MM-DD "<old decision>")` when step 2 applies.
4. Record the why, not just the what — the rationale line is what makes
   the entry worth reading in six months. One line; link evidence notes
   as `[[wikilinks]]` where they exist.
5. If the decision implies follow-up work, `add_task` each item (with
   `due` if Taimoor gave one).
6. If the decision changes how agents should behave in a specific repo,
   say so: the repo's AGENTS.md/CLAUDE.md is the enforcement point for
   per-repo behavior — the ledger records, it does not enforce.
