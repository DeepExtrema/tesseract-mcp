"""Vector source for hybrid search: Smart Connections' embeddings where
fresh, a same-model local fallback (cached) where stale or missing.

The fallback MUST use the identical model Smart Connections uses
(TaylorAI/bge-micro-v2) — vectors from a different model live in an
unrelated space and would silently corrupt similarity ranking if mixed in.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from . import sc_adapter
from .search import SKIP_DIRS
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


def _scan_note_texts(vault: Vault) -> dict[str, str]:
    texts: dict[str, str] = {}
    for path in sorted(vault.root.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        texts["/".join(rel_parts)] = path.read_text(encoding="utf-8", errors="ignore")
    return texts


def _load_fallback_cache(state_root: Path) -> dict[str, dict]:
    p = Path(state_root) / FALLBACK_CACHE_FILE
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_fallback_cache(state_root: Path, cache: dict[str, dict]) -> None:
    p = Path(state_root) / FALLBACK_CACHE_FILE
    p.write_text(json.dumps(cache), encoding="utf-8")


def get_note_vectors(vault: Vault, state_root: Path, embedder: Embedder) -> dict[str, list[float]]:
    sc_vectors = sc_adapter.load_note_vectors(vault)
    note_texts = _scan_note_texts(vault)
    fallback_cache = _load_fallback_cache(state_root)

    result: dict[str, list[float]] = {}
    to_embed: list[str] = []
    for rel, text in note_texts.items():
        sc_entry = sc_vectors.get(rel)
        if sc_entry and sc_entry["fresh"]:
            result[rel] = sc_entry["vec"]
            continue
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = fallback_cache.get(rel)
        if cached and cached["hash"] == content_hash:
            result[rel] = cached["vec"]
            continue
        to_embed.append(rel)

    if to_embed:
        vecs = embedder.embed_batch([note_texts[rel] for rel in to_embed])
        for rel, vec in zip(to_embed, vecs):
            result[rel] = vec
            fallback_cache[rel] = {
                "hash": hashlib.sha256(note_texts[rel].encode("utf-8")).hexdigest(),
                "vec": vec,
            }
        _save_fallback_cache(state_root, fallback_cache)

    return result
