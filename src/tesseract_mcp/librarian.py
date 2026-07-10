"""The Librarian: one caretaker sweep over the vault's databases and files.

Thin orchestrator — index, organize, cache, throttled dry-run consolidation,
health checks, report. Owns no indexing/organizing logic of its own; see
docs/superpowers/specs/2026-07-09-librarian-design.md.
"""

from __future__ import annotations

import argparse
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
from .vault import Vault, VaultError

TS_FMT = "%Y-%m-%d %H:%M:%S"
STATE_FILE = "librarian_state.json"
CONSOLIDATE_MIN_NEW_ENTITIES = 15
CONSOLIDATE_MAX_AGE_DAYS = 14
LIBRARIAN_NOTE = "Claude/Librarian.md"
REPORT_MAX_SWEEPS = 30
_NOTE_SEED = ("# Librarian\n\nCaretaker sweep reports (newest last). "
              "See constitution.\n")
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


def format_report(result: dict, now: datetime) -> str:
    steps = result["steps"]
    lines = [f"## Sweep {now.strftime('%Y-%m-%d %H:%M')}\n"]

    idx = steps.get("index")
    if idx is None:
        lines.append("- index: FAILED\n")
    elif "pending" in idx:
        lines.append(f"- index: {idx['pending']} pending (dry-run)\n")
    else:
        lines.append(f"- index: processed {idx['processed']}, "
                     f"failed {idx['failed']}, remaining {idx['remaining']}\n")

    org = steps.get("organize")
    if org is None:
        lines.append("- organize: FAILED\n")
    else:
        lines.append(f"- organize: moved {len(org['moved'])}, "
                     f"proposals {len(org['proposals'])}, "
                     f"skipped {len(org['skipped'])}\n")

    cch = steps.get("cache")
    if cch is None:
        lines.append("- cache: FAILED\n")
    elif cch["rebuilt"]:
        lines.append(f"- cache: rebuilt ({cch['by']})\n")
    else:
        lines.append("- cache: fresh, no rebuild needed\n")

    con = steps.get("consolidate")
    if con is None:
        lines.append("- consolidate: FAILED\n")
    elif con["ran"]:
        lines.append(f"- consolidate: ran ({con['reason']}) — "
                     f"{len(con['proposed'])} merge proposals\n")
    else:
        lines.append(f"- consolidate: skipped ({con['reason']})\n")

    h = result["health"]

    def mark(ok: bool) -> str:
        return "✓" if ok else "⚠"

    stale = h.get("stale_embeddings", -1)
    stale_n = stale if isinstance(stale, int) else -1
    drift = h.get("manifest_drift", {})
    drift_n = (len(drift.get("deleted_but_tracked", []))
               + len(drift.get("present_but_untracked", []))
               if isinstance(drift, dict) and "error" not in drift else -1)
    orphans = h.get("orphaned_entities", [])
    orph_n = len(orphans) if isinstance(orphans, list) else -1
    cc = h.get("cache_consistency", {})
    consistent = isinstance(cc, dict) and cc.get("consistent", False)
    lines.append(
        f"- health: stale_embeddings {stale_n} {mark(stale_n == 0)} | "
        f"manifest_drift {drift_n} {mark(drift_n == 0)} | "
        f"orphaned_entities {orph_n} {mark(orph_n == 0)} | "
        f"cache_consistency {mark(consistent)} | "
        f"pending_proposals {h.get('pending_proposals', 0)}\n")

    errs = result["errors"]
    if errs:
        lines.append("- errors: "
                     + ", ".join(f"{k}: {v}" for k, v in errs.items()) + "\n")
    else:
        lines.append("- errors: none\n")
    return "".join(lines)


def write_report(vault: Vault, section: str) -> None:
    try:
        text = vault.read(LIBRARIAN_NOTE)
    except VaultError:
        text = _NOTE_SEED
    text = text.rstrip("\n") + "\n\n" + section
    header, *sweeps = text.split("\n## Sweep ")
    if len(sweeps) > REPORT_MAX_SWEEPS:
        sweeps = sweeps[-REPORT_MAX_SWEEPS:]
    text = header + "".join("\n## Sweep " + s for s in sweeps)
    if not text.endswith("\n"):
        text += "\n"
    vault.write(LIBRARIAN_NOTE, text, overwrite=True)


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
        write_report(vault, format_report(result, now))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Librarian caretaker sweep: index, organize, cache, "
                    "consolidation proposals, health report.")
    parser.add_argument("vault", help="Path to the Obsidian vault root")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report without writing anything")
    args = parser.parse_args()
    result = run_sweep(Vault(args.vault), apply=not args.dry_run)
    if args.dry_run:
        print(format_report(result, datetime.now()))
    print(json.dumps(result, indent=2, default=str))
    if result["errors"]:
        raise SystemExit(1)


def status(vault: Vault) -> dict:
    """Read-only view of the last sweep for the librarian_status tool."""
    if not state_path(vault).exists():
        return {"status": "no sweep yet"}
    return load_state(vault)


if __name__ == "__main__":
    main()
