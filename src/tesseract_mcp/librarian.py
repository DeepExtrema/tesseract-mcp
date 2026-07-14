"""The Librarian: one caretaker sweep over the vault's databases and files.

Thin orchestrator — index, organize, cache, throttled dry-run consolidation,
health checks, report. Owns no indexing/organizing logic of its own; see
docs/superpowers/specs/2026-07-09-librarian-design.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import blocking
from . import cache
from . import consolidate as consolidate_mod
from . import embeddings as embeddings_mod
from . import extractor as extractor_mod
from . import indexer
from . import organizer as organizer_mod
from . import sheets as sheets_mod
from .vault import Vault, VaultError

TS_FMT = "%Y-%m-%d %H:%M:%S"
STATE_FILE = "librarian_state.json"
BACKSTOP_MIN_INTERVAL_DAYS = 14
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
    # temp file + atomic replace: an interrupted write must never leave a
    # truncated state file behind (it would break status() and the throttle)
    target = state_path(vault)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _backstop_due(con: dict, now: datetime) -> bool:
    """The rolling backstop re-check runs at most once per interval. An
    absent marker means the clock has not started (it is stamped on the
    first apply sweep), NOT that a full re-check is immediately owed — the
    cold-start unchecked drain already covers every entity."""
    last = con.get("backstop_last_advance")
    if not last:
        return False
    return (now - datetime.strptime(last, TS_FMT)).days >= BACKSTOP_MIN_INTERVAL_DAYS


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
    try:
        rows = con.execute(
            "SELECT DISTINCT entity_path, note_path FROM mentions"
        ).fetchall()
    finally:
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
    try:
        db_count = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    finally:
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


def count_invalid_sheet_rows(vault: Vault) -> int:
    total = 0
    for _name, folder in sheets_mod.discover_sheets(vault).items():
        schema = sheets_mod.load_schema(vault, folder)
        for _rel, meta in sheets_mod.iter_rows(vault, schema):
            try:
                sheets_mod.validate_fields(
                    schema,
                    {k: v for k, v in meta.items()
                     if k not in sheets_mod.STANDARD_COLUMNS},
                    require_required=True,
                )
            except sheets_mod.SheetError:
                total += 1
    return total


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
        "invalid_sheet_rows": lambda: count_invalid_sheet_rows(vault),
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
    if totals and totals.get("remaining", 0) > 0:
        # surface as a step error (non-zero exit) instead of silently
        # reporting partial totals as a successful index
        raise RuntimeError(
            f"index did not drain after {MAX_INDEX_ROUNDS} rounds; "
            f"{totals['remaining']} notes still pending"
        )
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
    vault: Vault, state: dict, consolidator, now: datetime, apply: bool, embedder
) -> dict:
    entities = consolidate_mod.gather_entities(vault)
    if not entities:
        return {"ran": False, "reason": "no entities", "proposed": [],
                "skipped_batches": 0}
    con = state.get("consolidation") or {}
    checked_hash = dict(con.get("checked_hash") or {})
    cursor = con.get("cursor")
    backstop_due = _backstop_due(con, now)
    state_root = indexer.state_dir(vault.root)
    vectors = blocking.compute_entity_vectors(entities, state_root, embedder)
    slice_, new_cursor, used_backstop = blocking.select_slice(
        entities, checked_hash, cursor, blocking.SLICE_SIZE, backstop_due=backstop_due)
    if not slice_:
        if apply and "backstop_last_advance" not in con:
            # carried-over state can have every entity checked but no
            # marker; without a stamp here the backstop stays off until
            # some entity's identity text happens to change
            con["backstop_last_advance"] = now.strftime(TS_FMT)
            state["consolidation"] = con
        return {"ran": False, "reason": "nothing to check", "proposed": [],
                "skipped_batches": 0}
    if consolidator is None:
        consolidator = extractor_mod.consolidation_extractor()
    clusters = blocking.candidate_clusters(slice_, entities, vectors)
    batches = blocking.batch_clusters(clusters)
    proposed, skipped = consolidate_mod.adjudicate_batches(
        consolidator, batches, entities)
    reason = f"backstop ({len(slice_)})" if used_backstop else f"{len(slice_)} unchecked"
    if apply:
        for e in slice_:
            checked_hash[e["path"]] = blocking.identity_hash(e)
        con["checked_hash"] = checked_hash
        con["cursor"] = new_cursor
        con["pending_proposals"] = proposed
        con["last_pass"] = now.strftime(TS_FMT)
        if used_backstop or "backstop_last_advance" not in con:
            con["backstop_last_advance"] = now.strftime(TS_FMT)
        state["consolidation"] = con
    return {"ran": True, "reason": reason, "proposed": proposed,
            "skipped_batches": skipped}


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
        "skipped_batches": con.get("skipped_batches", 0),
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
    invalid_rows = h.get("invalid_sheet_rows", -1)
    invalid_n = invalid_rows if isinstance(invalid_rows, int) else -1
    lines.append(
        f"- health: stale_embeddings {stale_n} {mark(stale_n == 0)} | "
        f"manifest_drift {drift_n} {mark(drift_n == 0)} | "
        f"orphaned_entities {orph_n} {mark(orph_n == 0)} | "
        f"cache_consistency {mark(consistent)} | "
        f"invalid_sheet_rows {invalid_n} {mark(invalid_n == 0)} | "
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
          lambda: _consolidate_step(vault, state, consolidator, now, apply, embedder))

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
    # Windows consoles (and files a scheduled sweep redirects stdout into)
    # default to cp1252, which can't encode the ✓/⚠ glyphs in
    # format_report's health line. Reconfigure before any output so the
    # sweep's report never crashes the CLI on a completed sweep.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

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
    p = state_path(vault)
    if not p.exists():
        return {"status": "no sweep yet"}
    try:
        return load_state(vault)
    except json.JSONDecodeError as e:
        return {"status": "state file unreadable",
                "error": str(e), "path": str(p)}


if __name__ == "__main__":
    main()
