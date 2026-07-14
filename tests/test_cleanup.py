"""Tests for graph deletion & orphaned-entity cleanup."""

from datetime import datetime

from tesseract_mcp import cache, cleanup, consolidate, indexer
from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GraphStore, resolve_redirect
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


def _retire(vault, rel):
    cleanup.retire_note(vault, rel, NOW,
                        reason="orphaned — no mentions or relations")


def test_retire_note_writes_tombstone_keeping_aliases_and_summary(vault):
    GraphStore(vault).upsert_entity(
        {"name": "Acme", "type": "organization",
         "aliases": ["ACME Inc"], "summary": "Maker of anvils."})
    rel = "Claude/Graph/Organizations/Acme.md"
    _retire(vault, rel)
    text = vault.read(rel)
    meta = parse_frontmatter(text)
    assert meta["retired"] == "2026-07-13 12:00"
    assert meta["aliases"] == ["ACME Inc"]
    assert "Maker of anvils." in text and "Retired:" in text


def test_gather_entities_skips_retired(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Acme"))
    store.upsert_entity(_ent("Zeta"))
    _retire(vault, "Claude/Graph/Organizations/Acme.md")
    assert {e["name"] for e in consolidate.gather_entities(vault)} == {"Zeta"}


def test_cache_rebuild_skips_retired(vault):
    GraphStore(vault).upsert_entity(_ent("Acme"))
    _retire(vault, "Claude/Graph/Organizations/Acme.md")
    cache.rebuild(vault, indexer.db_path(vault.root))
    assert cache.find_entity(indexer.db_path(vault.root), "Acme") == []


def _stub(vault, folder, name, target_path):
    """A merge-redirect stub, exactly as consolidate._apply_one writes them."""
    rel = f"Claude/Graph/{folder}/{name}.md"
    stem = target_path.rsplit("/", 1)[-1]
    vault.write(rel,
                ("---\nentity: organization\n"
                 f"merged_into: {target_path}\n---\n\n"
                 f"# {name}\n\nMerged into [[{stem}]].\n"),
                overwrite=True)
    return rel[:-3]


def test_resolve_redirect_follows_chain_to_live(vault):
    GraphStore(vault).upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Mid", "Claude/Graph/Organizations/Canonical")
    _stub(vault, "Organizations", "Old", "Claude/Graph/Organizations/Mid")
    assert resolve_redirect(vault, "Claude/Graph/Organizations/Old") == \
        "Claude/Graph/Organizations/Canonical"


def test_resolve_redirect_none_on_cycle_and_missing(vault):
    _stub(vault, "Organizations", "A", "Claude/Graph/Organizations/B")
    _stub(vault, "Organizations", "B", "Claude/Graph/Organizations/A")
    assert resolve_redirect(vault, "Claude/Graph/Organizations/A") is None
    assert resolve_redirect(vault, "Claude/Graph/Organizations/Ghost") is None


def test_resolve_redirect_gives_up_past_max_depth(vault):
    GraphStore(vault).upsert_entity(_ent("Live"))
    prev = "Claude/Graph/Organizations/Live"
    for i in range(6):  # S0 -> Live, S1 -> S0, ... S5 -> S4: 6-hop chain
        prev = _stub(vault, "Organizations", f"S{i}", prev)
    assert resolve_redirect(vault, prev) is None
    # a shorter suffix of the same chain still resolves
    assert resolve_redirect(vault, "Claude/Graph/Organizations/S1") == \
        "Claude/Graph/Organizations/Live"


def test_repair_relations_rewrites_stub_target(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Src"))
    store.upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Dup", "Claude/Graph/Organizations/Canonical")
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/Dup.md")
    result = cleanup.repair_relations(vault)
    text = vault.read("Claude/Graph/Organizations/Src.md")
    assert "- uses [[Claude/Graph/Organizations/Canonical|Canonical]]" in text
    assert "Dup" not in text
    assert result == {"fixed": 1, "removed": 0}


def test_repair_relations_removes_missing_target(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Src"))
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/Ghost.md")
    result = cleanup.repair_relations(vault)
    assert "Ghost" not in vault.read("Claude/Graph/Organizations/Src.md")
    assert result == {"fixed": 0, "removed": 1}


def test_repair_relations_dedupes_when_canonical_already_present(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Src"))
    store.upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Dup", "Claude/Graph/Organizations/Canonical")
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/Canonical.md")
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/Dup.md")
    result = cleanup.repair_relations(vault)
    text = vault.read("Claude/Graph/Organizations/Src.md")
    assert text.count("[[Claude/Graph/Organizations/Canonical|") == 1
    assert result == {"fixed": 0, "removed": 1}


def test_repair_relations_respects_cap(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Src"))
    store.add_relation("Claude/Graph/Organizations/Src.md", "uses",
                       "Claude/Graph/Organizations/GhostA.md")
    store.add_relation("Claude/Graph/Organizations/Src.md", "cites",
                       "Claude/Graph/Organizations/GhostB.md")
    result = cleanup.repair_relations(vault, limit=1)
    assert result["fixed"] + result["removed"] == 1


def test_repair_relations_skips_stub_and_retired_sources(vault):
    """Relation lines INSIDE stubs/tombstones are never edited — those notes
    are frozen redirects/audit records, not live graph state."""
    dangling = "- uses [[Claude/Graph/Organizations/Ghost|Ghost]]"
    vault.write("Claude/Graph/Organizations/Stubby.md",
                ("---\nentity: organization\n"
                 "merged_into: Claude/Graph/Organizations/X\n---\n\n"
                 f"# Stubby\n\n## Relations\n{dangling}\n"), overwrite=True)
    vault.write("Claude/Graph/Organizations/Tomb.md",
                ("---\nentity: organization\n"
                 "retired: \"2026-07-13 12:00\"\n---\n\n"
                 f"# Tomb\n\n## Relations\n{dangling}\n"), overwrite=True)
    assert cleanup.repair_relations(vault) == {"fixed": 0, "removed": 0}
    assert dangling in vault.read("Claude/Graph/Organizations/Stubby.md")
    assert dangling in vault.read("Claude/Graph/Organizations/Tomb.md")


def test_flatten_stubs_points_chain_at_final_canonical(vault):
    GraphStore(vault).upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Mid", "Claude/Graph/Organizations/Canonical")
    _stub(vault, "Organizations", "Old", "Claude/Graph/Organizations/Mid")
    result = cleanup.flatten_stubs(vault, NOW)
    assert result == {"flattened": 1, "retired_stubs": 0}
    meta = parse_frontmatter(vault.read("Claude/Graph/Organizations/Old.md"))
    assert meta["merged_into"] == "Claude/Graph/Organizations/Canonical"
    assert "[[Canonical]]" in vault.read("Claude/Graph/Organizations/Old.md")


def test_flatten_stubs_retires_dead_end_stub(vault):
    _stub(vault, "Organizations", "Old", "Claude/Graph/Organizations/Ghost")
    result = cleanup.flatten_stubs(vault, NOW)
    assert result == {"flattened": 0, "retired_stubs": 1}
    meta = parse_frontmatter(vault.read("Claude/Graph/Organizations/Old.md"))
    assert meta["retired"] == "2026-07-13 12:00"


def test_flatten_stubs_leaves_live_targets_alone(vault):
    GraphStore(vault).upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Old", "Claude/Graph/Organizations/Canonical")
    assert cleanup.flatten_stubs(vault, NOW) == {
        "flattened": 0, "retired_stubs": 0}


def test_find_orphans_flags_unsupported_entity_only(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Lonely"))                    # nothing supports it
    store.apply("A.md", Extraction([_ent("Mentioned")], []))
    store.apply("B.md", Extraction(
        [_ent("Source")],
        [{"from": "Source", "from_type": "organization", "rel": "uses",
          "to": "Endpoint", "to_type": "organization", "evidence": ""}]))
    orphans = cleanup.find_orphans(vault)
    assert [o["path"] for o in orphans] == \
        ["Claude/Graph/Organizations/Lonely"]
    # Mentioned has a mention; Source has a mention + outbound relation;
    # Endpoint is a relation-only entity supported by its inbound edge.


def test_find_orphans_skips_stubs_and_retired(vault):
    GraphStore(vault).upsert_entity(_ent("Canonical"))
    _stub(vault, "Organizations", "Dup", "Claude/Graph/Organizations/Canonical")
    GraphStore(vault).upsert_entity(_ent("Tomb"))
    _retire(vault, "Claude/Graph/Organizations/Tomb.md")
    paths = {o["path"] for o in cleanup.find_orphans(vault)}
    assert "Claude/Graph/Organizations/Dup" not in paths
    assert "Claude/Graph/Organizations/Tomb" not in paths


def test_update_retirement_proposals_self_heals_and_caps(vault):
    block = {"pending_retirements": [
        {"path": "gone-now-supported", "name": "X", "type": "topic",
         "reason": "orphaned: no mentions or relations"}]}
    orphans = [{"path": f"o{i}", "name": f"o{i}", "type": "topic",
                "reason": "orphaned: no mentions or relations"}
               for i in range(3)]
    pending = cleanup.update_retirement_proposals(block, orphans, limit=2)
    assert [p["path"] for p in pending] == ["o0", "o1"]  # healed + capped
    assert block["pending_retirements"] == pending


def test_apply_retirements_tombstones_and_rebuilds(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("Lonely"))
    store.apply("A.md", Extraction([_ent("Kept")], []))
    result = cleanup.apply_retirements(vault, now=NOW)
    assert result == {"retired": ["Claude/Graph/Organizations/Lonely"]}
    meta = parse_frontmatter(vault.read("Claude/Graph/Organizations/Lonely.md"))
    assert meta["retired"] == "2026-07-13 12:00"
    assert cache.find_entity(indexer.db_path(vault.root), "Lonely") == []


def test_apply_retirements_paths_filter(vault):
    store = GraphStore(vault)
    store.upsert_entity(_ent("LonelyA"))
    store.upsert_entity(_ent("LonelyB"))
    result = cleanup.apply_retirements(
        vault, paths=["Claude/Graph/Organizations/LonelyA"], now=NOW)
    assert result == {"retired": ["Claude/Graph/Organizations/LonelyA"]}
    meta = parse_frontmatter(vault.read("Claude/Graph/Organizations/LonelyB.md"))
    assert "retired" not in meta


def test_prune_checked_hash_drops_vanished_paths():
    con = {"checked_hash": {"live": "h1", "gone": "h2"}}
    assert cleanup.prune_checked_hash(con, {"live"}) == 1
    assert con["checked_hash"] == {"live": "h1"}


def test_prune_checked_hash_handles_empty_block():
    assert cleanup.prune_checked_hash({}, {"anything"}) == 0
