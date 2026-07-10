# Recall Harness — Design

**Date:** 2026-07-10
**Status:** Approved (brainstorm complete)
**Depends on:** hybrid search, GraphRAG graph, Librarian, write quarantine (all shipped)

## Context

The tesseract engine (retrieval, graph, caretaking, evals) is built. What is
missing is the **usage layer** — how the human actually asks, reviews, and
compounds knowledge day to day. This design covers the first of four
usage-layer sub-projects, decomposed during brainstorming:

1. **Recall harness (this spec)** — the human-facing ask/review loop.
2. Agent discipline layer — hooks/prompts so every session recalls-then-logs.
3. External agent onboarding — Hermes, OpenClaw, deep-research agents with scoped access.
4. Ingest loop — web clips, papers, transcripts feeding the vault.

Each later sub-project gets its own spec → plan → implementation cycle.

### Source inspiration

Andrej Karpathy's "LLM Knowledge Bases" post (x.com/karpathy/status/2039805659525644595,
2026-04-02) describes a personal system with six pillars. Mapping to tesseract:

| Pillar | Tesseract status |
|---|---|
| Ingest: raw sources compiled into a wiki | Partial (fetch/arxiv MCPs; no compile pipeline — sub-project 4) |
| Obsidian as IDE; LLM owns the wiki | Built — quarantine enforces what he has by convention |
| Q&A against the wiki | Built and beyond (hybrid search + GraphRAG, measured) |
| **Outputs filed back so queries "add up"** | **Gap — the core of this spec** |
| Linting/health checks | Built (Librarian, consolidate_graph) |
| Search tool shared by human and LLM | Partial — MCP tools exist; no human ritual |

The load-bearing idea adopted here: **query outputs are writes.** Every
answer is rendered as a markdown note and filed into the vault, so the
knowledge base compounds from asking questions, not only from ingesting.

## Decisions made during brainstorming

- **Surface:** Claude Code / Cowork is the recall cockpit; Obsidian is the
  viewer. No new UI is built.
- **Use cases:** all four — research Q&A, project/work memory, digest/review
  ritual, serendipity/connections.
- **Approach:** Hybrid (option C) — skills own composition and rendering;
  the server gains exactly one deterministic read-only tool (`recall_bundle`).
  Server-side LLM synthesis was rejected (duplicates the calling agent);
  skills-only was rejected in favor of one round-trip-saving bundle tool.

## Architecture

```
You, in Claude Code / Cowork  ── the cockpit (ask, resume, digest, connect)
        │
Skills layer (NEW)            ── /recall /resume /digest /connections
        │                        versioned in repo under skills/,
        │                        synced additively to ~/.claude/skills/
        │
MCP server (ONE new tool)     ── recall_bundle (read-only, deterministic)
        │
Vault (NEW conventions only)  ── Claude/Answers/  rendered recall outputs
                                 Claude/Digests/  daily/weekly review notes
```

### Vault conventions

- `/recall` answers file to `Claude/Answers/YYYY-MM-DD-<slug>.md` with
  frontmatter: `type: answer`, `question: "..."`; every claim cited as a
  `[[wikilink]]` to its source note.
- Digests file to `Claude/Digests/YYYY-MM-DD.md` — a separate folder because
  it is a ritual surface: newest digest = morning inbox in Obsidian.
- Both folders live inside the `Claude/` quarantine, so with zero new code:
  writes are permitted, the Librarian indexes them into hybrid search, and
  the extractor pulls their entities/wikilinks into the graph. Past answers
  become retrievable context for future questions.
- `type: answer` frontmatter is the anti-echo-chamber valve: if answers ever
  pollute retrieval, filters can exclude them. The filter is not built now;
  the tag makes it possible later.
- Conventions are added to the conventions installer
  (`scripts/install_conventions.py`) and documented in the vault constitution.
- Skills are versioned in the repo (`skills/`) and synced to
  `~/.claude/skills/` (personal level — recall happens from any directory;
  Cowork picks up the same skills). Sync is additive, mirroring the
  `mcp_sync` philosophy: never modify or remove existing entries without
  an explicit `--force`.

## Components

### `/recall <question>` — research Q&A (flagship)

1. `context_bundle(question)` — hybrid hits + graph entities + related notes.
2. `read_note` the top hits in full (excerpts are not enough for synthesis).
3. Synthesize with **citation-or-label contract**: every claim carries a
   `[[wikilink]]`; model-knowledge additions are explicitly labeled
   "not from the vault."
4. Mandatory **"What the vault doesn't know"** section; each gap optionally
   becomes `add_task` or `capture` (builds the future ingest queue).
5. File to `Claude/Answers/`; also print the answer in the terminal.

### `/resume [project]` — project/work memory

1. `recall_bundle(mode="resume", project=...)` → session notes matching the
   project, `Decisions.md` entries mentioning it, open tasks, related entities.
2. Compose a "you were here" brief: last state, open threads, decisions in
   force, next actions.
3. **Not filed by default** — resume briefs expire in days; `--save` files a
   milestone snapshot when wanted.

### `/digest` — the review ritual

1. `recall_bundle(mode="digest", since=<last digest date>)` → new/changed
   notes, inbox captures awaiting triage, open + newly completed tasks,
   Librarian last-sweep health, pending organizer proposals, entities that
   gained new edges.
2. Compose `Claude/Digests/YYYY-MM-DD.md` with a **fixed section order** (the
   eye learns where to look), ending with **"Suggested questions"** — 2–3
   questions the vault is newly equipped to answer, each pasteable into
   `/recall`.
3. Manual for the first week (format iteration), then scheduled daily via a
   scheduled agent.

### `/connections [topic]` — serendipity

1. Seed: the topic argument, else entities from the most recent sessions/answers.
2. Walk `related_notes`/`find_entity` two hops; rank by surprise —
   connections whose endpoints never co-occur in any note.
3. Present 3–5 with the chain shown (`A —[person: X]— B`); interesting ones
   are `capture`d on request.

### Filing rule across the verbs

**File what compounds, skip what expires.** `/recall` always files, `/digest`
always files, `/connections` files only blessed items, `/resume` files only
with `--save`. Filing expiring state would teach hybrid search to retrieve
stale context.

### `recall_bundle` — the one server addition

Read-only MCP tool in `server.py`, composing existing internals. No new
indexes, no LLM calls.

```
recall_bundle(mode: "digest" | "resume",
              project: str | None,   # resume only: matched against session
                                     #   frontmatter, decisions, tasks
              since: str | None)     # digest only: ISO date, default 7 days back
→ JSON: named sections, each with note paths + excerpts + per-section status
```

Purpose: collapse four-to-six tool round-trips into one and give skills a
stable shape to render from. If a section's source fails (e.g., stale graph
cache), that section reports its own status; the bundle never fails whole.

## The compounding loop

```
/recall → retrieve → answer filed in Claude/Answers/
                          │
        Librarian sweep indexes it + extracts entities
                          │
   future /recall retrieves past answers as first-class sources
                          │
 /digest surfaces gaps + suggests questions → ask again ↺
```

The `/recall` gaps section and `/digest` suggested-questions section are the
loop's fuel injectors: they convert "what the vault doesn't know" into
tomorrow's ingest and queries.

## Error handling

- **Hallucination guard:** citation-or-label contract. On thin retrieval,
  `/recall` says "the vault has almost nothing on this" and stops.
- **Staleness:** every digest opens with the Librarian's last-sweep
  timestamp; old sweep = flagged in the digest header.
- **Write safety:** all writes land under `Claude/` — quarantine-safe by
  construction; the confirmation path is untouched.
- **Degradation:** `recall_bundle` reports per-section status rather than
  failing the whole bundle.

## Testing

- `recall_bundle`: pytest against the existing fixtures vault — section
  population, `since` filtering, resume project-matching, per-section
  degradation.
- Skills: manual QA checklist per skill (they are prompts, not code).
- Deferred, once the loop runs for real: citation-rate check on `/recall`
  answers over golden queries, reusing the evals harness.
- Digest scheduling follows the Librarian operational rule: manual first,
  human-reviewed, then scheduled.

## Rollout order (each step independently usable)

1. `recall_bundle` + tests (server)
2. Vault conventions: `Claude/Answers/` + `Claude/Digests/` in the
   conventions installer + constitution
3. `/recall` skill — compounding starts here
4. `/digest` skill, manual for a week
5. `/resume` + `/connections`
6. Schedule the digest; consider the citation-rate eval

## Forward compatibility with deferred sub-projects

- **External agents (sub-project 3):** `recall_bundle` is an MCP tool, so
  Hermes/OpenClaw inherit it; they can also read filed answers.
- **Ingest loop (sub-project 4):** plugs directly into the gaps/`capture`
  queue this harness creates.
- **Agent discipline (sub-project 2):** `/resume` becomes the natural
  session-start hook target.
