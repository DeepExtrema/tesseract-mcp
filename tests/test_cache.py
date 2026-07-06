import sqlite3

import pytest

from tesseract_mcp import cache
from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GraphStore

ACME = {"name": "Acme Corp", "type": "organization", "aliases": ["ACME"], "summary": "A company."}
CHAIN = {"name": "Supply Chain", "type": "domain", "aliases": [], "summary": "Logistics."}
REL = {"from": "Acme Corp", "from_type": "organization", "rel": "operates_in",
       "to": "Supply Chain", "to_type": "domain", "evidence": "logistics"}


@pytest.fixture
def populated(vault, tmp_path):
    store = GraphStore(vault)
    store.apply("Projects/Sentinel ESG.md", Extraction([ACME, CHAIN], [REL]))
    vault.write("Claude/Inbox/interview.md", "Talked to [[Acme Corp]] folks.\n")
    store.apply("Claude/Inbox/interview.md", Extraction([ACME], []))
    db = tmp_path / "graph.db"
    cache.rebuild(vault, db)
    return db


def test_rebuild_creates_tables(populated):
    con = sqlite3.connect(populated)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entities", "edges", "mentions"} <= names


def test_find_entity_by_name_and_alias(populated):
    got = cache.find_entity(populated, "acme")
    assert got and got[0]["name"] == "Acme Corp" and got[0]["type"] == "organization"
    assert cache.find_entity(populated, "ACME")  # alias
    assert got[0]["mention_count"] == 2
    assert {"rel": "operates_in", "to": "Supply Chain"} in [
        {"rel": e["rel"], "to": e["dst"]} for e in got[0]["relations"]
    ]


def test_find_entity_type_filter(populated):
    assert cache.find_entity(populated, "supply", type="domain")
    assert cache.find_entity(populated, "supply", type="person") == []


def test_related_notes_shared_entity(populated, vault):
    got = cache.related_notes(populated, vault, "Claude/Inbox/interview.md", hops=1)
    paths = [r["path"] for r in got]
    assert "Projects/Sentinel ESG.md" in paths
    assert any("Acme Corp" in r["via"] for r in got)


def test_related_notes_excludes_self_and_graph_notes(populated, vault):
    got = cache.related_notes(populated, vault, "Claude/Inbox/interview.md", hops=2)
    paths = [r["path"] for r in got]
    assert "Claude/Inbox/interview.md" not in paths
    assert not any(p.startswith("Claude/Graph/") for p in paths)


def test_stats(populated):
    s = cache.stats(populated)
    assert s["entities"]["organization"] == 1
    assert s["entities"]["domain"] == 1
    assert s["edges"] == 1
    assert s["mentions"] == 3


def test_rebuild_atomic_replaces(populated, vault, tmp_path):
    db = populated
    cache.rebuild(vault, db)  # second rebuild over existing db must not error
    assert cache.find_entity(db, "acme")
