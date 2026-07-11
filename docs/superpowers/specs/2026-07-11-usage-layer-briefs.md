# M4–M7: Usage-Layer Briefs

**Date:** 2026-07-11
**Status:** Decision-complete briefs — each becomes a full spec via a
brainstorm session when its build slot arrives (roadmap build order).
**Roadmap:** [2026-07-11-tesseract-roadmap.md](2026-07-11-tesseract-roadmap.md)

A brief pins: goal, interfaces (so nearer-term work can't paint over
them), decisions already taken, and the open questions the future
brainstorm must answer. Briefs are deliberately not implementable.

---

## M4: Ingest loop

**Goal.** Deliberate compilation of external sources — web pages, arXiv
papers, transcripts — into vault notes with provenance, closing Karpathy
pillar 1 (the only pillar still "partial").

**Interfaces (pinned).**
- Ingest writes **only** to `Claude/Inbox/` with `source:` (URL/DOI) and
  `type: clip` frontmatter; the Librarian indexes, the organizer files.
- Raw fetching uses the already-pinned MCPs (`mcp-server-fetch`,
  `arxiv-mcp-server` — see mcp-servers.json); paper/page content is
  untrusted input (existing bundle rule).
- The compile step is agent-side (a skill), not server-side (principle:
  no server-side LLM synthesis).

**Decisions taken.**
- Ritual, not firehose: a `/clip <url>` skill compiles one source into
  one cited note on request. No background crawlers, no auto-subscriptions
  in v1 — volume without curation dilutes retrieval (eval scores are the
  canary).
- Compiled note format: summary + key claims, each carrying its source
  anchor; entities left to the existing extractor.

**Open questions for the spec.**
- Transcript ingestion (meetings? YouTube?) — which sources actually
  matter to Taimoor's workflow.
- Dedupe when the same URL is clipped twice (upsert-by-source vs. new
  note + wikilink).
- Whether /clip should propose graph entities inline or defer entirely to
  the extractor sweep.

---

## M5: Projects sheet

**Goal.** Project state as records: one row per active project (stage,
next action, blockers, links), so `/resume` reads typed state and Cowork
project folders have a vault-side anchor.

**Interfaces (pinned).**
- Implemented as a second sheet: one `_schema.md` in a `Projects/`
  folder — **zero new server code expected** (M1's acceptance criterion
  for mechanism generality).
- `/resume` consumes `sheet_query("projects", ...)` in addition to the
  resume bundle; `/digest` gains a stalled-projects section
  (`last_touched` older than N days).

**Decisions taken.**
- Rows are project *state*, not project *content*: briefs, decisions, and
  artifacts stay notes/wikilinks; code stays in repos. The sheet is the
  index, not the filing cabinet.

**Open questions for the spec.**
- Column set (stage enum, priority, next_action, last_touched, links).
- Relationship to `Claude/Graph/Projects/` entities (row ↔ entity
  linking convention) and to Cowork's own project folders.
- Whether session logs should auto-touch `last_touched` (would be the
  first automated sheet write not initiated by an agent decision).

---

## M6: Retrieval upgrade

**Goal.** Keep retrieval quality ahead of corpus growth without breaking
the vector-space invariant.

**Trigger (pinned — evidence, not calendar).** Golden-set scores degrade
across two consecutive eval runs, or the corpus passes ~2k notes,
whichever first. Until then: bge-micro-v2 measured at success@10 0.94 /
MRR 0.89 (2026-07-11) — not the bottleneck.

**Interfaces (pinned).**
- Any embedder change goes through the eval harness A/B (history JSONL
  keeps the before/after); acceptance = strictly better scorecard on an
  expanded golden set (≥10 added hard-paraphrase queries, including the
  known para-dentist miss).
- Changing the model means **decoupling from Smart Connections'
  embeddings** (same-model invariant, embeddings.py) — the fallback store
  becomes the only store; SC remains an Obsidian UI feature, not search
  infrastructure.

**Open questions for the spec.**
- Candidate models (bge-small, nomic-embed-text, whatever 2027 brings)
  and their Windows/CPU latency budget.
- Whether to add a reranker lane instead of/on top of a bigger embedder.
- Migration mechanics: full re-embed cost, staged cutover.

---

## M7: Generic agent onboarding

**Goal.** Any agent — Hermes, OpenClaw, deep-research fleets — becomes a
vault worker the way Cowork did in M2: register, `onboard`, discover
contracts. M7 turns M2's checklist into a repeatable pattern with access
tiers.

**Interfaces (pinned).**
- Onboarding is server-side discovery (M2 rule): if an agent needs copied
  contracts to behave, the server contract surface is the bug.
- All access flows through the three write classes; nothing about M7 may
  weaken them.

**Decisions taken.**
- Cowork (M2) is the template; M7 starts by extracting its checklist into
  a doc + `mcp_sync` coverage for each client's config location.

**Open questions for the spec.**
- **Access tiers:** does a deep-research fleet get write access at all,
  or read + `capture` only? Per-agent `agent:` stamping exists (M1);
  per-agent *grants* (e.g., sheet-level allowlists in `_schema.md`) are
  the likely mechanism — design carefully against confused-deputy
  problems.
- Transport for non-local agents (OpenClaw sessions on other machines:
  LiveSync replica + local server per machine, or a remote MCP
  endpoint? The Oracle VM could host one — new security surface, needs
  its own review).
- Rate/volume guardrails so a fleet can't flood `Claude/Inbox/`.
