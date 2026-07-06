"""FastMCP server exposing the Tesseract vault to Claude."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import cache as cache_mod, graph, indexer, notes, search as search_mod, tasks as tasks_mod
from .extractor import CliExtractor
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


@mcp.tool()
def add_task(content: str, due: str | None = None) -> str:
    """Add a checkbox task to Claude/Tasks.md in Obsidian Tasks-plugin format.
    Optional due date as YYYY-MM-DD."""
    return tasks_mod.add_task(get_vault(), content, due=due)


@mcp.tool()
def list_tasks(include_done: bool = False, folder: str | None = None) -> list[dict]:
    """List checkbox tasks across the vault (open only by default)."""
    return tasks_mod.list_tasks(get_vault(), include_done=include_done, folder=folder)


@mcp.tool()
def query_notes(
    project: str | None = None,
    tags: list[str] | None = None,
    folder: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query notes by frontmatter metadata (Dataview-style). Returns each
    note's path and frontmatter. Without filters, lists notes that have
    frontmatter."""
    return graph.query_notes(
        get_vault(), project=project, tags=tags, folder=folder, limit=limit
    )


@mcp.tool()
def get_backlinks(path: str) -> list[str]:
    """List notes whose [[wikilinks]] point at the given note — use to see
    how a topic connects before extending it."""
    return graph.get_backlinks(get_vault(), path)


@mcp.tool()
def list_recent(n: int = 10) -> list[dict]:
    """Most recently modified notes, newest first — use to catch up on what
    changed in the vault."""
    return graph.list_recent(get_vault(), n=n)


def _make_extractor():
    return CliExtractor()


def _graph_db():
    db = indexer.db_path()
    if not db.exists():
        raise VaultError("Graph cache not built yet — run index_brain first.")
    return db


@mcp.tool()
def index_brain(force: bool = False) -> dict:
    """Index new/changed vault notes into the semantic graph (LLM entity
    extraction via the configured CLI backend). Returns counts including
    'remaining' — call again if remaining > 0. force=True re-indexes all."""
    return indexer.run(get_vault(), _make_extractor(), force=force)


@mcp.tool()
def find_entity(query: str, type: str | None = None) -> list[dict]:
    """Look up graph entities by name or alias (case-insensitive substring).
    Optional type filter: person, organization, domain, topic, project, source."""
    return cache_mod.find_entity(_graph_db(), query, type=type)


@mcp.tool()
def related_notes(path: str, hops: int = 2) -> list[dict]:
    """Notes connected to the given note through shared graph entities within
    N hops. Each result includes the entity chain explaining the connection —
    the GraphRAG way to gather context beyond text search."""
    return cache_mod.related_notes(_graph_db(), get_vault(), path, hops=hops)


@mcp.tool()
def graph_stats() -> dict:
    """Entity/edge/mention counts for the semantic graph."""
    return cache_mod.stats(_graph_db())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
