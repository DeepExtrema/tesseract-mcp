import json

import pytest

from tesseract_mcp.provision import (
    PluginSpec,
    ProvisionError,
    TEMPLATE_DIR,
    install_plugin,
    installed_version,
    load_plugin_manifest,
)

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
