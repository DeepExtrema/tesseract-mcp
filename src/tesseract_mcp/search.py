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


def parse_frontmatter(text: str) -> dict:
    """Parse the leading YAML frontmatter block; {} on any failure."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        meta = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _frontmatter_tags(text: str) -> list[str]:
    tags = parse_frontmatter(text).get("tags") or []
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
        if tags and not {t.casefold() for t in tags} <= {
            t.casefold() for t in _frontmatter_tags(text)
        }:
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
