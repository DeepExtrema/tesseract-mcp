import os

import pytest

from tesseract_mcp.vault import Vault, VaultError


def test_missing_root_raises(tmp_path):
    with pytest.raises(VaultError, match="does not exist"):
        Vault(tmp_path / "nope")


def test_read_note(vault):
    assert "Remember to check" in vault.read("Daily.md")


def test_read_missing_note_raises(vault):
    with pytest.raises(VaultError, match="not found"):
        vault.read("Ghost.md")


def test_path_escape_rejected(vault):
    with pytest.raises(VaultError, match="escapes"):
        vault.read("../outside.md")


def test_write_inside_claude_allowed(vault):
    vault.write("Claude/Inbox/note.md", "hello")
    assert vault.read("Claude/Inbox/note.md") == "hello"


def test_write_outside_claude_refused_by_default(vault):
    with pytest.raises(VaultError, match="outside Claude/"):
        vault.write("Projects/New.md", "hello")


def test_write_outside_claude_with_confirmation(vault):
    vault.write("Projects/New.md", "hello", confirm_outside_claude=True)
    assert vault.read("Projects/New.md") == "hello"


def test_write_refuses_overwrite_by_default(vault):
    vault.write("Claude/Inbox/note.md", "v1")
    with pytest.raises(VaultError, match="already exists"):
        vault.write("Claude/Inbox/note.md", "v2")


def test_write_overwrite_flag(vault):
    vault.write("Claude/Inbox/note.md", "v1")
    vault.write("Claude/Inbox/note.md", "v2", overwrite=True)
    assert vault.read("Claude/Inbox/note.md") == "v2"


def test_write_creates_parent_dirs(vault):
    vault.write("Claude/Sessions/2026/deep.md", "x")
    assert vault.read("Claude/Sessions/2026/deep.md") == "x"


def test_append_creates_and_appends(vault):
    vault.append("Claude/Inbox/2026-07-05.md", "- one\n")
    vault.append("Claude/Inbox/2026-07-05.md", "- two\n")
    assert vault.read("Claude/Inbox/2026-07-05.md") == "- one\n- two\n"


def test_append_outside_claude_refused_by_default(vault):
    with pytest.raises(VaultError, match="outside Claude/"):
        vault.append("Daily.md", "- sneaky\n")


def test_in_claude(vault):
    assert vault.in_claude("Claude/Index.md")
    assert vault.in_claude("Claude/Sessions/x.md")
    assert not vault.in_claude("Daily.md")
    assert not vault.in_claude("ClaudeFake/x.md")


@pytest.mark.skipif(
    os.path.normcase("A") == "A",
    reason="case-insensitive quarantine semantics only apply on case-insensitive filesystems",
)
def test_in_claude_case_insensitive_on_windows(vault):
    assert vault.in_claude("claude/Sessions/x.md")
    assert vault.in_claude("CLAUDE/Index.md")


def test_write_to_directory_raises_vault_error(vault):
    with pytest.raises(VaultError, match="directory"):
        vault.write("Claude/Sessions", "x", overwrite=True)


def test_write_to_vault_root_raises_vault_error(vault):
    with pytest.raises(VaultError, match="directory"):
        vault.write("", "x", overwrite=True, confirm_outside_claude=True)


def test_append_to_directory_raises_vault_error(vault):
    with pytest.raises(VaultError, match="directory"):
        vault.append("Claude/Inbox", "- x\n")
