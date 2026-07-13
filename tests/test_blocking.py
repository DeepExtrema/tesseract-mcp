from tesseract_mcp import blocking


def test_identity_text_combines_name_aliases_summary():
    e = {"name": "Oracle VM", "type": "organization",
         "aliases": ["OVM"], "summary": "Cloud VM.", "path": "x"}
    assert blocking.identity_text(e) == "Oracle VM\nOVM\nCloud VM."


def test_identity_hash_changes_with_summary():
    a = {"name": "N", "aliases": [], "summary": "one", "path": "p"}
    b = {"name": "N", "aliases": [], "summary": "two", "path": "p"}
    assert blocking.identity_hash(a) != blocking.identity_hash(b)


def test_identity_hash_stable_for_same_identity():
    a = {"name": "N", "aliases": ["x"], "summary": "s", "path": "p"}
    b = {"name": "N", "aliases": ["x"], "summary": "s", "path": "OTHER"}
    assert blocking.identity_hash(a) == blocking.identity_hash(b)  # path is NOT identity
