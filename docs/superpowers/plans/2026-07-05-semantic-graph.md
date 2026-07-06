# Tesseract Semantic Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GitNexus-style GraphRAG over the Tesseract vault — LLM-extracted typed entities materialized as markdown in `Claude/Graph/` plus a rebuildable SQLite cache, exposed through four new MCP tools (`index_brain`, `find_entity`, `related_notes`, `graph_stats`).

**Architecture:** Four new modules in the existing tesseract-mcp package. `extractor.py` runs an extraction prompt through a pluggable CLI backend (`codex exec` default, `claude -p` fallback) and coerces the reply into fixed entity/relation vocabularies. `graphstore.py` writes/merges entity notes (markdown is the source of truth; idempotent appends only). `cache.py` rebuilds a SQLite index from the entity notes (atomic swap) and answers queries. `indexer.py` orchestrates: hash-diff manifest → extract changed notes → apply to store → rebuild cache; shared by the MCP tool and a CLI entry point for scheduled sweeps. Machine-local state lives in `~/.tesseract-mcp/` (env-overridable), never in the vault.

**Tech Stack:** Python 3.11+, stdlib only for new code (subprocess, sqlite3, hashlib, json, argparse); existing deps (pyyaml, mcp) unchanged. pytest with a `FakeExtractor` — no real LLM calls in tests.

**Spec:** `docs/superpowers/specs/2026-07-05-semantic-graph-design.md`
**Repo:** `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp` (suite currently 75 passed; run everything with `.venv\Scripts\python`)

---

## File structure

```
src/tesseract_mcp/
├── extractor.py      # prompt, JSON parse+coerce, CliExtractor (codex/claude), ExtractorError
├── graphstore.py     # entity notes in Claude/Graph/: upsert, mention, relation, apply
├── cache.py          # SQLite rebuild from Claude/Graph markdown + queries
└── indexer.py        # manifest, hash-diff, batch run, CLI entry point
tests/
├── test_extractor.py
├── test_graphstore.py
├── test_cache.py
├── test_indexer.py
└── test_server.py    # + four tool tests (modify)
```

---

### Task G1: Extraction engine (`extractor.py`)

**Files:**
- Create: `src/tesseract_mcp/extractor.py`
- Create: `tests/test_extractor.py`

- [ ] **Step 1: Write failing tests in `tests/test_extractor.py`**

```python
import json

import pytest

from tesseract_mcp.extractor import (
    ENTITY_TYPES,
    RELATIONS,
    CliExtractor,
    Extraction,
    ExtractorError,
    _coerce,
)

GOOD = {
    "entities": [
        {"name": "Acme Corp", "type": "organization", "aliases": ["ACME"], "summary": "A company."},
        {"name": "Supply Chain", "type": "domain", "aliases": [], "summary": "Logistics domain."},
    ],
    "relations": [
        {"from": "Acme Corp", "from_type": "organization", "rel": "operates_in",
         "to": "Supply Chain", "to_type": "domain", "evidence": "Acme runs logistics."},
    ],
}


class FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def make_runner(outputs):
    """Returns a runner that pops canned FakeProcs; records invocations."""
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return outputs.pop(0)

    runner.calls = calls
    return runner


def test_coerce_valid_passthrough():
    ex = _coerce(GOOD)
    assert isinstance(ex, Extraction)
    assert ex.entities[0]["name"] == "Acme Corp"
    assert ex.relations[0]["rel"] == "operates_in"


def test_coerce_unknown_types_fold_to_topic_and_related_to():
    raw = {
        "entities": [{"name": "X", "type": "spaceship", "aliases": None, "summary": ""}],
        "relations": [{"from": "X", "from_type": "spaceship", "rel": "zaps",
                       "to": "Y", "to_type": "alien", "evidence": ""}],
    }
    ex = _coerce(raw)
    assert ex.entities[0]["type"] == "topic"
    assert ex.relations[0]["rel"] == "related_to"
    assert ex.relations[0]["from_type"] == "topic" and ex.relations[0]["to_type"] == "topic"


def test_coerce_drops_nameless():
    ex = _coerce({"entities": [{"name": " ", "type": "person"}], "relations": [{"from": "", "to": "Y"}]})
    assert ex.entities == [] and ex.relations == []


def test_extract_happy_path_uses_backend_command():
    runner = make_runner([FakeProc(stdout=json.dumps(GOOD))])
    ex = CliExtractor(backend="codex", runner=runner).extract("Note.md", "content")
    assert ex.entities and runner.calls[0][:2] == ["codex", "exec"]


def test_extract_claude_backend_command():
    runner = make_runner([FakeProc(stdout=json.dumps(GOOD))])
    CliExtractor(backend="claude", runner=runner).extract("Note.md", "content")
    assert runner.calls[0][:2] == ["claude", "-p"]


def test_extract_parses_json_with_surrounding_prose():
    out = "Sure! Here is the JSON:\n" + json.dumps(GOOD) + "\nHope that helps."
    runner = make_runner([FakeProc(stdout=out)])
    ex = CliExtractor(backend="codex", runner=runner).extract("N.md", "c")
    assert len(ex.entities) == 2


def test_extract_retries_once_then_succeeds():
    runner = make_runner([FakeProc(stdout="not json at all"), FakeProc(stdout=json.dumps(GOOD))])
    ex = CliExtractor(backend="codex", runner=runner).extract("N.md", "c")
    assert len(runner.calls) == 2 and ex.entities


def test_extract_fails_after_second_bad_reply():
    runner = make_runner([FakeProc(stdout="junk"), FakeProc(stdout="more junk")])
    with pytest.raises(ExtractorError):
        CliExtractor(backend="codex", runner=runner).extract("N.md", "c")


def test_nonzero_exit_raises():
    runner = make_runner([FakeProc(stdout="", returncode=1, stderr="boom")])
    with pytest.raises(ExtractorError, match="boom"):
        CliExtractor(backend="codex", runner=runner).extract("N.md", "c")


def test_unknown_backend_rejected():
    with pytest.raises(ExtractorError, match="Unknown backend"):
        CliExtractor(backend="gpt9000")


def test_backend_from_env(monkeypatch):
    monkeypatch.setenv("TESSERACT_EXTRACTOR", "claude")
    assert CliExtractor().backend == "claude"


def test_vocabularies():
    assert "organization" in ENTITY_TYPES and "related_to" in RELATIONS
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/test_extractor.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement `src/tesseract_mcp/extractor.py`**

```python
"""LLM entity extraction via pluggable CLI backends (codex / claude)."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

ENTITY_TYPES = {"person", "organization", "domain", "topic", "project", "source"}
RELATIONS = {"mentions", "works_at", "part_of", "operates_in", "about", "related_to"}

PROMPT_TEMPLATE = """You are an entity-extraction engine for a personal knowledge vault.
Read the note below and extract entities and relationships.

Entity types (use EXACTLY one of): person, organization, domain, topic, project, source.
Relation types (use EXACTLY one of): mentions, works_at, part_of, operates_in, about, related_to.

Reply with ONLY a JSON object, no prose, matching:
{{"entities": [{{"name": str, "type": str, "aliases": [str], "summary": str}}],
  "relations": [{{"from": str, "from_type": str, "rel": str, "to": str, "to_type": str, "evidence": str}}]}}

Rules: extract only significant entities (skip generic words); summaries are one
sentence; evidence is a short quote or paraphrase from the note; relations must
connect extracted entities.

Note path: {path}
Note content:
---
{content}
---"""


class ExtractorError(Exception):
    """Raised when extraction fails after retry."""


@dataclass
class Extraction:
    entities: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)


def _coerce(raw: dict) -> Extraction:
    """Fold arbitrary extractor output into the fixed vocabularies."""
    entities = []
    for e in raw.get("entities") or []:
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        etype = str(e.get("type") or "").strip().lower()
        if etype not in ENTITY_TYPES:
            etype = "topic"
        aliases = [str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()]
        entities.append(
            {"name": name, "type": etype, "aliases": aliases,
             "summary": str(e.get("summary") or "").strip()}
        )
    relations = []
    for r in raw.get("relations") or []:
        src = str(r.get("from") or "").strip()
        dst = str(r.get("to") or "").strip()
        if not src or not dst:
            continue
        rel = str(r.get("rel") or "").strip().lower()
        if rel not in RELATIONS:
            rel = "related_to"
        from_type = str(r.get("from_type") or "").strip().lower()
        to_type = str(r.get("to_type") or "").strip().lower()
        relations.append(
            {"from": src,
             "from_type": from_type if from_type in ENTITY_TYPES else "topic",
             "rel": rel,
             "to": dst,
             "to_type": to_type if to_type in ENTITY_TYPES else "topic",
             "evidence": str(r.get("evidence") or "").strip()}
        )
    return Extraction(entities, relations)


class CliExtractor:
    COMMANDS = {"codex": ["codex", "exec"], "claude": ["claude", "-p"]}

    def __init__(self, backend: str | None = None, timeout: int = 120, runner=subprocess.run):
        self.backend = backend or os.environ.get("TESSERACT_EXTRACTOR", "codex")
        if self.backend not in self.COMMANDS:
            raise ExtractorError(f"Unknown backend: {self.backend}")
        self.timeout = timeout
        self._run = runner

    def _invoke(self, prompt: str) -> str:
        cmd = self.COMMANDS[self.backend] + [prompt]
        proc = self._run(
            cmd, capture_output=True, text=True, timeout=self.timeout, encoding="utf-8"
        )
        if proc.returncode != 0:
            raise ExtractorError(
                f"{self.backend} exited {proc.returncode}: {(proc.stderr or '')[:300]}"
            )
        return proc.stdout or ""

    @staticmethod
    def _parse(output: str) -> dict:
        start, end = output.find("{"), output.rfind("}")
        if start == -1 or end <= start:
            raise ExtractorError("no JSON object in extractor output")
        return json.loads(output[start : end + 1])

    def extract(self, path: str, content: str) -> Extraction:
        prompt = PROMPT_TEMPLATE.format(path=path, content=content)
        out = self._invoke(prompt)
        try:
            return _coerce(self._parse(out))
        except (ExtractorError, json.JSONDecodeError):
            repair = prompt + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
            out = self._invoke(repair)
            try:
                return _coerce(self._parse(out))
            except json.JSONDecodeError as e:
                raise ExtractorError(f"invalid JSON after retry: {e}") from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_extractor.py -v`
Expected: 12 PASS. Full suite: 87 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/extractor.py tests/test_extractor.py
git commit -m "feat(graph): pluggable CLI entity extractor with vocabulary coercion"
```

---

### Task G2: Graph store (`graphstore.py`)

Entity notes under `Claude/Graph/<TypePlural>/`, idempotent merge.

**Files:**
- Create: `src/tesseract_mcp/graphstore.py`
- Create: `tests/test_graphstore.py`

- [ ] **Step 1: Write failing tests in `tests/test_graphstore.py`**

```python
from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GRAPH_ROOT, GraphStore, entity_rel_path

ACME = {"name": "Acme Corp", "type": "organization", "aliases": ["ACME"], "summary": "A company."}
CHAIN = {"name": "Supply Chain", "type": "domain", "aliases": [], "summary": "Logistics."}
REL = {"from": "Acme Corp", "from_type": "organization", "rel": "operates_in",
       "to": "Supply Chain", "to_type": "domain", "evidence": "Acme runs logistics."}


def test_entity_rel_path():
    assert entity_rel_path("organization", "Acme Corp") == "Claude/Graph/Organizations/Acme Corp.md"
    assert entity_rel_path("person", 'Bad:Name?') == "Claude/Graph/People/BadName.md"


def test_upsert_creates_note(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    body = vault.read(rel)
    assert "entity: organization" in body
    assert "# Acme Corp" in body and "A company." in body
    assert "## Mentions" in body and "## Relations" in body
    assert "ACME" in body  # alias in frontmatter


def test_upsert_existing_merges_aliases_only(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    before = vault.read(rel)
    rel2 = store.upsert_entity({**ACME, "aliases": ["ACME", "Acme Inc"], "summary": "Different."})
    assert rel2 == rel
    after = vault.read(rel)
    assert "Acme Inc" in after
    assert "A company." in after and "Different." not in after  # summary not rewritten


def test_find_by_alias_casefold(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    assert store.find_entity_note("organization", "acme") == rel
    assert store.find_entity_note("organization", "ACME CORP") == rel
    assert store.find_entity_note("organization", "Unknown Co") is None


def test_add_mention_idempotent(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    assert store.add_mention(rel, "Projects/Sentinel ESG.md", "mentioned in pipeline") is True
    assert store.add_mention(rel, "Projects/Sentinel ESG.md", "again") is False
    body = vault.read(rel)
    assert body.count("[[Sentinel ESG]]") == 1
    assert "mentioned in pipeline" in body


def test_add_relation_idempotent(vault):
    store = GraphStore(vault)
    a = store.upsert_entity(ACME)
    b = store.upsert_entity(CHAIN)
    assert store.add_relation(a, "operates_in", b) is True
    assert store.add_relation(a, "operates_in", b) is False
    assert "- operates_in [[Supply Chain]]" in vault.read(a)


def test_apply_full_extraction(vault):
    store = GraphStore(vault)
    counts = store.apply("Projects/Sentinel ESG.md", Extraction([ACME, CHAIN], [REL]))
    assert counts == {"entities_created": 2, "entities_merged": 0,
                      "mentions_added": 2, "relations_added": 1}
    acme = vault.read(entity_rel_path("organization", "Acme Corp"))
    assert "[[Sentinel ESG]]" in acme
    assert "- operates_in [[Supply Chain]]" in acme


def test_apply_twice_is_idempotent(vault):
    store = GraphStore(vault)
    store.apply("Daily.md", Extraction([ACME], []))
    counts = store.apply("Daily.md", Extraction([ACME], []))
    assert counts["entities_created"] == 0
    assert counts["mentions_added"] == 0


def test_relation_entity_not_extracted_gets_stub(vault):
    """A relation endpoint that wasn't in entities[] still gets an entity note."""
    store = GraphStore(vault)
    counts = store.apply("Daily.md", Extraction([ACME], [REL]))
    assert vault.resolve(entity_rel_path("domain", "Supply Chain")).exists()
    assert counts["entities_created"] == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/test_graphstore.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement `src/tesseract_mcp/graphstore.py`**

```python
"""Markdown-native graph store: entity notes under Claude/Graph/."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from .extractor import Extraction
from .notes import AGENT_NAME, safe_filename
from .search import parse_frontmatter
from .vault import Vault, VaultError

GRAPH_ROOT = "Claude/Graph"
TYPE_FOLDERS = {
    "person": "People",
    "organization": "Organizations",
    "domain": "Domains",
    "topic": "Topics",
    "project": "Projects",
    "source": "Sources",
}
MENTIONS_HEADER = "## Mentions"
RELATIONS_HEADER = "## Relations"


def entity_rel_path(etype: str, name: str) -> str:
    return f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{safe_filename(name)}.md"


def _note_template(ent: dict, now: datetime) -> str:
    meta = {
        "created": now.strftime("%Y-%m-%d %H:%M"),
        "agent": AGENT_NAME,
        "entity": ent["type"],
        "aliases": ent.get("aliases") or [],
        "tags": [f"graph/{ent['type']}"],
    }
    fm = "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n"
    summary = ent.get("summary") or ""
    return (
        fm
        + f"# {ent['name']}\n\n"
        + (summary + "\n\n" if summary else "")
        + f"{MENTIONS_HEADER}\n\n{RELATIONS_HEADER}\n"
    )


class GraphStore:
    def __init__(self, vault: Vault):
        self.vault = vault

    def find_entity_note(self, etype: str, name: str) -> str | None:
        folder = self.vault.resolve(f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}")
        if not folder.is_dir():
            return None
        needle = name.casefold()
        for p in sorted(folder.glob("*.md")):
            if p.stem.casefold() == needle or safe_filename(name).casefold() == p.stem.casefold():
                return f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{p.name}"
            meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
            aliases = meta.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = [aliases]
            if needle in {str(a).casefold() for a in aliases}:
                return f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{p.name}"
        return None

    def upsert_entity(self, ent: dict, now: datetime | None = None) -> tuple[str, bool] | str:
        """Create or merge an entity note. Returns the rel path (created flag
        available via upsert_entity_ex)."""
        rel, _created = self.upsert_entity_ex(ent, now=now)
        return rel

    def upsert_entity_ex(self, ent: dict, now: datetime | None = None) -> tuple[str, bool]:
        now = now or datetime.now()
        existing = self.find_entity_note(ent["type"], ent["name"])
        if existing is None:
            rel = entity_rel_path(ent["type"], ent["name"])
            self.vault.write(rel, _note_template(ent, now))
            return rel, True
        # merge new aliases into frontmatter (append-only semantics)
        new_aliases = [a for a in (ent.get("aliases") or []) if a]
        if new_aliases:
            text = self.vault.read(existing)
            meta = parse_frontmatter(text)
            current = meta.get("aliases") or []
            if not isinstance(current, list):
                current = [current]
            known = {str(a).casefold() for a in current} | {ent["name"].casefold()}
            added = [a for a in new_aliases if a.casefold() not in known]
            if added:
                meta["aliases"] = [str(a) for a in current] + added
                end = text.find("\n---", 3)
                body = text[end + 4 :]
                fm = "---\n" + yaml.safe_dump(meta, sort_keys=False, default_flow_style=None) + "---"
                self.vault.write(existing, fm + body, overwrite=True)
        return existing, False

    def _insert_line(self, rel: str, header: str, line: str, already: str) -> bool:
        text = self.vault.read(rel)
        start = text.find(header)
        if start == -1:  # section missing (human deleted it) — recreate at end
            text = text.rstrip() + f"\n\n{header}\n"
            start = text.find(header)
        next_header = text.find("\n## ", start + len(header))
        section = text[start : next_header if next_header != -1 else len(text)]
        if already in section:
            return False
        insert_at = next_header if next_header != -1 else len(text)
        updated = text[:insert_at].rstrip() + "\n" + line + "\n" + (
            text[insert_at:] if next_header != -1 else ""
        )
        self.vault.write(rel, updated, overwrite=True)
        return True

    def add_mention(self, entity_rel: str, note_path: str, evidence: str) -> bool:
        stem = Path(note_path).stem
        line = f"- [[{stem}]]" + (f" — {evidence}" if evidence else "")
        return self._insert_line(entity_rel, MENTIONS_HEADER, line, f"[[{stem}]]")

    def add_relation(self, src_rel: str, relation: str, dst_rel: str) -> bool:
        dst_stem = Path(dst_rel).stem
        line = f"- {relation} [[{dst_stem}]]"
        return self._insert_line(src_rel, RELATIONS_HEADER, line, line)

    def apply(self, note_path: str, extraction: Extraction) -> dict:
        counts = {"entities_created": 0, "entities_merged": 0,
                  "mentions_added": 0, "relations_added": 0}
        paths: dict[tuple[str, str], str] = {}

        def ensure(name: str, etype: str, ent: dict | None = None) -> str:
            key = (etype, name.casefold())
            if key not in paths:
                rel, created = self.upsert_entity_ex(
                    ent or {"name": name, "type": etype, "aliases": [], "summary": ""}
                )
                paths[key] = rel
                counts["entities_created" if created else "entities_merged"] += 1
            return paths[key]

        for ent in extraction.entities:
            rel = ensure(ent["name"], ent["type"], ent)
            if self.add_mention(rel, note_path, ent.get("summary", "")):
                counts["mentions_added"] += 1
        for r in extraction.relations:
            src = ensure(r["from"], r["from_type"])
            dst = ensure(r["to"], r["to_type"])
            if self.add_relation(src, r["rel"], dst):
                counts["relations_added"] += 1
        return counts
```

- [ ] **Step 4: Run tests, fix only real mismatches, re-run**

Run: `.venv\Scripts\python -m pytest tests/test_graphstore.py -v`
Expected: 9 PASS. NOTE the `apply` counting semantics the tests pin: `entities_merged` counts an `ensure()` hit on an EXISTING note (upsert of already-present entity), and `test_apply_twice_is_idempotent` expects `entities_created == 0` and `mentions_added == 0` on the second run (the second `apply` finds the note → merged, mention already present → False). `test_apply_full_extraction` expects `mentions_added == 2` because each extracted entity gets a mention from the source note.

Full suite: 96 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/graphstore.py tests/test_graphstore.py
git commit -m "feat(graph): markdown entity store with idempotent merge"
```

---

### Task G3: Query cache (`cache.py`)

**Files:**
- Create: `src/tesseract_mcp/cache.py`
- Create: `tests/test_cache.py`

- [ ] **Step 1: Write failing tests in `tests/test_cache.py`**

```python
import json
import sqlite3

import pytest

from tesseract_mcp import cache
from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GraphStore

ACME = {"name": "Acme Corp", "type": "organization", "aliases": ["ACME"], "summary": "A company."}
CHAIN = {"name": "Supply Chain", "type": "domain", "aliases": [], "summary": "Logistics."}
REL = {"from": "Acme Corp", "from_type": "organization", "rel": "operates_in",
       "to": "Supply Chain", "to_type": "domain", "evidence": "logistics"}


@pytest.fixture
def populated(vault, tmp_path):
    store = GraphStore(vault)
    store.apply("Projects/Sentinel ESG.md", Extraction([ACME, CHAIN], [REL]))
    vault.write("Claude/Inbox/interview.md", "Talked to [[Acme Corp]] folks.\n")
    store.apply("Claude/Inbox/interview.md", Extraction([ACME], []))
    db = tmp_path / "graph.db"
    cache.rebuild(vault, db)
    return db


def test_rebuild_creates_tables(populated):
    con = sqlite3.connect(populated)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entities", "edges", "mentions"} <= names


def test_find_entity_by_name_and_alias(populated):
    got = cache.find_entity(populated, "acme")
    assert got and got[0]["name"] == "Acme Corp" and got[0]["type"] == "organization"
    assert cache.find_entity(populated, "ACME")  # alias
    assert got[0]["mention_count"] == 2
    assert {"rel": "operates_in", "to": "Supply Chain"} in [
        {"rel": e["rel"], "to": e["dst"]} for e in got[0]["relations"]
    ]


def test_find_entity_type_filter(populated):
    assert cache.find_entity(populated, "supply", type="domain")
    assert cache.find_entity(populated, "supply", type="person") == []


def test_related_notes_shared_entity(populated, vault):
    got = cache.related_notes(populated, vault, "Claude/Inbox/interview.md", hops=1)
    paths = [r["path"] for r in got]
    assert "Projects/Sentinel ESG.md" in paths
    assert any("Acme Corp" in r["via"] for r in got)


def test_related_notes_excludes_self_and_graph_notes(populated, vault):
    got = cache.related_notes(populated, vault, "Claude/Inbox/interview.md", hops=2)
    paths = [r["path"] for r in got]
    assert "Claude/Inbox/interview.md" not in paths
    assert not any(p.startswith("Claude/Graph/") for p in paths)


def test_stats(populated):
    s = cache.stats(populated)
    assert s["entities"]["organization"] == 1
    assert s["entities"]["domain"] == 1
    assert s["edges"] == 1
    assert s["mentions"] == 3


def test_rebuild_atomic_replaces(populated, vault, tmp_path):
    db = populated
    cache.rebuild(vault, db)  # second rebuild over existing db must not error
    assert cache.find_entity(db, "acme")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/test_cache.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement `src/tesseract_mcp/cache.py`**

```python
"""Derived SQLite cache over the Claude/Graph markdown (rebuildable anytime)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from .graphstore import GRAPH_ROOT, MENTIONS_HEADER, RELATIONS_HEADER
from .search import SKIP_DIRS, parse_frontmatter
from .vault import Vault

_MENTION = re.compile(r"^- \[\[([^\]|]+)\]\](?:\s+—\s+(.*))?$")
_RELATION = re.compile(r"^- (\w+) \[\[([^\]|]+)\]\]$")

SCHEMA = """
CREATE TABLE entities (name TEXT, type TEXT, path TEXT, summary TEXT, aliases TEXT);
CREATE TABLE edges (src TEXT, rel TEXT, dst TEXT);
CREATE TABLE mentions (entity TEXT, note_path TEXT, evidence TEXT);
"""


def _section(text: str, header: str) -> str:
    start = text.find(header)
    if start == -1:
        return ""
    nxt = text.find("\n## ", start + len(header))
    return text[start : nxt if nxt != -1 else len(text)]


def _stem_index(vault: Vault) -> dict[str, str]:
    """casefolded stem -> vault-relative path, for resolving wikilinks."""
    index: dict[str, str] = {}
    for path in vault.root.rglob("*.md"):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        index.setdefault(path.stem.casefold(), "/".join(rel_parts))
    return index


def rebuild(vault: Vault, db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    stems = _stem_index(vault)
    graph_dir = vault.resolve(GRAPH_ROOT)
    if graph_dir.is_dir():
        for p in sorted(graph_dir.rglob("*.md")):
            text = p.read_text(encoding="utf-8", errors="ignore")
            meta = parse_frontmatter(text)
            etype = str(meta.get("entity") or "topic")
            name = p.stem
            m = re.search(r"^# (.+)$", text, re.MULTILINE)
            if m:
                name = m.group(1).strip()
            aliases = meta.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = [aliases]
            body_after_h1 = text.split("\n# ", 1)[-1]
            summary = ""
            for line in body_after_h1.splitlines()[1:]:
                if line.strip() and not line.startswith("#"):
                    summary = line.strip()
                    break
            rel_path = "/".join(p.relative_to(vault.root).parts)
            con.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?)",
                (name, etype, rel_path, summary, json.dumps([str(a) for a in aliases])),
            )
            for line in _section(text, MENTIONS_HEADER).splitlines():
                mm = _MENTION.match(line.strip())
                if mm:
                    note = stems.get(mm.group(1).strip().casefold(), mm.group(1).strip())
                    con.execute(
                        "INSERT INTO mentions VALUES (?,?,?)",
                        (name, note, mm.group(2) or ""),
                    )
            for line in _section(text, RELATIONS_HEADER).splitlines():
                rm = _RELATION.match(line.strip())
                if rm:
                    con.execute(
                        "INSERT INTO edges VALUES (?,?,?)",
                        (name, rm.group(1), rm.group(2).strip()),
                    )
    con.commit()
    con.close()
    os.replace(tmp, db_path)


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def find_entity(db_path: Path, query: str, type: str | None = None) -> list[dict]:
    con = _connect(db_path)
    q = query.casefold()
    results = []
    for row in con.execute("SELECT * FROM entities"):
        names = [row["name"]] + json.loads(row["aliases"])
        if not any(q in n.casefold() for n in names):
            continue
        if type and row["type"] != type:
            continue
        relations = [
            dict(e)
            for e in con.execute(
                "SELECT rel, dst FROM edges WHERE src = ?", (row["name"],)
            )
        ]
        count = con.execute(
            "SELECT COUNT(*) FROM mentions WHERE entity = ?", (row["name"],)
        ).fetchone()[0]
        results.append(
            {"name": row["name"], "type": row["type"], "path": row["path"],
             "summary": row["summary"], "aliases": json.loads(row["aliases"]),
             "relations": relations, "mention_count": count}
        )
    con.close()
    return results


def related_notes(db_path: Path, vault: Vault, path: str, hops: int = 2) -> list[dict]:
    con = _connect(db_path)
    seed = [
        r["entity"]
        for r in con.execute("SELECT entity FROM mentions WHERE note_path = ?", (path,))
    ]
    reached: dict[str, str] = {e: e for e in seed}  # entity -> chain
    frontier = list(seed)
    for _ in range(max(0, hops - 1)):
        nxt = []
        for ent in frontier:
            for row in con.execute(
                "SELECT rel, dst FROM edges WHERE src = ? UNION SELECT rel, src FROM edges WHERE dst = ?",
                (ent, ent),
            ):
                other = row[1]
                if other not in reached:
                    reached[other] = f"{reached[ent]} ({row[0]}) {other}"
                    nxt.append(other)
        frontier = nxt
    results = []
    seen = set()
    for ent, chain in reached.items():
        for row in con.execute(
            "SELECT note_path FROM mentions WHERE entity = ?", (ent,)
        ):
            note = row["note_path"]
            if note == path or note in seen or note.startswith("Claude/Graph/"):
                continue
            seen.add(note)
            results.append({"path": note, "via": chain})
    con.close()
    return results


def stats(db_path: Path) -> dict:
    con = _connect(db_path)
    by_type: dict[str, int] = {}
    for row in con.execute("SELECT type, COUNT(*) c FROM entities GROUP BY type"):
        by_type[row["type"]] = row["c"]
    edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    mentions = con.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
    con.close()
    return {"entities": by_type, "edges": edges, "mentions": mentions}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_cache.py -v`
Expected: 7 PASS. Full suite: 103 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/cache.py tests/test_cache.py
git commit -m "feat(graph): SQLite query cache with atomic rebuild"
```

---

### Task G4: Incremental indexer (`indexer.py`)

**Files:**
- Create: `src/tesseract_mcp/indexer.py`
- Create: `tests/test_indexer.py`

- [ ] **Step 1: Write failing tests in `tests/test_indexer.py`**

```python
import json

import pytest

from tesseract_mcp import indexer
from tesseract_mcp.extractor import Extraction, ExtractorError

ACME = {"name": "Acme Corp", "type": "organization", "aliases": [], "summary": "A company."}


class FakeExtractor:
    def __init__(self, mapping=None, fail=()):
        self.mapping = mapping or {}
        self.fail = set(fail)
        self.calls = []

    def extract(self, path, content):
        self.calls.append(path)
        if path in self.fail:
            raise ExtractorError("boom")
        return self.mapping.get(path, Extraction())


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))


def test_scan_skips_graph_ignored_and_hidden(vault):
    vault.write("Claude/Graph/Topics/T.md", "x")
    (vault.root / "copilot").mkdir()
    (vault.root / "copilot" / "p.md").write_text("x", encoding="utf-8")
    scanned = indexer.scan_notes(vault)
    assert "Daily.md" in scanned
    assert not any(p.startswith("Claude/Graph/") for p in scanned)
    assert not any(p.startswith("copilot/") for p in scanned)


def test_run_processes_all_then_nothing(vault):
    fx = FakeExtractor({"Daily.md": Extraction([ACME], [])})
    counts = indexer.run(vault, fx)
    assert counts["processed"] > 0
    assert counts["entities_created"] == 1
    assert counts["remaining"] == 0
    fx2 = FakeExtractor()
    counts2 = indexer.run(vault, fx2)
    assert counts2["processed"] == 0 and fx2.calls == []


def test_run_reprocesses_changed_note(vault):
    fx = FakeExtractor()
    indexer.run(vault, fx)
    vault.write("Claude/Inbox/new.md", "fresh content")
    fx2 = FakeExtractor()
    indexer.run(vault, fx2)
    assert fx2.calls == ["Claude/Inbox/new.md"]


def test_run_force_reprocesses_everything(vault):
    indexer.run(vault, FakeExtractor())
    fx = FakeExtractor()
    counts = indexer.run(vault, fx, force=True)
    assert counts["processed"] == len(indexer.scan_notes(vault))


def test_run_batch_cap_reports_remaining(vault):
    fx = FakeExtractor()
    counts = indexer.run(vault, fx, batch=1)
    assert counts["processed"] == 1
    assert counts["remaining"] == len(indexer.scan_notes(vault)) - 1


def test_failure_recorded_and_retried_next_run(vault):
    fx = FakeExtractor(fail={"Daily.md"})
    counts = indexer.run(vault, fx)
    assert counts["failed"] == 1
    manifest = json.loads(
        (indexer.state_dir() / "manifest.json").read_text(encoding="utf-8")
    )
    assert "Daily.md" in manifest["failures"]
    fx2 = FakeExtractor()
    indexer.run(vault, fx2)
    assert "Daily.md" in fx2.calls  # failed notes retried


def test_run_rebuilds_cache(vault):
    from tesseract_mcp import cache

    indexer.run(vault, FakeExtractor({"Daily.md": Extraction([ACME], [])}))
    db = indexer.state_dir() / "graph.db"
    assert db.exists()
    assert cache.find_entity(db, "acme")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/test_indexer.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement `src/tesseract_mcp/indexer.py`**

```python
"""Incremental vault indexing: hash-diff manifest -> extract -> store -> cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from . import cache
from .extractor import CliExtractor, ExtractorError
from .graphstore import GRAPH_ROOT, GraphStore
from .search import SKIP_DIRS
from .vault import Vault

DEFAULT_IGNORE = ("copilot",)
DEFAULT_BATCH = 25


def state_dir() -> Path:
    override = os.environ.get("TESSERACT_STATE_DIR")
    d = Path(override) if override else Path.home() / ".tesseract-mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return state_dir() / "manifest.json"


def db_path() -> Path:
    return state_dir() / "graph.db"


def load_manifest() -> dict:
    p = _manifest_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"hashes": {}, "failures": {}}


def save_manifest(manifest: dict) -> None:
    _manifest_path().write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def scan_notes(vault: Vault, ignore: tuple[str, ...] = DEFAULT_IGNORE) -> dict[str, str]:
    """vault-relative path -> sha256 of content, for every indexable note."""
    hashes: dict[str, str] = {}
    for path in sorted(vault.root.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        rel = "/".join(rel_parts)
        if rel.startswith(GRAPH_ROOT + "/") or rel_parts[0] in ignore:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[rel] = digest
    return hashes


def run(
    vault: Vault,
    extractor,
    batch: int = DEFAULT_BATCH,
    force: bool = False,
    ignore: tuple[str, ...] = DEFAULT_IGNORE,
) -> dict:
    manifest = load_manifest()
    current = scan_notes(vault, ignore)
    if force:
        pending = list(current)
    else:
        pending = [
            rel
            for rel, digest in current.items()
            if manifest["hashes"].get(rel) != digest or rel in manifest["failures"]
        ]
    todo, remaining = pending[:batch], max(0, len(pending) - batch)

    store = GraphStore(vault)
    counts = {"processed": 0, "entities_created": 0, "entities_merged": 0,
              "mentions_added": 0, "relations_added": 0, "failed": 0,
              "remaining": remaining}
    for rel in todo:
        try:
            extraction = extractor.extract(rel, vault.read(rel))
        except ExtractorError as e:
            manifest["failures"][rel] = str(e)[:300]
            counts["failed"] += 1
            continue
        applied = store.apply(rel, extraction)
        for key in ("entities_created", "entities_merged", "mentions_added", "relations_added"):
            counts[key] += applied[key]
        manifest["hashes"][rel] = current[rel]
        manifest["failures"].pop(rel, None)
        counts["processed"] += 1
    save_manifest(manifest)
    cache.rebuild(vault, db_path())
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Index the vault into the semantic graph.")
    parser.add_argument("vault", help="Path to the Obsidian vault root")
    parser.add_argument("--backend", default=None, help="codex | claude")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    counts = run(
        Vault(args.vault),
        CliExtractor(backend=args.backend),
        batch=args.batch,
        force=args.force,
    )
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_indexer.py -v`
Expected: 7 PASS. Full suite: 110 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/indexer.py tests/test_indexer.py
git commit -m "feat(graph): incremental indexer with manifest, batching, and CLI"
```

---

### Task G5: MCP tools + docs

**Files:**
- Modify: `src/tesseract_mcp/server.py`
- Modify: `tests/test_server.py`
- Modify: `README.md`
- Modify: `vault/constitution.md`

- [ ] **Step 1: Add failing tests to `tests/test_server.py`**

The graph tools need isolated state; add near the top (after existing imports):

```python
@pytest.fixture(autouse=True)
def isolated_graph_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "graph-state"))
```

Update `test_all_tools_registered` to the 15-name set (add: `index_brain`, `find_entity`, `related_notes`, `graph_stats`). Then add:

```python
def test_index_and_graph_tools_roundtrip(monkeypatch):
    from tesseract_mcp.extractor import Extraction

    class FakeExtractor:
        def extract(self, path, content):
            if "Sentinel" in path:
                return Extraction(
                    [{"name": "Acme Corp", "type": "organization", "aliases": [], "summary": "Co."}],
                    [],
                )
            return Extraction()

    monkeypatch.setattr(server, "_make_extractor", lambda: FakeExtractor())
    counts = server.index_brain()
    assert counts["processed"] > 0 and counts["entities_created"] == 1

    found = server.find_entity("acme")
    assert found and found[0]["type"] == "organization"

    related = server.related_notes("Projects/Sentinel ESG.md")
    assert isinstance(related, list)

    s = server.graph_stats()
    assert s["entities"]["organization"] == 1


def test_graph_tools_without_cache_raise_helpful_error():
    with pytest.raises(VaultError, match="index_brain"):
        server.find_entity("anything")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/test_server.py -v`
Expected: registration test FAILS (11 != 15) and new tests fail (missing attributes)

- [ ] **Step 3: Implement in `src/tesseract_mcp/server.py`**

Imports: add `from . import cache as cache_mod, indexer` and `from .extractor import CliExtractor`. Then:

```python
def _make_extractor():
    return CliExtractor()


def _graph_db():
    db = indexer.db_path()
    if not db.exists():
        raise VaultError("Graph cache not built yet — run index_brain first.")
    return db


@mcp.tool()
def index_brain(force: bool = False) -> dict:
    """Index new/changed vault notes into the semantic graph (LLM entity
    extraction via the configured CLI backend). Returns counts including
    'remaining' — call again if remaining > 0. force=True re-indexes all."""
    return indexer.run(get_vault(), _make_extractor(), force=force)


@mcp.tool()
def find_entity(query: str, type: str | None = None) -> list[dict]:
    """Look up graph entities by name or alias (case-insensitive substring).
    Optional type filter: person, organization, domain, topic, project, source."""
    return cache_mod.find_entity(_graph_db(), query, type=type)


@mcp.tool()
def related_notes(path: str, hops: int = 2) -> list[dict]:
    """Notes connected to the given note through shared graph entities within
    N hops. Each result includes the entity chain explaining the connection —
    the GraphRAG way to gather context beyond text search."""
    return cache_mod.related_notes(_graph_db(), get_vault(), path, hops=hops)


@mcp.tool()
def graph_stats() -> dict:
    """Entity/edge/mention counts for the semantic graph."""
    return cache_mod.stats(_graph_db())
```

- [ ] **Step 4: Update `README.md` and `vault/constitution.md`**

README tool table — add four rows:

```markdown
| `index_brain` | Extract entities from new/changed notes into the semantic graph |
| `find_entity` | Look up graph entities (people, orgs, domains, topics…) by name/alias |
| `related_notes` | GraphRAG: notes connected via shared entities, with the connecting chain |
| `graph_stats` | Entity/edge/mention counts for the graph |
```

Also add a short section after "## The contract":

```markdown
## The semantic graph

`Claude/Graph/` holds LLM-extracted entity notes (People/, Organizations/,
Domains/, Topics/, Projects/, Sources/) whose wikilinks connect source notes
into a typed knowledge graph — visible in Obsidian, synced by LiveSync,
queried through a rebuildable SQLite cache in `~/.tesseract-mcp/`. Index on
demand with the `index_brain` tool or `python -m tesseract_mcp.indexer
<vault>` (extraction backend: TESSERACT_EXTRACTOR=codex|claude).
```

constitution.md — add to `## Structure`:

```markdown
- `Claude/Graph/` — the semantic graph: entity notes (People, Organizations,
  Domains, Topics, Projects, Sources) maintained by `index_brain`. Fix wrong
  facts by editing entity notes directly; the graph is markdown. Prefer
  `related_notes`/`find_entity` when gathering context for a topic.
```

- [ ] **Step 5: Full suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: 112 passed (110 + 2 new server tests; registration test updated in place)

- [ ] **Step 6: Commit**

```powershell
git add src/tesseract_mcp/server.py tests/test_server.py README.md vault/constitution.md
git commit -m "feat(graph): index_brain, find_entity, related_notes, graph_stats MCP tools"
```

---

### Task G6: Live verification (controller, not subagent)

- [ ] Sync updated constitution into the real vault (copy `vault/constitution.md` → `C:\Vaults\Tesseract\Claude\README.md`)
- [ ] Verify a real backend exists: `codex --version` (else `claude --version`, set `TESSERACT_EXTRACTOR=claude`)
- [ ] Run `python -m tesseract_mcp.indexer C:\Vaults\Tesseract --batch 5` with the real backend; inspect created `Claude/Graph/` notes for sane entities
- [ ] `find_entity` / `related_notes` / `graph_stats` smoke via direct calls
- [ ] Open Obsidian graph view — entity hub notes should appear linked to source notes
- [ ] Document the nightly sweep option: Claude Code scheduled agent invoking `index_brain`, or Task Scheduler running the CLI

---

## Self-review notes

- Spec coverage: extraction/coercion (G1), markdown store + idempotency + stubs for relation endpoints (G2), cache + atomic rebuild + queries incl. chain explanations (G3), manifest/batch/force/failures/CLI + state dir env override (G4), four tools + docs + helpful no-cache error (G5), real-backend smoke + scheduled sweep documentation (G6). No gaps found.
- Type consistency: `Extraction(entities, relations)` used across G1/G2/G4; `GraphStore.apply -> dict` keys match indexer aggregation; `indexer.state_dir()/db_path()` used by server tools; `cache.related_notes(db, vault, path, hops)` signature consistent between G3 and G5.
- Counting semantics pinned by tests: `entities_merged` = upsert hit on existing note; second `apply` of same extraction yields all-zero adds.
