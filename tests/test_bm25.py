from tesseract_mcp.bm25 import rank, tokenize


def test_tokenize_lowercases_and_splits():
    assert tokenize("Sentinel-ESG Pipeline!") == ["sentinel", "esg", "pipeline"]


def test_rank_favors_exact_term_over_no_match():
    corpus = {
        "a.md": "the sentinel esg pipeline ingests incident data",
        "b.md": "an unrelated note about weather patterns",
    }
    results = rank(corpus, "sentinel pipeline")
    assert [p for p, _ in results] == ["a.md"]


def test_rank_favors_rare_term_match():
    corpus = {
        "common.md": "the the the the the project project",
        "rare.md": "zephyr appears exactly once here",
    }
    results = rank(corpus, "zephyr")
    assert results and results[0][0] == "rare.md"


def test_rank_empty_corpus_returns_empty():
    assert rank({}, "anything") == []


def test_rank_no_match_returns_empty():
    corpus = {"a.md": "completely unrelated content"}
    assert rank(corpus, "zzzznomatch") == []


def test_rank_respects_limit():
    corpus = {f"n{i}.md": "shared keyword appears here" for i in range(10)}
    assert len(rank(corpus, "shared", limit=3)) == 3
