from tesseract_mcp import cache, consolidate
from tesseract_mcp.extractor import Extraction
from tesseract_mcp.graphstore import GraphStore, entity_rel_path

ORACLE_VM = {"name": "Oracle VM", "type": "organization", "aliases": [], "summary": "Cloud VM."}
ORACLE_DEPLOY = {"name": "Oracle VM deploy", "type": "organization", "aliases": [], "summary": "Deploying it."}


class FakeBackend:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def seed(vault):
    store = GraphStore(vault)
    store.apply("A.md", Extraction([ORACLE_VM], []))
    store.apply("B.md", Extraction([ORACLE_DEPLOY], [
        {"from": "Oracle VM deploy", "from_type": "organization", "rel": "related_to",
         "to": "DEPLOY guide", "to_type": "source", "evidence": ""},
    ]))
    return store


MERGE = {"merges": [{"type": "organization", "canonical": "Oracle VM",
                     "duplicates": ["Oracle VM deploy"]}]}


def test_gather_entities(vault):
    seed(vault)
    got = consolidate.gather_entities(vault)
    names = {(e["type"], e["name"]) for e in got}
    assert ("organization", "Oracle VM") in names
    assert ("organization", "Oracle VM deploy") in names


def test_propose_merges_validates(vault):
    seed(vault)
    entities = consolidate.gather_entities(vault)
    bad = {"merges": [
        {"type": "organization", "canonical": "Oracle VM", "duplicates": ["Oracle VM deploy"]},
        {"type": "organization", "canonical": "Nonexistent", "duplicates": ["Oracle VM"]},
        {"type": "person", "canonical": "Oracle VM", "duplicates": ["Oracle VM deploy"]},
    ]}
    got = consolidate.propose_merges(FakeBackend(bad), entities)
    assert got == [{"type": "organization", "canonical": "Oracle VM",
                    "duplicates": ["Oracle VM deploy"]}]


def test_dry_run_changes_nothing(vault):
    seed(vault)
    result = consolidate.run(vault, FakeBackend(MERGE), apply=False)
    assert result["proposed"] and result["applied"] is False
    assert "Merged into" not in vault.read(entity_rel_path("organization", "Oracle VM deploy"))


def test_apply_merges_mentions_relations_aliases_and_redirects(vault):
    seed(vault)
    result = consolidate.run(vault, FakeBackend(MERGE), apply=True)
    assert result["applied"] is True and result["merged_entities"] == 1
    canon = vault.read(entity_rel_path("organization", "Oracle VM"))
    assert "[[B|" in canon                      # dup's mention moved over
    assert "related_to [[" in canon             # dup's relation moved over
    assert "Oracle VM deploy" in canon          # name folded into aliases
    dup = vault.read(entity_rel_path("organization", "Oracle VM deploy"))
    assert "merged_into:" in dup and "Merged into [[Oracle VM]]" in dup


def test_cache_rebuild_skips_redirect_stubs(vault, tmp_path):
    seed(vault)
    consolidate.run(vault, FakeBackend(MERGE), apply=True)
    db = tmp_path / "g.db"
    cache.rebuild(vault, db)
    names = [e["name"] for e in cache.find_entity(db, "oracle")]
    assert names == ["Oracle VM"]               # stub not an entity anymore
    assert cache.find_entity(db, "oracle")[0]["mention_count"] == 2
