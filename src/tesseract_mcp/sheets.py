"""Structured sheets: schema-validated records in human folders.

A folder outside Claude/ becomes an agent-writable sheet iff the human
places a _schema.md in it; sheet_upsert is the only agent write path and
every write is validated. Spec:
docs/superpowers/specs/2026-07-11-structured-sheets-design.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

from .search import SKIP_DIRS, parse_frontmatter
from .vault import Vault

SCHEMA_FILE = "_schema.md"
STANDARD_COLUMNS = {"created", "agent", "project", "tags"}
COLUMN_TYPES = {"string", "enum", "date", "bool", "url", "number"}

_WS = re.compile(r"\s+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TRACKING = re.compile(r"^(utm_.*|ref|src|gh_src|lever-origin)$")


class SheetError(Exception):
    """Agent-actionable sheet failure; message names field/expected/got."""


@dataclass
class Column:
    type: str
    required: bool = False
    values: list[str] | None = None
    max_length: int | None = None


@dataclass
class Schema:
    name: str
    folder: str
    filename: str
    key: list[str]
    identity: list[str] = field(default_factory=list)
    columns: dict[str, Column] = field(default_factory=dict)


def load_schema(vault: Vault, folder_rel: str) -> Schema:
    path = vault.resolve(f"{folder_rel}/{SCHEMA_FILE}")
    if not path.is_file():
        raise SheetError(f"No {SCHEMA_FILE} in '{folder_rel}' — not a sheet.")
    meta = parse_frontmatter(path.read_text(encoding="utf-8"))
    for req in ("sheet", "filename", "key", "columns"):
        if req not in meta:
            raise SheetError(f"{folder_rel}/{SCHEMA_FILE}: missing '{req}'.")
    columns: dict[str, Column] = {}
    for name, spec in dict(meta["columns"]).items():
        if not isinstance(spec, dict) or spec.get("type") not in COLUMN_TYPES:
            raise SheetError(
                f"{folder_rel}/{SCHEMA_FILE}: column '{name}' has invalid type "
                f"'{(spec or {}).get('type')}' (allowed: {sorted(COLUMN_TYPES)})."
            )
        if spec["type"] == "enum" and not spec.get("values"):
            raise SheetError(
                f"{folder_rel}/{SCHEMA_FILE}: enum column '{name}' needs 'values'."
            )
        columns[name] = Column(
            type=spec["type"],
            required=bool(spec.get("required", False)),
            values=[str(v) for v in spec["values"]] if spec.get("values") else None,
            max_length=spec.get("max_length"),
        )
    for field_name in ("key", "identity"):
        if field_name == "identity" and field_name not in meta:
            continue
        value = meta[field_name] if field_name == "key" else meta.get("identity", [])
        if not isinstance(value, list):
            raise SheetError(
                f"{folder_rel}/{SCHEMA_FILE}: field '{field_name}' expected list, "
                f"got {value!r}."
            )
    return Schema(
        name=str(meta["sheet"]),
        folder=folder_rel,
        filename=str(meta["filename"]),
        key=[str(k) for k in meta["key"]],
        identity=[str(i) for i in meta.get("identity", [])],
        columns=columns,
    )


def discover_sheets(vault: Vault) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(vault.root.rglob(SCHEMA_FILE)):
        rel_parts = path.relative_to(vault.root).parts
        if SKIP_DIRS & set(rel_parts):
            continue
        folder = "/".join(rel_parts[:-1])
        schema = load_schema(vault, folder)
        found[schema.name] = folder
    return found


def get_schema(vault: Vault, sheet_name: str) -> Schema:
    registry = discover_sheets(vault)
    if sheet_name not in registry:
        raise SheetError(
            f"Unknown sheet '{sheet_name}'. Registered: {sorted(registry) or 'none'}."
        )
    return load_schema(vault, registry[sheet_name])


def is_sheet_folder(vault: Vault, folder_rel: str) -> bool:
    return vault.resolve(f"{folder_rel}/{SCHEMA_FILE}").is_file()


def norm_str(s: str) -> str:
    return _WS.sub(" ", str(s)).strip().casefold()


def normalize_link(url: str) -> str:
    parts = urlsplit(str(url).strip())
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(k)
    )
    return urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(),
        parts.path.rstrip("/") or "/", urlencode(query), "",
    ))


def _check_value(name: str, col: Column, value) -> None:
    if col.type == "enum":
        if value not in (col.values or []):
            raise SheetError(
                f"Field '{name}': expected one of {col.values}, got '{value}'.")
        return
    if col.type == "date":
        if not isinstance(value, str) or not _DATE.match(value):
            raise SheetError(
                f"Field '{name}': expected date YYYY-MM-DD, got '{value}'.")
        return
    if col.type == "bool":
        if not isinstance(value, bool):
            raise SheetError(f"Field '{name}': expected bool, got '{value!r}'.")
        return
    if col.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SheetError(f"Field '{name}': expected number, got '{value!r}'.")
        return
    # string / url
    if not isinstance(value, str):
        raise SheetError(f"Field '{name}': expected {col.type}, got '{value!r}'.")
    if col.max_length and len(value) > col.max_length:
        raise SheetError(
            f"Field '{name}': exceeds max_length {col.max_length} "
            f"({len(value)} chars).")


def validate_fields(schema: Schema, fields: dict, *, require_required: bool) -> dict:
    for name, value in fields.items():
        if name in STANDARD_COLUMNS:
            continue
        col = schema.columns.get(name)
        if col is None:
            raise SheetError(
                f"Field '{name}' is not declared in sheet '{schema.name}' "
                f"(columns: {sorted(schema.columns)}; standard: "
                f"{sorted(STANDARD_COLUMNS)}).")
        _check_value(name, col, value)
    if require_required:
        missing = [n for n, c in schema.columns.items()
                   if c.required and n not in fields]
        if missing:
            raise SheetError(f"Missing required field(s): {missing}.")
    return fields


_FILENAME_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def iter_rows(vault: Vault, schema: Schema) -> list[tuple[str, dict]]:
    folder = vault.resolve(schema.folder)
    rows: list[tuple[str, dict]] = []
    for path in sorted(folder.glob("*.md")):
        if path.name == SCHEMA_FILE or not path.is_file():
            continue
        rel = f"{schema.folder}/{path.name}"
        rows.append((rel, parse_frontmatter(
            path.read_text(encoding="utf-8", errors="ignore"))))
    return rows


def render_filename(schema: Schema, fields: dict) -> str:
    rendered = schema.filename.format(
        **{k: str(fields.get(k, "")) for k in schema.columns})
    rendered = _FILENAME_ILLEGAL.sub("-", rendered)
    rendered = _WS.sub(" ", rendered).strip()
    return rendered[:120].rstrip()


def _identity_value(schema: Schema, meta: dict) -> tuple[str, str] | None:
    """(column, normalized value) for the highest-priority identity present."""
    for col in schema.identity:
        raw = meta.get(col)
        if raw in (None, ""):
            continue
        if schema.columns.get(col) and schema.columns[col].type == "url":
            return col, normalize_link(raw)
        return col, norm_str(raw)
    return None


def match_row(vault: Vault, schema: Schema,
              fields: dict) -> tuple[str | None, dict]:
    candidates = [
        (rel, meta) for rel, meta in iter_rows(vault, schema)
        if all(norm_str(meta.get(k, "")) == norm_str(fields.get(k, ""))
               for k in schema.key)
    ]
    incoming = _identity_value(schema, fields)
    if incoming is not None:
        col, value = incoming
        for rel, meta in candidates:
            existing = _identity_value(schema, meta)
            if existing is not None and existing[1] == value:
                return rel, {}
        bare = [(rel, meta) for rel, meta in candidates
                if _identity_value(schema, meta) is None]
        if len(bare) == 1 and len(candidates) == 1:
            return bare[0][0], {col: fields[col]}
        return None, {}
    if len(candidates) == 1:
        return candidates[0][0], {}
    if not candidates:
        return None, {}
    raise SheetError(
        "Ambiguous match: multiple rows share this key — supply "
        f"{schema.identity} to disambiguate. Candidates: "
        f"{[rel for rel, _ in candidates]}")


def _split(text: str) -> tuple[list[str], str]:
    """(frontmatter lines without --- fences, body). Empty meta if none."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[4:end].splitlines()
            body = text[end + 4:].lstrip("\r").lstrip("\n")
            return fm, body
    return [], text


def _yaml_line(key: str, value) -> str:
    if isinstance(value, str) and _DATE.match(value):
        return f"{key}: {value}"
    return yaml.safe_dump({key: value}, sort_keys=False).strip()


def _patch_lines(fm_lines: list[str], updates: dict) -> list[str]:
    """Replace top-level 'key: value' lines; append keys not present."""
    done: set[str] = set()
    out: list[str] = []
    for line in fm_lines:
        m = re.match(r"^([A-Za-z_][\w-]*):", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(_yaml_line(key, updates[key]))
            done.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in done:
            out.append(_yaml_line(key, value))
    return out


def _log_line(field_changes: dict, agent: str, now: datetime) -> str | None:
    st = field_changes.get("status")
    if not st:
        return None
    frm = st["from"] if st["from"] is not None else "(new)"
    return f"- {now:%Y-%m-%d} status: {frm} → {st['to']} (agent: {agent})"


def _append_log(body: str, line: str) -> str:
    if "## Log" not in body:
        return body.rstrip("\n") + "\n\n## Log\n" + line + "\n"
    return body.rstrip("\n") + "\n" + line + "\n"


def upsert(vault: Vault, sheet: str, fields: dict, body: str | None = None,
           agent: str = "claude", now: datetime | None = None) -> dict:
    now = now or datetime.now()
    schema = get_schema(vault, sheet)
    rel, backfill = match_row(vault, schema, dict(fields))
    merged = {**fields, **backfill}
    if rel is None:
        validate_fields(schema, merged, require_required=True)
        stem = render_filename(schema, merged)
        candidate, n = stem, 2
        while vault.resolve(f"{schema.folder}/{candidate}.md").exists():
            candidate = f"{stem} {n}"
            n += 1
        rel = f"{schema.folder}/{candidate}.md"
        meta = {**merged,
                "created": f"{now:%Y-%m-%d %H:%M}", "agent": agent}
        fm = yaml.safe_dump(meta, sort_keys=False)
        changed = {k: {"from": None, "to": v} for k, v in merged.items()}
        text_body = body if body is not None else ""
        log = _log_line(changed, agent, now)
        if log:
            text_body = _append_log(text_body, log)
        vault.write(rel, f"---\n{fm}---\n\n{text_body}",
                    confirm_outside_claude=True)
        return {"result": "created", "path": rel, "changed": changed}

    validate_fields(schema, merged, require_required=False)
    text = vault.read(rel)
    fm_lines, note_body = _split(text)
    old = parse_frontmatter(text)
    changed = {k: {"from": old.get(k), "to": v}
               for k, v in merged.items() if old.get(k) != v}
    if not changed:
        return {"result": "updated", "path": rel, "changed": {}}
    new_fm = _patch_lines(fm_lines, {k: v["to"] for k, v in changed.items()})
    new_fm = _patch_lines(new_fm, {"agent": agent})
    log = _log_line(changed, agent, now)
    if log:
        note_body = _append_log(note_body, log)
    vault.write(rel, "---\n" + "\n".join(new_fm) + "\n---\n\n" + note_body,
                overwrite=True, confirm_outside_claude=True)
    return {"result": "updated", "path": rel, "changed": changed}


_OPS = {"eq", "ne", "lt", "lte", "gt", "gte", "contains", "missing", "in", "nin"}
_ORDERED_TYPES = {"date", "number"}


def _matches(col_type: str | None, actual, op: str, expected) -> bool:
    if op == "missing":
        return (actual in (None, "")) is bool(expected)
    if actual in (None, ""):
        return op in ("ne", "nin")
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "nin":
        return actual not in expected
    if op == "contains":
        return str(expected).casefold() in str(actual).casefold()
    if col_type == "date":
        if hasattr(actual, "isoformat"):
            actual = actual.isoformat()
        if hasattr(expected, "isoformat"):
            expected = expected.isoformat()
    return {"lt": actual < expected, "lte": actual <= expected,
            "gt": actual > expected, "gte": actual >= expected}[op]


def query(vault: Vault, sheet: str, filters: dict | None = None,
          sort: dict | None = None, limit: int = 50) -> list[dict]:
    schema = get_schema(vault, sheet)
    filters = filters or {}
    for col, ops in filters.items():
        col_type = schema.columns[col].type if col in schema.columns else None
        for op in ops:
            if op not in _OPS:
                raise SheetError(f"Unknown operator '{op}' (allowed: {sorted(_OPS)}).")
            if op in ("lt", "lte", "gt", "gte") and col_type not in _ORDERED_TYPES:
                raise SheetError(
                    f"Column '{col}' ({col_type}) does not support ordering "
                    f"operators; only {sorted(_ORDERED_TYPES)} columns do.")
    out = []
    for rel, meta in iter_rows(vault, schema):
        ok = all(
            _matches(schema.columns[col].type if col in schema.columns else None,
                     meta.get(col), op, expected)
            for col, ops in filters.items() for op, expected in ops.items())
        if ok:
            out.append({"path": rel, **meta})
    if sort:
        by, desc = sort.get("by"), sort.get("dir") == "desc"
        present = [r for r in out if r.get(by) not in (None, "")]
        absent = [r for r in out if r.get(by) in (None, "")]
        present.sort(key=lambda r: r[by], reverse=desc)
        out = present + absent
    return out[:limit]


def schema_info(vault: Vault, sheet: str | None = None) -> dict:
    if sheet is None:
        registry = discover_sheets(vault)
        return {name: {"folder": folder,
                       "rows": len(iter_rows(vault, load_schema(vault, folder)))}
                for name, folder in registry.items()}
    s = get_schema(vault, sheet)
    path = vault.resolve(f"{s.folder}/{SCHEMA_FILE}")
    _, instructions = _split(path.read_text(encoding="utf-8"))
    return {"sheet": s.name, "folder": s.folder, "filename": s.filename,
            "key": s.key, "identity": s.identity,
            "columns": {n: vars(c) for n, c in s.columns.items()},
            "instructions": instructions.strip()}


def check(vault: Vault) -> int:
    report: dict = {"sheets": {}, "clean": True}
    for name, folder in discover_sheets(vault).items():
        schema = load_schema(vault, folder)
        invalid, seen, dupes = [], {}, []
        for rel, meta in iter_rows(vault, schema):
            try:
                validate_fields(schema, {k: v for k, v in meta.items()
                                         if k not in STANDARD_COLUMNS},
                                require_required=True)
            except SheetError as e:
                invalid.append({"path": rel, "error": str(e)})
            key = tuple(norm_str(meta.get(k, "")) for k in schema.key)
            ident = _identity_value(schema, meta)
            full = (key, ident[1] if ident else None)
            if full in seen:
                dupes.append({"paths": [seen[full], rel]})
            else:
                seen[full] = rel
        report["sheets"][name] = {
            "rows": len(iter_rows(vault, schema)),
            "invalid": invalid, "duplicates": dupes}
        if invalid or dupes:
            report["clean"] = False
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["clean"] else 1


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Structured sheets: validate all rows against schemas.")
    parser.add_argument("vault")
    parser.add_argument("--check", action="store_true", required=True)
    args = parser.parse_args()
    raise SystemExit(check(Vault(args.vault)))


if __name__ == "__main__":
    main()
