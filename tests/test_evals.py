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


def test_load_golden_scalar_expect_accept_tags_normalized(tmp_path):
    """A scalar where a list is expected must become a 1-item list, not
    be iterated character-by-character."""
    p = tmp_path / "golden.yaml"
    p.write_text(
        "- {id: q1, query: a, expect: Notes/A.md, accept: Notes/B.md, tags: x}\n",
        encoding="utf-8",
    )
    q = load_golden(p)[0]
    assert q.expect == ["Notes/A.md"]
    assert q.accept == ["Notes/B.md"]
    assert q.tags == ["x"]


def test_load_golden_non_list_non_scalar_field_errors(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text("- {id: q1, query: a, expect: {oops: 1}}\n", encoding="utf-8")
    with pytest.raises(EvalConfigError, match="expect"):
        load_golden(p)


def test_load_golden_empty_id_or_query_errors(tmp_path):
    p = tmp_path / "golden.yaml"
    p.write_text('- {id: "", query: a, expect: [A.md]}\n', encoding="utf-8")
    with pytest.raises(EvalConfigError, match="non-empty"):
        load_golden(p)
    p.write_text('- {id: q1, query: "  ", expect: [A.md]}\n', encoding="utf-8")
    with pytest.raises(EvalConfigError, match="non-empty"):
        load_golden(p)


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


def test_validate_paths_rejects_paths_escaping_the_vault(tmp_path):
    """Absolute and ..-traversal golden paths must never be probed: they are
    reported missing even when the target exists outside the vault."""
    outside = tmp_path / "outside.md"
    outside.write_text("exists but out of bounds", encoding="utf-8")
    root = tmp_path / "vault"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes" / "A.md").write_text("in bounds", encoding="utf-8")
    qs = [GoldenQuery(id="q1", query="a",
                      expect=[str(outside), "../outside.md", "Notes/A.md"])]
    missing = validate_paths(qs, root, strict=False)
    assert missing == {"q1": [str(outside), "../outside.md"]}
    with pytest.raises(EvalConfigError, match="outside.md"):
        validate_paths(qs, root, strict=True)


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


import json as jsonlib

from tesseract_mcp import evals as evals_mod
from tesseract_mcp.evals import append_history, format_table, main, to_json


def _scorecard(tmp_path, monkeypatch):
    vault = _eval_vault(tmp_path, monkeypatch)
    qs = [GoldenQuery(id="q1", query="alpha", expect=["Notes/A.md"])]
    return run_evals(vault, tmp_path / "state", KeywordEmbedder(), qs)


def test_format_table_has_aggregate_line(tmp_path, monkeypatch):
    out = format_table(_scorecard(tmp_path, monkeypatch))
    assert "MRR" in out and "success@10" in out and "q1" in out


def test_to_json_round_trips(tmp_path, monkeypatch):
    d = to_json(_scorecard(tmp_path, monkeypatch))
    assert d["mrr"] == 1.0
    assert d["queries"][0]["id"] == "q1"
    jsonlib.dumps(d)  # serializable


def test_append_history_writes_jsonl(tmp_path, monkeypatch):
    sc = _scorecard(tmp_path, monkeypatch)
    p = append_history(tmp_path / "state", sc, "vaultpath", "goldenpath")
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    rec = jsonlib.loads(lines[-1])
    assert rec["mrr"] == 1.0 and rec["vault"] == "vaultpath"


def test_main_fixture_mode_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    vault_root = tmp_path / "vault"
    (vault_root / "Notes").mkdir(parents=True)
    (vault_root / "Notes" / "A.md").write_text("alpha", encoding="utf-8")
    golden = tmp_path / "golden.yaml"
    golden.write_text("- {id: q1, query: alpha, expect: [Notes/A.md]}\n",
                      encoding="utf-8")
    monkeypatch.setattr(evals_mod, "_make_embedder", KeywordEmbedder)
    rc = main(["--vault", str(vault_root), "--golden", str(golden), "--json"])
    assert rc == 0
    out = jsonlib.loads(capsys.readouterr().out)
    assert out["mrr"] == 1.0
    history = tmp_path / "state" / "eval_history.jsonl"
    assert history.exists()


def test_main_history_write_failure_warns_not_crashes(tmp_path, monkeypatch, capsys):
    """A failed history append must not turn a successful eval into a traceback."""
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    vault_root = tmp_path / "vault"
    (vault_root / "Notes").mkdir(parents=True)
    (vault_root / "Notes" / "A.md").write_text("alpha", encoding="utf-8")
    golden = tmp_path / "golden.yaml"
    golden.write_text("- {id: q1, query: alpha, expect: [Notes/A.md]}\n",
                      encoding="utf-8")
    monkeypatch.setattr(evals_mod, "_make_embedder", KeywordEmbedder)

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(evals_mod, "append_history", boom)
    rc = main(["--vault", str(vault_root), "--golden", str(golden)])
    assert rc == 0
    assert "history" in capsys.readouterr().err.lower()


def test_main_bad_golden_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "vault").mkdir()
    rc = main(["--vault", str(tmp_path / "vault"),
               "--golden", str(tmp_path / "missing.yaml")])
    assert rc == 2
    assert "error:" in capsys.readouterr().err


def test_main_live_without_env_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TESSERACT_VAULT_PATH", raising=False)
    rc = main(["--live"])
    assert rc == 2


def test_main_missing_vault_exits_2(tmp_path, monkeypatch, capsys):
    # Vault.__init__ raises VaultError for a nonexistent root; the CLI
    # must map that to the exit-2 config-error contract, not a traceback.
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    rc = main(["--vault", str(tmp_path / "nope"),
               "--golden", str(tmp_path / "missing.yaml")])
    assert rc == 2
    assert "error:" in capsys.readouterr().err


from tesseract_mcp.evals import init_live


def test_init_live_creates_template_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "vault"
    root.mkdir()
    vault = Vault(root)
    target, created = init_live(vault)
    assert created is True and target.is_file()
    assert load_golden(target)[0].id == "example-constitution"
    marker = "USER EDIT"
    target.write_text(target.read_text(encoding="utf-8") + marker,
                      encoding="utf-8")
    target2, created2 = init_live(vault)
    assert created2 is False
    assert marker in target2.read_text(encoding="utf-8")


def test_main_init_live_uses_env_vault(tmp_path, monkeypatch, capsys):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setenv("TESSERACT_VAULT_PATH", str(root))
    assert main(["--init-live"]) == 0
    assert "created" in capsys.readouterr().out
    assert (root / "Claude" / "Evals.md").is_file()


import os


@pytest.mark.skipif(
    os.environ.get("TESSERACT_RUN_EVALS") != "1",
    reason="set TESSERACT_RUN_EVALS=1 to run the model-backed eval gate",
)
def test_fixture_thresholds_with_real_model(tmp_path, monkeypatch):
    """Floors, not exact ranks: if this fails after a ranking change, the
    change lost real recall. If the baseline sits below a floor, fix the
    fixture or golden set -- never lower the floor."""
    monkeypatch.setenv("TESSERACT_STATE_DIR", str(tmp_path / "state"))
    queries = load_golden(FIXTURE_GOLDEN)
    sc = run_evals(
        Vault(FIXTURE_VAULT), tmp_path / "state",
        evals_mod._make_embedder(), queries,
    )
    assert sc.skipped == 0
    assert sc.success_at[10] >= 0.80
    assert sc.mrr >= 0.50
