import json

import pytest

from tesseract_mcp import indexer
from tesseract_mcp.extractor import Extraction, ExtractorError

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
