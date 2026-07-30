"""Shared note-parsing and vault-scanning helpers (frontmatter, note walks)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

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


def body_text(text: str) -> str:
    """Note content with the leading YAML frontmatter block removed."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def as_str_list(value) -> list[str]:
    """Normalize a frontmatter value to a list of strings: None -> [],
    a bare scalar -> a 1-item list."""
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(v) for v in value]


def iter_note_files(
    vault: Vault, folder: str | None = None
) -> Iterator[tuple[Path, str]]:
    """(absolute path, vault-relative path) for every .md note under
    `folder` (default: the whole vault), skipping SKIP_DIRS."""
    base = vault.resolve(folder) if folder else vault.root
    for path in sorted(base.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        yield path, "/".join(rel_parts)


def iter_candidate_notes(
    vault: Vault, tags: list[str] | None = None, folder: str | None = None
) -> list[tuple[str, str]]:
    """(rel_path, text) for every note passing the tag/folder filters."""
    wanted = {t.casefold() for t in tags} if tags else None
    out: list[tuple[str, str]] = []
    for path, rel in iter_note_files(vault, folder):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if wanted and not wanted <= {
            t.casefold()
            for t in as_str_list(parse_frontmatter(text).get("tags"))
        }:
            continue
        out.append((rel, text))
    return out
