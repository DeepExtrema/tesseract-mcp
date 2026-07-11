# M3: Discipline Hooks — Design

**Date:** 2026-07-11
**Status:** Spec ready
**Depends on:** recall harness (shipped 2026-07-10); M1 for the digest
follow-ups section; M0 for a working server
**Roadmap:** [2026-07-11-tesseract-roadmap.md](2026-07-11-tesseract-roadmap.md)

## Goal

Make recall-then-log a property of the harness instead of a habit. Today
the vault compounds only when someone remembers to `log_session`; sessions
that forget leave nothing behind. This is usage-layer sub-project 2 (agent
discipline) from the recall-harness decomposition.

## Design

### Component 1: a hook-friendly CLI on the recall module

Hooks run shell commands, not MCP calls. `recall.py` has the bundle
functions but no CLI (verified 2026-07-11). Add one:

```
python -m tesseract_mcp.recall --vault <path> --context [--project <name>] [--budget N]
```

Prints a compact, deterministic context block built from the resume bundle:
active project state (latest sessions, open tasks, recent decisions),
capped at `--budget` characters (default ~2,000 — enough to orient, small
enough to not tax every session). Exit 0 with empty output when the vault
is unreachable — **a broken hook must never block a session.**

### Component 2: SessionStart hook (recall-at-start)

Claude Code `SessionStart` hook invokes the CLI; stdout becomes
`additionalContext`. Project inference: hook passes the workspace folder
name; the CLI maps it to vault project naming (same heuristic
`/resume` uses). Result: every session opens already knowing where the
work left off — no `/resume` invocation required.

### Component 3: Stop-hook nudge (log-at-end)

A `Stop` hook that checks whether the session appears significant (tool
calls happened, files changed) and whether `log_session` was called; if
not, it emits a reminder message asking the agent to file a session log
before finishing. Nudge, not enforcement — hooks cannot force an MCP call,
and hard-blocking Stop on vault availability would hold sessions hostage.
Implementation detail (transcript inspection vs. marker file) belongs to
the plan; the contract is: **zero false blocks, best-effort reminders.**

### Component 4: /digest gains follow-ups + staleness sections

- Follow-ups due (M1 interface): `sheet_query("jobs",
  {next_follow_up: {lte: today}, status: {nin: [Rejected, Ghosted,
  Withdrawn]}})`.
- Discipline meter: sessions in the last 7 days vs. session logs filed
  (from `Claude/Sessions/` timestamps) — makes the habit visible in the
  ritual where Taimoor already looks.

### Component 5: digest scheduling — after the manual week

Standing decision (2026-07-11 rollout): run `/digest` by hand for a week
first, iterate the format, then schedule. When approved: a scheduled
Claude session (Claude Code scheduled routine, or Task Scheduler launching
`claude -p "/digest"`) each morning. The schedule is part of this
milestone's rollout, gated on the manual week's verdict.

### Config placement

Hook definitions live in `~/.claude/settings.json` (user scope — every
project on this machine gets the discipline layer, matching the global MCP
registration). Versioned copies + an installer live in the repo
(`skills/` or `scripts/hooks/` — plan decides), synced additively like
`skill_sync`; agents may run `--check` freely, real installs are
consent-gated (mirrors the skill_sync consent rule).

## Acceptance

1. Fresh Claude Code session in the tesseract repo opens with a context
   block naming the last session and open tasks — with no manual command.
2. A session that edits files and ends without `log_session` shows the
   nudge; a trivial Q&A session does not.
3. Vault offline (rename it temporarily): sessions start normally, no
   context block, no error.
4. `/digest` output includes follow-ups-due rows (seeded fixture) and the
   discipline meter.

## Testing

- CLI: unit tests for the context block (fixture vault) — project
  inference, budget truncation, unreachable-vault empty output.
- Hooks: script-level tests where feasible (invoke the hook script against
  a fake transcript/marker), plus the acceptance checklist run manually —
  hooks execute inside the Claude harness, which unit tests can't fully
  simulate.
- Digest sections: extend `recall_bundle`/skill tests per the recall
  harness suite's pattern.

## Explicitly out of scope

- Hard enforcement of logging (agents can always be interrupted; the
  discipline meter surfaces gaps instead).
- Hooks for non-Claude-Code agents (Cowork hook support differs; revisit
  in M7).
- Auto-committing or auto-writing anything from hooks beyond the context
  injection.
