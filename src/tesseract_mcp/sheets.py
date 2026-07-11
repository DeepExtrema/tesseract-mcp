"""Structured sheets: schema-validated records in human folders.

A folder outside Claude/ becomes an agent-writable sheet iff the human
places a _schema.md in it; sheet_upsert is the only agent write path and
every write is validated. Spec:
docs/superpowers/specs/2026-07-11-structured-sheets-design.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
