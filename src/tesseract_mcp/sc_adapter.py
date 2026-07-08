"""Reads Smart Connections' local embeddings directly from disk.

.smart-env/multi/*.ajson is Smart Connections' own "append-only JSON"
format: one `"key": {...},` fragment per line, with later lines for the
same key superseding earlier ones. Wrapping the stripped lines in braces
and parsing as one JSON object gives last-occurrence-wins for free, since
Python's dict construction from duplicate keys keeps the last value.

Only whole-note entries (`smart_sources:<path>`) are used — block-level
`smart_blocks:<path>#chunk` entries are Smart Connections' finer-grained
index and are out of scope for note-level ranking.
"""

from __future__ import annotations

import json
from pathlib import Path

from .vault import Vault

SMART_ENV_DIR = ".smart-env"
MODEL_KEY = "TaylorAI/bge-micro-v2"
_SOURCE_PREFIX = "smart_sources:"


def _parse_ajson_file(path: Path) -> dict[str, dict]:
    lines = [
        line.strip().rstrip(",")
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    ]
    if not lines:
        return {}
    blob = "{" + ",".join(lines) + "}"
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return {}


def load_note_vectors(vault: Vault, model_key: str = MODEL_KEY) -> dict[str, dict]:
    multi_dir = vault.root / SMART_ENV_DIR / "multi"
    if not multi_dir.is_dir():
        return {}
    results: dict[str, dict] = {}
    for ajson_file in sorted(multi_dir.glob("*.ajson")):
        entries = _parse_ajson_file(ajson_file)
        for key, entry in entries.items():
            if not key.startswith(_SOURCE_PREFIX):
                continue
            note_path = entry.get("path")
            embeddings = entry.get("embeddings") or {}
            model_entry = embeddings.get(model_key)
            if not note_path or not model_entry or "vec" not in model_entry:
                continue
            note_file = vault.root / note_path
            if not note_file.is_file():
                continue
            embedded_at_ms = (entry.get("last_embed") or {}).get("at", 0)
            mtime_ms = note_file.stat().st_mtime * 1000
            results[note_path] = {
                "vec": model_entry["vec"],
                "fresh": embedded_at_ms >= mtime_ms,
            }
    return results
