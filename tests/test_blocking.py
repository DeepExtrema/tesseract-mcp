from tesseract_mcp import blocking


def test_identity_text_combines_name_aliases_summary():
    e = {"name": "Oracle VM", "type": "organization",
         "aliases": ["OVM"], "summary": "Cloud VM.", "path": "x"}
    assert blocking.identity_text(e) == "Oracle VM\nOVM\nCloud VM."


def test_identity_hash_changes_with_summary():
    a = {"name": "N", "aliases": [], "summary": "one", "path": "p"}
    b = {"name": "N", "aliases": [], "summary": "two", "path": "p"}
    assert blocking.identity_hash(a) != blocking.identity_hash(b)


def test_identity_hash_stable_for_same_identity():
    a = {"name": "N", "aliases": ["x"], "summary": "s", "path": "p"}
    b = {"name": "N", "aliases": ["x"], "summary": "s", "path": "OTHER"}
    assert blocking.identity_hash(a) == blocking.identity_hash(b)  # path is NOT identity


class FakeEmbedder:
    """Deterministic stand-in — records each batch, no model download."""

    def __init__(self):
        self.calls = []

    def embed_batch(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]


def _ents():
    return [
        {"name": "Acme", "type": "organization", "aliases": [], "summary": "a",
         "path": "Claude/Graph/Organizations/Acme"},
        {"name": "Acme Corp", "type": "organization", "aliases": [], "summary": "b",
         "path": "Claude/Graph/Organizations/Acme Corp"},
    ]


def test_compute_entity_vectors_returns_vector_per_entity(tmp_path):
    got = blocking.compute_entity_vectors(_ents(), tmp_path, FakeEmbedder())
    assert set(got) == {"Claude/Graph/Organizations/Acme",
                        "Claude/Graph/Organizations/Acme Corp"}


def test_unchanged_identity_is_a_cache_hit(tmp_path):
    emb = FakeEmbedder()
    blocking.compute_entity_vectors(_ents(), tmp_path, emb)
    first = len(emb.calls)
    blocking.compute_entity_vectors(_ents(), tmp_path, emb)
    assert len(emb.calls) == first  # nothing re-embedded


def test_changed_identity_is_reembedded(tmp_path):
    emb = FakeEmbedder()
    blocking.compute_entity_vectors(_ents(), tmp_path, emb)
    changed = _ents()
    changed[0]["summary"] = "DIFFERENT"
    blocking.compute_entity_vectors(changed, tmp_path, emb)
    assert emb.calls[-1] == ["Acme\n\nDIFFERENT"]  # only the changed one
