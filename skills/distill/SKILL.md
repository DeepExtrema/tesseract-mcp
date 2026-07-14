---
name: distill
description: Use when Taimoor asks to consolidate the vault's raw memory — "distill the inbox", "promote captures to concepts", "consolidate what we learned about X" — or when the Inbox has piled up. Promotes repeated capture/session material into evergreen Claude/Concepts/ notes with cited sources.
---

# Distill — promote memory into knowledge

Requires the `tesseract` MCP server. Turns layer-1 memory (captures,
session logs) into layer-2 knowledge (concepts). Salience-gated: most
raw material should NOT be promoted.

## Steps

1. Gather candidates:
   - With a topic argument: `search_brain(topic, limit=10)` plus
     `query_notes(folder="Claude/Inbox")`.
   - Without: `query_notes(folder="Claude/Inbox")` plus the last ~14
     days of `Claude/Sessions/` notes (`list_recent`, filter by folder).
2. Cluster the material into candidate concepts. The salience gate: a
   candidate qualifies ONLY if its idea appears in 2+ independent notes,
   or Taimoor named the topic explicitly. Everything else stays in the
   Inbox — premature promotion bloats Concepts and dilutes retrieval.
3. For each qualifying candidate, search before writing:
   `find_entity(name)` and `query_notes(folder="Claude/Concepts")`.
   If a nearby concept exists, EXTEND it — never create a near-duplicate
   with a different name.
4. `read_note` every source note in full — excerpts locate, they are
   not enough to distill from.
5. `upsert_concept(name, content)`. Content rules:
   - Only claims that will still hold beyond the week they were written.
   - Every claim cites its source note as a `[[wikilink]]`.
   - Contradictions between sources are stated side by side with their
     dates, never silently resolved — the newer claim is flagged as
     newer, not declared the winner.
6. Report in chat: what was promoted into which concept, and what was
   left in the Inbox and why (the gate's verdict, one line each).
7. Delete nothing and move nothing — captures stay where they are; the
   organizer and Librarian own filing and cleanup.
