# Librarian recovery + failure-visibility fixes

Date: 2026-07-12
Status: approved
Origin: external MCP test run (2026-07-12) surfaced five symptoms; investigation
traced them to two operational root causes and one code bug. This spec covers the
three approved remediation items.

## Background

The 2026-07-11 06:32 librarian sweep reported 765 index failures and a
consolidate error blaming a codex skill-loading bug. Investigation findings:

- The real fatal error was the codex ChatGPT quota being exhausted until
  2026-08-10 (`ERROR: You've hit your usage limit...`, last line of stderr).
  `extractor.py` truncates stderr to the **first** 300 chars, which only shows
  codex's cosmetic skill-load noise — so the librarian report named the wrong
  cause. 765 = 255 notes x 3 attempts (`MAX_ATTEMPTS`).
- The 265-file `manifest_drift.present_but_untracked` is two cohorts:
  190 notes stranded when the 07-11 claude-backend force-reindex drain crashed
  at 22:18 on the `WinError 5` `os.replace` LiveSync lock — three minutes before
  the retry fix (aa35bff) landed; the drain was never re-run. Plus 75 notes
  benched at `attempts=3` from transient `claude exited 1` failures
  (`claude -p --model haiku` verified working now).
- Once a note hits `MAX_ATTEMPTS=3` it is skipped forever; the only revival
  paths today are hand-editing `manifest.json` or `--force` (re-extracts the
  whole vault).
- `search_brain`/`context_bundle` excerpts return `"---"` for semantic-only
  hits: `_excerpt` in `hybrid.py` falls back to the first raw file line, which
  is the frontmatter delimiter. (An earlier draft also claimed a `"\\"` path
  separator bug in the title match; plan-time verification showed line 49
  already splits on `/` — no fix needed there.)
- `context_bundle`'s empty `entities`/`related_notes` is NOT a bug: the hit
  notes are among the 265 unindexed, so they have no mentions in graph.db.
  Heals when the index drains.
- Manifest drift does NOT affect `search_brain` full-text/vector recall:
  `hybrid_search` scans the vault live and embeddings are computed
  independently of the extraction manifest.

## Scope

1. `--retry-failures` indexer flag (durable escape hatch).
2. Body-aware `_excerpt` fix in `hybrid.py`.
3. Smart stderr summary in `ExtractorError`.
4. One-time recovery runbook using (1) after (1)-(3) land.

Out of scope: codex plugin-cache cleanup (one-line ad-hoc delete), librarian
auto-retry policy for aged failures (deferred — can burn quota re-failing
permanently broken notes), richer excerpt windows/highlighting.

## Design

### 1. `--retry-failures` (indexer.py)

- `indexer.run()` gains `retry_failures: bool = False`.
- When set, clear every entry in `manifest["failures"]` before computing
  `pending`. The cleared notes then flow through the normal hash-diff logic
  (untracked or hash-mismatched -> pending) and are re-extracted by the
  regular batch drain.
- Unlike `--force`, tracked-and-unchanged notes are untouched: only benched
  and never-attempted notes get work.
- CLI: `python -m tesseract_mcp.indexer <vault> --retry-failures` (composable
  with `--batch`, `--backend`).
- Librarian sweep behavior is unchanged.
- Empty failures dict: no-op, not an error.

### 2. Body-aware `_excerpt` (hybrid.py)

- Hoist frontmatter-stripping into a shared helper (e.g. `body_text(text)`)
  in `search.py` next to `parse_frontmatter`; `recall.py._body_excerpt`
  refactors to use it.
- `_excerpt` becomes:
  1. Strip frontmatter once.
  2. Title match: `rel.rsplit("/", 1)[-1][:-3]` (unchanged — already correct)
     -> `"(title match)"` as today.
  3. Line match against **body** lines only (frontmatter lines can no longer
     be returned as excerpts).
  4. Fallback: first non-empty **body** line, truncated to 120 chars — never
     the `---` delimiter.
- Empty/frontmatter-only notes return `""` (as today).

### 3. Smart stderr summary (extractor.py)

- Replace `(proc.stderr or '')[:300]` in the non-zero-exit branch with
  `_stderr_summary(proc.stderr)`:
  - Scan lines for the **last** one matching `ERROR` or `FATAL`
    (case-insensitive); if found, return it truncated to 300 chars.
  - Otherwise return the last 300 chars of stderr.
  - Empty/None stderr returns `""` (same terse message as today).
- Result: manifest failure entries and librarian reports name the actual
  fatal error (e.g. the quota message) instead of leading noise.

### 4. Recovery runbook (one-time, after 1-3 land)

```
set TESSERACT_EXTRACTOR=claude
python -m tesseract_mcp.indexer C:\Vaults\Tesseract --retry-failures   # first call
python -m tesseract_mcp.indexer C:\Vaults\Tesseract                    # repeat until remaining: 0
```

- The `os.replace` retry (aa35bff) is already in `vault.py`, so the
  LiveSync-lock crash that stranded the previous drain should not recur.
- Verification: `librarian_status` shows `manifest_drift` -> 0 (or near it)
  and no failure entries; `context_bundle` on a recent-session query returns
  non-empty `entities`/`related_notes`; excerpts show body text.

## Testing

- `--retry-failures`: maxed failure entries are cleared and those notes
  re-pend; unchanged tracked notes stay skipped; empty failures dict no-ops.
- `_excerpt`: semantic-only hit on a frontmatter note returns the first body
  line, not `---`; title match works with `/`-separated rels; line match
  never returns a frontmatter line; empty note returns `""`.
- `_stderr_summary`: picks the last ERROR line from noise-first stderr;
  falls back to tail when no ERROR line; empty stderr -> `""`.
- Existing suite in `tests/` stays green.
