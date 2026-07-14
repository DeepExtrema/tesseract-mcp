"""Tests for graph deletion & orphaned-entity cleanup."""

from datetime import datetime

from tesseract_mcp import cache, cleanup, consolidate, indexer
from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GraphStore
from tesseract_mcp.search import parse_frontmatter

NOW = datetime(2026, 7, 13, 12, 0, 0)


def _ent(name, etype="organization"):
    return {"name": name, "type": etype, "aliases": [], "summary": "S."}


def _index_note(vault, rel, entities, relations=()):
    """Simulate one indexer pass: write the human note with a raw Path write
    (vault.write refuses non-Claude paths), extract, track in the manifest."""
    p = vault.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {p.stem}\n\nBody.\n", encoding="utf-8")
    GraphStore(vault).apply(rel, Extraction(list(entities), list(relations)))
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"][rel] = "digest"
    indexer.save_manifest(manifest, vault.root)
    cache.rebuild(vault, indexer.db_path(vault.root))


def test_deleted_notes_lists_tracked_but_missing(vault):
    _index_note(vault, "Projects/Kept.md", [_ent("Acme")])
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"]["Projects/Gone.md"] = "digest"
    manifest["failures"]["Projects/Benched.md"] = {"error": "x", "attempts": 3}
    indexer.save_manifest(manifest, vault.root)
    assert cleanup.deleted_notes(vault) == [
        "Projects/Benched.md", "Projects/Gone.md"]


def test_retract_deleted_removes_mentions_and_manifest_entry(vault):
    _index_note(vault, "Projects/Doomed.md", [_ent("Acme")])
    entity_rel = "Claude/Graph/Organizations/Acme.md"
    assert "[[Projects/Doomed|" in vault.read(entity_rel)
    (vault.root / "Projects" / "Doomed.md").unlink()
    result = cleanup.retract_deleted(vault)
    assert result == {"retracted_notes": 1, "removed_mentions": 1,
                      "remaining": 0}
    assert "[[Projects/Doomed|" not in vault.read(entity_rel)
    manifest = indexer.load_manifest(vault.root)
    assert "Projects/Doomed.md" not in manifest["hashes"]


def test_retract_deleted_respects_cap(vault):
    for i in range(3):
        _index_note(vault, f"Projects/N{i}.md", [_ent(f"Org{i}")])
        (vault.root / "Projects" / f"N{i}.md").unlink()
    result = cleanup.retract_deleted(vault, limit=2)
    assert result["retracted_notes"] == 2 and result["remaining"] == 1


def test_retract_deleted_scans_markdown_when_db_missing(vault):
    _index_note(vault, "Projects/Doomed.md", [_ent("Acme")])
    (vault.root / "Projects" / "Doomed.md").unlink()
    indexer.db_path(vault.root).unlink()
    result = cleanup.retract_deleted(vault)
    assert result["removed_mentions"] == 1


def test_retract_deleted_tolerates_hand_deleted_entity_note(vault):
    """The cache may hold a mention row for an entity note a human deleted;
    the VaultError from remove_mention must not abort the pass and the
    manifest entry must still be pruned."""
    _index_note(vault, "Projects/Doomed.md", [_ent("Acme")])
    (vault.root / "Projects" / "Doomed.md").unlink()
    (vault.root / "Claude" / "Graph" / "Organizations" / "Acme.md").unlink()
    result = cleanup.retract_deleted(vault)
    assert result == {"retracted_notes": 1, "removed_mentions": 0,
                      "remaining": 0}
    assert "Projects/Doomed.md" not in indexer.load_manifest(vault.root)["hashes"]
