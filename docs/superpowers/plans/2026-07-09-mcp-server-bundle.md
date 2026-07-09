# MCP Server Bundle (manifest + additive sync) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pinned `mcp-servers.json` manifest of the user's MCP stack plus `python -m tesseract_mcp.mcp_sync`, which additively registers missing servers into Claude Code's user-scope config and reports drift without ever touching existing entries.

**Architecture:** One new module `src/tesseract_mcp/mcp_sync.py` (manifest loading, placeholder resolution, config classification, `claude mcp add` shell-out) + one data file `mcp-servers.json` at repo root. Same idioms as `provision.py`: pure functions, `--check` mode, absent-only writes.

**Tech Stack:** Python 3.11 stdlib only (json, pathlib, subprocess, argparse, shutil). pytest with tmp_path fixtures + monkeypatched subprocess.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-09-mcp-server-bundle-design.md` — follow exactly.
- **Additive only:** the tool NEVER removes and NEVER modifies an existing `mcpServers` entry in `~/.claude.json`. Drift and extras are report-only, with exact remediation commands printed.
- Manifest v1 servers: `tesseract` (this clone), `fetch` (`mcp-server-fetch`, PyPI pin `2026.6.4`, env `PYTHONIOENCODING=utf-8`), `arxiv` (`arxiv-mcp-server`, exact PyPI pin verified at implementation time — Task 4 Step 1).
- Placeholders: `{REPO}` = repo root (resolved from the installed package location, `Path(__file__).resolve().parents[2]`); `{VAULT}` = `--vault` flag or `TESSERACT_VAULT_PATH` env. Sync aborts with a clear message if a manifest entry needs `{VAULT}` and neither source is set.
- Unparseable `~/.claude.json` → abort before any subprocess call, exit 2.
- `claude` CLI missing from PATH → print every `claude mcp add` command that WOULD run, exit 3, register nothing.
- `--check` exits 1 if anything is missing or drifted, 0 if clean.
- Windows paths in the manifest use `\\` in JSON strings; the code must treat command paths as opaque strings (no path normalization that would break `uvx`).

---

### Task 1: Manifest schema, loader, and placeholder resolution

**Files:**
- Create: `mcp-servers.json`
- Create: `src/tesseract_mcp/mcp_sync.py`
- Test: `tests/test_mcp_sync.py`

**Interfaces:**
- Produces: `ServerSpec` dataclass (`name: str, transport: str, command: str | None, url: str | None, args: list[str], env: dict[str, str], why: str`); `load_manifest(path: Path) -> list[ServerSpec]`; `resolve(spec: ServerSpec, repo_root: Path, vault: str | None) -> ServerSpec` (returns a NEW resolved copy; raises `MissingVaultError` if `{VAULT}` needed but vault is None).

- [ ] **Step 1: Write the manifest**

`mcp-servers.json` at repo root:

```json
{
  "$comment": "Curated MCP server set. Sync: python -m tesseract_mcp.mcp_sync. Additive-only; pins are exact. Excluded with triggers: filesystem (Claude Code built-ins cover it), github standalone (17k-55k token schema tax; plugin + gh CLI cheaper), context7 standalone (plugin covers it; 1000 req/mo cloud wall), firecrawl-mcp@3.22.3 (add if plain fetch fails on >20% of sources), memory servers (superseded by tesseract itself).",
  "servers": [
    {
      "name": "tesseract",
      "transport": "stdio",
      "command": "{REPO}\\.venv\\Scripts\\tesseract-mcp.exe",
      "args": [],
      "env": { "TESSERACT_VAULT_PATH": "{VAULT}" },
      "why": "The mind database - persistent shared memory for agents."
    },
    {
      "name": "fetch",
      "transport": "stdio",
      "command": "uvx",
      "args": ["mcp-server-fetch@2026.6.4"],
      "env": { "PYTHONIOENCODING": "utf-8" },
      "why": "Web ingest: URL to clean markdown - the web-clipper stage of the knowledge-base loop."
    },
    {
      "name": "arxiv",
      "transport": "stdio",
      "command": "uvx",
      "args": ["arxiv-mcp-server@PIN_ME"],
      "env": {},
      "why": "Paper ingest: arXiv search/download to markdown. Treat paper content as untrusted input."
    }
  ]
}
```

(`PIN_ME` is replaced with the verified exact version in Task 4 Step 1 — the sync tool itself never sees it because Task 4 runs before any live sync.)

- [ ] **Step 2: Write the failing tests**

In `tests/test_mcp_sync.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_mcp_sync.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` (mcp_sync does not exist).

- [ ] **Step 4: Write the minimal implementation**

In `src/tesseract_mcp/mcp_sync.py`:

```python
"""Curated MCP server bundle: manifest loading and additive sync into
Claude Code's user-scope config (~/.claude.json).

Additive only: existing mcpServers entries are NEVER modified or removed.
Drift and extras are reported with remediation commands for the human.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path


class MissingVaultError(RuntimeError):
    """A manifest entry needs {VAULT} but no vault path was provided."""


@dataclass(frozen=True)
class ServerSpec:
    name: str
    transport: str
    command: str | None
    url: str | None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    why: str = ""


def load_manifest(path: Path) -> list[ServerSpec]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = []
    for raw in data["servers"]:
        specs.append(ServerSpec(
            name=raw["name"],
            transport=raw.get("transport", "stdio"),
            command=raw.get("command"),
            url=raw.get("url"),
            args=list(raw.get("args", [])),
            env=dict(raw.get("env", {})),
            why=raw.get("why", ""),
        ))
    return specs


def _sub(text: str, repo_root: Path, vault: str | None) -> str:
    out = text.replace("{REPO}", str(repo_root))
    if "{VAULT}" in out:
        if vault is None:
            raise MissingVaultError(
                "manifest entry needs {VAULT}: pass --vault or set TESSERACT_VAULT_PATH"
            )
        out = out.replace("{VAULT}", vault)
    return out


def resolve(spec: ServerSpec, repo_root: Path, vault: str | None) -> ServerSpec:
    return replace(
        spec,
        command=_sub(spec.command, repo_root, vault) if spec.command else None,
        url=_sub(spec.url, repo_root, vault) if spec.url else None,
        args=[_sub(a, repo_root, vault) for a in spec.args],
        env={k: _sub(v, repo_root, vault) for k, v in spec.env.items()},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_mcp_sync.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```powershell
git add mcp-servers.json src/tesseract_mcp/mcp_sync.py tests/test_mcp_sync.py
git commit -m "feat(mcp-sync): manifest schema, loader, placeholder resolution"
```

---

### Task 2: Config reading and classification (present / drifted / missing / extras)

**Files:**
- Modify: `src/tesseract_mcp/mcp_sync.py`
- Test: `tests/test_mcp_sync.py`

**Interfaces:**
- Consumes: `ServerSpec`, `resolve` from Task 1.
- Produces: `read_config(config_path: Path) -> dict[str, dict]` (returns the `mcpServers` mapping, `{}` if file or key absent; raises `ConfigParseError` on invalid JSON); `Classification` dataclass (`present: list[str], drifted: list[tuple[str, str]], missing: list[ServerSpec], extras: list[str]` — drifted tuples are `(name, human-readable difference)`); `classify(resolved_specs: list[ServerSpec], config_servers: dict[str, dict]) -> Classification`.
- Matching rule: an entry is **present** iff config `command` == spec.command (or `url` for http), config `args` == spec.args, and every key/value in spec.env appears in config env (config may have EXTRA env keys — those do not count as drift).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_sync.py`:

```python
from tesseract_mcp.mcp_sync import Classification, ConfigParseError, classify, read_config


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
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv\Scripts\python -m pytest tests/test_mcp_sync.py -v`
Expected: the 5 new tests FAIL with ImportError (`Classification` etc. undefined); the 4 Task-1 tests still pass.

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/mcp_sync.py`:

```python
class ConfigParseError(RuntimeError):
    """~/.claude.json exists but is not valid JSON — abort, zero writes."""


@dataclass
class Classification:
    present: list[str] = field(default_factory=list)
    drifted: list[tuple[str, str]] = field(default_factory=list)
    missing: list[ServerSpec] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)


def read_config(config_path: Path) -> dict[str, dict]:
    p = Path(config_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigParseError(f"cannot parse {p}: {e}") from e
    return data.get("mcpServers", {})


def _diff(spec: ServerSpec, entry: dict) -> str | None:
    if spec.url:
        if entry.get("url") != spec.url:
            return f"url: config={entry.get('url')!r} manifest={spec.url!r}"
        return None
    if entry.get("command") != spec.command:
        return f"command: config={entry.get('command')!r} manifest={spec.command!r}"
    if list(entry.get("args", [])) != spec.args:
        return f"args: config={entry.get('args')!r} manifest={spec.args!r}"
    entry_env = entry.get("env", {})
    for k, v in spec.env.items():
        if entry_env.get(k) != v:
            return f"env[{k}]: config={entry_env.get(k)!r} manifest={v!r}"
    return None


def classify(resolved_specs: list[ServerSpec], config_servers: dict[str, dict]) -> Classification:
    result = Classification()
    manifest_names = {s.name for s in resolved_specs}
    for spec in resolved_specs:
        if spec.name not in config_servers:
            result.missing.append(spec)
        else:
            diff = _diff(spec, config_servers[spec.name])
            if diff is None:
                result.present.append(spec.name)
            else:
                result.drifted.append((spec.name, diff))
    result.extras = sorted(set(config_servers) - manifest_names)
    return result
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `.venv\Scripts\python -m pytest tests/test_mcp_sync.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/mcp_sync.py tests/test_mcp_sync.py
git commit -m "feat(mcp-sync): config reading and present/drifted/missing/extras classification"
```

---

### Task 3: Registration commands, sync orchestration, additive-only invariant

**Files:**
- Modify: `src/tesseract_mcp/mcp_sync.py`
- Test: `tests/test_mcp_sync.py`

**Interfaces:**
- Consumes: everything from Tasks 1–2.
- Produces: `build_add_command(spec: ServerSpec) -> list[str]` (the exact `claude mcp add` argv); `run_sync(manifest_path: Path, config_path: Path, repo_root: Path, vault: str | None, check_only: bool, runner=subprocess.run) -> int` (returns process exit code; prints the report). `runner` is injectable for tests.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_sync.py`:

```python
from tesseract_mcp.mcp_sync import build_add_command, run_sync


def test_build_add_command_stdio_with_env():
    spec = _spec("fetch", env={"PYTHONIOENCODING": "utf-8"})
    cmd = build_add_command(spec)
    assert cmd == ["claude", "mcp", "add", "--scope", "user", "fetch",
                   "-e", "PYTHONIOENCODING=utf-8", "--",
                   "uvx", "mcp-server-fetch@2026.6.4"]


def test_run_sync_registers_only_missing(tmp_path, capsys):
    manifest = _write_manifest(tmp_path, [
        {"name": "fetch", "transport": "stdio", "command": "uvx",
         "args": ["mcp-server-fetch@2026.6.4"], "env": {}, "why": ""},
    ])
    config = tmp_path / "claude.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    calls = []

    def fake_runner(argv, **kw):
        calls.append(argv)
        class R: returncode = 0
        return R()

    code = run_sync(manifest, config, tmp_path, None, check_only=False, runner=fake_runner)
    assert code == 0
    assert len(calls) == 1 and calls[0][:3] == ["claude", "mcp", "add"]


def test_run_sync_never_touches_existing_entries(tmp_path):
    """Additive-only invariant: pre-existing config is byte-identical after sync."""
    manifest = _write_manifest(tmp_path, [
        {"name": "fetch", "transport": "stdio", "command": "uvx",
         "args": ["DIFFERENT@9.9.9"], "env": {}, "why": ""},
    ])
    config = tmp_path / "claude.json"
    original = json.dumps({"mcpServers": {"fetch": {
        "type": "stdio", "command": "uvx",
        "args": ["mcp-server-fetch@2026.6.4"], "env": {}}}})
    config.write_text(original, encoding="utf-8")
    calls = []

    def fake_runner(argv, **kw):
        calls.append(argv)
        class R: returncode = 0
        return R()

    run_sync(manifest, config, tmp_path, None, check_only=False, runner=fake_runner)
    assert calls == []                       # drifted -> reported, never re-registered
    assert config.read_text(encoding="utf-8") == original


def test_run_sync_check_mode_exit_codes(tmp_path):
    manifest = _write_manifest(tmp_path, [
        {"name": "fetch", "transport": "stdio", "command": "uvx",
         "args": ["mcp-server-fetch@2026.6.4"], "env": {}, "why": ""},
    ])
    config = tmp_path / "claude.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    assert run_sync(manifest, config, tmp_path, None, check_only=True) == 1
    config.write_text(json.dumps({"mcpServers": {"fetch": {
        "type": "stdio", "command": "uvx",
        "args": ["mcp-server-fetch@2026.6.4"], "env": {}}}}), encoding="utf-8")
    assert run_sync(manifest, config, tmp_path, None, check_only=True) == 0


def test_run_sync_unparseable_config_aborts_before_subprocess(tmp_path):
    manifest = _write_manifest(tmp_path, [
        {"name": "fetch", "transport": "stdio", "command": "uvx",
         "args": ["mcp-server-fetch@2026.6.4"], "env": {}, "why": ""},
    ])
    config = tmp_path / "claude.json"
    config.write_text("{broken", encoding="utf-8")
    calls = []

    def fake_runner(argv, **kw):
        calls.append(argv)

    code = run_sync(manifest, config, tmp_path, None, check_only=False, runner=fake_runner)
    assert code == 2 and calls == []


def test_run_sync_claude_missing_prints_commands_exit_3(tmp_path, capsys):
    manifest = _write_manifest(tmp_path, [
        {"name": "fetch", "transport": "stdio", "command": "uvx",
         "args": ["mcp-server-fetch@2026.6.4"], "env": {}, "why": ""},
    ])
    config = tmp_path / "claude.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    def fake_runner(argv, **kw):
        raise FileNotFoundError("claude not found")

    code = run_sync(manifest, config, tmp_path, None, check_only=False, runner=fake_runner)
    out = capsys.readouterr().out
    assert code == 3
    assert "claude mcp add" in out          # printed for manual use
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv\Scripts\python -m pytest tests/test_mcp_sync.py -v`
Expected: 6 new FAIL (ImportError), 9 old pass.

- [ ] **Step 3: Implement**

Append to `src/tesseract_mcp/mcp_sync.py`:

```python
import shlex
import subprocess


def build_add_command(spec: ServerSpec) -> list[str]:
    cmd = ["claude", "mcp", "add", "--scope", "user"]
    if spec.url:
        return cmd + ["--transport", "http", spec.name, spec.url]
    cmd.append(spec.name)
    for k, v in spec.env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd.append("--")
    cmd.append(spec.command)
    cmd += spec.args
    return cmd


def _remediation(name: str) -> str:
    return (f"  to fix drift manually: claude mcp remove --scope user {name} "
            f"&& re-run this sync")


def run_sync(manifest_path: Path, config_path: Path, repo_root: Path,
             vault: str | None, check_only: bool,
             runner=subprocess.run) -> int:
    try:
        config_servers = read_config(config_path)
    except ConfigParseError as e:
        print(f"ABORT (nothing changed): {e}")
        return 2
    try:
        specs = [resolve(s, repo_root, vault) for s in load_manifest(manifest_path)]
    except MissingVaultError as e:
        print(f"ABORT (nothing changed): {e}")
        return 2

    result = classify(specs, config_servers)
    for name in result.present:
        print(f"present : {name}")
    for name, diff in result.drifted:
        print(f"DRIFTED : {name} — {diff}\n{_remediation(name)}")
    for name in result.extras:
        print(f"extra   : {name} (not in manifest; left alone)")
    for spec in result.missing:
        print(f"MISSING : {spec.name} — {spec.why}")

    if check_only:
        return 1 if (result.missing or result.drifted) else 0

    failures = 0
    for spec in result.missing:
        argv = build_add_command(spec)
        print(f"register: {' '.join(shlex.quote(a) for a in argv)}")
        try:
            proc = runner(argv, check=False)
        except FileNotFoundError:
            print("claude CLI not found on PATH. Run the printed command(s) "
                  "manually, or install Claude Code. Nothing was registered.")
            return 3
        if getattr(proc, "returncode", 1) != 0:
            print(f"  FAILED (exit {proc.returncode}) — continuing with the rest")
            failures += 1
    return 1 if failures else 0
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `.venv\Scripts\python -m pytest tests/test_mcp_sync.py -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```powershell
git add src/tesseract_mcp/mcp_sync.py tests/test_mcp_sync.py
git commit -m "feat(mcp-sync): additive-only sync with drift report and injectable runner"
```

---

### Task 4: CLI entry point, real pins, live run, docs

**Files:**
- Create: `src/tesseract_mcp/mcp_sync_cli.py` is NOT created — add `main()` + `__main__` guard to `src/tesseract_mcp/mcp_sync.py` (matches how `provision.py`/`organize.py` do it)
- Modify: `mcp-servers.json` (replace `PIN_ME`)
- Modify: `README.md` (quickstart), `docs/ARCHITECTURE.md` (module map row)
- Test: `tests/test_mcp_sync.py`

**Interfaces:**
- Consumes: `run_sync` from Task 3.
- Produces: `python -m tesseract_mcp.mcp_sync [--check] [--vault <path>] [--manifest <path>] [--config <path>]` (last two default to repo `mcp-servers.json` and `~/.claude.json`; exposed mainly for tests/ops).

- [ ] **Step 1: Verify and set the real arxiv pin**

Run: `.venv\Scripts\pip index versions arxiv-mcp-server` (or `pip install arxiv-mcp-server==` to list). Take the newest exact version, replace `PIN_ME` in `mcp-servers.json` (format `arxiv-mcp-server@<version>`). Then verify both pinned packages actually launch:

```powershell
uvx mcp-server-fetch@2026.6.4 --help      # expect: usage text, exit 0
uvx arxiv-mcp-server@<version> --help     # expect: usage text (note any required args like --storage-path; if required, add them to the manifest args now)
```

- [ ] **Step 2: Write the failing CLI test**

```python
def test_cli_check_against_fixture(tmp_path, monkeypatch, capsys):
    from tesseract_mcp import mcp_sync
    manifest = _write_manifest(tmp_path, [
        {"name": "fetch", "transport": "stdio", "command": "uvx",
         "args": ["mcp-server-fetch@2026.6.4"], "env": {}, "why": ""},
    ])
    config = tmp_path / "claude.json"
    config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    code = mcp_sync.main(["--check", "--manifest", str(manifest), "--config", str(config)])
    assert code == 1
    assert "MISSING : fetch" in capsys.readouterr().out
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_mcp_sync.py::test_cli_check_against_fixture -v`
Expected: FAIL, `main` not defined.

- [ ] **Step 4: Implement main()**

Append to `src/tesseract_mcp/mcp_sync.py`:

```python
import argparse
import os


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Additively sync the curated MCP server set into Claude Code (user scope).")
    parser.add_argument("--check", action="store_true",
                        help="report only; exit 1 if missing/drifted")
    parser.add_argument("--vault", default=os.environ.get("TESSERACT_VAULT_PATH"),
                        help="vault path for {VAULT} (default: TESSERACT_VAULT_PATH env)")
    parser.add_argument("--manifest", default=str(repo_root / "mcp-servers.json"))
    parser.add_argument("--config", default=str(Path.home() / ".claude.json"))
    args = parser.parse_args(argv)
    return run_sync(Path(args.manifest), Path(args.config), repo_root,
                    args.vault, check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python -m pytest tests/test_mcp_sync.py -v`
Expected: 16 passed. Then run the whole repo suite: `.venv\Scripts\python -m pytest -q` — expected: no regressions.

- [ ] **Step 6: Live run on this machine**

```powershell
.venv\Scripts\python -m tesseract_mcp.mcp_sync --check     # expect: tesseract present, fetch+arxiv MISSING, exit 1
.venv\Scripts\python -m tesseract_mcp.mcp_sync             # registers fetch + arxiv
claude mcp list                                            # expect: tesseract, fetch, arxiv
.venv\Scripts\python -m tesseract_mcp.mcp_sync --check     # expect: all present, exit 0
```

Note: the existing `tesseract` entry may classify as DRIFTED rather than present (it was registered by hand). That is correct additive behavior — do NOT "fix" it by modifying config; the report's remediation command is the documented path.

- [ ] **Step 7: Update docs**

In `README.md`: replace the manual `claude mcp add` block in Quickstart with:

```powershell
# Register the curated MCP server set (tesseract + web/paper ingest)
$env:TESSERACT_VAULT_PATH = "<path-to-vault>"
.venv\Scripts\python -m tesseract_mcp.mcp_sync
```

Keep one sentence noting the manifest lives in `mcp-servers.json` and sync is additive-only. In `docs/ARCHITECTURE.md` module map add: `mcp_sync.py | Curated MCP server manifest sync — additive registration into Claude Code user scope`.

- [ ] **Step 8: Commit**

```powershell
git add src/tesseract_mcp/mcp_sync.py tests/test_mcp_sync.py mcp-servers.json README.md docs/ARCHITECTURE.md
git commit -m "feat(mcp-sync): CLI entry, real pins, README/ARCHITECTURE integration"
```
