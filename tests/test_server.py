import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tesseract_mcp import server
from tesseract_mcp.vault import VaultError


@pytest.fixture(autouse=True)
def point_at_fixture_vault(vault_dir, monkeypatch):
    monkeypatch.setenv("TESSERACT_VAULT_PATH", str(vault_dir))
    server._vault = None  # reset cache between tests
    yield
    server._vault = None


def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "search_brain",
        "read_note",
        "log_session",
        "capture",
        "upsert_concept",
        "write_note",
        "add_task",
        "list_tasks",
        "query_notes",
        "get_backlinks",
        "list_recent",
    }


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("TESSERACT_VAULT_PATH")
    with pytest.raises(VaultError, match="TESSERACT_VAULT_PATH"):
        server.get_vault()


def test_search_brain_returns_dicts():
    hits = server.search_brain("ingestion pipeline")
    assert hits == [
        {
            "path": "Projects/Sentinel ESG.md",
            "excerpt": "ESG incident ingestion pipeline with CouchDB-free architecture.",
        }
    ]


def test_search_brain_limit():
    assert len(server.search_brain("e", limit=1)) == 1


def test_read_note():
    assert "Remember to check" in server.read_note("Daily.md")


def test_log_session_roundtrip():
    rel = server.log_session(
        "Test session", "Did things.", project="tesseract", tags=["test"]
    )
    assert rel.startswith("Claude/Sessions/")
    assert "Did things." in server.read_note(rel)


def test_capture_roundtrip():
    rel = server.capture("a quick thought")
    assert "a quick thought" in server.read_note(rel)


def test_upsert_concept_roundtrip():
    rel = server.upsert_concept("Testing", "Notes about testing.")
    assert "Notes about testing." in server.read_note(rel)


def test_write_note_quarantine_enforced():
    with pytest.raises(VaultError, match="outside Claude/"):
        server.write_note("Projects/Injected.md", "nope")


def test_write_note_with_confirmation():
    server.write_note("Projects/Asked For.md", "yes", confirm_outside_claude=True)
    assert server.read_note("Projects/Asked For.md") == "yes"


def test_quarantine_error_reaches_mcp_client_verbatim():
    # In the installed mcp 1.28, FastMCP.call_tool does not return an error
    # result — it raises mcp.server.fastmcp.exceptions.ToolError, wrapping
    # the original exception message as "Error executing tool {name}: {msg}".
    # We assert the quarantine message survives verbatim inside that wrapper.
    with pytest.raises(ToolError, match="outside Claude/"):
        asyncio.run(
            server.mcp.call_tool(
                "write_note", {"path": "Projects/x.md", "content": "no"}
            )
        )


def test_add_and_list_tasks_roundtrip():
    server.add_task("torture the tests", due="2026-07-09")
    got = server.list_tasks()
    assert any(t["text"].startswith("torture the tests") for t in got)


def test_query_notes_roundtrip():
    assert any(
        r["path"] == "Projects/Sentinel ESG.md"
        for r in server.query_notes(tags=["esg"])
    )


def test_backlinks_and_recent_roundtrip():
    server.capture("see [[CouchDB]] note")
    assert any("Inbox" in p for p in server.get_backlinks("Claude/Concepts/CouchDB.md"))
    assert server.list_recent(n=3)
