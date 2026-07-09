import json

import pytest

from tesseract_mcp.organizer import (
    VOTE_K,
    VOTE_THRESHOLD,
    Classification,
    classify,
    discover_taxonomy,
    iter_candidates,
    iter_organized,
)
from tesseract_mcp.vault import Vault


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
