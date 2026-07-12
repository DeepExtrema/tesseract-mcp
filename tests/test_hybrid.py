import pytest

from tesseract_mcp.hybrid import _excerpt, hybrid_search, rrf_fuse


class FakeEmbedder:
    """Maps note text to a deterministic vector by simple keyword presence,
    so 'semantic' matches can be tested without a real model."""

    VOCAB = ["logistics", "cooking", "finance"]

    def embed_batch(self, texts):
        return [
            [1.0 if word in t.lower() else 0.0 for word in self.VOCAB]
            for t in texts
        ]


class FakeSemanticEmbedder:
    """Embeds 'owe money' queries and 'invoice' notes into the same region
    of vector space despite zero shared tokens — the paraphrase case the
    vector half of hybrid search exists for."""

    def embed_batch(self, texts):
        out = []
        for t in texts:
            lower = t.lower()
            if "owe" in lower or "invoice" in lower:
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out


def test_hybrid_search_semantic_match_without_shared_tokens(vault, vault_dir):
    (vault_dir / "Contractors.md").write_text(
        "Outstanding invoices from contractors need review.\n", encoding="utf-8"
    )
    hits = hybrid_search(
        vault, vault.root, FakeSemanticEmbedder(), "who do I owe money to"
    )
    assert "Contractors.md" in [h.path for h in hits]


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


def test_substring_signal_only_when_bm25_empty(vault, vault_dir):
    # "aaa.md" contains the query only inside another word (substring match,
    # not a token match); "zzz.md" contains it as a real token. When BM25 has
    # results, the alphabetical substring signal must stay out of fusion, so
    # aaa.md must not appear at all.
    (vault_dir / "aaa.md").write_text(
        "an important announcement was made\n", encoding="utf-8"
    )
    (vault_dir / "zzz.md").write_text(
        "the port of hamburg is busy\n", encoding="utf-8"
    )
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "port")
    paths = [h.path for h in hits]
    assert "zzz.md" in paths       # real BM25 token match
    assert "aaa.md" not in paths   # substring-only; signal gated off


FRONTMATTER_NOTE = "---\ntags: [x]\n---\n\n# Title Line\n\nReal content here.\n"


def test_excerpt_semantic_only_hit_returns_first_body_line_not_delimiter():
    assert _excerpt(FRONTMATTER_NOTE, "Notes/N.md", "unrelated-query") == "# Title Line"


def test_excerpt_line_match_never_returns_frontmatter_line():
    text = "---\ntags: [billing]\n---\n\nInvoice for the billing cycle.\n"
    assert _excerpt(text, "Notes/N.md", "billing") == "Invoice for the billing cycle."


def test_excerpt_frontmatter_only_note_returns_empty():
    assert _excerpt("---\ntags: [x]\n---\n", "Notes/N.md", "zzz") == ""


def test_excerpt_title_match_unchanged():
    assert _excerpt(FRONTMATTER_NOTE, "Claude/Sessions/Weekly Review.md", "weekly") == "(title match)"


def test_hybrid_search_semantic_hit_excerpt_is_not_frontmatter(vault, vault_dir):
    (vault_dir / "Contractors.md").write_text(
        "---\ntags: [money]\n---\n\nOutstanding invoices from contractors.\n",
        encoding="utf-8",
    )
    hits = hybrid_search(
        vault, vault.root, FakeSemanticEmbedder(), "who do I owe money to"
    )
    by_path = {h.path: h.excerpt for h in hits}
    assert by_path["Contractors.md"] == "Outstanding invoices from contractors."


def test_substring_fallback_still_works_when_bm25_empty(vault):
    # Single-character query: BM25's [a-z0-9]+ tokenizer yields no token
    # matches, so the substring fallback must still return results.
    hits = hybrid_search(vault, vault.root, FakeEmbedder(), "e", tags=["esg"])
    assert [h.path for h in hits] == ["Projects/Sentinel ESG.md"]
