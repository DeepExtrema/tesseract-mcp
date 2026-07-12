"""hook_sync: additive merge of SessionStart/Stop hooks into settings.json.

Mirrors tests/test_skill_sync.py's fixture patterns: an existing (drifted)
hook entry is never clobbered; --check reports without writing; a second
install is idempotent. All tests inject the settings path via the
CLAUDE_SETTINGS_PATH env var / explicit path — the real ~/.claude/settings.json
is never touched.
"""

import json
from pathlib import Path

import pytest

from tesseract_mcp import hook_sync


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_expected_command_is_absolute_and_uses_repo_venv_python(tmp_path):
    repo_root = tmp_path / "repo"
    cmd = hook_sync.expected_command("SessionStart", repo_root=repo_root)
    assert str(repo_root) in cmd
    assert "session-start.py" in cmd
    assert ".venv" in cmd


def test_classify_reports_missing_when_settings_absent(tmp_path):
    settings_path = tmp_path / "settings.json"
    result = hook_sync.sync(settings_path, check=True)
    assert set(result["missing"]) == {"SessionStart", "Stop"}
    assert result["present"] == []
    assert result["drift"] == []
    assert not settings_path.exists()  # --check never writes


def test_install_adds_entries_without_clobbering_existing_hooks(tmp_path):
    settings_path = tmp_path / "settings.json"
    original = {
        "model": "sonnet",
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "some-other-tool.exe"}]}
            ],
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "lint.sh"}]}
            ],
        },
    }
    settings_path.write_text(json.dumps(original, indent=2), encoding="utf-8")

    result = hook_sync.sync(settings_path, check=False)

    assert set(result["missing"]) == {"SessionStart", "Stop"}
    data = _read(settings_path)
    # unrelated top-level key untouched
    assert data["model"] == "sonnet"
    # unrelated PreToolUse hook untouched
    assert data["hooks"]["PreToolUse"] == original["hooks"]["PreToolUse"]
    # unrelated SessionStart entry preserved, ours appended alongside it
    session_start_entries = data["hooks"]["SessionStart"]
    assert len(session_start_entries) == 2
    assert session_start_entries[0] == original["hooks"]["SessionStart"][0]
    ours = session_start_entries[1]["hooks"][0]["command"]
    assert "session-start.py" in ours
    # Stop hook created fresh
    stop_entries = data["hooks"]["Stop"]
    assert len(stop_entries) == 1
    assert "stop-nudge.py" in stop_entries[0]["hooks"][0]["command"]


def test_check_never_writes_file(tmp_path):
    settings_path = tmp_path / "settings.json"
    hook_sync.sync(settings_path, check=True)
    assert not settings_path.exists()


def test_check_reports_drift_when_our_entry_differs(tmp_path):
    settings_path = tmp_path / "settings.json"
    original = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command",
                            "command": "C:\\old\\python.exe C:\\old\\scripts\\hooks\\session-start.py"}]}
            ],
        }
    }
    settings_path.write_text(json.dumps(original), encoding="utf-8")

    result = hook_sync.sync(settings_path, check=True)
    assert "SessionStart" in result["drift"]
    assert "Stop" in result["missing"]

    # --install must not touch (overwrite or duplicate) the drifted entry
    hook_sync.sync(settings_path, check=False)
    data = _read(settings_path)
    assert len(data["hooks"]["SessionStart"]) == 1
    assert data["hooks"]["SessionStart"][0] == original["hooks"]["SessionStart"][0]
    # but the genuinely-missing Stop hook still gets installed
    assert len(data["hooks"]["Stop"]) == 1


def test_classify_reports_present_when_ours_is_not_first_hook_in_entry(tmp_path):
    """_find_ours may match an entry whose 'hooks' array has ours at index 1;
    classify() must compare *that* hook's command, not unconditionally index 0."""
    settings_path = tmp_path / "settings.json"
    expected = hook_sync.expected_command("SessionStart")
    original = {
        "hooks": {
            "SessionStart": [
                {"hooks": [
                    {"type": "command", "command": "other-tool"},
                    {"type": "command", "command": expected},
                ]}
            ],
        }
    }
    settings_path.write_text(json.dumps(original), encoding="utf-8")

    result = hook_sync.sync(settings_path, check=True)
    assert "SessionStart" in result["present"]
    assert "SessionStart" not in result["drift"]


def test_second_install_is_idempotent(tmp_path):
    settings_path = tmp_path / "settings.json"
    hook_sync.sync(settings_path, check=False)
    first = settings_path.read_text(encoding="utf-8")
    result = hook_sync.sync(settings_path, check=False)
    second = settings_path.read_text(encoding="utf-8")

    assert first == second
    assert result["missing"] == []
    assert set(result["present"]) == {"SessionStart", "Stop"}
    data = _read(settings_path)
    assert len(data["hooks"]["SessionStart"]) == 1
    assert len(data["hooks"]["Stop"]) == 1


def test_default_settings_path_honors_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-settings.json"
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(override))
    assert hook_sync.default_settings_path() == override


def test_default_settings_path_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("CLAUDE_SETTINGS_PATH", raising=False)
    assert hook_sync.default_settings_path() == Path.home() / ".claude" / "settings.json"


def test_cli_check_exit_code_reflects_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr("sys.argv", ["hook_sync", "--check"])
    with pytest.raises(SystemExit) as exc:
        hook_sync.main()
    assert exc.value.code == 1


def test_cli_install_writes_to_env_overridden_path(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(settings_path))
    monkeypatch.setattr("sys.argv", ["hook_sync", "--install"])
    hook_sync.main()  # must not raise
    data = _read(settings_path)
    assert "SessionStart" in data["hooks"]
    assert "Stop" in data["hooks"]


def test_cli_requires_check_or_install(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr("sys.argv", ["hook_sync"])
    with pytest.raises(SystemExit) as exc:
        hook_sync.main()
    assert exc.value.code == 2  # argparse parser.error
