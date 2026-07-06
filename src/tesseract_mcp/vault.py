"""Filesystem access to the Obsidian vault with safety rules.

Two rules are enforced in code, not by convention:
- No path may escape the vault root.
- Writes outside the Claude/ subtree require confirm_outside_claude=True,
  which callers may only pass when the user explicitly asked for the write.
"""

from __future__ import annotations

import os
from pathlib import Path


class VaultError(Exception):
    """Raised when a vault operation is invalid."""


class Vault:
    CLAUDE_DIR = "Claude"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise VaultError(f"Vault root does not exist: {self.root}")

    def resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise VaultError(f"Path escapes the vault: {relative}")
        return candidate

    def in_claude(self, relative: str) -> bool:
        path = self.resolve(relative)
        claude_root = self.root / self.CLAUDE_DIR
        norm = os.path.normcase(str(path))
        claude_norm = os.path.normcase(str(claude_root))
        return norm == claude_norm or norm.startswith(claude_norm + os.sep)

    def read(self, relative: str) -> str:
        path = self.resolve(relative)
        if path.is_dir():
            raise VaultError(f"'{relative}' is a directory, not a note.")
        if not path.is_file():
            raise VaultError(f"Note not found: {relative}")
        return path.read_text(encoding="utf-8")

    def _check_write_allowed(self, relative: str, confirm_outside_claude: bool) -> None:
        if not self.in_claude(relative) and not confirm_outside_claude:
            raise VaultError(
                f"'{relative}' is outside Claude/. Pass confirm_outside_claude=True "
                "only when the user explicitly asked for this write."
            )

    def write(
        self,
        relative: str,
        content: str,
        *,
        overwrite: bool = False,
        confirm_outside_claude: bool = False,
    ) -> Path:
        path = self.resolve(relative)
        self._check_write_allowed(relative, confirm_outside_claude)
        if path.is_dir():
            raise VaultError(f"'{relative}' is a directory, not a note.")
        if path.exists() and not overwrite:
            raise VaultError(
                f"'{relative}' already exists. Pass overwrite=True to replace it."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp-write")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        return path

    def append(
        self,
        relative: str,
        content: str,
        *,
        confirm_outside_claude: bool = False,
    ) -> Path:
        path = self.resolve(relative)
        self._check_write_allowed(relative, confirm_outside_claude)
        if path.is_dir():
            raise VaultError(f"'{relative}' is a directory, not a note.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
        return path
