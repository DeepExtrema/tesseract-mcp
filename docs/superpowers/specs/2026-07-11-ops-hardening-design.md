# M0: Ops Hardening — Design

**Date:** 2026-07-11
**Status:** Spec ready (grounded in the 2026-07-11 full-system audit)
**Depends on:** nothing — this unblocks everything else
**Roadmap:** [2026-07-11-tesseract-roadmap.md](2026-07-11-tesseract-roadmap.md)

## Context: what the audit found

Code health is clean (366 tests, eval success@10 0.94 / MRR 0.89), but the
live deployment has one bug and two never-turned-on gaps:

1. **`search_brain`/`context_bundle` time out from every MCP client.**
   Root-caused and reproduced: on Python 3.14 + Windows + mcp SDK 1.28.1,
   the first import of a heavy C-extension chain (numpy,
   sentence_transformers/torch) inside a FastMCP tool worker thread stalls
   until the *next* message arrives on stdin. Wake-up probe: a search sat
   60s undispatched, then executed in 4s the instant a second request
   landed. Trigger in our code: the lazy import in
   `SentenceTransformerEmbedder.__init__` (embeddings.py:29). Search itself
   is fast (7.6s cold standalone, correct results).
2. **The caretakers have never run against the live vault.** The state dir
   for `C:\Vaults\Tesseract` (`~/.tesseract-mcp/8175395c1bbf/`) contains
   only `fallback_embeddings.json` — no `graph.db`, no
   `librarian_state.json`. No scheduled task exists (Task Scheduler's
   "IndexerAutomaticMaintenance" is a Windows built-in, not ours).
   `graph_stats` errors with "Graph cache not built yet".
3. **Stale branches.** `master` is 135 commits behind
   `codex/architecture-roadmap` with zero unique commits;
   `feat/search-eval-harness` (local) duplicates merged eval work (roadmap
   has it plus fixes d2ef270, 79ffdcb).

## Fix 1: eager-load the embedding stack at server startup

**Change:** in `server.py`'s `main()`, before `mcp.run()`, construct the
embedder eagerly: `_get_embedder()`. This forces
`import sentence_transformers` (and numpy/torch) plus the model load
(~4-6s) onto the **main thread before the event loop starts**. First
`search_brain` then runs in single-digit seconds.

Decisions:

- Warm the *model*, not just the import — model construction is also
  in-thread work today and costs the same stall risk.
- No env-var escape hatch (YAGNI). Tests import the module, not `main()`,
  so test startup is unaffected.
- Startup cost ~6s is acceptable: MCP initialize/initialized handshake is
  independent of tool readiness, and clients tolerate slow starts far
  better than mid-session timeouts.
- **New repo rule (AGENTS.md + ARCHITECTURE.md):** never lazy-import
  C-extension chains inside MCP tool bodies; eager-import at startup in the
  main thread. Cite the audit session log.

Out of scope: fixing the SDK dispatch behavior upstream (worth a minimal
repro + issue against modelcontextprotocol/python-sdk later; not blocking).

**Verification:** probe the built exe over stdio (the audit's wake-up probe
script, single request, no wake-up message): `search_brain` responds < 15s
cold, < 2s warm. Then from a fresh Claude session: `search_brain`,
`context_bundle`, `related_notes` all return.

Operational follow-through: `Stop-Process` all running `tesseract-mcp.exe`
after installing (sessions respawn them; they hold the old code otherwise —
lesson from 2026-07-08).

## Fix 2: first Librarian sweep, then schedule it

Order is consent-gated (standing decision: first live sweep must be
dry-run):

1. `python -m tesseract_mcp.librarian C:\Vaults\Tesseract --dry-run` —
   review the printed report with Taimoor: what would be indexed, which
   notes the organizer would move (neighbor-vote ≥ 0.7), consolidation
   proposals, health items.
2. Approve → real sweep. Verify `librarian_status` shows the run and
   `Claude/Librarian.md` has the report. Any organizer move that looks
   wrong: `undo_move` (journaled).
3. Schedule: Windows Task Scheduler entry (name `tesseract-librarian`),
   daily at a quiet hour, action = the venv's python with the librarian
   module and vault path, output appended to
   `~/.tesseract-mcp/librarian-task.log`. Created via `schtasks` (exact
   command in the plan); removal documented alongside.

Note: the graph-extraction step inside the sweep uses the configured CLI
extractor backend (`TESSERACT_EXTRACTOR`, default codex). The scheduled
task must inherit that env var or set it explicitly.

## Fix 3: build the live graph

`index_brain` (or the sweep's index phase) against the live vault to
populate `graph.db` for state dir `8175395c1bbf`. First run extracts
entities for ~359 notes via the LLM extractor — this is the long pole
(minutes to hours depending on backend quota); run it as the dry-run/sweep
step 2, not inside an MCP call. Verify: `graph_stats` returns entity/note
counts; `related_notes` works on a known note; the Obsidian graph shows
`Claude/Graph/` entities.

## Fix 4: branch cleanup

- Delete local `feat/search-eval-harness` (superseded duplicate — verified
  by patch-diff against roadmap).
- Fast-forward `master` (local + origin) to `codex/architecture-roadmap`'s
  tip so the default-looking branch is never 135 commits stale. Keep
  `codex/architecture-roadmap` as the working default (renaming to `main`
  is optional and deferred — origin/HEAD already points at roadmap).
- `origin/feat/recall-harness`: deletion previously not approved — ask once
  during rollout, delete on yes, leave on no.

## Acceptance (the whole milestone)

From a fresh Claude Code session, in one sitting: `search_brain` returns
ranked hits in seconds; `context_bundle` returns hits + entities;
`graph_stats` returns counts; `librarian_status` shows a completed sweep
timestamp; `git branch -a -v` shows no stale branches; the scheduled task
exists and its first unattended run leaves a fresh report in
`Claude/Librarian.md`.

## Testing

- Regression test for Fix 1: a test that asserts `server.main`'s startup
  path references the embedder warm-up (structural), plus the stdio probe
  script checked into `scripts/` as `probe_server.py` for manual/CI use
  against the built exe (it caught the bug; keep the instrument).
- Fixes 2–4 are operational; their verification is the acceptance list
  above, recorded in the session log.
