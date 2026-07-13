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


def test_candidate_pairs_same_type_only():
    ents = [
        {"path": "p1", "type": "person"},
        {"path": "p2", "type": "person"},
        {"path": "o1", "type": "organization"},
    ]
    vectors = {"p1": [1.0, 0.0], "p2": [1.0, 0.01], "o1": [1.0, 0.0]}
    pairs = blocking._candidate_pairs(ents, ents, vectors, k=5, threshold=0.85)
    assert pairs == {("p1", "p2")}  # o1 identical direction but wrong type


def test_candidate_pairs_respects_threshold():
    ents = [{"path": "a", "type": "topic"}, {"path": "b", "type": "topic"}]
    vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0]}  # cosine 0.0
    assert blocking._candidate_pairs(ents, ents, vectors, k=5, threshold=0.85) == set()


def test_candidate_pairs_top_k_limit():
    ents = [{"path": f"p{i}", "type": "topic"} for i in range(6)]
    vectors = {f"p{i}": [1.0, i * 0.001] for i in range(6)}  # all near-parallel
    pairs = blocking._candidate_pairs([ents[0]], ents, vectors, k=2, threshold=0.85)
    assert len(pairs) == 2  # only p0's 2 nearest, not all 5


def test_cluster_pairs_unions_overlapping():
    # a-b and b-c overlap on b -> one cluster {a,b,c}
    clusters = blocking._cluster_pairs({("a", "b"), ("b", "c")}, max_cluster=10)
    assert clusters == [["a", "b", "c"]]


def test_cluster_pairs_splits_oversize():
    members = [f"n{i:02d}" for i in range(11)]
    pairs = {("n00", m) for m in members[1:]}  # star -> one component of 11
    clusters = blocking._cluster_pairs(pairs, max_cluster=10)
    assert sorted(len(c) for c in clusters) == [1, 10]


def test_candidate_clusters_maps_to_entities_and_drops_singletons():
    ents = [
        {"path": "a", "type": "topic"}, {"path": "b", "type": "topic"},
        {"path": "lonely", "type": "topic"},
    ]
    vectors = {"a": [1.0, 0.0], "b": [1.0, 0.01], "lonely": [0.0, 1.0]}
    clusters = blocking.candidate_clusters(ents, ents, vectors)
    assert len(clusters) == 1
    assert {e["path"] for e in clusters[0]} == {"a", "b"}
