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


def _candidate_pairs(
    slice_entities: list[dict],
    all_entities: list[dict],
    vectors: dict[str, list[float]],
    *,
    k: int,
    threshold: float,
) -> set[tuple[str, str]]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in all_entities:
        by_type[e["type"]].append(e)
    pairs: set[tuple[str, str]] = set()
    for s in slice_entities:
        sv = vectors.get(s["path"])
        if sv is None:
            continue
        sims: list[tuple[float, str]] = []
        for other in by_type[s["type"]]:
            if other["path"] == s["path"]:
                continue
            ov = vectors.get(other["path"])
            if ov is None:
                continue
            c = _cosine(sv, ov)
            if c >= threshold:
                sims.append((c, other["path"]))
        sims.sort(reverse=True)
        for _, op in sims[:k]:
            pairs.add(tuple(sorted((s["path"], op))))
    return pairs


def _cluster_pairs(pairs: set[tuple[str, str]], *, max_cluster: int) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in pairs:
        union(a, b)

    groups: dict[str, list[str]] = defaultdict(list)
    for node in parent:
        groups[find(node)].append(node)

    clusters: list[list[str]] = []
    for members in groups.values():
        members.sort()
        for i in range(0, len(members), max_cluster):
            clusters.append(members[i:i + max_cluster])
    return clusters


def candidate_clusters(
    slice_entities: list[dict],
    all_entities: list[dict],
    vectors: dict[str, list[float]],
    *,
    k: int = K_NEIGHBORS,
    threshold: float = SIM_THRESHOLD,
    max_cluster: int = MAX_CLUSTER,
) -> list[list[dict]]:
    pairs = _candidate_pairs(
        slice_entities, all_entities, vectors, k=k, threshold=threshold
    )
    path_clusters = _cluster_pairs(pairs, max_cluster=max_cluster)
    by_path = {e["path"]: e for e in all_entities}
    return [
        [by_path[p] for p in members]
        for members in path_clusters
        if len(members) >= 2
    ]


def batch_clusters(
    clusters: list[list[dict]], *, max_entities_per_call: int = MAX_ENTITIES_PER_CALL
) -> list[list[list[dict]]]:
    batches: list[list[list[dict]]] = []
    current: list[list[dict]] = []
    count = 0
    for cluster in clusters:
        if current and count + len(cluster) > max_entities_per_call:
            batches.append(current)
            current, count = [], 0
        current.append(cluster)
        count += len(cluster)
    if current:
        batches.append(current)
    return batches


def _resume_after(by_path: list[dict], cursor: str | None) -> list[dict]:
    if cursor is None:
        return list(by_path)
    paths = [e["path"] for e in by_path]
    idx = bisect.bisect_right(paths, cursor)  # first index with path > cursor
    return by_path[idx:] + by_path[:idx]


def select_slice(
    entities: list[dict],
    checked_hash: dict[str, str],
    cursor: str | None,
    slice_size: int,
    *,
    backstop_due: bool,
) -> tuple[list[dict], str | None, bool]:
    by_path = sorted(entities, key=lambda e: e["path"])
    slice_ = [e for e in by_path if checked_hash.get(e["path"]) != identity_hash(e)]
    slice_ = slice_[:slice_size]
    new_cursor = cursor
    used_backstop = False
    if backstop_due and len(slice_) < slice_size:
        chosen = {e["path"] for e in slice_}
        for e in _resume_after(by_path, cursor):
            if len(slice_) >= slice_size:
                break
            if e["path"] in chosen:
                continue
            slice_.append(e)
            chosen.add(e["path"])
            new_cursor = e["path"]
            used_backstop = True
    return slice_, new_cursor, used_backstop
