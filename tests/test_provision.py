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
