"""Install the Claude/ conventions tree into an Obsidian vault.

Moved from scripts/install_conventions.py so provision.py can import it;
the script remains as a thin CLI wrapper.
Idempotent: never overwrites anything that already exists.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONSTITUTION = REPO_ROOT / "vault" / "constitution.md"
ROOT_GUIDE = REPO_ROOT / "vault" / "root-guide.md"

INDEX_SEED = "# Index\n\nMap of agent-written notes. Session entries append below.\n\n"

DECISIONS_SEED = (
    "---\ncreated: 2026-07-06\nagent: claude\ntags: [decisions]\n---\n\n"
    "# Decisions\n\n"
    "Append-only log. One line per decision:\n"
    "`- YYYY-MM-DD — <decision> ([[session note]])`\n\n"
)


def install(vault_root: Path) -> list[str]:
    """Create missing pieces of the Claude/ tree. Returns what was created."""
    vault_root = Path(vault_root)
    claude = vault_root / "Claude"
    created: list[str] = []

    for folder in (
        claude / "Inbox",
        claude / "Sessions",
        claude / "Concepts",
        claude / "Answers",
        claude / "Digests",
    ):
        if not folder.is_dir():
            folder.mkdir(parents=True)
            created.append(str(folder.relative_to(vault_root)))

    readme = claude / "README.md"
    if not readme.exists():
        readme.write_text(
            CONSTITUTION.read_text(encoding="utf-8"), encoding="utf-8"
        )
        created.append("Claude/README.md")

    index = claude / "Index.md"
    if not index.exists():
        index.write_text(INDEX_SEED, encoding="utf-8")
        created.append("Claude/Index.md")

    decisions = claude / "Decisions.md"
    if not decisions.exists():
        decisions.write_text(DECISIONS_SEED, encoding="utf-8")
        created.append("Claude/Decisions.md")

    guide_text = ROOT_GUIDE.read_text(encoding="utf-8")
    for name in ("CLAUDE.md", "AGENTS.md"):
        guide = vault_root / name
        if not guide.exists():
            guide.write_text(guide_text, encoding="utf-8")
            created.append(name)

    return created
