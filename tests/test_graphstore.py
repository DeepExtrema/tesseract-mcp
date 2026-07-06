from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GRAPH_ROOT, GraphStore, entity_rel_path

ACME = {"name": "Acme Corp", "type": "organization", "aliases": ["ACME"], "summary": "A company."}
CHAIN = {"name": "Supply Chain", "type": "domain", "aliases": [], "summary": "Logistics."}
REL = {"from": "Acme Corp", "from_type": "organization", "rel": "operates_in",
       "to": "Supply Chain", "to_type": "domain", "evidence": "Acme runs logistics."}


def test_entity_rel_path():
    assert entity_rel_path("organization", "Acme Corp") == "Claude/Graph/Organizations/Acme Corp.md"
    assert entity_rel_path("person", 'Bad:Name?') == "Claude/Graph/People/BadName.md"


def test_upsert_creates_note(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    body = vault.read(rel)
    assert "entity: organization" in body
    assert "# Acme Corp" in body and "A company." in body
    assert "## Mentions" in body and "## Relations" in body
    assert "ACME" in body  # alias in frontmatter


def test_upsert_existing_merges_aliases_only(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    before = vault.read(rel)
    rel2 = store.upsert_entity({**ACME, "aliases": ["ACME", "Acme Inc"], "summary": "Different."})
    assert rel2 == rel
    after = vault.read(rel)
    assert "Acme Inc" in after
    assert "A company." in after and "Different." not in after  # summary not rewritten


def test_find_by_alias_casefold(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    assert store.find_entity_note("organization", "acme") == rel
    assert store.find_entity_note("organization", "ACME CORP") == rel
    assert store.find_entity_note("organization", "Unknown Co") is None


def test_add_mention_idempotent(vault):
    store = GraphStore(vault)
    rel = store.upsert_entity(ACME)
    assert store.add_mention(rel, "Projects/Sentinel ESG.md", "mentioned in pipeline") is True
    assert store.add_mention(rel, "Projects/Sentinel ESG.md", "again") is False
    body = vault.read(rel)
    assert body.count("[[Sentinel ESG]]") == 1
    assert "mentioned in pipeline" in body


def test_add_relation_idempotent(vault):
    store = GraphStore(vault)
    a = store.upsert_entity(ACME)
    b = store.upsert_entity(CHAIN)
    assert store.add_relation(a, "operates_in", b) is True
    assert store.add_relation(a, "operates_in", b) is False
    assert "- operates_in [[Supply Chain]]" in vault.read(a)


def test_apply_full_extraction(vault):
    store = GraphStore(vault)
    counts = store.apply("Projects/Sentinel ESG.md", Extraction([ACME, CHAIN], [REL]))
    assert counts == {"entities_created": 2, "entities_merged": 0,
                      "mentions_added": 2, "relations_added": 1}
    acme = vault.read(entity_rel_path("organization", "Acme Corp"))
    assert "[[Sentinel ESG]]" in acme
    assert "- operates_in [[Supply Chain]]" in acme


def test_apply_twice_is_idempotent(vault):
    store = GraphStore(vault)
    store.apply("Daily.md", Extraction([ACME], []))
    counts = store.apply("Daily.md", Extraction([ACME], []))
    assert counts["entities_created"] == 0
    assert counts["mentions_added"] == 0


def test_relation_entity_not_extracted_gets_stub(vault):
    """A relation endpoint that wasn't in entities[] still gets an entity note."""
    store = GraphStore(vault)
    counts = store.apply("Daily.md", Extraction([ACME], [REL]))
    assert vault.resolve(entity_rel_path("domain", "Supply Chain")).exists()
    assert counts["entities_created"] == 2
