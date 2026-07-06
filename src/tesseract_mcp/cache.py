"""Derived SQLite cache over the Claude/Graph markdown (rebuildable anytime)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from .graphstore import GRAPH_ROOT, MENTIONS_HEADER, RELATIONS_HEADER
from .search import parse_frontmatter
from .vault import Vault

# Mentions are path-qualified: - [[Projects/Sentinel ESG|Sentinel ESG]] — evidence
# group 1 = full vault-relative path (no .md); group 2 = evidence (optional)
_MENTION = re.compile(r"^- \[\[([^\]|]+)\|[^\]]+\]\](?:\s+[—-]\s+(.*))?$")
# Relations are path-qualified the same way: - operates_in [[Claude/Graph/.../X|X]]
# group 1 = relation name; group 2 = full vault-relative path of target (no .md)
_RELATION = re.compile(r"^- (\w+) \[\[([^\]|]+)\|[^\]]+\]\]$")

SCHEMA = """
CREATE TABLE entities (path TEXT, name TEXT, type TEXT, summary TEXT, aliases TEXT);
CREATE TABLE edges (src_path TEXT, rel TEXT, dst_path TEXT);
CREATE TABLE mentions (entity_path TEXT, note_path TEXT, evidence TEXT);
"""


def _section(text: str, header: str) -> str:
    start = text.find(header)
    if start == -1:
        return ""
    nxt = text.find("\n## ", start + len(header))
    return text[start : nxt if nxt != -1 else len(text)]


def rebuild(vault: Vault, db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    graph_dir = vault.resolve(GRAPH_ROOT)
    if graph_dir.is_dir():
        for p in sorted(graph_dir.rglob("*.md")):
            text = p.read_text(encoding="utf-8", errors="ignore")
            meta = parse_frontmatter(text)
            if meta.get("merged_into"):
                continue
            etype = str(meta.get("entity") or "topic")
            name = p.stem
            m = re.search(r"^# (.+)$", text, re.MULTILINE)
            if m:
                name = m.group(1).strip()
            aliases = meta.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = [aliases]
            summary = ""
            after = text.split("\n# ", 1)[-1]
            for line in after.splitlines()[1:]:
                if line.strip() and not line.startswith("#"):
                    summary = line.strip()
                    break
            rel_path = "/".join(p.relative_to(vault.root).parts)
            entity_path = rel_path[:-3] if rel_path.endswith(".md") else rel_path
            con.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?)",
                (entity_path, name, etype, summary, json.dumps([str(a) for a in aliases])),
            )
            for line in _section(text, MENTIONS_HEADER).splitlines():
                mm = _MENTION.match(line.strip())
                if mm:
                    con.execute(
                        "INSERT INTO mentions VALUES (?,?,?)",
                        (entity_path, mm.group(1).strip(), mm.group(2) or ""),
                    )
            for line in _section(text, RELATIONS_HEADER).splitlines():
                rm = _RELATION.match(line.strip())
                if rm:
                    con.execute(
                        "INSERT INTO edges VALUES (?,?,?)",
                        (entity_path, rm.group(1), rm.group(2).strip()),
                    )
    con.commit()
    con.close()
    os.replace(tmp, db_path)


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def note_entity_paths(db_path: Path, note_path: str) -> list[str]:
    """Entity paths (no .md) that a note currently mentions, per the cache."""
    lookup = note_path[:-3] if note_path.endswith(".md") else note_path
    con = _connect(db_path)
    rows = con.execute(
        "SELECT DISTINCT entity_path FROM mentions WHERE note_path = ?", (lookup,)
    ).fetchall()
    con.close()
    return sorted(r["entity_path"] for r in rows)


def _path_name_map(con: sqlite3.Connection) -> dict[str, str]:
    return {r["path"]: r["name"] for r in con.execute("SELECT path, name FROM entities")}


def find_entity(db_path: Path, query: str, type: str | None = None) -> list[dict]:
    con = _connect(db_path)
    q = query.casefold()
    names_by_path = _path_name_map(con)
    results = []
    for row in con.execute("SELECT * FROM entities"):
        names = [row["name"]] + json.loads(row["aliases"])
        if not any(q in n.casefold() for n in names):
            continue
        if type and row["type"] != type:
            continue
        relations = [
            {"rel": e["rel"], "to": names_by_path.get(e["dst_path"], Path(e["dst_path"]).stem)}
            for e in con.execute(
                "SELECT rel, dst_path FROM edges WHERE src_path = ?", (row["path"],)
            )
        ]
        count = con.execute(
            "SELECT COUNT(*) FROM mentions WHERE entity_path = ?", (row["path"],)
        ).fetchone()[0]
        results.append(
            {"name": row["name"], "type": row["type"], "path": row["path"] + ".md",
             "summary": row["summary"], "aliases": json.loads(row["aliases"]),
             "relations": relations, "mention_count": count}
        )
    con.close()
    return results


def related_notes(db_path: Path, vault: Vault, path: str, hops: int = 2) -> list[dict]:
    con = _connect(db_path)
    # normalize once: entity_path/mentions.note_path are stored without .md
    lookup = path[:-3] if path.endswith(".md") else path
    names_by_path = _path_name_map(con)
    seed = [
        r["entity_path"]
        for r in con.execute(
            "SELECT entity_path FROM mentions WHERE note_path = ?", (lookup,)
        )
    ]
    reached: dict[str, str] = {
        e: names_by_path.get(e, Path(e).stem) for e in seed
    }  # entity_path -> chain (rendered with names)
    frontier = list(seed)
    for _ in range(max(0, hops - 1)):
        nxt = []
        for ent in frontier:
            for row in con.execute(
                "SELECT rel, dst_path FROM edges WHERE src_path = ? "
                "UNION SELECT rel, src_path FROM edges WHERE dst_path = ?",
                (ent, ent),
            ):
                other = row[1]
                if other not in reached:
                    other_name = names_by_path.get(other, Path(other).stem)
                    reached[other] = f"{reached[ent]} ({row[0]}) {other_name}"
                    nxt.append(other)
        frontier = nxt
    results = []
    seen = set()
    for ent, chain in reached.items():
        for row in con.execute(
            "SELECT note_path FROM mentions WHERE entity_path = ?", (ent,)
        ):
            note = row["note_path"]
            if note == lookup or note.startswith("Claude/Graph/"):
                continue
            note_full = note + ".md"
            if note_full in seen:
                continue
            seen.add(note_full)
            results.append({"path": note_full, "via": chain})
    con.close()
    return results


def stats(db_path: Path) -> dict:
    con = _connect(db_path)
    by_type: dict[str, int] = {}
    for row in con.execute("SELECT type, COUNT(*) c FROM entities GROUP BY type"):
        by_type[row["type"]] = row["c"]
    edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    mentions = con.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
    con.close()
    return {"entities": by_type, "edges": edges, "mentions": mentions}
