# Search Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A golden-query evaluation harness that scores the production hybrid search pipeline (BM25L + vectors + RRF) with recall@k / success@k / MRR, over a committed synthetic fixture corpus and an optional private live-vault golden set.

**Architecture:** One new module `src/tesseract_mcp/evals.py` (metrics, golden loading, runner, CLI) that calls the existing `hybrid.hybrid_search` production path unchanged; committed benchmark assets under `evals/` (fixture mini-vault + golden.yaml); an env-guarded pytest threshold gate. Spec: `docs/superpowers/specs/2026-07-10-search-eval-harness-design.md`.

**Tech Stack:** Python 3.11+, pyyaml, rank-bm25, sentence-transformers (all already dependencies — add nothing to pyproject.toml).

## Global Constraints

- Repo is **public**: everything committed under `evals/` must be synthetic — never real vault content.
- Run all commands from the repo root `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`.
- Python is `.venv\Scripts\python`; tests are `.venv\Scripts\python -m pytest -q`.
- Base branch is `codex/architecture-roadmap`; commit to it directly (repo convention for docs+feature commits).
- All new files ASCII-only (PowerShell 5.1 cp1252 lesson).
- No new dependencies in `pyproject.toml`.
- Unit tests must not load the real sentence-transformers model; only the env-guarded gate test (Task 7) may.
- Every test that touches `indexer.state_dir` must set `TESSERACT_STATE_DIR` to a tmp dir via monkeypatch, so runs never pollute `~/.tesseract-mcp/`.
- End every commit message with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

**Existing interfaces used (do not modify them):**
- `hybrid.hybrid_search(vault, state_root, embedder, query, tags=None, folder=None, limit=20) -> list[search.Hit]` — `Hit` has `.path` (vault-relative, `/`-separated) and `.excerpt`.
- `indexer.state_dir(vault_root: str | Path | None = None) -> Path` — honors `TESSERACT_STATE_DIR` env override.
- `vault.Vault(root: str | Path)` — has `.root: Path`.
- Test fake pattern (see `tests/test_hybrid.py`): a class with `embed_batch(self, texts) -> list[list[float]]`.

---

### Task 1: Metrics and dataclasses (`evals.py` core)

**Files:**
- Create: `src/tesseract_mcp/evals.py`
- Create: `tests/test_evals.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (used by Tasks 2, 4, 5): `EvalConfigError(Exception)`; `GoldenQuery(id, query, expect, accept=[], tags=None, folder=None, note="")`; `QueryResult(id, hits, first_rank, recall_at, success_at, missing, skipped=False)`; `Scorecard(results, success_at, recall_at, mrr, skipped)`; `first_relevant_rank(hits: list[str], relevant: set[str]) -> int | None`; `recall_at_k(hits: list[str], expect: set[str], k: int) -> float`; `success_at_k(hits: list[str], relevant: set[str], k: int) -> bool`; constants `RETRIEVE_LIMIT = 20`, `KS = (5, 10)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evals.py
from tesseract_mcp.evals import (
    first_relevant_rank, recall_at_k, success_at_k,
)


def test_first_relevant_rank_is_one_based():
    assert first_relevant_rank(["a.md", "b.md", "c.md"], {"b.md"}) == 2


def test_first_relevant_rank_none_when_absent():
    assert first_relevant_rank(["a.md"], {"z.md"}) is None


def test_first_relevant_rank_empty_hits():
    assert first_relevant_rank([], {"z.md"}) is None


def test_recall_at_k_counts_expect_fraction_within_k():
    hits = ["a.md", "b.md", "c.md", "d.md"]
    assert recall_at_k(hits, {"a.md", "d.md"}, 2) == 0.5
    assert recall_at_k(hits, {"a.md", "d.md"}, 4) == 1.0


def test_recall_at_k_empty_expect_is_zero():
    assert recall_at_k(["a.md"], set(), 5) == 0.0


def test_success_at_k_any_relevant_in_top_k():
    assert success_at_k(["a.md", "b.md"], {"b.md"}, 2) is True
    assert success_at_k(["a.md", "b.md"], {"b.md"}, 1) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` (evals module does not exist).

- [ ] **Step 3: Write the module core**

```python
# src/tesseract_mcp/evals.py
"""Golden-query evaluation harness for hybrid search.

Scores the production retrieval path (hybrid.hybrid_search with real
embeddings) against golden query sets: a synthetic fixture corpus
committed under evals/, and optionally a private set stored in the live
vault at Claude/Evals.md. Metrics are rank-based (success@k, recall@k,
MRR) with threshold floors asserted in tests -- never exact-order
assertions, which punish semantic recall improvements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_VAULT = REPO_ROOT / "evals" / "vault"
FIXTURE_GOLDEN = REPO_ROOT / "evals" / "golden.yaml"
LIVE_GOLDEN_REL = "Claude/Evals.md"
HISTORY_FILE = "eval_history.jsonl"
RETRIEVE_LIMIT = 20
KS = (5, 10)


class EvalConfigError(Exception):
    """Bad golden set, bad paths, or bad invocation -- exit code 2."""


@dataclass
class GoldenQuery:
    id: str
    query: str
    expect: list[str]
    accept: list[str] = field(default_factory=list)
    tags: list[str] | None = None
    folder: str | None = None
    note: str = ""


@dataclass
class QueryResult:
    id: str
    hits: list[str]
    first_rank: int | None
    recall_at: dict[int, float]
    success_at: dict[int, bool]
    missing: list[str]
    skipped: bool = False


@dataclass
class Scorecard:
    results: list[QueryResult]
    success_at: dict[int, float]
    recall_at: dict[int, float]
    mrr: float
    skipped: int


def first_relevant_rank(hits: list[str], relevant: set[str]) -> int | None:
    for i, h in enumerate(hits, start=1):
        if h in relevant:
            return i
    return None


def recall_at_k(hits: list[str], expect: set[str], k: int) -> float:
    if not expect:
        return 0.0
    return len(set(hits[:k]) & expect) / len(expect)


def success_at_k(hits: list[str], relevant: set[str], k: int) -> bool:
    return any(h in relevant for h in hits[:k])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/evals.py tests/test_evals.py
git commit -m "feat(evals): metrics core for search eval harness

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Golden-set loading and path validation

**Files:**
- Modify: `src/tesseract_mcp/evals.py` (append)
- Modify: `tests/test_evals.py` (append)

**Interfaces:**
- Consumes: Task 1 (`GoldenQuery`, `EvalConfigError`).
- Produces (used by Tasks 4, 5): `load_golden(path: str | Path) -> list[GoldenQuery]` (whole-file YAML for `.yaml`/`.yml`, first fenced yaml block for `.md`); `validate_paths(queries, vault_root, strict: bool) -> dict[str, list[str]]` (qid -> missing paths; raises `EvalConfigError` when strict and anything is missing).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_evals.py`)

~~~python
import pytest

from tesseract_mcp.evals import EvalConfigError, load_golden, validate_paths

GOLDEN_YAML = """\
- id: q1
  query: alpha beta
  expect: [Notes/A.md]
  accept: [Notes/B.md]
  tags: [x]
  folder: Notes
  note: demo
- id: q2
  query: gamma
  expect: [Notes/B.md]
"""


def test_load_golden_yaml(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text(GOLDEN_YAML, encoding="utf-8")
    qs = load_golden(p)
    assert [q.id for q in qs] == ["q1", "q2"]
    assert qs[0].accept == ["Notes/B.md"]
    assert qs[0].tags == ["x"] and qs[0].folder == "Notes"
    assert qs[1].accept == [] and qs[1].tags is None


def test_load_golden_from_markdown_fence(tmp_path):
    p = tmp_path / "Evals.md"
    p.write_text("# Golden\n\n```yaml\n" + GOLDEN_YAML + "```\n", encoding="utf-8")
    assert [q.id for q in load_golden(p)] == ["q1", "q2"]


def test_load_golden_markdown_without_fence_errors(tmp_path):
    p = tmp_path / "Evals.md"
    p.write_text("no yaml here", encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_golden(p)


def test_load_golden_duplicate_id_errors(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text(
        "- {id: q1, query: a, expect: [A.md]}\n- {id: q1, query: b, expect: [B.md]}\n",
        encoding="utf-8",
    )
    with pytest.raises(EvalConfigError):
        load_golden(p)


def test_load_golden_empty_expect_errors(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text("- {id: q1, query: a, expect: []}\n", encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_golden(p)


def test_load_golden_missing_file_errors(tmp_path):
    with pytest.raises(EvalConfigError):
        load_golden(tmp_path / "nope.yaml")


def _mini_vault(tmp_path):
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "A.md").write_text("alpha", encoding="utf-8")
    return tmp_path


def test_validate_paths_strict_raises_listing_missing(tmp_path):
    root = _mini_vault(tmp_path)
    qs = [GoldenQuery(id="q1", query="a", expect=["Notes/A.md", "Notes/GONE.md"])]
    with pytest.raises(EvalConfigError, match="GONE.md"):
        validate_paths(qs, root, strict=True)


def test_validate_paths_lenient_returns_missing_map(tmp_path):
    root = _mini_vault(tmp_path)
    qs = [GoldenQuery(id="q1", query="a", expect=["Notes/GONE.md"])]
    assert validate_paths(qs, root, strict=False) == {"q1": ["Notes/GONE.md"]}
~~~

Also add `GoldenQuery` to the existing import from `tesseract_mcp.evals` at the top of the test file.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 6 pass (Task 1), 8 FAIL with ImportError on `load_golden`.

- [ ] **Step 3: Implement loader and validation** (append to `evals.py`; add `import re` and `import yaml` to the imports block)

```python
_YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)


def load_golden(path: str | Path) -> list[GoldenQuery]:
    p = Path(path)
    if not p.is_file():
        raise EvalConfigError(f"golden file not found: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".md":
        m = _YAML_FENCE_RE.search(text)
        if not m:
            raise EvalConfigError(f"no ```yaml block found in {p}")
        text = m.group(1)
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise EvalConfigError(f"invalid YAML in {p}: {e}") from e
    if not isinstance(raw, list):
        raise EvalConfigError(f"golden set must be a YAML list: {p}")
    queries: list[GoldenQuery] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or "id" not in item or "query" not in item:
            raise EvalConfigError(f"entry {i} in {p} needs 'id' and 'query'")
        queries.append(
            GoldenQuery(
                id=str(item["id"]),
                query=str(item["query"]),
                expect=[str(x) for x in item.get("expect") or []],
                accept=[str(x) for x in item.get("accept") or []],
                tags=[str(t) for t in item["tags"]] if item.get("tags") else None,
                folder=str(item["folder"]) if item.get("folder") else None,
                note=str(item.get("note") or ""),
            )
        )
    seen: set[str] = set()
    for q in queries:
        if q.id in seen:
            raise EvalConfigError(f"duplicate golden id: {q.id}")
        seen.add(q.id)
        if not q.expect:
            raise EvalConfigError(f"golden {q.id}: 'expect' must be non-empty")
    return queries


def validate_paths(
    queries: list[GoldenQuery], vault_root: str | Path, strict: bool
) -> dict[str, list[str]]:
    """Map of query id -> expect/accept paths missing from the vault."""
    root = Path(vault_root)
    missing: dict[str, list[str]] = {}
    for q in queries:
        gone = [p for p in q.expect + q.accept if not (root / p).is_file()]
        if gone:
            missing[q.id] = gone
    if strict and missing:
        detail = "; ".join(f"{qid}: {', '.join(ps)}" for qid, ps in missing.items())
        raise EvalConfigError(f"golden paths missing from vault: {detail}")
    return missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/evals.py tests/test_evals.py
git commit -m "feat(evals): golden-set loading (yaml + md fence) and path validation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Fixture corpus and golden set

**Files:**
- Create: `evals/vault/` (20 synthetic notes, exact contents below)
- Create: `evals/golden.yaml` (16 queries, exact contents below)
- Create: `evals/README.md`
- Modify: `tests/test_evals.py` (append one consistency test)

**Interfaces:**
- Consumes: Task 2 (`load_golden`, `validate_paths`).
- Produces: the committed benchmark assets at `evals.FIXTURE_VAULT` / `evals.FIXTURE_GOLDEN` that Tasks 5 and 7 run against.

- [ ] **Step 1: Write the failing consistency test** (append to `tests/test_evals.py`)

```python
from tesseract_mcp.evals import FIXTURE_GOLDEN, FIXTURE_VAULT


def test_fixture_golden_paths_all_exist():
    queries = load_golden(FIXTURE_GOLDEN)
    assert len(queries) == 16
    assert validate_paths(queries, FIXTURE_VAULT, strict=True) == {}
```

Run: `.venv\Scripts\python -m pytest tests/test_evals.py::test_fixture_golden_paths_all_exist -q`
Expected: FAIL — `EvalConfigError: golden file not found`.

- [ ] **Step 2: Create the fixture corpus with this script**

Save as `scratch_build_fixture.py` in the repo root, run once, then delete it. (A script guarantees exact, reproducible content; the committed artifact is the files, not the script.)

```python
from pathlib import Path

ROOT = Path("evals/vault")

NOTES = {
"Projects/Solar Balcony.md": """---
tags: [project, energy]
---
# Solar Balcony

Sizing the inverter for two 400W panels on the south rail.
Micro-inverter beats string inverter at this scale; check balcony load limit.
Next: order mounting hooks, measure rail spacing.
""",
"Projects/Homelab.md": """---
tags: [project, tech]
---
# Homelab

Proxmox cluster on two mini PCs plus the NAS for shared storage.
VLAN for IoT devices; backups nightly to the NAS, weekly offsite.
""",
"Projects/Garden Irrigation.md": """---
tags: [project]
---
# Garden Irrigation

Drip line spacing: 30cm for the tomato bed, 15cm for herbs.
Moisture sensor calibration: dry soil reads 780, saturated reads 310.
Controller waters at dawn when the reading crosses the threshold.
""",
"Areas/Finance/Invoices.md": """# Invoices

Outstanding balance: plumber invoice, 240 due Friday.
Utility bill still unpaid from last month.
Send the freelance invoice to Acme Robotics for June work.
""",
"Areas/Finance/Tax Prep 2026.md": """# Tax Prep 2026

Collect deduction receipts: home office, education, charity.
Deadline for the filing extension is October.
""",
"Areas/Health/Knee Rehab.md": """# Knee Rehab

Physio plan: wall sits, step-downs, banded side walks.
Three sets daily; add load only when pain stays under 3 of 10.
""",
"Areas/Health/Sleep Log.md": """# Sleep Log

Average 6h40m this week. Screens off by 23:00 helped twice.
Caffeine cutoff at noon still not consistent.
""",
"Notes/Sourdough Starter.md": """# Sourdough Starter

Feeding schedule: 1:5:5 at 100% hydration, twice daily in summer.
If the loaf comes out flat and dense, the starter likely peaked and
collapsed before mixing; feed it four hours earlier next time.
""",
"Notes/Espresso Dialing.md": """# Espresso Dialing

Shots running fast taste sour; grind finer and re-time to 28s.
Overextracted pulls turn harsh and dry at the finish.
Dose 18g, yield 36g as the baseline recipe.
""",
"Notes/Kubernetes Basics.md": """# Kubernetes Basics

The kubelet runs on every node and reconciles pod specs with reality.
Pods are the scheduling unit; services give them a stable address.
""",
"Notes/Long Ramble.md": """# Long Ramble

Weekly brain dump, unsorted.

Thought about the garden again while watering by hand; the moisture
meter idea from spring is still on the list. Also the balcony gets
afternoon shade so maybe the herbs move there.

Espresso this morning was fine, no complaints for once.

Money admin: checked one old invoice, the rest can wait for the weekend.

Read half an article about container orchestration and lost interest;
the homelab already does what I need with plain Proxmox.

Sleep was rough on Tuesday. Otherwise okay.

Long list of maybes: solar tracker toy, e-ink dashboard, replace the
drip timer battery, reorganize the tool shelf, scan paper receipts.
""",
"Claude/Sessions/2026-07-01 Homelab migration.md": """---
tags: [session]
---
# 2026-07-01 Homelab migration

Moved both nodes into the new Proxmox cluster with Alice Zhang pairing
on the network config. VLAN trunking works; NAS mounts survived reboot.
Decision: keep containers, skip full VMs for now.
""",
"Claude/Sessions/2026-07-03 Budget review.md": """---
tags: [session]
---
# 2026-07-03 Budget review

Monthly money check: paid the Acme Robotics invoice, flagged the unpaid
utility bill, set aside the plumber payment for Friday.
""",
"Claude/Concepts/Reciprocal Rank Fusion.md": """# Reciprocal Rank Fusion

Merges ranked lists by summing 1/(k + rank) per item across lists.
Rank positions, not raw scores, so heterogeneous rankers fuse cleanly.
""",
"Claude/Concepts/Vector Embeddings.md": """# Vector Embeddings

Text mapped to points in a shared space; nearby points mean similar text.
Never mix vectors from different models in one similarity ranking.
""",
"Claude/Graph/People/Alice Zhang.md": """---
type: person
---
# Alice Zhang

Network engineer friend; helped with the [[Projects/Homelab]] VLAN setup.
""",
"Claude/Graph/Organizations/Acme Robotics.md": """---
type: organization
---
# Acme Robotics

Freelance client for June; invoiced via the finance notes.
""",
"Claude/Graph/Topics/Fermentation.md": """---
type: topic
---
# Fermentation

Covers the sourdough starter, yogurt experiments, and hot sauce jars.
""",
"Inbox/Quick capture dentist.md": """Book a dentist appointment for a cleaning sometime next month.
""",
"Inbox/Idea solar tracker.md": """Tiny single-axis solar tracker for the balcony panels; servo plus RTC,
no light sensor needed if sun position is computed from time of day.
""",
}

for rel, text in NOTES.items():
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
print(f"wrote {len(NOTES)} notes under {ROOT}")
```

Run: `.venv\Scripts\python scratch_build_fixture.py` then `del scratch_build_fixture.py`
Expected: `wrote 20 notes under evals\vault`

Constraint check baked into the content: the `%` character appears in exactly one note (`Notes/Sourdough Starter.md`) — the `degen-percent` query below depends on that being unique.

- [ ] **Step 3: Create `evals/golden.yaml`** (exact content)

```yaml
# Golden queries for the fixture corpus in evals/vault/.
# Schema: docs/superpowers/specs/2026-07-10-search-eval-harness-design.md
- id: kw-inverter
  query: inverter sizing
  expect: [Projects/Solar Balcony.md]
  accept: [Inbox/Idea solar tracker.md]
  note: exact-keyword lane (BM25)
- id: kw-proxmox
  query: proxmox cluster
  expect: [Projects/Homelab.md]
  accept:
    - Claude/Sessions/2026-07-01 Homelab migration.md
    - Notes/Long Ramble.md
  note: keyword in three notes; the focused note should lead
- id: kw-kubelet
  query: kubelet
  expect: [Notes/Kubernetes Basics.md]
  note: single-note rare keyword
- id: para-owe
  query: who do I owe money to
  expect: [Areas/Finance/Invoices.md]
  accept:
    - Claude/Sessions/2026-07-03 Budget review.md
    - Notes/Long Ramble.md
  note: paraphrase, zero content-word overlap with target (vector lane)
- id: para-bread
  query: why is my bread not rising
  expect: [Notes/Sourdough Starter.md]
  accept: [Claude/Graph/Topics/Fermentation.md]
  note: paraphrase (note says loaf flat and dense), vector lane
- id: para-dentist
  query: need to see the tooth doctor
  expect: [Inbox/Quick capture dentist.md]
  note: paraphrase, zero overlap, vector lane
- id: kw-espresso-sour
  query: espresso shots sour
  expect: [Notes/Espresso Dialing.md]
  accept: [Notes/Long Ramble.md]
  note: mixed keyword + semantic
- id: title-rrf
  query: reciprocal rank fusion
  expect: [Claude/Concepts/Reciprocal Rank Fusion.md]
  note: title match
- id: title-knee
  query: knee rehab
  expect: [Areas/Health/Knee Rehab.md]
  note: title match
- id: tag-energy-panels
  query: panels
  tags: [energy]
  expect: [Projects/Solar Balcony.md]
  note: tag filter must exclude the untagged solar-tracker capture
- id: folder-sessions-migration
  query: migration
  folder: Claude/Sessions
  expect: [Claude/Sessions/2026-07-01 Homelab migration.md]
  note: folder filter
- id: degen-percent
  query: "%"
  expect: [Notes/Sourdough Starter.md]
  note: untokenizable query; exercises the substring fallback lane
- id: trap-moisture
  query: moisture sensor calibration
  expect: [Projects/Garden Irrigation.md]
  accept: [Notes/Long Ramble.md]
  note: focused note vs long rambling note (granularity trap)
- id: trap-drip
  query: drip line spacing
  expect: [Projects/Garden Irrigation.md]
  accept: [Notes/Long Ramble.md]
  note: granularity trap 2
- id: entity-alice
  query: Alice Zhang
  expect: [Claude/Graph/People/Alice Zhang.md]
  accept: [Claude/Sessions/2026-07-01 Homelab migration.md]
  note: entity-note retrieval by name
- id: entity-acme
  query: Acme Robotics
  expect: [Claude/Graph/Organizations/Acme Robotics.md]
  accept:
    - Areas/Finance/Invoices.md
    - Claude/Sessions/2026-07-03 Budget review.md
  note: entity note plus its mentions
```

- [ ] **Step 4: Create `evals/README.md`** (exact content)

~~~markdown
# Search evals

Benchmark assets for `python -m tesseract_mcp.evals`. Everything here is
**synthetic** — this repo is public, so real vault content never goes in.

- `vault/` — a ~20-note fixture mini-vault shaped like a real one
  (Projects/Areas/Notes/Inbox plus Claude/Sessions, Concepts, Graph).
- `golden.yaml` — 16 golden queries covering every retrieval lane:
  BM25 keyword, vector paraphrase, title match, tag/folder filters, the
  substring fallback (untokenizable query), granularity traps, and
  entity notes.

## Query schema

```yaml
- id: unique-slug
  query: the search text
  expect: [Path/Must Find.md]      # recall is computed on these
  accept: [Path/Also Fine.md]      # relevant too, never punished
  tags: [optional]                 # forwarded to hybrid_search
  folder: optional/subfolder       # forwarded to hybrid_search
  note: why this query exists
```

Adding a query: cover a behavior, not a note. If it documents a known
weakness (like the granularity traps), say so in `note` — those rows are
the before/after scoreboard for ranking changes.

Metrics: success@k / recall@k (k = 5, 10) and MRR at retrieval depth 20.
Thresholds live in `tests/test_evals.py` (env-guarded by
`TESSERACT_RUN_EVALS=1`). Rule: if the baseline sits below a floor, fix
the fixture or the golden set — never lower the floor to pass.

## Baseline

(recorded by Task 7 after the first real run)

| date | git | success@5 | success@10 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|---|---|
~~~

- [ ] **Step 5: Run the consistency test and the full suite**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 15 passed.

- [ ] **Step 6: Verify git actually sees the fixture files**

Run: `git status --porcelain evals/ | head -5`
Expected: `??` (or `A`) lines for `evals/` files. If `.gitignore` swallows them (it has vault-related rules), append this line to `.gitignore` and re-check:

```
!evals/vault/
```

- [ ] **Step 7: Commit**

```bash
git add evals/ tests/test_evals.py .gitignore
git commit -m "feat(evals): synthetic fixture corpus (20 notes) + 16 golden queries

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: The runner (`run_evals`)

**Files:**
- Modify: `src/tesseract_mcp/evals.py` (append)
- Modify: `tests/test_evals.py` (append)

**Interfaces:**
- Consumes: Task 1 metrics/dataclasses, Task 2 `validate_paths`; existing `hybrid.hybrid_search`, `vault.Vault`.
- Produces (used by Task 5): `run_evals(vault, state_root, embedder, queries, ks=KS, limit=RETRIEVE_LIMIT, lenient=False) -> Scorecard`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_evals.py`)

```python
from tesseract_mcp.evals import run_evals
from tesseract_mcp.vault import Vault


class KeywordEmbedder:
    """Same FakeEmbedder pattern as tests/test_hybrid.py: deterministic
    keyword-presence vectors so semantic ranking is testable modelless."""

    VOCAB = ["alpha", "beta", "gamma"]

    def embed_batch(self, texts):
        return [
            [1.0 if w in t.lower() else 0.0 for w in self.VOCAB] for t in texts
        ]


def _eval_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "vault"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes" / "A.md").write_text("alpha alpha content", encoding="utf-8")
    (root / "Notes" / "B.md").write_text("beta content", encoding="utf-8")
    return Vault(root)


def test_run_evals_scores_hits(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [GoldenQuery(id="q1", query="alpha", expect=["Notes/A.md"])]
    sc = run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)
    assert sc.results[0].first_rank == 1
    assert sc.success_at[5] == 1.0 and sc.recall_at[10] == 1.0
    assert sc.mrr == 1.0 and sc.skipped == 0


def test_run_evals_zero_when_never_found(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [GoldenQuery(id="q1", query="zzz-nowhere", expect=["Notes/B.md"])]
    sc = run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)
    assert sc.results[0].first_rank is None
    assert sc.mrr == 0.0 and sc.success_at[10] == 0.0


def test_run_evals_strict_raises_on_stale_path(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [GoldenQuery(id="q1", query="alpha", expect=["Notes/GONE.md"])]
    with pytest.raises(EvalConfigError):
        run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)


def test_run_evals_lenient_skips_fully_stale_query(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [
        GoldenQuery(id="stale", query="alpha", expect=["Notes/GONE.md"]),
        GoldenQuery(id="ok", query="alpha", expect=["Notes/A.md"]),
    ]
    sc = run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs, lenient=True)
    assert sc.skipped == 1
    assert sc.results[0].skipped is True
    # aggregates computed over the scored query only
    assert sc.success_at[5] == 1.0 and sc.mrr == 1.0


def test_run_evals_accept_counts_for_rank_not_recall(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    # B is accept-only; a query that only finds B succeeds but has recall 0
    qs = [GoldenQuery(id="q1", query="beta", expect=["Notes/A.md"],
                      accept=["Notes/B.md"])]
    sc = run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)
    r = sc.results[0]
    assert r.first_rank is not None          # B found -> relevant
    assert r.recall_at[10] == 0.0            # but expect A never showed
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 15 pass, 5 FAIL with ImportError on `run_evals`.

- [ ] **Step 3: Implement the runner** (append to `evals.py`; add `from . import hybrid` and `from .vault import Vault` to imports)

```python
def run_evals(
    vault: Vault,
    state_root: str | Path,
    embedder,
    queries: list[GoldenQuery],
    ks: tuple[int, ...] = KS,
    limit: int = RETRIEVE_LIMIT,
    lenient: bool = False,
) -> Scorecard:
    missing = validate_paths(queries, vault.root, strict=not lenient)
    results: list[QueryResult] = []
    for q in queries:
        gone = missing.get(q.id, [])
        if lenient and set(gone) >= set(q.expect):
            results.append(
                QueryResult(q.id, [], None, {k: 0.0 for k in ks},
                            {k: False for k in ks}, gone, skipped=True)
            )
            continue
        hits = hybrid.hybrid_search(
            vault, state_root, embedder, q.query,
            tags=q.tags, folder=q.folder, limit=limit,
        )
        paths = [h.path for h in hits]
        expect = set(q.expect) - set(gone)
        relevant = (set(q.expect) | set(q.accept)) - set(gone)
        results.append(
            QueryResult(
                q.id, paths,
                first_relevant_rank(paths, relevant),
                {k: recall_at_k(paths, expect, k) for k in ks},
                {k: success_at_k(paths, relevant, k) for k in ks},
                gone,
            )
        )
    scored = [r for r in results if not r.skipped]
    n = len(scored) or 1
    return Scorecard(
        results=results,
        success_at={k: sum(r.success_at[k] for r in scored) / n for k in ks},
        recall_at={k: sum(r.recall_at[k] for r in scored) / n for k in ks},
        mrr=sum(1.0 / r.first_rank for r in scored if r.first_rank) / n,
        skipped=len(results) - len(scored),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/evals.py tests/test_evals.py
git commit -m "feat(evals): scorecard runner over hybrid_search with lenient live mode

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: CLI, table/JSON output, history

**Files:**
- Modify: `src/tesseract_mcp/evals.py` (append)
- Modify: `tests/test_evals.py` (append)

**Interfaces:**
- Consumes: Tasks 1-4; existing `indexer.state_dir`.
- Produces: `format_table(sc) -> str`, `to_json(sc) -> dict`, `append_history(state_root, sc, vault_path, golden_path) -> Path`, `_make_embedder()` (module-level factory, monkeypatch target), `main(argv=None) -> int`; `python -m tesseract_mcp.evals` entry.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_evals.py`)

```python
import json as jsonlib

from tesseract_mcp import evals as evals_mod
from tesseract_mcp.evals import append_history, format_table, main, to_json


def _scorecard(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [GoldenQuery(id="q1", query="alpha", expect=["Notes/A.md"])]
    return run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)


def test_format_table_has_aggregate_line(tmp_path, monkeypatch):
    out = format_table(_scorecard(tmp_path, monkeypatch))
    assert "MRR" in out and "success@10" in out and "q1" in out


def test_to_json_round_trips(tmp_path, monkeypatch):
    d = to_json(_scorecard(tmp_path, monkeypatch))
    assert d["mrr"] == 1.0
    assert d["queries"][0]["id"] == "q1"
    jsonlib.dumps(d)  # serializable


def test_append_history_writes_jsonl(tmp_path, monkeypatch):
    sc = _scorecard(tmp_path, monkeypatch)
    p = append_history(tmp_path / "state", sc, "vaultpath", "goldenpath")
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    rec = jsonlib.loads(lines[-1])
    assert rec["mrr"] == 1.0 and rec["vault"] == "vaultpath"


def test_main_fixture_mode_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    vault_root = tmp_path / "vault"
    (vault_root / "Notes").mkdir(parents=True)
    (vault_root / "Notes" / "A.md").write_text("alpha", encoding="utf-8")
    golden = tmp_path / "golden.yaml"
    golden.write_text("- {id: q1, query: alpha, expect: [Notes/A.md]}\n",
                      encoding="utf-8")
    monkeypatch.setattr(evals_mod, "_make_embedder", KeywordEmbedder)
    rc = main(["--vault", str(vault_root), "--golden", str(golden), "--json"])
    assert rc == 0
    out = jsonlib.loads(capsys.readouterr().out)
    assert out["mrr"] == 1.0
    history = tmp_path / "state" / "eval_history.jsonl"
    assert history.exists()


def test_main_bad_golden_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "vault").mkdir()
    rc = main(["--vault", str(tmp_path / "vault"),
               "--golden", str(tmp_path / "missing.yaml")])
    assert rc == 2
    assert "error:" in capsys.readouterr().err


def test_main_live_without_env_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TESSERACT_VAULT_PATH", raising=False)
    rc = main(["--live"])
    assert rc == 2
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 20 pass, 6 FAIL with ImportError.

- [ ] **Step 3: Implement CLI + output + history** (append to `evals.py`; extend imports with `import argparse`, `import json`, `import os`, `import subprocess`, `import sys`, `import time`, and `from . import indexer`)

```python
def format_table(sc: Scorecard) -> str:
    lines = [f"{'id':<28} {'rank':>4} {'r@10':>5}  miss"]
    for r in sc.results:
        if r.skipped:
            lines.append(f"{r.id:<28} {'skip':>4} {'-':>5}  {', '.join(r.missing)}")
            continue
        rank = str(r.first_rank) if r.first_rank else "-"
        lines.append(
            f"{r.id:<28} {rank:>4} {r.recall_at[10]:>5.2f}  {', '.join(r.missing)}"
        )
    scored = len(sc.results) - sc.skipped
    lines.append("-" * 60)
    lines.append(
        f"queries {scored}  skipped {sc.skipped}  "
        f"success@5 {sc.success_at[5]:.2f}  success@10 {sc.success_at[10]:.2f}  "
        f"recall@5 {sc.recall_at[5]:.2f}  recall@10 {sc.recall_at[10]:.2f}  "
        f"MRR {sc.mrr:.2f}"
    )
    return "\n".join(lines)


def to_json(sc: Scorecard) -> dict:
    return {
        "success_at_5": sc.success_at[5],
        "success_at_10": sc.success_at[10],
        "recall_at_5": sc.recall_at[5],
        "recall_at_10": sc.recall_at[10],
        "mrr": sc.mrr,
        "skipped": sc.skipped,
        "queries": [
            {
                "id": r.id,
                "first_rank": r.first_rank,
                "skipped": r.skipped,
                "recall_at_10": r.recall_at.get(10),
                "missing": r.missing,
            }
            for r in sc.results
        ],
    }


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except OSError:
        return None


def append_history(
    state_root: str | Path, sc: Scorecard, vault_path, golden_path
) -> Path:
    state = Path(state_root)
    state.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "git": _git_sha(),
        "vault": str(vault_path),
        "golden": str(golden_path),
        **to_json(sc),
    }
    p = state / HISTORY_FILE
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return p


def _make_embedder():
    from .embeddings import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tesseract_mcp.evals",
        description="Golden-query evaluation for hybrid search.",
    )
    p.add_argument("--vault", help="vault root (default: fixture corpus)")
    p.add_argument("--golden", help="golden file (default: evals/golden.yaml)")
    p.add_argument("--live", action="store_true",
                   help="use TESSERACT_VAULT_PATH and Claude/Evals.md")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--no-history", action="store_true")
    p.add_argument("--init-live", action="store_true",
                   help="create Claude/Evals.md template if absent, then exit")
    args = p.parse_args(argv)
    try:
        if args.live or args.init_live:
            root = os.environ.get("TESSERACT_VAULT_PATH")
            if not root:
                raise EvalConfigError(
                    "--live/--init-live require TESSERACT_VAULT_PATH"
                )
            vault_path = Path(root)
            golden_path = vault_path / LIVE_GOLDEN_REL
            lenient = True
        else:
            vault_path = Path(args.vault) if args.vault else FIXTURE_VAULT
            golden_path = Path(args.golden) if args.golden else FIXTURE_GOLDEN
            lenient = False
        vault = Vault(vault_path)
        if args.init_live:
            target, created = init_live(vault)
            print(f"{'created' if created else 'already exists'}: {target}")
            return 0
        queries = load_golden(golden_path)
        sc = run_evals(
            vault, indexer.state_dir(vault.root), _make_embedder(),
            queries, lenient=lenient,
        )
    except EvalConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(to_json(sc), indent=2) if args.as_json else format_table(sc))
    if not args.no_history:
        append_history(indexer.state_dir(vault.root), sc, vault_path, golden_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: `main` references `init_live`, which Task 6 implements. To keep this task green on its own, add this stub now (Task 6 replaces it):

```python
def init_live(vault: Vault) -> tuple[Path, bool]:
    raise EvalConfigError("--init-live not implemented yet")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/evals.py tests/test_evals.py
git commit -m "feat(evals): CLI scorecard with table/json output and history jsonl

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `--init-live` template for the vault golden set

**Files:**
- Modify: `src/tesseract_mcp/evals.py` (replace the Task 5 stub)
- Modify: `tests/test_evals.py` (append)

**Interfaces:**
- Consumes: Task 5 CLI (`--init-live` flag already wired to `init_live`).
- Produces: `init_live(vault: Vault) -> tuple[Path, bool]` — creates `Claude/Evals.md` from `LIVE_TEMPLATE`, returns `(path, created)`; never overwrites.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_evals.py`)

```python
from tesseract_mcp.evals import init_live


def test_init_live_creates_template_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "vault"
    root.mkdir()
    vault = Vault(root)
    target, created = init_live(vault)
    assert created is True and target.is_file()
    assert load_golden(target)[0].id == "example-constitution"
    marker = "USER EDIT"
    target.write_text(target.read_text(encoding="utf-8") + marker,
                      encoding="utf-8")
    target2, created2 = init_live(vault)
    assert created2 is False
    assert marker in target2.read_text(encoding="utf-8")


def test_main_init_live_uses_env_vault(tmp_path, monkeypatch, capsys):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("TESSERACT_VAULT_PATH", str(root))
    assert main(["--init-live"]) == 0
    assert "created" in capsys.readouterr().out
    assert (root / "Claude" / "Evals.md").is_file()
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv\Scripts\python -m pytest tests/test_evals.py -q`
Expected: 26 pass, 2 FAIL (stub raises / ImportError on `init_live` export is fine too).

- [ ] **Step 3: Replace the stub with the real implementation**

~~~python
LIVE_TEMPLATE = '''# Search eval golden set (live vault)

Queries for `python -m tesseract_mcp.evals --live`. Paths are
vault-relative. Stale paths are skipped and reported here, never fatal:
this vault legitimately drifts. Add a query whenever a search annoys
you -- that is the whole curation strategy.

```yaml
- id: example-constitution
  query: what are the rules for agents writing to the vault
  expect:
    - Claude/README.md
  note: seed example -- replace or extend freely
```
'''


def init_live(vault: Vault) -> tuple[Path, bool]:
    target = Path(vault.root) / LIVE_GOLDEN_REL
    if target.exists():
        return target, False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(LIVE_TEMPLATE, encoding="utf-8")
    return target, True
~~~

- [ ] **Step 4: Run the whole suite**

Run: `.venv\Scripts\python -m pytest -q`
Expected: all tests pass (existing suite + 28 eval tests), no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/evals.py tests/test_evals.py
git commit -m "feat(evals): --init-live template writer for Claude/Evals.md

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Baseline run, threshold gate, docs

**Files:**
- Modify: `tests/test_evals.py` (append gate test)
- Modify: `evals/README.md` (fill baseline table row)
- Modify: `docs/ARCHITECTURE.md` (one module-map row)

**Interfaces:**
- Consumes: everything above.
- Produces: the recorded baseline and the enforced floors (`success@10 >= 0.80`, `MRR >= 0.50`).

- [ ] **Step 1: Add the env-guarded gate test** (append to `tests/test_evals.py`)

```python
import os


@pytest.mark.skipif(
    not os.environ.get("TESSERACT_RUN_EVALS"),
    reason="set TESSERACT_RUN_EVALS=1 to run the model-backed eval gate",
)
def test_fixture_thresholds_with_real_model(tmp_path, monkeypatch):
    """Floors, not exact ranks: if this fails after a ranking change, the
    change lost real recall. If the baseline sits below a floor, fix the
    fixture or golden set -- never lower the floor."""
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    queries = load_golden(FIXTURE_GOLDEN)
    sc = run_evals(
        Vault(FIXTURE_VAULT), tmp_path / "state",
        evals_mod._make_embedder(), queries,
    )
    assert sc.skipped == 0
    assert sc.success_at[10] >= 0.80
    assert sc.mrr >= 0.50
```

- [ ] **Step 2: Run the baseline for real** (downloads bge-micro-v2 on first use, ~60MB)

Run: `.venv\Scripts\python -m tesseract_mcp.evals --json > baseline.json` then inspect `baseline.json`.
Expected: exit 0; a full scorecard; typical healthy numbers are success@10 close to 1.0 and MRR well above 0.6 (keyword/title queries should hit rank 1; only the paraphrase and trap rows may rank lower).

- [ ] **Step 3: Run the gate**

Run (PowerShell): `$env:TESSERACT_RUN_EVALS = "1"; .venv\Scripts\python -m pytest tests/test_evals.py::test_fixture_thresholds_with_real_model -v; Remove-Item Env:TESSERACT_RUN_EVALS`
Expected: PASS. If it fails a floor, the fixture/golden content is at fault (e.g. a paraphrase pair the model genuinely cannot bridge) — adjust the *corpus or query* (e.g. soften the paraphrase, or move the target note to `accept` of a different query) and re-run Step 2. Do not lower the floors.

- [ ] **Step 4: Record the baseline**

Fill the table row in `evals/README.md` from `baseline.json` (date, `git rev-parse --short HEAD`, the five aggregates), then delete `baseline.json`.

- [ ] **Step 5: Add the module-map row to `docs/ARCHITECTURE.md`**

In the section-7 table, insert alphabetically after the `embeddings.py` row:

```markdown
| `evals.py` | Golden-query eval harness: scores hybrid search (success@k, recall@k, MRR) against the fixture corpus in `evals/` or a private `Claude/Evals.md` set in the live vault. |
```

- [ ] **Step 6: Full suite + commit**

Run: `.venv\Scripts\python -m pytest -q`
Expected: all pass (gate test reported as skipped without the env var).

```bash
git add tests/test_evals.py evals/README.md docs/ARCHITECTURE.md
git commit -m "test(evals): model-backed threshold gate + recorded baseline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review (completed)

- **Spec coverage:** metrics/locked definitions → Task 1+4; yaml+md-fence loading, strict/lenient validation → Task 2; fixture corpus with all query lanes incl. `%` fallback and granularity traps → Task 3; production-path runner → Task 4; CLI flags, exit codes, history jsonl, `TESSERACT_STATE_DIR` honored → Task 5; `Claude/Evals.md` + `--init-live` never-overwrite → Task 6; env-guarded floors, baseline record, fix-fixture-not-floor rule → Task 7. Non-goals untouched. No gaps found.
- **Placeholder scan:** none — every step has full code/content; the one intentional stub (`init_live` in Task 5) is explicit, tested around, and replaced in Task 6.
- **Type consistency:** `run_evals(vault, state_root, embedder, queries, ks, limit, lenient)` used identically in Tasks 4, 5, 7; `Scorecard`/`QueryResult` field names match across `format_table`, `to_json`, tests; `_make_embedder` referenced via `evals_mod._make_embedder` in tests matching the module-level def.
