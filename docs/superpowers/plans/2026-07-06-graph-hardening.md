# Graph Hardening (G7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three known graph limitations — permanent-failure retry storms (backoff), stale mentions lingering after note edits (retraction), and same-entity fragmentation across name variants (LLM consolidation pass with a `consolidate_graph` MCP tool).

**Architecture:** Backoff lives in the indexer manifest (attempt counts, skip after 3, `--force` overrides). Retraction runs inside `indexer.run`: before applying a changed note's new extraction, the previous cache DB says which entities that note used to mention, and those mention lines are removed from the entity notes. Consolidation is a new `consolidate.py`: gather all entity names/aliases → one LLM call proposes merge groups → applying a merge moves Mentions/Relations into the canonical note, folds names into aliases, and turns duplicates into redirect stubs (`merged_into` frontmatter) that the cache skips — no deletions, wikilinks stay resolvable.

**Tech Stack:** Python stdlib; existing modules (extractor/graphstore/cache/indexer/server); pytest with fake backends. Repo `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`, branch `codex/architecture-roadmap`, baseline **132 passed**. Run everything with `.venv\Scripts\python`.

**Known limitation accepted:** relations carry no per-note provenance, so retraction covers mentions only; stale relations remain until consolidation or hand-editing. Recorded here deliberately.

---

## File structure

```
src/tesseract_mcp/
├── extractor.py    # MODIFY: extract public complete_json(prompt) from extract()
├── graphstore.py   # MODIFY: remove_mention(); section-line helpers reused by consolidate
├── cache.py        # MODIFY: note_entity_paths(); rebuild() skips merged_into stubs
├── indexer.py      # MODIFY: failure backoff, retraction hook, rebuild gating
└── consolidate.py  # CREATE: gather → propose (LLM) → apply merges; CLI
src/tesseract_mcp/server.py  # MODIFY: consolidate_graph tool (15 → 16)
tests/: test_indexer.py, test_graphstore.py, test_cache.py, test_extractor.py,
        test_consolidate.py (new), test_server.py
```

---

### Task H1: Failure backoff + rebuild gating (indexer)

**Files:**
- Modify: `src/tesseract_mcp/indexer.py`
- Modify: `tests/test_indexer.py`

- [ ] **Step 1: Write failing tests** (append to `tests/test_indexer.py`; `FakeExtractor`, `ACME`, and the `isolated_state`/`vault` fixtures already exist there):

```python
def test_failure_backoff_skips_after_three_attempts(vault):
    for _ in range(3):
        indexer.run(vault, FakeExtractor(fail={"Daily.md"}))
    fx = FakeExtractor(fail={"Daily.md"})
    counts = indexer.run(vault, fx)
    assert "Daily.md" not in fx.calls          # skipped, not retried
    assert counts["skipped"] == 1


def test_force_overrides_backoff(vault):
    for _ in range(3):
        indexer.run(vault, FakeExtractor(fail={"Daily.md"}))
    fx = FakeExtractor()
    indexer.run(vault, fx, force=True)
    assert "Daily.md" in fx.calls


def test_success_clears_attempt_count(vault):
    indexer.run(vault, FakeExtractor(fail={"Daily.md"}))
    indexer.run(vault, FakeExtractor())        # succeeds now
    manifest = indexer.load_manifest()
    assert "Daily.md" not in manifest["failures"]


def test_old_string_failure_format_migrates(vault):
    indexer.state_dir()
    indexer.save_manifest({"hashes": {}, "failures": {"Daily.md": "old error"}})
    manifest = indexer.load_manifest()
    assert manifest["failures"]["Daily.md"]["attempts"] == 1


def test_rebuild_skipped_when_nothing_processed(vault, monkeypatch):
    indexer.run(vault, FakeExtractor())        # first run indexes everything
    calls = []
    from tesseract_mcp import cache
    monkeypatch.setattr(cache, "rebuild", lambda v, p: calls.append(1))
    indexer.run(vault, FakeExtractor())        # no changes -> no rebuild
    assert calls == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv\Scripts\python -m pytest tests/test_indexer.py -v`
Expected: the 5 new tests FAIL (`skipped` key missing; attempts format missing; rebuild always called).

- [ ] **Step 3: Implement in `src/tesseract_mcp/indexer.py`**

Add constant near `DEFAULT_BATCH`:

```python
MAX_ATTEMPTS = 3
```

Replace `load_manifest` with (migrates old string-valued failures):

```python
def load_manifest() -> dict:
    p = _manifest_path()
    if p.exists():
        manifest = json.loads(p.read_text(encoding="utf-8"))
    else:
        manifest = {"hashes": {}, "failures": {}}
    for rel, val in list(manifest.get("failures", {}).items()):
        if isinstance(val, str):
            manifest["failures"][rel] = {"error": val, "attempts": 1}
    return manifest
```

In `run()`, replace the `pending` computation and the loop body's failure handling, and gate the rebuild. The full updated `run()`:

```python
def run(
    vault: Vault,
    extractor,
    batch: int = DEFAULT_BATCH,
    force: bool = False,
    ignore: tuple[str, ...] = DEFAULT_IGNORE,
) -> dict:
    manifest = load_manifest()
    current = scan_notes(vault, ignore)
    skipped = 0
    if force:
        pending = list(current)
    else:
        pending = []
        for rel, digest in current.items():
            failure = manifest["failures"].get(rel)
            if failure and failure["attempts"] >= MAX_ATTEMPTS:
                skipped += 1
                continue
            if manifest["hashes"].get(rel) != digest or failure:
                pending.append(rel)
    todo, remaining = pending[:batch], max(0, len(pending) - batch)

    store = GraphStore(vault)
    counts = {"processed": 0, "entities_created": 0, "entities_merged": 0,
              "mentions_added": 0, "relations_added": 0,
              "mentions_retracted": 0, "failed": 0,
              "skipped": skipped, "remaining": remaining}
    for rel in todo:
        try:
            extraction = extractor.extract(rel, vault.read(rel))
        except ExtractorError as e:
            prev = manifest["failures"].get(rel, {"attempts": 0})
            manifest["failures"][rel] = {
                "error": str(e)[:300], "attempts": prev["attempts"] + 1
            }
            counts["failed"] += 1
            continue
        counts["mentions_retracted"] += _retract_stale_mentions(vault, store, rel)
        applied = store.apply(rel, extraction)
        for key in ("entities_created", "entities_merged", "mentions_added", "relations_added"):
            counts[key] += applied[key]
        manifest["hashes"][rel] = current[rel]
        manifest["failures"].pop(rel, None)
        counts["processed"] += 1
    save_manifest(manifest)
    if counts["processed"] or not db_path().exists():
        cache.rebuild(vault, db_path())
    return counts
```

NOTE: `_retract_stale_mentions` is defined in Task H2. For THIS task, add a stub so H1 stands alone and tests pass:

```python
def _retract_stale_mentions(vault: Vault, store: GraphStore, rel: str) -> int:
    return 0
```

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python -m pytest tests/test_indexer.py -v` → all pass (12 + 5 new = 17... report actual). Full suite: `.venv\Scripts\python -m pytest -q` → 137 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/indexer.py tests/test_indexer.py
git commit -m "feat(graph): failure backoff with attempt counts and rebuild gating"
```
Trailer: use your model's standard co-author trailer.

---

### Task H2: Stale-mention retraction

**Files:**
- Modify: `src/tesseract_mcp/cache.py` (helper), `src/tesseract_mcp/graphstore.py` (remove_mention), `src/tesseract_mcp/indexer.py` (real retraction)
- Modify: `tests/test_cache.py`, `tests/test_graphstore.py`, `tests/test_indexer.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_cache.py` (uses its existing `populated` fixture):

```python
def test_note_entity_paths(populated, vault):
    got = cache.note_entity_paths(populated, "Claude/Inbox/interview.md")
    assert got == ["Claude/Graph/Organizations/Acme Corp"]
    assert cache.note_entity_paths(populated, "Nope.md") == []
```

Append to `tests/test_graphstore.py`:

```python
def test_remove_mention(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    store.add_mention(rel, "A/Report.md", "in A")
    store.add_mention(rel, "B/Report.md", "in B")
    assert store.remove_mention(rel, "A/Report.md") is True
    body = vault.read(rel)
    assert "[[A/Report|" not in body
    assert "[[B/Report|" in body                  # other mention intact
    assert store.remove_mention(rel, "A/Report.md") is False  # idempotent
```

Append to `tests/test_indexer.py`:

```python
def test_stale_mentions_retracted_on_reprocess(vault):
    from tesseract_mcp.graphstore import entity_rel_path

    vault.write("Claude/Inbox/story.md", "About Acme.")
    fx = FakeExtractor({"Claude/Inbox/story.md": Extraction([ACME], [])})
    indexer.run(vault, fx)
    acme_rel = entity_rel_path("organization", "Acme Corp")
    assert "[[Claude/Inbox/story|" in vault.read(acme_rel)

    vault.write("Claude/Inbox/story.md", "Actually about nothing.", overwrite=True)
    counts = indexer.run(vault, FakeExtractor())   # re-extraction finds no entities
    assert counts["mentions_retracted"] == 1
    assert "[[Claude/Inbox/story|" not in vault.read(acme_rel)
```

- [ ] **Step 2: Run to verify failure** — the three new tests fail (missing functions / retraction stub returns 0).

- [ ] **Step 3: Implement**

`cache.py` — add:

```python
def note_entity_paths(db_path: Path, note_path: str) -> list[str]:
    """Entity paths (no .md) that a note currently mentions, per the cache."""
    lookup = note_path[:-3] if note_path.endswith(".md") else note_path
    con = _connect(db_path)
    rows = con.execute(
        "SELECT DISTINCT entity_path FROM mentions WHERE note_path = ?", (lookup,)
    ).fetchall()
    con.close()
    return sorted(r["entity_path"] for r in rows)
```

`graphstore.py` — add to `GraphStore`:

```python
    def remove_mention(self, entity_rel: str, note_path: str) -> bool:
        target = note_path[:-3] if note_path.endswith(".md") else note_path
        marker = f"[[{target}|"
        text = self.vault.read(entity_rel)
        kept = [l for l in text.splitlines(keepends=True) if marker not in l]
        if len(kept) == len(text.splitlines(keepends=True)):
            return False
        self.vault.write(entity_rel, "".join(kept), overwrite=True)
        return True
```

`indexer.py` — replace the H1 stub:

```python
def _retract_stale_mentions(vault: Vault, store: GraphStore, rel: str) -> int:
    db = db_path()
    if not db.exists():
        return 0
    removed = 0
    for entity_path in cache.note_entity_paths(db, rel):
        entity_rel = entity_path + ".md"
        try:
            if store.remove_mention(entity_rel, rel):
                removed += 1
        except VaultError:
            continue  # entity note deleted/renamed by hand — nothing to retract
    return removed
```

Add `from .vault import Vault, VaultError` (extend the existing Vault import).

- [ ] **Step 4: Run** — touched files then full suite: expect **140 passed**.

- [ ] **Step 5: Commit** — `git add` the six files; message: `feat(graph): retract stale mentions when a note is reprocessed`

---

### Task H3: Public `complete_json` on the extractor

**Files:**
- Modify: `src/tesseract_mcp/extractor.py`, `tests/test_extractor.py`

- [ ] **Step 1: Failing test** (append; `FakeProc`/`make_runner`/`GOOD` exist):

```python
def test_complete_json_generic_prompt():
    runner = make_runner([FakeProc(stdout='{"merges": []}')])
    ex = CliExtractor(backend="codex", runner=runner, which=lambda n: n)
    assert ex.complete_json("any prompt") == {"merges": []}


def test_complete_json_retries_then_raises():
    runner = make_runner([FakeProc(stdout="junk"), FakeProc(stdout="junk2")])
    ex = CliExtractor(backend="codex", runner=runner, which=lambda n: n)
    with pytest.raises(ExtractorError):
        ex.complete_json("p")
```

- [ ] **Step 2: Verify failure** (no `complete_json` attribute).

- [ ] **Step 3: Implement** — in `CliExtractor`, add `complete_json` and slim `extract` to use it:

```python
    def complete_json(self, prompt: str) -> dict:
        """Send any prompt via the CLI backend; return its JSON object reply
        (one repair retry, then ExtractorError)."""
        out = self._invoke(prompt)
        try:
            return self._parse(out)
        except (ExtractorError, json.JSONDecodeError):
            repair = prompt + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
            out = self._invoke(repair)
            try:
                return self._parse(out)
            except json.JSONDecodeError as e:
                raise ExtractorError(f"invalid JSON after retry: {e}") from e

    def extract(self, path: str, content: str) -> Extraction:
        prompt = PROMPT_TEMPLATE.format(path=path, content=content)
        return _coerce(self.complete_json(prompt))
```

(Behavior identical — the old inline retry moves into `complete_json`; all 24 existing extractor tests must still pass unchanged.)

- [ ] **Step 4: Run** — extractor file then full suite: expect **142 passed**.

- [ ] **Step 5: Commit** — `refactor(graph): expose complete_json for generic LLM JSON calls`

---

### Task H4: Consolidation engine (`consolidate.py`)

**Files:**
- Create: `src/tesseract_mcp/consolidate.py`
- Modify: `src/tesseract_mcp/cache.py` (skip merged stubs in rebuild)
- Create: `tests/test_consolidate.py`

- [ ] **Step 1: Failing tests** — create `tests/test_consolidate.py`:

```python
from tesseract_mcp import cache, consolidate
from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GraphStore, entity_rel_path

ORACLE_VM = {"name": "Oracle VM", "type": "organization", "aliases": [], "summary": "Cloud VM."}
ORACLE_DEPLOY = {"name": "Oracle VM deploy", "type": "organization", "aliases": [], "summary": "Deploying it."}


class FakeBackend:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def seed(vault):
    store = GraphStore(vault)
    store.apply("A.md", Extraction([ORACLE_VM], []))
    store.apply("B.md", Extraction([ORACLE_DEPLOY], [
        {"from": "Oracle VM deploy", "from_type": "organization", "rel": "related_to",
         "to": "DEPLOY guide", "to_type": "source", "evidence": ""},
    ]))
    return store


MERGE = {"merges": [{"type": "organization", "canonical": "Oracle VM",
                     "duplicates": ["Oracle VM deploy"]}]}


def test_gather_entities(vault):
    seed(vault)
    got = consolidate.gather_entities(vault)
    names = {(e["type"], e["name"]) for e in got}
    assert ("organization", "Oracle VM") in names
    assert ("organization", "Oracle VM deploy") in names


def test_propose_merges_validates(vault):
    seed(vault)
    entities = consolidate.gather_entities(vault)
    bad = {"merges": [
        {"type": "organization", "canonical": "Oracle VM", "duplicates": ["Oracle VM deploy"]},
        {"type": "organization", "canonical": "Nonexistent", "duplicates": ["Oracle VM"]},
        {"type": "person", "canonical": "Oracle VM", "duplicates": ["Oracle VM deploy"]},
    ]}
    got = consolidate.propose_merges(FakeBackend(bad), entities)
    assert got == [{"type": "organization", "canonical": "Oracle VM",
                    "duplicates": ["Oracle VM deploy"]}]


def test_dry_run_changes_nothing(vault):
    seed(vault)
    result = consolidate.run(vault, FakeBackend(MERGE), apply=False)
    assert result["proposed"] and result["applied"] is False
    assert "Merged into" not in vault.read(entity_rel_path("organization", "Oracle VM deploy"))


def test_apply_merges_mentions_relations_aliases_and_redirects(vault):
    seed(vault)
    result = consolidate.run(vault, FakeBackend(MERGE), apply=True)
    assert result["applied"] is True and result["merged_entities"] == 1
    canon = vault.read(entity_rel_path("organization", "Oracle VM"))
    assert "[[B|" in canon                      # dup's mention moved over
    assert "related_to [[" in canon             # dup's relation moved over
    assert "Oracle VM deploy" in canon          # name folded into aliases
    dup = vault.read(entity_rel_path("organization", "Oracle VM deploy"))
    assert "merged_into:" in dup and "Merged into [[Oracle VM]]" in dup


def test_cache_rebuild_skips_redirect_stubs(vault, tmp_path):
    seed(vault)
    consolidate.run(vault, FakeBackend(MERGE), apply=True)
    db = tmp_path / "g.db"
    cache.rebuild(vault, db)
    names = [e["name"] for e in cache.find_entity(db, "oracle")]
    assert names == ["Oracle VM"]               # stub not an entity anymore
    assert cache.find_entity(db, "oracle")[0]["mention_count"] == 2
```

- [ ] **Step 2: Verify failure** — ImportError on `consolidate`; the cache-skip test fails last.

- [ ] **Step 3: Implement `src/tesseract_mcp/consolidate.py`**

```python
"""LLM-driven consolidation of duplicate graph entities."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import yaml

from . import cache
from .extractor import CliExtractor
from .graphstore import (
    GRAPH_ROOT,
    MENTIONS_HEADER,
    RELATIONS_HEADER,
    GraphStore,
    TYPE_FOLDERS,
)
from .indexer import db_path
from .search import parse_frontmatter
from .vault import Vault

PROMPT = """You are deduplicating entities in a personal knowledge graph.
Below is the full list of entities (one per line: type | name | aliases).
Identify groups that are name-variants of the SAME real-world thing.

Rules: merge only true variants (e.g. "Oracle VM" / "Oracle VM deploy");
same type only; pick the most standard, complete name as canonical; use only
names from the list; when unsure, do NOT merge. Reply with ONLY JSON:
{{"merges": [{{"type": str, "canonical": str, "duplicates": [str]}}]}}
Empty merges list if nothing qualifies.

Entities:
{listing}"""


def _section_lines(text: str, header: str) -> list[str]:
    start = text.find(header)
    if start == -1:
        return []
    nxt = text.find("\n## ", start + len(header))
    section = text[start : nxt if nxt != -1 else len(text)]
    return [l for l in section.splitlines() if l.startswith("- ")]


def gather_entities(vault: Vault) -> list[dict]:
    out: list[dict] = []
    graph_dir = vault.resolve(GRAPH_ROOT)
    if not graph_dir.is_dir():
        return out
    for p in sorted(graph_dir.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        if meta.get("merged_into"):
            continue
        aliases = meta.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [aliases]
        out.append(
            {"name": p.stem, "type": str(meta.get("entity") or "topic"),
             "path": "/".join(p.relative_to(vault.root).parts)[:-3],
             "aliases": [str(a) for a in aliases]}
        )
    return out


def propose_merges(backend, entities: list[dict]) -> list[dict]:
    if not entities:
        return []
    listing = "\n".join(
        f"{e['type']} | {e['name']} | {', '.join(e['aliases']) or '-'}"
        for e in entities
    )
    raw = backend.complete_json(PROMPT.format(listing=listing))
    known = {(e["type"], e["name"].casefold()) for e in entities}
    merges = []
    for m in raw.get("merges") or []:
        etype = str(m.get("type") or "").lower()
        canonical = str(m.get("canonical") or "").strip()
        dups = [str(d).strip() for d in (m.get("duplicates") or []) if str(d).strip()]
        dups = [d for d in dups if d.casefold() != canonical.casefold()]
        if not canonical or not dups or etype not in TYPE_FOLDERS:
            continue
        if (etype, canonical.casefold()) not in known:
            continue
        if any((etype, d.casefold()) not in known for d in dups):
            continue
        merges.append({"type": etype, "canonical": canonical, "duplicates": dups})
    return merges


def _apply_one(vault: Vault, store: GraphStore, merge: dict, now: datetime) -> None:
    etype = merge["type"]
    canon_rel = store.find_entity_note(etype, merge["canonical"])
    for dup_name in merge["duplicates"]:
        dup_rel = store.find_entity_note(etype, dup_name)
        if dup_rel is None or dup_rel == canon_rel or canon_rel is None:
            continue
        dup_text = vault.read(dup_rel)
        for line in _section_lines(dup_text, MENTIONS_HEADER):
            marker = line.split("|", 1)[0] + "|"
            store._insert_line(canon_rel, MENTIONS_HEADER, line, marker)
        for line in _section_lines(dup_text, RELATIONS_HEADER):
            store._insert_line(canon_rel, RELATIONS_HEADER, line, line)
        dup_meta = parse_frontmatter(dup_text)
        dup_aliases = dup_meta.get("aliases") or []
        if not isinstance(dup_aliases, list):
            dup_aliases = [dup_aliases]
        store.upsert_entity_ex(
            {"name": dup_name, "type": etype,
             "aliases": [str(a) for a in dup_aliases], "summary": ""}
        )
        stub_meta = {
            "created": now.strftime("%Y-%m-%d %H:%M"),
            "agent": "claude",
            "entity": etype,
            "merged_into": canon_rel[:-3],
            "tags": [f"graph/{etype}"],
        }
        canon_stem = canon_rel.rsplit("/", 1)[-1][:-3]
        stub = ("---\n" + yaml.safe_dump(stub_meta, sort_keys=False) + "---\n\n"
                + f"# {dup_name}\n\nMerged into [[{canon_stem}]].\n")
        vault.write(dup_rel, stub, overwrite=True)


def run(vault: Vault, backend, apply: bool = False) -> dict:
    entities = gather_entities(vault)
    merges = propose_merges(backend, entities)
    result = {"entities": len(entities), "proposed": merges, "applied": False,
              "merged_entities": 0}
    if apply and merges:
        store = GraphStore(vault)
        now = datetime.now()
        for m in merges:
            _apply_one(vault, store, m, now)
            result["merged_entities"] += len(m["duplicates"])
        result["applied"] = True
        cache.rebuild(vault, db_path())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolidate duplicate graph entities.")
    parser.add_argument("vault")
    parser.add_argument("--backend", default=None, help="codex | claude")
    parser.add_argument("--apply", action="store_true",
                        help="apply proposed merges (default: dry-run)")
    args = parser.parse_args()
    result = run(Vault(args.vault), CliExtractor(backend=args.backend), apply=args.apply)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
```

`cache.py` — in `rebuild`, right after `meta = parse_frontmatter(text)`, add:

```python
            if meta.get("merged_into"):
                continue
```

NOTE on `_apply_one`'s alias fold: `upsert_entity_ex` with the DUP name finds the canonical note only AFTER the dup note becomes a stub... it does NOT — `find_entity_note` scans filenames/aliases, and the dup note still exists at this point with its own name, so the upsert would hit the DUP note, not the canonical. FIX (implementer: do it this way, the test pins it): fold aliases into the canonical directly instead of via upsert. Replace the `store.upsert_entity_ex(...)` call with:

```python
        canon_text = vault.read(canon_rel)
        canon_meta = parse_frontmatter(canon_text)
        current = canon_meta.get("aliases") or []
        if not isinstance(current, list):
            current = [current]
        canon_name = canon_rel.rsplit("/", 1)[-1][:-3]
        known_names = {str(a).casefold() for a in current} | {canon_name.casefold()}
        added = [a for a in [dup_name, *map(str, dup_aliases)]
                 if a.casefold() not in known_names]
        if added:
            end = canon_text.find("\n---", 3)
            if end != -1:
                canon_meta["aliases"] = [str(a) for a in current] + added
                fm = "---\n" + yaml.safe_dump(canon_meta, sort_keys=False,
                                              default_flow_style=None) + "---"
                vault.write(canon_rel, fm + canon_text[end + 4:], overwrite=True)
```

- [ ] **Step 4: Run** — `tests/test_consolidate.py` (6 pass) then full suite: expect **148 passed**.

- [ ] **Step 5: Commit** — `feat(graph): LLM consolidation pass merging duplicate entities into redirect stubs`

---

### Task H5: `consolidate_graph` MCP tool + docs

**Files:**
- Modify: `src/tesseract_mcp/server.py`, `tests/test_server.py`, `README.md`

- [ ] **Step 1: Failing tests** — in `tests/test_server.py`, add `"consolidate_graph"` to the expected set in `test_all_tools_registered` (15 → 16 names), and append:

```python
def test_consolidate_graph_dry_run(monkeypatch):
    class FakeBackend:
        def complete_json(self, prompt):
            return {"merges": []}

    monkeypatch.setattr(server, "_make_extractor", lambda: FakeBackend())
    result = server.consolidate_graph()
    assert result["applied"] is False and result["proposed"] == []
```

- [ ] **Step 2: Verify failure** (set mismatch; missing attribute).

- [ ] **Step 3: Implement** — in `server.py`, add `from . import consolidate as consolidate_mod` to the imports, then:

```python
@mcp.tool()
def consolidate_graph(apply: bool = False) -> dict:
    """Find duplicate graph entities (name variants of the same thing) via an
    LLM pass. Dry-run by default — returns proposed merges for review; call
    again with apply=True to merge them into canonical entities."""
    return consolidate_mod.run(get_vault(), _make_extractor(), apply=apply)
```

README: add tool-table row `| `consolidate_graph` | Merge duplicate graph entities (dry-run by default) |` and mention `python -m tesseract_mcp.consolidate <vault> [--apply]` in the semantic-graph section.

- [ ] **Step 4: Run** — full suite: expect **150 passed**.

- [ ] **Step 5: Commit** — `feat(graph): consolidate_graph MCP tool`

---

### Task H6: Live run (controller/human-supervised, real vault)

- [ ] Re-mirror the vault backup first (robocopy to `C:\Users\Taimoor\OneDrive\Backups\Tesseract-vault-backup`) — consolidation rewrites entity notes.
- [ ] `python -m tesseract_mcp.consolidate C:\Vaults\Tesseract --backend codex` (dry-run): review proposed merges — expect the known fragments (Tesseract/tesseract-mcp/Tesseract MCP variants, Oracle VM/Oracle VM deploy, Claude/Claude Code).
- [ ] If proposals look right: re-run with `--apply`; inspect 2-3 canonical notes and stubs in Obsidian; `graph_stats` should show fewer entities, same-or-more mentions on canonicals.
- [ ] Edit one note to drop an entity mention, run `index_brain`, verify `mentions_retracted >= 1`.
- [ ] `log_session` the hardening upgrade; check off the G7 task in `Claude/Tasks.md` (`- [x]`).

---

## Self-review notes

- Coverage vs G7 scope: backoff+gating (H1), retraction incl. cache helper + removal (H2), consolidation incl. complete_json dependency (H3+H4), tool+docs (H5), live validation with backup-first (H6). Accepted limitation (relations lack provenance) documented in header.
- Type consistency: `counts` gains `skipped` and `mentions_retracted` (H1 defines both; H2 fills retraction); `note_entity_paths` returns .md-less paths matching graphstore link targets; `merged_into` written by consolidate, honored by cache.rebuild and gather_entities; `complete_json` signature identical between CliExtractor (H3) and test FakeBackends (H4/H5).
- Placeholders: none; the one known-tricky spot (alias fold hitting the dup note via find_entity_note) is called out with the exact replacement code rather than left to discovery.
- Expected suite trajectory: 132 → 137 → 140 → 142 → 148 → 150 (implementers report actuals).
