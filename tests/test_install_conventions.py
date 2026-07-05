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
    assert "Constitution" in (tmp_path / "Claude" / "README.md").read_text(
        encoding="utf-8"
    )
    assert len(created) == 5


def test_idempotent_does_not_clobber(tmp_path):
    install(tmp_path)
    index = tmp_path / "Claude" / "Index.md"
    index.write_text("# Index\n\n- [[existing]]\n", encoding="utf-8")
    created = install(tmp_path)
    assert "existing" in index.read_text(encoding="utf-8")
    assert created == []
