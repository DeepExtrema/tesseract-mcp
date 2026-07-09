"""Curated MCP server bundle: manifest loading and additive sync into
Claude Code's user-scope config (~/.claude.json).

Additive only: existing mcpServers entries are NEVER modified or removed.
Drift and extras are reported with remediation commands for the human.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
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
