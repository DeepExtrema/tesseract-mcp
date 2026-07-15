"""Vault metadata queries: frontmatter, wikilink backlinks, recent files."""

from __future__ import annotations

import re
from datetime import datetime

from .search import as_str_list, iter_note_files, parse_frontmatter
from .vault import Vault

_WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)  # dates and anything exotic -> ISO-ish string


def query_notes(
    vault: Vault,
    project: str | None = None,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Dataview-lite: filter notes by frontmatter fields, return metadata."""
    results: list[dict] = []
    for path, rel in iter_note_files(vault, folder):
        meta = parse_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
        if project is not None and str(meta.get("project", "")).casefold() != project.casefold():
            continue
        if tags:
            note_tags = {t.casefold() for t in as_str_list(meta.get("tags"))}
            if not {t.casefold() for t in tags} <= note_tags:
                continue
        if project is None and not tags and not meta:
            continue  # plain listing: only notes WITH frontmatter
        results.append({"path": rel, "frontmatter": {k: _json_safe(v) for k, v in meta.items()}})
        if len(results) >= limit:
            break
    return results


def get_backlinks(vault: Vault, path: str) -> list[str]:
    """Paths of notes whose [[wikilinks]] point at the given note."""
    target = vault.resolve(path)
    stem = target.stem.casefold()
    hits: list[str] = []
    for p, rel in iter_note_files(vault):
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
        (path.stat().st_mtime, rel) for path, rel in iter_note_files(vault)
    ]
    entries.sort(reverse=True)
    return [
        {
            "path": rel,
            "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
        }
        for mtime, rel in entries[:n]
    ]
