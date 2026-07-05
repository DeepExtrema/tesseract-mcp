"""Full-text search across the vault."""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from .vault import Vault

SKIP_DIRS = {".obsidian", ".trash", ".git"}


@dataclass
class Hit:
    path: str
    excerpt: str


def _frontmatter_tags(text: str) -> list[str]:
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    try:
        meta = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return []
    if not isinstance(meta, dict):
        return []
    tags = meta.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    return [str(t) for t in tags]


def search(
    vault: Vault,
    query: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 20,
) -> list[Hit]:
    base = vault.resolve(folder) if folder else vault.root
    q = query.lower()
    hits: list[Hit] = []
    for path in sorted(base.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if tags and not set(tags) <= set(_frontmatter_tags(text)):
            continue
        rel = "/".join(rel_parts)
        if q in path.stem.lower():
            hits.append(Hit(rel, "(title match)"))
        else:
            for line in text.splitlines():
                if q in line.lower():
                    hits.append(Hit(rel, line.strip()))
                    break
        if len(hits) >= limit:
            break
    return hits
