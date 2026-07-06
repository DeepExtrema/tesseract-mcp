"""Vault metadata queries: frontmatter, wikilink backlinks, recent files."""

from __future__ import annotations

import re
from datetime import datetime

from .search import SKIP_DIRS, parse_frontmatter
from .vault import Vault

_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def _vault_files(vault: Vault, folder: str | None = None):
    base = vault.resolve(folder) if folder else vault.root
    for path in sorted(base.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        yield path, "/".join(rel_parts)


def query_notes(
    vault: Vault,
    project: str | None = None,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Dataview-lite: filter notes by frontmatter fields, return metadata."""
    results: list[dict] = []
    for path, rel in _vault_files(vault, folder):
        meta = parse_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
        if project is not None and str(meta.get("project", "")).casefold() != project.casefold():
            continue
        if tags:
            note_tags = meta.get("tags") or []
            if not isinstance(note_tags, list):
                note_tags = [note_tags]
            note_tags = {str(t).casefold() for t in note_tags}
            if not {t.casefold() for t in tags} <= note_tags:
                continue
        if project is None and not tags and not meta:
            continue  # plain listing: only notes WITH frontmatter
        results.append({"path": rel, "frontmatter": {k: str(v) for k, v in meta.items()}})
        if len(results) >= limit:
            break
    return results


def get_backlinks(vault: Vault, path: str) -> list[str]:
    """Paths of notes whose [[wikilinks]] point at the given note."""
    target = vault.resolve(path)
    stem = target.stem.casefold()
    hits: list[str] = []
    for p, rel in _vault_files(vault):
        if p == target:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for link in _WIKILINK.findall(text):
            link_stem = link.strip().split("/")[-1].casefold()
            if link_stem == stem:
                hits.append(rel)
                break
    return hits


def list_recent(vault: Vault, n: int = 10) -> list[dict]:
    """Most recently modified notes, newest first."""
    entries = [
        (path.stat().st_mtime, rel) for path, rel in _vault_files(vault)
    ]
    entries.sort(reverse=True)
    return [
        {
            "path": rel,
            "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
        }
        for mtime, rel in entries[:n]
    ]
