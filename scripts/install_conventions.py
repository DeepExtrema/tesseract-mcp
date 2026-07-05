"""Install the Claude/ conventions tree into an Obsidian vault.

Usage: python scripts/install_conventions.py C:\\Vaults\\Tesseract
Idempotent: never overwrites anything that already exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONSTITUTION = REPO_ROOT / "vault" / "constitution.md"

INDEX_SEED = "# Index\n\nMap of agent-written notes. Session entries append below.\n\n"


def install(vault_root: Path) -> list[str]:
    """Create missing pieces of the Claude/ tree. Returns what was created."""
    vault_root = Path(vault_root)
    claude = vault_root / "Claude"
    created: list[str] = []

    for folder in (claude / "Inbox", claude / "Sessions", claude / "Concepts"):
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

    return created


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/install_conventions.py <vault-path>")
    root = Path(sys.argv[1])
    if not root.is_dir():
        sys.exit(f"Vault not found: {root}")
    made = install(root)
    print("Created:", ", ".join(made) if made else "nothing (already installed)")
