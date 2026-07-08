"""Hybrid retrieval: BM25 keyword ranking + vector similarity, fused via
Reciprocal Rank Fusion. RRF merges by rank position rather than raw score,
so BM25 scores and cosine similarities never need to be normalized against
each other.
"""

from __future__ import annotations

from pathlib import Path

from . import bm25 as bm25_mod
from .embeddings import Embedder, get_note_vectors
from .search import Hit, iter_candidate_notes
from .vault import Vault

RRF_K = 60


def rrf_fuse(ranked_lists: list[list[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return [item for item, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _vector_rank(
    vectors: dict[str, list[float]], candidate_paths: set[str], query_vec: list[float], limit: int
) -> list[str]:
    scored = [
        (path, _cosine(vec, query_vec))
        for path, vec in vectors.items()
        if path in candidate_paths
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [p for p, s in scored if s > 0][:limit]


def _excerpt(text: str, rel: str, query: str) -> str:
    stem = rel.rsplit("/", 1)[-1][:-3]
    q = query.lower()
    if q in stem.lower():
        return "(title match)"
    for line in text.splitlines():
        if q in line.lower():
            return line.strip()
    return text.strip().splitlines()[0][:120] if text.strip() else ""


def _substring_rank(corpus: dict[str, str], query: str, limit: int) -> list[str]:
    q = query.lower()
    ranked: list[str] = []
    for rel, text in sorted(corpus.items()):
        stem = rel.rsplit("/", 1)[-1][:-3]
        if q in stem.lower():
            ranked.append(rel)
        elif any(q in line.lower() for line in text.splitlines()):
            ranked.append(rel)
        if len(ranked) >= limit:
            break
    return ranked


def hybrid_search(
    vault: Vault,
    state_root: str | Path,
    embedder: Embedder,
    query: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 20,
) -> list[Hit]:
    candidates = iter_candidate_notes(vault, tags, folder)
    if not candidates:
        return []
    corpus = dict(candidates)
    candidate_paths = set(corpus.keys())

    bm25_ranked = [p for p, _ in bm25_mod.rank(corpus, query, limit=50)]

    all_vectors = get_note_vectors(vault, state_root, embedder)
    query_vec = embedder.embed_batch([query])[0]
    vector_ranked = _vector_rank(all_vectors, candidate_paths, query_vec, limit=50)

    substring_ranked = _substring_rank(corpus, query, limit=50)
    fused = rrf_fuse([bm25_ranked, vector_ranked, substring_ranked])[:limit]
    return [Hit(rel, _excerpt(corpus[rel], rel, query)) for rel in fused]
