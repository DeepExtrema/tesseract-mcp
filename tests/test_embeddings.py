import json

import pytest

from tesseract_mcp.embeddings import get_note_vectors, stale_notes
from tesseract_mcp.sc_adapter import MODEL_KEY


class FakeEmbedder:
    """Deterministic stand-in — no model download in tests."""

    def __init__(self):
        self.calls = []

    def embed_batch(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 0.0] for t in texts]


def _write_sc_vector(vault_dir, note_rel, vec, fresh=True):
    note = vault_dir / note_rel
    env_dir = vault_dir / ".smart-env" / "multi"
    env_dir.mkdir(parents=True, exist_ok=True)
    offset = 3600 if fresh else -3600
    at_ms = int((note.stat().st_mtime + offset) * 1000)
    entry = {
        "path": note_rel,
        "last_embed": {"at": at_ms},
        "embeddings": {MODEL_KEY: {"vec": vec}},
    }
    (env_dir / f"{note_rel.replace('/', '_')}.ajson").write_text(
        f'"smart_sources:{note_rel}": {json.dumps(entry)},', encoding="utf-8"
    )


def test_uses_smart_connections_vector_when_fresh(vault, vault_dir):
    _write_sc_vector(vault_dir, "Daily.md", [1.0, 2.0], fresh=True)
    for path in sorted(vault_dir.rglob("*.md")):
        rel = "/".join(path.relative_to(vault_dir).parts)
        if rel != "Daily.md":
            _write_sc_vector(vault_dir, rel, [0.0, 0.0], fresh=True)
    embedder = FakeEmbedder()
    got = get_note_vectors(vault, vault.root, embedder)
    assert got["Daily.md"] == [1.0, 2.0]
    assert embedder.calls == []  # never fell back for this note


def test_falls_back_when_stale(vault, vault_dir):
    _write_sc_vector(vault_dir, "Daily.md", [1.0, 2.0], fresh=False)
    embedder = FakeEmbedder()
    got = get_note_vectors(vault, vault.root, embedder)
    assert got["Daily.md"] != [1.0, 2.0]
    assert embedder.calls  # fallback was used


def test_falls_back_when_missing(vault):
    embedder = FakeEmbedder()
    got = get_note_vectors(vault, vault.root, embedder)
    assert "Daily.md" in got
    assert embedder.calls


def test_fallback_cached_across_calls(vault):
    embedder = FakeEmbedder()
    get_note_vectors(vault, vault.root, embedder)
    call_count_after_first = len(embedder.calls)
    get_note_vectors(vault, vault.root, embedder)
    assert len(embedder.calls) == call_count_after_first  # no re-embedding


def test_stale_notes_lists_only_uncached_edits(vault, vault_dir, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    get_note_vectors(vault, state, FakeEmbedder())  # warm the fallback cache
    assert stale_notes(vault, state) == []

    (vault_dir / "Daily.md").write_text("edited content\n", encoding="utf-8")
    assert stale_notes(vault, state) == ["Daily.md"]


def test_stale_notes_does_not_write(vault, vault_dir, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    stale_notes(vault, state)
    assert not (state / "fallback_embeddings.json").exists()
