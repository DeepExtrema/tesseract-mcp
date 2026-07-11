import pytest

from tesseract_mcp import sheets
from tesseract_mcp.sheets import SheetError
from tesseract_mcp.vault import Vault

JOBS_SCHEMA = """---
sheet: jobs
filename: "{company} - {role}"
key: [company, role]
identity: [req_id, job_link]
columns:
  company: {type: string, required: true, max_length: 120}
  role: {type: string, required: true, max_length: 160}
  req_id: {type: string, max_length: 80}
  status:
    type: enum
    required: true
    values: [Saved, Applied, OA, Interview, Offer, Rejected, Ghosted, Withdrawn]
  date_applied: {type: date}
  sponsorship_required: {type: bool}
  job_link: {type: url, max_length: 500}
  next_follow_up: {type: date}
---

One note per posting. Never delete rows.
"""


@pytest.fixture
def sheet_vault(vault_dir):
    folder = vault_dir / "Job Search" / "Applications"
    folder.mkdir(parents=True)
    (folder / "_schema.md").write_text(JOBS_SCHEMA, encoding="utf-8")
    return Vault(vault_dir)


def test_load_schema_parses_columns(sheet_vault):
    s = sheets.load_schema(sheet_vault, "Job Search/Applications")
    assert s.name == "jobs"
    assert s.key == ["company", "role"]
    assert s.identity == ["req_id", "job_link"]
    assert s.columns["status"].type == "enum"
    assert "Ghosted" in s.columns["status"].values
    assert s.columns["company"].required is True
    assert s.columns["company"].max_length == 120


def test_discover_and_get_schema(sheet_vault):
    assert sheets.discover_sheets(sheet_vault) == {"jobs": "Job Search/Applications"}
    assert sheets.get_schema(sheet_vault, "jobs").folder == "Job Search/Applications"
    with pytest.raises(SheetError, match="jobs"):
        sheets.get_schema(sheet_vault, "nope")


def test_is_sheet_folder(sheet_vault):
    assert sheets.is_sheet_folder(sheet_vault, "Job Search/Applications") is True
    assert sheets.is_sheet_folder(sheet_vault, "Projects") is False


def test_malformed_schema_refuses(sheet_vault, vault_dir):
    (vault_dir / "Job Search" / "Applications" / "_schema.md").write_text(
        "---\nsheet: jobs\nfilename: \"{x}\"\nkey: [x]\ncolumns:\n  x: {type: alien}\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(SheetError, match="alien"):
        sheets.load_schema(sheet_vault, "Job Search/Applications")


def test_scalar_key_raises(sheet_vault, vault_dir):
    (vault_dir / "Job Search" / "Applications" / "_schema.md").write_text(
        "---\nsheet: jobs\nfilename: \"{company}\"\nkey: company\ncolumns:\n"
        "  company: {type: string, required: true}\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(SheetError, match="key"):
        sheets.load_schema(sheet_vault, "Job Search/Applications")


def test_norm_str_and_link():
    assert sheets.norm_str("  Adobe   Inc ") == sheets.norm_str("adobe inc")
    a = sheets.normalize_link("HTTPS://Jobs.Example.com/p/123/?utm_source=li&x=1#frag")
    b = sheets.normalize_link("https://jobs.example.com/p/123?x=1")
    assert a == b


VALID = {"company": "Adobe", "role": "SWE Intern", "status": "Saved"}


def test_validate_accepts_valid(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    assert sheets.validate_fields(s, VALID, require_required=True) == VALID


@pytest.mark.parametrize("fields,fragment", [
    ({**VALID, "recruiter": "Bob"}, "recruiter"),          # undeclared
    ({**VALID, "status": "applied"}, "applied"),            # bad enum (case-sensitive)
    ({**VALID, "date_applied": "07/11/2026"}, "YYYY-MM-DD"),
    ({**VALID, "sponsorship_required": "yes"}, "bool"),
    ({**VALID, "company": "x" * 121}, "max_length"),
    ({"company": "Adobe", "status": "Saved"}, "role"),      # missing required
])
def test_validate_rejects(sheet_vault, fields, fragment):
    s = sheets.get_schema(sheet_vault, "jobs")
    with pytest.raises(SheetError, match=fragment):
        sheets.validate_fields(s, fields, require_required=True)


def test_validate_patch_mode_skips_required(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    out = sheets.validate_fields(s, {"status": "Applied"}, require_required=False)
    assert out == {"status": "Applied"}


def test_standard_metadata_always_allowed(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    sheets.validate_fields(s, {**VALID, "tags": ["job"]}, require_required=True)
