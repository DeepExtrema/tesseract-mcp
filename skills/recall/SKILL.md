---
name: recall
description: Use when Taimoor asks what he or the vault knows about a topic, wants a researched answer from the mind database, or says "recall X" / "what do we know about X". Searches the Tesseract vault, synthesizes a cited answer, and files it into Claude/Answers/ so knowledge compounds.
---

# Recall — researched Q&A over the Tesseract vault

Answer the question from the vault, cite every claim, file the answer.
Requires the `tesseract` MCP server.

## Contract (non-negotiable)

1. **Citation-or-label.** Every factual claim either cites its source note
   as a `[[wikilink]]`, or is explicitly marked *(not from the vault)*.
2. **Thin retrieval → say so.** If the vault has little on the topic,
   report that honestly and STOP. Never pad an answer with model knowledge
   dressed up as vault knowledge. No answer note is filed in that case.
3. Always end with a **"What the vault doesn't know"** section.

## Steps

1. `context_bundle(question, limit=10)` — hybrid hits + graph entities +
   related notes in one call.
2. Judge coverage: are at least 2 hits genuinely about the question? If
   not: tell Taimoor the vault has almost nothing on this, list the nearest
   misses, offer to `capture` the question or `add_task` a research
   follow-up, and stop here.
3. `read_note` the top 3–5 relevant hits IN FULL — the bundle's excerpts
   locate notes, they are not enough to synthesize from. Follow one or two
   `related_notes` chains if they add real context.
4. Compose the answer with exactly this structure:
   - `## Answer` — synthesized, every claim carrying a `[[wikilink]]`.
   - `## Sources` — bullet list of every cited note.
   - `## What the vault doesn't know` — gaps, contradictions, staleness.
5. Offer to file each gap as a task (`add_task`) or a capture — the gaps
   are tomorrow's ingest queue.
6. File the answer with `write_note`:
   - Path: `Claude/Answers/YYYY-MM-DD <question slug>.md` — slug is the
     question compressed to at most 6 words with the characters
     `\ / : * ? " < > | [ ] # ^` stripped. If the path already exists,
     suffix ` 2`, ` 3`, … (same rule as session notes).
   - Frontmatter (YAML):
     `created: YYYY-MM-DD HH:MM`, `agent: claude`, `project: <if obvious,
     else "">`, `tags: [answer]`, `type: answer`,
     `question: "<the exact question asked>"`.
   - Body: the three sections from step 4.
7. Show the full answer in chat as well — the note is for the vault, the
   chat reply is for now.
