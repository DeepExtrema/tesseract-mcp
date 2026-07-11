# Structured Sheets — Design

**Date:** 2026-07-11
**Status:** Approved (brainstormed with Taimoor 2026-07-11; supersedes the same-day draft)
**Depends on:** write quarantine (shipped), server eager-import fix (queued from the 2026-07-11 audit — must ship first)

## Context

The vault already contains a working structured-data pattern: `Job Search/Tracker.base`
(Obsidian Bases, core plugin enabled) renders ~60 application notes whose typed
frontmatter (`company`, `role`, `status`, `date_applied`, `channel`,
`sponsorship_required`, `resume_version`, ...) acts as columns, with computed
formulas (freshness at apply, days since contact, follow-up due) and four views
(All Applications, Follow-ups Due, Active Pipeline, Sponsorship Flagged).

What is missing is the **agent contract**. The existing notes are mixed-origin
(human + agents) and the frontmatter has drifted. Any agent (the target user is
Claude Cowork applying to jobs) must today hand-write frontmatter from folklore:

- No schema enforcement — `status: Applied` vs `stage: applied` drift silently
  breaks the Base views.
- No upsert/dedupe — two sessions applying to the same role create two notes.
- Write friction — `Job Search/` is outside `Claude/`, so every write needs the
  `confirm_outside_claude` escape hatch.
- No typed query — `query_notes` is equality-only; "follow-ups due before
  today" is impossible.

This is usage-layer work: sheets are the **records layer** of the vault
(memory → knowledge → records → rituals → workers). Jobs is the first sheet;
the mechanism is generic.

## Decisions (brainstormed 2026-07-11, Taimoor in the loop)

1. **Scope: generic mechanism, one sheet.** Registry + validation + upsert +
   typed query work for any folder; jobs is the only sheet registered in v1.
2. **Approach A — schema-as-write-grant.** The sheet stays in `Job Search/`;
   a human-placed `_schema.md` grants scoped, validated agent write access.
   (Rejected: moving data under `Claude/Sheets/` — surrenders the ownership
   boundary and breaks `Tracker.base` paths. Rejected: skill-only contract —
   enforcement by prompt is the drift disease itself, and `query_notes`
   cannot express date comparisons.)
3. **Posting = row.** Row identity is company + role + posting identity
   (`req_id`/`job_link`) when present, company + role as fallback. Reapplying
   to a new posting is a new row.
4. **Queue + record.** The sheet holds the whole pipeline, starting at
   `Saved` (discovered, not yet applied). "Work the queue" =
   `sheet_query(status eq Saved)`.
5. **Reject undeclared fields.** Validation failure, not silent drift.
   Standard metadata (`created`, `agent`, `project`, `tags`) is always allowed.
6. **Status changes are journaled in the note body** under a `## Log` heading
   (decision taken during gap review; see Body policy).

## Architecture

### Write classes

The constitution gains a `## Sheets` section documenting a third write class;
`vault.py` enforces all three:

| Write class | Rule |
|---|---|
| `Claude/` | agents write freely (unchanged) |
| Sheet folders (`_schema.md` present) | writes allowed **only** via `sheet_upsert`, schema-validated |
| Everything else | `confirm_outside_claude=True` on explicit user request only (unchanged) |

A folder outside `Claude/` is a registered sheet **iff it contains
`_schema.md`**. There is no separate registry to drift. Agents cannot write
outside `Claude/`, therefore cannot plant a `_schema.md` and self-grant
access; only the human blesses a folder. Raw `write_note` into a sheet folder
stays confirm-gated — `sheet_upsert` is the only agent door, and validation is
the safety story. `Tracker.base` is never written by the server; it remains
the human view.

Sheet membership is **direct children only** (v1): rows are the `*.md` files
directly inside the sheet folder, excluding `_schema.md`. Subfolders are not
scanned (keeps row identity unambiguous; recursive sheets are deferred).

### The schema file

`_schema.md` is an ordinary note: frontmatter defines the contract, body is
prose filing instructions agents are expected to read (returned verbatim by
`sheet_schema`). The body also carries the agent playbook (below).

## Jobs schema (v1)

```yaml
sheet: jobs
filename: "{company} - {role}"
columns:
  company:              {type: string, required: true, max_length: 120}
  role:                 {type: string, required: true, max_length: 160}
  req_id:               {type: string, max_length: 80}
  status:               {type: enum, required: true, values:
                         [Saved, Applied, OA, Interview, Offer, Rejected, Ghosted, Withdrawn]}
  date_applied:         {type: date}
  job_posted_date:      {type: date}
  channel:              {type: string, max_length: 80}
  location:             {type: string, max_length: 120}
  sponsorship_required: {type: bool}
  resume_version:       {type: string, max_length: 80}
  job_link:             {type: url, max_length: 500}
  last_contact:         {type: date}
  next_follow_up:       {type: date}
```

Notes on judgment calls:

- `date_applied` is optional because `Saved` rows don't have one. The filing
  instructions tell agents to set it when flipping to `Applied`.
- No status-transition constraints: real pipelines go backwards
  (Ghosted → Interview happens). Any enum value may follow any other; the
  `## Log` journal preserves the history.
- Column types in v1: `string`, `enum`, `date`, `bool`, `url`, `number`.
  Every string-ish column takes an optional `max_length` (input hygiene, see
  Security).
- Standard metadata allowed on every sheet: `created` (server-stamped on
  create, never patched), `agent` (server-stamped with the calling agent's
  name on every upsert), `project`, `tags`.

## Row identity and matching

All matching is over frontmatter — filenames are display only (existing
"Adobe - ... R160473" filenames need no rename).

**Normalization (applies to both sides of every comparison):**

- Strings: trim, collapse internal whitespace runs to one space,
  case-insensitive comparison. Stored values keep their original casing.
- `job_link`: parsed as URL; scheme and host lowercased; default ports,
  trailing slashes, and fragments dropped; known tracking params removed
  (`utm_*`, `ref`, `src`, `gh_src`, `lever-origin`); remaining query params
  kept sorted. Two links equal ⇔ normalized forms equal.
- Dates: ISO `YYYY-MM-DD` only; anything else is a validation error. "Today"
  is the server's local date; agents compute their own relative dates.

**Matching algorithm.** Candidates = rows where `company` + `role` match.

1. Incoming carries posting identity (`req_id`, or `job_link` if no req_id):
   - candidate with the same identity value → **update**;
   - no candidate has any posting identity and exactly one candidate exists →
     **update it and backfill** the identity field;
   - otherwise → **create** a new row.
2. Incoming carries no posting identity:
   - exactly one candidate → **update**;
   - zero → **create**;
   - multiple → **error** listing candidate paths; agent must supply `req_id`
     or `job_link` to disambiguate.

**Filename rendering.** Template fields are sanitized for Windows/NTFS:
`\/:*?"<>|` and control chars replaced with `-`, runs collapsed, trimmed,
capped at 120 chars total (before `.md`). Collision with a *different*
identity → numeric suffix (session-log convention).

## MCP tools (server 21 → 24, new module `sheets.py`)

### `sheet_upsert(sheet, fields, body=None)`

Validate `fields` against the schema (types, enums, max_length, undeclared →
reject); run the matching algorithm; then either patch the matched note's
frontmatter (only the passed fields; all other frontmatter and the body
untouched — sole exception: the `## Log` status append, see Body policy) or
create a new note from the filename template. Returns:

```json
{"result": "created" | "updated", "path": "...",
 "changed": {"status": {"from": "Saved", "to": "Applied"}}}
```

The `changed` map (old → new per field actually modified) lets the agent
verify its own write and gives the human an audit line in session logs.
No-op upserts (nothing changed) return `"updated"` with empty `changed` and
do not touch the file (LiveSync noise avoidance).

### `sheet_query(sheet, filters, sort=None, limit=50)`

Typed operators over typed columns: `eq ne lt lte gt gte contains missing in
nin`. Filters compose with AND (OR is deferred; agents can merge two calls).
`contains` is substring on strings; ordering operators require `date` or
`number` columns. `sort` = `{"by": column, "dir": "asc"|"desc"}`, missing
values last. Returns row objects: `path` + full frontmatter (no body — bodies
are fetched via `read_note` when the agent needs the story). `_schema.md` is
never a row.

Example — follow-ups due: `filters = {"next_follow_up": {"lte": "2026-07-11"},
"status": {"nin": ["Rejected", "Ghosted", "Withdrawn"]}}`.

### `sheet_schema(sheet=None)`

No arg → list all registered sheets (name, folder, row count). With arg →
columns with types/required/enums, the filename template, and the
filing-instructions body verbatim. Contract discovery for any agent with zero
out-of-band knowledge — this is what makes Cowork onboarding "register the
MCP server, done."

## Body policy and the status log

The note body belongs to the story (recruiter threads, interview notes) and
`sheet_upsert` never edits it, with **one strictly-scoped exception**: when an
upsert changes `status`, the server appends one line under a `## Log` heading
(creating the heading at the end of the body if absent):

```
- 2026-07-11 status: Saved → Applied (agent: cowork)
```

Append-only, one line per status change, nothing else in the body is ever
touched. This is the audit trail that makes "any enum value may follow any
other" safe. `body` may only be supplied on create; if omitted, new rows are
seeded with a `## Log` heading and a creation line.

## Caretaker and retrieval integration

- **Organizer/Mover MUST exclude sheet folders.** The embedding neighbor-vote
  auto-filer would otherwise try to move rows out of the sheet (or file new
  notes into it). Rule: any folder containing `_schema.md` is invisible to the
  organizer, both as a source and as a destination. Same exclusion for
  `_schema.md` itself everywhere.
- **Indexer/hybrid search include rows** (desirable: `/recall` cites
  `[[Adobe - 2026 Intern Software Engineer Intern R160473]]`).
- **Graph extractor includes rows** (companies become `Claude/Graph/
  Organizations/` entities — the job pipeline feeds the knowledge graph).
- **Librarian health check** gains a sheets line: rows failing validation
  (e.g., human hand-edits introducing drift) are reported in `Claude/
  Librarian.md`, never auto-fixed.
- `/digest` gains a follow-ups-due section via `sheet_query` (skill edit,
  separate task in the plan).

## Concurrency and sync

- Note-per-row means agents updating *different* rows never conflict, on any
  machine.
- Same-row, same-machine races (two Claude sessions): writes go through the
  existing atomic `Vault.write` (tmp + `os.replace`); last write wins at
  frontmatter-patch granularity. Accepted for v1 (single human operator).
- Same-row, cross-machine: LiveSync's normal conflict resolution applies;
  markdown diffs are human-legible. Accepted for v1; the sheet write journal
  (deferred) is the eventual belt-and-suspenders.

## Security and input hygiene

Job postings are **untrusted web content** that Cowork copies into fields.

- Field values are data, never instructions: length caps (`max_length`) stop
  junk dumps; enum/date/bool/url types can't carry prose at all.
- The only free-text surfaces are `company`/`role`/`channel`/`location`-class
  strings (capped) and the body (agent-authored story, same trust level as
  any session note).
- `sheet_upsert` path handling goes through the existing `Vault.resolve`
  escape guard; the sheet name maps to a folder server-side — agents never
  pass raw paths.
- `_schema.md` creation/edit by agents is blocked by the quarantine itself
  (it lives outside `Claude/`); tests assert this explicitly.

## Error handling

Every error is agent-actionable (the consumer is Cowork self-correcting):

- Validation failure names the field, expected type/enum/max_length, and the
  got-value.
- Ambiguous match lists candidate paths and the disambiguating fields.
- Missing/malformed `_schema.md` (bad YAML, unknown column type, missing
  `sheet:`/`filename:`) → all sheet tools refuse that folder with the parse
  error; nothing writes.
- Unknown sheet name → error listing registered sheets.
- Filename collision with different identity → numeric suffix (not an error).
- Frontmatter patch preserves untouched fields and the body byte-identically
  (round-trip test) — except the `## Log` status append (Body policy), which
  is itself covered by append-only tests.

## Migration

CLI: `python -m tesseract_mcp.sheets --check <vault>` — validates every row of
every registered sheet **before go-live**:

- Reports per-note drift: unknown fields, bad enum values, missing required
  fields, malformed dates/urls.
- Reports duplicate identities (rows that would collide under the matching
  algorithm) — the pre-existing dupes surface here, human resolves.
- **Report-only.** It never rewrites notes. Fixes happen by hand, by
  extending the schema (the check tells us what's actually in the wild — if
  half the notes carry `recruiter`, that's a column candidate), or by
  accepting listed drift.
- Go-live requires a clean check.

## Rollout (consent-gated, recall-harness pattern)

1. Eager-import fix ships and the server restarts (prerequisite — sheets
   tools are I/O-only but ride the same release).
2. Taimoor approves `_schema.md` content (columns above + filing
   instructions).
3. Constitution gains `## Sheets`; conventions installer updated.
4. `--check` run against the live vault; drift resolved to clean.
5. tesseract registered in **Cowork's MCP config** (today only Claude Code
   has it).
6. Acceptance: Cowork saves one real posting (`Saved` row appears in
   `Tracker.base`), applies (`status` flips to `Applied`, log line appended),
   and a second apply-run **updates** instead of duplicating.
7. Optional, human-applied: a "Queue" view (status = Saved) added to
   `Tracker.base` — proposed as a snippet, never written by the server.

## Cowork playbook (goes in `_schema.md` body)

1. `sheet_schema("jobs")` once per session — read the contract.
2. Found a posting → `sheet_upsert("jobs", {company, role, req_id?, job_link,
   status: "Saved", job_posted_date?, location?, sponsorship_required?})`.
3. Working the queue → `sheet_query("jobs", {"status": {"eq": "Saved"}})`.
4. Applied → upsert the same identity with `{status: "Applied", date_applied,
   channel, resume_version}`.
5. Heard back → upsert `{status: "OA"|"Interview"|..., last_contact,
   next_follow_up}`.
6. Story (recruiter emails, interview notes) → `read_note` the row, then
   append via normal note flow only when asked; the frontmatter is the
   record, the body is the narrative.
7. One note per posting; never delete rows — `Withdrawn`/`Rejected` are
   states, deletion is the human's.

## Testing (TDD, per repo convention)

- **Quarantine matrix:** upsert allowed only in schema'd folders; raw
  `write_note` still confirm-gated there; agent writing `_schema.md`
  anywhere outside `Claude/` blocked; path-escape attempts fail.
- **Matching table:** the full algorithm as parameterized cases (req_id hit,
  backfill, no-identity single/zero/multi candidate, job_link normalization
  equivalences, case/whitespace variants).
- **Validation:** every type, enum membership, max_length, undeclared field,
  missing required, standard-metadata allowance.
- **Body preservation:** byte-for-byte round-trip on patch; `## Log`
  append-only semantics; heading created when absent; non-status upserts
  never touch the body.
- **Query:** each operator, AND composition, sort with missing values,
  `_schema.md` exclusion, direct-children-only scope.
- **Caretaker exclusion:** organizer skips sheet folders as source and
  destination.
- **Migration checker:** fixture corpus with seeded drift (unknown fields,
  bad enums, duplicate identities) produces the expected report.
- **Filename rendering:** sanitization, length cap, collision suffix.

## Performance

Row scan is O(rows) file reads per call. At 60–500 rows this is well under a
second (the 2026-07-11 audit measured a 359-file full-vault scan at 0.05s
warm). No index, no cache, no invalidation bugs. Revisit only if a sheet
passes ~5k rows.

## Schema evolution

- **Add a column / enum value:** edit `_schema.md`, re-run `--check`. Old
  rows missing the new optional column are fine (`missing` operator queries
  them).
- **Rename/retype a column:** out of scope for the server; do it as a manual
  migration (edit notes + schema together, `--check` until clean).
- The schema file is human-owned; agents proposing schema changes do so in
  prose (session log / task), never by editing the file.

## Deferred (explicitly out of v1)

- Sheet write journal (reversibility beyond LiveSync + `## Log`).
- OR in query filters; recursive sheet folders.
- More sheets (projects, subscriptions, contacts, experiments) — zero code
  once v1 lands; each is one `_schema.md`.
- Project folders living in the vault (records layer, larger unit — own
  spec).
- Any-agent onboarding beyond Cowork (Hermes/OpenClaw — usage-layer
  sub-project 3).
- Automatic `Tracker.base` edits of any kind.
