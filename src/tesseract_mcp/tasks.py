"""Task operations compatible with the Obsidian Tasks plugin format."""

from __future__ import annotations

import re
from datetime import datetime

from .search import SKIP_DIRS
from .vault import Vault

TASKS_FILE = "Claude/Tasks.md"
TASKS_SEED = (
    "---\nagent: claude\ntags: [tasks]\n---\n\n# Tasks\n\n"
    "## Live view (all open tasks, vault-wide)\n\n"
    "```tasks\nnot done\ngroup by filename\n```\n\n"
    "## Open\n\n"
)
_TASK_LINE = re.compile(r"^\s*[-*] \[(?P<state>[ xX])\] (?P<text>.+)$")


def add_task(vault: Vault, content: str, due: str | None = None) -> str:
    """Append a Tasks-plugin-format checkbox to Claude/Tasks.md."""
    if not vault.resolve(TASKS_FILE).exists():
        vault.write(TASKS_FILE, TASKS_SEED)
    line = f"- [ ] {' '.join(content.split())}"
    if due:
        datetime.strptime(due, "%Y-%m-%d")  # validate; raises ValueError if bad
        line += f" \U0001F4C5 {due}"
    vault.append(TASKS_FILE, line + "\n")
    return TASKS_FILE


def list_tasks(
    vault: Vault, include_done: bool = False, folder: str | None = None
) -> list[dict]:
    """Scan the vault for checkbox tasks. Returns path, text, done per task."""
    base = vault.resolve(folder) if folder else vault.root
    found: list[dict] = []
    for path in sorted(base.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            m = _TASK_LINE.match(line)
            if not m:
                continue
            done = m.group("state") in "xX"
            if done and not include_done:
                continue
            found.append(
                {
                    "path": "/".join(rel_parts),
                    "text": m.group("text").strip(),
                    "done": done,
                }
            )
    return found
