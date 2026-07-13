"""Bounded candidate selection for entity consolidation.

Given the graph's entities, decide which small same-type groups to ask the
LLM to dedupe, in size-capped batches, so consolidation work never scales
with total graph size. See
docs/superpowers/specs/2026-07-12-scalable-consolidation-design.md.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .embeddings import Embedder
from .hybrid import _cosine

SIM_THRESHOLD = 0.85
K_NEIGHBORS = 5
MAX_CLUSTER = 10
SLICE_SIZE = 200
MAX_ENTITIES_PER_CALL = 40

ENTITY_VECTOR_FILE = "entity_vectors.json"


def identity_text(entity: dict) -> str:
    aliases = ", ".join(entity.get("aliases") or [])
    return f"{entity['name']}\n{aliases}\n{entity.get('summary') or ''}".strip()


def identity_hash(entity: dict) -> str:
    return hashlib.sha256(identity_text(entity).encode("utf-8")).hexdigest()


def _load_entity_vectors(state_root: Path) -> dict[str, dict]:
    p = Path(state_root) / ENTITY_VECTOR_FILE
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_entity_vectors(state_root: Path, cache: dict[str, dict]) -> None:
    p = Path(state_root) / ENTITY_VECTOR_FILE
    p.write_text(json.dumps(cache), encoding="utf-8")


def compute_entity_vectors(
    entities: list[dict], state_root: Path, embedder: Embedder
) -> dict[str, list[float]]:
    cache = _load_entity_vectors(state_root)
    result: dict[str, list[float]] = {}
    to_embed: list[dict] = []
    hashes: dict[str, str] = {}
    for e in entities:
        h = identity_hash(e)
        hashes[e["path"]] = h
        cached = cache.get(e["path"])
        if cached and cached["hash"] == h:
            result[e["path"]] = cached["vec"]
        else:
            to_embed.append(e)
    if to_embed:
        vecs = embedder.embed_batch([identity_text(e) for e in to_embed])
        for e, vec in zip(to_embed, vecs):
            result[e["path"]] = vec
            cache[e["path"]] = {"hash": hashes[e["path"]], "vec": vec}
        _save_entity_vectors(state_root, cache)
    return result
