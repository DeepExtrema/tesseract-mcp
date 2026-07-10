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

from . import cache
from . import consolidate as consolidate_mod
from . import embeddings as embeddings_mod
from . import extractor as extractor_mod
from . import indexer
from . import organizer as organizer_mod
from .vault import Vault

TS_FMT = "%Y-%m-%d %H:%M:%S"
STATE_FILE = "librarian_state.json"
CONSOLIDATE_MIN_NEW_ENTITIES = 15
CONSOLIDATE_MAX_AGE_DAYS = 14
MAX_INDEX_ROUNDS = 40


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


def _drain_index(vault: Vault, extractor) -> dict:
    """Run indexer batches until nothing remains (bounded)."""
    totals: dict | None = None
    for _ in range(MAX_INDEX_ROUNDS):
        counts = indexer.run(vault, extractor)
        if totals is None:
            totals = dict(counts)
        else:
            for key, val in counts.items():
                if key not in ("remaining", "skipped"):
                    totals[key] += val
            totals["remaining"] = counts["remaining"]
            totals["skipped"] = counts["skipped"]
        if counts["remaining"] == 0:
            break
    return totals or {}


def _index_preview(vault: Vault) -> dict:
    """Dry-run index: count pending notes without extracting or writing."""
    manifest = indexer.load_manifest(vault.root)
    current = indexer.scan_notes(vault)
    pending = [
        rel for rel, digest in current.items()
        if manifest["hashes"].get(rel) != digest or manifest["failures"].get(rel)
    ]
    return {"pending": len(pending)}


def _ensure_cache(vault: Vault, result: dict) -> dict:
    idx = result["steps"].get("index") or {}
    org = result["steps"].get("organize") or {}
    if idx.get("processed"):
        return {"rebuilt": True, "by": "index"}
    if org.get("cache_rebuilt"):
        return {"rebuilt": True, "by": "organize"}
    db = indexer.db_path(vault.root)
    if not db.exists():
        if not result["applied"]:
            return {"rebuilt": False, "by": "none"}
        cache.rebuild(vault, db)
        return {"rebuilt": True, "by": "librarian"}
    return {"rebuilt": False, "by": "none"}


def _sync_manifest_after_moves(vault: Vault, organize_report: dict | None) -> None:
    moved = (organize_report or {}).get("moved") or []
    has_organizer_note = (vault.root / "Claude" / "Organizer.md").is_file()
    if not moved and not has_organizer_note:
        return
    manifest = indexer.load_manifest(vault.root)
    changed = False
    for item in moved:
        src = item.get("from")
        folder = item.get("to_folder")
        if not src or not folder:
            continue
        stem = src.rsplit("/", 1)[-1]
        dst = f"{folder}/{stem}"
        if src in manifest["hashes"]:
            manifest["hashes"][dst] = manifest["hashes"].pop(src)
            changed = True
        if src in manifest["failures"]:
            manifest["failures"][dst] = manifest["failures"].pop(src)
            changed = True
    if has_organizer_note:
        current = indexer.scan_notes(vault)
        rel = "Claude/Organizer.md"
        digest = current.get(rel)
        if digest and manifest["hashes"].get(rel) != digest:
            manifest["hashes"][rel] = digest
            manifest["failures"].pop(rel, None)
            changed = True
    if changed:
        indexer.save_manifest(manifest, vault.root)


def _consolidate_step(
    vault: Vault, state: dict, consolidator, now: datetime, apply: bool
) -> dict:
    entities = consolidate_mod.gather_entities(vault)
    due, reason = should_consolidate(state, len(entities), now)
    if not due:
        return {"ran": False, "reason": reason, "proposed": []}
    if consolidator is None:
        consolidator = extractor_mod.consolidation_extractor()
    proposed = consolidate_mod.propose_merges(consolidator, entities)
    if apply:
        state["consolidation"] = {
            "entities_at_last_pass": len(entities),
            "last_pass": now.strftime(TS_FMT),
            "pending_proposals": proposed,
        }
    return {"ran": True, "reason": reason, "proposed": proposed}


def _step(result: dict, name: str, fn) -> None:
    try:
        result["steps"][name] = fn()
    except Exception as e:  # noqa: BLE001 — one step must not kill the sweep
        result["steps"][name] = None
        result["errors"][name] = f"{type(e).__name__}: {e}"


def _summarize_steps(steps: dict) -> dict:
    out: dict = {}
    idx = steps.get("index")
    out["index"] = idx if idx is None else {
        k: idx[k] for k in ("processed", "failed", "remaining", "pending")
        if k in idx
    }
    org = steps.get("organize")
    out["organize"] = org if org is None else {
        "moved": len(org["moved"]), "proposals": len(org["proposals"]),
        "skipped": len(org["skipped"]),
    }
    out["cache"] = steps.get("cache")
    con = steps.get("consolidate")
    out["consolidate"] = con if con is None else {
        "ran": con["ran"], "reason": con["reason"],
        "proposed": len(con["proposed"]),
    }
    return out


def run_sweep(
    vault: Vault,
    extractor=None,
    consolidator=None,
    embedder=None,
    apply: bool = True,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    state = load_state(vault)
    result: dict = {"steps": {}, "health": {}, "errors": {}, "applied": apply}

    if apply:
        if extractor is None:
            extractor = extractor_mod.extraction_extractor()
        _step(result, "index", lambda: _drain_index(vault, extractor))
    else:
        _step(result, "index", lambda: _index_preview(vault))

    if embedder is None:
        embedder = embeddings_mod.SentenceTransformerEmbedder()
    _step(result, "organize",
          lambda: organizer_mod.run_sweep(vault, embedder, apply=apply))
    if apply:
        _sync_manifest_after_moves(vault, result["steps"].get("organize"))

    _step(result, "cache", lambda: _ensure_cache(vault, result))

    _step(result, "consolidate",
          lambda: _consolidate_step(vault, state, consolidator, now, apply))

    result["health"] = run_health(
        vault, state, result["steps"].get("organize"),
        result["steps"].get("consolidate"), result["errors"],
    )

    if apply:
        state["last_sweep"] = now.strftime(TS_FMT)
        state["steps"] = _summarize_steps(result["steps"])
        state["health"] = result["health"]
        state["errors"] = dict(result["errors"])
        save_state(vault, state)
    return result
