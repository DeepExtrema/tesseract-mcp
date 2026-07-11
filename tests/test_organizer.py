import json

import pytest

from tesseract_mcp import indexer
from tesseract_mcp.mover import move_note
from tesseract_mcp.organizer import (
    ORGANIZER_NOTE,
    VOTE_K,
    VOTE_THRESHOLD,
    Classification,
    classify,
    discover_taxonomy,
    iter_candidates,
    iter_organized,
    journal_path,
    record_move,
    run_sweep,
    undo_move,
)
from tesseract_mcp.vault import Vault, VaultError


@pytest.fixture
def org_vault(tmp_path):
    """A vault with two topical folders, excluded dirs, and loose notes."""
    for d in (".obsidian", ".smart-env", ".trash", "00 - Maps of Content",
              "Claude/Inbox", "02 - Space", "05 - Cooking"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "02 - Space" / "NASA JPL.md").write_text(
        "space telemetry anomaly research\n", encoding="utf-8")
    (tmp_path / "02 - Space" / "SmallSat.md").write_text(
        "space conference smallsat\n", encoding="utf-8")
    (tmp_path / "02 - Space" / "Telemanom.md").write_text(
        "space lstm telemetry\n", encoding="utf-8")
    (tmp_path / "05 - Cooking" / "Sourdough.md").write_text(
        "recipe starter flour\n", encoding="utf-8")
    (tmp_path / "05 - Cooking" / "Ramen.md").write_text(
        "recipe broth noodles\n", encoding="utf-8")
    (tmp_path / "00 - Maps of Content" / "Home.md").write_text(
        "moc\n", encoding="utf-8")
    (tmp_path / "Claude" / "Inbox" / "capture.md").write_text(
        "agent capture\n", encoding="utf-8")
    (tmp_path / "Loose Space Note.md").write_text(
        "space orbital telemetry note\n", encoding="utf-8")
    (tmp_path / "Pinned.md").write_text(
        "---\norganize: false\n---\n\nspace note that must stay put\n",
        encoding="utf-8")
    return Vault(tmp_path)


def test_constants_match_spec():
    assert VOTE_K == 10
    assert VOTE_THRESHOLD == 0.7


def test_discover_taxonomy_excludes_hard_exclusions(org_vault):
    assert discover_taxonomy(org_vault) == ["02 - Space", "05 - Cooking"]


def test_discover_taxonomy_picks_up_new_human_folder(org_vault):
    (org_vault.root / "07 - Finance").mkdir()
    assert "07 - Finance" in discover_taxonomy(org_vault)


def test_iter_organized_lists_taxonomy_notes_only(org_vault):
    organized = iter_organized(org_vault)
    assert "02 - Space/NASA JPL.md" in organized
    assert "05 - Cooking/Ramen.md" in organized
    assert not any(p.startswith("Claude/") for p in organized)
    assert not any(p.startswith("00 - Maps of Content") for p in organized)


def test_iter_candidates_root_and_organized_minus_pinned(org_vault):
    candidates = iter_candidates(org_vault)
    assert "Loose Space Note.md" in candidates
    assert "02 - Space/NASA JPL.md" in candidates       # filed notes are re-checkable
    assert "Pinned.md" not in candidates                # organize: false
    assert "Claude/Inbox/capture.md" not in candidates  # excluded dir


SPACE = [1.0, 0.0]
COOK = [0.0, 1.0]
MIXED = [0.7, 0.7]

LABELED_VECS = {
    "02 - Space/NASA JPL.md": SPACE,
    "02 - Space/SmallSat.md": SPACE,
    "02 - Space/Telemanom.md": SPACE,
    "05 - Cooking/Sourdough.md": COOK,
    "05 - Cooking/Ramen.md": COOK,
}
LABELED = list(LABELED_VECS)


def test_classify_clear_majority():
    vectors = {**LABELED_VECS, "Loose Space Note.md": [0.9, 0.1]}
    got = classify("Loose Space Note.md", vectors, LABELED)
    assert got.folder == "02 - Space"
    assert got.share >= 0.7
    assert "02 - Space/NASA JPL.md" in got.neighbors


def test_classify_split_vote_low_share():
    vectors = {**LABELED_VECS, "Ambiguous.md": MIXED}
    got = classify("Ambiguous.md", vectors, LABELED)
    assert got.share < 0.7


def test_classify_candidate_never_votes_for_itself():
    vectors = {**LABELED_VECS, "02 - Space/NASA JPL.md": SPACE}
    got = classify("02 - Space/NASA JPL.md", vectors, LABELED)
    assert "02 - Space/NASA JPL.md" not in got.neighbors


def test_classify_no_vector_or_no_labeled_returns_none():
    got = classify("Unknown.md", LABELED_VECS, LABELED)  # no vector for it
    assert got.folder is None and got.share == 0.0
    got2 = classify("X.md", {"X.md": SPACE}, [])          # nothing labeled
    assert got2.folder is None


@pytest.fixture
def moved(org_vault):
    record = move_note(org_vault, "Loose Space Note.md", "02 - Space/Loose Space Note.md")
    record_move(org_vault, record, share=0.85,
                neighbors=["02 - Space/NASA JPL.md"])
    return record


def test_record_move_writes_jsonl_and_note(org_vault, moved):
    lines = journal_path(org_vault).read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[-1])
    assert entry["from"] == "Loose Space Note.md"
    assert entry["to"] == "02 - Space/Loose Space Note.md"
    assert entry["share"] == 0.85
    note = org_vault.read(ORGANIZER_NOTE)
    assert "Loose Space Note" in note and "0.85" in note


def test_undo_restores_location_and_journal(org_vault, moved):
    result = undo_move(org_vault, "02 - Space/Loose Space Note.md")
    assert result["restored"] == "Loose Space Note.md"
    assert (org_vault.root / "Loose Space Note.md").is_file()
    assert not (org_vault.root / "02 - Space" / "Loose Space Note.md").exists()


def test_undo_twice_raises(org_vault, moved):
    undo_move(org_vault, "02 - Space/Loose Space Note.md")
    with pytest.raises(VaultError, match="No undoable move"):
        undo_move(org_vault, "02 - Space/Loose Space Note.md")


def test_undo_unknown_path_raises(org_vault):
    with pytest.raises(VaultError, match="No undoable move"):
        undo_move(org_vault, "02 - Space/Never Moved.md")


def test_undo_transfers_failure_record_back(org_vault, moved):
    """Undo restores a failing note's retry count to its original path."""
    manifest = indexer.load_manifest(org_vault.root)
    manifest["failures"]["02 - Space/Loose Space Note.md"] = {
        "error": "boom", "attempts": 2}
    indexer.save_manifest(manifest, org_vault.root)
    undo_move(org_vault, "02 - Space/Loose Space Note.md")
    manifest = indexer.load_manifest(org_vault.root)
    assert "02 - Space/Loose Space Note.md" not in manifest["failures"]
    assert manifest["failures"]["Loose Space Note.md"] == {
        "error": "boom", "attempts": 2}


class ClusterEmbedder:
    """space→[1,0], recipe→[0,1], both/neither→[0.7,0.7]. Deterministic."""

    def embed_batch(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            has_space, has_recipe = "space" in low, "recipe" in low
            if has_space and not has_recipe:
                out.append([1.0, 0.0])
            elif has_recipe and not has_space:
                out.append([0.0, 1.0])
            else:
                out.append([0.7, 0.7])
        return out


def test_sweep_dry_run_reports_but_touches_nothing(org_vault):
    report = run_sweep(org_vault, ClusterEmbedder(), apply=False)
    moves = {m["from"]: m["to_folder"] for m in report["moved"]}
    assert moves.get("Loose Space Note.md") == "02 - Space"
    assert (org_vault.root / "Loose Space Note.md").is_file()  # not actually moved
    assert not journal_path(org_vault).exists()


def test_sweep_apply_moves_and_journals(org_vault):
    report = run_sweep(org_vault, ClusterEmbedder(), apply=True)
    assert any(m["from"] == "Loose Space Note.md" for m in report["moved"])
    assert (org_vault.root / "02 - Space" / "Loose Space Note.md").is_file()
    assert not (org_vault.root / "Loose Space Note.md").exists()
    assert journal_path(org_vault).exists()
    assert report["cache_rebuilt"] is True


def test_sweep_correctly_filed_note_skipped(org_vault):
    report = run_sweep(org_vault, ClusterEmbedder(), apply=False)
    moved_from = [m["from"] for m in report["moved"]]
    assert "02 - Space/NASA JPL.md" not in moved_from  # already in the right place


def test_sweep_ambiguous_note_becomes_proposal(org_vault):
    (org_vault.root / "Fusion Cuisine In Space.md").write_text(
        "space station recipe experiments\n", encoding="utf-8")  # mixed → [0.7, 0.7]
    report = run_sweep(org_vault, ClusterEmbedder(), apply=True)
    props = [p["path"] for p in report["proposals"]]
    assert "Fusion Cuisine In Space.md" in props
    assert (org_vault.root / "Fusion Cuisine In Space.md").is_file()  # not moved
    assert "Proposals" in org_vault.read(ORGANIZER_NOTE)


def test_sweep_duplicate_stem_becomes_proposal(org_vault):
    (org_vault.root / "05 - Cooking" / "Loose Space Note.md").write_text(
        "recipe named confusingly\n", encoding="utf-8")
    report = run_sweep(org_vault, ClusterEmbedder(), apply=True)
    props = {p["path"]: p for p in report["proposals"]}
    assert "Loose Space Note.md" in props
    assert "duplicate" in props["Loose Space Note.md"]["reason"]
    assert (org_vault.root / "Loose Space Note.md").is_file()  # not moved


def test_root_agent_guides_are_never_candidates(org_vault):
    (org_vault.root / "CLAUDE.md").write_text(
        "space vault guide for agents\n", encoding="utf-8")
    (org_vault.root / "AGENTS.md").write_text(
        "space agent instructions\n", encoding="utf-8")
    candidates = iter_candidates(org_vault)
    assert "CLAUDE.md" not in candidates
    assert "AGENTS.md" not in candidates
    # and a full sweep never proposes or moves them either
    report = run_sweep(org_vault, ClusterEmbedder(), apply=True)
    touched = [m["from"] for m in report["moved"]] + [p["path"] for p in report["proposals"]]
    assert "CLAUDE.md" not in touched and "AGENTS.md" not in touched
    assert (org_vault.root / "CLAUDE.md").is_file()


def test_dot_directories_never_taxonomy(org_vault):
    (org_vault.root / ".claude" / "commands").mkdir(parents=True)
    (org_vault.root / ".claude" / "commands" / "day.md").write_text(
        "space daily note command\n", encoding="utf-8")
    assert not any(f.startswith(".") for f in discover_taxonomy(org_vault))
    assert ".claude/commands/day.md" not in iter_candidates(org_vault)
    report = run_sweep(org_vault, ClusterEmbedder(), apply=True)
    touched = [m["from"] for m in report["moved"]] + [p["path"] for p in report["proposals"]]
    assert not any(t.startswith(".claude/") for t in touched)
    assert (org_vault.root / ".claude" / "commands" / "day.md").is_file()


def test_organizer_skips_sheet_folders(org_vault, tmp_path):
    folder = tmp_path / "Records"
    folder.mkdir()
    (folder / "_schema.md").write_text(
        "---\nsheet: things\nfilename: \"{name}\"\nkey: [name]\n"
        "columns:\n  name: {type: string}\n---\n", encoding="utf-8")
    (folder / "Row.md").write_text("---\nname: Row\n---\n", encoding="utf-8")
    vault = Vault(tmp_path)
    assert all("Records/" not in c for c in iter_candidates(vault))
    assert "Records" not in discover_taxonomy(vault)
