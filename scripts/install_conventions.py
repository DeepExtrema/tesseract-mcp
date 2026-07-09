"""Thin CLI wrapper — the implementation lives in tesseract_mcp.conventions.

Usage: python scripts/install_conventions.py C:\\Vaults\\Tesseract
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tesseract_mcp.conventions import install  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/install_conventions.py <vault-path>")
    root = Path(sys.argv[1])
    if not root.is_dir():
        sys.exit(f"Vault not found: {root}")
    made = install(root)
    print("Created:", ", ".join(made) if made else "nothing (already installed)")
