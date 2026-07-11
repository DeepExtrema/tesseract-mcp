"""Lint the repo's Claude Code skills: frontmatter present and well-formed."""

from pathlib import Path

import yaml

SKILLS = Path(__file__).resolve().parent.parent / "skills"
EXPECTED = {"recall"}


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} missing frontmatter"
    end = text.index("\n---", 4)
    return yaml.safe_load(text[4:end])


def test_expected_skills_exist():
    found = {p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()}
    assert found == EXPECTED


def test_frontmatter_names_match_dirs_and_descriptions_are_real():
    for name in sorted(EXPECTED):
        meta = _frontmatter(SKILLS / name / "SKILL.md")
        assert meta["name"] == name
        assert len(meta["description"]) > 40, f"{name}: description too thin"
