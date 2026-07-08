"""In-memory BM25 keyword ranking over vault notes.

Rebuilt fresh per query from the current vault scan rather than persisted:
rank-bm25 has no incremental-update API, and a personal vault (hundreds to
low-thousands of notes) is cheap to re-tokenize and re-rank on every call.
"""

from __future__ import annotations

import re

# BM25L (not BM25Okapi): Okapi's Robertson IDF yields zero scores on the
# small corpora typical of per-query vault scans and the plan's test fixtures.
from rank_bm25 import BM25L

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def rank(corpus: dict[str, str], query: str, limit: int = 50) -> list[tuple[str, float]]:
    if not corpus:
        return []
    paths = list(corpus.keys())
    tokenized_docs = [tokenize(corpus[p]) for p in paths]
    bm25 = BM25L(tokenized_docs)
    scores = bm25.get_scores(tokenize(query))
    ranked = sorted(zip(paths, scores), key=lambda pair: pair[1], reverse=True)
    return [(p, s) for p, s in ranked if s > 0][:limit]
