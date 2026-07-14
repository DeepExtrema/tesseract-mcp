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
    resolve_redirect,
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


def retire_note(vault: Vault, rel: str, now: datetime, reason: str) -> None:
    """Replace an entity note with a retired tombstone. Aliases stay in the
    frontmatter and the summary stays in the body for audit/revival."""
    text = vault.read(rel)
    meta = parse_frontmatter(text)
    meta["retired"] = now.strftime("%Y-%m-%d %H:%M")
    summary = _entity_summary(text)
    stem = rel.rsplit("/", 1)[-1][:-3]
    body = (f"# {stem}\n\n"
            + (f"{summary}\n\n" if summary else "")
            + f"Retired: {reason}.\n")
    fm = "---\n" + yaml.safe_dump(meta, sort_keys=False,
                                  default_flow_style=None) + "---\n\n"
    vault.write(rel, fm + body, overwrite=True)


def _target_status(vault: Vault, path: str) -> tuple[str, str | None]:
    """('live', None) | ('stub', canonical-or-None) | ('gone', None)."""
    try:
        p = vault.resolve(path + ".md")
    except VaultError:
        return "gone", None
    if not p.is_file():
        return "gone", None
    meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
    if meta.get("retired"):
        return "gone", None
    if meta.get("merged_into"):
        return "stub", resolve_redirect(vault, path)
    return "live", None


def repair_relations(
    vault: Vault, limit: int = MAX_RELATION_FIXES_PER_SWEEP
) -> dict:
    """Rewrite relation lines whose target is a merge stub to the final
    canonical; drop lines whose target is retired or missing. Bounded."""
    graph_dir = vault.resolve(GRAPH_ROOT)
    fixed = removed = 0
    if not graph_dir.is_dir():
        return {"fixed": 0, "removed": 0}
    # targets repeat heavily across the graph's relation lines; repair never
    # touches a target's own frontmatter, so one status per target per pass
    status_cache: dict[str, tuple[str, str | None]] = {}
    for p in sorted(graph_dir.rglob("*.md")):
        if fixed + removed >= limit:
            break
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        if meta.get("merged_into") or meta.get("retired"):
            continue
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        changed = False
        for line in lines:
            m = _RELATION.match(line.strip())
            if not m or fixed + removed >= limit:
                out.append(line)
                continue
            target = m.group(2).strip()
            if target not in status_cache:
                status_cache[target] = _target_status(vault, target)
            status, canonical = status_cache[target]
            if status == "live":
                out.append(line)
                continue
            changed = True
            if status == "stub" and canonical:
                stem = canonical.rsplit("/", 1)[-1]
                new = f"- {m.group(1)} [[{canonical}|{stem}]]\n"
                if new in out or new in lines:
                    removed += 1  # canonical relation already present
                else:
                    out.append(new)
                    fixed += 1
            else:
                removed += 1
        if changed:
            rel = "/".join(p.relative_to(vault.root).parts)
            vault.write(rel, "".join(out), overwrite=True)
    return {"fixed": fixed, "removed": removed}


def flatten_stubs(vault: Vault, now: datetime) -> dict:
    """Point stub chains at the final canonical; retire dead-end stubs
    (target missing, retired, or a cycle)."""
    graph_dir = vault.resolve(GRAPH_ROOT)
    flattened = retired = 0
    if not graph_dir.is_dir():
        return {"flattened": 0, "retired_stubs": 0}
    for p in sorted(graph_dir.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        if not meta.get("merged_into") or meta.get("retired"):
            continue
        target = str(meta["merged_into"])
        status, _ = _target_status(vault, target)
        if status == "live":
            continue
        rel = "/".join(p.relative_to(vault.root).parts)
        final = resolve_redirect(vault, target)
        if final:
            meta["merged_into"] = final
            stem = final.rsplit("/", 1)[-1]
            fm = "---\n" + yaml.safe_dump(meta, sort_keys=False,
                                          default_flow_style=None) + "---\n\n"
            vault.write(rel, fm + f"# {p.stem}\n\nMerged into [[{stem}]].\n",
                        overwrite=True)
            flattened += 1
        else:
            retire_note(vault, rel, now, reason="merge redirect target gone")
            retired += 1
    return {"flattened": flattened, "retired_stubs": retired}
