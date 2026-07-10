"""The Librarian: one caretaker sweep over the vault's databases and files.

Thin orchestrator — index, organize, cache, throttled dry-run consolidation,
health checks, report. Owns no indexing/organizing logic of its own; see
docs/superpowers/specs/2026-07-09-librarian-design.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

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
