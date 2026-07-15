"""Vector source for hybrid search: Smart Connections' embeddings where
fresh, a same-model local fallback (cached) where stale or missing.

The fallback MUST use the identical model Smart Connections uses
(TaylorAI/bge-micro-v2) — vectors from a different model live in an
unrelated space and would silently corrupt similarity ranking if mixed in.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from . import sc_adapter
from .search import iter_note_files
from .vault import Vault

FALLBACK_CACHE_FILE = "fallback_embeddings.json"


class Embedder(Protocol):
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_key: str = sc_adapter.MODEL_KEY):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_key)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts).tolist()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def load_vector_cache(path: Path) -> dict[str, dict]:
    """JSON vector cache keyed by path: {key: {"hash": ..., "vec": [...]}}."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}  # corrupt/truncated cache self-heals: treat as empty and rewrite


def save_vector_cache(path: Path, cache: dict[str, dict]) -> None:
    # temp file + atomic replace: an interrupted write must never leave a
    # truncated cache that would fail every later load.
    path = Path(path)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    os.replace(tmp, path)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fallback_path(state_root: Path) -> Path:
    return Path(state_root) / FALLBACK_CACHE_FILE


def _partition(
    vault: Vault, state_root: Path
) -> tuple[dict[str, str], dict[str, dict], dict[str, list[float]], list[str]]:
    """(note_texts, fallback_cache, vectors, stale): a vector for every note
    with a fresh Smart Connections entry or a matching fallback-cache entry;
    `stale` lists the rest — the notes a search would embed inline."""
    sc_vectors = sc_adapter.load_note_vectors(vault)
    note_texts = {
        rel: path.read_text(encoding="utf-8", errors="ignore")
        for path, rel in iter_note_files(vault)
    }
    fallback_cache = load_vector_cache(_fallback_path(state_root))
    vectors: dict[str, list[float]] = {}
    stale: list[str] = []
    for rel, text in note_texts.items():
        sc_entry = sc_vectors.get(rel)
        if sc_entry and sc_entry["fresh"]:
            vectors[rel] = sc_entry["vec"]
            continue
        cached = fallback_cache.get(rel)
        if cached and cached["hash"] == _content_hash(text):
            vectors[rel] = cached["vec"]
            continue
        stale.append(rel)
    return note_texts, fallback_cache, vectors, stale


def get_note_vectors(vault: Vault, state_root: Path, embedder: Embedder) -> dict[str, list[float]]:
    note_texts, fallback_cache, vectors, stale = _partition(vault, state_root)
    if stale:
        vecs = embedder.embed_batch([note_texts[rel] for rel in stale])
        for rel, vec in zip(stale, vecs):
            vectors[rel] = vec
            fallback_cache[rel] = {"hash": _content_hash(note_texts[rel]), "vec": vec}
        save_vector_cache(_fallback_path(state_root), fallback_cache)
    return vectors


def stale_notes(vault: Vault, state_root: Path) -> list[str]:
    """Rel paths of notes with no fresh Smart Connections vector AND no
    matching fallback-cache entry — the notes a search would embed inline.
    Read-only: never computes or caches anything."""
    return _partition(vault, state_root)[3]
