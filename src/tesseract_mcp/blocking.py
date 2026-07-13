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
