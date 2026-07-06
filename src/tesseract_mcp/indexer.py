"""Incremental vault indexing: hash-diff manifest -> extract -> store -> cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from . import cache
from .extractor import CliExtractor, ExtractorError
from .graphstore import GRAPH_ROOT, GraphStore
from .search import SKIP_DIRS
from .vault import Vault

DEFAULT_IGNORE = ("copilot",)
DEFAULT_BATCH = 25


def state_dir() -> Path:
    override = os.environ.get("TESSERACT_STATE_DIR")
    d = Path(override) if override else Path.home() / ".tesseract-mcp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return state_dir() / "manifest.json"


def db_path() -> Path:
    return state_dir() / "graph.db"


def load_manifest() -> dict:
    p = _manifest_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"hashes": {}, "failures": {}}


def save_manifest(manifest: dict) -> None:
    _manifest_path().write_text(
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
) -> dict:
    manifest = load_manifest()
    current = scan_notes(vault, ignore)
    if force:
        pending = list(current)
    else:
        pending = [
            rel
            for rel, digest in current.items()
            if manifest["hashes"].get(rel) != digest or rel in manifest["failures"]
        ]
    todo, remaining = pending[:batch], max(0, len(pending) - batch)

    store = GraphStore(vault)
    counts = {"processed": 0, "entities_created": 0, "entities_merged": 0,
              "mentions_added": 0, "relations_added": 0, "failed": 0,
              "remaining": remaining}
    for rel in todo:
        try:
            extraction = extractor.extract(rel, vault.read(rel))
        except ExtractorError as e:
            manifest["failures"][rel] = str(e)[:300]
            counts["failed"] += 1
            continue
        applied = store.apply(rel, extraction)
        for key in ("entities_created", "entities_merged", "mentions_added", "relations_added"):
            counts[key] += applied[key]
        manifest["hashes"][rel] = current[rel]
        manifest["failures"].pop(rel, None)
        counts["processed"] += 1
    save_manifest(manifest)
    cache.rebuild(vault, db_path())
    return counts


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
        cache.rebuild(Vault(args.vault), db_path())
        print(json.dumps({"rebuilt": True, "db": str(db_path())}))
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
