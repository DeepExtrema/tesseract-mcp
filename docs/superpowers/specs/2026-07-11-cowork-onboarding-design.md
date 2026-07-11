# M2: Cowork Onboarding — Design

**Date:** 2026-07-11
**Status:** Spec ready
**Depends on:** M0 (working server), M1 (sheets v1)
**Roadmap:** [2026-07-11-tesseract-roadmap.md](2026-07-11-tesseract-roadmap.md)

## Goal

Claude Cowork becomes the first non-Claude-Code worker on the vault: it
runs the job pipeline (save postings, apply, update statuses) against the
jobs sheet, with zero client-side contract copies. This milestone is
deliberately thin — it's configuration plus verification, and it
establishes the onboarding pattern M7 will generalize.

## What "onboarding an agent" means here

Server-side discovery, not client-side setup:

1. **Register** the tesseract MCP server in the client's config.
2. The agent calls **`onboard`** (orientation + constitution pointer),
   reads **`Claude/README.md`** rules, and discovers sheet contracts via
   **`sheet_schema`**.
3. Everything else is enforced by the server: quarantine, sheet
   validation, filing conventions.

If a client needs special prompts, skills, or copied schemas to behave,
that's a defect in the server's contract surface — fix the contract, not
the client.

## Design

### Registration

- Cowork (Claude Code desktop app) reads the same user-scope MCP
  registration on this machine (`~/.claude.json` `mcpServers.tesseract` —
  verified present). **Open item to verify at rollout:** whether Taimoor's
  Cowork surface (claude.ai Cowork) uses a separate connector config; if
  so, register the same stdio server there via its MCP/connector settings.
  The server entry is identical: the venv exe +
  `TESSERACT_VAULT_PATH=C:\Vaults\Tesseract`.
- `mcp-servers.json` (the curated bundle synced by `mcp_sync`) already
  carries tesseract; extend the manifest sync check to whichever config
  file Cowork actually reads, so drift is caught by `--check` like
  everything else.

### Session conventions for Cowork

No new skills required for the job pipeline: the playbook lives in the
jobs `_schema.md` body (M1) and is served by `sheet_schema("jobs")`. The
expected Cowork loop:

1. `sheet_schema("jobs")` once per session.
2. Found postings → `sheet_upsert` with `status: Saved`.
3. Work the queue → `sheet_query(status eq Saved)`.
4. Applied → upsert `{status: Applied, date_applied, channel,
   resume_version}`.
5. End of session → `log_session` (until M3 automates the nudge).

### Guardrails (unchanged, stated for the record)

Cowork gets exactly the same surface as every MCP client: `Claude/` free,
jobs sheet via validated `sheet_upsert`, everything else confirm-gated.
Job postings it reads are untrusted web content — the sheet's typed
columns and length caps (M1 security section) are the containment.

## Acceptance

Run from Cowork, not Claude Code:

1. `onboard` returns orientation; `sheet_schema("jobs")` returns the
   contract.
2. Cowork saves one real posting → `Saved` row visible in `Tracker.base`.
3. Cowork applies → same row flips to `Applied`, `## Log` line appended,
   `changed` map returned.
4. A second apply-run on the same posting **updates** (no duplicate note).
5. A deliberately malformed write (bad enum) → validation error message
   that Cowork can read and self-correct from.

## Testing

Server-side behavior is fully covered by M1's suite (validation, matching,
quarantine). This milestone adds only the rollout checklist above plus a
session log recording the acceptance run. If Cowork's config location
differs from `~/.claude.json`, add it to `mcp_sync --check` coverage with
a test.

## Explicitly out of scope

- Hermes / OpenClaw / deep-research agents (M7 generalizes this pattern;
  their access may need scoping decisions — read-only tiers, per-sheet
  grants — that Cowork doesn't).
- Any Cowork-side skill authoring.
- Automation of the apply flow itself (Cowork drives applications; this
  milestone only gives it durable memory).
