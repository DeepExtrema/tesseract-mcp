import pytest
import yaml

from tesseract_mcp import sheets
from tesseract_mcp.search import parse_frontmatter
from tesseract_mcp.sheets import SheetError
from tesseract_mcp.vault import Vault, VaultError

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


# ---------------------------------------------------------------------------
# Review fix wave: Critical 1 — _patch_lines must not orphan continuation
# lines of a block-style value (e.g. tags) it is replacing.
# ---------------------------------------------------------------------------

def test_patch_lines_consumes_continuation_of_replaced_key():
    fm_lines = [
        "company: Nova",
        "role: MLE",
        "status: Saved",
        "tags:",
        "  - old",
        "  - stale",
    ]
    out = sheets._patch_lines(fm_lines, {"tags": ["new", "shiny"]})
    text = "\n".join(out)
    assert "- old" not in text
    assert "- stale" not in text
    parsed = yaml.safe_load(text)
    assert parsed["tags"] == ["new", "shiny"]
    assert parsed["status"] == "Saved"


def test_upsert_patch_round_trips_block_style_tags(sheet_vault, vault_dir):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Saved\n"
             "tags:\n  - old\n  - stale\n")
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Applied",
                         "tags": ["new", "shiny"]})
    assert out["result"] == "updated"
    after = p.read_text(encoding="utf-8")
    parsed = parse_frontmatter(after)
    assert parsed["tags"] == ["new", "shiny"]
    assert parsed["status"] == "Applied"
    assert parsed["company"] == "Nova"  # untouched fields survived the patch


# ---------------------------------------------------------------------------
# Review fix wave: Critical 2 — Claude/ subtree can never register a sheet;
# duplicate sheet names must raise, not silently last-write-wins.
# ---------------------------------------------------------------------------

def test_discover_sheets_ignores_claude_planted_schema(vault_dir):
    # "Applications" (real, top-level) sorts alphabetically BEFORE "Claude/..."
    # ('A' < 'C'), so rglob visits the real folder first and the Claude-planted
    # duplicate is processed second — the exact ordering a naive last-write-wins
    # registry would let a Claude-planted schema shadow. Regressing to that
    # sort-order-dependent behavior is the bug this test pins.
    real = vault_dir / "Applications"
    real.mkdir()
    (real / "_schema.md").write_text(JOBS_SCHEMA, encoding="utf-8")
    claude_sheet = vault_dir / "Claude" / "Fake"
    claude_sheet.mkdir(parents=True)
    (claude_sheet / "_schema.md").write_text(
        "---\nsheet: jobs\nfilename: \"{x}\"\nkey: [x]\ncolumns:\n"
        "  x: {type: string}\n---\n",
        encoding="utf-8",
    )
    v = Vault(vault_dir)
    # The real sheet must win — a Claude-planted schema must never shadow it,
    # even when it would sort later and "win" a naive last-write-wins registry.
    assert sheets.discover_sheets(v) == {"jobs": "Applications"}


def test_discover_sheets_raises_on_duplicate_names(sheet_vault, vault_dir):
    other = vault_dir / "Other Folder"
    other.mkdir()
    (other / "_schema.md").write_text(
        "---\nsheet: jobs\nfilename: \"{x}\"\nkey: [x]\ncolumns:\n"
        "  x: {type: string}\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(SheetError, match="Duplicate sheet name"):
        sheets.discover_sheets(sheet_vault)


# ---------------------------------------------------------------------------
# Review fix wave: Critical 3 — dates must normalize to isoformat strings
# before any comparison (changed-calc and _matches), so unquoted YAML dates
# don't cause false "changed" churn or missed eq/query hits.
# ---------------------------------------------------------------------------

def test_upsert_noop_with_unquoted_date_field(sheet_vault, vault_dir):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Applied\n"
             "date_applied: 2026-07-11\n")
    mtime = p.stat().st_mtime_ns
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Applied",
                         "date_applied": "2026-07-11"})
    assert out["result"] == "updated"
    assert out["changed"] == {}
    assert p.stat().st_mtime_ns == mtime


def test_query_eq_matches_unquoted_date(sheet_vault, vault_dir):
    _row(vault_dir, "Nova - MLE",
         "company: Nova\nrole: MLE\nstatus: Applied\n"
         "date_applied: 2026-07-11\n")
    rows = sheets.query(sheet_vault, "jobs",
                        {"date_applied": {"eq": "2026-07-11"}})
    assert len(rows) == 1
    assert rows[0]["company"] == "Nova"


# ---------------------------------------------------------------------------
# Review fix wave: Important 4 — one malformed _schema.md must not brick
# every sheet tool; bad folders are skipped and surfaced in schema_info.
# ---------------------------------------------------------------------------

def test_bad_schema_in_one_folder_does_not_brick_others(sheet_vault, vault_dir):
    bad_folder = vault_dir / "Job Search" / "Bad"
    bad_folder.mkdir()
    (bad_folder / "_schema.md").write_text(
        "---\nsheet: broken\nfilename: \"{x}\"\nkey: [x]\ncolumns:\n"
        "  x: {type: alien}\n---\n",
        encoding="utf-8",
    )
    assert sheets.discover_sheets(sheet_vault) == {"jobs": "Job Search/Applications"}
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Saved"})
    assert out["result"] == "created"
    listing = sheets.schema_info(sheet_vault)
    assert "jobs" in listing
    assert "invalid" in listing
    assert listing["invalid"][0]["folder"] == "Job Search/Bad"


# ---------------------------------------------------------------------------
# Review fix wave: Important 6 — created is server-stamped on create only;
# a caller-supplied created/agent field must not leak through on update.
# ---------------------------------------------------------------------------

def test_upsert_update_ignores_caller_supplied_created(sheet_vault, vault_dir):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Saved\n"
             "created: '2020-01-01 00:00'\n")
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Applied",
                         "created": "2099-01-01 00:00"})
    assert "created" not in out["changed"]
    after = p.read_text(encoding="utf-8")
    assert "2020-01-01" in after
    assert "2099-01-01" not in after


def test_upsert_update_caller_supplied_agent_field_does_not_leak_to_changed(
    sheet_vault, vault_dir
):
    _row(vault_dir, "Nova - MLE",
         "company: Nova\nrole: MLE\nstatus: Saved\nagent: claude\n")
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "Nova", "role": "MLE", "status": "Applied",
                         "agent": "sneaky"},
                        agent="cowork")
    assert "agent" not in out["changed"]
    after = sheet_vault.read(out["path"])
    assert "agent: cowork" in after
    assert "sneaky" not in after


# ---------------------------------------------------------------------------
# Review fix wave: Important 7 — url columns must reject arbitrary prose.
# ---------------------------------------------------------------------------

def test_url_rejects_prose(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    with pytest.raises(SheetError, match="URL"):
        sheets.validate_fields(
            s, {**VALID, "job_link": "Apply via the careers page, ask for Jane"},
            require_required=True,
        )


# ---------------------------------------------------------------------------
# Review fix wave: Important 9 — body may only be supplied on create; an
# update path must reject it rather than silently dropping it.
# ---------------------------------------------------------------------------

def test_upsert_update_rejects_body(sheet_vault, vault_dir):
    _row(vault_dir, "Nova - MLE", "company: Nova\nrole: MLE\nstatus: Saved\n")
    with pytest.raises(SheetError, match="body"):
        sheets.upsert(sheet_vault, "jobs",
                      {"company": "Nova", "role": "MLE", "status": "Applied"},
                      body="New story paragraph.")


# ---------------------------------------------------------------------------
# Review fix wave: Important 10 — '## Log' heading match must be
# line-anchored (not substring, so "## Logistics" is never mistaken for
# it), and the line must land at the end of the actual Log section, not
# unconditionally at the end of the body.
# ---------------------------------------------------------------------------

def test_upsert_log_line_lands_under_log_section_before_later_sections(
    sheet_vault, vault_dir
):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Saved\n",
             body="Story.\n\n## Log\n"
                  "- 2026-07-10 status: (new) → Saved (agent: claude)\n\n"
                  "## Notes\nSome notes here.\n")
    sheets.upsert(sheet_vault, "jobs",
                 {"company": "Nova", "role": "MLE", "status": "Applied"})
    after = p.read_text(encoding="utf-8")
    log_idx = after.index("## Log")
    notes_idx = after.index("## Notes")
    new_line_idx = after.index("Saved → Applied")
    assert log_idx < new_line_idx < notes_idx


def test_upsert_log_heading_not_confused_with_logistics(sheet_vault, vault_dir):
    p = _row(vault_dir, "Nova - MLE",
             "company: Nova\nrole: MLE\nstatus: Saved\n",
             body="Intro.\n\n## Logistics\nAddress and travel details.\n")
    sheets.upsert(sheet_vault, "jobs",
                 {"company": "Nova", "role": "MLE", "status": "Applied"})
    after = p.read_text(encoding="utf-8")
    assert after.count("## Log") == 2          # "## Logistics" + new "## Log"
    assert "## Logistics\nAddress and travel details." in after
    logistics_idx = after.index("## Logistics")
    new_log_idx = after.rindex("## Log")
    assert new_log_idx > logistics_idx
    assert "Saved → Applied" in after


# ---------------------------------------------------------------------------
# Review fix wave: Important 11 — spec-promised quarantine tests that were
# missing: (a) agent _schema.md write outside Claude/ blocked, (b) filename
# rendering cannot escape the sheet folder, (c) Claude-planted schema
# ignored (covered above by test_discover_sheets_ignores_claude_planted_schema,
# repeated here against the raw write path per the finding).
# ---------------------------------------------------------------------------

def test_schema_md_write_outside_claude_blocked(sheet_vault):
    with pytest.raises(VaultError, match="outside Claude/"):
        sheet_vault.write("Projects/_schema.md", "sheet: evil\n")


def test_upsert_filename_cannot_escape_sheet_folder(sheet_vault, vault_dir):
    out = sheets.upsert(sheet_vault, "jobs",
                        {"company": "../../evil", "role": "x", "status": "Saved"})
    assert out["result"] == "created"
    assert out["path"].startswith("Job Search/Applications/")
    remainder = out["path"][len("Job Search/Applications/"):]
    # Sanitized to a flat filename — no directory separator survives, so the
    # path can't escape the sheet folder no matter what the caller supplies.
    assert "/" not in remainder and "\\" not in remainder
    assert not (vault_dir / "evil").exists()


def test_sheet_name_path_escape_attempt_is_unknown_sheet(sheet_vault):
    # The sheet name maps to a folder server-side via the registry — it is
    # never used as a raw path — so a traversal attempt just fails to match
    # any registered sheet rather than escaping anywhere.
    with pytest.raises(SheetError, match="Unknown sheet"):
        sheets.upsert(sheet_vault, "../../Claude/Evil", {"company": "x"})


def test_claude_planted_schema_ignored_by_get_schema(vault_dir):
    # Same sort-order trap as test_discover_sheets_ignores_claude_planted_schema:
    # "Applications" sorts before "Claude/Sneaky", so the Claude-planted
    # duplicate is processed second and would shadow the real folder under a
    # naive last-write-wins registry.
    real = vault_dir / "Applications"
    real.mkdir()
    (real / "_schema.md").write_text(JOBS_SCHEMA, encoding="utf-8")
    claude_sheet = vault_dir / "Claude" / "Sneaky"
    claude_sheet.mkdir(parents=True)
    (claude_sheet / "_schema.md").write_text(
        "---\nsheet: jobs\nfilename: \"{x}\"\nkey: [x]\ncolumns:\n"
        "  x: {type: string}\n---\n",
        encoding="utf-8",
    )
    v = Vault(vault_dir)
    # get_schema must still resolve the real, human-blessed sheet.
    assert sheets.get_schema(v, "jobs").folder == "Applications"


def test_check_accepts_unquoted_yaml_dates(sheet_vault, vault_dir):
    # yaml parses unquoted dates to datetime.date; validation must accept
    # them - real Obsidian rows are written unquoted (2026-07-11 migration).
    _row(vault_dir, "Dated - Row",
         "company: Dated\nrole: Row\nstatus: Applied\ndate_applied: 2026-04-20\n")
    assert sheets.check(sheet_vault) == 0


def test_validate_treats_null_optional_as_absent(sheet_vault, vault_dir):
    # 'next_follow_up:' with no value parses to None - absent, not invalid.
    _row(vault_dir, "Nully - Row",
         "company: Nully\nrole: Row\nstatus: Saved\nnext_follow_up:\n")
    assert sheets.check(sheet_vault) == 0


def test_validate_null_required_still_missing(sheet_vault):
    s = sheets.get_schema(sheet_vault, "jobs")
    with pytest.raises(sheets.SheetError, match="company"):
        sheets.validate_fields(s, {"company": None, "role": "R",
                                   "status": "Saved"}, require_required=True)
