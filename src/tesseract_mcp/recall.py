"""Read-only bundle composition for the recall harness (digest/resume).

Deterministic packaging of what the /digest and /resume skills render —
no LLM calls, no writes. Each section degrades independently: a failure
becomes {"status": "error", ...} instead of killing the bundle.
Spec: docs/superpowers/specs/2026-07-10-recall-harness-design.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from . import librarian as librarian_mod
from . import tasks as tasks_mod
from .cache import find_entity
from .graph import _vault_files
from .indexer import db_path
from .search import parse_frontmatter
from .vault import Vault, VaultError

DIGEST_DEFAULT_DAYS = 7
TS_FMT = "%Y-%m-%d %H:%M"
ORGANIZER_NOTE = "Claude/Organizer.md"
DECISIONS_NOTE = "Claude/Decisions.md"


def _section(fn) -> dict:
    """Run one bundle section; failures degrade to a status payload."""
    try:
        out = fn()
        out["status"] = "ok"
        return out
    except Exception as e:  # noqa: BLE001 — a section must not kill the bundle
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


def _notes_since(
    vault: Vault, cutoff: datetime, folder: str | None = None
) -> list[dict]:
    out = []
    for path, rel in _vault_files(vault, folder):
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if mtime >= cutoff:
            out.append({"path": rel, "modified": mtime.strftime(TS_FMT)})
    out.sort(key=lambda n: n["modified"], reverse=True)
    return out


def digest_bundle(
    vault: Vault, since: str | None = None, now: datetime | None = None
) -> dict:
    now = now or datetime.now()
    if since:
        try:
            cutoff = datetime.strptime(since, "%Y-%m-%d")
        except ValueError as e:
            raise VaultError(f"since must be YYYY-MM-DD: {since!r}") from e
    else:
        cutoff = now - timedelta(days=DIGEST_DEFAULT_DAYS)

    def _tasks() -> dict:
        all_tasks = tasks_mod.list_tasks(vault, include_done=True)
        changed = {n["path"] for n in _notes_since(vault, cutoff)}
        return {
            "open": [t for t in all_tasks if not t["done"]],
            "done_recently": [
                t for t in all_tasks if t["done"] and t["path"] in changed
            ],
        }

    def _proposals() -> dict:
        state = librarian_mod.status(vault)
        pending = (state.get("health") or {}).get("pending_proposals", 0)
        return {"pending": pending, "detail_note": ORGANIZER_NOTE}

    return {
        "mode": "digest",
        "generated": now.strftime(TS_FMT),
        "since": cutoff.strftime("%Y-%m-%d"),
        "librarian": _section(lambda: {"report": librarian_mod.status(vault)}),
        "recent_notes": _section(lambda: {"notes": _notes_since(vault, cutoff)}),
        "inbox_captures": _section(
            lambda: {"notes": _notes_since(vault, cutoff, folder="Claude/Inbox")}
        ),
        "tasks": _section(_tasks),
        "proposals": _section(_proposals),
        "new_entities": _section(
            lambda: {"notes": _notes_since(vault, cutoff, folder="Claude/Graph")}
        ),
    }


def _body_excerpt(text: str, limit: int = 400) -> str:
    """First `limit` chars of the note body, frontmatter stripped."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return " ".join(text.split())[:limit]


def _session_notes(vault: Vault, project: str, limit: int) -> list[dict]:
    q = project.casefold()
    sessions = []
    for path, rel in _vault_files(vault, "Claude/Sessions"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        hay = f"{meta.get('project', '')} {path.stem}".casefold()
        if q not in hay:
            continue
        created = str(meta.get("created") or "")
        if not created:
            created = datetime.fromtimestamp(path.stat().st_mtime).strftime(TS_FMT)
        sessions.append(
            {"path": rel, "created": created, "excerpt": _body_excerpt(text)}
        )
    sessions.sort(key=lambda s: s["created"], reverse=True)
    return sessions[:limit]


def resume_bundle(vault: Vault, project: str, limit: int = 10) -> dict:
    q = project.casefold()

    def _decisions() -> dict:
        target = vault.resolve(DECISIONS_NOTE)
        if not target.is_file():
            return {"lines": []}
        lines = [
            ln for ln in target.read_text(encoding="utf-8").splitlines()
            if ln.startswith("- ") and q in ln.casefold()
        ]
        return {"lines": lines}

    def _open_tasks() -> dict:
        tasks = tasks_mod.list_tasks(vault)
        return {"tasks": [t for t in tasks if q in t["text"].casefold()]}

    def _entities() -> dict:
        db = db_path(vault.root)
        if not db.exists():
            return {"entities": [], "note": "graph cache not built"}
        found = find_entity(db, project)
        return {
            "entities": [
                {"name": e["name"], "type": e["type"],
                 "path": e["path"], "summary": e["summary"]}
                for e in found
            ]
        }

    return {
        "mode": "resume",
        "project": project,
        "sessions": _section(
            lambda: {"notes": _session_notes(vault, project, limit)}
        ),
        "decisions": _section(_decisions),
        "tasks": _section(_open_tasks),
        "entities": _section(_entities),
    }


def context_block(vault: Vault, project: str | None, budget: int = 2000) -> str:
    """Hook-friendly context snippet: latest sessions, open tasks, decisions."""
    if not project:
        return ""
    bundle = resume_bundle(vault, project, limit=10)
    parts: list[str] = []
    sessions = bundle.get("sessions") or {}
    if sessions.get("status") == "ok":
        for note in sessions.get("notes", [])[:3]:
            title = note["path"].rsplit("/", 1)[-1].removesuffix(".md")
            parts.append(f"Session: {title} — {note.get('excerpt', '')[:300]}")
    tasks_sec = bundle.get("tasks") or {}
    if tasks_sec.get("status") == "ok":
        for task in tasks_sec.get("tasks", [])[:5]:
            parts.append(f"Open task: {task['text']}")
    decisions = bundle.get("decisions") or {}
    if decisions.get("status") == "ok":
        parts.extend(decisions.get("lines", [])[:3])
    text = "\n".join(parts)
    if len(text) <= budget:
        return text
    cut = text[:budget]
    boundary = cut.rfind("\n")
    return cut[:boundary] if boundary > 0 else cut


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        parser = argparse.ArgumentParser(
            description="Recall harness helpers for hooks and skills.")
        parser.add_argument("--vault", required=True)
        parser.add_argument("--context", action="store_true")
        parser.add_argument("--project")
        parser.add_argument("--budget", type=int, default=2000)
        args = parser.parse_args()
        if args.context:
            block = context_block(Vault(args.vault), args.project, budget=args.budget)
            print(block, end="")
    except Exception:
        return


if __name__ == "__main__":
    main()
