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


def _write_session(vault_dir, name, project, created, body):
    (vault_dir / "Claude" / "Sessions" / name).write_text(
        f"---\ncreated: {created}\nagent: claude\n"
        f"project: {project}\ntags: []\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_resume_matches_project_substring_newest_first(vault, vault_dir):
    _write_session(vault_dir, "2026-07-01 Graph work.md",
                   "tesseract-mcp", "2026-07-01 10:00", "Built the graph.")
    _write_session(vault_dir, "2026-07-09 Evals.md",
                   "tesseract-mcp", "2026-07-09 10:00", "Shipped evals.")
    _write_session(vault_dir, "2026-07-05 Other.md",
                   "sentinel", "2026-07-05 10:00", "Unrelated work.")
    bundle = recall.resume_bundle(vault, "tesseract")
    assert bundle["mode"] == "resume"
    assert bundle["project"] == "tesseract"
    notes = bundle["sessions"]["notes"]
    assert [n["path"] for n in notes] == [
        "Claude/Sessions/2026-07-09 Evals.md",
        "Claude/Sessions/2026-07-01 Graph work.md",
    ]
    assert "Shipped evals." in notes[0]["excerpt"]
    assert "---" not in notes[0]["excerpt"]  # frontmatter stripped


def test_resume_respects_limit(vault, vault_dir):
    for day in range(1, 5):
        _write_session(vault_dir, f"2026-07-0{day} S{day}.md",
                       "tesseract", f"2026-07-0{day} 10:00", f"Work {day}.")
    bundle = recall.resume_bundle(vault, "tesseract", limit=2)
    assert len(bundle["sessions"]["notes"]) == 2


def test_resume_decisions_and_tasks_filter_by_project(vault, vault_dir):
    (vault_dir / "Claude" / "Decisions.md").write_text(
        "# Decisions\n\n"
        "- 2026-07-08 — hybrid search ships in tesseract ([[x]])\n"
        "- 2026-07-09 — sentinel retired\n",
        encoding="utf-8",
    )
    (vault_dir / "Claude" / "Tasks.md").write_text(
        "# Tasks\n\n- [ ] tune tesseract eval gate\n"
        "- [ ] water plants\n- [x] tesseract done thing\n",
        encoding="utf-8",
    )
    bundle = recall.resume_bundle(vault, "Tesseract")  # case-insensitive
    assert bundle["decisions"]["lines"] == [
        "- 2026-07-08 — hybrid search ships in tesseract ([[x]])"
    ]
    assert [t["text"] for t in bundle["tasks"]["tasks"]] == [
        "tune tesseract eval gate"
    ]


def test_resume_decisions_missing_file_is_empty_not_error(vault):
    bundle = recall.resume_bundle(vault, "tesseract")
    assert bundle["decisions"]["status"] == "ok"
    assert bundle["decisions"]["lines"] == []


def test_resume_entities_without_graph_cache(vault):
    bundle = recall.resume_bundle(vault, "tesseract")
    assert bundle["entities"]["status"] == "ok"
    assert bundle["entities"]["entities"] == []
