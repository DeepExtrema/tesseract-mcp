import pytest

from tesseract_mcp.vault import Vault


@pytest.fixture
def vault_dir(tmp_path):
    """A miniature Obsidian vault with human notes and a Claude/ subtree."""
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")

    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Sentinel ESG.md").write_text(
        "---\ntags: [project, esg]\n---\n\n# Sentinel ESG\n\n"
        "ESG incident ingestion pipeline with CouchDB-free architecture.\n",
        encoding="utf-8",
    )
    (tmp_path / "Daily.md").write_text(
        "# Daily\n\nRemember to check the pipeline.\n", encoding="utf-8"
    )

    claude = tmp_path / "Claude"
    (claude / "Sessions").mkdir(parents=True)
    (claude / "Inbox").mkdir()
    (claude / "Concepts").mkdir()
    (claude / "Index.md").write_text("# Index\n\n", encoding="utf-8")
    (claude / "Concepts" / "CouchDB.md").write_text(
        "---\ntags: [concept]\n---\n\n# CouchDB\n\nDocument database used for LiveSync.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def vault(vault_dir):
    return Vault(vault_dir)
