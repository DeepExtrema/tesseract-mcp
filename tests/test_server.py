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


@pytest.fixture(autouse=True)
def isolated_graph_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "graph-state"))


def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    assert {t.name for t in tools} == {
        "search_brain", "read_note", "log_session", "capture",
        "upsert_concept", "write_note", "add_task", "list_tasks",
        "query_notes", "get_backlinks", "list_recent",
        "index_brain", "find_entity", "related_notes", "graph_stats",
        "consolidate_graph", "onboard", "context_bundle",
    }


def test_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("TESSERACT_VAULT_PATH")
    with pytest.raises(VaultError, match="TESSERACT_VAULT_PATH"):
        server.get_vault()


def test_search_brain_returns_dicts():
    hits = server.search_brain("ingestion pipeline")
    assert hits[0] == {
        "path": "Projects/Sentinel ESG.md",
        "excerpt": "ESG incident ingestion pipeline with CouchDB-free architecture.",
    }


def test_search_brain_limit():
    assert len(server.search_brain("e", limit=1)) == 1


def test_search_brain_uses_hybrid_engine(monkeypatch):
    from tesseract_mcp import hybrid as hybrid_mod
    from tesseract_mcp.search import Hit

    called = {}

    def fake_hybrid_search(vault, state_root, embedder, query, tags=None, folder=None, limit=20):
        called["query"] = query
        called["limit"] = limit
        return [Hit("Fake.md", "fake excerpt")]

    monkeypatch.setattr(hybrid_mod, "hybrid_search", fake_hybrid_search)
    result = server.search_brain("anything", limit=5)
    assert called["query"] == "anything"
    assert called["limit"] == 5
    assert result == [{"path": "Fake.md", "excerpt": "fake excerpt"}]


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


def test_index_and_graph_tools_roundtrip(monkeypatch):
    from tesseract_mcp.extractor import Extraction

    class FakeExtractor:
        def extract(self, path, content):
            if "Sentinel" in path:
                return Extraction(
                    [{"name": "Acme Corp", "type": "organization", "aliases": [], "summary": "Co."}],
                    [],
                )
            return Extraction()

    monkeypatch.setattr(server, "_make_extractor", lambda: FakeExtractor())
    counts = server.index_brain()
    assert counts["processed"] > 0 and counts["entities_created"] == 1

    found = server.find_entity("acme")
    assert found and found[0]["type"] == "organization"

    related = server.related_notes("Projects/Sentinel ESG.md")
    assert isinstance(related, list)

    s = server.graph_stats()
    assert s["entities"]["organization"] == 1


def test_graph_tools_without_cache_raise_helpful_error():
    with pytest.raises(VaultError, match="index_brain"):
        server.find_entity("anything")


def test_consolidate_graph_dry_run(monkeypatch):
    class FakeBackend:
        def complete_json(self, prompt):
            return {"merges": []}

    monkeypatch.setattr(server, "_make_extractor", lambda: FakeBackend())
    result = server.consolidate_graph()
    assert result["applied"] is False and result["proposed"] == []


def test_server_instructions_orient_clients():
    text = server.mcp.instructions or ""
    assert "Claude/" in text
    assert "search_brain" in text
    assert "quarantine" in text.lower() or "outside Claude/" in text


def test_onboard_returns_orientation(vault_dir):
    (vault_dir / "Claude" / "README.md").write_text(
        "# The Claude/ Constitution\n\nRules here.\n", encoding="utf-8"
    )
    (vault_dir / "CLAUDE.md").write_text(
        "# Vault Guide\n\nRouting rules.\n", encoding="utf-8"
    )
    got = server.onboard()
    assert "Constitution" in got["constitution"]
    assert "Routing rules" in got["vault_guide"]
    assert any("search_brain" in t for t in got["tools"])
    assert got["graph"] == "not built yet — call index_brain"


def test_onboard_tolerates_missing_guides():
    got = server.onboard()  # fixture vault has neither guide file
    assert "not installed" in got["constitution"]
    assert "not installed" in got["vault_guide"]


def test_context_bundle_composes_search_and_graph(monkeypatch):
    from tesseract_mcp.extractor import Extraction

    class FakeExtractor:
        def extract(self, path, content):
            if "Sentinel" in path:
                return Extraction(
                    [{"name": "Acme Corp", "type": "organization", "aliases": [], "summary": "Co."}],
                    [],
                )
            return Extraction()

    monkeypatch.setattr(server, "_make_extractor", lambda: FakeExtractor())
    server.index_brain()

    bundle = server.context_bundle("ingestion pipeline")
    assert bundle["hits"]
    assert bundle["hits"][0]["path"] == "Projects/Sentinel ESG.md"
    assert any(e["name"] == "Acme Corp" for e in bundle["entities"])
    assert isinstance(bundle["related_notes"], list)


def test_context_bundle_without_graph_still_returns_hits():
    bundle = server.context_bundle("ingestion pipeline")
    assert bundle["hits"]
    assert bundle["entities"] == []
    assert bundle["related_notes"] == []
