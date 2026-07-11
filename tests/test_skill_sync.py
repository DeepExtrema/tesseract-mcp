"""skill_sync: additive by default, --force to update, --check writes nothing."""

import pytest

from tesseract_mcp import skill_sync


def _make_skill(base, name, body="---\nname: x\ndescription: d\n---\nbody\n"):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def test_installs_missing_skill(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall")
    result = skill_sync.sync(src=src, dest=dest)
    assert result["installed"] == ["recall"]
    assert (dest / "recall" / "SKILL.md").is_file()


def test_never_touches_existing_without_force(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall", "new upstream content\n")
    _make_skill(dest, "recall", "user edited this\n")
    result = skill_sync.sync(src=src, dest=dest)
    assert result["drift"] == ["recall"]
    text = (dest / "recall" / "SKILL.md").read_text(encoding="utf-8")
    assert text == "user edited this\n"


def test_force_overwrites_drifted_skill(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall", "new upstream content\n")
    _make_skill(dest, "recall", "user edited this\n")
    result = skill_sync.sync(src=src, dest=dest, force=True)
    assert result["updated"] == ["recall"]
    text = (dest / "recall" / "SKILL.md").read_text(encoding="utf-8")
    assert text == "new upstream content\n"


def test_identical_skill_reports_up_to_date(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall", "same\n")
    _make_skill(dest, "recall", "same\n")
    result = skill_sync.sync(src=src, dest=dest)
    assert result["up_to_date"] == ["recall"]


def test_check_reports_without_writing(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall")
    result = skill_sync.sync(src=src, dest=dest, check=True)
    assert result["installed"] == ["recall"]
    assert not (dest / "recall").exists()


def test_check_plus_force_still_writes_nothing(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_skill(src, "recall", "new\n")
    _make_skill(dest, "recall", "old\n")
    result = skill_sync.sync(src=src, dest=dest, force=True, check=True)
    assert result["drift"] == ["recall"]
    assert (dest / "recall" / "SKILL.md").read_text(encoding="utf-8") == "old\n"


def test_ignores_dirs_without_skill_md(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    (src / "not-a-skill").mkdir(parents=True)
    result = skill_sync.sync(src=src, dest=dest)
    assert result == {"installed": [], "updated": [], "up_to_date": [], "drift": []}


def test_cli_fails_fast_when_repo_skills_missing(tmp_path, monkeypatch):
    # wheel installs don't package skills/ — the CLI must not report an
    # empty success (same fail-fast philosophy as mcp_sync's manifest check)
    monkeypatch.setattr(skill_sync, "REPO_SKILLS", tmp_path / "missing")
    monkeypatch.setattr("sys.argv", ["skill_sync", "--check"])
    with pytest.raises(SystemExit) as exc:
        skill_sync.main()
    assert exc.value.code == 2  # argparse parser.error
