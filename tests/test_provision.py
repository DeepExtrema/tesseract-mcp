import json
import shutil

import pytest

from tesseract_mcp.provision import (
    PluginSpec,
    ProvisionError,
    TEMPLATE_DIR,
    apply_overlays,
    check,
    enable_plugins,
    install_plugin,
    installed_version,
    load_plugin_manifest,
    provision,
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
    assert (tmp_path / "Claude" / "README.md").is_file()


def test_provision_is_idempotent(tmp_path):
    provision(tmp_path, make_fetcher(all_good_assets()))
    fetch2 = make_fetcher(all_good_assets())
    report = provision(tmp_path, fetch2)
    assert all(v == "ok" for v in report["plugins"].values())
    assert fetch2.calls == []
    assert report["enabled"] == []


def test_provision_isolates_one_bad_plugin(tmp_path):
    assets = all_good_assets()
    del assets["https://github.com/blacksmithgu/obsidian-dataview/releases/download/0.5.68/main.js"]
    report = provision(tmp_path, make_fetcher(assets))
    assert "dataview" in report["errors"]
    assert "main.js" in report["errors"]["dataview"]
    assert report["plugins"]["omnisearch"] == "installed"
    enabled = json.loads(
        (tmp_path / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")
    )
    assert "dataview" not in enabled


def test_provision_rejects_missing_vault(tmp_path):
    with pytest.raises(ProvisionError, match="does not exist"):
        provision(tmp_path / "nope", make_fetcher({}))


def test_check_reports_ok_drift_missing(tmp_path):
    provision(tmp_path, make_fetcher(all_good_assets()))
    mf = tmp_path / ".obsidian" / "plugins" / "dataview" / "manifest.json"
    mf.write_text(json.dumps({"id": "dataview", "version": "0.0.1"}), encoding="utf-8")
    shutil.rmtree(tmp_path / ".obsidian" / "plugins" / "omnisearch")
    report = check(tmp_path)
    assert report["dataview"]["status"] == "drift"
    assert report["omnisearch"]["status"] == "missing"
    assert report["smart-connections"]["status"] == "ok"
