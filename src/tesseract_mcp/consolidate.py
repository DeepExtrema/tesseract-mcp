"""LLM-driven consolidation of duplicate graph entities."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import yaml

from . import cache
from .extractor import consolidation_extractor
from .graphstore import (
    GRAPH_ROOT,
    MENTIONS_HEADER,
    RELATIONS_HEADER,
    GraphStore,
    TYPE_FOLDERS,
    entity_rel_path,
)
from .indexer import db_path
from .search import parse_frontmatter, body_text
from .vault import Vault

PROMPT = """You are deduplicating entities in a personal knowledge graph.
Below is the full list of entities (one per line: type | name | aliases).
Identify groups that are name-variants of the SAME real-world thing.

Rules: merge only true variants (e.g. "Oracle VM" / "Oracle VM deploy");
same type only; pick the most standard, complete name as canonical; use only
names from the list; when unsure, do NOT merge. Reply with ONLY JSON:
{{"merges": [{{"type": str, "canonical": str, "duplicates": [str]}}]}}
Empty merges list if nothing qualifies.

Entities:
{listing}"""


def _section_lines(text: str, header: str) -> list[str]:
    start = text.find(header)
    if start == -1:
        return []
    nxt = text.find("\n## ", start + len(header))
    section = text[start : nxt if nxt != -1 else len(text)]
    return [l for l in section.splitlines() if l.startswith("- ")]


def _entity_summary(text: str) -> str:
    """Body text between the `# name` H1 and `## Mentions` — the note
    template writes the entity summary there (not frontmatter)."""
    body = body_text(text)
    cut = body.find(MENTIONS_HEADER)
    if cut != -1:
        body = body[:cut]
    lines = [l for l in body.splitlines() if not l.startswith("# ")]
    return "\n".join(lines).strip()


def gather_entities(vault: Vault) -> list[dict]:
    out: list[dict] = []
    graph_dir = vault.resolve(GRAPH_ROOT)
    if not graph_dir.is_dir():
        return out
    for p in sorted(graph_dir.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter(text)
        if meta.get("merged_into"):
            continue
        aliases = meta.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [aliases]
        out.append(
            {"name": p.stem, "type": str(meta.get("entity") or "topic"),
             "path": "/".join(p.relative_to(vault.root).parts)[:-3],
             "aliases": [str(a) for a in aliases],
             "summary": _entity_summary(text)}
        )
    return out


def _validate_merges(raw: dict, known: set[tuple[str, str]]) -> list[dict]:
    out = []
    for m in raw.get("merges") or []:
        etype = str(m.get("type") or "").lower()
        canonical = str(m.get("canonical") or "").strip()
        dups = [str(d).strip() for d in (m.get("duplicates") or []) if str(d).strip()]
        dups = [d for d in dups if d.casefold() != canonical.casefold()]
        if not canonical or not dups or etype not in TYPE_FOLDERS:
            continue
        if (etype, canonical.casefold()) not in known:
            continue
        if any((etype, d.casefold()) not in known for d in dups):
            continue
        out.append({"type": etype, "canonical": canonical, "duplicates": dups})
    return out


def _listing(entities: list[dict]) -> str:
    return "\n".join(
        f"{e['type']} | {e['name']} | {', '.join(e['aliases']) or '-'}"
        for e in entities
    )


def adjudicate_batches(
    backend, batches: list[list[list[dict]]], all_entities: list[dict]
) -> tuple[list[dict], int]:
    """Run one LLM call per batch, isolating failures. A batch is a list of
    clusters; a cluster is a list of entity dicts. Returns (merges, skipped)."""
    known = {(e["type"], e["name"].casefold()) for e in all_entities}
    merges: list[dict] = []
    seen: set[tuple] = set()
    skipped = 0
    for batch in batches:
        entities = [e for cluster in batch for e in cluster]
        try:
            raw = backend.complete_json(PROMPT.format(listing=_listing(entities)))
        except Exception:  # noqa: BLE001 — one bad batch must not fail the step
            skipped += 1
            continue
        for m in _validate_merges(raw, known):
            key = (m["type"], m["canonical"].casefold(),
                   tuple(sorted(d.casefold() for d in m["duplicates"])))
            if key in seen:
                continue
            seen.add(key)
            merges.append(m)
    return merges, skipped


def propose_merges(backend, entities: list[dict]) -> list[dict]:
    if not entities:
        return []
    merges, _ = adjudicate_batches(backend, [[entities]], entities)
    return merges


def _resolve_dup_note(vault: Vault, etype: str, name: str) -> str | None:
    """Find duplicate entity note by filename/stem only — not aliases."""
    rel = entity_rel_path(etype, name)
    path = vault.resolve(rel)
    if path.exists():
        if parse_frontmatter(path.read_text(encoding="utf-8")).get("merged_into"):
            return None
        return rel
    folder = vault.resolve(f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}")
    if not folder.is_dir():
        return None
    needle = name.casefold()
    for p in sorted(folder.glob("*.md")):
        if p.stem.casefold() != needle:
            continue
        if parse_frontmatter(p.read_text(encoding="utf-8", errors="ignore")).get(
            "merged_into"
        ):
            return None
        return f"{GRAPH_ROOT}/{TYPE_FOLDERS[etype]}/{p.name}"
    return None


def _apply_one(vault: Vault, store: GraphStore, merge: dict, now: datetime) -> None:
    etype = merge["type"]
    canon_rel = store.find_entity_note(etype, merge["canonical"])
    for dup_name in merge["duplicates"]:
        dup_rel = _resolve_dup_note(vault, etype, dup_name)
        if dup_rel is None or dup_rel == canon_rel or canon_rel is None:
            continue
        dup_text = vault.read(dup_rel)
        for line in _section_lines(dup_text, MENTIONS_HEADER):
            marker = line.split("|", 1)[0] + "|"
            store._insert_line(canon_rel, MENTIONS_HEADER, line, marker)
        for line in _section_lines(dup_text, RELATIONS_HEADER):
            store._insert_line(canon_rel, RELATIONS_HEADER, line, line)
        dup_meta = parse_frontmatter(dup_text)
        dup_aliases = dup_meta.get("aliases") or []
        if not isinstance(dup_aliases, list):
            dup_aliases = [dup_aliases]
        canon_text = vault.read(canon_rel)
        canon_meta = parse_frontmatter(canon_text)
        current = canon_meta.get("aliases") or []
        if not isinstance(current, list):
            current = [current]
        canon_name = canon_rel.rsplit("/", 1)[-1][:-3]
        known_names = {str(a).casefold() for a in current} | {canon_name.casefold()}
        added = [a for a in [dup_name, *map(str, dup_aliases)]
                 if a.casefold() not in known_names]
        if added:
            end = canon_text.find("\n---", 3)
            if end != -1:
                canon_meta["aliases"] = [str(a) for a in current] + added
                fm = "---\n" + yaml.safe_dump(canon_meta, sort_keys=False,
                                              default_flow_style=None) + "---"
                vault.write(canon_rel, fm + canon_text[end + 4:], overwrite=True)
        stub_meta = {
            "created": now.strftime("%Y-%m-%d %H:%M"),
            "agent": "claude",
            "entity": etype,
            "merged_into": canon_rel[:-3],
            "tags": [f"graph/{etype}"],
        }
        canon_stem = canon_rel.rsplit("/", 1)[-1][:-3]
        stub = ("---\n" + yaml.safe_dump(stub_meta, sort_keys=False) + "---\n\n"
                + f"# {dup_name}\n\nMerged into [[{canon_stem}]].\n")
        vault.write(dup_rel, stub, overwrite=True)


def run(vault: Vault, backend, apply: bool = False) -> dict:
    entities = gather_entities(vault)
    merges = propose_merges(backend, entities)
    result = {"entities": len(entities), "proposed": merges, "applied": False,
              "merged_entities": 0}
    if apply and merges:
        store = GraphStore(vault)
        now = datetime.now()
        for m in merges:
            _apply_one(vault, store, m, now)
            result["merged_entities"] += len(m["duplicates"])
        result["applied"] = True
        cache.rebuild(vault, db_path())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolidate duplicate graph entities.")
    parser.add_argument("vault")
    parser.add_argument("--backend", default=None, help="codex | claude")
    parser.add_argument("--apply", action="store_true",
                        help="apply proposed merges (default: dry-run)")
    args = parser.parse_args()
    result = run(Vault(args.vault), consolidation_extractor(backend=args.backend), apply=args.apply)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
