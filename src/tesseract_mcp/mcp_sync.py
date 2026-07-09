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
