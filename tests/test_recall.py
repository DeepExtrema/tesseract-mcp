"""Tests for the recall bundle module (digest + resume raw material)."""

import os
from datetime import datetime

import pytest

from tesseract_mcp import recall
from tesseract_mcp.vault import VaultError


def test_digest_sections_present(vault):
    bundle = recall.digest_bundle(vault)
    assert bundle["mode"] == "digest"
    assert set(bundle) == {
        "mode", "generated", "since", "librarian", "recent_notes",
        "inbox_captures", "tasks", "proposals", "new_entities",
    }
    for name in ("librarian", "recent_notes", "inbox_captures",
                 "tasks", "proposals", "new_entities"):
        assert bundle[name]["status"] == "ok"


def test_digest_includes_fresh_notes(vault, vault_dir):
    (vault_dir / "Claude" / "Inbox" / "2026-07-10.md").write_text(
        "- 09:00 a fresh thought\n", encoding="utf-8"
    )
    bundle = recall.digest_bundle(vault)
    paths = [n["path"] for n in bundle["inbox_captures"]["notes"]]
    assert "Claude/Inbox/2026-07-10.md" in paths


def test_digest_since_filters_old_notes(vault, vault_dir):
    old = vault_dir / "Claude" / "Inbox" / "2020-01-01.md"
    old.write_text("- 09:00 ancient thought\n", encoding="utf-8")
    stamp = datetime(2020, 1, 2).timestamp()
    os.utime(old, (stamp, stamp))
    bundle = recall.digest_bundle(vault, since="2026-01-01")
    assert bundle["since"] == "2026-01-01"
    paths = [n["path"] for n in bundle["inbox_captures"]["notes"]]
    assert "Claude/Inbox/2020-01-01.md" not in paths


def test_digest_rejects_bad_since(vault):
    with pytest.raises(VaultError, match="YYYY-MM-DD"):
        recall.digest_bundle(vault, since="last tuesday")


def test_digest_tasks_split_open_and_recently_done(vault, vault_dir):
    (vault_dir / "Claude" / "Tasks.md").write_text(
        "# Tasks\n\n- [ ] open item\n- [x] finished item\n", encoding="utf-8"
    )
    bundle = recall.digest_bundle(vault)
    tasks = bundle["tasks"]
    assert [t["text"] for t in tasks["open"]] == ["open item"]
    # Tasks.md was just written, so it counts as changed since the cutoff
    assert [t["text"] for t in tasks["done_recently"]] == ["finished item"]


def test_digest_section_degrades_without_killing_bundle(vault, monkeypatch):
    def boom(v):
        raise RuntimeError("state file exploded")

    monkeypatch.setattr(recall.librarian_mod, "status", boom)
    bundle = recall.digest_bundle(vault)
    assert bundle["librarian"]["status"] == "error"
    assert "RuntimeError" in bundle["librarian"]["error"]
    assert bundle["recent_notes"]["status"] == "ok"


def test_digest_proposals_default_zero_without_sweep(vault):
    bundle = recall.digest_bundle(vault)
    assert bundle["proposals"]["pending"] == 0
    assert bundle["proposals"]["detail_note"] == "Claude/Organizer.md"
