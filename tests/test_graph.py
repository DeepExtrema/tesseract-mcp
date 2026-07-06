from tesseract_mcp import graph


def test_query_notes_by_project(vault):
    vault.write(
        "Claude/Sessions/2026-07-05 Q.md",
        "---\ncreated: 2026-07-05 10:00\nagent: claude\nproject: tesseract\ntags: [infra]\n---\n\nBody.\n",
    )
    got = graph.query_notes(vault, project="tesseract")
    assert [r["path"] for r in got] == ["Claude/Sessions/2026-07-05 Q.md"]
    assert got[0]["frontmatter"]["project"] == "tesseract"


def test_query_notes_by_tags_case_insensitive(vault):
    got = graph.query_notes(vault, tags=["ESG"])
    assert [r["path"] for r in got] == ["Projects/Sentinel ESG.md"]


def test_query_notes_plain_lists_frontmattered_only(vault):
    got = graph.query_notes(vault)
    paths = {r["path"] for r in got}
    assert "Projects/Sentinel ESG.md" in paths
    assert "Daily.md" not in paths  # no frontmatter


def test_query_notes_limit(vault):
    assert len(graph.query_notes(vault, limit=1)) == 1


def test_backlinks_found_case_insensitive(vault):
    vault.write("Claude/Inbox/ref.md", "See [[couchdb]] for details.\n")
    vault.write("Claude/Inbox/ref2.md", "Also [[Claude/Concepts/CouchDB|the db]].\n")
    got = graph.get_backlinks(vault, "Claude/Concepts/CouchDB.md")
    assert set(got) == {"Claude/Inbox/ref.md", "Claude/Inbox/ref2.md"}


def test_backlinks_none(vault):
    assert graph.get_backlinks(vault, "Daily.md") == []


def test_backlinks_excludes_self(vault):
    vault.write(
        "Claude/Concepts/SelfRef.md", "I link to [[SelfRef]] myself.\n"
    )
    assert graph.get_backlinks(vault, "Claude/Concepts/SelfRef.md") == []


def test_query_notes_preserves_json_types(vault):
    vault.write(
        "Claude/Concepts/Typed.md",
        "---\ncreated: 2026-07-05\nproject: typed\ntags: [a, b]\ncount: 3\ndone: true\n---\n\nBody.\n",
    )
    got = graph.query_notes(vault, project="typed")
    fm = got[0]["frontmatter"]
    assert fm["tags"] == ["a", "b"]
    assert fm["count"] == 3
    assert fm["done"] is True
    assert isinstance(fm["created"], str) and fm["created"].startswith("2026-07-05")


def test_query_notes_output_is_json_serializable(vault):
    import json

    vault.write(
        "Claude/Concepts/Dated.md",
        "---\ncreated: 2026-07-05\nproject: dated\n---\n\nBody.\n",
    )
    json.dumps(graph.query_notes(vault, project="dated"))  # must not raise


def test_list_recent_orders_newest_first(vault):
    import os
    import time

    vault.write("Claude/Inbox/old.md", "old")
    vault.write("Claude/Inbox/new.md", "new")
    now = time.time()
    os.utime(vault.resolve("Claude/Inbox/old.md"), (now - 1000, now - 1000))
    os.utime(vault.resolve("Claude/Inbox/new.md"), (now, now))
    got = graph.list_recent(vault, n=2)
    assert got[0]["path"] == "Claude/Inbox/new.md"
    assert len(got) == 2 and all("modified" in r for r in got)
