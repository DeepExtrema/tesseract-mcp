"""FastMCP server exposing the Tesseract vault to Claude."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import notes, search as search_mod
from .vault import Vault, VaultError

mcp = FastMCP("tesseract")

_vault: Vault | None = None


def get_vault() -> Vault:
    global _vault
    if _vault is None:
        root = os.environ.get("TESSERACT_VAULT_PATH")
        if not root:
            raise VaultError(
                "TESSERACT_VAULT_PATH is not set; point it at the vault folder."
            )
        _vault = Vault(root)
    return _vault


@mcp.tool()
def search_brain(
    query: str,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Full-text search across the whole vault. Optionally filter by
    frontmatter tags or restrict to a subfolder. Returns path + excerpt."""
    hits = search_mod.search(get_vault(), query, tags=tags, folder=folder, limit=limit)
    return [{"path": h.path, "excerpt": h.excerpt} for h in hits]


@mcp.tool()
def read_note(path: str) -> str:
    """Read a note by vault-relative path (e.g. 'Claude/Index.md')."""
    return get_vault().read(path)


@mcp.tool()
def log_session(
    title: str, content: str, project: str, tags: list[str] | None = None
) -> str:
    """Log a work session to Claude/Sessions/ and update Claude/Index.md.
    Use at the end of significant work: what we did, learned, decided."""
    return notes.log_session(
        get_vault(), title, content, project=project, tags=tags or []
    )


@mcp.tool()
def capture(content: str) -> str:
    """Append a quick timestamped thought to today's Claude/Inbox/ note."""
    return notes.capture(get_vault(), content)


@mcp.tool()
def upsert_concept(name: str, content: str) -> str:
    """Create or extend an evergreen concept note in Claude/Concepts/."""
    return notes.upsert_concept(get_vault(), name, content)


@mcp.tool()
def write_note(
    path: str,
    content: str,
    confirm_outside_claude: bool = False,
    overwrite: bool = False,
) -> str:
    """General write. Refuses paths outside Claude/ unless
    confirm_outside_claude=True — set it ONLY when the user explicitly
    asked for the write. Refuses to replace existing notes unless
    overwrite=True."""
    get_vault().write(
        path,
        content,
        overwrite=overwrite,
        confirm_outside_claude=confirm_outside_claude,
    )
    return path


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
