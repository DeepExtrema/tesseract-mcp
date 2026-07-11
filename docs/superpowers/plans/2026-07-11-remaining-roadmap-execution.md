# Remaining Roadmap Execution Implementation Plan (M0 tail → M1 → M2 → M3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish M0 acceptance (verify backlog extraction, prove the scheduled sweep, close the books), then ship M1 structured sheets (the jobs tracker agent contract), M2 Cowork onboarding, and M3 discipline hooks.

**Architecture:** Phase A is operational close-out of M0 (no new code). Phase B adds one new module `sheets.py` (schema-as-write-grant records layer: validation, deterministic upsert, typed query) plus three MCP tools and an organizer exclusion. Phase C is configuration + acceptance (Cowork). Phase D adds a recall CLI and Claude Code hooks so every session recalls-then-logs. Specs: `docs/superpowers/specs/2026-07-11-ops-hardening-design.md`, `...-structured-sheets-design.md`, `...-cowork-onboarding-design.md`, `...-discipline-hooks-design.md`.

**Tech Stack:** Python 3.14 (repo venv `.venv`), pytest, FastMCP, PyYAML, Windows Task Scheduler, Claude Code hooks.

## Global Constraints

- Working dir `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`; branch `codex/architecture-roadmap`.
- Tests: `.venv\Scripts\python -m pytest -q` full suite before each commit; focused file runs while iterating. In Git Bash use `./.venv/Scripts/python.exe`.
- Live vault `C:\Vaults\Tesseract`. Steps marked **STOP (consent)** require Taimoor's explicit go in chat. Live-vault writes happen only in consent-gated steps and `log_session`.
- Quarantine invariants (must survive every task): agents write freely only under `Claude/`; sheet folders (containing `_schema.md`) are writable **only** via `sheet_upsert`; everything else needs `confirm_outside_claude=True`. `Tracker.base` is never written by server code.
- Never lazy-import C-extension chains inside MCP tool bodies (AGENTS.md rule).
- Extractor backend is `claude` until codex quota returns 2026-08-10 (`scripts/librarian-task.cmd` pins it).
- MCP tool count: 22 today → 25 after Phase B. `tests/test_server.py::test_all_tools_registered` pins the set.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## Phase A — M0 close-out (operational, no new code)

### Task A1: Verify the backlog extraction and re-sweep health

**Files:** none.

**Interfaces:**
- Consumes: the background run `python -m tesseract_mcp.indexer C:\Vaults\Tesseract --backend claude --force --batch 300`, logging to `~/.tesseract-mcp/force-reindex-full.log`.
- Produces: a verified, complete live graph — Phase B's live migration relies on a healthy caretaker baseline.

- [ ] **Step 1: Confirm the big batch finished**

Run: `tail -15 ~/.tesseract-mcp/force-reindex-full.log`
Expected: a JSON block ending with `"remaining": 0` and `"failed": 0`, then `FULL REINDEX EXIT: 0`. If the process is still running (no JSON yet), wait — check `Get-Process -Id <pid>` rather than re-launching. If `failed > 0`, re-run once: `./.venv/Scripts/python.exe -m tesseract_mcp.indexer 'C:\Vaults\Tesseract' --backend claude --force --batch 300`; if failures persist, STOP and report the log tail.

- [ ] **Step 2: Verify graph completeness via MCP** — from the session, call `graph_stats` (expect entity totals well above the partial 327 / 403 edges) and `related_notes` on `Claude/Sessions/2026-07-11 Full-system audit - search_brain root cause, cold caretakers, branch verdicts.md` (expect non-empty).

- [ ] **Step 3: One fresh sweep to reconcile health counters**

Run: `PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m tesseract_mcp.librarian 'C:\Vaults\Tesseract' 2>&1 | tail -30` (set `TESSERACT_EXTRACTOR=claude` in the environment first: `export TESSERACT_EXTRACTOR=claude`)
Expected: report shows `manifest_drift` near 0, `errors: none` (consolidate now runs on claude backend), `remaining 0`. The three pending organizer proposals stay pending — they are Taimoor's to accept in the vault.

### Task A2: Prove the scheduled task end-to-end

**Files:** none.

**Interfaces:**
- Consumes: registered task `tesseract-librarian`, wrapper `scripts/librarian-task.cmd`.
- Produces: verified unattended caretaking (M0 acceptance item).

- [ ] **Step 1: Force a run**

Run (PowerShell): `schtasks /run /tn tesseract-librarian`
Expected: `SUCCESS: Attempted to run the scheduled task "tesseract-librarian".`

- [ ] **Step 2: Verify it wrote through** (wait ~3 minutes for an incremental sweep)

Run (PowerShell): `Get-Content "$env:USERPROFILE\.tesseract-mcp\librarian-task.log" -Tail 25; schtasks /query /tn tesseract-librarian /v /fo list | Select-String "Last Result"`
Expected: JSON result with `"errors": {}` (or none), Last Result `0`. Also confirm `librarian_status` (MCP) shows the newest timestamp and `C:\Vaults\Tesseract\Claude\Librarian.md` gained a section.

### Task A3: Close the books on M0

**Files:**
- Modify: `docs/superpowers/specs/2026-07-11-tesseract-roadmap.md` (milestone board row M0)

- [ ] **Step 1: Flip the board** — in the roadmap's milestone table change M0's Status cell from `spec ready` to `shipped 2026-07-11`. While there, change M1's row from `spec approved` to `in progress` (Phase B starts next).

- [ ] **Step 2: Commit and sync master**

```bash
git add docs/superpowers/specs/2026-07-11-tesseract-roadmap.md
git commit -m "docs(roadmap): M0 shipped; M1 in progress"
git push origin codex/architecture-roadmap
git push origin codex/architecture-roadmap:master
```

- [ ] **Step 3: log_session** (MCP) — title `M0 ops hardening accepted`, project `tesseract-mcp`, content: acceptance list results (search latency, graph counts, sweep timestamp, scheduled-task Last Result, branch states), plus the codex-quota → claude-backend decision and the eval salvage numbers (17 queries, success@10 1.00, MRR 0.86).

---

## Phase B — M1 structured sheets (spec: 2026-07-11-structured-sheets-design.md)

**File structure for the phase:**

| File | Responsibility |
|---|---|
| `src/tesseract_mcp/sheets.py` (new) | schema load/discovery, validation, matching, upsert, query, `--check` CLI |
| `src/tesseract_mcp/server.py` | 3 new MCP tools |
| `src/tesseract_mcp/organizer.py` | sheet-folder exclusion (source + destination) |
| `vault/constitution.md` | `## Sheets` write-class section |
| `tests/test_sheets.py` (new) | everything above except server/organizer wiring |
| `tests/test_server.py`, `tests/test_organizer.py` | wiring tests |

### Task B1: Schema parsing and sheet discovery

**Files:**
- Create: `src/tesseract_mcp/sheets.py`
- Test: `tests/test_sheets.py`

**Interfaces:**
- Consumes: `Vault` (vault.py), `parse_frontmatter` + `SKIP_DIRS` (search.py).
- Produces: `SheetError(Exception)`; `Column(type, required=False, values=None, max_length=None)`; `Schema(name, folder, filename, key: list[str], identity: list[str], columns: dict[str, Column])`; `load_schema(vault, folder_rel) -> Schema`; `discover_sheets(vault) -> dict[str, str]` (sheet name → folder rel); `get_schema(vault, sheet_name) -> Schema`; `is_sheet_folder(vault, folder_rel) -> bool`; constants `SCHEMA_FILE = "_schema.md"`, `STANDARD_COLUMNS = {"created", "agent", "project", "tags"}`, `COLUMN_TYPES = {"string", "enum", "date", "bool", "url", "number"}`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_sheets.py`:

```python
import pytest

from tesseract_mcp import sheets
from tesseract_mcp.sheets import SheetError
from tesseract_mcp.vault import Vault

JOBS_SCHEMA = """---
sheet: jobs
filename: "{company} - {role}"
key: [company, role]
identity: [req_id, job_link]
columns:
  company: {type: string, required: true, max_length: 120}
  role: {type: string, required: true, max_length: 160}
  req_id: {type: string, max_length: 80}
  status:
    type: enum
    required: true
    values: [Saved, Applied, OA, Interview, Offer, Rejected, Ghosted, Withdrawn]
  date_applied: {type: date}
  sponsorship_required: {type: bool}
  job_link: {type: url, max_length: 500}
  next_follow_up: {type: date}
---

One note per posting. Never delete rows.
"""


@pytest.fixture
def sheet_vault(vault_dir):
    folder = vault_dir / "Job Search" / "Applications"
    folder.mkdir(parents=True)
    (folder / "_schema.md").write_text(JOBS_SCHEMA, encoding="utf-8")
    return Vault(vault_dir)


def test_load_schema_parses_columns(sheet_vault):
    s = sheets.load_schema(sheet_vault, "Job Search/Applications")
    assert s.name == "jobs"
    assert s.key == ["company", "role"]
    assert s.identity == ["req_id", "job_link"]
    assert s.columns["status"].type == "enum"
    assert "Ghosted" in s.columns["status"].values
    assert s.columns["company"].required is True
    assert s.columns["company"].max_length == 120


def test_discover_and_get_schema(sheet_vault):
    assert sheets.discover_sheets(sheet_vault) == {"jobs": "Job Search/Applications"}
    assert sheets.get_schema(sheet_vault, "jobs").folder == "Job Search/Applications"
    with pytest.raises(SheetError, match="jobs"):
        sheets.get_schema(sheet_vault, "nope")


def test_is_sheet_folder(sheet_vault):
    assert sheets.is_sheet_folder(sheet_vault, "Job Search/Applications") is True
    assert sheets.is_sheet_folder(sheet_vault, "Projects") is False


def test_malformed_schema_refuses(sheet_vault, vault_dir):
    (vault_dir / "Job Search" / "Applications" / "_schema.md").write_text(
        "---\nsheet: jobs\ncolumns:\n  x: {type: alien}\n---\n", encoding="utf-8"
    )
    with pytest.raises(SheetError, match="alien"):
        sheets.load_schema(sheet_vault, "Job Search/Applications")
```

- [ ] **Step 2: Run to verify failure** — `./.venv/Scripts/python.exe -m pytest tests/test_sheets.py -v` → FAIL: `ModuleNotFoundError`/`AttributeError`.

- [ ] **Step 3: Implement** — create `src/tesseract_mcp/sheets.py`:

```python
"""Structured sheets: schema-validated records in human folders.

A folder outside Claude/ becomes an agent-writable sheet iff the human
places a _schema.md in it; sheet_upsert is the only agent write path and
every write is validated. Spec:
docs/superpowers/specs/2026-07-11-structured-sheets-design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .search import SKIP_DIRS, parse_frontmatter
from .vault import Vault

SCHEMA_FILE = "_schema.md"
STANDARD_COLUMNS = {"created", "agent", "project", "tags"}
COLUMN_TYPES = {"string", "enum", "date", "bool", "url", "number"}


class SheetError(Exception):
    """Agent-actionable sheet failure; message names field/expected/got."""


@dataclass
class Column:
    type: str
    required: bool = False
    values: list[str] | None = None
    max_length: int | None = None


@dataclass
class Schema:
    name: str
    folder: str
    filename: str
    key: list[str]
    identity: list[str] = field(default_factory=list)
    columns: dict[str, Column] = field(default_factory=dict)


def load_schema(vault: Vault, folder_rel: str) -> Schema:
    path = vault.resolve(f"{folder_rel}/{SCHEMA_FILE}")
    if not path.is_file():
        raise SheetError(f"No {SCHEMA_FILE} in '{folder_rel}' — not a sheet.")
    meta = parse_frontmatter(path.read_text(encoding="utf-8"))
    for req in ("sheet", "filename", "key", "columns"):
        if req not in meta:
            raise SheetError(f"{folder_rel}/{SCHEMA_FILE}: missing '{req}'.")
    columns: dict[str, Column] = {}
    for name, spec in dict(meta["columns"]).items():
        if not isinstance(spec, dict) or spec.get("type") not in COLUMN_TYPES:
            raise SheetError(
                f"{folder_rel}/{SCHEMA_FILE}: column '{name}' has invalid type "
                f"'{(spec or {}).get('type')}' (allowed: {sorted(COLUMN_TYPES)})."
            )
        if spec["type"] == "enum" and not spec.get("values"):
            raise SheetError(
                f"{folder_rel}/{SCHEMA_FILE}: enum column '{name}' needs 'values'."
            )
        columns[name] = Column(
            type=spec["type"],
            required=bool(spec.get("required", False)),
            values=[str(v) for v in spec["values"]] if spec.get("values") else None,
            max_length=spec.get("max_length"),
        )
    return Schema(
        name=str(meta["sheet"]),
        folder=folder_rel,
        filename=str(meta["filename"]),
        key=[str(k) for k in meta["key"]],
        identity=[str(i) for i in meta.get("identity", [])],
        columns=columns,
    )


def discover_sheets(vault: Vault) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(vault.root.rglob(SCHEMA_FILE)):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        folder = "/".join(rel_parts[:-1])
        schema = load_schema(vault, folder)
        found[schema.name] = folder
    return found


def get_schema(vault: Vault, sheet_name: str) -> Schema:
    registry = discover_sheets(vault)
    if sheet_name not in registry:
        raise SheetError(
            f"Unknown sheet '{sheet_name}'. Registered: {sorted(registry) or 'none'}."
        )
    return load_schema(vault, registry[sheet_name])


def is_sheet_folder(vault: Vault, folder_rel: str) -> bool:
    return vault.resolve(f"{folder_rel}/{SCHEMA_FILE}").is_file()
```

- [ ] **Step 4: Run to verify pass** — `./.venv/Scripts/python.exe -m pytest tests/test_sheets.py -v` → all PASS.

- [ ] **Step 5: Commit** — `git add src/tesseract_mcp/sheets.py tests/test_sheets.py && git commit -m "feat(sheets): schema parsing and sheet discovery"`

### Task B2: Validation and normalization

**Files:**
- Modify: `src/tesseract_mcp/sheets.py` (append)
- Test: `tests/test_sheets.py` (append)

**Interfaces:**
- Produces: `norm_str(s) -> str` (trim, collapse whitespace, casefold — for comparison only); `normalize_link(url) -> str`; `validate_fields(schema, fields, *, require_required: bool) -> dict` (returns fields unchanged on success; raises SheetError naming field, expected, got). Tracking params stripped from links: `utm_*`, `ref`, `src`, `gh_src`, `lever-origin`.

- [ ] **Step 1: Failing tests** — append to `tests/test_sheets.py`:

```python
def test_norm_str_and_link():
    assert sheets.norm_str("  Adobe   Inc ") == sheets.norm_str("adobe inc")
    a = sheets.normalize_link("HTTPS://Jobs.Example.com/p/123/?utm_source=li&x=1#frag")
    b = sheets.normalize_link("https://jobs.example.com/p/123?x=1")
    assert a == b


VALID = {"company": "Adobe", "role": "SWE Intern", "status": "Saved"}


def test_validate_accepts_valid(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    assert sheets.validate_fields(s, VALID, require_required=True) == VALID


@pytest.mark.parametrize("fields,fragment", [
    ({**VALID, "recruiter": "Bob"}, "recruiter"),          # undeclared
    ({**VALID, "status": "applied"}, "applied"),            # bad enum (case-sensitive)
    ({**VALID, "date_applied": "07/11/2026"}, "YYYY-MM-DD"),
    ({**VALID, "sponsorship_required": "yes"}, "bool"),
    ({**VALID, "company": "x" * 121}, "max_length"),
    ({"company": "Adobe", "status": "Saved"}, "role"),      # missing required
])
def test_validate_rejects(sheet_vault, fields, fragment):
    s = sheets.get_schema(sheet_vault, "jobs")
    with pytest.raises(SheetError, match=fragment):
        sheets.validate_fields(s, fields, require_required=True)


def test_validate_patch_mode_skips_required(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    out = sheets.validate_fields(s, {"status": "Applied"}, require_required=False)
    assert out == {"status": "Applied"}


def test_standard_metadata_always_allowed(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    sheets.validate_fields(s, {**VALID, "tags": ["job"]}, require_required=True)
```

- [ ] **Step 2: Verify failure** — same pytest command; new tests FAIL with AttributeError.

- [ ] **Step 3: Implement** — append to `sheets.py`:

```python
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_WS = re.compile(r"\s+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TRACKING = re.compile(r"^(utm_.*|ref|src|gh_src|lever-origin)$")


def norm_str(s: str) -> str:
    return _WS.sub(" ", str(s)).strip().casefold()


def normalize_link(url: str) -> str:
    parts = urlsplit(str(url).strip())
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(k)
    )
    return urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(),
        parts.path.rstrip("/") or "/", urlencode(query), "",
    ))


def _check_value(name: str, col: Column, value) -> None:
    if col.type == "enum":
        if value not in (col.values or []):
            raise SheetError(
                f"Field '{name}': expected one of {col.values}, got '{value}'.")
        return
    if col.type == "date":
        if not isinstance(value, str) or not _DATE.match(value):
            raise SheetError(
                f"Field '{name}': expected date YYYY-MM-DD, got '{value}'.")
        return
    if col.type == "bool":
        if not isinstance(value, bool):
            raise SheetError(f"Field '{name}': expected bool, got '{value!r}'.")
        return
    if col.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SheetError(f"Field '{name}': expected number, got '{value!r}'.")
        return
    # string / url
    if not isinstance(value, str):
        raise SheetError(f"Field '{name}': expected {col.type}, got '{value!r}'.")
    if col.max_length and len(value) > col.max_length:
        raise SheetError(
            f"Field '{name}': exceeds max_length {col.max_length} "
            f"({len(value)} chars).")


def validate_fields(schema: Schema, fields: dict, *, require_required: bool) -> dict:
    for name, value in fields.items():
        if name in STANDARD_COLUMNS:
            continue
        col = schema.columns.get(name)
        if col is None:
            raise SheetError(
                f"Field '{name}' is not declared in sheet '{schema.name}' "
                f"(columns: {sorted(schema.columns)}; standard: "
                f"{sorted(STANDARD_COLUMNS)}).")
        _check_value(name, col, value)
    if require_required:
        missing = [n for n, c in schema.columns.items()
                   if c.required and n not in fields]
        if missing:
            raise SheetError(f"Missing required field(s): {missing}.")
    return fields
```

Move the `import re` / `urllib` lines up into the module's import block (stdlib imports at top, per file style).

- [ ] **Step 4: Verify pass**, **Step 5: Commit** — `git commit -m "feat(sheets): typed validation and normalization"`

### Task B3: Row scan, filename rendering, matching algorithm

**Files:**
- Modify: `src/tesseract_mcp/sheets.py`; Test: `tests/test_sheets.py`

**Interfaces:**
- Produces: `iter_rows(vault, schema) -> list[tuple[str, dict]]` (rel path + frontmatter; **direct children only**, `_schema.md` excluded); `render_filename(schema, fields) -> str` (sanitized, ≤120 chars, no extension); `match_row(vault, schema, fields) -> tuple[str | None, dict]` — (existing rel or None, identity backfill dict); ambiguity raises SheetError listing candidate paths.

- [ ] **Step 1: Failing tests** — append:

```python
def _row(vault_dir, name, meta_yaml, body="Body.\n"):
    p = vault_dir / "Job Search" / "Applications" / f"{name}.md"
    p.write_text(f"---\n{meta_yaml}---\n\n{body}", encoding="utf-8")
    return p


def test_iter_rows_direct_children_only(sheet_vault, vault_dir):
    _row(vault_dir, "Adobe - SWE", "company: Adobe\nrole: SWE\nstatus: Saved\n")
    sub = vault_dir / "Job Search" / "Applications" / "Archive"
    sub.mkdir()
    (sub / "Old.md").write_text("---\ncompany: Old\n---\n", encoding="utf-8")
    rows = sheets.iter_rows(sheet_vault, sheets.get_schema(sheet_vault, "jobs"))
    assert [r[0] for r in rows] == ["Job Search/Applications/Adobe - SWE.md"]


def test_render_filename_sanitizes(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    out = sheets.render_filename(s, {"company": "A/B: Corp?", "role": "ML|Eng"})
    assert out == "A-B- Corp- - ML-Eng"
    long = sheets.render_filename(s, {"company": "C" * 200, "role": "R"})
    assert len(long) <= 120


def test_match_by_req_id(sheet_vault, vault_dir):
    _row(vault_dir, "Adobe - SWE R1",
         "company: Adobe\nrole: SWE\nreq_id: R1\nstatus: Saved\n")
    _row(vault_dir, "Adobe - SWE R2",
         "company: Adobe\nrole: SWE\nreq_id: R2\nstatus: Saved\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    rel, backfill = sheets.match_row(
        sheet_vault, s, {"company": "adobe", "role": "SWE", "req_id": "R2"})
    assert rel == "Job Search/Applications/Adobe - SWE R2.md"
    assert backfill == {}


def test_match_backfills_single_candidate(sheet_vault, vault_dir):
    _row(vault_dir, "Acme - DS", "company: Acme\nrole: DS\nstatus: Saved\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    rel, backfill = sheets.match_row(
        sheet_vault, s, {"company": "Acme", "role": "DS", "req_id": "R9"})
    assert rel == "Job Search/Applications/Acme - DS.md"
    assert backfill == {"req_id": "R9"}


def test_match_new_posting_creates(sheet_vault, vault_dir):
    _row(vault_dir, "Acme - DS R1",
         "company: Acme\nrole: DS\nreq_id: R1\nstatus: Saved\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    rel, _ = sheets.match_row(
        sheet_vault, s, {"company": "Acme", "role": "DS", "req_id": "R2"})
    assert rel is None  # different posting -> new row


def test_match_ambiguous_errors_with_candidates(sheet_vault, vault_dir):
    _row(vault_dir, "Acme - DS R1",
         "company: Acme\nrole: DS\nreq_id: R1\nstatus: Saved\n")
    _row(vault_dir, "Acme - DS R2",
         "company: Acme\nrole: DS\nreq_id: R2\nstatus: Saved\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    with pytest.raises(SheetError, match="R1"):
        sheets.match_row(sheet_vault, s, {"company": "Acme", "role": "DS"})


def test_match_job_link_normalized(sheet_vault, vault_dir):
    _row(vault_dir, "Beta - MLE",
         "company: Beta\nrole: MLE\nstatus: Saved\n"
         "job_link: https://jobs.beta.com/x?utm_source=a\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    rel, _ = sheets.match_row(sheet_vault, s, {
        "company": "Beta", "role": "MLE",
        "job_link": "HTTPS://JOBS.BETA.COM/x/"})
    assert rel == "Job Search/Applications/Beta - MLE.md"
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement** — append to `sheets.py`:

```python
_FILENAME_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def iter_rows(vault: Vault, schema: Schema) -> list[tuple[str, dict]]:
    folder = vault.resolve(schema.folder)
    rows: list[tuple[str, dict]] = []
    for path in sorted(folder.glob("*.md")):
        if path.name == SCHEMA_FILE or not path.is_file():
            continue
        rel = f"{schema.folder}/{path.name}"
        rows.append((rel, parse_frontmatter(
            path.read_text(encoding="utf-8", errors="ignore"))))
    return rows


def render_filename(schema: Schema, fields: dict) -> str:
    rendered = schema.filename.format(
        **{k: str(fields.get(k, "")) for k in schema.columns})
    rendered = _FILENAME_ILLEGAL.sub("-", rendered)
    rendered = _WS.sub(" ", rendered).strip()
    return rendered[:120].rstrip()


def _identity_value(schema: Schema, meta: dict) -> tuple[str, str] | None:
    """(column, normalized value) for the highest-priority identity present."""
    for col in schema.identity:
        raw = meta.get(col)
        if raw in (None, ""):
            continue
        if schema.columns.get(col) and schema.columns[col].type == "url":
            return col, normalize_link(raw)
        return col, norm_str(raw)
    return None


def match_row(vault: Vault, schema: Schema,
              fields: dict) -> tuple[str | None, dict]:
    candidates = [
        (rel, meta) for rel, meta in iter_rows(vault, schema)
        if all(norm_str(meta.get(k, "")) == norm_str(fields.get(k, ""))
               for k in schema.key)
    ]
    incoming = _identity_value(schema, fields)
    if incoming is not None:
        col, value = incoming
        for rel, meta in candidates:
            existing = _identity_value(schema, meta)
            if existing is not None and existing[1] == value:
                return rel, {}
        bare = [(rel, meta) for rel, meta in candidates
                if _identity_value(schema, meta) is None]
        if len(bare) == 1 and len(candidates) == 1:
            return bare[0][0], {col: fields[col]}
        return None, {}
    if len(candidates) == 1:
        return candidates[0][0], {}
    if not candidates:
        return None, {}
    raise SheetError(
        "Ambiguous match: multiple rows share this key — supply "
        f"{schema.identity} to disambiguate. Candidates: "
        f"{[rel for rel, _ in candidates]}")
```

- [ ] **Step 4: Verify pass**, **Step 5: Commit** — `git commit -m "feat(sheets): row scan, filename rendering, posting-identity matching"`

### Task B4: Upsert — frontmatter patch, ## Log append, create

**Files:**
- Modify: `src/tesseract_mcp/sheets.py`; Test: `tests/test_sheets.py`

**Interfaces:**
- Produces: `upsert(vault, sheet: str, fields: dict, body: str | None = None, agent: str = "claude", now: datetime | None = None) -> dict` returning `{"result": "created"|"updated", "path": rel, "changed": {field: {"from": old, "to": new}}}`. Line-level frontmatter patching (untouched lines byte-identical); body untouched except one `## Log` line appended on status change; no-op upserts don't rewrite the file. Uses `vault.write(..., overwrite=True, confirm_outside_claude=True)` — the human-placed `_schema.md` is the standing grant (checked first via `get_schema`); this call site is the ONLY one allowed to pass the flag without a per-write user ask, per the spec's write-class table.

- [ ] **Step 1: Failing tests** — append:

```python
def test_upsert_creates_with_log(sheet_vault):
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Saved"},
                        agent="cowork")
    assert out["result"] == "created"
    text = sheet_vault.read(out["path"])
    assert "company: Nova" in text and "## Log" in text
    assert "status: (new) → Saved (agent: cowork)" in text
    assert "agent: cowork" in text and "created:" in text


def test_upsert_patch_preserves_untouched_bytes(sheet_vault, vault_dir):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Saved\n"
             "channel: LinkedIn   # via referral\n",
             body="Story para.\n\n## Log\n- 2026-07-10 status: (new) → Saved (agent: claude)\n")
    before = p.read_text(encoding="utf-8")
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Applied",
                         "date_applied": "2026-07-11"})
    assert out["result"] == "updated"
    assert out["changed"]["status"] == {"from": "Saved", "to": "Applied"}
    after = p.read_text(encoding="utf-8")
    assert "channel: LinkedIn   # via referral" in after   # untouched line intact
    assert "date_applied: 2026-07-11" in after             # new field appended
    assert "Story para.\n" in after                        # body intact
    assert after.count("## Log") == 1
    assert "status: Saved → Applied" in after


def test_upsert_noop_does_not_touch_file(sheet_vault, vault_dir):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Saved\n")
    mtime = p.stat().st_mtime_ns
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Saved"})
    assert out["result"] == "updated" and out["changed"] == {}
    assert p.stat().st_mtime_ns == mtime


def test_upsert_refuses_undeclared_and_unknown_sheet(sheet_vault):
    with pytest.raises(SheetError, match="recruiter"):
        sheets.upsert(sheet_vault, "jobs",
                      {"company": "N", "role": "R", "status": "Saved",
                       "recruiter": "Bob"})
    with pytest.raises(SheetError, match="Unknown sheet"):
        sheets.upsert(sheet_vault, "subscriptions", {"company": "N"})


def test_raw_write_still_confirm_gated(sheet_vault):
    from tesseract_mcp.vault import VaultError
    with pytest.raises(VaultError, match="outside Claude/"):
        sheet_vault.write("Job Search/Applications/Sneak.md", "hi")


def test_filename_collision_gets_suffix(sheet_vault, vault_dir):
    _row(vault_dir, "Nova - MLE",
         "company: Nova\nrole: MLE\nreq_id: R1\nstatus: Saved\n")
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "req_id": "R2",
                         "status": "Saved"})
    assert out["result"] == "created"
    assert out["path"].endswith("Nova - MLE 2.md")
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement** — append to `sheets.py` (add `from datetime import datetime` to imports):

```python
def _split(text: str) -> tuple[list[str], str]:
    """(frontmatter lines without --- fences, body). Empty meta if none."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[4:end].splitlines()
            body = text[end + 4:].lstrip("\r").lstrip("\n")
            return fm, body
    return [], text


def _patch_lines(fm_lines: list[str], updates: dict) -> list[str]:
    """Replace top-level 'key: value' lines; append keys not present."""
    import yaml as _yaml
    done: set[str] = set()
    out: list[str] = []
    for line in fm_lines:
        m = re.match(r"^([A-Za-z_][\w-]*):", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(_yaml.safe_dump({key: updates[key]},
                                       sort_keys=False).strip())
            done.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in done:
            out.append(_yaml.safe_dump({key: value}, sort_keys=False).strip())
    return out


def _log_line(field_changes: dict, agent: str, now: datetime) -> str | None:
    st = field_changes.get("status")
    if not st:
        return None
    frm = st["from"] if st["from"] is not None else "(new)"
    return f"- {now:%Y-%m-%d} status: {frm} → {st['to']} (agent: {agent})"


def _append_log(body: str, line: str) -> str:
    if "## Log" not in body:
        return body.rstrip("\n") + "\n\n## Log\n" + line + "\n"
    return body.rstrip("\n") + "\n" + line + "\n"


def upsert(vault: Vault, sheet: str, fields: dict, body: str | None = None,
           agent: str = "claude", now: datetime | None = None) -> dict:
    now = now or datetime.now()
    schema = get_schema(vault, sheet)
    rel, backfill = match_row(vault, schema, dict(fields))
    merged = {**fields, **backfill}
    if rel is None:
        validate_fields(schema, merged, require_required=True)
        stem = render_filename(schema, merged)
        candidate, n = stem, 2
        while vault.resolve(f"{schema.folder}/{candidate}.md").exists():
            candidate = f"{stem} {n}"
            n += 1
        rel = f"{schema.folder}/{candidate}.md"
        meta = {**merged,
                "created": f"{now:%Y-%m-%d %H:%M}", "agent": agent}
        import yaml as _yaml
        fm = _yaml.safe_dump(meta, sort_keys=False)
        changed = {k: {"from": None, "to": v} for k, v in merged.items()}
        text_body = body if body is not None else ""
        log = _log_line(changed, agent, now)
        if log:
            text_body = _append_log(text_body, log)
        vault.write(rel, f"---\n{fm}---\n\n{text_body}",
                    confirm_outside_claude=True)
        return {"result": "created", "path": rel, "changed": changed}

    validate_fields(schema, merged, require_required=False)
    text = vault.read(rel)
    fm_lines, note_body = _split(text)
    old = parse_frontmatter(text)
    changed = {k: {"from": old.get(k), "to": v}
               for k, v in merged.items() if old.get(k) != v}
    if not changed:
        return {"result": "updated", "path": rel, "changed": {}}
    new_fm = _patch_lines(fm_lines, {k: v["to"] for k, v in changed.items()})
    new_fm = _patch_lines(new_fm, {"agent": agent})
    log = _log_line(changed, agent, now)
    if log:
        note_body = _append_log(note_body, log)
    vault.write(rel, "---\n" + "\n".join(new_fm) + "\n---\n\n" + note_body,
                overwrite=True, confirm_outside_claude=True)
    return {"result": "updated", "path": rel, "changed": changed}
```

- [ ] **Step 4: Verify pass** (the byte-preservation test is the one to watch), **Step 5: Commit** — `git commit -m "feat(sheets): validated upsert with line-level patch and status log"`

### Task B5: Typed query

**Files:**
- Modify: `src/tesseract_mcp/sheets.py`; Test: `tests/test_sheets.py`

**Interfaces:**
- Produces: `query(vault, sheet: str, filters: dict | None = None, sort: dict | None = None, limit: int = 50) -> list[dict]`. Filters `{column: {op: value}}`, ops `eq ne lt lte gt gte contains missing in nin`, AND-composed. `sort={"by": col, "dir": "asc"|"desc"}`, missing values last. Rows returned as `{"path": rel, **frontmatter}`.

- [ ] **Step 1: Failing tests** — append:

```python
@pytest.fixture
def populated(sheet_vault, vault_dir):
    _row(vault_dir, "A - R1", "company: A\nrole: R1\nstatus: Saved\n")
    _row(vault_dir, "B - R2",
         "company: B\nrole: R2\nstatus: Applied\nnext_follow_up: 2026-07-01\n")
    _row(vault_dir, "C - R3",
         "company: C\nrole: R3\nstatus: Rejected\nnext_follow_up: 2026-07-05\n")
    return sheet_vault


def test_query_follow_ups_due(populated):
    rows = sheets.query(populated, "jobs", {
        "next_follow_up": {"lte": "2026-07-11"},
        "status": {"nin": ["Rejected", "Ghosted", "Withdrawn"]},
    })
    assert [r["company"] for r in rows] == ["B"]


def test_query_ops_and_sort(populated):
    assert len(sheets.query(populated, "jobs", {"status": {"eq": "Saved"}})) == 1
    assert len(sheets.query(populated, "jobs",
                            {"next_follow_up": {"missing": True}})) == 1
    rows = sheets.query(populated, "jobs", {},
                        sort={"by": "next_follow_up", "dir": "desc"})
    assert rows[0]["company"] == "C" and rows[-1]["company"] == "A"  # missing last


def test_query_rejects_bad_op_and_untyped_ordering(populated):
    with pytest.raises(SheetError, match="Unknown operator"):
        sheets.query(populated, "jobs", {"status": {"like": "x"}})
    with pytest.raises(SheetError, match="ordering"):
        sheets.query(populated, "jobs", {"company": {"lt": "M"}})


def test_query_excludes_schema_and_respects_limit(populated):
    rows = sheets.query(populated, "jobs", {}, limit=2)
    assert len(rows) == 2
    assert all(not r["path"].endswith("_schema.md") for r in rows)
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement** — append:

```python
_OPS = {"eq", "ne", "lt", "lte", "gt", "gte", "contains", "missing", "in", "nin"}
_ORDERED_TYPES = {"date", "number"}


def _matches(col_type: str | None, actual, op: str, expected) -> bool:
    if op == "missing":
        return (actual in (None, "")) is bool(expected)
    if actual in (None, ""):
        return op in ("ne", "nin")
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "nin":
        return actual not in expected
    if op == "contains":
        return str(expected).casefold() in str(actual).casefold()
    return {"lt": actual < expected, "lte": actual <= expected,
            "gt": actual > expected, "gte": actual >= expected}[op]


def query(vault: Vault, sheet: str, filters: dict | None = None,
          sort: dict | None = None, limit: int = 50) -> list[dict]:
    schema = get_schema(vault, sheet)
    filters = filters or {}
    for col, ops in filters.items():
        col_type = schema.columns[col].type if col in schema.columns else None
        for op in ops:
            if op not in _OPS:
                raise SheetError(f"Unknown operator '{op}' (allowed: {sorted(_OPS)}).")
            if op in ("lt", "lte", "gt", "gte") and col_type not in _ORDERED_TYPES:
                raise SheetError(
                    f"Column '{col}' ({col_type}) does not support ordering "
                    f"operators; only {sorted(_ORDERED_TYPES)} columns do.")
    out = []
    for rel, meta in iter_rows(vault, schema):
        ok = all(
            _matches(schema.columns[col].type if col in schema.columns else None,
                     meta.get(col), op, expected)
            for col, ops in filters.items() for op, expected in ops.items())
        if ok:
            out.append({"path": rel, **meta})
    if sort:
        by, desc = sort.get("by"), sort.get("dir") == "desc"
        present = [r for r in out if r.get(by) not in (None, "")]
        absent = [r for r in out if r.get(by) in (None, "")]
        present.sort(key=lambda r: r[by], reverse=desc)
        out = present + absent
    return out[:limit]


def schema_info(vault: Vault, sheet: str | None = None) -> dict:
    if sheet is None:
        registry = discover_sheets(vault)
        return {name: {"folder": folder,
                       "rows": len(iter_rows(vault, load_schema(vault, folder)))}
                for name, folder in registry.items()}
    s = get_schema(vault, sheet)
    path = vault.resolve(f"{s.folder}/{SCHEMA_FILE}")
    _, instructions = _split(path.read_text(encoding="utf-8"))
    return {"sheet": s.name, "folder": s.folder, "filename": s.filename,
            "key": s.key, "identity": s.identity,
            "columns": {n: vars(c) for n, c in s.columns.items()},
            "instructions": instructions.strip()}
```

Also add a quick test for `schema_info` in the same step:

```python
def test_schema_info_lists_and_details(populated):
    listing = sheets.schema_info(populated)
    assert listing["jobs"]["rows"] == 3
    detail = sheets.schema_info(populated, "jobs")
    assert detail["columns"]["status"]["values"][0] == "Saved"
    assert "Never delete rows" in detail["instructions"]
```

- [ ] **Step 4: Verify pass**, **Step 5: Commit** — `git commit -m "feat(sheets): typed query and schema discovery surface"`

### Task B6: MCP tools + organizer exclusion

**Files:**
- Modify: `src/tesseract_mcp/server.py` (append 3 tools), `src/tesseract_mcp/organizer.py`
- Test: `tests/test_server.py` (registration + smoke), `tests/test_organizer.py` (exclusion)

**Interfaces:**
- Consumes: `sheets.upsert / query / schema_info / is_sheet_folder` (B4/B5).
- Produces: MCP tools `sheet_upsert(sheet, fields, body=None)`, `sheet_query(sheet, filters=None, sort=None, limit=50)`, `sheet_schema(sheet=None)`. Organizer never moves notes out of, or into, folders where `is_sheet_folder` is true.

- [ ] **Step 1: Failing tests.** In `tests/test_server.py`, add `"sheet_upsert", "sheet_query", "sheet_schema"` to the expected set in `test_all_tools_registered`, and append:

```python
def test_sheet_tools_smoke(vault_dir, monkeypatch):
    folder = vault_dir / "Records"
    folder.mkdir()
    (folder / "_schema.md").write_text(
        "---\nsheet: things\nfilename: \"{name}\"\nkey: [name]\n"
        "columns:\n  name: {type: string, required: true}\n"
        "  when: {type: date}\n---\nFile one note per thing.\n",
        encoding="utf-8")
    out = server.sheet_upsert("things", {"name": "First"})
    assert out["result"] == "created"
    rows = server.sheet_query("things", {"when": {"missing": True}})
    assert rows[0]["name"] == "First"
    assert "things" in server.sheet_schema()
```

In `tests/test_organizer.py`, append (adapt fixture names to that file's existing pattern after reading its imports — it exercises `organizer` against a temp vault; the assertion contract is below):

```python
def test_organizer_skips_sheet_folders(vault, vault_dir):
    from tesseract_mcp import sheets
    folder = vault_dir / "Records"
    folder.mkdir()
    (folder / "_schema.md").write_text(
        "---\nsheet: things\nfilename: \"{name}\"\nkey: [name]\n"
        "columns:\n  name: {type: string}\n---\n", encoding="utf-8")
    (folder / "Row.md").write_text("---\nname: Row\n---\n", encoding="utf-8")
    from tesseract_mcp import organizer
    # Rows must be excluded as move SOURCES:
    assert all("Records/" not in c for c in organizer.candidate_paths(vault))
    # and sheet folders excluded as DESTINATIONS:
    assert "Records" not in organizer.destination_folders(vault)
```

Before writing this test, read `src/tesseract_mcp/organizer.py` top-to-bottom; if the source/destination enumeration functions have different names, target those names — the behavioral contract (skip as source AND destination) is what the test must pin, using the module's real API.

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement.** In `server.py`, import `from . import sheets as sheets_mod` in the existing import block, and append after `recall_bundle`:

```python
@mcp.tool()
def sheet_upsert(sheet: str, fields: dict, body: str | None = None) -> dict:
    """Create or update one row in a registered sheet (a human-blessed folder
    with _schema.md). Validates every field against the schema; finds the
    existing row by key + posting identity; patches only the passed fields.
    Returns created|updated, the path, and a changed map."""
    return sheets_mod.upsert(get_vault(), sheet, fields, body=body)


@mcp.tool()
def sheet_query(sheet: str, filters: dict | None = None,
                sort: dict | None = None, limit: int = 50) -> list[dict]:
    """Typed query over a sheet's rows. Filters: {column: {op: value}} with
    ops eq/ne/lt/lte/gt/gte/contains/missing/in/nin, AND-composed.
    Example — follow-ups due: {"next_follow_up": {"lte": "2026-07-11"},
    "status": {"nin": ["Rejected", "Ghosted", "Withdrawn"]}}."""
    return sheets_mod.query(get_vault(), sheet, filters, sort=sort, limit=limit)


@mcp.tool()
def sheet_schema(sheet: str | None = None) -> dict:
    """No arg: list registered sheets (folder + row count). With a sheet
    name: full contract — columns, types, key, filing instructions."""
    return sheets_mod.schema_info(get_vault(), sheet)
```

In `organizer.py`, wherever candidate notes are enumerated, skip notes whose parent folder is a sheet (`sheets.is_sheet_folder(vault, parent_rel)`), and wherever destination folders are enumerated, drop sheet folders. Import at top: `from . import sheets`.

Also (spec's caretaker section): in `librarian.py`'s health step, add a `sheets` line — run `sheets.check`-style validation per registered sheet (reuse `discover_sheets` + `validate_fields`; count invalid rows, never fix) and include `invalid_sheet_rows N` in the health dict and `format_report` line. Add one test in `tests/test_librarian.py`: a vault with one invalid row reports `invalid_sheet_rows 1`; a sheetless vault reports 0.

- [ ] **Step 4: Verify** — `./.venv/Scripts/python.exe -m pytest tests/test_server.py tests/test_organizer.py tests/test_sheets.py -v` then full suite `-q`.

- [ ] **Step 5: Commit** — `git commit -m "feat(server,organizer): sheet_* MCP tools; caretakers respect sheet islands"`

### Task B7: `--check` migration CLI

**Files:**
- Modify: `src/tesseract_mcp/sheets.py` (add `main()`); Test: `tests/test_sheets.py`

**Interfaces:**
- Produces: `python -m tesseract_mcp.sheets <vault> --check` → per-sheet report of invalid rows (field errors), duplicate identities, and a `clean: true|false` verdict; exit 1 when dirty. Report-only — never writes.

- [ ] **Step 1: Failing test** — append:

```python
def test_check_reports_drift_and_dupes(sheet_vault, vault_dir, capsys):
    _row(vault_dir, "Ok - Row", "company: Ok\nrole: Row\nstatus: Saved\n")
    _row(vault_dir, "Bad - Row",
         "company: Bad\nrole: Row\nstage: applied\nstatus: Saved\n")  # undeclared
    _row(vault_dir, "Dup - A", "company: Dup\nrole: A\nstatus: Saved\n")
    _row(vault_dir, "Dup - A2", "company: Dup\nrole: A\nstatus: Saved\n")
    rc = sheets.check(sheet_vault)
    out = capsys.readouterr().out
    assert rc == 1
    assert "stage" in out and "Dup" in out and '"clean": false' in out


def test_check_clean_vault_exits_zero(sheet_vault, vault_dir, capsys):
    _row(vault_dir, "Ok - Row", "company: Ok\nrole: Row\nstatus: Saved\n")
    assert sheets.check(sheet_vault) == 0
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement** — append to `sheets.py`:

```python
def check(vault: Vault) -> int:
    import json as _json
    report: dict = {"sheets": {}, "clean": True}
    for name, folder in discover_sheets(vault).items():
        schema = load_schema(vault, folder)
        invalid, seen, dupes = [], {}, []
        for rel, meta in iter_rows(vault, schema):
            try:
                validate_fields(schema, {k: v for k, v in meta.items()
                                         if k not in STANDARD_COLUMNS},
                                require_required=True)
            except SheetError as e:
                invalid.append({"path": rel, "error": str(e)})
            key = tuple(norm_str(meta.get(k, "")) for k in schema.key)
            ident = _identity_value(schema, meta)
            full = (key, ident[1] if ident else None)
            if full in seen:
                dupes.append({"paths": [seen[full], rel]})
            else:
                seen[full] = rel
        report["sheets"][name] = {
            "rows": len(iter_rows(vault, schema)),
            "invalid": invalid, "duplicates": dupes}
        if invalid or dupes:
            report["clean"] = False
    print(_json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["clean"] else 1


def main() -> None:
    import argparse
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Structured sheets: validate all rows against schemas.")
    parser.add_argument("vault")
    parser.add_argument("--check", action="store_true", required=True)
    args = parser.parse_args()
    raise SystemExit(check(Vault(args.vault)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify pass**, full suite, **Step 5: Commit** — `git commit -m "feat(sheets): --check migration CLI (report-only)"`

### Task B8: Constitution, live schema, migration, rollout — **consent-gated**

**Files:**
- Modify: `vault/constitution.md` (repo source of the vault's `Claude/README.md`)
- Live artifacts (consent): `C:\Vaults\Tesseract\Job Search\Applications\_schema.md`, live constitution refresh.

- [ ] **Step 1: Add the `## Sheets` section** to `vault/constitution.md` (append near the write-rules section):

```markdown
## Sheets

Some folders outside Claude/ are **sheets**: structured records the human
has opened to agents by placing a `_schema.md` in them. Three write classes:

| Where | Rule |
|---|---|
| `Claude/` | agents write freely |
| Sheet folders (`_schema.md` present) | writes only via `sheet_upsert`, schema-validated |
| Everything else | explicit human request only (`confirm_outside_claude`) |

Discover contracts with `sheet_schema`. One note per row; never delete
rows — states like Withdrawn/Rejected exist instead. The schema file is
human-owned: propose changes in prose, never edit it.
```

Run the conventions/constitution test file that covers installs (`./.venv/Scripts/python.exe -m pytest tests/test_install_conventions.py -q`) and fix any fixture expectations that pin the old text. Commit: `git commit -m "feat(conventions): Sheets write class in the constitution"`.

- [ ] **Step 2: STOP (consent) — live `_schema.md`.** Show Taimoor the exact jobs schema (the full v1 from the spec: company, role, req_id, status enum Saved→Withdrawn, date_applied, job_posted_date, channel, location, sponsorship_required, resume_version, job_link, last_contact, next_follow_up — with `key: [company, role]`, `identity: [req_id, job_link]`, `filename: "{company} - {role}"`, and the filing-instructions body including the Cowork playbook from the spec). On approval, write it to `C:\Vaults\Tesseract\Job Search\Applications\_schema.md` and refresh the live constitution per the conventions installer.

- [ ] **Step 3: Run migration check against the live vault**

Run: `./.venv/Scripts/python.exe -m tesseract_mcp.sheets 'C:\Vaults\Tesseract' --check`
Expected: a report over ~60 rows. Triage with Taimoor: extend the schema for fields that are genuinely in the wild (the check tells us), fix notes by hand, or accept listed drift. Iterate until `"clean": true`. **Never auto-rewrite his notes.**

- [ ] **Step 4: Restart servers** (`Get-Process tesseract-mcp | Stop-Process -Force`), then smoke via MCP: `sheet_schema("jobs")` returns the contract; `sheet_query("jobs", {"status": {"eq": "Applied"}})` returns rows. Flip the roadmap board M1 row to `shipped`, commit, push, `log_session`.

---

## Phase C — M2 Cowork onboarding (spec: 2026-07-11-cowork-onboarding-design.md)

### Task C1: Register tesseract where Cowork reads MCP config

**Files:** none in repo (unless `mcp_sync` coverage needs a new path — see Step 2).

- [ ] **Step 1: Find Cowork's MCP config.** Claude Code desktop reads user-scope `~/.claude.json` (`mcpServers.tesseract` already present — verify with: `cat ~/.claude.json | python -c "import json,sys; print('tesseract' in json.load(sys.stdin).get('mcpServers', {}))"` → `True`). If Taimoor's Cowork surface is claude.ai Cowork (browser), it uses claude.ai connector settings instead — a local stdio server is reachable there only via Desktop; confirm with him which surface he applies to jobs from. If Desktop: nothing to install. If claude.ai: STOP and record that remote access is an M7 question (Oracle VM endpoint), and the acceptance test runs on Desktop Cowork.

- [ ] **Step 2: If (and only if) a second config file was needed**, add its path to `mcp_sync`'s `--check` coverage with a test mirroring the existing manifest-drift tests in `tests/test_mcp_sync.py`, and commit.

### Task C2: Acceptance — Cowork runs the job pipeline (**consent-gated**: one real posting)

**Files:** none. This is the M1+M2 proof.

- [ ] **Step 1: STOP (consent):** Taimoor opens a Cowork session and picks a real posting he wants tracked.
- [ ] **Step 2: In Cowork:** `onboard` → `sheet_schema("jobs")` → save the posting (`sheet_upsert`, `status: Saved`) → apply → upsert `{status: "Applied", date_applied, channel, resume_version}`.
- [ ] **Step 3: Verify all five spec checks:** row visible in `Tracker.base` with correct columns; `## Log` line appended; `changed` map returned on the status flip; a second identical apply-run returns `updated` with empty `changed` (no duplicate note); a deliberately bad enum (`status: "applied"`) returns a validation error naming the allowed values, and Cowork self-corrects.
- [ ] **Step 4:** Flip roadmap M2 row to `shipped`; `log_session` with the transcript highlights; push.

---

## Phase D — M3 discipline hooks (spec: 2026-07-11-discipline-hooks-design.md)

### Task D1: Recall context CLI

**Files:**
- Modify: `src/tesseract_mcp/recall.py` (add `context_block()` + `main()`); Test: `tests/test_recall.py` (append)

**Interfaces:**
- Consumes: the existing resume-bundle functions in `recall.py` (read the module first; it exposes the digest/resume bundle builders the recall harness shipped — reuse, do not duplicate).
- Produces: `context_block(vault, project: str | None, budget: int = 2000) -> str` and CLI `python -m tesseract_mcp.recall --vault <path> --context [--project NAME] [--budget N]`. Empty output + exit 0 on ANY failure (unreachable vault, no data) — **a broken hook must never block a session.**

- [ ] **Step 1: Failing tests** — append to `tests/test_recall.py` (match its existing fixtures — read the file top first):

```python
def test_context_block_names_latest_session(vault):
    from tesseract_mcp import notes, recall
    notes.log_session(vault, "Fixed the flux capacitor", "Did things.",
                      "tesseract-mcp", [])
    block = recall.context_block(vault, "tesseract-mcp")
    assert "flux capacitor" in block
    assert len(block) <= 2000


def test_context_block_budget_truncates(vault):
    from tesseract_mcp import notes, recall
    notes.log_session(vault, "Session", "x" * 5000, "p", [])
    assert len(recall.context_block(vault, "p", budget=500)) <= 500


def test_context_cli_survives_missing_vault(tmp_path):
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "tesseract_mcp.recall",
         "--vault", str(tmp_path / "nope"), "--context"],
        capture_output=True, text=True, env={**__import__('os').environ,
                                             "PYTHONPATH": "src"})
    assert r.returncode == 0 and r.stdout.strip() == ""
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement** in `recall.py` — `context_block` composes from the existing resume bundle (latest sessions for the project, open tasks, recent decisions), truncated to `budget` chars ending at a line boundary; `main()` parses `--vault/--context/--project/--budget`, wraps everything in `try/except Exception: return` (print nothing, exit 0), and reconfigures stdout to UTF-8 like `librarian.main()` does. Add `if __name__ == "__main__": main()`.

- [ ] **Step 4: Verify pass**, full suite, **Step 5: Commit** — `git commit -m "feat(recall): hook-friendly context CLI (never blocks a session)"`

### Task D2: Hook scripts + installer

**Files:**
- Create: `scripts/hooks/session-start.py`, `scripts/hooks/stop-nudge.py`
- Create: `src/tesseract_mcp/hook_sync.py`; Test: `tests/test_hook_sync.py`

**Interfaces:**
- Consumes: D1's CLI.
- Produces: `python -m tesseract_mcp.hook_sync --check | --install` — additively merges two hook entries into `~/.claude/settings.json` (`SessionStart` → runs `session-start.py`; `Stop` → runs `stop-nudge.py`), mirroring `skill_sync.py`'s consent rule (agents run `--check` freely; `--install` only on explicit request). Read `src/tesseract_mcp/skill_sync.py` (83 lines) first and copy its structure: same additive-merge, same drift reporting.
- `session-start.py`: reads the hook JSON from stdin (contains `cwd`), infers the project from the workspace folder name, execs the D1 CLI with `TESSERACT_VAULT_PATH` from its own environment or the default `C:\Vaults\Tesseract`, prints the context block to stdout (Claude Code injects stdout as additionalContext). Any exception → print nothing, exit 0.
- `stop-nudge.py`: reads hook JSON (contains `transcript_path`), counts assistant `tool_use` entries and greps for `log_session`; if tool uses ≥ 10 and no `log_session` found, prints a one-line reminder ("Significant session with no log_session — consider filing a session log before finishing."); ANY doubt or error → exit 0 silently. **Zero false blocks:** never exit nonzero.

- [ ] **Step 1: Failing tests** for `hook_sync` (merge adds entries without clobbering existing hooks; `--check` reports missing/drifted entries; second install is idempotent) — write them against a temp settings.json path injected via env var `CLAUDE_SETTINGS_PATH` (the module reads it, defaulting to `~/.claude/settings.json`), mirroring the patterns in `tests/test_skill_sync.py` (read that file first and follow its fixtures).

- [ ] **Step 2-4: TDD cycle** as above; hook scripts get unit tests only for their pure functions (project inference; transcript counting on a fixture JSONL) — invoke logic is exercised manually in D3.

- [ ] **Step 5: Commit** — `git commit -m "feat(hooks): session-start context + stop nudge, additive hook_sync installer"`

### Task D3: Live install + acceptance (**consent-gated**)

- [ ] **Step 1: STOP (consent):** show Taimoor the exact hook JSON `--install` will merge into `~/.claude/settings.json`. On yes, run `./.venv/Scripts/python.exe -m tesseract_mcp.hook_sync --install`.
- [ ] **Step 2: Acceptance per spec:** (a) fresh Claude Code session in this repo opens with a context block naming the last session — no manual command; (b) an edit-heavy session ending without `log_session` shows the nudge; a trivial Q&A session doesn't; (c) rename the vault folder temporarily → sessions start normally with no context and no error (rename it back!).
- [ ] **Step 3:** `log_session`, push.

### Task D4: /digest gains follow-ups + discipline meter

**Files:**
- Modify: `skills/digest/SKILL.md` (the repo-versioned skill), then `python -m tesseract_mcp.skill_sync` (additive re-install, consent already standing for skill_sync per recall-harness rollout).

- [ ] **Step 1:** Add to the digest skill's composition steps: a **Follow-ups due** section built from `sheet_query("jobs", {"next_follow_up": {"lte": <today>}, "status": {"nin": ["Rejected", "Ghosted", "Withdrawn"]}})`, and a **Discipline meter** line comparing session-log count in `Claude/Sessions/` (last 7 days, by filename date) against the hook's nudge expectations. Degrade gracefully: if the jobs sheet or hooks aren't installed, omit the section (the recall-harness per-section degradation pattern).
- [ ] **Step 2:** Run `./.venv/Scripts/python.exe -m tesseract_mcp.skill_sync --check` then (standing consent) the real sync; run `/digest` once manually and eyeball both sections. Commit skill change: `git commit -m "feat(skills): digest follow-ups-due + discipline meter sections"`.

### Task D5: Digest scheduling — **deferred gate**

- [ ] After the manual week (standing decision from the recall-harness rollout): STOP (consent), then schedule the morning digest (Claude Code scheduled routine, or `schtasks` launching `claude -p "/digest"`). Not before.

---

## Completion

After D5 (or D4 if the manual week is still running): flip roadmap rows, fast-forward master (`git push origin codex/architecture-roadmap:master`), final `log_session` for the phase, and update the milestone board — M3 shipped means the roadmap's next open items are M4 (ingest) and M5 (projects sheet), which need their brainstorm sessions per the briefs.
