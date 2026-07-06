import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from install_conventions import install


def test_installs_structure(tmp_path):
    created = install(tmp_path)
    assert (tmp_path / "Claude" / "README.md").is_file()
    assert (tmp_path / "Claude" / "Inbox").is_dir()
    assert (tmp_path / "Claude" / "Sessions").is_dir()
    assert (tmp_path / "Claude" / "Concepts").is_dir()
    assert (tmp_path / "Claude" / "Index.md").is_file()
    assert (tmp_path / "Claude" / "Decisions.md").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    assert "Constitution" in (tmp_path / "Claude" / "README.md").read_text(
        encoding="utf-8"
    )
    assert len(created) == 8


def test_root_guides_identical_and_routed(tmp_path):
    install(tmp_path)
    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    agents_md = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert claude_md == agents_md
    assert "Routing rules" in claude_md
    assert "Claude/README" in claude_md  # points at the constitution
    assert "search_brain" in claude_md   # tool guidance present


def test_decisions_seed_is_append_only_log(tmp_path):
    install(tmp_path)
    body = (tmp_path / "Claude" / "Decisions.md").read_text(encoding="utf-8")
    assert body.startswith("---\n")       # frontmatter
    assert "# Decisions" in body
    assert "append" in body.lower()


def test_idempotent_does_not_clobber(tmp_path):
    install(tmp_path)
    index = tmp_path / "Claude" / "Index.md"
    index.write_text("# Index\n\n- [[existing]]\n", encoding="utf-8")
    created = install(tmp_path)
    assert "existing" in index.read_text(encoding="utf-8")
    assert created == []
