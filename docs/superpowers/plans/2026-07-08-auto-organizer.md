# Autonomous Vault Organizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fully autonomous librarian that files vault notes into the right existing top-level folder by embedding neighbor vote, with a journal, undo, proposals queue for low confidence, and hard exclusions.

**Architecture:** Two new modules: `organizer.py` (taxonomy discovery, candidate scan, cosine-weighted K-nearest-neighbor folder vote, sweep orchestration, journal/undo) and `mover.py` (the delicate part: move a note while rewriting path-qualified inbound wikilinks vault-wide with a prefix-collision guard, and transferring the indexer manifest key). Vectors come from the existing `embeddings.get_note_vectors`. Surfaces: CLI `python -m tesseract_mcp.organize <vault> [--dry-run]` plus MCP tools `organize_vault` (dry-run default) and `undo_move`.

**Tech Stack:** Python 3.11+ stdlib only (reuses existing `embeddings`/`sc_adapter`/`indexer`/`cache` modules). pytest.

## Global Constraints

- Repo: `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`, branch from `codex/architecture-roadmap` (work in a worktree; run `pip install -e ".[dev]"` in the worktree venv; run tests with the worktree's `.venv\Scripts\python.exe`).
- Constants, verbatim from spec: `VOTE_K = 10`, `VOTE_THRESHOLD = 0.7` — not configurable in v1.
- Hard exclusions, verbatim: `Claude`, `00 - Maps of Content`, `.obsidian`, `.smart-env`, `.trash`, `.space`, `copilot`, non-`.md` files, notes with `organize: false` frontmatter.
- Link rewriting touches ONLY path-qualified links: `[[old/path` immediately followed by `]]`, `|`, or `#`. Bare `[[Stem]]` links are never rewritten.
- Duplicate filename stem anywhere in the vault → proposal, never a move.
- `organize_vault` MCP tool defaults to `apply=False` (dry-run); autonomy lives in the scheduled CLI sweep.
- No new dependencies; tests use a deterministic fake embedder (no model downloads); full suite green after every task (203 tests at start).

---

## Task 1: Taxonomy discovery and candidate scan

**Files:**
- Create: `src/tesseract_mcp/organizer.py`
- Test: `tests/test_organizer.py` (new)

**Interfaces:**
- Consumes: `Vault` (existing), `search.parse_frontmatter` (existing).
- Produces: `EXCLUDED_DIRS: frozenset[str]`, `VOTE_K = 10`, `VOTE_THRESHOLD = 0.7`, `discover_taxonomy(vault: Vault) -> list[str]` (sorted top-level folder names), `iter_organized(vault: Vault) -> list[str]` (rel paths of .md notes inside taxonomy folders), `iter_candidates(vault: Vault) -> list[str]` (root-level .md + organized notes, minus `organize: false` notes). Tasks 2 and 5 consume all of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_organizer.py`:

```python
import json

import pytest

from tesseract_mcp.organizer import (
    VOTE_K,
    VOTE_THRESHOLD,
    discover_taxonomy,
    iter_candidates,
    iter_organized,
)
from tesseract_mcp.vault import Vault


@pytest.fixture
def org_vault(tmp_path):
    """A vault with two topical folders, excluded dirs, and loose notes."""
    for d in (".obsidian", ".smart-env", ".trash", "00 - Maps of Content",
              "Claude/Inbox", "02 - Space", "05 - Cooking"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "02 - Space" / "NASA JPL.md").write_text(
        "space telemetry anomaly research\n", encoding="utf-8")
    (tmp_path / "02 - Space" / "SmallSat.md").write_text(
        "space conference smallsat\n", encoding="utf-8")
    (tmp_path / "02 - Space" / "Telemanom.md").write_text(
        "space lstm telemetry\n", encoding="utf-8")
    (tmp_path / "05 - Cooking" / "Sourdough.md").write_text(
        "recipe starter flour\n", encoding="utf-8")
    (tmp_path / "05 - Cooking" / "Ramen.md").write_text(
        "recipe broth noodles\n", encoding="utf-8")
    (tmp_path / "00 - Maps of Content" / "Home.md").write_text(
        "moc\n", encoding="utf-8")
    (tmp_path / "Claude" / "Inbox" / "capture.md").write_text(
        "agent capture\n", encoding="utf-8")
    (tmp_path / "Loose Space Note.md").write_text(
        "space orbital telemetry note\n", encoding="utf-8")
    (tmp_path / "Pinned.md").write_text(
        "---\norganize: false\n---\n\nspace note that must stay put\n",
        encoding="utf-8")
    return Vault(tmp_path)


def test_constants_match_spec():
    assert VOTE_K == 10
    assert VOTE_THRESHOLD == 0.7


def test_discover_taxonomy_excludes_hard_exclusions(org_vault):
    assert discover_taxonomy(org_vault) == ["02 - Space", "05 - Cooking"]


def test_discover_taxonomy_picks_up_new_human_folder(org_vault):
    (org_vault.root / "07 - Finance").mkdir()
    assert "07 - Finance" in discover_taxonomy(org_vault)


def test_iter_organized_lists_taxonomy_notes_only(org_vault):
    organized = iter_organized(org_vault)
    assert "02 - Space/NASA JPL.md" in organized
    assert "05 - Cooking/Ramen.md" in organized
    assert not any(p.startswith("Claude/") for p in organized)
    assert not any(p.startswith("00 - Maps of Content") for p in organized)


def test_iter_candidates_root_and_organized_minus_pinned(org_vault):
    candidates = iter_candidates(org_vault)
    assert "Loose Space Note.md" in candidates
    assert "02 - Space/NASA JPL.md" in candidates       # filed notes are re-checkable
    assert "Pinned.md" not in candidates                # organize: false
    assert "Claude/Inbox/capture.md" not in candidates  # excluded dir
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesseract_mcp.organizer'`

- [ ] **Step 3: Implement**

Create `src/tesseract_mcp/organizer.py`:

```python
"""Autonomous vault organizer: files notes where their semantic neighbors
live, via cosine-weighted K-nearest-neighbor folder vote.

Standing permission for autonomous moves in the human topical tree was
granted by Taimoor 2026-07-08 (see the Organizer section of the
constitution). Rails: journal + undo, proposals queue below the confidence
threshold, hard exclusions below.
"""

from __future__ import annotations

from .search import parse_frontmatter
from .vault import Vault

EXCLUDED_DIRS = frozenset({
    "Claude", "00 - Maps of Content", ".obsidian", ".smart-env",
    ".trash", ".space", "copilot",
})
VOTE_K = 10
VOTE_THRESHOLD = 0.7


def discover_taxonomy(vault: Vault) -> list[str]:
    """Existing top-level folders = the frozen taxonomy."""
    return sorted(
        p.name for p in vault.root.iterdir()
        if p.is_dir() and p.name not in EXCLUDED_DIRS
    )


def _wants_organizing(vault: Vault, rel: str) -> bool:
    text = vault.read(rel)
    return parse_frontmatter(text).get("organize") is not False


def iter_organized(vault: Vault) -> list[str]:
    """Rel paths of .md notes currently inside taxonomy folders."""
    out: list[str] = []
    for folder in discover_taxonomy(vault):
        for p in sorted((vault.root / folder).rglob("*.md")):
            out.append("/".join(p.relative_to(vault.root).parts))
    return out


def iter_candidates(vault: Vault) -> list[str]:
    """Notes the organizer may classify: vault-root .md files plus
    already-organized notes (re-checkable), minus organize: false."""
    root_notes = sorted(
        p.name for p in vault.root.iterdir()
        if p.is_file() and p.suffix == ".md"
    )
    return [
        rel for rel in root_notes + iter_organized(vault)
        if _wants_organizing(vault, rel)
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizer.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/organizer.py tests/test_organizer.py
git commit -m "feat(organizer): taxonomy discovery and candidate scan with hard exclusions"
```

---

## Task 2: Neighbor-vote classifier

**Files:**
- Modify: `src/tesseract_mcp/organizer.py`
- Test: `tests/test_organizer.py`

**Interfaces:**
- Consumes: `VOTE_K`, `VOTE_THRESHOLD` (Task 1).
- Produces: `Classification` dataclass (`folder: str | None`, `share: float`, `neighbors: list[str]`) and `classify(rel: str, vectors: dict[str, list[float]], labeled: list[str], k: int = VOTE_K) -> Classification`. Pure function — no vault access; Task 5 wires it to real vectors.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_organizer.py`:

```python
from tesseract_mcp.organizer import Classification, classify

SPACE = [1.0, 0.0]
COOK = [0.0, 1.0]
MIXED = [0.7, 0.7]

LABELED_VECS = {
    "02 - Space/NASA JPL.md": SPACE,
    "02 - Space/SmallSat.md": SPACE,
    "02 - Space/Telemanom.md": SPACE,
    "05 - Cooking/Sourdough.md": COOK,
    "05 - Cooking/Ramen.md": COOK,
}
LABELED = list(LABELED_VECS)


def test_classify_clear_majority():
    vectors = {**LABELED_VECS, "Loose Space Note.md": [0.9, 0.1]}
    got = classify("Loose Space Note.md", vectors, LABELED)
    assert got.folder == "02 - Space"
    assert got.share >= 0.7
    assert "02 - Space/NASA JPL.md" in got.neighbors


def test_classify_split_vote_low_share():
    vectors = {**LABELED_VECS, "Ambiguous.md": MIXED}
    got = classify("Ambiguous.md", vectors, LABELED)
    assert got.share < 0.7


def test_classify_candidate_never_votes_for_itself():
    vectors = {**LABELED_VECS, "02 - Space/NASA JPL.md": SPACE}
    got = classify("02 - Space/NASA JPL.md", vectors, LABELED)
    assert "02 - Space/NASA JPL.md" not in got.neighbors


def test_classify_no_vector_or_no_labeled_returns_none():
    got = classify("Unknown.md", LABELED_VECS, LABELED)  # no vector for it
    assert got.folder is None and got.share == 0.0
    got2 = classify("X.md", {"X.md": SPACE}, [])          # nothing labeled
    assert got2.folder is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizer.py -v`
Expected: FAIL with `ImportError: cannot import name 'Classification'`

- [ ] **Step 3: Implement**

Add to `src/tesseract_mcp/organizer.py` (add `from dataclasses import dataclass` to imports):

```python
@dataclass
class Classification:
    folder: str | None
    share: float
    neighbors: list[str]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def classify(
    rel: str,
    vectors: dict[str, list[float]],
    labeled: list[str],
    k: int = VOTE_K,
) -> Classification:
    """Cosine-weighted K-nearest-neighbor vote among labeled notes.
    share = winning folder's similarity mass / total mass of the top K."""
    vec = vectors.get(rel)
    if vec is None:
        return Classification(None, 0.0, [])
    scored = [
        (other, _cosine(vec, vectors[other]))
        for other in labeled
        if other != rel and other in vectors
    ]
    scored = [(p, s) for p, s in scored if s > 0]
    if not scored:
        return Classification(None, 0.0, [])
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = scored[:k]
    votes: dict[str, float] = {}
    for path, sim in top:
        votes[path.split("/")[0]] = votes.get(path.split("/")[0], 0.0) + sim
    winner = max(votes, key=votes.get)
    share = votes[winner] / sum(votes.values())
    return Classification(winner, share, [p for p, _ in top])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizer.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/organizer.py tests/test_organizer.py
git commit -m "feat(organizer): cosine-weighted neighbor-vote classifier"
```

---

## Task 3: Move engine with link rewriting

**Files:**
- Create: `src/tesseract_mcp/mover.py`
- Test: `tests/test_mover.py` (new)

**Interfaces:**
- Consumes: `Vault` (existing), `indexer.load_manifest(vault_root)` / `indexer.save_manifest(manifest, vault_root)` (existing, vault-rooted since v0.4), `search.SKIP_DIRS` (existing).
- Produces: `duplicate_stem_exists(vault: Vault, rel: str) -> bool`; `move_note(vault: Vault, old_rel: str, new_rel: str) -> dict` returning `{"from", "to", "rewrites": [{"path", "count"}]}`; `reverse_rewrites(vault, old_rel, new_rel, rewrite_paths: list[str]) -> None` (used by undo: rewrites new→old in exactly the listed files). Tasks 4-5 consume these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mover.py`:

```python
import pytest

from tesseract_mcp import indexer
from tesseract_mcp.mover import duplicate_stem_exists, move_note, reverse_rewrites
from tesseract_mcp.vault import Vault


@pytest.fixture
def mv_vault(tmp_path):
    (tmp_path / "02 - Space").mkdir()
    (tmp_path / "Claude" / "Graph" / "Topics").mkdir(parents=True)
    (tmp_path / "Telemetry.md").write_text(
        "root note about telemetry\n", encoding="utf-8")
    # path-qualified inbound link (root-level note: path == stem)
    (tmp_path / "02 - Space" / "Research.md").write_text(
        "See [[Telemetry]] and [[Telemetry|the telemetry note]] "
        "and [[Telemetry#Details]].\n", encoding="utf-8")
    # prefix-collision neighbor: must NOT be rewritten
    (tmp_path / "Telemetry 2.md").write_text("sequel\n", encoding="utf-8")
    (tmp_path / "02 - Space" / "Mentions2.md").write_text(
        "See [[Telemetry 2]] too.\n", encoding="utf-8")
    # graph entity note with a path-qualified mention
    (tmp_path / "Claude" / "Graph" / "Topics" / "Telemetry Topic.md").write_text(
        "## Mentions\n\n- [[Telemetry|Telemetry]] — evidence\n", encoding="utf-8")
    return Vault(tmp_path)


def test_move_rewrites_qualified_links_everywhere(mv_vault):
    record = move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    assert record["from"] == "Telemetry.md"
    assert record["to"] == "02 - Space/Telemetry.md"
    assert not (mv_vault.root / "Telemetry.md").exists()
    assert (mv_vault.root / "02 - Space" / "Telemetry.md").is_file()
    research = mv_vault.read("02 - Space/Research.md")
    assert "[[02 - Space/Telemetry]]" in research
    assert "[[02 - Space/Telemetry|the telemetry note]]" in research
    assert "[[02 - Space/Telemetry#Details]]" in research
    graph = mv_vault.read("Claude/Graph/Topics/Telemetry Topic.md")
    assert "[[02 - Space/Telemetry|Telemetry]]" in graph
    rewritten = {r["path"] for r in record["rewrites"]}
    assert "02 - Space/Research.md" in rewritten
    assert "Claude/Graph/Topics/Telemetry Topic.md" in rewritten


def test_move_leaves_prefix_collision_alone(mv_vault):
    move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    assert "[[Telemetry 2]]" in mv_vault.read("02 - Space/Mentions2.md")


def test_move_transfers_manifest_key(mv_vault):
    manifest = indexer.load_manifest(mv_vault.root)
    manifest["hashes"]["Telemetry.md"] = "abc123"
    indexer.save_manifest(manifest, mv_vault.root)
    move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    manifest = indexer.load_manifest(mv_vault.root)
    assert "Telemetry.md" not in manifest["hashes"]
    assert manifest["hashes"]["02 - Space/Telemetry.md"] == "abc123"


def test_duplicate_stem_detected(mv_vault):
    (mv_vault.root / "02 - Space" / "Clone.md").write_text("a\n", encoding="utf-8")
    (mv_vault.root / "Clone.md").write_text("b\n", encoding="utf-8")
    assert duplicate_stem_exists(mv_vault, "Clone.md")
    assert not duplicate_stem_exists(mv_vault, "Telemetry.md")  # 'Telemetry 2' is a different stem


def test_reverse_rewrites_restores_links(mv_vault):
    record = move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    reverse_rewrites(
        mv_vault, "Telemetry.md", "02 - Space/Telemetry.md",
        [r["path"] for r in record["rewrites"]],
    )
    assert "[[Telemetry]]" in mv_vault.read("02 - Space/Research.md")
    assert "[[02 - Space/Telemetry]]" not in mv_vault.read("02 - Space/Research.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mover.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesseract_mcp.mover'`

- [ ] **Step 3: Implement**

Create `src/tesseract_mcp/mover.py`:

```python
"""Move a vault note while keeping every inbound link resolvable.

Only path-qualified wikilinks are rewritten: `[[old/path` immediately
followed by `]]`, `|`, or `#` (the lookahead prevents `[[Note 2` from
matching a move of `Note`). Bare `[[Stem]]` links keep working because the
stem does not change and the organizer's duplicate-stem guard ensures the
stem stays unique vault-wide.
"""

from __future__ import annotations

import os
import re

from . import indexer
from .search import SKIP_DIRS
from .vault import Vault, VaultError


def _no_md(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def _link_pattern(rel: str) -> re.Pattern:
    return re.compile(r"\[\[" + re.escape(_no_md(rel)) + r"(?=[\]|#])")


def duplicate_stem_exists(vault: Vault, rel: str) -> bool:
    stem = _no_md(rel).rsplit("/", 1)[-1].casefold()
    count = 0
    for p in vault.root.rglob("*.md"):
        parts = p.relative_to(vault.root).parts
        if SKIP_DIRS & set(parts):
            continue
        if p.stem.casefold() == stem:
            count += 1
            if count > 1:
                return True
    return False


def _rewrite_links(vault: Vault, src_rel: str, dst_rel: str) -> list[dict]:
    pattern = _link_pattern(src_rel)
    replacement = "[[" + _no_md(dst_rel)
    rewrites: list[dict] = []
    for p in sorted(vault.root.rglob("*.md")):
        parts = p.relative_to(vault.root).parts
        if SKIP_DIRS & set(parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        new_text, count = pattern.subn(replacement, text)
        if count:
            p.write_text(new_text, encoding="utf-8")
            rewrites.append({"path": "/".join(parts), "count": count})
    return rewrites


def move_note(vault: Vault, old_rel: str, new_rel: str) -> dict:
    src = vault.resolve(old_rel)
    dst = vault.resolve(new_rel)
    if not src.is_file():
        raise VaultError(f"Cannot move: not a file: {old_rel}")
    if dst.exists():
        raise VaultError(f"Cannot move: destination exists: {new_rel}")
    rewrites = _rewrite_links(vault, old_rel, new_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    manifest = indexer.load_manifest(vault.root)
    if old_rel in manifest["hashes"]:
        manifest["hashes"][new_rel] = manifest["hashes"].pop(old_rel)
        indexer.save_manifest(manifest, vault.root)
    return {"from": old_rel, "to": new_rel, "rewrites": rewrites}


def reverse_rewrites(
    vault: Vault, old_rel: str, new_rel: str, rewrite_paths: list[str]
) -> None:
    """Undo helper: rewrite new→old in exactly the files a move touched."""
    pattern = _link_pattern(new_rel)
    replacement = "[[" + _no_md(old_rel)
    for rel in rewrite_paths:
        p = vault.resolve(rel)
        if not p.is_file():
            continue
        p.write_text(
            pattern.sub(replacement, p.read_text(encoding="utf-8", errors="ignore")),
            encoding="utf-8",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mover.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/mover.py tests/test_mover.py
git commit -m "feat(mover): move notes with qualified-link rewriting and manifest transfer"
```

---

## Task 4: Journal and undo

**Files:**
- Modify: `src/tesseract_mcp/organizer.py`
- Test: `tests/test_organizer.py`

**Interfaces:**
- Consumes: `move_note`, `reverse_rewrites` (Task 3), `indexer.state_dir(vault_root)` (existing), `Vault.append` (existing).
- Produces: `ORGANIZER_NOTE = "Claude/Organizer.md"`, `journal_path(vault) -> Path`, `record_move(vault, record: dict, share: float, neighbors: list[str]) -> None` (JSONL entry + human-readable line in Claude/Organizer.md), `undo_move(vault, note_rel: str) -> dict` (reverses newest not-undone move whose `to` == note_rel; raises `VaultError` if none). Task 5 consumes `record_move`; the MCP tool consumes `undo_move`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_organizer.py`:

```python
from tesseract_mcp import indexer
from tesseract_mcp.mover import move_note
from tesseract_mcp.organizer import (
    ORGANIZER_NOTE,
    journal_path,
    record_move,
    undo_move,
)
from tesseract_mcp.vault import VaultError


@pytest.fixture
def moved(org_vault):
    record = move_note(org_vault, "Loose Space Note.md", "02 - Space/Loose Space Note.md")
    record_move(org_vault, record, share=0.85,
                neighbors=["02 - Space/NASA JPL.md"])
    return record


def test_record_move_writes_jsonl_and_note(org_vault, moved):
    lines = journal_path(org_vault).read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[-1])
    assert entry["from"] == "Loose Space Note.md"
    assert entry["to"] == "02 - Space/Loose Space Note.md"
    assert entry["share"] == 0.85
    note = org_vault.read(ORGANIZER_NOTE)
    assert "Loose Space Note" in note and "0.85" in note


def test_undo_restores_location_and_journal(org_vault, moved):
    result = undo_move(org_vault, "02 - Space/Loose Space Note.md")
    assert result["restored"] == "Loose Space Note.md"
    assert (org_vault.root / "Loose Space Note.md").is_file()
    assert not (org_vault.root / "02 - Space" / "Loose Space Note.md").exists()


def test_undo_twice_raises(org_vault, moved):
    undo_move(org_vault, "02 - Space/Loose Space Note.md")
    with pytest.raises(VaultError, match="No undoable move"):
        undo_move(org_vault, "02 - Space/Loose Space Note.md")


def test_undo_unknown_path_raises(org_vault):
    with pytest.raises(VaultError, match="No undoable move"):
        undo_move(org_vault, "02 - Space/Never Moved.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizer.py -v`
Expected: FAIL with `ImportError: cannot import name 'ORGANIZER_NOTE'`

- [ ] **Step 3: Implement**

Add to `src/tesseract_mcp/organizer.py` (extend imports with `import json`, `from datetime import datetime`, `from pathlib import Path`, `from . import indexer`, `from .mover import move_note, reverse_rewrites`, `from .vault import Vault, VaultError` — merge with the existing Vault import):

```python
ORGANIZER_NOTE = "Claude/Organizer.md"
_NOTE_SEED = "# Organizer\n\nAutonomous move log and proposals. See constitution → Organizer.\n\n## Log\n"


def journal_path(vault: Vault) -> Path:
    return indexer.state_dir(vault.root) / "organizer_journal.jsonl"


def _ensure_note(vault: Vault) -> None:
    try:
        vault.read(ORGANIZER_NOTE)
    except VaultError:
        vault.write(ORGANIZER_NOTE, _NOTE_SEED)


def record_move(vault: Vault, record: dict, share: float, neighbors: list[str]) -> None:
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from": record["from"],
        "to": record["to"],
        "share": share,
        "neighbors": neighbors,
        "rewrites": record["rewrites"],
        "undone": False,
    }
    with journal_path(vault).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    _ensure_note(vault)
    stem = record["to"].rsplit("/", 1)[-1][:-3]
    vault.append(
        ORGANIZER_NOTE,
        f"- {entry['ts']} — moved [[{record['to'][:-3]}|{stem}]] "
        f"from `{record['from']}` (share {share:.2f})\n",
    )


def undo_move(vault: Vault, note_rel: str) -> dict:
    jp = journal_path(vault)
    entries = []
    if jp.exists():
        entries = [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()]
    target_idx = None
    for i in range(len(entries) - 1, -1, -1):
        if entries[i]["to"] == note_rel and not entries[i].get("undone"):
            target_idx = i
            break
    if target_idx is None:
        raise VaultError(f"No undoable move found for: {note_rel}")
    entry = entries[target_idx]
    src = vault.resolve(entry["to"])
    dst = vault.resolve(entry["from"])
    if not src.is_file():
        raise VaultError(f"Cannot undo: file no longer at {entry['to']}")
    if dst.exists():
        raise VaultError(f"Cannot undo: original location occupied: {entry['from']}")
    import os

    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    reverse_rewrites(vault, entry["from"], entry["to"],
                     [r["path"] for r in entry["rewrites"]])
    manifest = indexer.load_manifest(vault.root)
    if entry["to"] in manifest["hashes"]:
        manifest["hashes"][entry["from"]] = manifest["hashes"].pop(entry["to"])
        indexer.save_manifest(manifest, vault.root)
    entries[target_idx]["undone"] = True
    jp.write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )
    _ensure_note(vault)
    vault.append(
        ORGANIZER_NOTE,
        f"- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — UNDID move of "
        f"`{entry['to']}` back to `{entry['from']}`\n",
    )
    return {"restored": entry["from"], "was": entry["to"]}
```

(Move the `import os` to the top of the file with the other imports.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizer.py tests/test_mover.py -v`
Expected: PASS (14 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/organizer.py tests/test_organizer.py
git commit -m "feat(organizer): move journal with human-readable mirror and undo"
```

---

## Task 5: Sweep orchestrator, CLI, dry-run

**Files:**
- Modify: `src/tesseract_mcp/organizer.py`
- Create: `src/tesseract_mcp/organize.py` (thin `__main__`-style CLI wrapper so the command is `python -m tesseract_mcp.organize`)
- Test: `tests/test_organizer.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4 plus `embeddings.get_note_vectors(vault, state_root, embedder)` (existing), `mover.duplicate_stem_exists` (Task 3), `cache.rebuild(vault, db_path)` + `indexer.db_path(vault_root)` (existing).
- Produces: `run_sweep(vault: Vault, embedder=None, apply: bool = False) -> dict` with keys `moved: list[dict]`, `proposals: list[dict]`, `skipped: list[dict]`, `cache_rebuilt: bool`; CLI `python -m tesseract_mcp.organize <vault> [--dry-run]` (note: CLI default APPLIES; `--dry-run` disables — the CLI is the autonomous scheduled path). The MCP tool (Task 6) consumes `run_sweep`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_organizer.py`:

```python
from tesseract_mcp.organizer import run_sweep


class ClusterEmbedder:
    """space→[1,0], recipe→[0,1], both/neither→[0.7,0.7]. Deterministic."""

    def embed_batch(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            has_space, has_recipe = "space" in low, "recipe" in low
            if has_space and not has_recipe:
                out.append([1.0, 0.0])
            elif has_recipe and not has_space:
                out.append([0.0, 1.0])
            else:
                out.append([0.7, 0.7])
        return out


def test_sweep_dry_run_reports_but_touches_nothing(org_vault):
    report = run_sweep(org_vault, ClusterEmbedder(), apply=False)
    moves = {m["from"]: m["to_folder"] for m in report["moved"]}
    assert moves.get("Loose Space Note.md") == "02 - Space"
    assert (org_vault.root / "Loose Space Note.md").is_file()  # not actually moved
    assert not journal_path(org_vault).exists()


def test_sweep_apply_moves_and_journals(org_vault):
    report = run_sweep(org_vault, ClusterEmbedder(), apply=True)
    assert any(m["from"] == "Loose Space Note.md" for m in report["moved"])
    assert (org_vault.root / "02 - Space" / "Loose Space Note.md").is_file()
    assert not (org_vault.root / "Loose Space Note.md").exists()
    assert journal_path(org_vault).exists()
    assert report["cache_rebuilt"] is True


def test_sweep_correctly_filed_note_skipped(org_vault):
    report = run_sweep(org_vault, ClusterEmbedder(), apply=False)
    moved_from = [m["from"] for m in report["moved"]]
    assert "02 - Space/NASA JPL.md" not in moved_from  # already in the right place


def test_sweep_ambiguous_note_becomes_proposal(org_vault):
    (org_vault.root / "Fusion Cuisine In Space.md").write_text(
        "space station recipe experiments\n", encoding="utf-8")  # mixed → [0.7, 0.7]
    report = run_sweep(org_vault, ClusterEmbedder(), apply=True)
    props = [p["path"] for p in report["proposals"]]
    assert "Fusion Cuisine In Space.md" in props
    assert (org_vault.root / "Fusion Cuisine In Space.md").is_file()  # not moved
    assert "Proposals" in org_vault.read(ORGANIZER_NOTE)


def test_sweep_duplicate_stem_becomes_proposal(org_vault):
    (org_vault.root / "05 - Cooking" / "Loose Space Note.md").write_text(
        "recipe named confusingly\n", encoding="utf-8")
    report = run_sweep(org_vault, ClusterEmbedder(), apply=True)
    props = {p["path"]: p for p in report["proposals"]}
    assert "Loose Space Note.md" in props
    assert "duplicate" in props["Loose Space Note.md"]["reason"]
    assert (org_vault.root / "Loose Space Note.md").is_file()  # not moved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizer.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_sweep'`

- [ ] **Step 3: Implement the sweep**

Add to `src/tesseract_mcp/organizer.py` (extend imports with `from . import cache, embeddings as embeddings_mod` and `from .mover import duplicate_stem_exists`):

```python
def run_sweep(vault: Vault, embedder=None, apply: bool = False) -> dict:
    if embedder is None:
        embedder = embeddings_mod.SentenceTransformerEmbedder()
    vectors = embeddings_mod.get_note_vectors(
        vault, indexer.state_dir(vault.root), embedder
    )
    taxonomy = set(discover_taxonomy(vault))
    labeled = iter_organized(vault)
    moved: list[dict] = []
    proposals: list[dict] = []
    skipped: list[dict] = []

    for rel in iter_candidates(vault):
        current = rel.split("/")[0] if "/" in rel and rel.split("/")[0] in taxonomy else None
        cls = classify(rel, vectors, labeled)
        if cls.folder is None:
            skipped.append({"path": rel, "reason": "no vector or no labeled neighbors"})
            continue
        if current == cls.folder:
            skipped.append({"path": rel, "reason": "already correctly filed"})
            continue
        if cls.share < VOTE_THRESHOLD:
            if current is None:  # root notes queue for a human; filed notes rest
                proposals.append({
                    "path": rel, "suggested": cls.folder,
                    "share": round(cls.share, 3),
                    "neighbors": cls.neighbors[:3],
                    "reason": "low confidence",
                })
            else:
                skipped.append({"path": rel, "reason": "low-confidence disagreement"})
            continue
        if duplicate_stem_exists(vault, rel):
            proposals.append({
                "path": rel, "suggested": cls.folder,
                "share": round(cls.share, 3),
                "neighbors": cls.neighbors[:3],
                "reason": "duplicate stem — bare links would become ambiguous",
            })
            continue
        stem_name = rel.rsplit("/", 1)[-1]
        target_rel = f"{cls.folder}/{stem_name}"
        if apply:
            record = move_note(vault, rel, target_rel)
            record_move(vault, record, share=cls.share, neighbors=cls.neighbors[:3])
        moved.append({
            "from": rel, "to_folder": cls.folder,
            "share": round(cls.share, 3), "neighbors": cls.neighbors[:3],
        })

    cache_rebuilt = False
    if apply and moved:
        cache.rebuild(vault, indexer.db_path(vault.root))
        cache_rebuilt = True
    if apply and proposals:
        _ensure_note(vault)
        lines = [f"\n### Proposals {datetime.now().strftime('%Y-%m-%d')}\n"]
        for p in proposals:
            lines.append(
                f"- `{p['path']}` → **{p['suggested']}** "
                f"(share {p['share']}; {p['reason']})\n"
            )
        vault.append(ORGANIZER_NOTE, "".join(lines))
    return {
        "moved": moved, "proposals": proposals,
        "skipped": skipped, "cache_rebuilt": cache_rebuilt,
    }
```

- [ ] **Step 4: Create the CLI wrapper**

Create `src/tesseract_mcp/organize.py`:

```python
"""CLI sweep: python -m tesseract_mcp.organize <vault> [--dry-run]

Default APPLIES moves (this is the scheduled autonomous path). The FIRST
live run against a real vault must be --dry-run, reviewed by a human — see
the design spec and README.
"""

from __future__ import annotations

import argparse
import json

from .organizer import run_sweep
from .vault import Vault


def main() -> None:
    parser = argparse.ArgumentParser(description="Organize the vault by neighbor vote.")
    parser.add_argument("vault", help="Path to the vault root")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report without moving anything")
    args = parser.parse_args()
    report = run_sweep(Vault(args.vault), apply=not args.dry_run)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_organizer.py tests/test_mover.py -v`
Expected: PASS (19 passed)

- [ ] **Step 6: Commit**

```bash
git add src/tesseract_mcp/organizer.py src/tesseract_mcp/organize.py tests/test_organizer.py
git commit -m "feat(organizer): autonomous sweep with proposals queue and dry-run CLI"
```

---

## Task 6: MCP tools, constitution amendment, docs

**Files:**
- Modify: `src/tesseract_mcp/server.py`
- Modify: `vault/constitution.md`
- Modify: `README.md`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `organizer.run_sweep(vault, embedder=None, apply=False) -> dict`, `organizer.undo_move(vault, note_rel) -> dict` (Tasks 4-5), `server._get_embedder()` (existing).
- Produces: MCP tools `organize_vault(apply: bool = False) -> dict` and `undo_move(path: str) -> dict`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server.py`:

```python
def test_organize_vault_dry_run_default(vault_dir):
    (vault_dir / "02 - Space").mkdir()
    (vault_dir / "02 - Space" / "Orbits.md").write_text("space orbits\n", encoding="utf-8")
    (vault_dir / "Loose.md").write_text("space loose note\n", encoding="utf-8")

    class FakeEmbedder:
        def embed_batch(self, texts):
            return [[1.0, 0.0] if "space" in t.lower() else [0.0, 1.0] for t in texts]

    import tesseract_mcp.server as srv
    srv._embedder = FakeEmbedder()
    try:
        report = server.organize_vault()
        assert (vault_dir / "Loose.md").is_file()  # dry-run: nothing moved
        assert any(m["from"] == "Loose.md" for m in report["moved"])
    finally:
        srv._embedder = None


def test_undo_move_tool_raises_cleanly_when_nothing_to_undo():
    with pytest.raises(VaultError, match="No undoable move"):
        server.undo_move("02 - Space/Nope.md")
```

Also update `test_all_tools_registered`'s expected set to add the two new
names (final set):

```python
    assert {t.name for t in tools} == {
        "search_brain", "read_note", "log_session", "capture",
        "upsert_concept", "write_note", "add_task", "list_tasks",
        "query_notes", "get_backlinks", "list_recent",
        "index_brain", "find_entity", "related_notes", "graph_stats",
        "consolidate_graph", "onboard", "context_bundle",
        "organize_vault", "undo_move",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -v`
Expected: FAIL — `test_all_tools_registered` set mismatch and `AttributeError: module 'tesseract_mcp.server' has no attribute 'organize_vault'`.

- [ ] **Step 3: Implement the tools**

In `src/tesseract_mcp/server.py`, add `organizer` to the package import line:

```python
from . import cache as cache_mod, consolidate as consolidate_mod, graph, hybrid, indexer, notes, organizer as organizer_mod, tasks as tasks_mod
```

Add after `context_bundle`:

```python
@mcp.tool()
def organize_vault(apply: bool = False) -> dict:
    """Neighbor-vote sweep of the vault's topical folders. Dry-run by
    default: returns {moved, proposals, skipped} without touching files.
    apply=True executes moves (journaled to Claude/Organizer.md; reversible
    via undo_move). The scheduled CLI sweep is the autonomous path — see
    constitution → Organizer."""
    return organizer_mod.run_sweep(get_vault(), _get_embedder(), apply=apply)


@mcp.tool()
def undo_move(path: str) -> dict:
    """Reverse the organizer's most recent move of the given note (current
    vault-relative path). Restores the file, its inbound links, and the
    index manifest entry."""
    return organizer_mod.undo_move(get_vault(), path)
```

Add to the `tools` list inside `onboard()`:

```python
        "organize_vault(apply?) / undo_move(path) — neighbor-vote filing; dry-run default",
```

- [ ] **Step 4: Run the server tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_server.py -v`
Expected: PASS.

- [ ] **Step 5: Constitution amendment and README**

Append to `vault/constitution.md`:

```markdown
## Organizer

Standing permission (granted by Taimoor, 2026-07-08): the organizer may
move notes within the human topical tree autonomously — vault-root notes
and misfiled notes in topical folders — filing each where its semantic
neighbors live (K=10 cosine vote, share ≥ 0.7). It NEVER touches Claude/,
00 - Maps of Content, dotfolders, non-markdown files, or notes with
`organize: false` frontmatter; it never creates or renames folders; on any
duplicate filename stem it proposes instead of moving. Every move is
journaled to Claude/Organizer.md and reversible via the undo_move tool.
Low-confidence classifications queue as proposals in the same note —
resolving them teaches future votes.
```

Add to `README.md` after the "Provision a new vault" section:

```markdown
## Autonomous organizer

    python -m tesseract_mcp.organize C:\Vaults\Tesseract --dry-run   # ALWAYS first
    python -m tesseract_mcp.organize C:\Vaults\Tesseract             # scheduled sweep

Files notes into the existing top-level folders by embedding neighbor vote
(share ≥ 0.7 moves; below queues a proposal in `Claude/Organizer.md`).
Every move is journaled and reversible (`undo_move` tool). The FIRST run
against a real vault must be --dry-run and human-reviewed. MCP tools:
`organize_vault(apply?)` (dry-run default) and `undo_move(path)`.
```

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS — 224 tests (203 baseline + 21 new).

- [ ] **Step 7: Commit**

```bash
git add src/tesseract_mcp/server.py vault/constitution.md README.md tests/test_server.py
git commit -m "feat(organizer): MCP tools, constitution amendment, README

organize_vault (dry-run default) + undo_move; standing-permission
Organizer section in the constitution source."
```

---

## Post-merge human steps (not tasks — record in the PR/merge notes)

1. The live vault's `Claude/README.md` predates this change and the
   conventions installer never overwrites — append the `## Organizer`
   section to it (explicitly-confirmed write or by hand).
2. **First live run must be `--dry-run`**, output reviewed by Taimoor — it
   doubles as an audit of the 2026-07-05 Notion import's folder purity.
3. Only after that review: schedule the applying sweep (Task Scheduler,
   same pattern as the indexer nightly).

## Self-Review Notes

**Spec coverage:** taxonomy discovery/frozen (T1), exclusions + `organize: false` (T1), neighbor-vote classifier with K=10/0.7 verbatim (T2), move engine with qualified-link rewrite + prefix-collision guard + Graph-note links + manifest transfer (T3), duplicate-stem guard (T3 predicate, enforced in T5 sweep), journal JSONL + human mirror + undo incl. double-undo error (T4), sweep semantics — root-low→proposal / filed-low→skip / agree→skip, proposals block in Organizer.md, once-per-sweep cache rebuild, dry-run untouched (T5), CLI with apply-default + --dry-run (T5), MCP tools with dry-run default (T6), constitution amendment + README + first-run rule (T6 + post-merge steps). Non-goals: no tasks touch them.

**Placeholder scan:** clean; every code step is complete.

**Type consistency:** `classify(rel, vectors, labeled, k)` → `Classification(folder, share, neighbors)` used identically in T2/T5; `move_note` → `{"from","to","rewrites"}` consumed by `record_move` (T4) with that exact shape; `run_sweep` report keys `moved/proposals/skipped/cache_rebuilt` match T5 tests and T6 tool docstring; `undo_move(vault, note_rel)` (organizer) vs MCP `undo_move(path)` wrapper — distinct namespaces, wrapper delegates.

**Judgment calls made explicit:** (1) CLI applies by default while the MCP tool dry-runs by default — the CLI is the scheduled autonomous surface, the tool is the interactive one; the asymmetry is deliberate and documented in both docstrings. (2) Filed notes with low-confidence disagreement are skipped rather than proposed, per spec, to avoid proposal spam for settled notes. (3) `iter_candidates` reads every candidate's frontmatter (one file read per note per sweep) — acceptable at personal-vault scale, same cost profile as existing scans.
