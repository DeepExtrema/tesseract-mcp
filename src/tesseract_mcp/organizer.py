"""Autonomous vault organizer: files notes where their semantic neighbors
live, via cosine-weighted K-nearest-neighbor folder vote.

Standing permission for autonomous moves in the human topical tree was
granted by Taimoor 2026-07-08 (see the Organizer section of the
constitution). Rails: journal + undo, proposals queue below the confidence
threshold, hard exclusions below.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import cache, indexer
from . import embeddings as embeddings_mod
from .mover import duplicate_stem_exists, move_note, reverse_rewrites
from .search import parse_frontmatter
from .vault import Vault, VaultError

EXCLUDED_DIRS = frozenset({
    "Claude", "00 - Maps of Content", ".obsidian", ".smart-env",
    ".trash", ".space", "copilot",
})
VOTE_K = 10
VOTE_THRESHOLD = 0.7
ORGANIZER_NOTE = "Claude/Organizer.md"
_NOTE_SEED = (
    "# Organizer\n\nAutonomous move log and proposals. "
    "See constitution → Organizer.\n\n## Log\n"
)


def discover_taxonomy(vault: Vault) -> list[str]:
    """Existing top-level folders = the frozen taxonomy."""
    return sorted(
        p.name for p in vault.root.iterdir()
        if p.is_dir() and p.name not in EXCLUDED_DIRS
    )


def _wants_organizing(vault: Vault, rel: str) -> bool:
    text = vault.read(rel)
    return parse_frontmatter(text).get("organize") is not False


def iter_organized(vault: Vault) -> list[str]:
    """Rel paths of .md notes currently inside taxonomy folders."""
    out: list[str] = []
    for folder in discover_taxonomy(vault):
        for p in sorted((vault.root / folder).rglob("*.md")):
            out.append("/".join(p.relative_to(vault.root).parts))
    return out


def iter_candidates(vault: Vault) -> list[str]:
    """Notes the organizer may classify: vault-root .md files plus
    already-organized notes (re-checkable), minus organize: false."""
    root_notes = sorted(
        p.name for p in vault.root.iterdir()
        if p.is_file() and p.suffix == ".md"
    )
    return [
        rel for rel in root_notes + iter_organized(vault)
        if _wants_organizing(vault, rel)
    ]


@dataclass
class Classification:
    folder: str | None
    share: float
    neighbors: list[str]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def classify(
    rel: str,
    vectors: dict[str, list[float]],
    labeled: list[str],
    k: int = VOTE_K,
) -> Classification:
    """Cosine-weighted K-nearest-neighbor vote among labeled notes.
    share = winning folder's similarity mass / total mass of the top K."""
    vec = vectors.get(rel)
    if vec is None:
        return Classification(None, 0.0, [])
    scored = [
        (other, _cosine(vec, vectors[other]))
        for other in labeled
        if other != rel and other in vectors
    ]
    scored = [(p, s) for p, s in scored if s > 0]
    if not scored:
        return Classification(None, 0.0, [])
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = scored[:k]
    votes: dict[str, float] = {}
    for path, sim in top:
        votes[path.split("/")[0]] = votes.get(path.split("/")[0], 0.0) + sim
    winner = max(votes, key=votes.get)
    share = votes[winner] / sum(votes.values())
    return Classification(winner, share, [p for p, _ in top])


def journal_path(vault: Vault) -> Path:
    return indexer.state_dir(vault.root) / "organizer_journal.jsonl"


def _ensure_note(vault: Vault) -> None:
    try:
        vault.read(ORGANIZER_NOTE)
    except VaultError:
        vault.write(ORGANIZER_NOTE, _NOTE_SEED)


def record_move(
    vault: Vault, record: dict, share: float, neighbors: list[str]
) -> None:
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from": record["from"],
        "to": record["to"],
        "share": share,
        "neighbors": neighbors,
        "rewrites": record["rewrites"],
        "undone": False,
    }
    with journal_path(vault).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    _ensure_note(vault)
    stem = record["to"].rsplit("/", 1)[-1][:-3]
    vault.append(
        ORGANIZER_NOTE,
        f"- {entry['ts']} — moved [[{record['to'][:-3]}|{stem}]] "
        f"from `{record['from']}` (share {share:.2f})\n",
    )


def undo_move(vault: Vault, note_rel: str) -> dict:
    jp = journal_path(vault)
    entries = []
    if jp.exists():
        entries = [
            json.loads(line)
            for line in jp.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    target_idx = None
    for i in range(len(entries) - 1, -1, -1):
        if entries[i]["to"] == note_rel and not entries[i].get("undone"):
            target_idx = i
            break
    if target_idx is None:
        raise VaultError(f"No undoable move found for: {note_rel}")
    entry = entries[target_idx]
    src = vault.resolve(entry["to"])
    dst = vault.resolve(entry["from"])
    if not src.is_file():
        raise VaultError(f"Cannot undo: file no longer at {entry['to']}")
    if dst.exists():
        raise VaultError(f"Cannot undo: original location occupied: {entry['from']}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    reverse_rewrites(
        vault,
        entry["from"],
        entry["to"],
        [r["path"] for r in entry["rewrites"]],
    )
    manifest = indexer.load_manifest(vault.root)
    if entry["to"] in manifest["hashes"]:
        manifest["hashes"][entry["from"]] = manifest["hashes"].pop(entry["to"])
        indexer.save_manifest(manifest, vault.root)
    entries[target_idx]["undone"] = True
    jp.write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )
    _ensure_note(vault)
    vault.append(
        ORGANIZER_NOTE,
        f"- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — UNDID move of "
        f"`{entry['to']}` back to `{entry['from']}`\n",
    )
    return {"restored": entry["from"], "was": entry["to"]}


def run_sweep(vault: Vault, embedder=None, apply: bool = False) -> dict:
    if embedder is None:
        embedder = embeddings_mod.SentenceTransformerEmbedder()
    vectors = embeddings_mod.get_note_vectors(
        vault, indexer.state_dir(vault.root), embedder
    )
    taxonomy = set(discover_taxonomy(vault))
    labeled = iter_organized(vault)
    moved: list[dict] = []
    proposals: list[dict] = []
    skipped: list[dict] = []

    for rel in iter_candidates(vault):
        current = rel.split("/")[0] if "/" in rel and rel.split("/")[0] in taxonomy else None
        cls = classify(rel, vectors, labeled)
        if cls.folder is None:
            skipped.append({"path": rel, "reason": "no vector or no labeled neighbors"})
            continue
        if current == cls.folder:
            skipped.append({"path": rel, "reason": "already correctly filed"})
            continue
        if cls.share < VOTE_THRESHOLD:
            if current is None:  # root notes queue for a human; filed notes rest
                proposals.append({
                    "path": rel, "suggested": cls.folder,
                    "share": round(cls.share, 3),
                    "neighbors": cls.neighbors[:3],
                    "reason": "low confidence",
                })
            else:
                skipped.append({"path": rel, "reason": "low-confidence disagreement"})
            continue
        if duplicate_stem_exists(vault, rel):
            proposals.append({
                "path": rel, "suggested": cls.folder,
                "share": round(cls.share, 3),
                "neighbors": cls.neighbors[:3],
                "reason": "duplicate stem — bare links would become ambiguous",
            })
            continue
        stem_name = rel.rsplit("/", 1)[-1]
        target_rel = f"{cls.folder}/{stem_name}"
        if apply:
            record = move_note(vault, rel, target_rel)
            record_move(vault, record, share=cls.share, neighbors=cls.neighbors[:3])
        moved.append({
            "from": rel, "to_folder": cls.folder,
            "share": round(cls.share, 3), "neighbors": cls.neighbors[:3],
        })

    cache_rebuilt = False
    if apply and moved:
        cache.rebuild(vault, indexer.db_path(vault.root))
        cache_rebuilt = True
    if apply and proposals:
        _ensure_note(vault)
        lines = [f"\n### Proposals {datetime.now().strftime('%Y-%m-%d')}\n"]
        for p in proposals:
            lines.append(
                f"- `{p['path']}` → **{p['suggested']}** "
                f"(share {p['share']}; {p['reason']})\n"
            )
        vault.append(ORGANIZER_NOTE, "".join(lines))
    return {
        "moved": moved, "proposals": proposals,
        "skipped": skipped, "cache_rebuilt": cache_rebuilt,
    }
