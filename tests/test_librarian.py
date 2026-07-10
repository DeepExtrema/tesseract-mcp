"""Tests for the Librarian caretaker loop."""

from datetime import datetime, timedelta

import pytest

from tesseract_mcp import librarian
from tesseract_mcp.vault import Vault

NOW = datetime(2026, 7, 9, 12, 0, 0)


class FakeEmbedder:
    """Deterministic stand-in — no model download in tests."""

    def embed_batch(self, texts):
        return [[float(len(t)), 0.0] for t in texts]


@pytest.fixture(autouse=True)
def _no_model_downloads(monkeypatch):
    from tesseract_mcp import embeddings as embeddings_mod

    monkeypatch.setattr(embeddings_mod, "SentenceTransformerEmbedder", FakeEmbedder)


def _throttle_state(baseline: int, last_pass: datetime) -> dict:
    return {"consolidation": {"entities_at_last_pass": baseline,
                              "last_pass": last_pass.strftime(librarian.TS_FMT),
                              "pending_proposals": []}}


def test_constants_match_spec():
    assert librarian.CONSOLIDATE_MIN_NEW_ENTITIES == 15
    assert librarian.CONSOLIDATE_MAX_AGE_DAYS == 14


def test_load_state_default_when_missing(vault):
    state = librarian.load_state(vault)
    assert state["last_sweep"] is None
    assert state["consolidation"] == {}


def test_state_roundtrip(vault):
    state = librarian.load_state(vault)
    state["last_sweep"] = "2026-07-09 12:00:00"
    librarian.save_state(vault, state)
    assert librarian.load_state(vault)["last_sweep"] == "2026-07-09 12:00:00"


def test_first_pass_runs_when_entities_exist():
    due, reason = librarian.should_consolidate({"consolidation": {}}, 3, NOW)
    assert due
    assert reason == "first pass"


def test_no_entities_never_runs():
    due, _ = librarian.should_consolidate({"consolidation": {}}, 0, NOW)
    assert not due


def test_14_new_entities_skips():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 24, NOW + timedelta(days=1))
    assert not due


def test_15_new_entities_runs():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 25, NOW + timedelta(days=1))
    assert due


def test_age_trigger_requires_a_new_entity():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 10, NOW + timedelta(days=20))
    assert not due


def test_age_trigger_fires_at_14_days_with_one_new_entity():
    due, _ = librarian.should_consolidate(
        _throttle_state(10, NOW), 11, NOW + timedelta(days=14))
    assert due


from tesseract_mcp import cache, indexer


def _entity_note(vault_dir, folder, name, etype, mentions=()):
    p = vault_dir / "Claude" / "Graph" / folder / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"---\nentity: {etype}\n---\n\n# {name}\n\nSummary.\n"]
    if mentions:
        lines.append("\n## Mentions\n")
        for note_path in mentions:
            stem = note_path.rsplit("/", 1)[-1]
            lines.append(f"- [[{note_path}|{stem}]] — evidence\n")
    p.write_text("".join(lines), encoding="utf-8")


def test_manifest_drift_detects_both_directions(vault):
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"]["Ghost.md"] = "deadbeef"
    indexer.save_manifest(manifest, vault.root)
    drift = librarian.check_manifest_drift(vault)
    assert "Ghost.md" in drift["deleted_but_tracked"]
    assert "Daily.md" in drift["present_but_untracked"]


def test_orphaned_entities_detects_missing_note(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization",
                 mentions=["Projects/Gone"])
    cache.rebuild(vault, indexer.db_path(vault.root))
    orphans = librarian.check_orphaned_entities(vault)
    assert orphans == [{"entity": "Claude/Graph/Organizations/Acme",
                        "missing_note": "Projects/Gone"}]


def test_orphaned_entities_clean_when_note_exists(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization",
                 mentions=["Projects/Sentinel ESG"])
    cache.rebuild(vault, indexer.db_path(vault.root))
    assert librarian.check_orphaned_entities(vault) == []


def test_orphaned_entities_empty_without_db(vault):
    assert librarian.check_orphaned_entities(vault) == []


def test_cache_consistency_flags_mismatch(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    cache.rebuild(vault, indexer.db_path(vault.root))
    assert librarian.check_cache_consistency(vault)["consistent"] is True
    _entity_note(vault_dir, "Topics", "Orbit", "topic")  # note added, no rebuild
    result = librarian.check_cache_consistency(vault)
    assert result == {"db_entities": 1, "md_entities": 2, "consistent": False}


def test_pending_proposals_counts_state_and_report():
    state = {"consolidation": {"pending_proposals": [{"canonical": "A"}]}}
    organize_report = {"proposals": [1, 2]}
    assert librarian.count_pending_proposals(state, organize_report, None) == 3
    ran = {"ran": True, "reason": "first pass", "proposed": [1]}
    assert librarian.count_pending_proposals(state, organize_report, ran) == 3


def test_run_health_survives_check_failure(vault, monkeypatch):
    def boom(v):
        raise RuntimeError("kaput")

    monkeypatch.setattr(librarian, "check_manifest_drift", boom)
    health = librarian.run_health(vault, {}, None, None, {})
    assert health["manifest_drift"] == {"error": "RuntimeError: kaput"}
    assert "orphaned_entities" in health
    assert health["stale_embeddings"] >= 0
