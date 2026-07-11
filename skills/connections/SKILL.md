---
name: connections
description: Use when Taimoor asks what connects to a topic, wants serendipity from the vault — "show me something I forgot", "what links to X", "anything related to what I'm doing?" — or after finishing work on a topic. Walks the Tesseract entity graph for non-obvious links.
---

# Connections — graph serendipity

Requires the `tesseract` MCP server. Chat-only output; nothing is filed
unless Taimoor blesses a connection.

## Steps

1. Seed selection:
   - With a topic argument: `find_entity(topic)` for seed entities and
     `search_brain(topic, limit=3)` for seed notes.
   - Without: `list_recent(10)` and take the 2 newest notes under
     `Claude/Sessions/` or `Claude/Answers/` as seed notes.
2. For each seed note: `related_notes(path, hops=2)`.
3. Rank by SURPRISE, not relevance:
   - Prefer results whose `via` chain passes through 2+ entities — one-hop
     neighbors are usually already known.
   - Drop results the seed note already links directly (check the seed's
     own `[[wikilinks]]` via `read_note`, or `get_backlinks`) — those are
     memory, not serendipity.
4. Present the top 3–5 in chat, each as one line:
   `[[note]] — via <the "via" chain>` plus one sentence on why it might
   matter right now.
5. For each connection Taimoor calls interesting, file exactly one
   capture: `capture("<seed> ↔ <note>: <why it matters>")`.
