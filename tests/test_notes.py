from datetime import datetime

from tesseract_mcp import notes

NOW = datetime(2026, 7, 5, 14, 30)


def test_safe_filename_strips_illegal_chars():
    assert notes.safe_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"


def test_safe_filename_empty_falls_back():
    assert notes.safe_filename("///") == "untitled"


def test_make_frontmatter_fields():
    fm = notes.make_frontmatter(project="sentinel", tags=["esg"], created=NOW)
    assert fm.startswith("---\n")
    assert "created: 2026-07-05 14:30" in fm
    assert "agent: claude" in fm
    assert "project: sentinel" in fm
    assert "- esg" in fm
    assert fm.endswith("---\n\n")


def test_log_session_creates_note_and_updates_index(vault):
    rel = notes.log_session(
        vault, "LiveSync setup", "We configured CouchDB.",
        project="tesseract", tags=["infra"], now=NOW,
    )
    assert rel == "Claude/Sessions/2026-07-05 LiveSync setup.md"
    body = vault.read(rel)
    assert "agent: claude" in body
    assert "We configured CouchDB." in body
    index = vault.read("Claude/Index.md")
    assert "[[2026-07-05 LiveSync setup]]" in index
    assert "tesseract" in index


def test_capture_appends_timestamped_bullet(vault):
    rel = notes.capture(vault, "check R2 pricing", now=NOW)
    assert rel == "Claude/Inbox/2026-07-05.md"
    assert vault.read(rel) == "- 14:30 check R2 pricing\n"
    notes.capture(vault, "second thought", now=NOW)
    assert vault.read(rel).count("- 14:30") == 2


def test_upsert_concept_creates_new(vault):
    rel = notes.upsert_concept(vault, "DuckDNS", "Free dynamic DNS.", now=NOW)
    assert rel == "Claude/Concepts/DuckDNS.md"
    body = vault.read(rel)
    assert body.startswith("---\n")
    assert "# DuckDNS" in body
    assert "Free dynamic DNS." in body


def test_upsert_concept_appends_to_existing(vault):
    notes.upsert_concept(vault, "CouchDB", "Used by LiveSync.", now=NOW)
    body = vault.read("Claude/Concepts/CouchDB.md")
    assert "Document database used for LiveSync." in body  # original preserved
    assert "## Update 2026-07-05" in body
    assert "Used by LiveSync." in body
