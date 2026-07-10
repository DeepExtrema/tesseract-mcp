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

# Source-checkout layout (src/tesseract_mcp/evals.py -> repo root). The
# fixture paths only make sense in a checkout, not an installed wheel;
# this harness is a repo-local dev tool.
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
