# Tesseract Mind Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Tesseract Obsidian vault into a shared Claude+human knowledge base: CouchDB sync server on an Oracle free VM, vault moved out of OneDrive, LiveSync configured, and a custom `tesseract-mcp` Python MCP server giving Claude structured, quarantine-enforced read/write access.

**Architecture:** Three layers. (1) Infra: Docker on an Oracle Always Free VM runs CouchDB behind Caddy (Let's Encrypt TLS on a DuckDNS hostname); LiveSync end-to-end encryption means the server stores only ciphertext. (2) Vault: `C:\Vaults\Tesseract` with an agent-owned `Claude/` subtree governed by a constitution file. (3) Access: `tesseract-mcp`, a Python FastMCP server operating directly on the vault filesystem, enforcing the `Claude/` write quarantine in code.

**Tech Stack:** Python 3.11+, `mcp` SDK (FastMCP), PyYAML, pytest; Docker Compose, CouchDB 3, Caddy 2, DuckDNS; PowerShell for the migration script.

**Repo:** `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp` (this repo). All code tasks happen here.

**Human checkpoints:** Tasks 9, 10, and 12 need Taimoor at the keyboard (Oracle/DuckDNS signup, Obsidian GUI). The controller must pause and hand those to the user with the instructions in the task, then resume. Do not dispatch subagents for them.

---

## File structure

```
tesseract-mcp/
├── pyproject.toml               # package metadata, deps, pytest config
├── .gitignore
├── README.md                    # what this is, how to install/register the MCP
├── src/tesseract_mcp/
│   ├── __init__.py
│   ├── vault.py                 # Vault class: path safety, read/write/append, quarantine
│   ├── notes.py                 # frontmatter, log_session, capture, upsert_concept
│   ├── search.py                # full-text search with tag/folder filters
│   └── server.py                # FastMCP tool wiring + entry point
├── tests/
│   ├── conftest.py              # fixture vault
│   ├── test_vault.py
│   ├── test_notes.py
│   ├── test_search.py
│   └── test_server.py
├── scripts/
│   ├── install_conventions.py   # creates Claude/ tree + constitution in a vault
│   └── migrate-vault.ps1        # copies vault out of OneDrive, verifies counts
├── vault/
│   └── constitution.md          # canonical Claude/README.md content
└── server/                      # deployed to the Oracle VM
    ├── docker-compose.yml
    ├── Caddyfile
    ├── couchdb-config/local.ini
    ├── .env.example
    └── DEPLOY.md                # step-by-step VM provisioning + deploy guide
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/tesseract_mcp/__init__.py`
- Create: `tests/test_sanity.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "tesseract-mcp"
version = "0.1.0"
description = "MCP server for the Tesseract Obsidian mind database"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.2.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
tesseract-mcp = "tesseract_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tesseract_mcp"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Create `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
dist/
*.egg-info/
server/.env
```

- [ ] **Step 3: Create `src/tesseract_mcp/__init__.py`**

```python
"""MCP server for the Tesseract Obsidian mind database."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Create `tests/test_sanity.py`**

```python
import tesseract_mcp


def test_package_imports():
    assert tesseract_mcp.__version__
```

- [ ] **Step 5: Create venv, install, run tests**

Run (PowerShell, from repo root):
```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest -v
```
Expected: `1 passed`

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml .gitignore src tests
git commit -m "chore: scaffold tesseract-mcp package"
```

---

### Task 2: Vault primitives (`vault.py`)

The safety core: path validation (no escaping the vault), read, write with overwrite protection, append, and the `Claude/` write quarantine.

**Files:**
- Create: `src/tesseract_mcp/vault.py`
- Create: `tests/conftest.py`
- Create: `tests/test_vault.py`

- [ ] **Step 1: Write the fixture vault in `tests/conftest.py`**

```python
import pytest

from tesseract_mcp.vault import Vault


@pytest.fixture
def vault_dir(tmp_path):
    """A miniature Obsidian vault with human notes and a Claude/ subtree."""
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")

    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Sentinel ESG.md").write_text(
        "---\ntags: [project, esg]\n---\n\n# Sentinel ESG\n\n"
        "ESG incident ingestion pipeline with CouchDB-free architecture.\n",
        encoding="utf-8",
    )
    (tmp_path / "Daily.md").write_text(
        "# Daily\n\nRemember to check the pipeline.\n", encoding="utf-8"
    )

    claude = tmp_path / "Claude"
    (claude / "Sessions").mkdir(parents=True)
    (claude / "Inbox").mkdir()
    (claude / "Concepts").mkdir()
    (claude / "Index.md").write_text("# Index\n\n", encoding="utf-8")
    (claude / "Concepts" / "CouchDB.md").write_text(
        "---\ntags: [concept]\n---\n\n# CouchDB\n\nDocument database used for LiveSync.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def vault(vault_dir):
    return Vault(vault_dir)
```

- [ ] **Step 2: Write failing tests in `tests/test_vault.py`**

```python
import pytest

from tesseract_mcp.vault import Vault, VaultError


def test_missing_root_raises(tmp_path):
    with pytest.raises(VaultError, match="does not exist"):
        Vault(tmp_path / "nope")


def test_read_note(vault):
    assert "Remember to check" in vault.read("Daily.md")


def test_read_missing_note_raises(vault):
    with pytest.raises(VaultError, match="not found"):
        vault.read("Ghost.md")


def test_path_escape_rejected(vault):
    with pytest.raises(VaultError, match="escapes"):
        vault.read("../outside.md")


def test_write_inside_claude_allowed(vault):
    vault.write("Claude/Inbox/note.md", "hello")
    assert vault.read("Claude/Inbox/note.md") == "hello"


def test_write_outside_claude_refused_by_default(vault):
    with pytest.raises(VaultError, match="outside Claude/"):
        vault.write("Projects/New.md", "hello")


def test_write_outside_claude_with_confirmation(vault):
    vault.write("Projects/New.md", "hello", confirm_outside_claude=True)
    assert vault.read("Projects/New.md") == "hello"


def test_write_refuses_overwrite_by_default(vault):
    vault.write("Claude/Inbox/note.md", "v1")
    with pytest.raises(VaultError, match="already exists"):
        vault.write("Claude/Inbox/note.md", "v2")


def test_write_overwrite_flag(vault):
    vault.write("Claude/Inbox/note.md", "v1")
    vault.write("Claude/Inbox/note.md", "v2", overwrite=True)
    assert vault.read("Claude/Inbox/note.md") == "v2"


def test_write_creates_parent_dirs(vault):
    vault.write("Claude/Sessions/2026/deep.md", "x")
    assert vault.read("Claude/Sessions/2026/deep.md") == "x"


def test_append_creates_and_appends(vault):
    vault.append("Claude/Inbox/2026-07-05.md", "- one\n")
    vault.append("Claude/Inbox/2026-07-05.md", "- two\n")
    assert vault.read("Claude/Inbox/2026-07-05.md") == "- one\n- two\n"


def test_append_outside_claude_refused_by_default(vault):
    with pytest.raises(VaultError, match="outside Claude/"):
        vault.append("Daily.md", "- sneaky\n")


def test_in_claude(vault):
    assert vault.in_claude("Claude/Index.md")
    assert vault.in_claude("Claude/Sessions/x.md")
    assert not vault.in_claude("Daily.md")
    assert not vault.in_claude("ClaudeFake/x.md")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_vault.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` (vault module doesn't exist yet)

- [ ] **Step 4: Implement `src/tesseract_mcp/vault.py`**

```python
"""Filesystem access to the Obsidian vault with safety rules.

Two rules are enforced in code, not by convention:
- No path may escape the vault root.
- Writes outside the Claude/ subtree require confirm_outside_claude=True,
  which callers may only pass when the user explicitly asked for the write.
"""

from __future__ import annotations

from pathlib import Path


class VaultError(Exception):
    """Raised when a vault operation is invalid."""


class Vault:
    CLAUDE_DIR = "Claude"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise VaultError(f"Vault root does not exist: {self.root}")

    def resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise VaultError(f"Path escapes the vault: {relative}")
        return candidate

    def in_claude(self, relative: str) -> bool:
        path = self.resolve(relative)
        claude_root = self.root / self.CLAUDE_DIR
        return path == claude_root or claude_root in path.parents

    def read(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise VaultError(f"Note not found: {relative}")
        return path.read_text(encoding="utf-8")

    def _check_write_allowed(self, relative: str, confirm_outside_claude: bool) -> None:
        if not self.in_claude(relative) and not confirm_outside_claude:
            raise VaultError(
                f"'{relative}' is outside Claude/. Pass confirm_outside_claude=True "
                "only when the user explicitly asked for this write."
            )

    def write(
        self,
        relative: str,
        content: str,
        *,
        overwrite: bool = False,
        confirm_outside_claude: bool = False,
    ) -> Path:
        path = self.resolve(relative)
        self._check_write_allowed(relative, confirm_outside_claude)
        if path.exists() and not overwrite:
            raise VaultError(
                f"'{relative}' already exists. Pass overwrite=True to replace it."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def append(
        self,
        relative: str,
        content: str,
        *,
        confirm_outside_claude: bool = False,
    ) -> Path:
        path = self.resolve(relative)
        self._check_write_allowed(relative, confirm_outside_claude)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
        return path
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_vault.py -v`
Expected: all PASS (13 tests)

- [ ] **Step 6: Commit**

```powershell
git add src/tesseract_mcp/vault.py tests/conftest.py tests/test_vault.py
git commit -m "feat: vault primitives with path safety and Claude/ write quarantine"
```

---

### Task 3: Note operations (`notes.py`)

Frontmatter generation and the three structured write operations: session logs (with index update), inbox capture, concept upsert.

**Files:**
- Create: `src/tesseract_mcp/notes.py`
- Create: `tests/test_notes.py`

- [ ] **Step 1: Write failing tests in `tests/test_notes.py`**

```python
from datetime import datetime

from tesseract_mcp import notes

NOW = datetime(2026, 7, 5, 14, 30)


def test_safe_filename_strips_illegal_chars():
    assert notes.safe_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"


def test_safe_filename_empty_falls_back():
    assert notes.safe_filename("///") == "untitled"


def test_make_frontmatter_fields():
    fm = notes.make_frontmatter(project="sentinel", tags=["esg"], created=NOW)
    assert fm.startswith("---\n")
    assert "created: 2026-07-05 14:30" in fm
    assert "agent: claude" in fm
    assert "project: sentinel" in fm
    assert "- esg" in fm
    assert fm.endswith("---\n\n")


def test_log_session_creates_note_and_updates_index(vault):
    rel = notes.log_session(
        vault, "LiveSync setup", "We configured CouchDB.",
        project="tesseract", tags=["infra"], now=NOW,
    )
    assert rel == "Claude/Sessions/2026-07-05 LiveSync setup.md"
    body = vault.read(rel)
    assert "agent: claude" in body
    assert "We configured CouchDB." in body
    index = vault.read("Claude/Index.md")
    assert "[[2026-07-05 LiveSync setup]]" in index
    assert "tesseract" in index


def test_capture_appends_timestamped_bullet(vault):
    rel = notes.capture(vault, "check R2 pricing", now=NOW)
    assert rel == "Claude/Inbox/2026-07-05.md"
    assert vault.read(rel) == "- 14:30 check R2 pricing\n"
    notes.capture(vault, "second thought", now=NOW)
    assert vault.read(rel).count("- 14:30") == 2


def test_upsert_concept_creates_new(vault):
    rel = notes.upsert_concept(vault, "DuckDNS", "Free dynamic DNS.", now=NOW)
    assert rel == "Claude/Concepts/DuckDNS.md"
    body = vault.read(rel)
    assert body.startswith("---\n")
    assert "# DuckDNS" in body
    assert "Free dynamic DNS." in body


def test_upsert_concept_appends_to_existing(vault):
    notes.upsert_concept(vault, "CouchDB", "Used by LiveSync.", now=NOW)
    body = vault.read("Claude/Concepts/CouchDB.md")
    assert "Document database used for LiveSync." in body  # original preserved
    assert "## Update 2026-07-05" in body
    assert "Used by LiveSync." in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_notes.py -v`
Expected: FAIL — `ImportError` (notes module doesn't exist)

- [ ] **Step 3: Implement `src/tesseract_mcp/notes.py`**

```python
"""Structured note operations for the Claude/ subtree."""

from __future__ import annotations

import re
from datetime import datetime

import yaml

from .vault import Vault, VaultError

AGENT_NAME = "claude"
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')


def safe_filename(title: str) -> str:
    cleaned = _ILLEGAL.sub("", title).strip()
    return cleaned or "untitled"


def make_frontmatter(
    *,
    project: str = "",
    tags: list[str] | None = None,
    agent: str = AGENT_NAME,
    created: datetime | None = None,
) -> str:
    created = created or datetime.now()
    meta = {
        "created": created.strftime("%Y-%m-%d %H:%M"),
        "agent": agent,
        "project": project,
        "tags": tags or [],
    }
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n"


def log_session(
    vault: Vault,
    title: str,
    content: str,
    project: str,
    tags: list[str],
    now: datetime | None = None,
) -> str:
    now = now or datetime.now()
    stem = f"{now:%Y-%m-%d} {safe_filename(title)}"
    rel = f"Claude/Sessions/{stem}.md"
    vault.write(
        rel,
        make_frontmatter(project=project, tags=tags, created=now) + content + "\n",
    )
    vault.append("Claude/Index.md", f"- [[{stem}]] — {project}: {title}\n")
    return rel


def capture(vault: Vault, content: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    rel = f"Claude/Inbox/{now:%Y-%m-%d}.md"
    vault.append(rel, f"- {now:%H:%M} {content}\n")
    return rel


def upsert_concept(
    vault: Vault, name: str, content: str, now: datetime | None = None
) -> str:
    now = now or datetime.now()
    rel = f"Claude/Concepts/{safe_filename(name)}.md"
    try:
        vault.read(rel)
    except VaultError:
        vault.write(
            rel,
            make_frontmatter(tags=["concept"], created=now)
            + f"# {name}\n\n{content}\n",
        )
    else:
        vault.append(rel, f"\n## Update {now:%Y-%m-%d}\n\n{content}\n")
    return rel
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_notes.py -v`
Expected: all PASS (7 tests)

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/notes.py tests/test_notes.py
git commit -m "feat: session log, inbox capture, and concept upsert operations"
```

---

### Task 4: Search (`search.py`)

Full-text search across the vault with optional tag and folder filters. Matches note titles too. Skips `.obsidian`, `.trash`, `.git`.

**Files:**
- Create: `src/tesseract_mcp/search.py`
- Create: `tests/test_search.py`

- [ ] **Step 1: Write failing tests in `tests/test_search.py`**

```python
from tesseract_mcp.search import search


def test_finds_content_match(vault):
    hits = search(vault, "ingestion pipeline")
    assert [h.path for h in hits] == ["Projects/Sentinel ESG.md"]
    assert "ingestion" in hits[0].excerpt


def test_case_insensitive(vault):
    assert search(vault, "INGESTION PIPELINE")


def test_title_match(vault):
    hits = search(vault, "couchdb")
    assert "Claude/Concepts/CouchDB.md" in [h.path for h in hits]


def test_tag_filter(vault):
    hits = search(vault, "e", tags=["esg"])
    assert [h.path for h in hits] == ["Projects/Sentinel ESG.md"]


def test_folder_filter(vault):
    hits = search(vault, "couchdb", folder="Claude")
    assert all(h.path.startswith("Claude/") for h in hits)


def test_skips_obsidian_dir(vault):
    assert not search(vault, "{}")


def test_no_match_returns_empty(vault):
    assert search(vault, "zebra unicorn") == []


def test_limit(vault):
    assert len(search(vault, "e", limit=1)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_search.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `src/tesseract_mcp/search.py`**

```python
"""Full-text search across the vault."""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from .vault import Vault

SKIP_DIRS = {".obsidian", ".trash", ".git"}


@dataclass
class Hit:
    path: str
    excerpt: str


def _frontmatter_tags(text: str) -> list[str]:
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    try:
        meta = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return []
    if not isinstance(meta, dict):
        return []
    tags = meta.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    return [str(t) for t in tags]


def search(
    vault: Vault,
    query: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 20,
) -> list[Hit]:
    base = vault.resolve(folder) if folder else vault.root
    q = query.lower()
    hits: list[Hit] = []
    for path in sorted(base.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if tags and not set(tags) <= set(_frontmatter_tags(text)):
            continue
        rel = "/".join(rel_parts)
        if q in path.stem.lower():
            hits.append(Hit(rel, "(title match)"))
        else:
            for line in text.splitlines():
                if q in line.lower():
                    hits.append(Hit(rel, line.strip()))
                    break
        if len(hits) >= limit:
            break
    return hits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_search.py -v`
Expected: all PASS (8 tests)

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/search.py tests/test_search.py
git commit -m "feat: full-text vault search with tag and folder filters"
```

---

### Task 5: MCP server wiring (`server.py`) + README

Thin FastMCP layer over the modules above. Vault path comes from `TESSERACT_VAULT_PATH`.

**Files:**
- Create: `src/tesseract_mcp/server.py`
- Create: `tests/test_server.py`
- Create: `README.md`

- [ ] **Step 1: Write failing tests in `tests/test_server.py`**

```python
import asyncio

import pytest

from tesseract_mcp import server
from tesseract_mcp.vault import VaultError


@pytest.fixture(autouse=True)
def point_at_fixture_vault(vault_dir, monkeypatch):
    monkeypatch.setenv("TESSERACT_VAULT_PATH", str(vault_dir))
    server._vault = None  # reset cache between tests
    yield
    server._vault = None


def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "search_brain",
        "read_note",
        "log_session",
        "capture",
        "upsert_concept",
        "write_note",
    }


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("TESSERACT_VAULT_PATH")
    with pytest.raises(VaultError, match="TESSERACT_VAULT_PATH"):
        server.get_vault()


def test_search_brain_returns_dicts():
    hits = server.search_brain("ingestion pipeline")
    assert hits == [
        {
            "path": "Projects/Sentinel ESG.md",
            "excerpt": "ESG incident ingestion pipeline with CouchDB-free architecture.",
        }
    ]


def test_read_note():
    assert "Remember to check" in server.read_note("Daily.md")


def test_log_session_roundtrip():
    rel = server.log_session(
        "Test session", "Did things.", project="tesseract", tags=["test"]
    )
    assert rel.startswith("Claude/Sessions/")
    assert "Did things." in server.read_note(rel)


def test_capture_roundtrip():
    rel = server.capture("a quick thought")
    assert "a quick thought" in server.read_note(rel)


def test_upsert_concept_roundtrip():
    rel = server.upsert_concept("Testing", "Notes about testing.")
    assert "Notes about testing." in server.read_note(rel)


def test_write_note_quarantine_enforced():
    with pytest.raises(VaultError, match="outside Claude/"):
        server.write_note("Projects/Injected.md", "nope")


def test_write_note_with_confirmation():
    server.write_note("Projects/Asked For.md", "yes", confirm_outside_claude=True)
    assert server.read_note("Projects/Asked For.md") == "yes"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_server.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement `src/tesseract_mcp/server.py`**

```python
"""FastMCP server exposing the Tesseract vault to Claude."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import notes, search as search_mod
from .vault import Vault, VaultError

mcp = FastMCP("tesseract")

_vault: Vault | None = None


def get_vault() -> Vault:
    global _vault
    if _vault is None:
        root = os.environ.get("TESSERACT_VAULT_PATH")
        if not root:
            raise VaultError(
                "TESSERACT_VAULT_PATH is not set; point it at the vault folder."
            )
        _vault = Vault(root)
    return _vault


@mcp.tool()
def search_brain(
    query: str, tags: list[str] | None = None, folder: str | None = None
) -> list[dict]:
    """Full-text search across the whole vault. Optionally filter by
    frontmatter tags or restrict to a subfolder. Returns path + excerpt."""
    hits = search_mod.search(get_vault(), query, tags=tags, folder=folder)
    return [{"path": h.path, "excerpt": h.excerpt} for h in hits]


@mcp.tool()
def read_note(path: str) -> str:
    """Read a note by vault-relative path (e.g. 'Claude/Index.md')."""
    return get_vault().read(path)


@mcp.tool()
def log_session(
    title: str, content: str, project: str, tags: list[str] | None = None
) -> str:
    """Log a work session to Claude/Sessions/ and update Claude/Index.md.
    Use at the end of significant work: what we did, learned, decided."""
    return notes.log_session(
        get_vault(), title, content, project=project, tags=tags or []
    )


@mcp.tool()
def capture(content: str) -> str:
    """Append a quick timestamped thought to today's Claude/Inbox/ note."""
    return notes.capture(get_vault(), content)


@mcp.tool()
def upsert_concept(name: str, content: str) -> str:
    """Create or extend an evergreen concept note in Claude/Concepts/."""
    return notes.upsert_concept(get_vault(), name, content)


@mcp.tool()
def write_note(
    path: str,
    content: str,
    confirm_outside_claude: bool = False,
    overwrite: bool = False,
) -> str:
    """General write. Refuses paths outside Claude/ unless
    confirm_outside_claude=True — set it ONLY when the user explicitly
    asked for the write. Refuses to replace existing notes unless
    overwrite=True."""
    get_vault().write(
        path,
        content,
        overwrite=overwrite,
        confirm_outside_claude=confirm_outside_claude,
    )
    return path


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest -v`
Expected: full suite passes (sanity + vault + notes + search + server)

- [ ] **Step 5: Create `README.md`**

````markdown
# tesseract-mcp

MCP server exposing the Tesseract Obsidian vault ("the mind database") to
Claude. Operates directly on the vault filesystem; Self-hosted LiveSync
replicates changes to all machines via CouchDB.

## Install

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .
```

## Register with Claude Code

```powershell
claude mcp add --scope user tesseract `
  -e TESSERACT_VAULT_PATH=C:\Vaults\Tesseract `
  -- C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\.venv\Scripts\tesseract-mcp.exe
```

## Tools

| Tool | Purpose |
|---|---|
| `search_brain` | Full-text search, optional tag/folder filters |
| `read_note` | Read any note |
| `log_session` | Session log into `Claude/Sessions/` + index update |
| `capture` | Quick thought into `Claude/Inbox/` |
| `upsert_concept` | Evergreen notes in `Claude/Concepts/` |
| `write_note` | General write — quarantined to `Claude/` unless explicitly confirmed |

## The contract

Agents write proactively **only inside `Claude/`**. Everything else is
read-only unless the user explicitly asks. The quarantine is enforced in
code (`vault.py`), and the human-readable rules live in the vault at
`Claude/README.md`.

Server infrastructure (CouchDB + Caddy for LiveSync) lives in `server/`.
````

- [ ] **Step 6: Commit**

```powershell
git add src/tesseract_mcp/server.py tests/test_server.py README.md
git commit -m "feat: FastMCP server wiring with six vault tools"
```

---

### Task 6: Vault conventions installer + constitution

**Files:**
- Create: `vault/constitution.md`
- Create: `scripts/install_conventions.py`
- Create: `tests/test_install_conventions.py`

- [ ] **Step 1: Create `vault/constitution.md`**

```markdown
---
created: 2026-07-05
agent: claude
tags: [meta, constitution]
---

# The Claude/ Constitution

Rules for every AI agent writing to this vault. Read this before writing.

## Ownership

- Everything under `Claude/` is agent territory: write freely, following the
  structure below.
- Everything OUTSIDE `Claude/` belongs to Taimoor. Read freely; write only
  when explicitly asked, and confirm the exact path first.

## Structure

- `Claude/Inbox/` — quick captures. One note per day (`YYYY-MM-DD.md`),
  timestamped bullets.
- `Claude/Sessions/` — one note per significant work session:
  `YYYY-MM-DD <short title>.md`. Record what was done, learned, and decided.
- `Claude/Concepts/` — evergreen topic notes, one concept per note. Extend
  under dated `## Update` headings; never silently rewrite history.
- `Claude/Index.md` — map of contents. Append a line per new session note.

## Note format

- YAML frontmatter on every agent note: `created`, `agent`, `project`, `tags`.
- Use `[[wikilinks]]` to connect related notes — an unlinked note is a lost
  memory.
- Write for the reader who has no session context: full sentences, no
  transcript dumps.

## Conflict etiquette

- Never resolve LiveSync conflicts by deleting someone else's content.
- When extending an existing note, append; don't reorder or rewrite what a
  human wrote.
```

- [ ] **Step 2: Write failing tests in `tests/test_install_conventions.py`**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from install_conventions import install


def test_installs_structure(tmp_path):
    created = install(tmp_path)
    assert (tmp_path / "Claude" / "README.md").is_file()
    assert (tmp_path / "Claude" / "Inbox").is_dir()
    assert (tmp_path / "Claude" / "Sessions").is_dir()
    assert (tmp_path / "Claude" / "Concepts").is_dir()
    assert (tmp_path / "Claude" / "Index.md").is_file()
    assert "Constitution" in (tmp_path / "Claude" / "README.md").read_text(
        encoding="utf-8"
    )
    assert len(created) == 5


def test_idempotent_does_not_clobber(tmp_path):
    install(tmp_path)
    index = tmp_path / "Claude" / "Index.md"
    index.write_text("# Index\n\n- [[existing]]\n", encoding="utf-8")
    created = install(tmp_path)
    assert "existing" in index.read_text(encoding="utf-8")
    assert created == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_install_conventions.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 4: Implement `scripts/install_conventions.py`**

```python
"""Install the Claude/ conventions tree into an Obsidian vault.

Usage: python scripts/install_conventions.py C:\\Vaults\\Tesseract
Idempotent: never overwrites anything that already exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONSTITUTION = REPO_ROOT / "vault" / "constitution.md"

INDEX_SEED = "# Index\n\nMap of agent-written notes. Session entries append below.\n\n"


def install(vault_root: Path) -> list[str]:
    """Create missing pieces of the Claude/ tree. Returns what was created."""
    vault_root = Path(vault_root)
    claude = vault_root / "Claude"
    created: list[str] = []

    for folder in (claude / "Inbox", claude / "Sessions", claude / "Concepts"):
        if not folder.is_dir():
            folder.mkdir(parents=True)
            created.append(str(folder.relative_to(vault_root)))

    readme = claude / "README.md"
    if not readme.exists():
        readme.write_text(
            CONSTITUTION.read_text(encoding="utf-8"), encoding="utf-8"
        )
        created.append("Claude/README.md")

    index = claude / "Index.md"
    if not index.exists():
        index.write_text(INDEX_SEED, encoding="utf-8")
        created.append("Claude/Index.md")

    return created


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/install_conventions.py <vault-path>")
    root = Path(sys.argv[1])
    if not root.is_dir():
        sys.exit(f"Vault not found: {root}")
    made = install(root)
    print("Created:", ", ".join(made) if made else "nothing (already installed)")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_install_conventions.py -v`
Expected: all PASS (2 tests)

- [ ] **Step 6: Commit**

```powershell
git add vault/constitution.md scripts/install_conventions.py tests/test_install_conventions.py
git commit -m "feat: Claude/ conventions installer and constitution"
```

---

### Task 7: Server infrastructure files (`server/`)

Docker Compose stack for the Oracle VM: CouchDB + Caddy + DuckDNS updater, plus the LiveSync-required CouchDB config and a deploy guide. No tests — validated by YAML parse and, ultimately, by deployment (Task 9).

**Files:**
- Create: `server/docker-compose.yml`
- Create: `server/Caddyfile`
- Create: `server/couchdb-config/local.ini`
- Create: `server/.env.example`
- Create: `server/DEPLOY.md`

- [ ] **Step 1: Create `server/docker-compose.yml`**

```yaml
services:
  couchdb:
    image: couchdb:3
    restart: always
    environment:
      - COUCHDB_USER=${COUCHDB_USER}
      - COUCHDB_PASSWORD=${COUCHDB_PASSWORD}
    volumes:
      - couchdb-data:/opt/couchdb/data
      - ./couchdb-config/local.ini:/opt/couchdb/etc/local.d/livesync.ini
    expose:
      - "5984"

  caddy:
    image: caddy:2
    restart: always
    ports:
      - "80:80"
      - "443:443"
    environment:
      - DOMAIN=${DOMAIN}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      - couchdb

  duckdns:
    image: lscr.io/linuxserver/duckdns:latest
    restart: always
    environment:
      - SUBDOMAINS=${DUCKDNS_SUBDOMAIN}
      - TOKEN=${DUCKDNS_TOKEN}
      - TZ=Etc/UTC

volumes:
  couchdb-data:
  caddy-data:
  caddy-config:
```

- [ ] **Step 2: Create `server/Caddyfile`**

```
{$DOMAIN} {
	reverse_proxy couchdb:5984
}
```

- [ ] **Step 3: Create `server/couchdb-config/local.ini`**

(Settings LiveSync requires: CORS for the Obsidian app origin, large request
sizes for chunk sync, mandatory authentication.)

```ini
[couchdb]
single_node = true
max_document_size = 50000000

[chttpd]
require_valid_user = true
max_http_request_size = 4294967296
enable_cors = true

[chttpd_auth]
require_valid_user = true
authentication_redirect = /_utils/session.html

[httpd]
WWW-Authenticate = Basic realm="couchdb"
bind_address = 0.0.0.0

[cors]
origins = app://obsidian.md, capacitor://localhost, http://localhost
credentials = true
headers = accept, authorization, content-type, origin, referer
methods = GET, PUT, POST, HEAD, DELETE
max_age = 3600
```

- [ ] **Step 4: Create `server/.env.example`**

```
# Copy to .env on the VM and fill in. NEVER commit .env.
COUCHDB_USER=admin
COUCHDB_PASSWORD=CHANGE-ME-long-random
DOMAIN=YOUR-SUBDOMAIN.duckdns.org
DUCKDNS_SUBDOMAIN=YOUR-SUBDOMAIN
DUCKDNS_TOKEN=YOUR-DUCKDNS-TOKEN
```

- [ ] **Step 5: Create `server/DEPLOY.md`**

````markdown
# Deploying the sync server (Oracle Always Free)

## 1. Accounts (human)

- Oracle Cloud: https://signup.cloud.oracle.com — free tier, card required
  for identity but not charged. Pick a home region close to you.
- DuckDNS: https://www.duckdns.org — sign in, create a subdomain
  (e.g. `taimoor-brain`), note the token.

## 2. Provision the VM (human, Oracle console)

- Compute → Instances → Create instance
- Image: Ubuntu 24.04. Shape: Ampere A1 Flex (Always Free): 2 OCPU, 12 GB.
- Add your SSH public key. Create.
- Networking: in the instance's subnet security list, add ingress rules for
  TCP 80 and 443 from 0.0.0.0/0. (22 is open by default.)
- Note the public IP. In DuckDNS, point your subdomain at it.

## 3. Install Docker (SSH into the VM)

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER && newgrp docker
# Ubuntu on Oracle also runs iptables rules; open the ports:
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

## 4. Deploy the stack

```bash
git clone https://github.com/Taimoor/tesseract-mcp.git   # or scp the server/ dir
cd tesseract-mcp/server
cp .env.example .env && nano .env    # fill all values
docker compose up -d
```

## 5. Verify

```bash
docker compose ps                          # all three services Up
curl -u "$COUCHDB_USER:$COUCHDB_PASSWORD" https://$DOMAIN/_up
# expect: {"status":"ok"}
```

Then create the LiveSync database:

```bash
curl -u "$COUCHDB_USER:$COUCHDB_PASSWORD" -X PUT https://$DOMAIN/tesseract
# expect: {"ok":true}
```

## Maintenance

- `docker compose pull && docker compose up -d` occasionally for updates.
- CouchDB data lives in the `couchdb-data` volume; snapshot the VM or
  `docker run --rm -v server_couchdb-data:/d -v $PWD:/b alpine tar czf /b/couch-backup.tgz /d`
  for backups.
````

- [ ] **Step 6: Validate YAML and commit**

Run: `.venv\Scripts\python -c "import yaml; yaml.safe_load(open('server/docker-compose.yml')); print('compose ok')"`
Expected: `compose ok`

```powershell
git add server
git commit -m "feat: CouchDB+Caddy+DuckDNS compose stack and deploy guide"
```

---

### Task 8: Vault migration script

**Files:**
- Create: `scripts/migrate-vault.ps1`

- [ ] **Step 1: Create `scripts/migrate-vault.ps1`**

```powershell
# Copies the Tesseract vault out of OneDrive to C:\Vaults\Tesseract.
# Copy (not move): the OneDrive original stays frozen as a backup.
param(
    [string]$Source = "$env:USERPROFILE\OneDrive\Documents\Tesseract",
    [string]$Dest = "C:\Vaults\Tesseract"
)

if (-not (Test-Path $Source)) { Write-Error "Source not found: $Source"; exit 1 }
if (Test-Path $Dest) { Write-Error "Destination already exists: $Dest — refusing to merge."; exit 1 }

Write-Host "Copying $Source -> $Dest ..."
robocopy $Source $Dest /E /COPY:DAT /DCOPY:T /R:2 /W:2 /NFL /NDL | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy reported failure ($LASTEXITCODE)"; exit 1 }

$srcCount = (Get-ChildItem $Source -Recurse -File | Measure-Object).Count
$dstCount = (Get-ChildItem $Dest -Recurse -File | Measure-Object).Count
Write-Host "Files: source=$srcCount dest=$dstCount"
if ($srcCount -ne $dstCount) { Write-Error "File counts differ — investigate before proceeding."; exit 1 }

Write-Host ""
Write-Host "Done. Next steps (manual):"
Write-Host " 1. Open Obsidian -> 'Open folder as vault' -> $Dest"
Write-Host " 2. Verify plugins/settings loaded (they live in .obsidian inside the vault)."
Write-Host " 3. Do NOT open the OneDrive copy again; delete it after ~2 weeks."
```

- [ ] **Step 2: Dry-check the script parses**

Run: `powershell -NoProfile -Command "Get-Command -Syntax .\scripts\migrate-vault.ps1"`
Expected: prints the parameter syntax, no parse errors

- [ ] **Step 3: Commit**

```powershell
git add scripts/migrate-vault.ps1
git commit -m "feat: OneDrive vault migration script with count verification"
```

---

### Task 9: HUMAN CHECKPOINT — provision and deploy the server

No subagent. The controller pauses and walks Taimoor through `server/DEPLOY.md`:

- [ ] Oracle account created, Ampere A1 VM provisioned (Ubuntu 24.04, 2 OCPU/12 GB), ports 80/443 opened in the security list
- [ ] DuckDNS subdomain created and pointed at the VM's public IP
- [ ] Docker installed on the VM; `server/` deployed; `.env` filled
- [ ] `docker compose up -d` running; `curl https://$DOMAIN/_up` returns `{"status":"ok"}`
- [ ] `tesseract` database created via the PUT in DEPLOY.md

**Verification:** From the Windows PC: `curl.exe -u user:pass https://<domain>/_up` returns `{"status":"ok"}` over valid TLS.

---

### Task 10: HUMAN CHECKPOINT — vault migration + LiveSync configuration

No subagent for the Obsidian GUI parts. Order matters: migrate first, then configure LiveSync against the new location.

- [ ] Close Obsidian. Run `scripts/migrate-vault.ps1` (agent may run this with the user watching; it refuses to run if the destination exists)
- [ ] Run `python scripts/install_conventions.py C:\Vaults\Tesseract` to install the `Claude/` tree
- [ ] Open Obsidian → "Open folder as vault" → `C:\Vaults\Tesseract`; verify plugins and settings survived
- [ ] In LiveSync settings → Setup wizard → manual setup: Remote type **CouchDB**, URI `https://<domain>`, username/password from `.env`, database `tesseract`
- [ ] Enable **End-to-end encryption** with a strong passphrase (record it in a password manager — losing it means rebuilding the database)
- [ ] "Check database configuration" — LiveSync will offer to fix any CouchDB settings it needs; accept
- [ ] Run initial sync ("Rebuild everything → Overwrite remote" for a fresh database); wait for completion
- [ ] Command palette → "Copy current settings as a new setup URI" → save the Setup URI + its passphrase in a password manager. **This is the string the plugin dialog asks for on every new machine.**
- [ ] Enable periodic database cleanup in LiveSync settings (keeps revision bloat under control)

**Verification:** Edit a note on the PC; see the change land in CouchDB (LiveSync log shows the push, no errors in the log pane).

---

### Task 11: Register tesseract-mcp with Claude Code and verify live

**Files:** none (configuration + live verification)

- [ ] **Step 1: Install and register**

```powershell
cd C:\Users\Taimoor\Documents\GitHub\tesseract-mcp
.venv\Scripts\pip install -e .
claude mcp add --scope user tesseract `
  -e TESSERACT_VAULT_PATH=C:\Vaults\Tesseract `
  -- C:\Users\Taimoor\Documents\GitHub\tesseract-mcp\.venv\Scripts\tesseract-mcp.exe
```

- [ ] **Step 2: Verify registration**

Run: `claude mcp list`
Expected: `tesseract` listed with the env var

- [ ] **Step 3: Live smoke test (in a fresh Claude Code session or via `claude mcp get tesseract`)**

- `search_brain("constitution")` returns `Claude/README.md`
- `capture("mind database is live")` creates today's inbox note
- Confirm the inbox note appears inside Obsidian (proving the MCP→vault→Obsidian loop)
- `write_note("Daily.md", ...)` WITHOUT `confirm_outside_claude` is refused (quarantine holds in production)

- [ ] **Step 4: Log the first session note**

Use `log_session` to record this setup session — the brain's first real memory. Verify `Claude/Index.md` gained a line and LiveSync pushed it (check the LiveSync log).

---

### Task 12: HUMAN CHECKPOINT — enroll a second computer, prove the loop

- [ ] On the second computer: install Obsidian, create an empty folder, open it as a vault, install Self-hosted LiveSync from Community plugins
- [ ] When the "Enter Setup URI" dialog appears (the one from the original screenshot): paste the Setup URI + passphrase saved in Task 10 → "Test Settings and Continue" → follow the wizard, choose to fetch everything from the remote
- [ ] Wait for initial replication to complete; verify note counts look right
- [ ] Edit a note on machine A → appears on machine B within seconds; edit on B → appears on A
- [ ] (Optional, if Claude Code runs on machine B) clone tesseract-mcp there, register it with that machine's vault path

**Verification:** Bidirectional edit propagation observed; `Claude/` tree present on both machines.

---

## Final review

After all tasks: dispatch a final code reviewer over the whole repo (`git log --oneline` + full diff against the root commit), then use superpowers:finishing-a-development-branch. The repo has no remote yet — offer to create a GitHub repo (`gh repo create`) since DEPLOY.md's clone step assumes one exists (private repo recommended; the compose stack contains no secrets, but private is safer).
