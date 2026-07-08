"""Incremental vault indexing: hash-diff manifest -> extract -> store -> cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from . import cache
from . import embeddings as embeddings_mod
from .extractor import CliExtractor, ExtractorError
from .graphstore import GRAPH_ROOT, GraphStore
from .search import SKIP_DIRS
from .vault import Vault, VaultError

DEFAULT_IGNORE = ("copilot",)
DEFAULT_BATCH = 25
MAX_ATTEMPTS = 3


def state_dir(vault_root: str | Path | None = None) -> Path:
    override = os.environ.get("TESSERACT_STATE_DIR")
    if override:
        d = Path(override)
    else:
        root = vault_root or os.environ.get("TESSERACT_VAULT_PATH")
        if not root:
            raise VaultError(
                "Cannot determine state directory: pass vault_root or set "
                "TESSERACT_VAULT_PATH."
            )
        digest = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
        d = Path.home() / ".tesseract-mcp" / digest
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path(vault_root: str | Path | None = None) -> Path:
    return state_dir(vault_root) / "manifest.json"


def db_path(vault_root: str | Path | None = None) -> Path:
    return state_dir(vault_root) / "graph.db"


def load_manifest(vault_root: str | Path | None = None) -> dict:
    p = _manifest_path(vault_root)
    if p.exists():
        manifest = json.loads(p.read_text(encoding="utf-8"))
    else:
        manifest = {"hashes": {}, "failures": {}}
    for rel, val in list(manifest.get("failures", {}).items()):
        if isinstance(val, str):
            manifest["failures"][rel] = {"error": val, "attempts": 1}
    return manifest


def save_manifest(manifest: dict, vault_root: str | Path | None = None) -> None:
    _manifest_path(vault_root).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def scan_notes(vault: Vault, ignore: tuple[str, ...] = DEFAULT_IGNORE) -> dict[str, str]:
    """vault-relative path -> sha256 of content, for every indexable note."""
    hashes: dict[str, str] = {}
    for path in sorted(vault.root.rglob("*.md")):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        rel = "/".join(rel_parts)
        if rel.startswith(GRAPH_ROOT + "/") or rel_parts[0] in ignore:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[rel] = digest
    return hashes


def run(
    vault: Vault,
    extractor,
    batch: int = DEFAULT_BATCH,
    force: bool = False,
    ignore: tuple[str, ...] = DEFAULT_IGNORE,
    precompute_embeddings: bool = True,
) -> dict:
    manifest = load_manifest(vault.root)
    current = scan_notes(vault, ignore)
    skipped = 0
    if force:
        pending = list(current)
    else:
        pending = []
        for rel, digest in current.items():
            failure = manifest["failures"].get(rel)
            if failure and failure["attempts"] >= MAX_ATTEMPTS:
                skipped += 1
                continue
            if manifest["hashes"].get(rel) != digest or failure:
                pending.append(rel)
    todo, remaining = pending[:batch], max(0, len(pending) - batch)

    store = GraphStore(vault)
    counts = {"processed": 0, "entities_created": 0, "entities_merged": 0,
              "mentions_added": 0, "relations_added": 0,
              "mentions_retracted": 0, "failed": 0,
              "skipped": skipped, "remaining": remaining}
    for rel in todo:
        try:
            extraction = extractor.extract(rel, vault.read(rel))
        except ExtractorError as e:
            prev = manifest["failures"].get(rel, {"attempts": 0})
            manifest["failures"][rel] = {
                "error": str(e)[:300], "attempts": prev["attempts"] + 1
            }
            counts["failed"] += 1
            continue
        counts["mentions_retracted"] += _retract_stale_mentions(vault, store, rel)
        applied = store.apply(rel, extraction)
        for key in ("entities_created", "entities_merged", "mentions_added", "relations_added"):
            counts[key] += applied[key]
        manifest["hashes"][rel] = current[rel]
        manifest["failures"].pop(rel, None)
        counts["processed"] += 1
    save_manifest(manifest, vault.root)
    if counts["processed"] or not db_path(vault.root).exists():
        cache.rebuild(vault, db_path(vault.root))
    if precompute_embeddings:
        embedder = embeddings_mod.SentenceTransformerEmbedder()
        embeddings_mod.get_note_vectors(vault, state_dir(vault.root), embedder)
    return counts


def _retract_stale_mentions(vault: Vault, store: GraphStore, rel: str) -> int:
    db = db_path(vault.root)
    if not db.exists():
        return 0
    removed = 0
    for entity_path in cache.note_entity_paths(db, rel):
        entity_rel = entity_path + ".md"
        try:
            if store.remove_mention(entity_rel, rel):
                removed += 1
        except VaultError:
            continue  # entity note deleted/renamed by hand — nothing to retract
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Index the vault into the semantic graph.")
    parser.add_argument("vault", help="Path to the Obsidian vault root")
    parser.add_argument("--backend", default=None, help="codex | claude")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="Rebuild the query cache from Claude/Graph markdown without any LLM extraction",
    )
    args = parser.parse_args()
    if args.rebuild_only:
        cache.rebuild(Vault(args.vault), db_path(args.vault))
        print(json.dumps({"rebuilt": True, "db": str(db_path(args.vault))}))
        return
    counts = run(
        Vault(args.vault),
        CliExtractor(backend=args.backend),
        batch=args.batch,
        force=args.force,
    )
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
