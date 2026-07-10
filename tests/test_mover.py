import pytest

from tesseract_mcp import indexer
from tesseract_mcp.mover import duplicate_stem_exists, move_note, reverse_rewrites
from tesseract_mcp.vault import Vault


@pytest.fixture
def mv_vault(tmp_path):
    (tmp_path / "02 - Space").mkdir()
    (tmp_path / "Claude" / "Graph" / "Topics").mkdir(parents=True)
    (tmp_path / "Telemetry.md").write_text(
        "root note about telemetry\n", encoding="utf-8")
    # path-qualified inbound link (root-level note: path == stem)
    (tmp_path / "02 - Space" / "Research.md").write_text(
        "See [[Telemetry]] and [[Telemetry|the telemetry note]] "
        "and [[Telemetry#Details]].\n", encoding="utf-8")
    # prefix-collision neighbor: must NOT be rewritten
    (tmp_path / "Telemetry 2.md").write_text("sequel\n", encoding="utf-8")
    (tmp_path / "02 - Space" / "Mentions2.md").write_text(
        "See [[Telemetry 2]] too.\n", encoding="utf-8")
    # graph entity note with a path-qualified mention
    (tmp_path / "Claude" / "Graph" / "Topics" / "Telemetry Topic.md").write_text(
        "## Mentions\n\n- [[Telemetry|Telemetry]] — evidence\n", encoding="utf-8")
    return Vault(tmp_path)


def test_move_rewrites_qualified_links_everywhere(mv_vault):
    record = move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    assert record["from"] == "Telemetry.md"
    assert record["to"] == "02 - Space/Telemetry.md"
    assert not (mv_vault.root / "Telemetry.md").exists()
    assert (mv_vault.root / "02 - Space" / "Telemetry.md").is_file()
    research = mv_vault.read("02 - Space/Research.md")
    assert "[[02 - Space/Telemetry]]" in research
    assert "[[02 - Space/Telemetry|the telemetry note]]" in research
    assert "[[02 - Space/Telemetry#Details]]" in research
    graph = mv_vault.read("Claude/Graph/Topics/Telemetry Topic.md")
    assert "[[02 - Space/Telemetry|Telemetry]]" in graph
    rewritten = {r["path"] for r in record["rewrites"]}
    assert "02 - Space/Research.md" in rewritten
    assert "Claude/Graph/Topics/Telemetry Topic.md" in rewritten


def test_move_leaves_prefix_collision_alone(mv_vault):
    move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    assert "[[Telemetry 2]]" in mv_vault.read("02 - Space/Mentions2.md")


def test_move_transfers_manifest_key(mv_vault):
    manifest = indexer.load_manifest(mv_vault.root)
    manifest["hashes"]["Telemetry.md"] = "abc123"
    indexer.save_manifest(manifest, mv_vault.root)
    move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    manifest = indexer.load_manifest(mv_vault.root)
    assert "Telemetry.md" not in manifest["hashes"]
    assert manifest["hashes"]["02 - Space/Telemetry.md"] == "abc123"


def test_move_transfers_failure_record(mv_vault):
    """A failing note keeps its retry count when moved — otherwise a note at
    the MAX_ATTEMPTS skip cap gets fresh paid extraction attempts per move."""
    manifest = indexer.load_manifest(mv_vault.root)
    manifest["failures"]["Telemetry.md"] = {"error": "boom", "attempts": 2}
    indexer.save_manifest(manifest, mv_vault.root)
    move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    manifest = indexer.load_manifest(mv_vault.root)
    assert "Telemetry.md" not in manifest["failures"]
    assert manifest["failures"]["02 - Space/Telemetry.md"] == {
        "error": "boom", "attempts": 2}


def test_duplicate_stem_detected(mv_vault):
    (mv_vault.root / "02 - Space" / "Clone.md").write_text("a\n", encoding="utf-8")
    (mv_vault.root / "Clone.md").write_text("b\n", encoding="utf-8")
    assert duplicate_stem_exists(mv_vault, "Clone.md")
    assert not duplicate_stem_exists(mv_vault, "Telemetry.md")  # 'Telemetry 2' is a different stem


def test_reverse_rewrites_restores_links(mv_vault):
    record = move_note(mv_vault, "Telemetry.md", "02 - Space/Telemetry.md")
    reverse_rewrites(
        mv_vault, "Telemetry.md", "02 - Space/Telemetry.md",
        [r["path"] for r in record["rewrites"]],
    )
    assert "[[Telemetry]]" in mv_vault.read("02 - Space/Research.md")
    assert "[[02 - Space/Telemetry]]" not in mv_vault.read("02 - Space/Research.md")
