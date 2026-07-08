import pytest

from tesseract_mcp.hybrid import hybrid_search, rrf_fuse


class FakeEmbedder:
    """Maps note text to a deterministic vector by simple keyword presence,
    so 'semantic' matches can be tested without a real model."""

    VOCAB = ["logistics", "cooking", "finance"]

    def embed_batch(self, texts):
        return [
            [1.0 if word in t.lower() else 0.0 for word in self.VOCAB]
            for t in texts
        ]


def test_rrf_fuse_prefers_items_ranked_high_in_both_lists():
    a = ["x.md", "y.md", "z.md"]
    b = ["y.md", "x.md", "z.md"]
    fused = rrf_fuse([a, b])
    assert fused[0] in ("x.md", "y.md")  # both near top of both lists
    assert fused[-1] == "z.md"           # last in both lists


def test_rrf_fuse_includes_item_only_in_one_list():
    a = ["x.md"]
    b = []
    assert rrf_fuse([a, b]) == ["x.md"]


def test_rrf_fuse_empty_lists_returns_empty():
    assert rrf_fuse([[], []]) == []


def test_hybrid_search_exact_keyword_match(vault, vault_dir):
    (vault_dir / "Logistics.md").write_text(
        "This note is about supply chain logistics operations.\n",
        encoding="utf-8",
    )
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "logistics")
    assert "Logistics.md" in [h.path for h in hits]


def test_hybrid_search_respects_tag_filter(vault):
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "e", tags=["esg"])
    assert [h.path for h in hits] == ["Projects/Sentinel ESG.md"]


def test_hybrid_search_respects_limit(vault, vault_dir):
    for i in range(5):
        (vault_dir / f"Note{i}.md").write_text("shared keyword here", encoding="utf-8")
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "shared", limit=2)
    assert len(hits) == 2


def test_hybrid_search_no_match_returns_empty(vault):
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "zzzznomatch")
    assert hits == []
