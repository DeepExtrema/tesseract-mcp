---
name: dossier
description: Use when Taimoor wants a full briefing document on a topic rather than a quick answer — "give me the dossier on X", "brief me on everything we have about Y", "prep me for this meeting/interview from the vault". Synthesizes a multi-section, fully cited briefing from the Tesseract vault and files it to Claude/Answers/.
---

# Dossier — the deep briefing ritual

Requires the `tesseract` MCP server. A dossier is `/recall`'s big
sibling: a standing briefing document, not an answer to one question.
It inherits recall's contract — citation-or-label, thin retrieval means
say so and stop, never pad with model knowledge dressed as vault
knowledge.

## Steps

1. `context_bundle(topic, limit=10)`. Judge coverage: at least 3 hits
   genuinely about the topic, spanning more than one note type
   (sessions, concepts, answers, graph). If not, tell Taimoor the vault
   is too thin for a dossier, list the nearest misses, offer `/recall`
   for the narrow question or `add_task` to research — and stop. No
   note is filed.
2. `read_note` the top 5–8 relevant notes IN FULL; follow 1–2
   `related_notes` chains and `find_entity` results where they add
   context the hits lack.
3. Compose with exactly these sections:
   - `## Executive summary` — what Taimoor needs in 30 seconds.
   - `## Timeline` — dated events pulled from sessions and
     `Claude/Decisions.md`, oldest first.
   - `## Key concepts` — the load-bearing ideas, each `[[wikilinked]]`.
   - `## Tensions and open questions` — contradictions between notes
     (side by side, with dates) and gaps the vault cannot answer.
   - `## Sources` — every cited note.
   Every claim carries a `[[wikilink]]` or is marked *(not from the
   vault)*.
4. File with `write_note` to
   `Claude/Answers/YYYY-MM-DD Dossier <slug>.md` — slug rule as in
   `/recall` (≤6 words, characters `\ / : * ? " < > | [ ] # ^`
   stripped, suffix ` 2`, ` 3`, … on collision). Frontmatter:
   `created: YYYY-MM-DD HH:MM`, `agent: claude`, `project: <if obvious,
   else "">`, `tags: [dossier]`, `type: dossier`,
   `question: "<the topic as asked>"`.
5. Show the full dossier in chat. If the harness has a page surface
   (Claude Code's Artifact tool, a canvas), offer a rendered version —
   presentation only; the markdown note in the vault is the durable
   copy.
6. Offer each item under "Tensions and open questions" as an `add_task`
   — the gaps are tomorrow's ingest queue.
