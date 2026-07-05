from tesseract_mcp.search import search


def test_finds_content_match(vault):
    hits = search(vault, "ingestion pipeline")
    assert [h.path for h in hits] == ["Projects/Sentinel ESG.md"]
    assert "ingestion" in hits[0].excerpt


def test_case_insensitive(vault):
    assert search(vault, "INGESTION PIPELINE")


def test_title_match(vault):
    hits = search(vault, "couchdb")
    assert "Claude/Concepts/CouchDB.md" in [h.path for h in hits]


def test_tag_filter(vault):
    hits = search(vault, "e", tags=["esg"])
    assert [h.path for h in hits] == ["Projects/Sentinel ESG.md"]


def test_folder_filter(vault):
    hits = search(vault, "couchdb", folder="Claude")
    assert all(h.path.startswith("Claude/") for h in hits)


def test_skips_obsidian_dir(vault):
    assert not search(vault, "{}")


def test_no_match_returns_empty(vault):
    assert search(vault, "zebra unicorn") == []


def test_limit(vault):
    assert len(search(vault, "e", limit=1)) == 1
