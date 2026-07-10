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


from tesseract_mcp.evals import FIXTURE_GOLDEN, FIXTURE_VAULT


def test_fixture_golden_paths_all_exist():
    queries = load_golden(FIXTURE_GOLDEN)
    assert len(queries) == 16
    assert validate_paths(queries, FIXTURE_VAULT, strict=True) == {}


from tesseract_mcp.evals import run_evals
from tesseract_mcp.vault import Vault


class KeywordEmbedder:
    """Same FakeEmbedder pattern as tests/test_hybrid.py: deterministic
    keyword-presence vectors so semantic ranking is testable modelless."""

    VOCAB = ["alpha", "beta", "gamma"]

    def embed_batch(self, texts):
        return [
            [1.0 if w in t.lower() else 0.0 for w in self.VOCAB] for t in texts
        ]


def _eval_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "vault"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes" / "A.md").write_text("alpha alpha content", encoding="utf-8")
    (root / "Notes" / "B.md").write_text("beta content", encoding="utf-8")
    return Vault(root)


def test_run_evals_scores_hits(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [GoldenQuery(id="q1", query="alpha", expect=["Notes/A.md"])]
    sc = run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)
    assert sc.results[0].first_rank == 1
    assert sc.success_at[5] == 1.0 and sc.recall_at[10] == 1.0
    assert sc.mrr == 1.0 and sc.skipped == 0


def test_run_evals_zero_when_never_found(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [GoldenQuery(id="q1", query="zzz-nowhere", expect=["Notes/B.md"])]
    sc = run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)
    assert sc.results[0].first_rank is None
    assert sc.mrr == 0.0 and sc.success_at[10] == 0.0


def test_run_evals_strict_raises_on_stale_path(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [GoldenQuery(id="q1", query="alpha", expect=["Notes/GONE.md"])]
    with pytest.raises(EvalConfigError):
        run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)


def test_run_evals_lenient_skips_fully_stale_query(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [
        GoldenQuery(id="stale", query="alpha", expect=["Notes/GONE.md"]),
        GoldenQuery(id="ok", query="alpha", expect=["Notes/A.md"]),
    ]
    sc = run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs, lenient=True)
    assert sc.skipped == 1
    assert sc.results[0].skipped is True
    # aggregates computed over the scored query only
    assert sc.success_at[5] == 1.0 and sc.mrr == 1.0


def test_run_evals_accept_counts_for_rank_not_recall(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    # B is accept-only; a query that only finds B succeeds but has recall 0
    qs = [GoldenQuery(id="q1", query="beta", expect=["Notes/A.md"],
                      accept=["Notes/B.md"])]
    sc = run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)
    r = sc.results[0]
    assert r.first_rank is not None          # B found -> relevant
    assert r.recall_at[10] == 0.0            # but expect A never showed
