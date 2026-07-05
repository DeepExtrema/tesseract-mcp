"""Structured note operations for the Claude/ subtree."""

from __future__ import annotations

import re
from datetime import datetime

import yaml

from .vault import Vault

AGENT_NAME = "claude"
_ILLEGAL = re.compile(r'[\\/:*?"<>|\[\]#^]')
_WHITESPACE = re.compile(r"\s+")


def safe_filename(title: str) -> str:
    cleaned = _ILLEGAL.sub("", title)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned or "untitled"


def make_frontmatter(
    *,
    project: str = "",
    tags: list[str] | None = None,
    agent: str = AGENT_NAME,
    created: datetime | None = None,
) -> str:
    created = created or datetime.now()
    meta = {
        "created": created.strftime("%Y-%m-%d %H:%M"),
        "agent": agent,
        "project": project,
        "tags": tags or [],
    }
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n"


def log_session(
    vault: Vault,
    title: str,
    content: str,
    project: str,
    tags: list[str],
    now: datetime | None = None,
) -> str:
    now = now or datetime.now()
    base = f"{now:%Y-%m-%d} {safe_filename(title)}"
    stem, n = base, 2
    while vault.resolve(f"Claude/Sessions/{stem}.md").exists():
        stem = f"{base} {n}"
        n += 1
    rel = f"Claude/Sessions/{stem}.md"
    vault.write(
        rel,
        make_frontmatter(project=project, tags=tags, created=now) + content + "\n",
    )
    vault.append("Claude/Index.md", f"- [[{stem}]] — {project}: {title}\n")
    return rel


def capture(vault: Vault, content: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    rel = f"Claude/Inbox/{now:%Y-%m-%d}.md"
    vault.append(rel, f"- {now:%H:%M} {content}\n")
    return rel


def _find_concept(vault: Vault, filename: str) -> str | None:
    folder = vault.resolve("Claude/Concepts")
    if folder.is_dir():
        target = filename.casefold()
        for p in folder.iterdir():
            if p.name.casefold() == target:
                return f"Claude/Concepts/{p.name}"
    return None


def upsert_concept(
    vault: Vault, name: str, content: str, now: datetime | None = None
) -> str:
    now = now or datetime.now()
    filename = f"{safe_filename(name)}.md"
    existing = _find_concept(vault, filename)
    if existing is None:
        rel = f"Claude/Concepts/{filename}"
        vault.write(
            rel,
            make_frontmatter(tags=["concept"], created=now)
            + f"# {name}\n\n{content}\n",
        )
        return rel
    vault.append(existing, f"\n## Update {now:%Y-%m-%d}\n\n{content}\n")
    return existing
