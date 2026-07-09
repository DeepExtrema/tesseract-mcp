# Vault Provisioner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command (`python -m tesseract_mcp.provision <vault>`) turns a fresh Obsidian vault into a working Tesseract mind database: curated plugins at pinned versions, opinionated settings, conventions tree.

**Architecture:** A `vault-template/plugins.json` manifest pins 10 plugins (id + GitHub repo + version). `provision.py` downloads each plugin's release assets from GitHub (all network behind an injectable `fetch` callable, mirroring `CliExtractor`'s runner injection), verifies the release manifest's `id` matches before writing, merges ids into `community-plugins.json` without clobbering user entries, applies settings overlays only where none exist, then runs the conventions installer — whose `install()` moves from `scripts/` into the package first so `provision.py` can import it.

**Tech Stack:** Python 3.11+ stdlib only (`urllib.request`, `json`, `pathlib`) — no new dependencies. pytest.

## Global Constraints

- Repo: `C:\Users\Taimoor\Documents\GitHub\tesseract-mcp`, branch `codex/architecture-roadmap` (work in a worktree per superpowers:using-git-worktrees at execution time; worktree venvs need `pip install -e ".[dev]"`).
- No new runtime dependencies — stdlib only for the provisioner.
- Operator CLI only; do NOT register any new MCP tool (spec decision: agents must not trigger provisioning).
- Settings overlays are applied ONLY when the target file does not exist (spec: "re-provisioning never clobbers human tweaks").
- The Smart Connections embed model in the `.smart-env` template MUST be `TaylorAI/bge-micro-v2` (verbatim — `sc_adapter.py` depends on this key).
- Tests never touch the network: all downloads go through an injectable `fetch(url: str) -> bytes | None`.
- Full suite green after every task (185 tests at start).

---

## Task 1: Move the conventions installer into the package

**Files:**
- Create: `src/tesseract_mcp/conventions.py`
- Modify: `scripts/install_conventions.py` (becomes a thin wrapper)
- Test: `tests/test_install_conventions.py` (existing — must keep passing unchanged)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `tesseract_mcp.conventions.install(vault_root: Path) -> list[str]` — creates missing pieces of the Claude/ tree + root guides, returns vault-relative paths created. Task 5 imports this.

- [ ] **Step 1: Create the package module**

Create `src/tesseract_mcp/conventions.py` by moving the body of `scripts/install_conventions.py` (everything except the `if __name__ == "__main__":` block) verbatim, with one path fix — `REPO_ROOT` gains a third `.parent` because the file now lives one level deeper:

```python
"""Install the Claude/ conventions tree into an Obsidian vault.

Moved from scripts/install_conventions.py so provision.py can import it;
the script remains as a thin CLI wrapper.
Idempotent: never overwrites anything that already exists.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONSTITUTION = REPO_ROOT / "vault" / "constitution.md"
ROOT_GUIDE = REPO_ROOT / "vault" / "root-guide.md"

INDEX_SEED = "# Index\n\nMap of agent-written notes. Session entries append below.\n\n"

DECISIONS_SEED = (
    "---\ncreated: 2026-07-06\nagent: claude\ntags: [decisions]\n---\n\n"
    "# Decisions\n\n"
    "Append-only log. One line per decision:\n"
    "`- YYYY-MM-DD — <decision> ([[session note]])`\n\n"
)


def install(vault_root: Path) -> list[str]:
    """Create missing pieces of the Claude/ tree. Returns what was created."""
    vault_root = Path(vault_root)
    claude = vault_root / "Claude"
    created: list[str] = []

    for folder in (claude / "Inbox", claude / "Sessions", claude / "Concepts"):
        if not folder.is_dir():
            folder.mkdir(parents=True)
            created.append(str(folder.relative_to(vault_root)))

    readme = claude / "README.md"
    if not readme.exists():
        readme.write_text(
            CONSTITUTION.read_text(encoding="utf-8"), encoding="utf-8"
        )
        created.append("Claude/README.md")

    index = claude / "Index.md"
    if not index.exists():
        index.write_text(INDEX_SEED, encoding="utf-8")
        created.append("Claude/Index.md")

    decisions = claude / "Decisions.md"
    if not decisions.exists():
        decisions.write_text(DECISIONS_SEED, encoding="utf-8")
        created.append("Claude/Decisions.md")

    guide_text = ROOT_GUIDE.read_text(encoding="utf-8")
    for name in ("CLAUDE.md", "AGENTS.md"):
        guide = vault_root / name
        if not guide.exists():
            guide.write_text(guide_text, encoding="utf-8")
            created.append(name)

    return created
```

**Important:** copy the `install()` body from the CURRENT `scripts/install_conventions.py` in the worktree (shown above as of `66dbc69`) — if the script has drifted, the current script wins, not this listing.

- [ ] **Step 2: Reduce the script to a wrapper**

Replace the entire contents of `scripts/install_conventions.py` with:

```python
"""Thin CLI wrapper — the implementation lives in tesseract_mcp.conventions.

Usage: python scripts/install_conventions.py C:\\Vaults\\Tesseract
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tesseract_mcp.conventions import install  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/install_conventions.py <vault-path>")
    root = Path(sys.argv[1])
    if not root.is_dir():
        sys.exit(f"Vault not found: {root}")
    made = install(root)
    print("Created:", ", ".join(made) if made else "nothing (already installed)")
```

(The `sys.path.insert` keeps the script runnable standalone even outside an
editable install; `tests/test_install_conventions.py` imports `install` from
the script via its own path insertion and keeps working because the wrapper
re-exports the name.)

- [ ] **Step 3: Run the existing conventions tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_install_conventions.py -v`
Expected: PASS — every existing test, unchanged.

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (185 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/conventions.py scripts/install_conventions.py
git commit -m "refactor: move conventions installer into the package

provision.py needs to import install(); the script stays as a thin
CLI wrapper re-exporting it, so existing callers and tests are
untouched."
```

---

## Task 2: Plugin manifest + settings templates

**Files:**
- Create: `vault-template/plugins.json`
- Create: `vault-template/settings/smart-env/smart_env.json`
- Create: `src/tesseract_mcp/provision.py` (manifest loading only in this task)
- Test: `tests/test_provision.py` (new)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `PluginSpec` dataclass (`id: str, repo: str, version: str`), `load_plugin_manifest(path: Path | None = None) -> list[PluginSpec]`, `ProvisionError(Exception)`, module constants `TEMPLATE_DIR: Path`. Tasks 3-5 consume all of these.

- [ ] **Step 1: Create the data files**

Create `vault-template/plugins.json`:

```json
[
  {"id": "smart-connections", "repo": "brianpetro/obsidian-smart-connections", "version": "4.5.3"},
  {"id": "obsidian-tasks-plugin", "repo": "obsidian-tasks-group/obsidian-tasks", "version": "8.2.2"},
  {"id": "obsidian-livesync", "repo": "vrtmrz/obsidian-livesync", "version": "0.25.79"},
  {"id": "text-extractor", "repo": "scambier/obsidian-text-extractor", "version": "0.7.0"},
  {"id": "dataview", "repo": "blacksmithgu/obsidian-dataview", "version": "0.5.68"},
  {"id": "omnisearch", "repo": "scambier/obsidian-omnisearch", "version": "1.29.3"},
  {"id": "tag-wrangler", "repo": "pjeby/tag-wrangler", "version": "0.6.4"},
  {"id": "obsidian-kanban", "repo": "mgmeyers/obsidian-kanban", "version": "2.0.51"},
  {"id": "table-editor-obsidian", "repo": "tgrosinger/advanced-tables-obsidian", "version": "0.23.2"},
  {"id": "obsidian-importer", "repo": "obsidianmd/obsidian-importer", "version": "1.8.12"}
]
```

Create `vault-template/settings/smart-env/smart_env.json` (minimal template
pinning the embed model `sc_adapter.py` depends on; Smart Connections fills
in the rest of its defaults on first run):

```json
{
  "is_obsidian_vault": true,
  "smart_sources": {
    "min_chars": 200,
    "embed_model": {
      "adapter": "transformers",
      "transformers": {
        "model_key": "TaylorAI/bge-micro-v2"
      }
    }
  },
  "models": {
    "embedding_platform": "transformers"
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_provision.py`:

```python
import json

import pytest

from tesseract_mcp.provision import (
    PluginSpec,
    ProvisionError,
    TEMPLATE_DIR,
    load_plugin_manifest,
)


def test_repo_manifest_loads_ten_pinned_plugins():
    specs = load_plugin_manifest()
    assert len(specs) == 10
    by_id = {s.id: s for s in specs}
    assert by_id["smart-connections"].repo == "brianpetro/obsidian-smart-connections"
    assert all(s.version for s in specs)


def test_smart_env_template_pins_the_adapter_model():
    template = json.loads(
        (TEMPLATE_DIR / "settings" / "smart-env" / "smart_env.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        template["smart_sources"]["embed_model"]["transformers"]["model_key"]
        == "TaylorAI/bge-micro-v2"
    )


def test_manifest_entry_missing_field_raises(tmp_path):
    bad = tmp_path / "plugins.json"
    bad.write_text(json.dumps([{"id": "x", "repo": "a/b"}]), encoding="utf-8")
    with pytest.raises(ProvisionError, match="version"):
        load_plugin_manifest(bad)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_provision.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tesseract_mcp.provision'`

- [ ] **Step 4: Implement manifest loading**

Create `src/tesseract_mcp/provision.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_provision.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add vault-template/ src/tesseract_mcp/provision.py tests/test_provision.py
git commit -m "feat(provision): pinned plugin manifest and settings templates"
```

---

## Task 3: Release download + single-plugin install

**Files:**
- Modify: `src/tesseract_mcp/provision.py`
- Test: `tests/test_provision.py`

**Interfaces:**
- Consumes: `PluginSpec`, `ProvisionError` (Task 2).
- Produces: `http_fetch(url: str) -> bytes | None` (real network; 404 → None), `installed_version(vault_root: Path, plugin_id: str) -> str | None`, `install_plugin(vault_root: Path, spec: PluginSpec, fetch=http_fetch) -> str` returning `"ok"` (already at pin) or `"installed"`. Tasks 4-5 consume these. URL shape: `https://github.com/{repo}/releases/download/{version}/{filename}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_provision.py`:

```python
from tesseract_mcp.provision import install_plugin, installed_version

SPEC = PluginSpec("dataview", "blacksmithgu/obsidian-dataview", "0.5.68")
BASE = "https://github.com/blacksmithgu/obsidian-dataview/releases/download/0.5.68"


def make_fetcher(assets: dict[str, bytes]):
    """Fake fetch: dict of url -> bytes; unknown urls return None (404)."""
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        return assets.get(url)

    fetch.calls = calls
    return fetch


def good_assets(plugin_id="dataview", version="0.5.68", base=BASE):
    manifest = json.dumps({"id": plugin_id, "version": version}).encode()
    return {
        f"{base}/manifest.json": manifest,
        f"{base}/main.js": b"console.log('plugin');",
        f"{base}/styles.css": b".dv {}",
    }


def test_install_plugin_writes_all_three_files(tmp_path):
    fetch = make_fetcher(good_assets())
    result = install_plugin(tmp_path, SPEC, fetch)
    assert result == "installed"
    plugin_dir = tmp_path / ".obsidian" / "plugins" / "dataview"
    assert (plugin_dir / "manifest.json").is_file()
    assert (plugin_dir / "main.js").read_bytes() == b"console.log('plugin');"
    assert (plugin_dir / "styles.css").is_file()


def test_install_plugin_ok_when_already_at_pin(tmp_path):
    fetch = make_fetcher(good_assets())
    install_plugin(tmp_path, SPEC, fetch)
    fetch2 = make_fetcher(good_assets())
    assert install_plugin(tmp_path, SPEC, fetch2) == "ok"
    assert fetch2.calls == []  # no downloads when already pinned


def test_install_plugin_missing_styles_is_fine(tmp_path):
    assets = good_assets()
    del assets[f"{BASE}/styles.css"]
    fetch = make_fetcher(assets)
    assert install_plugin(tmp_path, SPEC, fetch) == "installed"
    assert not (tmp_path / ".obsidian" / "plugins" / "dataview" / "styles.css").exists()


def test_install_plugin_rejects_manifest_id_mismatch(tmp_path):
    assets = good_assets(plugin_id="evil-other-plugin")
    fetch = make_fetcher(assets)
    with pytest.raises(ProvisionError, match="evil-other-plugin"):
        install_plugin(tmp_path, SPEC, fetch)
    assert not (tmp_path / ".obsidian" / "plugins" / "dataview").exists()


def test_install_plugin_missing_main_js_raises(tmp_path):
    assets = good_assets()
    del assets[f"{BASE}/main.js"]
    fetch = make_fetcher(assets)
    with pytest.raises(ProvisionError, match="main.js"):
        install_plugin(tmp_path, SPEC, fetch)


def test_installed_version_reads_manifest(tmp_path):
    assert installed_version(tmp_path, "dataview") is None
    fetch = make_fetcher(good_assets())
    install_plugin(tmp_path, SPEC, fetch)
    assert installed_version(tmp_path, "dataview") == "0.5.68"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_provision.py -v`
Expected: FAIL with `ImportError: cannot import name 'install_plugin'`

- [ ] **Step 3: Implement**

Add to `src/tesseract_mcp/provision.py` (below `load_plugin_manifest`; add
`from urllib.error import HTTPError, URLError` and
`from urllib.request import Request, urlopen` to the imports):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_provision.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/provision.py tests/test_provision.py
git commit -m "feat(provision): download and install a pinned plugin from GitHub releases"
```

---

## Task 4: Enable in community-plugins.json + settings overlays

**Files:**
- Modify: `src/tesseract_mcp/provision.py`
- Test: `tests/test_provision.py`

**Interfaces:**
- Consumes: `TEMPLATE_DIR` (Task 2).
- Produces: `enable_plugins(vault_root: Path, ids: list[str]) -> list[str]` (returns newly added ids; merge-only, never removes), `apply_overlays(vault_root: Path) -> list[str]` (returns what was applied; absent-only). Task 5 consumes both.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_provision.py`:

```python
from tesseract_mcp.provision import apply_overlays, enable_plugins


def test_enable_plugins_creates_and_merges(tmp_path):
    added = enable_plugins(tmp_path, ["dataview", "omnisearch"])
    assert added == ["dataview", "omnisearch"]
    cfg = tmp_path / ".obsidian" / "community-plugins.json"
    assert json.loads(cfg.read_text(encoding="utf-8")) == ["dataview", "omnisearch"]


def test_enable_plugins_preserves_user_entries(tmp_path):
    cfg = tmp_path / ".obsidian" / "community-plugins.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps(["users-own-plugin", "dataview"]), encoding="utf-8")
    added = enable_plugins(tmp_path, ["dataview", "omnisearch"])
    assert added == ["omnisearch"]
    result = json.loads(cfg.read_text(encoding="utf-8"))
    assert result == ["users-own-plugin", "dataview", "omnisearch"]


def test_apply_overlays_writes_smart_env_when_absent(tmp_path):
    applied = apply_overlays(tmp_path)
    assert ".smart-env/smart_env.json" in applied
    written = json.loads(
        (tmp_path / ".smart-env" / "smart_env.json").read_text(encoding="utf-8")
    )
    assert (
        written["smart_sources"]["embed_model"]["transformers"]["model_key"]
        == "TaylorAI/bge-micro-v2"
    )


def test_apply_overlays_never_clobbers_existing(tmp_path):
    env_dir = tmp_path / ".smart-env"
    env_dir.mkdir()
    (env_dir / "smart_env.json").write_text('{"user": "tweaked"}', encoding="utf-8")
    applied = apply_overlays(tmp_path)
    assert ".smart-env/smart_env.json" not in applied
    assert json.loads(
        (env_dir / "smart_env.json").read_text(encoding="utf-8")
    ) == {"user": "tweaked"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_provision.py -v`
Expected: FAIL with `ImportError: cannot import name 'enable_plugins'`

- [ ] **Step 3: Implement**

Add to `src/tesseract_mcp/provision.py`:

```python
def enable_plugins(vault_root: Path, ids: list[str]) -> list[str]:
    """Merge ids into community-plugins.json. Never removes existing entries."""
    cfg = Path(vault_root) / ".obsidian" / "community-plugins.json"
    existing: list[str] = []
    if cfg.is_file():
        existing = json.loads(cfg.read_text(encoding="utf-8"))
    added = [i for i in ids if i not in existing]
    if added:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(existing + added, indent=2), encoding="utf-8")
    return added


def apply_overlays(vault_root: Path) -> list[str]:
    """Copy settings templates into the vault — only where nothing exists yet."""
    vault_root = Path(vault_root)
    applied: list[str] = []
    settings_dir = TEMPLATE_DIR / "settings"
    if not settings_dir.is_dir():
        return applied
    for src in sorted(settings_dir.glob("*/data.json")):
        plugin_id = src.parent.name
        dest = vault_root / ".obsidian" / "plugins" / plugin_id / "data.json"
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        applied.append(f"{plugin_id}/data.json")
    smart_env_src = settings_dir / "smart-env" / "smart_env.json"
    if smart_env_src.is_file():
        dest = vault_root / ".smart-env" / "smart_env.json"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(smart_env_src.read_text(encoding="utf-8"), encoding="utf-8")
            applied.append(".smart-env/smart_env.json")
    return applied
```

(Note: `smart-env` is deliberately not a plugin id — its template targets
`<vault>/.smart-env/`, not `.obsidian/plugins/`. The `*/data.json` glob
doesn't match it because the template file is named `smart_env.json`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_provision.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add src/tesseract_mcp/provision.py tests/test_provision.py
git commit -m "feat(provision): enable plugins and apply absent-only settings overlays"
```

---

## Task 5: Orchestrator, --check mode, CLI, docs

**Files:**
- Modify: `src/tesseract_mcp/provision.py`
- Modify: `README.md`
- Test: `tests/test_provision.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4: `conventions.install(vault_root) -> list[str]`, `load_plugin_manifest()`, `install_plugin(vault_root, spec, fetch)`, `enable_plugins(vault_root, ids)`, `apply_overlays(vault_root)`.
- Produces: `provision(vault_root, fetch=http_fetch) -> dict` with keys `plugins` (id → "ok"/"installed"), `errors` (id → message), `enabled`, `overlays`, `conventions`; `check(vault_root) -> dict` (id → {pinned, installed, status: ok/drift/missing}); `main()` CLI entry (`python -m tesseract_mcp.provision <vault> [--check]`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_provision.py`:

```python
from tesseract_mcp.provision import check, load_plugin_manifest, provision


def all_good_assets():
    assets = {}
    for spec in load_plugin_manifest():
        base = f"https://github.com/{spec.repo}/releases/download/{spec.version}"
        manifest = json.dumps({"id": spec.id, "version": spec.version}).encode()
        assets[f"{base}/manifest.json"] = manifest
        assets[f"{base}/main.js"] = b"//js"
        assets[f"{base}/styles.css"] = b"/*css*/"
    return assets


def test_provision_fresh_vault_end_to_end(tmp_path):
    fetch = make_fetcher(all_good_assets())
    report = provision(tmp_path, fetch)
    assert report["errors"] == {}
    assert set(report["plugins"]) == {s.id for s in load_plugin_manifest()}
    assert all(v == "installed" for v in report["plugins"].values())
    enabled = json.loads(
        (tmp_path / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")
    )
    assert "smart-connections" in enabled
    assert (tmp_path / ".smart-env" / "smart_env.json").is_file()
    assert (tmp_path / "Claude" / "README.md").is_file()  # conventions ran


def test_provision_is_idempotent(tmp_path):
    provision(tmp_path, make_fetcher(all_good_assets()))
    fetch2 = make_fetcher(all_good_assets())
    report = provision(tmp_path, fetch2)
    assert all(v == "ok" for v in report["plugins"].values())
    assert fetch2.calls == []  # nothing re-downloaded
    assert report["enabled"] == []  # nothing newly enabled


def test_provision_isolates_one_bad_plugin(tmp_path):
    assets = all_good_assets()
    # break dataview's main.js (404); everything else stays fine
    del assets["https://github.com/blacksmithgu/obsidian-dataview/releases/download/0.5.68/main.js"]
    report = provision(tmp_path, make_fetcher(assets))
    assert "dataview" in report["errors"]
    assert "main.js" in report["errors"]["dataview"]
    assert report["plugins"]["omnisearch"] == "installed"
    enabled = json.loads(
        (tmp_path / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")
    )
    assert "dataview" not in enabled  # failed plugins are not enabled


def test_provision_rejects_missing_vault(tmp_path):
    with pytest.raises(ProvisionError, match="does not exist"):
        provision(tmp_path / "nope", make_fetcher({}))


def test_check_reports_ok_drift_missing(tmp_path):
    provision(tmp_path, make_fetcher(all_good_assets()))
    # induce drift: rewrite dataview's manifest with an older version
    mf = tmp_path / ".obsidian" / "plugins" / "dataview" / "manifest.json"
    mf.write_text(json.dumps({"id": "dataview", "version": "0.0.1"}), encoding="utf-8")
    # induce missing: remove omnisearch entirely
    import shutil

    shutil.rmtree(tmp_path / ".obsidian" / "plugins" / "omnisearch")
    report = check(tmp_path)
    assert report["dataview"]["status"] == "drift"
    assert report["omnisearch"]["status"] == "missing"
    assert report["smart-connections"]["status"] == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_provision.py -v`
Expected: FAIL with `ImportError: cannot import name 'check'`

- [ ] **Step 3: Implement**

Add to `src/tesseract_mcp/provision.py` (plus `import argparse` at the top,
and `from .conventions import install as install_conventions`):

```python
NEXT_STEPS = """
Provisioning done. Remaining human steps:
 1. Open the vault in Obsidian once and turn OFF Restricted Mode
    (Settings -> Community plugins) so the installed plugins load.
 2. Complete LiveSync setup via its Setup-URI flow — server URI and the
    E2E passphrase live in your password manager, never in this repo.
 3. Register the vault with the MCP server (see README) and run the
    index_brain tool to build the semantic graph.
"""


def provision(vault_root: str | Path, fetch=http_fetch) -> dict:
    vault_root = Path(vault_root)
    if not vault_root.is_dir():
        raise ProvisionError(f"Vault root does not exist: {vault_root}")
    (vault_root / ".obsidian").mkdir(exist_ok=True)

    specs = load_plugin_manifest()
    plugins: dict[str, str] = {}
    errors: dict[str, str] = {}
    for spec in specs:
        try:
            plugins[spec.id] = install_plugin(vault_root, spec, fetch)
        except ProvisionError as e:
            errors[spec.id] = str(e)

    enabled = enable_plugins(
        vault_root, [s.id for s in specs if s.id not in errors]
    )
    overlays = apply_overlays(vault_root)
    conventions = install_conventions(vault_root)
    return {
        "plugins": plugins,
        "errors": errors,
        "enabled": enabled,
        "overlays": overlays,
        "conventions": conventions,
    }


def check(vault_root: str | Path) -> dict:
    vault_root = Path(vault_root)
    report: dict[str, dict] = {}
    for spec in load_plugin_manifest():
        installed = installed_version(vault_root, spec.id)
        if installed == spec.version:
            status = "ok"
        elif installed is None:
            status = "missing"
        else:
            status = "drift"
        report[spec.id] = {
            "pinned": spec.version, "installed": installed, "status": status
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision an Obsidian vault as a Tesseract mind database."
    )
    parser.add_argument("vault", help="Path to the vault root")
    parser.add_argument(
        "--check", action="store_true",
        help="Report pinned vs installed plugin versions without changing anything",
    )
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check(args.vault), indent=2))
        return
    report = provision(args.vault)
    print(json.dumps(report, indent=2))
    if report["errors"]:
        print("\nWARNING: some plugins failed — re-run after checking pins/network.")
    print(NEXT_STEPS)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_provision.py -v`
Expected: PASS (18 passed)

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (185 + 18 = 203 tests).

- [ ] **Step 6: Document in README**

In `README.md`, add after the "## Install" section:

```markdown
## Provision a new vault

    python -m tesseract_mcp.provision C:\Path\To\NewVault

Installs the curated plugin set (pinned in `vault-template/plugins.json`),
enables them, seeds Smart Connections settings (embed model pinned to the
one `sc_adapter` reads), and installs the Claude/ conventions tree. Then:
open the vault once in Obsidian and turn off Restricted Mode, complete
LiveSync via Setup-URI, and run `index_brain`.

    python -m tesseract_mcp.provision C:\Path\To\Vault --check

reports pinned vs installed versions (ok / drift / missing). Upgrading a
plugin = bump its pin in `vault-template/plugins.json`, re-run provision.
```

- [ ] **Step 7: Commit**

```bash
git add src/tesseract_mcp/provision.py tests/test_provision.py README.md
git commit -m "feat(provision): orchestrator, --check mode, and CLI

python -m tesseract_mcp.provision <vault> provisions the curated
plugin set end to end; failed plugins are isolated and not enabled;
prints the remaining human steps (Restricted Mode, LiveSync secrets)."
```

---

## Self-Review Notes

**Spec coverage:**
- Pinned 10-plugin manifest → Task 2
- GitHub release download, id-mismatch guard, optional styles.css, 404 semantics → Task 3
- community-plugins.json merge-not-clobber → Task 4
- Absent-only settings overlays + `.smart-env` template with `TaylorAI/bge-micro-v2` verbatim → Tasks 2 & 4
- Idempotent orchestrator, failure isolation (bad plugin doesn't block or get enabled), `--check`, human checklist (Restricted Mode, LiveSync Setup-URI, index_brain) → Task 5
- Conventions installer moved into package, script stays as wrapper, existing tests untouched → Task 1
- Non-goals (no removal, no LiveSync secrets, no auto-update, no sha256 lockfile, no MCP tool) → no tasks, and Task 5's CLI adds nothing beyond provision/check

**Placeholder scan:** clean — every code step has complete code; no TBDs.

**Type consistency:** `install_plugin` returns `"ok" | "installed"` and Task 5's idempotency test asserts exactly those values; `enable_plugins` returns newly-added ids and Task 5 asserts `enabled == []` on re-run; `conventions.install(vault_root) -> list[str]` matches the moved signature Task 1 defines.

**One deliberate judgment call:** Task 3 skips downloads entirely when the installed version equals the pin (checked via the plugin's own `manifest.json`) — this is what makes Task 5's idempotency test (`fetch2.calls == []`) meaningful and keeps re-provisioning fast and network-free.
