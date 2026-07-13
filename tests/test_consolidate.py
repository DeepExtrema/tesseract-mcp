import yaml

from tesseract_mcp import cache, consolidate
from tesseract_mcp.extractor import Extraction, ExtractorError
from tesseract_mcp.graphstore import GraphStore, entity_rel_path
from tesseract_mcp.search import parse_frontmatter

ORACLE_VM = {"name": "Oracle VM", "type": "organization", "aliases": [], "summary": "Cloud VM."}
ORACLE_DEPLOY = {"name": "Oracle VM deploy", "type": "organization", "aliases": [], "summary": "Deploying it."}


class FakeEmbedder:
    def embed_batch(self, texts):
        return [[float(len(t)), 1.0] for t in texts]


class FakeBackend:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        return self.reply


class FlakyBackend:
    """Raises on any prompt mentioning `boom_name`; else returns `reply`."""

    def __init__(self, reply, boom_name):
        self.reply = reply
        self.boom_name = boom_name
        self.calls = 0

    def complete_json(self, prompt):
        self.calls += 1
        if self.boom_name in prompt:
            raise ExtractorError("claude timed out after 120s")
        return self.reply


def _ent(name, etype="organization"):
    return {"name": name, "type": etype, "aliases": [], "summary": name}


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
    result = consolidate.run(vault, FakeBackend(MERGE), apply=False,
                             embedder=FakeEmbedder())
    assert result["proposed"] and result["applied"] is False
    assert "Merged into" not in vault.read(entity_rel_path("organization", "Oracle VM deploy"))


def test_run_reports_skipped_batches(vault):
    seed(vault)
    result = consolidate.run(vault, FakeBackend(MERGE), apply=False,
                             embedder=FakeEmbedder())
    assert result["skipped_batches"] == 0


def test_apply_merges_mentions_relations_aliases_and_redirects(vault):
    seed(vault)
    result = consolidate.run(vault, FakeBackend(MERGE), apply=True,
                             embedder=FakeEmbedder())
    assert result["applied"] is True and result["merged_entities"] == 1
    canon = vault.read(entity_rel_path("organization", "Oracle VM"))
    assert "[[B|" in canon                      # dup's mention moved over
    assert "related_to [[" in canon             # dup's relation moved over
    assert "Oracle VM deploy" in canon          # name folded into aliases
    dup = vault.read(entity_rel_path("organization", "Oracle VM deploy"))
    assert "merged_into:" in dup and "Merged into [[Oracle VM]]" in dup


def test_apply_merge_finds_dup_by_filename_when_canonical_has_alias(vault):
    seed(vault)
    canon_rel = entity_rel_path("organization", "Oracle VM")
    canon_text = vault.read(canon_rel)
    meta = parse_frontmatter(canon_text)
    meta["aliases"] = ["Oracle VM deploy"]
    end = canon_text.find("\n---", 3)
    fm = "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---"
    vault.write(canon_rel, fm + canon_text[end + 4 :], overwrite=True)
    consolidate.run(vault, FakeBackend(MERGE), apply=True, embedder=FakeEmbedder())
    dup = vault.read(entity_rel_path("organization", "Oracle VM deploy"))
    assert "merged_into:" in dup


def test_cache_rebuild_skips_redirect_stubs(vault, tmp_path):
    seed(vault)
    consolidate.run(vault, FakeBackend(MERGE), apply=True, embedder=FakeEmbedder())
    db = tmp_path / "g.db"
    cache.rebuild(vault, db)
    names = [e["name"] for e in cache.find_entity(db, "oracle")]
    assert names == ["Oracle VM"]               # stub not an entity anymore
    assert cache.find_entity(db, "oracle")[0]["mention_count"] == 2


def test_gather_entities_includes_body_summary(vault):
    seed(vault)
    got = {e["name"]: e for e in consolidate.gather_entities(vault)}
    assert got["Oracle VM"]["summary"] == "Cloud VM."
    assert got["Oracle VM deploy"]["summary"] == "Deploying it."


def test_entity_summary_no_frontmatter_with_horizontal_rule():
    text = "# Foo\n\nLine one.\n\n---\n\nLine two.\n\n## Mentions\n\n## Relations\n"
    # no leading frontmatter: the --- is a horizontal rule, NOT a frontmatter
    # terminator, so the whole body before Mentions is the summary
    assert consolidate._entity_summary(text) == "Line one.\n\n---\n\nLine two."


def test_entity_summary_empty_when_no_body():
    text = "---\nentity: topic\n---\n\n# Foo\n\n## Mentions\n\n## Relations\n"
    assert consolidate._entity_summary(text) == ""


def test_adjudicate_isolates_a_failing_batch():
    good = [_ent("Acme"), _ent("Acme Corp")]
    bad = [_ent("Zeta"), _ent("Zeta Inc")]
    all_ents = good + bad
    batches = [[good], [bad]]  # one cluster per batch
    reply = {"merges": [{"type": "organization", "canonical": "Acme",
                         "duplicates": ["Acme Corp"]}]}
    backend = FlakyBackend(reply, boom_name="Zeta")
    merges, skipped = consolidate.adjudicate_batches(backend, batches, all_ents)
    assert skipped == 1
    assert merges == [{"type": "organization", "canonical": "Acme",
                       "duplicates": ["Acme Corp"]}]


def test_adjudicate_dedupes_merges_across_batches():
    ents = [_ent("Acme"), _ent("Acme Corp")]
    reply = {"merges": [{"type": "organization", "canonical": "Acme",
                         "duplicates": ["Acme Corp"]}]}
    batches = [[ents], [ents]]  # same reply twice
    merges, skipped = consolidate.adjudicate_batches(
        FakeBackend(reply), batches, ents)
    assert skipped == 0 and len(merges) == 1
