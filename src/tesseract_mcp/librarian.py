"""The Librarian: one caretaker sweep over the vault's databases and files.

Thin orchestrator — index, organize, cache, throttled dry-run consolidation,
health checks, report. Owns no indexing/organizing logic of its own; see
docs/superpowers/specs/2026-07-09-librarian-design.md.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import consolidate as consolidate_mod
from . import embeddings as embeddings_mod
from . import indexer
from .vault import Vault

TS_FMT = "%Y-%m-%d %H:%M:%S"
STATE_FILE = "librarian_state.json"
CONSOLIDATE_MIN_NEW_ENTITIES = 15
CONSOLIDATE_MAX_AGE_DAYS = 14


def state_path(vault: Vault) -> Path:
    return indexer.state_dir(vault.root) / STATE_FILE


def load_state(vault: Vault) -> dict:
    p = state_path(vault)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"last_sweep": None, "steps": {}, "health": {},
            "errors": {}, "consolidation": {}}


def save_state(vault: Vault, state: dict) -> None:
    state_path(vault).write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_consolidate(
    state: dict, current_entities: int, now: datetime
) -> tuple[bool, str]:
    """Throttle: ≥15 new entities since the last pass, or ≥14 days with at
    least one. First pass runs as soon as any entity exists."""
    last = state.get("consolidation") or {}
    baseline = last.get("entities_at_last_pass")
    if baseline is None:
        if current_entities > 0:
            return True, "first pass"
        return False, "no entities yet"
    new = max(0, current_entities - baseline)
    if new >= CONSOLIDATE_MIN_NEW_ENTITIES:
        return True, f"{new} new entities (threshold {CONSOLIDATE_MIN_NEW_ENTITIES})"
    last_pass = datetime.strptime(last["last_pass"], TS_FMT)
    age_days = (now - last_pass).days
    if new >= 1 and age_days >= CONSOLIDATE_MAX_AGE_DAYS:
        return True, f"{age_days} days since last pass with {new} new entities"
    return False, (f"{new} new entities since last pass; "
                   f"threshold {CONSOLIDATE_MIN_NEW_ENTITIES}")


def check_manifest_drift(vault: Vault) -> dict:
    manifest = indexer.load_manifest(vault.root)
    current = indexer.scan_notes(vault)
    tracked, present = set(manifest["hashes"]), set(current)
    return {"deleted_but_tracked": sorted(tracked - present),
            "present_but_untracked": sorted(present - tracked)}


def check_orphaned_entities(vault: Vault) -> list[dict]:
    db = indexer.db_path(vault.root)
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT DISTINCT entity_path, note_path FROM mentions"
    ).fetchall()
    con.close()
    return [
        {"entity": entity_path, "missing_note": note_path}
        for entity_path, note_path in rows
        if not (vault.root / (note_path + ".md")).is_file()
    ]


def check_cache_consistency(vault: Vault) -> dict:
    md_count = len(consolidate_mod.gather_entities(vault))
    db = indexer.db_path(vault.root)
    if not db.exists():
        return {"db_entities": None, "md_entities": md_count,
                "consistent": md_count == 0}
    con = sqlite3.connect(db)
    db_count = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    con.close()
    return {"db_entities": db_count, "md_entities": md_count,
            "consistent": db_count == md_count}


def count_pending_proposals(
    state: dict, organize_report: dict | None, consolidate_result: dict | None
) -> int:
    n = len((organize_report or {}).get("proposals", []))
    if consolidate_result and consolidate_result.get("ran"):
        n += len(consolidate_result.get("proposed", []))
    else:
        n += len((state.get("consolidation") or {}).get("pending_proposals", []))
    return n


def run_health(
    vault: Vault,
    state: dict,
    organize_report: dict | None,
    consolidate_result: dict | None,
    errors: dict,
) -> dict:
    checks = {
        "stale_embeddings": lambda: len(
            embeddings_mod.stale_notes(vault, indexer.state_dir(vault.root))),
        "manifest_drift": lambda: check_manifest_drift(vault),
        "orphaned_entities": lambda: check_orphaned_entities(vault),
        "cache_consistency": lambda: check_cache_consistency(vault),
        "pending_proposals": lambda: count_pending_proposals(
            state, organize_report, consolidate_result),
        "sweep_errors": lambda: dict(errors),
    }
    out: dict = {}
    for name, fn in checks.items():
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — health must never kill the sweep
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    return out
