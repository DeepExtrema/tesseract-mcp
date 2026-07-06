"""Markdown-native graph store: entity notes under Claude/Graph/."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from .extractor import Extraction
from .notes import AGENT_NAME, safe_filename
from .search import parse_frontmatter
from .vault import Vault, VaultError

GRAPH_ROOT = "Claude/Graph"
TYPE_FOLDERS = {
    "person": "People",
    "organization": "Organizations",
    "domain": "Domains",
    "topic": "Topics",
    "project": "Projects",
    "source": "Sources",
}
MENTIONS_HEADER = "## Mentions"
RELATIONS_HEADER = "## Relations"


def entity_rel_path(etype: str, name: str) -> str:
    return f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{safe_filename(name)}.md"


def _note_template(ent: dict, now: datetime) -> str:
    meta = {
        "created": now.strftime("%Y-%m-%d %H:%M"),
        "agent": AGENT_NAME,
        "entity": ent["type"],
        "aliases": ent.get("aliases") or [],
        "tags": [f"graph/{ent['type']}"],
    }
    fm = "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n"
    summary = ent.get("summary") or ""
    return (
        fm
        + f"# {ent['name']}\n\n"
        + (summary + "\n\n" if summary else "")
        + f"{MENTIONS_HEADER}\n\n{RELATIONS_HEADER}\n"
    )


class GraphStore:
    def __init__(self, vault: Vault):
        self.vault = vault

    def find_entity_note(self, etype: str, name: str) -> str | None:
        folder = self.vault.resolve(f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}")
        if not folder.is_dir():
            return None
        needle = name.casefold()
        for p in sorted(folder.glob("*.md")):
            if p.stem.casefold() == needle or safe_filename(name).casefold() == p.stem.casefold():
                return f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{p.name}"
            meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
            aliases = meta.get("aliases") or []
            if not isinstance(aliases, list):
                aliases = [aliases]
            if needle in {str(a).casefold() for a in aliases}:
                return f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{p.name}"
        return None

    def upsert_entity(self, ent: dict, now: datetime | None = None) -> str:
        rel, _created = self.upsert_entity_ex(ent, now=now)
        return rel

    def upsert_entity_ex(self, ent: dict, now: datetime | None = None) -> tuple[str, bool]:
        now = now or datetime.now()
        existing = self.find_entity_note(ent["type"], ent["name"])
        if existing is None:
            rel = entity_rel_path(ent["type"], ent["name"])
            self.vault.write(rel, _note_template(ent, now))
            return rel, True
        # merge new aliases into frontmatter (append-only semantics)
        new_aliases = [a for a in (ent.get("aliases") or []) if a]
        if new_aliases:
            text = self.vault.read(existing)
            meta = parse_frontmatter(text)
            current = meta.get("aliases") or []
            if not isinstance(current, list):
                current = [current]
            known = {str(a).casefold() for a in current} | {ent["name"].casefold()}
            added = [a for a in new_aliases if a.casefold() not in known]
            if added:
                meta["aliases"] = [str(a) for a in current] + added
                end = text.find("\n---", 3)
                body = text[end + 4 :]
                fm = "---\n" + yaml.safe_dump(meta, sort_keys=False, default_flow_style=None) + "---"
                self.vault.write(existing, fm + body, overwrite=True)
        return existing, False

    def _insert_line(self, rel: str, header: str, line: str, already: str) -> bool:
        text = self.vault.read(rel)
        start = text.find(header)
        if start == -1:  # section missing (human deleted it) — recreate at end
            text = text.rstrip() + f"\n\n{header}\n"
            start = text.find(header)
        next_header = text.find("\n## ", start + len(header))
        section = text[start : next_header if next_header != -1 else len(text)]
        if already in section:
            return False
        insert_at = next_header if next_header != -1 else len(text)
        updated = text[:insert_at].rstrip() + "\n" + line + "\n" + (
            text[insert_at:] if next_header != -1 else ""
        )
        self.vault.write(rel, updated, overwrite=True)
        return True

    def add_mention(self, entity_rel: str, note_path: str, evidence: str) -> bool:
        stem = Path(note_path).stem
        line = f"- [[{stem}]]" + (f" — {evidence}" if evidence else "")
        return self._insert_line(entity_rel, MENTIONS_HEADER, line, f"[[{stem}]]")

    def add_relation(self, src_rel: str, relation: str, dst_rel: str) -> bool:
        dst_stem = Path(dst_rel).stem
        line = f"- {relation} [[{dst_stem}]]"
        return self._insert_line(src_rel, RELATIONS_HEADER, line, line)

    def apply(self, note_path: str, extraction: Extraction) -> dict:
        counts = {"entities_created": 0, "entities_merged": 0,
                  "mentions_added": 0, "relations_added": 0}
        paths: dict[tuple[str, str], str] = {}

        def ensure(name: str, etype: str, ent: dict | None = None) -> str:
            key = (etype, name.casefold())
            if key not in paths:
                rel, created = self.upsert_entity_ex(
                    ent or {"name": name, "type": etype, "aliases": [], "summary": ""}
                )
                paths[key] = rel
                counts["entities_created" if created else "entities_merged"] += 1
            return paths[key]

        for ent in extraction.entities:
            rel = ensure(ent["name"], ent["type"], ent)
            if self.add_mention(rel, note_path, ent.get("summary", "")):
                counts["mentions_added"] += 1
        for r in extraction.relations:
            src = ensure(r["from"], r["from_type"])
            dst = ensure(r["to"], r["to_type"])
            if self.add_relation(src, r["rel"], dst):
                counts["relations_added"] += 1
        return counts
