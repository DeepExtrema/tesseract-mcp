"""Pure-function tests for scripts/hooks/session-start.py and stop-nudge.py.

Both scripts are stdlib-only and structured as importable functions behind a
__main__ guard so their logic (project inference, transcript counting) can
be unit-tested without invoking them as a subprocess. Filenames use hyphens
(Claude Code hook convention), so they're loaded via importlib rather than
a normal package import.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "hooks"


def _load(name: str, filename: str):
    path = HOOKS_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def session_start():
    return _load("session_start_hook", "session-start.py")


@pytest.fixture(scope="module")
def stop_nudge():
    return _load("stop_nudge_hook", "stop-nudge.py")


# --- session-start.py: project inference ------------------------------

def test_infer_project_from_windows_path(session_start):
    assert session_start.infer_project(
        r"C:\Users\Taimoor\Documents\GitHub\tesseract-mcp"
    ) == "tesseract-mcp"


def test_infer_project_from_posix_path(session_start):
    assert session_start.infer_project("/home/taimoor/projects/tesseract-mcp") == "tesseract-mcp"


def test_infer_project_strips_trailing_slash(session_start):
    assert session_start.infer_project(r"C:\repo\tesseract-mcp\\") == "tesseract-mcp"


def test_infer_project_empty_cwd_returns_empty(session_start):
    assert session_start.infer_project("") == ""
    assert session_start.infer_project(None) == ""


def test_build_command_includes_vault_and_project(session_start):
    cmd = session_start.build_command("tesseract-mcp", r"C:\Vaults\Tesseract", python="py.exe")
    assert cmd == ["py.exe", "-m", "tesseract_mcp.recall", "--vault",
                   r"C:\Vaults\Tesseract", "--context", "--project", "tesseract-mcp"]


def test_main_prints_nothing_and_exits_zero_on_garbage_stdin(session_start, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
    session_start.main()  # must not raise
    assert capsys.readouterr().out == ""


def test_main_prints_nothing_when_cwd_missing(session_start, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
    session_start.main()
    assert capsys.readouterr().out == ""


# --- stop-nudge.py: transcript counting --------------------------------

def _jsonl(*entries):
    return "\n".join(json.dumps(e) for e in entries)


def _assistant_tool_use(name):
    return {"type": "assistant", "message": {"role": "assistant",
             "content": [{"type": "tool_use", "name": name, "input": {}}]}}


def test_count_tool_uses_counts_assistant_tool_use_blocks(stop_nudge):
    lines = _jsonl(
        _assistant_tool_use("Read"),
        _assistant_tool_use("Edit"),
        {"type": "user", "message": {"role": "user", "content": "hi"}},
    ).splitlines()
    count, saw_log = stop_nudge.count_tool_uses(lines)
    assert count == 2
    assert saw_log is False


def test_count_tool_uses_detects_log_session(stop_nudge):
    lines = _jsonl(
        _assistant_tool_use("Read"),
        _assistant_tool_use("mcp__tesseract__log_session"),
    ).splitlines()
    count, saw_log = stop_nudge.count_tool_uses(lines)
    assert count == 2
    assert saw_log is True


def test_count_tool_uses_ignores_malformed_lines(stop_nudge):
    lines = ["not json", "", _jsonl(_assistant_tool_use("Read"))]
    count, saw_log = stop_nudge.count_tool_uses(lines)
    assert count == 1
    assert saw_log is False


def test_evaluate_nudges_when_over_threshold_without_log(stop_nudge):
    msg = stop_nudge.evaluate(10, False)
    assert msg is not None
    assert "log_session" in msg


def test_evaluate_silent_when_under_threshold(stop_nudge):
    assert stop_nudge.evaluate(9, False) is None


def test_evaluate_silent_when_log_session_present(stop_nudge):
    assert stop_nudge.evaluate(25, True) is None


def test_main_silent_on_missing_transcript_path(stop_nudge, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
    stop_nudge.main()
    assert capsys.readouterr().out == ""


def test_main_silent_on_nonexistent_transcript_file(stop_nudge, monkeypatch, capsys, tmp_path):
    payload = json.dumps({"transcript_path": str(tmp_path / "nope.jsonl")})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    stop_nudge.main()
    assert capsys.readouterr().out == ""


def test_main_prints_nudge_for_significant_session_without_log(stop_nudge, monkeypatch, capsys, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    entries = [_assistant_tool_use("Read") for _ in range(10)]
    transcript.write_text(_jsonl(*entries), encoding="utf-8")
    payload = json.dumps({"transcript_path": str(transcript)})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    stop_nudge.main()
    out = capsys.readouterr().out
    assert "log_session" in out


def test_main_silent_for_trivial_session(stop_nudge, monkeypatch, capsys, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    entries = [_assistant_tool_use("Read") for _ in range(2)]
    transcript.write_text(_jsonl(*entries), encoding="utf-8")
    payload = json.dumps({"transcript_path": str(transcript)})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    stop_nudge.main()
    assert capsys.readouterr().out == ""


def test_main_silent_when_log_session_was_called(stop_nudge, monkeypatch, capsys, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    entries = [_assistant_tool_use("Read") for _ in range(10)]
    entries.append(_assistant_tool_use("mcp__tesseract__log_session"))
    transcript.write_text(_jsonl(*entries), encoding="utf-8")
    payload = json.dumps({"transcript_path": str(transcript)})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    stop_nudge.main()
    assert capsys.readouterr().out == ""
