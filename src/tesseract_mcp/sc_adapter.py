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

# In-process memo of parsed .ajson files keyed by (mtime_ns, size): Smart
# Connections' files are re-read on every search in a long-running server
# but only change when Obsidian re-embeds something.
_parsed_ajson: dict[str, tuple[tuple[int, int], dict]] = {}


def _parse_ajson_file(path: Path) -> dict[str, dict]:
    try:
        stat = path.stat()
    except OSError:
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    memo = _parsed_ajson.get(str(path))
    if memo and memo[0] == stamp:
        return memo[1]
    lines = [
        line.strip().rstrip(",")
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    ]
    blob = "{" + ",".join(lines) + "}" if lines else "{}"
    try:
        entries = json.loads(blob)
    except json.JSONDecodeError:
        entries = {}
    _parsed_ajson[str(path)] = (stamp, entries)
    return entries


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
