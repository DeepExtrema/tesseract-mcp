from tesseract_mcp.evals import (
    first_relevant_rank, recall_at_k, success_at_k,
)


def test_first_relevant_rank_is_one_based():
    assert first_relevant_rank(["a.md", "b.md", "c.md"], {"b.md"}) == 2


def test_first_relevant_rank_none_when_absent():
    assert first_relevant_rank(["a.md"], {"z.md"}) is None


def test_first_relevant_rank_empty_hits():
    assert first_relevant_rank([], {"z.md"}) is None


def test_recall_at_k_counts_expect_fraction_within_k():
    hits = ["a.md", "b.md", "c.md", "d.md"]
    assert recall_at_k(hits, {"a.md", "d.md"}, 2) == 0.5
    assert recall_at_k(hits, {"a.md", "d.md"}, 4) == 1.0


def test_recall_at_k_empty_expect_is_zero():
    assert recall_at_k(["a.md"], set(), 5) == 0.0


def test_success_at_k_any_relevant_in_top_k():
    assert success_at_k(["a.md", "b.md"], {"b.md"}, 2) is True
    assert success_at_k(["a.md", "b.md"], {"b.md"}, 1) is False
