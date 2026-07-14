"""Tests for the Librarian caretaker loop."""

import io
import json
import sys
from datetime import datetime, timedelta

import pytest

from tesseract_mcp import blocking, librarian
from tesseract_mcp.vault import Vault

NOW = datetime(2026, 7, 9, 12, 0, 0)


class FakeEmbedder:
    """Deterministic stand-in — no model download in tests."""

    def embed_batch(self, texts):
        return [[float(len(t)), 0.0] for t in texts]


@pytest.fixture(autouse=True)
def _no_model_downloads(monkeypatch):
    from tesseract_mcp import embeddings as embeddings_mod

    monkeypatch.setattr(embeddings_mod, "SentenceTransformerEmbedder", FakeEmbedder)


def test_constants_match_spec():
    assert librarian.BACKSTOP_MIN_INTERVAL_DAYS == 14
    assert blocking.SLICE_SIZE == 200
    assert blocking.MAX_ENTITIES_PER_CALL == 40


def test_backstop_due_on_first_pass():
    assert librarian._backstop_due({}, NOW) is True


def test_backstop_not_due_before_interval():
    con = {"backstop_last_advance": NOW.strftime(librarian.TS_FMT)}
    assert librarian._backstop_due(con, NOW + timedelta(days=13)) is False


def test_backstop_due_after_interval():
    con = {"backstop_last_advance": NOW.strftime(librarian.TS_FMT)}
    assert librarian._backstop_due(con, NOW + timedelta(days=14)) is True


def test_load_state_default_when_missing(vault):
    state = librarian.load_state(vault)
    assert state["last_sweep"] is None
    assert state["consolidation"] == {}


def test_state_roundtrip(vault):
    state = librarian.load_state(vault)
    state["last_sweep"] = "2026-07-09 12:00:00"
    librarian.save_state(vault, state)
    assert librarian.load_state(vault)["last_sweep"] == "2026-07-09 12:00:00"


def test_interrupted_save_leaves_previous_state_intact(vault, monkeypatch):
    """A write that dies mid-file must not corrupt librarian_state.json."""
    from pathlib import Path

    librarian.save_state(vault, {"last_sweep": "before"})
    real_write = Path.write_text

    def failing_write(self, content, *args, **kwargs):
        real_write(self, content[: len(content) // 2], *args, **kwargs)
        raise OSError("disk full mid-write")

    monkeypatch.setattr(Path, "write_text", failing_write)
    with pytest.raises(OSError):
        librarian.save_state(vault, {"last_sweep": "after"})
    # NOT monkeypatch.undo(): that would also revert conftest's autouse
    # TESSERACT_STATE_DIR isolation (fixtures share one monkeypatch instance)
    monkeypatch.setattr(Path, "write_text", real_write)
    assert librarian.load_state(vault)["last_sweep"] == "before"


def test_status_survives_corrupt_state_file(vault):
    librarian.state_path(vault).write_text("{not json", encoding="utf-8")
    result = librarian.status(vault)
    assert result["status"] == "state file unreadable"
    assert "error" in result


from tesseract_mcp import cache, indexer


def _entity_note(vault_dir, folder, name, etype, mentions=()):
    p = vault_dir / "Claude" / "Graph" / folder / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"---\nentity: {etype}\n---\n\n# {name}\n\nSummary.\n"]
    if mentions:
        lines.append("\n## Mentions\n")
        for note_path in mentions:
            stem = note_path.rsplit("/", 1)[-1]
            lines.append(f"- [[{note_path}|{stem}]] — evidence\n")
    p.write_text("".join(lines), encoding="utf-8")


def test_manifest_drift_detects_both_directions(vault):
    manifest = indexer.load_manifest(vault.root)
    manifest["hashes"]["Ghost.md"] = "deadbeef"
    indexer.save_manifest(manifest, vault.root)
    drift = librarian.check_manifest_drift(vault)
    assert "Ghost.md" in drift["deleted_but_tracked"]
    assert "Daily.md" in drift["present_but_untracked"]


def test_orphaned_entities_detects_missing_note(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization",
                 mentions=["Projects/Gone"])
    cache.rebuild(vault, indexer.db_path(vault.root))
    orphans = librarian.check_orphaned_entities(vault)
    assert orphans == [{"entity": "Claude/Graph/Organizations/Acme",
                        "missing_note": "Projects/Gone"}]


def test_orphaned_entities_clean_when_note_exists(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization",
                 mentions=["Projects/Sentinel ESG"])
    cache.rebuild(vault, indexer.db_path(vault.root))
    assert librarian.check_orphaned_entities(vault) == []


def test_orphaned_entities_empty_without_db(vault):
    assert librarian.check_orphaned_entities(vault) == []


def test_cache_consistency_flags_mismatch(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    cache.rebuild(vault, indexer.db_path(vault.root))
    assert librarian.check_cache_consistency(vault)["consistent"] is True
    _entity_note(vault_dir, "Topics", "Orbit", "topic")  # note added, no rebuild
    result = librarian.check_cache_consistency(vault)
    assert result == {"db_entities": 1, "md_entities": 2, "consistent": False}


def test_pending_proposals_counts_state_and_report():
    state = {"consolidation": {"pending_proposals": [{"canonical": "A"}]}}
    organize_report = {"proposals": [1, 2]}
    assert librarian.count_pending_proposals(state, organize_report, None) == 3
    ran = {"ran": True, "reason": "first pass", "proposed": [1]}
    assert librarian.count_pending_proposals(state, organize_report, ran) == 3


def test_run_health_survives_check_failure(vault, monkeypatch):
    def boom(v):
        raise RuntimeError("kaput")

    monkeypatch.setattr(librarian, "check_manifest_drift", boom)
    health = librarian.run_health(vault, {}, None, None, {})
    assert health["manifest_drift"] == {"error": "RuntimeError: kaput"}
    assert "orphaned_entities" in health
    assert health["stale_embeddings"] >= 0


from tesseract_mcp.extractor import Extraction


class FakeExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, path, content):
        self.calls.append(path)
        return Extraction(entities=[], relations=[])


class FakeConsolidator:
    def __init__(self, merges=None):
        self.merges = merges or []
        self.calls = 0

    def complete_json(self, prompt):
        self.calls += 1
        return {"merges": self.merges}


def _counts(**over):
    base = {"processed": 0, "entities_created": 0, "entities_merged": 0,
            "mentions_added": 0, "relations_added": 0,
            "mentions_retracted": 0, "failed": 0, "skipped": 0, "remaining": 0}
    base.update(over)
    return base


def _org_report(**over):
    base = {"moved": [], "proposals": [], "skipped": [], "cache_rebuilt": False}
    base.update(over)
    return base


def test_pipeline_runs_index_before_organize(vault, monkeypatch):
    calls = []
    monkeypatch.setattr(librarian.indexer, "run",
                        lambda v, e, **k: (calls.append("index"), _counts())[1])
    monkeypatch.setattr(librarian.organizer_mod, "run_sweep",
                        lambda v, emb, apply: (calls.append("organize"),
                                               _org_report())[1])
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    assert calls == ["index", "organize"]


def test_drain_index_loops_until_no_remaining(vault, monkeypatch):
    seq = [_counts(processed=25, remaining=5), _counts(processed=5)]
    monkeypatch.setattr(librarian.indexer, "run", lambda v, e, **k: seq.pop(0))
    totals = librarian._drain_index(vault, FakeExtractor())
    assert totals["processed"] == 30
    assert totals["remaining"] == 0


def test_drain_index_raises_when_rounds_exhausted(vault, monkeypatch):
    """Exhausting MAX_INDEX_ROUNDS with work pending is a step failure, not a
    silently-partial success — the CLI must exit non-zero."""
    monkeypatch.setattr(librarian.indexer, "run",
                        lambda v, e, **k: _counts(processed=1, remaining=5))
    with pytest.raises(RuntimeError, match="did not drain"):
        librarian._drain_index(vault, FakeExtractor())


def test_step_failure_is_isolated(vault, monkeypatch):
    def boom(v, emb, apply):
        raise RuntimeError("organize kaput")

    monkeypatch.setattr(librarian.organizer_mod, "run_sweep", boom)
    result = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                 consolidator=FakeConsolidator(),
                                 embedder=FakeEmbedder(), now=NOW)
    assert result["errors"]["organize"] == "RuntimeError: organize kaput"
    assert result["steps"]["organize"] is None
    assert result["steps"]["consolidate"] is not None
    assert result["health"]["sweep_errors"]["organize"]


def test_consolidation_first_pass_records_cursor_and_checked(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    _entity_note(vault_dir, "Organizations", "Acme Corp", "organization")
    fake = FakeConsolidator(merges=[{"type": "organization", "canonical": "Acme",
                                     "duplicates": ["Acme Corp"]}])
    result = librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                                 embedder=FakeEmbedder(), now=NOW)
    step = result["steps"]["consolidate"]
    assert step["ran"] and step["proposed"] == [
        {"type": "organization", "canonical": "Acme", "duplicates": ["Acme Corp"]}]
    con = librarian.load_state(vault)["consolidation"]
    assert set(con["checked_hash"]) == {
        "Claude/Graph/Organizations/Acme",
        "Claude/Graph/Organizations/Acme Corp"}
    assert con["pending_proposals"] == step["proposed"]


def test_second_sweep_skips_when_checked_and_backstop_not_due(vault, vault_dir):
    # two same-type entities so the first sweep actually clusters and calls the
    # consolidator (a lone entity can never form a cluster)
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    _entity_note(vault_dir, "Organizations", "Acme Corp", "organization")
    fake = FakeConsolidator()
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW)
    assert fake.calls == 1  # first sweep adjudicated the unchecked cluster
    # force the backstop clock recent so it is NOT due, then re-sweep unchanged
    state = librarian.load_state(vault)
    state["consolidation"]["backstop_last_advance"] = NOW.strftime(librarian.TS_FMT)
    librarian.save_state(vault, state)
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW + timedelta(days=1))
    assert fake.calls == 1  # all checked + backstop not due -> no new adjudication


def test_changed_entity_reenters_slice(vault, vault_dir):
    _entity_note(vault_dir, "Organizations", "Acme", "organization")
    _entity_note(vault_dir, "Organizations", "Acme Corp", "organization")  # stable partner
    fake = FakeConsolidator()
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW)
    assert fake.calls == 1
    # turn the backstop OFF so re-adjudication can ONLY come from a checked_hash mismatch
    state = librarian.load_state(vault)
    state["consolidation"]["backstop_last_advance"] = NOW.strftime(librarian.TS_FMT)
    librarian.save_state(vault, state)
    # a no-change sweep makes no new call (eager path is quiet when nothing changed)
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW + timedelta(minutes=1))
    assert fake.calls == 1
    # edit Acme's body: identity changes -> unchecked -> eager slice -> re-adjudicated
    note = vault_dir / "Claude" / "Graph" / "Organizations" / "Acme.md"
    note.write_text(note.read_text(encoding="utf-8").replace("Summary.", "New summary."),
                    encoding="utf-8")
    librarian.run_sweep(vault, extractor=FakeExtractor(), consolidator=fake,
                        embedder=FakeEmbedder(), now=NOW + timedelta(minutes=2))
    assert fake.calls == 2  # only the changed identity triggered a fresh call


def test_apply_sweep_saves_state(vault):
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    state = librarian.load_state(vault)
    assert state["last_sweep"] == NOW.strftime(librarian.TS_FMT)
    assert "index" in state["steps"]
    assert "stale_embeddings" in state["health"]


def test_dry_run_touches_nothing(vault, vault_dir):
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    snapshot = {p: p.read_bytes()
                for p in sorted(vault_dir.rglob("*")) if p.is_file()}
    state_before = librarian.load_state(vault)

    result = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                 consolidator=FakeConsolidator(),
                                 embedder=FakeEmbedder(), apply=False, now=NOW)
    assert result["applied"] is False
    assert result["steps"]["index"] == {"pending": 0}
    after = {p: p.read_bytes()
             for p in sorted(vault_dir.rglob("*")) if p.is_file()}
    assert after == snapshot
    assert librarian.load_state(vault) == state_before


def test_consecutive_apply_sweeps_keep_zero_pending(vault):
    """Each sweep appends to Librarian.md; the scan exclusion keeps the log
    from ever counting as pending index work."""
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    assert librarian._index_preview(vault) == {"pending": 0}


def test_apply_sweep_keeps_caretaker_notes_out_of_manifest(vault, vault_dir):
    """The indexer never scans the caretaker logs, so no sweep may write
    their hashes to the manifest (raw file: load_manifest prunes on load)."""
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    raw = json.loads((indexer.state_dir(vault.root) / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert librarian.LIBRARIAN_NOTE not in raw["hashes"]
    # the fixture sweep deterministically moves Daily.md, so the move log exists
    assert (vault_dir / "Claude" / "Organizer.md").is_file()
    assert "Claude/Organizer.md" not in raw["hashes"]


def test_first_apply_sweep_health_reports_no_drift(vault):
    """Health runs mid-sweep; the just-created Organizer.md is excluded from
    scans, or the human-reviewed first sweep shows a false manifest_drift ⚠."""
    result = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                 consolidator=FakeConsolidator(),
                                 embedder=FakeEmbedder(), now=NOW)
    assert result["health"]["manifest_drift"] == {
        "deleted_but_tracked": [], "present_but_untracked": []}


def test_out_of_sweep_undo_move_never_reaches_extractor(vault):
    """undo_move appends to Claude/Organizer.md outside any sweep; the next
    sweep must not feed the move log to the paid extractor."""
    first = librarian.run_sweep(vault, extractor=FakeExtractor(),
                                consolidator=FakeConsolidator(),
                                embedder=FakeEmbedder(), now=NOW)
    move = first["steps"]["organize"]["moved"][0]
    moved_rel = f"{move['to_folder']}/{move['from'].rsplit('/', 1)[-1]}"
    librarian.organizer_mod.undo_move(vault, moved_rel)

    fx = FakeExtractor()
    librarian.run_sweep(vault, extractor=fx,
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    assert "Claude/Organizer.md" not in fx.calls


def test_format_report_covers_all_steps():
    result = {
        "applied": True,
        "steps": {
            "index": _counts(processed=3, failed=1),
            "organize": _org_report(moved=[{"from": "A.md"}],
                                    proposals=[1, 2], skipped=[1]),
            "cache": {"rebuilt": True, "by": "index"},
            "consolidate": {"ran": False, "reason": "2 new entities since last pass; threshold 15", "proposed": []},
        },
        "health": {
            "stale_embeddings": 0,
            "manifest_drift": {"deleted_but_tracked": [], "present_but_untracked": []},
            "orphaned_entities": [{"entity": "E", "missing_note": "N"}],
            "cache_consistency": {"db_entities": 1, "md_entities": 1, "consistent": True},
            "pending_proposals": 2,
            "sweep_errors": {},
        },
        "errors": {},
    }
    text = librarian.format_report(result, NOW)
    assert text.startswith("## Sweep 2026-07-09 12:00\n")
    assert "- index: processed 3, failed 1, remaining 0\n" in text
    assert "- organize: moved 1, proposals 2, skipped 1\n" in text
    assert "- cache: rebuilt (index)\n" in text
    assert "- consolidate: skipped (2 new entities since last pass; threshold 15)\n" in text
    assert "orphaned_entities 1 ⚠" in text
    assert "stale_embeddings 0 ✓" in text
    assert "- errors: none\n" in text


def test_format_report_failed_step_and_errors():
    result = {"applied": True,
              "steps": {"index": None, "organize": _org_report(),
                        "cache": {"rebuilt": False, "by": "none"},
                        "consolidate": {"ran": True, "reason": "first pass",
                                        "proposed": [1]}},
              "health": {"stale_embeddings": 0, "manifest_drift": {},
                         "orphaned_entities": [], "cache_consistency":
                         {"consistent": True}, "pending_proposals": 1,
                         "sweep_errors": {"index": "RuntimeError: x"}},
              "errors": {"index": "RuntimeError: x"}}
    text = librarian.format_report(result, NOW)
    assert "- index: FAILED\n" in text
    assert "- consolidate: ran (first pass) — 1 merge proposals\n" in text
    assert "- errors: index: RuntimeError: x\n" in text


def test_summarize_steps_includes_skipped_batches():
    steps = {"consolidate": {"ran": True, "reason": "3 unchecked",
                             "proposed": [], "skipped_batches": 2}}
    out = librarian._summarize_steps(steps)
    assert out["consolidate"]["skipped_batches"] == 2


def test_write_report_seeds_and_appends(vault):
    librarian.write_report(vault, "## Sweep 2026-07-09 12:00\n- x\n")
    text = vault.read(librarian.LIBRARIAN_NOTE)
    assert text.startswith("# Librarian")
    assert "## Sweep 2026-07-09 12:00" in text


def test_report_trims_to_max_sweeps(vault):
    for i in range(33):
        librarian.write_report(vault, f"## Sweep 2026-07-09 12:{i:02d}\n- x\n")
    text = vault.read(librarian.LIBRARIAN_NOTE)
    assert text.count("## Sweep") == librarian.REPORT_MAX_SWEEPS
    assert "12:02" not in text
    assert "12:03" in text
    assert "12:32" in text


def test_apply_sweep_writes_report(vault):
    librarian.run_sweep(vault, extractor=FakeExtractor(),
                        consolidator=FakeConsolidator(),
                        embedder=FakeEmbedder(), now=NOW)
    text = vault.read(librarian.LIBRARIAN_NOTE)
    assert "## Sweep 2026-07-09 12:00" in text


def test_cli_dry_run_prints_and_exits_zero(vault_dir, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["librarian", str(vault_dir), "--dry-run"])
    librarian.main()
    out = capsys.readouterr().out
    assert "## Sweep" in out
    assert '"applied": false' in out


def test_cli_exits_nonzero_on_step_failure(vault_dir, monkeypatch):
    def boom(v, emb, apply):
        raise RuntimeError("kaput")

    monkeypatch.setattr(librarian.organizer_mod, "run_sweep", boom)
    monkeypatch.setattr(sys, "argv", ["librarian", str(vault_dir), "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        librarian.main()
    assert exc.value.code == 1


def test_cli_dry_run_survives_cp1252_console(vault_dir, monkeypatch):
    """Windows consoles/piped-to-file stdout default to cp1252, which cannot
    encode the checkmark/warning glyphs in format_report's health line.
    main() must reconfigure stdout/stderr to utf-8 before printing, or the
    CLI (and scheduled sweeps that redirect stdout to a log file) crashes
    with UnicodeEncodeError even though the sweep itself succeeded."""
    monkeypatch.setattr(sys, "argv", ["librarian", str(vault_dir), "--dry-run"])
    out_buf = io.BytesIO()
    err_buf = io.BytesIO()
    fake_stdout = io.TextIOWrapper(out_buf, encoding="cp1252", errors="strict")
    fake_stderr = io.TextIOWrapper(err_buf, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(sys, "stderr", fake_stderr)

    librarian.main()

    fake_stdout.flush()
    out = out_buf.getvalue().decode("cp1252")
    assert "## Sweep" in out
    assert "health:" in out


def test_invalid_sheet_rows_health(vault_dir):
    folder = vault_dir / "Records"
    folder.mkdir()
    (folder / "_schema.md").write_text(
        "---\nsheet: things\nfilename: \"{name}\"\nkey: [name]\n"
        "columns:\n  name: {type: string, required: true}\n---\n",
        encoding="utf-8")
    (folder / "Bad.md").write_text("---\nextra: x\n---\n", encoding="utf-8")
    vault = Vault(vault_dir)
    assert librarian.count_invalid_sheet_rows(vault) == 1


def test_invalid_sheet_rows_zero_without_sheets(vault):
    assert librarian.count_invalid_sheet_rows(vault) == 0
