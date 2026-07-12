import json
from pathlib import Path

import pytest

from tesseract_mcp import indexer
from tesseract_mcp.extractor import Extraction, ExtractorError
from tesseract_mcp.vault import VaultError

ACME = {"name": "Acme Corp", "type": "organization", "aliases": [], "summary": "A company."}


class FakeExtractor:
    def __init__(self, mapping=None, fail=()):
        self.mapping = mapping or {}
        self.fail = set(fail)
        self.calls = []

    def extract(self, path, content):
        self.calls.append(path)
        if path in self.fail:
            raise ExtractorError("boom")
        return self.mapping.get(path, Extraction())


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))


def test_scan_skips_graph_ignored_and_hidden(vault):
    vault.write("Claude/Graph/Topics/T.md", "x")
    (vault.root / "copilot").mkdir()
    (vault.root / "copilot" / "p.md").write_text("x", encoding="utf-8")
    scanned = indexer.scan_notes(vault)
    assert "Daily.md" in scanned
    assert not any(p.startswith("Claude/Graph/") for p in scanned)
    assert not any(p.startswith("copilot/") for p in scanned)


def test_scan_excludes_caretaker_log_notes(vault):
    """Move/report logs are appended outside sweeps too (undo_move, the
    organize_vault tool); scanning them would feed log lines to the paid
    extractor and mint junk graph entities."""
    vault.write("Claude/Organizer.md", "# Organizer\n\n- moved [[X]]\n")
    vault.write("Claude/Librarian.md", "# Librarian\n\n## Sweep\n")
    scanned = indexer.scan_notes(vault)
    assert "Claude/Organizer.md" not in scanned
    assert "Claude/Librarian.md" not in scanned


def test_caretaker_notes_match_owner_constants():
    from tesseract_mcp import librarian, organizer

    assert set(indexer.CARETAKER_NOTES) == {organizer.ORGANIZER_NOTE,
                                            librarian.LIBRARIAN_NOTE}


def test_load_manifest_drops_legacy_caretaker_entries(vault):
    """Vaults indexed before the scan exclusion still track the caretaker
    logs; left in place they'd flag deleted_but_tracked drift on every
    sweep forever."""
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"]["Claude/Organizer.md"] = "cafe"
    manifest["failures"]["Claude/Librarian.md"] = {"error": "x", "attempts": 1}
    indexer.save_manifest(manifest, vault.root)
    loaded = indexer.load_manifest(vault.root)
    assert "Claude/Organizer.md" not in loaded["hashes"]
    assert "Claude/Librarian.md" not in loaded["failures"]


def test_run_processes_all_then_nothing(vault):
    fx = FakeExtractor({"Daily.md": Extraction([ACME], [])})
    counts = indexer.run(vault, fx)
    assert counts["processed"] > 0
    assert counts["entities_created"] == 1
    assert counts["remaining"] == 0
    fx2 = FakeExtractor()
    counts2 = indexer.run(vault, fx2)
    assert counts2["processed"] == 0 and fx2.calls == []


def test_run_reprocesses_changed_note(vault):
    fx = FakeExtractor()
    indexer.run(vault, fx)
    vault.write("Claude/Inbox/new.md", "fresh content")
    fx2 = FakeExtractor()
    indexer.run(vault, fx2)
    assert fx2.calls == ["Claude/Inbox/new.md"]


def test_run_force_reprocesses_everything(vault):
    indexer.run(vault, FakeExtractor())
    fx = FakeExtractor()
    counts = indexer.run(vault, fx, force=True)
    assert counts["processed"] == len(indexer.scan_notes(vault))


def test_run_batch_cap_reports_remaining(vault):
    fx = FakeExtractor()
    counts = indexer.run(vault, fx, batch=1)
    assert counts["processed"] == 1
    assert counts["remaining"] == len(indexer.scan_notes(vault)) - 1


def test_failure_recorded_and_retried_next_run(vault):
    fx = FakeExtractor(fail={"Daily.md"})
    counts = indexer.run(vault, fx)
    assert counts["failed"] == 1
    manifest = json.loads(
        (indexer.state_dir() / "manifest.json").read_text(encoding="utf-8")
    )
    assert "Daily.md" in manifest["failures"]
    fx2 = FakeExtractor()
    indexer.run(vault, fx2)
    assert "Daily.md" in fx2.calls  # failed notes retried


def test_run_rebuilds_cache(vault):
    from tesseract_mcp import cache

    indexer.run(vault, FakeExtractor({"Daily.md": Extraction([ACME], [])}))
    db = indexer.state_dir() / "graph.db"
    assert db.exists()
    assert cache.find_entity(db, "acme")


def test_cli_rebuild_only_no_extraction(vault, capsys, monkeypatch):
    import sys
    from tesseract_mcp import cache
    from tesseract_mcp.graphstore import GraphStore
    from tesseract_mcp.extractor import Extraction

    GraphStore(vault).apply("Daily.md", Extraction([ACME], []))
    monkeypatch.setattr(
        sys, "argv", ["indexer", str(vault.root), "--rebuild-only"]
    )
    indexer.main()
    out = capsys.readouterr().out
    assert '"rebuilt": true' in out
    assert cache.find_entity(indexer.db_path(), "acme")


def test_failure_backoff_skips_after_three_attempts(vault):
    for _ in range(3):
        indexer.run(vault, FakeExtractor(fail={"Daily.md"}))
    fx = FakeExtractor(fail={"Daily.md"})
    counts = indexer.run(vault, fx)
    assert "Daily.md" not in fx.calls          # skipped, not retried
    assert counts["skipped"] == 1


def test_force_overrides_backoff(vault):
    for _ in range(3):
        indexer.run(vault, FakeExtractor(fail={"Daily.md"}))
    fx = FakeExtractor()
    indexer.run(vault, fx, force=True)
    assert "Daily.md" in fx.calls


def test_success_clears_attempt_count(vault):
    indexer.run(vault, FakeExtractor(fail={"Daily.md"}))
    indexer.run(vault, FakeExtractor())        # succeeds now
    manifest = indexer.load_manifest()
    assert "Daily.md" not in manifest["failures"]


def test_old_string_failure_format_migrates(vault):
    indexer.state_dir()
    indexer.save_manifest({"hashes": {}, "failures": {"Daily.md": "old error"}})
    manifest = indexer.load_manifest()
    assert manifest["failures"]["Daily.md"]["attempts"] == 1


def test_rebuild_skipped_when_nothing_processed(vault, monkeypatch):
    indexer.run(vault, FakeExtractor())        # first run indexes everything
    calls = []
    from tesseract_mcp import cache
    monkeypatch.setattr(cache, "rebuild", lambda v, p: calls.append(1))
    indexer.run(vault, FakeExtractor())        # no changes -> no rebuild
    assert calls == []


def test_state_dir_keyed_by_vault_root(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSERACT_STATE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    vault_a.mkdir()
    vault_b.mkdir()
    dir_a = indexer.state_dir(vault_a)
    dir_b = indexer.state_dir(vault_b)
    assert dir_a != dir_b
    assert dir_a.parent == dir_b.parent  # both live under ~/.tesseract-mcp
    assert indexer.state_dir(vault_a) == dir_a  # stable for the same root


def test_state_dir_falls_back_to_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSERACT_STATE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    vault_root = tmp_path / "vault-c"
    vault_root.mkdir()
    monkeypatch.setenv("TESSERACT_VAULT_PATH", str(vault_root))
    assert indexer.state_dir() == indexer.state_dir(vault_root)


def test_state_dir_requires_vault_root_or_env(tmp_path, monkeypatch):
    monkeypatch.delenv("TESSERACT_STATE_DIR", raising=False)
    monkeypatch.delenv("TESSERACT_VAULT_PATH", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(VaultError, match="TESSERACT_VAULT_PATH"):
        indexer.state_dir()


def test_run_precomputes_embeddings_by_default(vault, monkeypatch):
    from tesseract_mcp import embeddings as embeddings_mod

    calls = []

    class FakeEmbedder:
        def embed_batch(self, texts):
            calls.append(list(texts))
            return [[0.0] for _ in texts]

    monkeypatch.setattr(embeddings_mod, "SentenceTransformerEmbedder", FakeEmbedder)
    indexer.run(vault, FakeExtractor())
    assert calls  # embeddings were computed for the vault's notes


def test_run_can_skip_embeddings(vault, monkeypatch):
    from tesseract_mcp import embeddings as embeddings_mod

    calls = []

    class FakeEmbedder:
        def embed_batch(self, texts):
            calls.append(list(texts))
            return [[0.0] for _ in texts]

    monkeypatch.setattr(embeddings_mod, "SentenceTransformerEmbedder", FakeEmbedder)
    indexer.run(vault, FakeExtractor(), precompute_embeddings=False)
    assert calls == []


def test_stale_mentions_retracted_on_reprocess(vault):
    from tesseract_mcp.graphstore import entity_rel_path

    vault.write("Claude/Inbox/story.md", "About Acme.")
    fx = FakeExtractor({"Claude/Inbox/story.md": Extraction([ACME], [])})
    indexer.run(vault, fx)
    acme_rel = entity_rel_path("organization", "Acme Corp")
    assert "[[Claude/Inbox/story|" in vault.read(acme_rel)

    vault.write("Claude/Inbox/story.md", "Actually about nothing.", overwrite=True)
    counts = indexer.run(vault, FakeExtractor())   # re-extraction finds no entities
    assert counts["mentions_retracted"] == 1
    assert "[[Claude/Inbox/story|" not in vault.read(acme_rel)


def test_run_retry_failures_reattempts_maxed_out_notes(vault):
    for _ in range(indexer.MAX_ATTEMPTS):
        indexer.run(vault, FakeExtractor(fail={"Daily.md"}))
    benched = FakeExtractor()
    indexer.run(vault, benched)
    assert "Daily.md" not in benched.calls  # attempts exhausted: benched

    retried = FakeExtractor()
    counts = indexer.run(vault, retried, retry_failures=True)
    assert "Daily.md" in retried.calls
    assert counts["failed"] == 0
    assert "Daily.md" not in indexer.load_manifest(vault.root)["failures"]


def test_run_retry_failures_skips_unchanged_tracked_notes(vault):
    indexer.run(vault, FakeExtractor())  # index everything cleanly
    fx = FakeExtractor()
    counts = indexer.run(vault, fx, retry_failures=True)
    assert counts["processed"] == 0 and fx.calls == []
