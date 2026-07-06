import json

import pytest

from tesseract_mcp.extractor import (
    ENTITY_TYPES,
    RELATIONS,
    CliExtractor,
    Extraction,
    ExtractorError,
    _coerce,
)

GOOD = {
    "entities": [
        {"name": "Acme Corp", "type": "organization", "aliases": ["ACME"], "summary": "A company."},
        {"name": "Supply Chain", "type": "domain", "aliases": [], "summary": "Logistics domain."},
    ],
    "relations": [
        {"from": "Acme Corp", "from_type": "organization", "rel": "operates_in",
         "to": "Supply Chain", "to_type": "domain", "evidence": "Acme runs logistics."},
    ],
}


class FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def make_runner(outputs):
    """Returns a runner that pops canned FakeProcs; records invocations."""
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return outputs.pop(0)

    runner.calls = calls
    return runner


def test_coerce_valid_passthrough():
    ex = _coerce(GOOD)
    assert isinstance(ex, Extraction)
    assert ex.entities[0]["name"] == "Acme Corp"
    assert ex.relations[0]["rel"] == "operates_in"


def test_coerce_unknown_types_fold_to_topic_and_related_to():
    raw = {
        "entities": [{"name": "X", "type": "spaceship", "aliases": None, "summary": ""}],
        "relations": [{"from": "X", "from_type": "spaceship", "rel": "zaps",
                       "to": "Y", "to_type": "alien", "evidence": ""}],
    }
    ex = _coerce(raw)
    assert ex.entities[0]["type"] == "topic"
    assert ex.relations[0]["rel"] == "related_to"
    assert ex.relations[0]["from_type"] == "topic" and ex.relations[0]["to_type"] == "topic"


def test_coerce_drops_nameless():
    ex = _coerce({"entities": [{"name": " ", "type": "person"}], "relations": [{"from": "", "to": "Y"}]})
    assert ex.entities == [] and ex.relations == []


def test_extract_happy_path_uses_backend_command():
    runner = make_runner([FakeProc(stdout=json.dumps(GOOD))])
    ex = CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("Note.md", "content")
    assert ex.entities and runner.calls[0][:2] == ["codex", "exec"]


def test_extract_claude_backend_command():
    runner = make_runner([FakeProc(stdout=json.dumps(GOOD))])
    CliExtractor(backend="claude", runner=runner, which=lambda n: n).extract("Note.md", "content")
    assert runner.calls[0][:2] == ["claude", "-p"]


def test_extract_parses_json_with_surrounding_prose():
    out = "Sure! Here is the JSON:\n" + json.dumps(GOOD) + "\nHope that helps."
    runner = make_runner([FakeProc(stdout=out)])
    ex = CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")
    assert len(ex.entities) == 2


def test_extract_retries_once_then_succeeds():
    runner = make_runner([FakeProc(stdout="not json at all"), FakeProc(stdout=json.dumps(GOOD))])
    ex = CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")
    assert len(runner.calls) == 2 and ex.entities


def test_extract_fails_after_second_bad_reply():
    runner = make_runner([FakeProc(stdout="junk"), FakeProc(stdout="more junk")])
    with pytest.raises(ExtractorError):
        CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")


def test_nonzero_exit_raises():
    runner = make_runner([FakeProc(stdout="", returncode=1, stderr="boom")])
    with pytest.raises(ExtractorError, match="boom"):
        CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")


def test_unknown_backend_rejected():
    with pytest.raises(ExtractorError, match="Unknown backend"):
        CliExtractor(backend="gpt9000", which=lambda n: n)


def test_backend_from_env(monkeypatch):
    monkeypatch.setenv("TESSERACT_EXTRACTOR", "claude")
    assert CliExtractor(which=lambda n: n).backend == "claude"


def test_vocabularies():
    assert "organization" in ENTITY_TYPES and "related_to" in RELATIONS


def test_extract_passes_prompt_via_stdin_not_argv():
    captured = {}

    def runner(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return FakeProc(stdout=json.dumps(GOOD))

    CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "SECRET-CONTENT-XYZ")
    assert "SECRET-CONTENT-XYZ" in (captured["input"] or "")
    assert not any("SECRET-CONTENT-XYZ" in str(part) for part in captured["cmd"])


def test_large_content_does_not_crash():
    # 100KB content would exceed the Windows argv limit if passed as an arg
    big = "x" * 100_000

    def runner(cmd, **kwargs):
        assert kwargs.get("input") and len(kwargs["input"]) > 100_000
        return FakeProc(stdout=json.dumps(GOOD))

    ex = CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", big)
    assert ex.entities


def test_timeout_wrapped_as_extractor_error():
    import subprocess as sp

    def runner(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd, kwargs.get("timeout"))

    with pytest.raises(ExtractorError, match="timed out"):
        CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")


def test_missing_binary_wrapped_as_extractor_error():
    def runner(cmd, **kwargs):
        raise FileNotFoundError("codex not found")

    with pytest.raises(ExtractorError, match="failed to run"):
        CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")


def test_parse_handles_two_json_objects():
    out = '{"example": 1}\n' + json.dumps(GOOD)

    def runner(cmd, **kwargs):
        return FakeProc(stdout=out)

    # first decodable object wins; ensure no crash and we get a dict-shaped Extraction
    ex = CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")
    # GOOD is the second object; first is {"example":1} which coerces to empty Extraction.
    # Either way it must not raise. Assert it returned an Extraction.
    assert hasattr(ex, "entities")


def test_parse_prefers_fenced_json_block():
    out = "Here is context with a brace { not json.\n```json\n" + json.dumps(GOOD) + "\n```\ntrailing {oops"

    def runner(cmd, **kwargs):
        return FakeProc(stdout=out)

    ex = CliExtractor(backend="codex", runner=runner, which=lambda n: n).extract("N.md", "c")
    assert len(ex.entities) == 2


def test_resolve_wraps_windows_cmd_shim():
    runner = make_runner([FakeProc(stdout=json.dumps(GOOD))])
    ex = CliExtractor(
        backend="codex", runner=runner,
        which=lambda n: r"C:\npm\codex.cmd",
    )
    ex.extract("N.md", "c")
    assert runner.calls[0][:3] == ["cmd", "/c", r"C:\npm\codex.cmd"]
    assert runner.calls[0][3] == "exec"


def test_resolve_wraps_powershell_shim():
    runner = make_runner([FakeProc(stdout=json.dumps(GOOD))])
    ex = CliExtractor(
        backend="codex", runner=runner,
        which=lambda n: r"C:\npm\codex.ps1",
    )
    ex.extract("N.md", "c")
    assert runner.calls[0] == [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", r"C:\npm\codex.ps1", "exec",
    ]


def test_resolve_native_exe_runs_directly():
    runner = make_runner([FakeProc(stdout=json.dumps(GOOD))])
    ex = CliExtractor(
        backend="claude", runner=runner,
        which=lambda n: r"C:\bin\claude.exe",
    )
    ex.extract("N.md", "c")
    assert runner.calls[0] == [r"C:\bin\claude.exe", "-p"]


def test_resolve_missing_binary_raises():
    ex = CliExtractor(backend="codex", which=lambda n: None)
    with pytest.raises(ExtractorError, match="not found on PATH"):
        ex.extract("N.md", "c")
