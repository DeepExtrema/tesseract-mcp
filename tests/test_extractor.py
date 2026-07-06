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
    ex = CliExtractor(backend="codex", runner=runner).extract("Note.md", "content")
    assert ex.entities and runner.calls[0][:2] == ["codex", "exec"]


def test_extract_claude_backend_command():
    runner = make_runner([FakeProc(stdout=json.dumps(GOOD))])
    CliExtractor(backend="claude", runner=runner).extract("Note.md", "content")
    assert runner.calls[0][:2] == ["claude", "-p"]


def test_extract_parses_json_with_surrounding_prose():
    out = "Sure! Here is the JSON:\n" + json.dumps(GOOD) + "\nHope that helps."
    runner = make_runner([FakeProc(stdout=out)])
    ex = CliExtractor(backend="codex", runner=runner).extract("N.md", "c")
    assert len(ex.entities) == 2


def test_extract_retries_once_then_succeeds():
    runner = make_runner([FakeProc(stdout="not json at all"), FakeProc(stdout=json.dumps(GOOD))])
    ex = CliExtractor(backend="codex", runner=runner).extract("N.md", "c")
    assert len(runner.calls) == 2 and ex.entities


def test_extract_fails_after_second_bad_reply():
    runner = make_runner([FakeProc(stdout="junk"), FakeProc(stdout="more junk")])
    with pytest.raises(ExtractorError):
        CliExtractor(backend="codex", runner=runner).extract("N.md", "c")


def test_nonzero_exit_raises():
    runner = make_runner([FakeProc(stdout="", returncode=1, stderr="boom")])
    with pytest.raises(ExtractorError, match="boom"):
        CliExtractor(backend="codex", runner=runner).extract("N.md", "c")


def test_unknown_backend_rejected():
    with pytest.raises(ExtractorError, match="Unknown backend"):
        CliExtractor(backend="gpt9000")


def test_backend_from_env(monkeypatch):
    monkeypatch.setenv("TESSERACT_EXTRACTOR", "claude")
    assert CliExtractor().backend == "claude"


def test_vocabularies():
    assert "organization" in ENTITY_TYPES and "related_to" in RELATIONS
