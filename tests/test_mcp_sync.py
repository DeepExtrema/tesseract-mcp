import json
from pathlib import Path

import pytest

from tesseract_mcp.mcp_sync import MissingVaultError, ServerSpec, load_manifest, resolve


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
