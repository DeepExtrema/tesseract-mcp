---
name: digest
description: Use when Taimoor asks for his digest, review, "what's new in the vault", or a morning/weekly catch-up. Composes a review note from the Tesseract vault — recent changes, captures, tasks, librarian health, suggested questions — and files it to Claude/Digests/.
---

# Digest — the vault review ritual

Requires the `tesseract` MCP server.

## Steps

1. Find the newest note in `Claude/Digests/` (`query_notes` with
   `folder="Claude/Digests"`, or list the folder). Its date (from the
   filename `YYYY-MM-DD.md`) is `since`. If the folder is empty, omit
   `since` — the bundle defaults to 7 days back.
2. `recall_bundle(mode="digest", since="<YYYY-MM-DD>")`.
3. Compose the digest with EXACTLY these sections in this order. An empty
   section says "none" rather than disappearing — the eye learns the
   layout. A bundle section with `status: "error"` renders as
   `⚠ <section>: unavailable (<error>)` — never silently dropped.

   `## Health` — Librarian last-sweep timestamp and a one-line health
   summary from the `librarian` section. If the last sweep is older than
   48 hours, open the line with `⚠ stale sweep`.

   `## Captures to triage` — `inbox_captures` notes, each `[[wikilinked]]`.

   `## Tasks` — open tasks (count, then list), then `done_recently`.

   `## Recent changes` — `recent_notes` grouped by top-level folder,
   `[[wikilinked]]`. Skip the digest/answer notes this harness itself
   wrote if they dominate the list.

   `## Proposals pending` — the `proposals` count plus a pointer to
   `[[Organizer]]` and `[[Librarian]]`.

   `## New graph activity` — `new_entities` notes (entity names are the
   filename stems), `[[wikilinked]]`.

   `## Suggested questions` — 2–3 questions the vault is NEWLY equipped
   to answer, inferred from recent changes and new entities. Write each as
   a one-liner Taimoor can paste straight into `/recall`.

4. Write the note with `write_note` to `Claude/Digests/YYYY-MM-DD.md`
   (today's date), `overwrite=True` — rerunning the same day replaces that
   day's digest. Frontmatter: `created: YYYY-MM-DD HH:MM`, `agent: claude`,
   `project: ""`, `tags: [digest]`, `type: digest`.
5. Show the digest in chat too.
