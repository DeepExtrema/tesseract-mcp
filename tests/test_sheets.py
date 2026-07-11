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


def _row(vault_dir, name, meta_yaml, body="Body.\n"):
    p = vault_dir / "Job Search" / "Applications" / f"{name}.md"
    p.write_text(f"---\n{meta_yaml}---\n\n{body}", encoding="utf-8")
    return p


def test_iter_rows_direct_children_only(sheet_vault, vault_dir):
    _row(vault_dir, "Adobe - SWE", "company: Adobe\nrole: SWE\nstatus: Saved\n")
    sub = vault_dir / "Job Search" / "Applications" / "Archive"
    sub.mkdir()
    (sub / "Old.md").write_text("---\ncompany: Old\n---\n", encoding="utf-8")
    rows = sheets.iter_rows(sheet_vault, sheets.get_schema(sheet_vault, "jobs"))
    assert [r[0] for r in rows] == ["Job Search/Applications/Adobe - SWE.md"]


def test_render_filename_sanitizes(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    out = sheets.render_filename(s, {"company": "A/B: Corp?", "role": "ML|Eng"})
    assert out == "A-B- Corp- - ML-Eng"
    long = sheets.render_filename(s, {"company": "C" * 200, "role": "R"})
    assert len(long) <= 120


def test_match_by_req_id(sheet_vault, vault_dir):
    _row(vault_dir, "Adobe - SWE R1",
         "company: Adobe\nrole: SWE\nreq_id: R1\nstatus: Saved\n")
    _row(vault_dir, "Adobe - SWE R2",
         "company: Adobe\nrole: SWE\nreq_id: R2\nstatus: Saved\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    rel, backfill = sheets.match_row(
        sheet_vault, s, {"company": "adobe", "role": "SWE", "req_id": "R2"})
    assert rel == "Job Search/Applications/Adobe - SWE R2.md"
    assert backfill == {}


def test_match_backfills_single_candidate(sheet_vault, vault_dir):
    _row(vault_dir, "Acme - DS", "company: Acme\nrole: DS\nstatus: Saved\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    rel, backfill = sheets.match_row(
        sheet_vault, s, {"company": "Acme", "role": "DS", "req_id": "R9"})
    assert rel == "Job Search/Applications/Acme - DS.md"
    assert backfill == {"req_id": "R9"}


def test_match_new_posting_creates(sheet_vault, vault_dir):
    _row(vault_dir, "Acme - DS R1",
         "company: Acme\nrole: DS\nreq_id: R1\nstatus: Saved\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    rel, _ = sheets.match_row(
        sheet_vault, s, {"company": "Acme", "role": "DS", "req_id": "R2"})
    assert rel is None  # different posting -> new row


def test_match_ambiguous_errors_with_candidates(sheet_vault, vault_dir):
    _row(vault_dir, "Acme - DS R1",
         "company: Acme\nrole: DS\nreq_id: R1\nstatus: Saved\n")
    _row(vault_dir, "Acme - DS R2",
         "company: Acme\nrole: DS\nreq_id: R2\nstatus: Saved\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    with pytest.raises(SheetError, match="R1"):
        sheets.match_row(sheet_vault, s, {"company": "Acme", "role": "DS"})


def test_match_job_link_normalized(sheet_vault, vault_dir):
    _row(vault_dir, "Beta - MLE",
         "company: Beta\nrole: MLE\nstatus: Saved\n"
         "job_link: https://jobs.beta.com/x?utm_source=a\n")
    s = sheets.get_schema(sheet_vault, "jobs")
    rel, _ = sheets.match_row(sheet_vault, s, {
        "company": "Beta", "role": "MLE",
        "job_link": "HTTPS://JOBS.BETA.COM/x/"})
    assert rel == "Job Search/Applications/Beta - MLE.md"


def test_upsert_creates_with_log(sheet_vault):
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Saved"},
                        agent="cowork")
    assert out["result"] == "created"
    text = sheet_vault.read(out["path"])
    assert "company: Nova" in text and "## Log" in text
    assert "status: (new) → Saved (agent: cowork)" in text
    assert "agent: cowork" in text and "created:" in text


def test_upsert_patch_preserves_untouched_bytes(sheet_vault, vault_dir):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Saved\n"
             "channel: LinkedIn   # via referral\n",
             body="Story para.\n\n## Log\n- 2026-07-10 status: (new) → Saved (agent: claude)\n")
    before = p.read_text(encoding="utf-8")
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Applied",
                         "date_applied": "2026-07-11"})
    assert out["result"] == "updated"
    assert out["changed"]["status"] == {"from": "Saved", "to": "Applied"}
    after = p.read_text(encoding="utf-8")
    assert "channel: LinkedIn   # via referral" in after   # untouched line intact
    assert "date_applied: 2026-07-11" in after             # new field appended
    assert "Story para.\n" in after                        # body intact
    assert after.count("## Log") == 1
    assert "status: Saved → Applied" in after


def test_upsert_noop_does_not_touch_file(sheet_vault, vault_dir):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Saved\n")
    mtime = p.stat().st_mtime_ns
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Saved"})
    assert out["result"] == "updated" and out["changed"] == {}
    assert p.stat().st_mtime_ns == mtime


def test_upsert_refuses_undeclared_and_unknown_sheet(sheet_vault):
    with pytest.raises(SheetError, match="recruiter"):
        sheets.upsert(sheet_vault, "jobs",
                      {"company": "N", "role": "R", "status": "Saved",
                       "recruiter": "Bob"})
    with pytest.raises(SheetError, match="Unknown sheet"):
        sheets.upsert(sheet_vault, "subscriptions", {"company": "N"})


def test_raw_write_still_confirm_gated(sheet_vault):
    from tesseract_mcp.vault import VaultError
    with pytest.raises(VaultError, match="outside Claude/"):
        sheet_vault.write("Job Search/Applications/Sneak.md", "hi")


def test_filename_collision_gets_suffix(sheet_vault, vault_dir):
    _row(vault_dir, "Nova - MLE",
         "company: Nova\nrole: MLE\nreq_id: R1\nstatus: Saved\n")
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "req_id": "R2",
                         "status": "Saved"})
    assert out["result"] == "created"
    assert out["path"].endswith("Nova - MLE 2.md")


@pytest.fixture
def populated(sheet_vault, vault_dir):
    _row(vault_dir, "A - R1", "company: A\nrole: R1\nstatus: Saved\n")
    _row(vault_dir, "B - R2",
         "company: B\nrole: R2\nstatus: Applied\nnext_follow_up: 2026-07-01\n")
    _row(vault_dir, "C - R3",
         "company: C\nrole: R3\nstatus: Rejected\nnext_follow_up: 2026-07-05\n")
    return sheet_vault


def test_query_follow_ups_due(populated):
    rows = sheets.query(populated, "jobs", {
        "next_follow_up": {"lte": "2026-07-11"},
        "status": {"nin": ["Rejected", "Ghosted", "Withdrawn"]},
    })
    assert [r["company"] for r in rows] == ["B"]


def test_query_ops_and_sort(populated):
    assert len(sheets.query(populated, "jobs", {"status": {"eq": "Saved"}})) == 1
    assert len(sheets.query(populated, "jobs",
                            {"next_follow_up": {"missing": True}})) == 1
    rows = sheets.query(populated, "jobs", {},
                        sort={"by": "next_follow_up", "dir": "desc"})
    assert rows[0]["company"] == "C" and rows[-1]["company"] == "A"


def test_query_rejects_bad_op_and_untyped_ordering(populated):
    with pytest.raises(SheetError, match="Unknown operator"):
        sheets.query(populated, "jobs", {"status": {"like": "x"}})
    with pytest.raises(SheetError, match="ordering"):
        sheets.query(populated, "jobs", {"company": {"lt": "M"}})


def test_query_excludes_schema_and_respects_limit(populated):
    rows = sheets.query(populated, "jobs", {}, limit=2)
    assert len(rows) == 2
    assert all(not r["path"].endswith("_schema.md") for r in rows)


def test_schema_info_lists_and_details(populated):
    listing = sheets.schema_info(populated)
    assert listing["jobs"]["rows"] == 3
    detail = sheets.schema_info(populated, "jobs")
    assert detail["columns"]["status"]["values"][0] == "Saved"
    assert "Never delete rows" in detail["instructions"]


def test_check_reports_drift_and_dupes(sheet_vault, vault_dir, capsys):
    _row(vault_dir, "Ok - Row", "company: Ok\nrole: Row\nstatus: Saved\n")
    _row(vault_dir, "Bad - Row",
         "company: Bad\nrole: Row\nstage: applied\nstatus: Saved\n")
    _row(vault_dir, "Dup - A", "company: Dup\nrole: A\nstatus: Saved\n")
    _row(vault_dir, "Dup - A2", "company: Dup\nrole: A\nstatus: Saved\n")
    rc = sheets.check(sheet_vault)
    out = capsys.readouterr().out
    assert rc == 1
    assert "stage" in out and "Dup" in out and '"clean": false' in out


def test_check_clean_vault_exits_zero(sheet_vault, vault_dir, capsys):
    _row(vault_dir, "Ok - Row", "company: Ok\nrole: Row\nstatus: Saved\n")
    assert sheets.check(sheet_vault) == 0
