"""Markdown-native graph store: entity notes under Claude/Graph/.

This module owns the entity-note format — the section headers, the
mention/relation line shapes, and the frontmatter conventions. The parse
helpers (section, section_lines, entity_summary, MENTION_LINE,
RELATION_LINE) live here beside the code that writes those shapes.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from .extractor import Extraction
from .notes import AGENT_NAME, safe_filename
from .search import as_str_list, body_text, parse_frontmatter
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
REDIRECT_MAX_DEPTH = 5

# Mentions are path-qualified: - [[Projects/Sentinel ESG|Sentinel ESG]] — evidence
# group 1 = full vault-relative path (no .md); group 2 = evidence (optional)
MENTION_LINE = re.compile(r"^- \[\[([^\]|]+)\|[^\]]+\]\](?:\s+[—-]\s+(.*))?$")
# Relations are path-qualified the same way: - operates_in [[Claude/Graph/.../X|X]]
# group 1 = relation name; group 2 = full vault-relative path of target (no .md)
RELATION_LINE = re.compile(r"^- (\w+) \[\[([^\]|]+)\|[^\]]+\]\]$")


def section(text: str, header: str) -> str:
    """The slice of `text` from `header` up to the next `## ` heading."""
    start = text.find(header)
    if start == -1:
        return ""
    nxt = text.find("\n## ", start + len(header))
    return text[start : nxt if nxt != -1 else len(text)]


def section_lines(text: str, header: str) -> list[str]:
    """The `- ` bullet lines of one section."""
    return [l for l in section(text, header).splitlines() if l.startswith("- ")]


def entity_summary(text: str) -> str:
    """Body text between the `# name` H1 and `## Mentions` — the note
    template writes the entity summary there (not frontmatter)."""
    body = body_text(text)
    cut = body.find(MENTIONS_HEADER)
    if cut != -1:
        body = body[:cut]
    lines = [l for l in body.splitlines() if not l.startswith("# ")]
    return "\n".join(lines).strip()


def resolve_redirect(
    vault: Vault, path: str, max_depth: int = REDIRECT_MAX_DEPTH
) -> str | None:
    """Follow a merged_into chain from an entity path (no .md) to a live
    entity path. None on a dead end: missing file, retired note, cycle, or
    a chain deeper than max_depth."""
    seen: set[str] = set()
    current = path
    for _ in range(max_depth + 1):
        if current in seen:
            return None
        seen.add(current)
        try:
            p = vault.resolve(current + ".md")
        except VaultError:
            return None
        if not p.is_file():
            return None
        meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        if meta.get("retired"):
            return None
        nxt = meta.get("merged_into")
        if not nxt:
            return current
        current = str(nxt)
    return None


def entity_rel_path(etype: str, name: str) -> str:
    return f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{safe_filename(name)}.md"


def merge_aliases(vault: Vault, rel: str, candidates: list[str]) -> bool:
    """Fold new alias candidates into an entity note's frontmatter, skipping
    names already known (existing aliases or the note's own stem). False when
    nothing is new or the frontmatter is malformed (never corrupt the note)."""
    text = vault.read(rel)
    meta = parse_frontmatter(text)
    current = as_str_list(meta.get("aliases"))
    note_name = rel.rsplit("/", 1)[-1][:-3]
    known = {a.casefold() for a in current} | {note_name.casefold()}
    added = [a for a in candidates if a and a.casefold() not in known]
    if not added:
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    meta["aliases"] = current + added
    fm = "---\n" + yaml.safe_dump(meta, sort_keys=False, default_flow_style=None) + "---"
    vault.write(rel, fm + text[end + 4:], overwrite=True)
    return True


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
            meta = parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
            aliases = as_str_list(meta.get("aliases"))
            if (p.stem.casefold() != needle
                    and safe_filename(name).casefold() != p.stem.casefold()
                    and needle not in {a.casefold() for a in aliases}):
                continue
            rel = f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{p.name}"
            if meta.get("merged_into"):
                # a new mention of a merged name belongs on the canonical,
                # not the stub; a dead-end chain keeps the stub as-is
                canonical = resolve_redirect(self.vault, str(meta["merged_into"]))
                return (canonical + ".md") if canonical else rel
            return rel
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
        # merge new aliases (and a colliding display name) into frontmatter
        meta = parse_frontmatter(self.vault.read(existing))
        if meta.get("retired"):
            # revive: a retired entity that reappears in extraction comes
            # back as a fresh note, keeping the tombstone's recorded aliases
            revived = dict(ent)
            known = {str(a).casefold() for a in (ent.get("aliases") or [])}
            known.add(ent["name"].casefold())
            revived["aliases"] = list(ent.get("aliases") or []) + [
                a for a in as_str_list(meta.get("aliases"))
                if a.casefold() not in known]
            self.vault.write(existing, _note_template(revived, now),
                             overwrite=True)
            return existing, True
        # the note's canonical name is its H1; a differing incoming name is a
        # safe_filename collision we must not silently drop — record it as alias
        note_name = existing.rsplit("/", 1)[-1][:-3]
        candidates = list(ent.get("aliases") or [])
        if ent["name"].casefold() != note_name.casefold():
            candidates.append(ent["name"])
        merge_aliases(self.vault, existing, candidates)
        return existing, False

    def _insert_line(self, rel: str, header: str, line: str, already: str) -> bool:
        text = self.vault.read(rel)
        start = text.find(header)
        if start == -1:  # section missing (human deleted it) — recreate at end
            text = text.rstrip() + f"\n\n{header}\n"
            start = text.find(header)
        next_header = text.find("\n## ", start + len(header))
        current = text[start : next_header if next_header != -1 else len(text)]
        if already in current:
            return False
        insert_at = next_header if next_header != -1 else len(text)
        updated = text[:insert_at].rstrip() + "\n" + line + "\n" + (
            text[insert_at:] if next_header != -1 else ""
        )
        self.vault.write(rel, updated, overwrite=True)
        return True

    def add_mention(self, entity_rel: str, note_path: str, evidence: str) -> bool:
        target = note_path[:-3] if note_path.endswith(".md") else note_path
        stem = Path(note_path).stem
        marker = f"[[{target}|"  # unique per source note; dedup on this
        line = f"- [[{target}|{stem}]]" + (f" — {evidence}" if evidence else "")
        return self._insert_line(entity_rel, MENTIONS_HEADER, line, marker)

    def remove_mention(self, entity_rel: str, note_path: str) -> bool:
        target = note_path[:-3] if note_path.endswith(".md") else note_path
        marker = f"[[{target}|"
        text = self.vault.read(entity_rel)
        kept = [l for l in text.splitlines(keepends=True) if marker not in l]
        if len(kept) == len(text.splitlines(keepends=True)):
            return False
        self.vault.write(entity_rel, "".join(kept), overwrite=True)
        return True

    def add_relation(self, src_rel: str, relation: str, dst_rel: str) -> bool:
        dst_target = dst_rel[:-3] if dst_rel.endswith(".md") else dst_rel
        dst_stem = Path(dst_rel).stem
        marker = f"- {relation} [[{dst_target}|"
        line = f"- {relation} [[{dst_target}|{dst_stem}]]"
        return self._insert_line(src_rel, RELATIONS_HEADER, line, marker)

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
