from tesseract_mcp.evals import (
    GoldenQuery, first_relevant_rank, recall_at_k, success_at_k,
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


import pytest

from tesseract_mcp.evals import EvalConfigError, load_golden, validate_paths

GOLDEN_YAML = """\
- id: q1
  query: alpha beta
  expect: [Notes/A.md]
  accept: [Notes/B.md]
  tags: [x]
  folder: Notes
  note: demo
- id: q2
  query: gamma
  expect: [Notes/B.md]
"""


def test_load_golden_yaml(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text(GOLDEN_YAML, encoding="utf-8")
    qs = load_golden(p)
    assert [q.id for q in qs] == ["q1", "q2"]
    assert qs[0].accept == ["Notes/B.md"]
    assert qs[0].tags == ["x"] and qs[0].folder == "Notes"
    assert qs[1].accept == [] and qs[1].tags is None


def test_load_golden_from_markdown_fence(tmp_path):
    p = tmp_path / "Evals.md"
    p.write_text("# Golden\n\n```yaml\n" + GOLDEN_YAML + "```\n", encoding="utf-8")
    assert [q.id for q in load_golden(p)] == ["q1", "q2"]


def test_load_golden_markdown_without_fence_errors(tmp_path):
    p = tmp_path / "Evals.md"
    p.write_text("no yaml here", encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_golden(p)


def test_load_golden_duplicate_id_errors(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text(
        "- {id: q1, query: a, expect: [A.md]}\n- {id: q1, query: b, expect: [B.md]}\n",
        encoding="utf-8",
    )
    with pytest.raises(EvalConfigError):
        load_golden(p)


def test_load_golden_empty_expect_errors(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text("- {id: q1, query: a, expect: []}\n", encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_golden(p)


def test_load_golden_missing_file_errors(tmp_path):
    with pytest.raises(EvalConfigError):
        load_golden(tmp_path / "nope.yaml")


def _mini_vault(tmp_path):
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "A.md").write_text("alpha", encoding="utf-8")
    return tmp_path


def test_validate_paths_strict_raises_listing_missing(tmp_path):
    root = _mini_vault(tmp_path)
    qs = [GoldenQuery(id="q1", query="a", expect=["Notes/A.md", "Notes/GONE.md"])]
    with pytest.raises(EvalConfigError, match="GONE.md"):
        validate_paths(qs, root, strict=True)


def test_validate_paths_lenient_returns_missing_map(tmp_path):
    root = _mini_vault(tmp_path)
    qs = [GoldenQuery(id="q1", query="a", expect=["Notes/GONE.md"])]
    assert validate_paths(qs, root, strict=False) == {"q1": ["Notes/GONE.md"]}
