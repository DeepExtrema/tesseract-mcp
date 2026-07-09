import json
from pathlib import Path

import pytest

from tesseract_mcp.mcp_sync import (
    Classification,
    ConfigParseError,
    MissingVaultError,
    ServerSpec,
    classify,
    load_manifest,
    read_config,
    resolve,
)


def _write_manifest(tmp_path: Path, servers: list[dict]) -> Path:
    p = tmp_path / "mcp-servers.json"
    p.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return p


def test_load_manifest_parses_specs(tmp_path):
    p = _write_manifest(tmp_path, [{
        "name": "fetch", "transport": "stdio", "command": "uvx",
        "args": ["mcp-server-fetch@2026.6.4"],
        "env": {"PYTHONIOENCODING": "utf-8"}, "why": "ingest",
    }])
    specs = load_manifest(p)
    assert len(specs) == 1
    assert specs[0].name == "fetch"
    assert specs[0].args == ["mcp-server-fetch@2026.6.4"]
    assert specs[0].env == {"PYTHONIOENCODING": "utf-8"}


def test_resolve_substitutes_repo_and_vault(tmp_path):
    spec = ServerSpec(
        name="tesseract", transport="stdio",
        command="{REPO}\\.venv\\Scripts\\tesseract-mcp.exe", url=None,
        args=[], env={"TESSERACT_VAULT_PATH": "{VAULT}"}, why="",
    )
    out = resolve(spec, repo_root=Path("C:/repo"), vault="C:/Vaults/T")
    assert "{REPO}" not in out.command and "C:/repo" in out.command.replace("\\", "/")
    assert out.env["TESSERACT_VAULT_PATH"] == "C:/Vaults/T"
    # original untouched
    assert "{VAULT}" in spec.env["TESSERACT_VAULT_PATH"]


def test_resolve_raises_when_vault_needed_but_missing(tmp_path):
    spec = ServerSpec(
        name="tesseract", transport="stdio", command="x", url=None,
        args=[], env={"TESSERACT_VAULT_PATH": "{VAULT}"}, why="",
    )
    with pytest.raises(MissingVaultError):
        resolve(spec, repo_root=Path("C:/repo"), vault=None)


def test_resolve_no_vault_needed_passes_without_vault():
    spec = ServerSpec(
        name="fetch", transport="stdio", command="uvx", url=None,
        args=["mcp-server-fetch@2026.6.4"], env={}, why="",
    )
    out = resolve(spec, repo_root=Path("C:/repo"), vault=None)
    assert out.args == ["mcp-server-fetch@2026.6.4"]


def _spec(name="fetch", command="uvx", args=None, env=None):
    return ServerSpec(name=name, transport="stdio", command=command, url=None,
                      args=args or ["mcp-server-fetch@2026.6.4"], env=env or {}, why="")


def test_read_config_missing_file_is_empty(tmp_path):
    assert read_config(tmp_path / "nope.json") == {}


def test_read_config_invalid_json_raises(tmp_path):
    p = tmp_path / "claude.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigParseError):
        read_config(p)


def test_classify_missing_and_present_and_extra(tmp_path):
    config = {
        "fetch": {"type": "stdio", "command": "uvx",
                  "args": ["mcp-server-fetch@2026.6.4"], "env": {}},
        "somethingelse": {"type": "stdio", "command": "x", "args": [], "env": {}},
    }
    result = classify([_spec("fetch"), _spec("arxiv", args=["arxiv-mcp-server@0.4.12"])], config)
    assert result.present == ["fetch"]
    assert [s.name for s in result.missing] == ["arxiv"]
    assert result.extras == ["somethingelse"]
    assert result.drifted == []


def test_classify_drift_on_args():
    config = {"fetch": {"type": "stdio", "command": "uvx",
                        "args": ["mcp-server-fetch@1.0.0"], "env": {}}}
    result = classify([_spec("fetch")], config)
    assert result.present == []
    assert result.drifted[0][0] == "fetch"
    assert "args" in result.drifted[0][1]


def test_classify_extra_config_env_is_not_drift():
    config = {"fetch": {"type": "stdio", "command": "uvx",
                        "args": ["mcp-server-fetch@2026.6.4"],
                        "env": {"PYTHONIOENCODING": "utf-8", "UNRELATED": "1"}}}
    result = classify([_spec("fetch", env={"PYTHONIOENCODING": "utf-8"})], config)
    assert result.present == ["fetch"]
