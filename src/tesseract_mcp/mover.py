"""Move a vault note while keeping every inbound link resolvable.

Only path-qualified wikilinks are rewritten: `[[old/path` immediately
followed by `]]`, `|`, or `#` (the lookahead prevents `[[Note 2` from
matching a move of `Note`). Bare `[[Stem]]` links keep working because the
stem does not change and the organizer's duplicate-stem guard ensures the
stem stays unique vault-wide.
"""

from __future__ import annotations

import os
import re

from . import indexer
from .search import SKIP_DIRS
from .vault import Vault, VaultError


def _no_md(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def _link_pattern(rel: str) -> re.Pattern:
    return re.compile(r"\[\[" + re.escape(_no_md(rel)) + r"(?=[\]|#])")


def duplicate_stem_exists(vault: Vault, rel: str) -> bool:
    stem = _no_md(rel).rsplit("/", 1)[-1].casefold()
    count = 0
    for p in vault.root.rglob("*.md"):
        parts = p.relative_to(vault.root).parts
        if SKIP_DIRS & set(parts):
            continue
        if p.stem.casefold() == stem:
            count += 1
            if count > 1:
                return True
    return False


def _rewrite_links(vault: Vault, src_rel: str, dst_rel: str) -> list[dict]:
    pattern = _link_pattern(src_rel)
    replacement = "[[" + _no_md(dst_rel)
    rewrites: list[dict] = []
    for p in sorted(vault.root.rglob("*.md")):
        parts = p.relative_to(vault.root).parts
        if SKIP_DIRS & set(parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        new_text, count = pattern.subn(replacement, text)
        if count:
            p.write_text(new_text, encoding="utf-8")
            rewrites.append({"path": "/".join(parts), "count": count})
    return rewrites


def move_note(vault: Vault, old_rel: str, new_rel: str) -> dict:
    src = vault.resolve(old_rel)
    dst = vault.resolve(new_rel)
    if not src.is_file():
        raise VaultError(f"Cannot move: not a file: {old_rel}")
    if dst.exists():
        raise VaultError(f"Cannot move: destination exists: {new_rel}")
    rewrites = _rewrite_links(vault, old_rel, new_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    manifest = indexer.load_manifest(vault.root)
    if old_rel in manifest["hashes"]:
        manifest["hashes"][new_rel] = manifest["hashes"].pop(old_rel)
        indexer.save_manifest(manifest, vault.root)
    return {"from": old_rel, "to": new_rel, "rewrites": rewrites}


def reverse_rewrites(
    vault: Vault, old_rel: str, new_rel: str, rewrite_paths: list[str]
) -> None:
    """Undo helper: rewrite new→old in exactly the files a move touched."""
    pattern = _link_pattern(new_rel)
    replacement = "[[" + _no_md(old_rel)
    for rel in rewrite_paths:
        p = vault.resolve(rel)
        if not p.is_file():
            continue
        p.write_text(
            pattern.sub(replacement, p.read_text(encoding="utf-8", errors="ignore")),
            encoding="utf-8",
        )
