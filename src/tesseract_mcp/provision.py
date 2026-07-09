"""Provision a fresh Obsidian vault as a Tesseract mind database.

Downloads the curated community-plugin set (pinned in
vault-template/plugins.json) from each plugin's GitHub release, enables
them, applies opinionated settings where the vault has none, and installs
the Claude/ conventions tree. Operator CLI only — deliberately NOT an MCP
tool.

All network access goes through an injectable fetch(url) -> bytes | None
callable (the same injection pattern CliExtractor uses for subprocess.run),
so tests never touch the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = REPO_ROOT / "vault-template"


class ProvisionError(Exception):
    """Raised when provisioning cannot proceed for a plugin or the vault."""


@dataclass
class PluginSpec:
    id: str
    repo: str
    version: str


def load_plugin_manifest(path: Path | None = None) -> list[PluginSpec]:
    p = path or TEMPLATE_DIR / "plugins.json"
    entries = json.loads(p.read_text(encoding="utf-8"))
    specs: list[PluginSpec] = []
    for entry in entries:
        for field in ("id", "repo", "version"):
            if not entry.get(field):
                raise ProvisionError(f"plugins.json entry missing '{field}': {entry}")
        specs.append(PluginSpec(entry["id"], entry["repo"], entry["version"]))
    return specs


RELEASE_URL = "https://github.com/{repo}/releases/download/{version}/{filename}"
USER_AGENT = "tesseract-mcp-provisioner"


def http_fetch(url: str) -> bytes | None:
    """GET a release asset. Returns None on 404 (asset simply not present);
    raises ProvisionError on any other network failure."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except HTTPError as e:
        if e.code == 404:
            return None
        raise ProvisionError(f"download failed {url}: HTTP {e.code}") from e
    except URLError as e:
        raise ProvisionError(f"download failed {url}: {e.reason}") from e


def _asset_url(spec: PluginSpec, filename: str) -> str:
    return RELEASE_URL.format(repo=spec.repo, version=spec.version, filename=filename)


def installed_version(vault_root: Path, plugin_id: str) -> str | None:
    mf = Path(vault_root) / ".obsidian" / "plugins" / plugin_id / "manifest.json"
    if not mf.is_file():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8")).get("version")
    except (json.JSONDecodeError, OSError):
        return None


def install_plugin(vault_root: Path, spec: PluginSpec, fetch=http_fetch) -> str:
    vault_root = Path(vault_root)
    if installed_version(vault_root, spec.id) == spec.version:
        return "ok"
    manifest_bytes = fetch(_asset_url(spec, "manifest.json"))
    if manifest_bytes is None:
        raise ProvisionError(
            f"{spec.id}: no manifest.json in release {spec.version} of {spec.repo}"
        )
    manifest = json.loads(manifest_bytes)
    if manifest.get("id") != spec.id:
        raise ProvisionError(
            f"{spec.id}: release manifest declares id '{manifest.get('id')}' — refusing"
        )
    main_js = fetch(_asset_url(spec, "main.js"))
    if main_js is None:
        raise ProvisionError(f"{spec.id}: no main.js in release {spec.version}")
    styles = fetch(_asset_url(spec, "styles.css"))  # optional; None is fine

    dest = vault_root / ".obsidian" / "plugins" / spec.id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "manifest.json").write_bytes(manifest_bytes)
    (dest / "main.js").write_bytes(main_js)
    if styles is not None:
        (dest / "styles.css").write_bytes(styles)
    return "installed"
