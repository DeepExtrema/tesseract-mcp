import json
import time

from tesseract_mcp.sc_adapter import load_note_vectors


def _write_ajson(vault_dir, filename, entries):
    env_dir = vault_dir / ".smart-env" / "multi"
    env_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in entries:
        lines.append(f'"{key}": {json.dumps(value)},')
    (env_dir / filename).write_text("\n".join(lines), encoding="utf-8")


def test_loads_vector_for_fresh_note(vault, vault_dir):
    note = vault_dir / "Daily.md"
    note.write_text("hello", encoding="utf-8")
    future_ms = int((note.stat().st_mtime + 3600) * 1000)  # embedded after edit
    _write_ajson(
        vault_dir,
        "Daily_md.ajson",
        [(
            "smart_sources:Daily.md",
            {
                "path": "Daily.md",
                "last_embed": {"hash": "abc123", "at": future_ms},
                "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [0.1, 0.2, 0.3]}},
            },
        )],
    )
    got = load_note_vectors(vault)
    assert got["Daily.md"]["vec"] == [0.1, 0.2, 0.3]
    assert got["Daily.md"]["fresh"] is True


def test_marks_stale_when_edited_after_embedding(vault, vault_dir):
    note = vault_dir / "Daily.md"
    note.write_text("hello", encoding="utf-8")
    past_ms = int((note.stat().st_mtime - 3600) * 1000)  # embedded before edit
    _write_ajson(
        vault_dir,
        "Daily_md.ajson",
        [(
            "smart_sources:Daily.md",
            {
                "path": "Daily.md",
                "last_embed": {"hash": "abc123", "at": past_ms},
                "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [0.1, 0.2, 0.3]}},
            },
        )],
    )
    got = load_note_vectors(vault)
    assert got["Daily.md"]["fresh"] is False


def test_last_occurrence_wins_for_duplicate_keys(vault, vault_dir):
    note = vault_dir / "Daily.md"
    note.write_text("hello", encoding="utf-8")
    future_ms = int((note.stat().st_mtime + 3600) * 1000)
    _write_ajson(
        vault_dir,
        "Daily_md.ajson",
        [
            (
                "smart_sources:Daily.md",
                {
                    "path": "Daily.md",
                    "last_embed": {"hash": "old", "at": future_ms},
                    "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [1.0, 0.0]}},
                },
            ),
            (
                "smart_sources:Daily.md",
                {
                    "path": "Daily.md",
                    "last_embed": {"hash": "new", "at": future_ms},
                    "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [0.0, 1.0]}},
                },
            ),
        ],
    )
    got = load_note_vectors(vault)
    assert got["Daily.md"]["vec"] == [0.0, 1.0]


def test_no_smart_env_dir_returns_empty(vault):
    assert load_note_vectors(vault) == {}


def test_ignores_block_level_entries(vault, vault_dir):
    note = vault_dir / "Daily.md"
    note.write_text("hello", encoding="utf-8")
    future_ms = int((note.stat().st_mtime + 3600) * 1000)
    _write_ajson(
        vault_dir,
        "Daily_md.ajson",
        [
            (
                "smart_blocks:Daily.md#chunk0",
                {"path": "Daily.md#chunk0", "last_embed": {"at": future_ms},
                 "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [9.9]}}},
            ),
            (
                "smart_sources:Daily.md",
                {"path": "Daily.md", "last_embed": {"at": future_ms},
                 "embeddings": {"TaylorAI/bge-micro-v2": {"vec": [0.1, 0.2]}}},
            ),
        ],
    )
    got = load_note_vectors(vault)
    assert list(got.keys()) == ["Daily.md"]
    assert got["Daily.md"]["vec"] == [0.1, 0.2]
