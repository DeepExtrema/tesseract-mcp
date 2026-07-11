---
name: resume
description: Use when Taimoor asks where he left off, what the state of a project is, or to pick a project back up — "resume tesseract", "where was I on X", "what's the state of Y". Composes a briefing from the Tesseract vault's sessions, decisions, and tasks. Chat-only unless --save.
---

# Resume — project memory briefing

Requires the `tesseract` MCP server.

## Steps

1. Project = the argument. If missing, ask which project — offer candidates
   from `query_notes(folder="Claude/Sessions")` frontmatter `project`
   values.
2. `recall_bundle(mode="resume", project="<project>")`.
3. `read_note` the 1–2 newest session notes IN FULL — the bundle's excerpts
   locate them; the full text carries the actual state.
4. Compose the briefing in chat:
   - **Last state** — what the most recent session ended with.
   - **Open threads** — unresolved items across the recent sessions.
   - **Decisions in force** — the bundle's matching `Decisions.md` lines.
   - **Next actions** — the bundle's matching open tasks.
5. Do NOT file the briefing. Filing rule: file what compounds, skip what
   expires — a resume brief is stale in days, and filing it teaches search
   to retrieve dead state.
6. Exception: if the arguments contain `--save`, write a milestone snapshot
   with `write_note` to `Claude/Answers/YYYY-MM-DD Resume <project>.md`,
   frontmatter `created: YYYY-MM-DD HH:MM`, `agent: claude`,
   `project: <project>`, `tags: [resume]`, `type: resume`.
