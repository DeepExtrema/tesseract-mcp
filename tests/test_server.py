import asyncio

import pytest

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
