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
