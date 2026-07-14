"""Graph deletion & orphaned-entity cleanup.

Make graph state converge to the facts that still exist: retract mentions of
deleted notes, propose retirement of unsupported entities, repair dangling
relations and merge-stub chains, prune consolidation caches. Mechanical
repairs auto-apply from the librarian sweep; retiring an entity note is
propose-only, applied via this module's CLI. See
docs/superpowers/specs/2026-07-13-graph-deletion-cleanup-design.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import yaml

from . import cache
from . import indexer
from .cache import _RELATION
from .consolidate import _entity_summary, _section_lines
from .graphstore import (
    GRAPH_ROOT,
    MENTIONS_HEADER,
    RELATIONS_HEADER,
    GraphStore,
)
from .search import parse_frontmatter
from .vault import Vault, VaultError

MAX_RETRACTIONS_PER_SWEEP = 100
MAX_RELATION_FIXES_PER_SWEEP = 200
MAX_PENDING_RETIREMENTS = 200


def deleted_notes(vault: Vault) -> list[str]:
    """Notes the manifest tracks (hashes or failure ledger) that no longer
    exist on disk. Organizer moves never appear here: mover.move_note
    transfers manifest entries on move."""
    manifest = indexer.load_manifest(vault.root)
    tracked = set(manifest["hashes"]) | set(manifest["failures"])
    return sorted(tracked - set(indexer.scan_notes(vault)))


def _mentioning_entities(vault: Vault, note_rel: str) -> list[str]:
    """Entity paths (no .md) whose notes hold a mention of note_rel. Prefers
    the cache; falls back to a markdown scan when the DB is missing."""
    db = indexer.db_path(vault.root)
    if db.exists():
        return cache.note_entity_paths(db, note_rel)
    target = note_rel[:-3] if note_rel.endswith(".md") else note_rel
    marker = f"[[{target}|"
    graph_dir = vault.resolve(GRAPH_ROOT)
    if not graph_dir.is_dir():
        return []
    return sorted(
        "/".join(p.relative_to(vault.root).parts)[:-3]
        for p in graph_dir.rglob("*.md")
        if marker in p.read_text(encoding="utf-8", errors="ignore")
    )


def retract_deleted(vault: Vault, limit: int = MAX_RETRACTIONS_PER_SWEEP) -> dict:
    """Retract mentions of deleted-but-tracked notes and prune their manifest
    entries, bounded per sweep."""
    deleted = deleted_notes(vault)
    todo = deleted[:limit]
    store = GraphStore(vault)
    removed = 0
    manifest = indexer.load_manifest(vault.root)
    for rel in todo:
        for entity_path in _mentioning_entities(vault, rel):
            try:
                if store.remove_mention(entity_path + ".md", rel):
                    removed += 1
            except VaultError:
                continue  # entity note deleted/renamed by hand
        manifest["hashes"].pop(rel, None)
        manifest["failures"].pop(rel, None)
    if todo:
        indexer.save_manifest(manifest, vault.root)
    return {"retracted_notes": len(todo), "removed_mentions": removed,
            "remaining": len(deleted) - len(todo)}
