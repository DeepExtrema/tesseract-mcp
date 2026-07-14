# Librarian Recovery + Failure-Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make extraction failures name their real cause, make search excerpts show note content instead of `---`, add a durable way to retry permanently-benched notes, then run the one-time recovery drain that re-indexes the 265 stranded vault notes.

**Architecture:** Three small, independent changes in `src/tesseract_mcp/` — a stderr summarizer in `extractor.py`, a shared frontmatter-stripping helper in `search.py` consumed by `hybrid.py` and `recall.py`, and a `retry_failures` flag on `indexer.run()` + CLI. Task 4 is operational: use the new flag to drain the live vault's index backlog.

**Tech Stack:** Python 3 (stdlib only for these changes), pytest.

**Spec:** `docs/superpowers/specs/2026-07-12-librarian-recovery-and-failure-visibility-design.md`

## Global Constraints

- Run all commands from the repo root: `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`.
- Use the project venv for every command: `.venv\Scripts\python.exe -m pytest ...` (plain `python` may resolve to the system 3.14 without dev deps).
- Do not change the librarian sweep's behavior — `retry_failures` is CLI-only, default `False`.
- Truncation cap for error messages stays 300 chars (matches the existing `str(e)[:300]` cap in `indexer.run`'s failure entries).
- No new dependencies.
- Task 4 (recovery) touches the LIVE vault `C:\Vaults\Tesseract` and live state in `~/.tesseract-mcp/8175395c1bbf/` — it runs only after Tasks 1–3 are merged and their tests pass, and it uses `TESSERACT_EXTRACTOR=claude` (codex quota is exhausted until 2026-08-10).

---

### Task 1: `_stderr_summary` in extractor.py

The librarian's 07-11 sweep blamed a cosmetic codex skill-load warning because `ExtractorError` keeps the FIRST 300 chars of stderr; the real fatal error (`ERROR: You've hit your usage limit...`) is the LAST line. Summarize stderr as: last line containing ERROR/FATAL, else the tail.

**Files:**
- Modify: `src/tesseract_mcp/extractor.py` (the `_invoke` method, ~line 143, plus a new module-level helper)
- Test: `tests/test_extractor.py`

**Interfaces:**
- Produces: `_stderr_summary(stderr: str | None) -> str` (module-level in `extractor.py`; private, but imported by its test).
- The `ExtractorError` message format stays `"{backend} exited {returncode}: {summary}"` — `indexer.run` and `librarian` consume it as an opaque string, no changes there.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extractor.py` (it already imports `pytest`, `CliExtractor`, `ExtractorError`, and defines `FakeProc` / `make_runner`):

```python
from tesseract_mcp.extractor import _stderr_summary


def test_stderr_summary_prefers_last_error_line():
    noise = ("2026-07-11T12:32:12Z ERROR codex_core::session: failed to "
             "load skill X: invalid name\n") * 3
    tail = "OpenAI Codex v0.130.0\nERROR: You've hit your usage limit.\n"
    assert _stderr_summary(noise + tail) == "ERROR: You've hit your usage limit."


def test_stderr_summary_falls_back_to_tail_when_no_error_line():
    assert _stderr_summary("x" * 400) == "x" * 300


def test_stderr_summary_empty_and_none_return_empty():
    assert _stderr_summary("") == ""
    assert _stderr_summary(None) == ""
    assert _stderr_summary("   \n  ") == ""


def test_nonzero_exit_message_names_last_error_line():
    stderr = ("ERROR cosmetic skill-load noise\n"
              "OpenAI Codex v0.130.0\n"
              "ERROR: usage limit hit")
    runner = make_runner([FakeProc(stdout="", returncode=1, stderr=stderr)])
    with pytest.raises(ExtractorError, match="usage limit hit"):
        CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_extractor.py -v -k stderr_summary`
Expected: FAIL / collection error with `ImportError: cannot import name '_stderr_summary'`

- [ ] **Step 3: Implement `_stderr_summary` and use it in `_invoke`**

In `src/tesseract_mcp/extractor.py`, add `import re` to the imports block, then add this helper above `class CliExtractor` (after the `Extraction` dataclass / `_coerce`):

```python
_ERROR_LINE = re.compile(r"\b(ERROR|FATAL)\b", re.IGNORECASE)


def _stderr_summary(stderr: str | None, cap: int = 300) -> str:
    """The most useful `cap` chars of a failed CLI's stderr.

    CLIs like codex print cosmetic warnings first and the fatal error last,
    so head-truncation blames the wrong cause: prefer the last ERROR/FATAL
    line, fall back to the tail.
    """
    text = (stderr or "").strip()
    if not text:
        return ""
    for line in reversed(text.splitlines()):
        if _ERROR_LINE.search(line):
            return line.strip()[:cap]
    return text[-cap:]
```

Then in `_invoke`, replace:

```python
        if proc.returncode != 0:
            raise ExtractorError(
                f"{self.backend} exited {proc.returncode}: {(proc.stderr or '')[:300]}"
            )
```

with:

```python
        if proc.returncode != 0:
            raise ExtractorError(
                f"{self.backend} exited {proc.returncode}: {_stderr_summary(proc.stderr)}"
            )
```

- [ ] **Step 4: Run the full extractor test file**

Run: `.venv\Scripts\python.exe -m pytest tests/test_extractor.py -v`
Expected: all PASS — including the pre-existing `test_nonzero_exit_raises` (stderr `"boom"` has no ERROR line, so the tail fallback still yields `"boom"`).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/extractor.py tests/test_extractor.py
git commit -m "fix(extractor): summarize stderr by last ERROR line, not head truncation"
```

---

### Task 2: Body-aware excerpts (`search.py` helper + `hybrid.py` fix + `recall.py` refactor)

Semantic-only hits (ranked by vector/RRF, no literal substring in the note) fall back to the first raw file line — the frontmatter `---` delimiter. Hoist frontmatter-stripping into a shared `body_text()` in `search.py`, and make `_excerpt` match and fall back against the body only.

**Files:**
- Modify: `src/tesseract_mcp/search.py` (add `body_text` after `parse_frontmatter`, ~line 32)
- Modify: `src/tesseract_mcp/hybrid.py` (`_excerpt`, lines 48–56; import line 13)
- Modify: `src/tesseract_mcp/recall.py` (`_body_excerpt`, lines 95–101; import line 20)
- Test: `tests/test_search.py`, `tests/test_hybrid.py`, `tests/test_recall.py`

**Interfaces:**
- Produces: `body_text(text: str) -> str` in `search.py` — note content with the leading YAML frontmatter block removed; passthrough when there is no (closed) frontmatter block.
- Consumes: nothing new. `_excerpt(text, rel, query)` keeps its signature; `Hit` unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_search.py`:

```python
from tesseract_mcp.search import body_text


def test_body_text_strips_frontmatter():
    assert body_text("---\ntags: [x]\n---\n\n# T\n\nBody.\n") == "\n\n# T\n\nBody.\n"


def test_body_text_without_frontmatter_is_passthrough():
    assert body_text("# T\n\nBody.\n") == "# T\n\nBody.\n"


def test_body_text_unclosed_frontmatter_is_passthrough():
    assert body_text("---\ntags: [x]\nno closing fence\n") == "---\ntags: [x]\nno closing fence\n"
```

Append to `tests/test_hybrid.py` (it already has `vault`, `vault_dir`, and `FakeSemanticEmbedder`):

```python
from tesseract_mcp.hybrid import _excerpt


FRONTMATTER_NOTE = "---\ntags: [x]\n---\n\n# Title Line\n\nReal content here.\n"


def test_excerpt_semantic_only_hit_returns_first_body_line_not_delimiter():
    assert _excerpt(FRONTMATTER_NOTE, "Notes/N.md", "unrelated-query") == "# Title Line"


def test_excerpt_line_match_never_returns_frontmatter_line():
    text = "---\ntags: [billing]\n---\n\nInvoice for the billing cycle.\n"
    assert _excerpt(text, "Notes/N.md", "billing") == "Invoice for the billing cycle."


def test_excerpt_frontmatter_only_note_returns_empty():
    assert _excerpt("---\ntags: [x]\n---\n", "Notes/N.md", "zzz") == ""


def test_excerpt_title_match_unchanged():
    assert _excerpt(FRONTMATTER_NOTE, "Claude/Sessions/Weekly Review.md", "weekly") == "(title match)"


def test_hybrid_search_semantic_hit_excerpt_is_not_frontmatter(vault, vault_dir):
    (vault_dir / "Contractors.md").write_text(
        "---\ntags: [money]\n---\n\nOutstanding invoices from contractors.\n",
        encoding="utf-8",
    )
    hits = hybrid_search(
        vault, vault.root, FakeSemanticEmbedder(), "who do I owe money to"
    )
    by_path = {h.path: h.excerpt for h in hits}
    assert by_path["Contractors.md"] == "Outstanding invoices from contractors."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_search.py tests/test_hybrid.py -v -k "body_text or excerpt"`
Expected: FAIL — `ImportError: cannot import name 'body_text'` and, once that exists, the `_excerpt` tests fail with `'---'` (or `'tags: [billing]'`) instead of the body line.

- [ ] **Step 3: Implement `body_text` in search.py**

In `src/tesseract_mcp/search.py`, directly after `parse_frontmatter` (line 31), add:

```python
def body_text(text: str) -> str:
    """Note content with the leading YAML frontmatter block removed."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text
```

- [ ] **Step 4: Rewrite `_excerpt` in hybrid.py to be body-aware**

In `src/tesseract_mcp/hybrid.py`, change the import on line 13 to:

```python
from .search import Hit, body_text, iter_candidate_notes
```

and replace the whole `_excerpt` function (lines 48–56) with:

```python
def _excerpt(text: str, rel: str, query: str) -> str:
    stem = rel.rsplit("/", 1)[-1][:-3]
    q = query.lower()
    if q in stem.lower():
        return "(title match)"
    body = body_text(text)
    for line in body.splitlines():
        if q in line.lower():
            return line.strip()
    # Semantic-only hit: no literal match anywhere. First body line beats
    # the old behavior of returning the raw file's first line ("---").
    for line in body.splitlines():
        if line.strip():
            return line.strip()[:120]
    return ""
```

- [ ] **Step 5: Refactor `_body_excerpt` in recall.py onto the shared helper**

In `src/tesseract_mcp/recall.py`, change the import on line 20 to:

```python
from .search import body_text, parse_frontmatter
```

and replace `_body_excerpt` (lines 95–101) with:

```python
def _body_excerpt(text: str, limit: int = 400) -> str:
    """First `limit` chars of the note body, frontmatter stripped."""
    return " ".join(body_text(text).split())[:limit]
```

- [ ] **Step 6: Run the affected test files**

Run: `.venv\Scripts\python.exe -m pytest tests/test_search.py tests/test_hybrid.py tests/test_recall.py -v`
Expected: all PASS (recall's existing excerpt tests prove the refactor is behavior-preserving).

- [ ] **Step 7: Commit**

```bash
git add src/tesseract_mcp/search.py src/tesseract_mcp/hybrid.py src/tesseract_mcp/recall.py tests/test_search.py tests/test_hybrid.py
git commit -m "fix(search): body-aware excerpts - semantic hits no longer show the frontmatter delimiter"
```

---

### Task 3: `retry_failures` flag on the indexer

Once a note hits `MAX_ATTEMPTS=3` failures it is skipped forever; the only revival paths are hand-editing `manifest.json` or `--force` (re-extracts the whole vault). Add a surgical escape hatch: clear the failure ledger so benched notes re-enter the normal pending flow, leaving tracked-and-unchanged notes alone.

**Files:**
- Modify: `src/tesseract_mcp/indexer.py` (`run()` signature ~line 89, `main()` argparse ~line 159)
- Test: `tests/test_indexer.py`

**Interfaces:**
- Produces: `indexer.run(vault, extractor, batch=25, force=False, ignore=DEFAULT_IGNORE, precompute_embeddings=True, retry_failures=False) -> dict` (same counts dict as today). CLI flag `--retry-failures`.
- Consumes: `FakeExtractor` from `tests/test_indexer.py` (existing, takes `fail=` set of paths).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indexer.py`:

```python
def test_run_retry_failures_reattempts_maxed_out_notes(vault):
    for _ in range(indexer.MAX_ATTEMPTS):
        indexer.run(vault, FakeExtractor(fail={"Daily.md"}))
    benched = FakeExtractor()
    indexer.run(vault, benched)
    assert "Daily.md" not in benched.calls  # attempts exhausted: benched

    retried = FakeExtractor()
    counts = indexer.run(vault, retried, retry_failures=True)
    assert "Daily.md" in retried.calls
    assert counts["failed"] == 0
    assert "Daily.md" not in indexer.load_manifest(vault.root)["failures"]


def test_run_retry_failures_skips_unchanged_tracked_notes(vault):
    indexer.run(vault, FakeExtractor())  # index everything cleanly
    fx = FakeExtractor()
    counts = indexer.run(vault, fx, retry_failures=True)
    assert counts["processed"] == 0 and fx.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_indexer.py -v -k retry_failures`
Expected: FAIL with `TypeError: run() got an unexpected keyword argument 'retry_failures'`

- [ ] **Step 3: Implement the parameter and CLI flag**

In `src/tesseract_mcp/indexer.py`, change `run()`'s signature and the first lines of its body:

```python
def run(
    vault: Vault,
    extractor,
    batch: int = DEFAULT_BATCH,
    force: bool = False,
    ignore: tuple[str, ...] = DEFAULT_IGNORE,
    precompute_embeddings: bool = True,
    retry_failures: bool = False,
) -> dict:
    manifest = load_manifest(vault.root)
    if retry_failures:
        # Re-arm notes benched at MAX_ATTEMPTS (e.g. after a quota outage):
        # cleared entries fall through the normal hash-diff pending logic.
        manifest["failures"].clear()
    current = scan_notes(vault, ignore)
```

(The rest of `run()` is unchanged.)

In `main()`, add after the `--force` argument:

```python
    parser.add_argument(
        "--retry-failures",
        action="store_true",
        help="Clear the failure ledger so notes benched at max attempts are retried",
    )
```

and pass it through in the `run(...)` call:

```python
    counts = run(
        Vault(args.vault),
        extraction_extractor(backend=args.backend),
        batch=args.batch,
        force=args.force,
        retry_failures=args.retry_failures,
    )
```

- [ ] **Step 4: Run the indexer tests, then the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_indexer.py -v`
Expected: all PASS
Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all PASS (no regressions anywhere)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/indexer.py tests/test_indexer.py
git commit -m "feat(indexer): --retry-failures re-arms notes benched at max attempts"
```

---

### Task 4: One-time recovery drain of the live vault (operational)

Re-index the 265 stranded notes (75 benched by transient `claude exited 1` failures + 190 never attempted after the 07-11 drain crashed on the since-fixed LiveSync lock). **Live-vault operation — run only after Tasks 1–3 are committed and green.** No code changes, no commit.

**Files:**
- None created or modified in the repo. Writes go to the live vault's `Claude/Graph/` and `~/.tesseract-mcp/8175395c1bbf/` (manifest, graph.db, embeddings).

**Interfaces:**
- Consumes: `--retry-failures` from Task 3.

- [ ] **Step 1: First drain call with the failure reset**

Run (PowerShell, repo root):

```powershell
$env:TESSERACT_EXTRACTOR = 'claude'
.\.venv\Scripts\python.exe -m tesseract_mcp.indexer C:\Vaults\Tesseract --retry-failures
```

Expected: JSON counts with `"processed": 25` (one batch), `"failed"` 0 or small, and `"remaining"` ≈ 240 (265 pending minus this batch, ± notes edited since).

- [ ] **Step 2: Drain remaining batches**

Repeat until `"remaining": 0`:

```powershell
.\.venv\Scripts\python.exe -m tesseract_mcp.indexer C:\Vaults\Tesseract
```

Expected: `remaining` decreases by ~25 per call; each call takes minutes (one `claude -p` call per note). If a batch reports non-zero `failed`, note the count and keep draining — freshly failing notes get 3 attempts before benching; investigate only if the same notes still fail at the end (their manifest failure entries will now contain the real error thanks to Task 1).

- [ ] **Step 3: Verify recovery**

Run:

```powershell
.\.venv\Scripts\python.exe -c "
import json
from tesseract_mcp.vault import Vault
from tesseract_mcp import indexer, librarian
v = Vault(r'C:\Vaults\Tesseract')
m = indexer.load_manifest(v.root)
print('failures:', len(m['failures']))
drift = librarian.check_manifest_drift(v)
print('untracked:', len(drift['present_but_untracked']))
print('deleted_but_tracked:', len(drift['deleted_but_tracked']))
"
```

Expected: `failures: 0` (or a handful with real error messages), `untracked:` 0–5 (notes edited during the drain are fine — the next sweep catches them), `deleted_but_tracked: 0`.

- [ ] **Step 4: Verify the graph hop heals**

Via the tesseract MCP (or any client): call `context_bundle` with a query that hits recent session notes, e.g. `"recall harness"`. 
Expected: `entities` and `related_notes` are non-empty, and every hit `excerpt` shows body text (never `---`).

---

## Self-Review

- **Spec coverage:** Spec §1 (retry flag) → Task 3; §2 (body-aware excerpt + shared helper + recall refactor) → Task 2; §3 (stderr summary) → Task 1; §4 (runbook + verification) → Task 4; spec's testing section → each task's test steps. No gaps.
- **Placeholder scan:** none — every code step shows the full code, every command has expected output.
- **Type consistency:** `body_text(text: str) -> str` used identically in Tasks 2's three call sites; `_stderr_summary(stderr, cap=300)` matches its tests; `retry_failures` keyword matches CLI wiring and tests.
