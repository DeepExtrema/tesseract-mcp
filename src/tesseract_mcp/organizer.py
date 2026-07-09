"""Autonomous vault organizer: files notes where their semantic neighbors
live, via cosine-weighted K-nearest-neighbor folder vote.

Standing permission for autonomous moves in the human topical tree was
granted by Taimoor 2026-07-08 (see the Organizer section of the
constitution). Rails: journal + undo, proposals queue below the confidence
threshold, hard exclusions below.
"""

from __future__ import annotations

from .search import parse_frontmatter
from .vault import Vault

EXCLUDED_DIRS = frozenset({
    "Claude", "00 - Maps of Content", ".obsidian", ".smart-env",
    ".trash", ".space", "copilot",
})
VOTE_K = 10
VOTE_THRESHOLD = 0.7


def discover_taxonomy(vault: Vault) -> list[str]:
    """Existing top-level folders = the frozen taxonomy."""
    return sorted(
        p.name for p in vault.root.iterdir()
        if p.is_dir() and p.name not in EXCLUDED_DIRS
    )


def _wants_organizing(vault: Vault, rel: str) -> bool:
    text = vault.read(rel)
    return parse_frontmatter(text).get("organize") is not False


def iter_organized(vault: Vault) -> list[str]:
    """Rel paths of .md notes currently inside taxonomy folders."""
    out: list[str] = []
    for folder in discover_taxonomy(vault):
        for p in sorted((vault.root / folder).rglob("*.md")):
            out.append("/".join(p.relative_to(vault.root).parts))
    return out


def iter_candidates(vault: Vault) -> list[str]:
    """Notes the organizer may classify: vault-root .md files plus
    already-organized notes (re-checkable), minus organize: false."""
    root_notes = sorted(
        p.name for p in vault.root.iterdir()
        if p.is_file() and p.suffix == ".md"
    )
    return [
        rel for rel in root_notes + iter_organized(vault)
        if _wants_organizing(vault, rel)
    ]
