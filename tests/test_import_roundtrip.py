"""Excel import correctness at the library level (no UI involved).

Everything here drives ``scheduler_app.data_io.importer.load_scheduler_data_from_excel``
directly against workbooks built in ``tmp_path``, so a failure always points at
the import pipeline and never at Qt, at the user's home directory, or at a
checked-in binary fixture.

Findings guarded here
---------------------
* **ST-FUNC-002** (Critical, fixed in Phase 0) — blank ``joint_class_group``
  cells collapse to the string ``'nan'`` and silently merge unrelated classes.
* **ST-FUNC-003** (High, fixed in Phase 0) — one malformed numeric cell raises
  an uncaught ``ValueError`` that aborts the entire import. Split into three
  tests, because "malformed" covers three cases with three different correct
  outcomes: text in a numeric cell (report it), a blank *optional* cell (use
  the documented default silently), and a blank *required* cell (either, but
  never a crash). A single test demanding a report for all three would stay
  red after the register's own recommended fix.
* **ST-FUNC-011** (fixed in Phase 7) — a workbook with no recognized sheet at
  all reports ``is_valid=True``, which the UI announces as a successful import.
* **ST-FUNC-012** (fixed in Phase 7) — two teacher rows sharing a name fuse
  into one lecturer and the first one's availability is overwritten.
* **ST-FUNC-010** (fixed in Phase 7) — the row-2 help-text heuristic guessed
  from shape, so a class id written ``9 A`` was eaten as help text and the
  Chinese and Japanese templates' help text was imported as real data.
* **ST-FUNC-009** — pinned with ``xfail(strict=True)``; scheduled for a later
  phase, and the suite must go red the moment it starts passing so the pin
  gets flipped.
* **The generated template must re-import in all 22 shipped languages** —
  Spanish could not (two languages disagree about what a sheet titled *Aulas*
  holds) and Chinese and Japanese imported phantom rows.

Two conventions used throughout
-------------------------------
1. *Localized strings are never hard-coded.* Sheet titles, column headers and
   example values all come from ``data_io/schema.py`` and ``translations`` with
   the language pinned to Turkish by the session-wide ``_pinned_language``
   fixture in ``tests/conftest.py``.
2. *Hand-built workbooks omit the template's row-2 description row.*
   ``importer._read_sheet`` drops the first data row only when the first
   ``*_id`` column of that row holds one of the help-text strings the template
   writes, in any shipped language. The workbooks below put real IDs there, so
   nothing is dropped. ``test_builder_*`` below proves this both ways, so a
   failure in any other test can never be blamed on the fixture builder.
"""

import pytest

pytest.importorskip("pandas", reason="the Excel importer needs pandas")
pytest.importorskip("openpyxl", reason="workbook fixtures need openpyxl")

import openpyxl  # noqa: E402

from scheduler_app.core.models import (  # noqa: E402
    LOCATION_FACE_TO_FACE,
    LOCATION_LECTURER_OFFICE,
    LOCATION_ONLINE,
    get_location_label,
)
from scheduler_app.data_io import schema  # noqa: E402
from scheduler_app.data_io.importer import load_scheduler_data_from_excel  # noqa: E402
from scheduler_app.data_io.template import generate_excel_template  # noqa: E402
from scheduler_app.translations import (  # noqa: E402
    TRANSLATIONS,
    get_language,
    set_language,
    tr,
)

pytestmark = pytest.mark.excel

SHEET_IDS = ("teachers", "rooms", "branches", "classes")


# ── Workbook builder ────────────────────────────────────────────────────────

def _fields(sheet_id, omit=()):
    """Ordered canonical field names for a sheet, minus anything omitted."""
    return [f for f, _, _ in schema.WORKBOOK_SHEETS[sheet_id]["columns"]
            if f not in omit]


def build_workbook(path, *, classes, teachers=None, rooms=None, branches=None,
                   omit_columns=None, sheets=SHEET_IDS, description_row=False):
    """Write a workbook whose sheet titles and headers come from the schema.

    A field simply left out of a row dict is written as a *truly empty* cell,
    which is what pandas turns into ``NaN`` — the exact shape that triggers
    ST-FUNC-002 and ST-FUNC-003.
    """
    omit_columns = omit_columns or {}
    rows_by_sheet = {
        "teachers": DEFAULT_TEACHERS if teachers is None else teachers,
        "rooms": DEFAULT_ROOMS if rooms is None else rooms,
        "branches": DEFAULT_BRANCHES if branches is None else branches,
        "classes": list(classes),
    }

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_id in sheets:
        ws = wb.create_sheet(schema.get_workbook_sheet_title(sheet_id))
        fields = _fields(sheet_id, omit_columns.get(sheet_id, ()))
        headers = schema.get_workbook_sheet_header_map(sheet_id)
        for col, field in enumerate(fields, start=1):
            ws.cell(row=1, column=col, value=headers[field])

        excel_row = 2
        if description_row:
            descriptions = schema.get_workbook_sheet_description_map(sheet_id)
            for col, field in enumerate(fields, start=1):
                ws.cell(row=excel_row, column=col, value=descriptions[field])
            excel_row += 1

        for row in rows_by_sheet[sheet_id]:
            for col, field in enumerate(fields, start=1):
                if field in row:
                    ws.cell(row=excel_row, column=col, value=row[field])
            excel_row += 1

    wb.save(str(path))
    return str(path)


def _room_type(kind):
    return tr(f"template.workbook_example.room_type_{kind}")


DEFAULT_TEACHERS = [
    {"teacher_id": "T001", "name": "Ada Lovelace"},
    {"teacher_id": "T002", "name": "Bora Yildiz"},
]
DEFAULT_ROOMS = [
    {"room_id": "R001", "name": "Oda 1", "capacity": 30,
     "room_type": _room_type("lecture")},
    {"room_id": "R002", "name": "Lab 1", "capacity": 20,
     "room_type": _room_type("lab")},
]
DEFAULT_BRANCHES = [
    {"branch_id": "B001", "name": "Grup A"},
    {"branch_id": "B002", "name": "Grup B"},
]


def klass(class_id, **overrides):
    """A minimal, fully valid Classes row; override any field by keyword.

    ``joint_class_group`` defaults to a value unique per class so that the
    joint-merge machinery stays out of the way of tests that are not about it
    (``_resolve_joint_groups`` skips groups with fewer than two members).
    """
    row = {
        "class_id": class_id,
        "course_name": f"Ders {class_id}",
        "teacher_id": "T001",
        "branch_id": "B001",
        "duration": 1,
        "student_count": 10,
        "joint_class_group": f"UNIQ-{class_id}",
    }
    row.update(overrides)
    return row


def messages(report):
    """All human-readable report lines, errors and warnings together."""
    return list(report.errors) + list(report.warnings)


def row_prefix(sheet_id, excel_row):
    """The localized ``[Sheet] Row N:`` prefix the importer puts on row errors."""
    return tr("status.import_row_prefix").format(
        sheet=schema.get_workbook_sheet_title(sheet_id), row=excel_row)


def course_names(dataset):
    return sorted(c["name"] for c in dataset.state["classes"])


def branch_set(cls):
    return {t["branch"] for t in cls.get("targets", [])}


# ── Fixture-builder self-checks ─────────────────────────────────────────────
# These exist so that a failure anywhere below can be attributed to the
# importer and not to build_workbook().

def test_builder_produces_a_clean_importable_workbook(tmp_path):
    """Guard: the hand-built workbook shape imports cleanly.

    If this fails, every other test in this module is untrustworthy — the
    fixture, not the importer, would be the thing under test.
    """
    path = build_workbook(tmp_path / "clean.xlsx",
                          classes=[klass("C001"), klass("C002"), klass("C003")])
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    assert ds.report.warnings == []
    assert [e["class_id"] for e in ds.raw_classes] == ["C001", "C002", "C003"]
    assert len(ds.state["classes"]) == 3
    assert ds.state["lecturers"] == ["Ada Lovelace", "Bora Yildiz"]
    assert ds.state["classrooms"] == ["Oda 1", "Lab 1"]


def test_builder_row_two_description_row_is_skipped(tmp_path):
    """Guard: ``_read_sheet``'s description-row heuristic behaves as documented.

    A user importing the shipped template must not lose the first real data
    row, and must not gain a phantom class made of help text.
    """
    path = build_workbook(tmp_path / "described.xlsx", description_row=True,
                          classes=[klass("C001"), klass("C002"), klass("C003")])
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    assert [e["class_id"] for e in ds.raw_classes] == ["C001", "C002", "C003"]
    assert course_names(ds) == ["Ders C001", "Ders C002", "Ders C003"]


# ── ST-FUNC-002 — template round-trip ───────────────────────────────────────

def test_template_roundtrip_preserves_reference_data(tmp_path):
    """Guard (ST-FUNC-002 control): teachers/rooms/branches survive a round-trip.

    Pairs with the class-count test below: if this passes and that one fails,
    the loss is provably in joint-group resolution and nowhere else.
    """
    path = tmp_path / "template.xlsx"
    generate_excel_template(str(path))
    ds = load_scheduler_data_from_excel(str(path))

    assert ds.report.is_valid, ds.report.summary()
    assert ds.report.errors == []

    expected_teachers = [tr(f"template.workbook_example.teacher_{i}_name")
                         for i in (1, 2, 3)]
    expected_rooms = [tr(f"template.workbook_example.room_{i}_name")
                      for i in (1, 2, 3)]
    expected_branches = [tr(f"template.workbook_example.branch_{i}_name")
                         for i in (1, 2, 3)]

    assert ds.state["lecturers"] == expected_teachers
    assert ds.state["classrooms"] == expected_rooms
    assert sorted(ds.state["classroom_capacities"].values()) == [20, 30, 200]

    year = tr("status.default_year_name").format(n=1)
    assert list(ds.state["years"]) == [year]
    assert ds.state["years"][year] == expected_branches


def test_template_roundtrip_preserves_every_class(tmp_path):
    """ST-FUNC-002 — the app's own template must survive its own importer.

    ``template.py`` ships five example class rows. C001/C002/C003 leave
    ``joint_class_group`` blank and are three independent courses; C004 and
    C005 both carry the group ``J1`` and are deliberately the *same* English
    course taught jointly to branches B001 and B002, so they are supposed to
    collapse into a single joint session carrying both branch targets. The
    correct round-trip therefore yields **4** classes, not 5 and not 2.

    A failure means a school that downloads the built-in template, fills it in
    and imports it silently loses most of its courses with ``is_valid=True``
    and no warning.
    """
    path = tmp_path / "template.xlsx"
    generate_excel_template(str(path))
    ds = load_scheduler_data_from_excel(str(path))

    assert ds.report.is_valid, ds.report.summary()
    # All five rows were read; only the J1 pair may be collapsed afterwards.
    assert [e["class_id"] for e in ds.raw_classes] == [
        "C001", "C002", "C003", "C004", "C005"]

    solo_names = [tr(f"template.workbook_example.class_{i}_name") for i in (1, 2, 3)]
    joint_name = tr("template.workbook_example.class_4_name")
    branch_1 = tr("template.workbook_example.branch_1_name")
    branch_2 = tr("template.workbook_example.branch_2_name")

    assert len(ds.state["classes"]) == 4, (
        "expected C001/C002/C003 to stay separate and C004+C005 to merge into "
        f"one joint session; got {course_names(ds)}")
    assert course_names(ds) == sorted(solo_names + [joint_name])

    by_name = {c["name"]: c for c in ds.state["classes"]}
    for name in solo_names:
        assert len(by_name[name]["targets"]) == 1, (
            f"{name!r} has a blank joint group and must keep exactly one "
            f"branch target, got {by_name[name]['targets']}")

    joint = by_name[joint_name]
    # NOTE: `joint_session` is NOT evidence that a merge happened — new_class()
    # defaults it to True for every class. It is asserted only so that a future
    # fix which starts clearing the flag for single-target classes cannot also
    # clear it on a genuine joint session. The discriminating signal is targets.
    assert joint["joint_session"] is True
    assert branch_set(joint) == {branch_1, branch_2}
    assert joint["class_code"] == "ENG101"


# ── ST-FUNC-002 — blank joint-group cells ───────────────────────────────────

@pytest.mark.parametrize("variant", ["absent-cell", "empty-string"])
def test_blank_joint_group_never_merges_classes(tmp_path, variant):
    """ST-FUNC-002 — blank ``joint_class_group`` cells must not group anything.

    pandas reads a blank cell as ``NaN`` and ``str(NaN) == 'nan'``, so today
    every blank-group class shares the joint key ``'nan'`` and all but the
    first are deleted from the state. Three unrelated courses must come back
    as three courses, each keeping its own single branch target.

    Note on ``joint_session``: ``models.new_class()`` defaults that flag to
    ``True`` for *every* class, and it is only meaningful when a class has more
    than one target (``models.is_split_session``). The observable signature of
    an unwanted merge is therefore the target list, which is what is asserted.
    """
    # Both spellings a user can produce in Excel are covered. Verified that
    # pandas collapses them to the same float NaN, so this parametrization
    # documents two *user* actions, not two code paths.
    if variant == "absent-cell":
        # Field omitted entirely -> openpyxl writes nothing -> pandas NaN.
        rows = [klass(cid, joint_class_group=None) for cid in ("C001", "C002", "C003")]
        rows = [{k: v for k, v in r.items() if k != "joint_class_group"} for r in rows]
    else:
        rows = [klass(cid, joint_class_group="") for cid in ("C001", "C002", "C003")]

    path = build_workbook(tmp_path / f"joint_{variant}.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    assert len(ds.state["classes"]) == 3, (
        "blank joint-group cells merged unrelated classes; survivors: "
        f"{course_names(ds)}")
    assert course_names(ds) == ["Ders C001", "Ders C002", "Ders C003"]
    for cls in ds.state["classes"]:
        assert len(cls["targets"]) == 1, (
            f"{cls['name']!r} absorbed another class's target: {cls['targets']}")


def test_whitespace_only_joint_group_never_merges_classes(tmp_path):
    """Guard (ST-FUNC-002 control): a whitespace-only joint cell is no group.

    This path already works because ``str('   ').strip()`` is falsy; keeping it
    green guarantees the ST-FUNC-002 fix does not regress the one blank-cell
    spelling that behaved correctly.
    """
    rows = [klass(cid, joint_class_group="   ") for cid in ("C001", "C002", "C003")]
    path = build_workbook(tmp_path / "joint_ws.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    assert len(ds.state["classes"]) == 3
    assert all(len(c["targets"]) == 1 for c in ds.state["classes"])


def test_shared_joint_group_still_merges_and_keeps_all_targets(tmp_path):
    """Guard (ST-FUNC-002 control): a real joint group must keep merging.

    The ST-FUNC-002 fix must remove the bogus ``'nan'`` group without breaking
    the feature users actually asked for — one lecture attended by two groups.
    """
    rows = [
        klass("C001", branch_id="B001", joint_class_group="J1", course_name="Ortak"),
        klass("C002", branch_id="B002", joint_class_group="J1", course_name="Ortak"),
    ]
    path = build_workbook(tmp_path / "joint_real.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    assert len(ds.state["classes"]) == 1
    merged = ds.state["classes"][0]
    # See the note in the template round-trip: joint_session is True on every
    # class, so the merge is proved by the target list, not by this flag.
    assert merged["joint_session"] is True
    assert branch_set(merged) == {"Grup A", "Grup B"}


# ── ST-FUNC-003 — per-row numeric parsing ───────────────────────────────────

def test_garbage_numeric_cells_are_reported_per_row_and_do_not_abort(tmp_path):
    """ST-FUNC-003 — one malformed number must not destroy the whole import.

    ``int(row.get('duration', 1) or 1)`` raises ``ValueError`` on ``'iki'``, and
    nothing between here and the UI catches it. A user with one typo in a
    250-row roster currently gets no classes and no dialog; they must instead
    get the 248 good rows plus a message naming the two bad ones.

    Text where a number belongs is *not* something the importer may fix on the
    user's behalf without saying so, so both bad rows must appear in the report
    (as an error if the row is skipped, as a warning if the value is coerced).
    """
    rows = [
        klass("C001"),
        klass("C002", duration="iki"),        # non-numeric duration  (required col)
        klass("C003", student_count="cok"),   # non-numeric count     (optional col)
        klass("C004"),
    ]
    path = build_workbook(tmp_path / "bad_numbers.xlsx", classes=rows)

    # Must not raise: today this escapes as ValueError and aborts the import.
    ds = load_scheduler_data_from_excel(path)

    # The two intact rows survive.
    assert "Ders C001" in course_names(ds)
    assert "Ders C004" in course_names(ds)

    # Each bad row is identified — by its localized row prefix or by its ID.
    # Excel rows: header is row 1, so C002 is row 3 and C003 is row 4.
    lines = messages(ds.report)
    for class_id, excel_row in (("C002", 3), ("C003", 4)):
        assert any(row_prefix("classes", excel_row) in line or class_id in line
                   for line in lines), (
            f"nothing in the report names the bad row {class_id} "
            f"(Excel row {excel_row}); report was: {lines}")


def test_blank_optional_student_count_falls_back_to_zero(tmp_path):
    """ST-FUNC-003 — a blank *optional* numeric cell is not a malformed cell.

    ``student_count`` is in ``CLASS_OPTIONAL``; the code's own intent is
    ``int(row.get('student_count', 0) or 0)``. But pandas hands back ``NaN``
    rather than the ``0`` default, ``NaN`` is truthy so ``or 0`` never fires,
    and ``int(NaN)`` aborts the whole import.

    A user who simply did not know the head-count for one course must get that
    course with 0 participants — not lose the entire roster, and not be nagged
    about a column they were told was optional.
    """
    blank = {k: v for k, v in klass("C001").items() if k != "student_count"}
    path = build_workbook(tmp_path / "blank_count.xlsx",
                          classes=[blank, klass("C002")])

    # Must not raise: today this is `ValueError: cannot convert float NaN to integer`.
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    assert course_names(ds) == ["Ders C001", "Ders C002"]
    by_name = {c["name"]: c for c in ds.state["classes"]}
    assert by_name["Ders C001"]["participants"] == 0
    assert by_name["Ders C002"]["participants"] == 10


def test_blank_required_duration_does_not_abort_the_import(tmp_path):
    """ST-FUNC-003 — a blank *required* numeric cell must degrade, not explode.

    Whether a missing ``duration`` should be rejected (row skipped + error) or
    defaulted to one hour is a product decision the register leaves open, so
    this asserts the part that is not negotiable: the import completes, the
    other rows survive, and the affected row is either usable or explained.

    Today neither happens — ``int(NaN)`` raises and the user gets an empty
    timetable with no dialog at all.
    """
    blank = {k: v for k, v in klass("C001").items() if k != "duration"}
    path = build_workbook(tmp_path / "blank_duration.xlsx",
                          classes=[blank, klass("C002")])

    # Must not raise: today this is `ValueError: cannot convert float NaN to integer`.
    ds = load_scheduler_data_from_excel(path)

    # The untouched row always survives.
    assert "Ders C002" in course_names(ds)

    by_name = {c["name"]: c for c in ds.state["classes"]}
    imported_sanely = ("Ders C001" in by_name
                       and isinstance(by_name["Ders C001"]["duration"], int)
                       and by_name["Ders C001"]["duration"] >= 1)
    explained = any(row_prefix("classes", 2) in line or "C001" in line
                    for line in messages(ds.report))
    assert imported_sanely or explained, (
        "the row with a blank duration was neither imported with a usable "
        f"duration nor reported; classes={course_names(ds)}, "
        f"report={messages(ds.report)}")


# ── Guards: schema and reference validation ─────────────────────────────────

def test_missing_required_column_is_rejected_with_a_useful_message(tmp_path):
    """Guard: a Classes sheet without ``duration`` fails loudly, not silently.

    A user who deleted a column must be told which one, not handed an empty
    timetable.
    """
    path = build_workbook(tmp_path / "no_duration.xlsx",
                          classes=[klass("C001")],
                          omit_columns={"classes": ("duration",)})
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid is False
    assert ds.state["classes"] == []
    joined = "\n".join(ds.report.errors)
    assert "duration" in joined
    assert schema.get_workbook_sheet_title("classes") in joined


def test_unknown_teacher_and_branch_ids_are_rejected_per_row(tmp_path):
    """Guard: a dangling ``teacher_id``/``branch_id`` kills only its own row.

    Typing ``T999`` in one row must cost the user that row and produce a
    pointed error, not corrupt the other rows.
    """
    rows = [
        klass("C001", teacher_id="T999"),
        klass("C002", branch_id="B999"),
        klass("C003"),
    ]
    path = build_workbook(tmp_path / "bad_refs.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid is False
    assert course_names(ds) == ["Ders C003"]
    joined = "\n".join(ds.report.errors)
    assert "T999" in joined
    assert "B999" in joined
    assert row_prefix("classes", 2) in joined   # C001 sits on Excel row 2
    assert row_prefix("classes", 3) in joined   # C002 sits on Excel row 3


def test_duplicate_class_ids_are_reported(tmp_path):
    """Guard: two rows claiming the same ``class_id`` must be flagged.

    Silently accepting them would make later edits ambiguous for the user.
    """
    rows = [klass("C001"), klass("C001", course_name="Kopya")]
    path = build_workbook(tmp_path / "dupe_ids.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid is False
    assert any("C001" in line for line in ds.report.errors)


def test_location_type_labels_roundtrip(tmp_path):
    """Guard: the translated ``location_type`` labels map back to stable keys.

    If this breaks, online and lecturer-office lessons silently become
    face-to-face and get assigned physical rooms they never needed.
    """
    rows = [
        klass("C001", location_type=get_location_label(LOCATION_FACE_TO_FACE)),
        klass("C002", location_type=get_location_label(LOCATION_ONLINE)),
        klass("C003", location_type=get_location_label(LOCATION_LECTURER_OFFICE)),
    ]
    path = build_workbook(tmp_path / "locations.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    by_name = {c["name"]: c["location_type"] for c in ds.state["classes"]}
    assert by_name["Ders C001"] == LOCATION_FACE_TO_FACE
    assert by_name["Ders C002"] == LOCATION_ONLINE
    assert by_name["Ders C003"] == LOCATION_LECTURER_OFFICE


def test_unknown_allowed_rooms_warn_and_are_filtered(tmp_path):
    """Guard: ``allowed_rooms`` naming a room that does not exist is dropped.

    Keeping a phantom room in ``required_classrooms`` would make the class
    unplaceable with no explanation the user could act on.
    """
    rows = [klass("C001", allowed_rooms="Oda 1, Hayalet Oda")]
    path = build_workbook(tmp_path / "rooms.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    assert ds.state["classes"][0]["required_classrooms"] == ["Oda 1"]
    assert any("Hayalet Oda" in line for line in ds.report.warnings)


# ── ST-FUNC-011 — the wrong workbook must not look like a success ───────────

def test_workbook_with_no_recognized_sheets_is_invalid(tmp_path):
    """ST-FUNC-011 — importing the wrong file must not look like a success.

    Pointing the importer at an unrelated spreadsheet produced four warnings,
    an empty state and ``is_valid=True``; ``_import_from_excel`` only warns on
    ``not report.is_valid``, so the user got the success dialog over nothing.
    """
    path = tmp_path / "unrelated.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Butce"
    wb.active["A1"] = "Kalem"
    wb.save(str(path))

    ds = load_scheduler_data_from_excel(str(path))

    assert ds.state["classes"] == []
    assert ds.report.is_valid is False, (
        "an unrecognized workbook was reported as a valid import; "
        f"report was: {ds.report.summary()}")


def test_a_workbook_with_one_recognized_sheet_still_imports(tmp_path):
    """Guard (ST-FUNC-011 discrimination): *some* sheets missing is not fatal.

    A school may keep its teacher roster in its own file, and the importer has
    always merged whatever sheets it finds. The ST-FUNC-011 fix must therefore
    fire on *zero* recognized sheets, not on any missing one — a fix that
    errors whenever a sheet is absent would make partial workbooks
    un-importable and would show up here.
    """
    path = build_workbook(tmp_path / "teachers_only.xlsx", classes=[],
                          sheets=("teachers",))
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.errors == [], ds.report.summary()
    assert ds.report.is_valid is True
    assert ds.state["lecturers"] == ["Ada Lovelace", "Bora Yildiz"]
    # The three absent sheets are still named, as warnings.
    assert len(ds.report.warnings) == 3, ds.report.summary()


# ── Every shipped language must be able to read its own template back ───────

TEMPLATE_LOCALES = sorted(TRANSLATIONS)


@pytest.fixture
def ui_language():
    """Switch the UI language for the duration of one test, then put it back.

    The session-wide ``_pinned_language`` fixture pins Turkish so that every
    other test in this file is deterministic; these tests deliberately leave
    that pin and must restore it however they end.
    """
    original = get_language()

    def _switch(lang):
        set_language(lang)
        assert get_language() == lang, f"{lang!r} is not a shipped locale"

    try:
        yield _switch
    finally:
        set_language(original)


@pytest.mark.parametrize("lang", TEMPLATE_LOCALES)
def test_the_generated_template_reimports_in_every_shipped_locale(
        tmp_path, lang, ui_language):
    """File ▸ Generate Template then File ▸ Import Excel, in all 22 languages.

    The template is written in whatever language the app is running in — sheet
    titles, column headers, the row-2 help text and the example rows are all
    translated — and it is the file the bulk-entry workflow hands users. So the
    workbook the app writes is a workbook the app must be able to read, and
    that had only ever been checked in Turkish (``conftest`` pins the suite to
    ``tr``). Three of the 22 languages could not read their own output:

    * ``es`` — the Spanish room sheet is *Aulas* and the Portuguese class sheet
      is *Aulas* too, and the flat name→sheet-id map let one overwrite the
      other, so a Spanish workbook came back with 0 classrooms, 0 classes and
      "Faltan columnas obligatorias" against its rooms sheet.
    * ``zh`` / ``ja`` — the row-2 help text was recognized by "longer than 20
      characters or contains a space", which no CJK sentence satisfies, so the
      help text was imported as a teacher, a classroom *and* a branch.

    Asserted as an exact round-trip rather than "no errors": the zh failure
    mode was extra data, not missing data, and a count-free assertion would
    have passed straight over three phantom entities.
    """
    ui_language(lang)

    path = tmp_path / f"template_{lang}.xlsx"
    generate_excel_template(str(path))
    ds = load_scheduler_data_from_excel(str(path))

    assert ds.report.is_valid, ds.report.summary()
    assert ds.report.errors == []

    expected_teachers = [tr(f"template.workbook_example.teacher_{i}_name")
                         for i in (1, 2, 3)]
    expected_rooms = [tr(f"template.workbook_example.room_{i}_name")
                      for i in (1, 2, 3)]
    expected_branches = [tr(f"template.workbook_example.branch_{i}_name")
                         for i in (1, 2, 3)]

    assert ds.state["lecturers"] == expected_teachers
    assert ds.state["classrooms"] == expected_rooms
    assert sorted(b for names in ds.state.get("years", {}).values()
                  for b in names) == sorted(expected_branches)
    # The template ships five class rows, two of which share joint group J1.
    assert [e["class_id"] for e in ds.raw_classes] == [
        "C001", "C002", "C003", "C004", "C005"]
    assert len(ds.state["classes"]) == 4, (
        f"{lang}: expected the five template rows to merge into four classes, "
        f"got {[c['name'] for c in ds.state['classes']]}")


@pytest.mark.parametrize("workbook_lang", TEMPLATE_LOCALES)
def test_sheet_titles_resolve_per_workbook_not_by_the_current_ui_language(
        workbook_lang, ui_language):
    """A workbook's own four titles must resolve the same whoever opens it.

    *Aulas* means classrooms in Spanish and classes in Portuguese, so no flat
    name→sheet-id table can be right for both. The tempting one-line repair —
    let the active UI language's titles win — fixes Spanish by breaking the
    Portuguese workbook opened on a Spanish desktop, which works today. This
    test resolves each language's own title set under all 22 UI languages, so
    that repair fails here even though the round-trip test above would pass.
    """
    ui_language(workbook_lang)
    titles = [schema.get_workbook_sheet_title(s) for s in SHEET_IDS]
    assert len(set(titles)) == len(SHEET_IDS), (
        f"{workbook_lang} reuses one sheet title for two sheets: {titles}")
    expected = dict(zip(SHEET_IDS, titles))

    for ui_lang in TEMPLATE_LOCALES:
        ui_language(ui_lang)
        assert schema.resolve_workbook_sheet_ids(titles) == expected, (
            f"a {workbook_lang} workbook is misread with the UI in {ui_lang}")


# ── ST-FUNC-012 — two teachers must not fuse into one lecturer ──────────────

def test_duplicate_lecturer_names_are_not_silently_accepted(tmp_path):
    """ST-FUNC-012 — two teacher IDs sharing a name corrupt the lecturer list.

    ``state['lecturers']`` and ``state['lecturer_availability']`` are keyed by
    display name, so a duplicate name silently fuses two real teachers into one
    and overwrites the first one's availability. The defect is that this is
    both silent *and* duplicated, so either fix — reporting it or
    deduplicating — clears the pin.
    """
    teachers = [
        {"teacher_id": "T001", "name": "Ada Lovelace"},
        {"teacher_id": "T002", "name": "Ada Lovelace"},
    ]
    path = build_workbook(tmp_path / "dupe_names.xlsx", teachers=teachers,
                          classes=[klass("C001")])
    ds = load_scheduler_data_from_excel(path)

    lecturers = ds.state["lecturers"]
    reported = any("Ada Lovelace" in line for line in messages(ds.report))
    deduplicated = len(lecturers) == len(set(lecturers))
    assert reported or deduplicated, (
        f"duplicate lecturer name accepted silently; lecturers={lecturers}, "
        f"report={messages(ds.report)}")


def test_lecturer_names_differing_only_in_case_are_not_silently_split(tmp_path):
    """ST-FUNC-012 — the importer must use the app's own rule for "same teacher".

    ``SchedulingWorkflow.register_lecturer`` (ST-UI-020, Phase 6) matches typed
    lecturer names with ``casefold()``, so "ayşe yılmaz" is not a second
    teacher beside "Ayşe Yılmaz". The importer compared nothing at all, so a
    workbook with both spellings produced *two* lecturers for one person: the
    two classes came back carrying different lecturer strings, which the core
    compares with ``==``, so the same teacher could be booked twice in one
    hour — while the class form went on treating the two spellings as one.

    Asserted as a disjunction on purpose: collapsing the pair or reporting it
    are both honest, and the register leaves the choice open. What is not
    allowed is a silent split.
    """
    from scheduler_app.core.workflow import SchedulingWorkflow

    first, second = "Ayşe Yılmaz", "ayşe yılmaz"

    # Control: establish that the app really does read these as one teacher,
    # instead of assuming it. If this ever stops holding, the assertion below
    # is measuring the wrong rule and must be revisited, not relaxed.
    probe = {"lecturers": [first]}
    assert SchedulingWorkflow.register_lecturer(probe, second) == first
    assert probe["lecturers"] == [first]

    teachers = [
        {"teacher_id": "T001", "name": first},
        {"teacher_id": "T002", "name": second},
    ]
    path = build_workbook(tmp_path / "case_names.xlsx", teachers=teachers,
                          classes=[klass("C001"), klass("C002", teacher_id="T002")])
    ds = load_scheduler_data_from_excel(path)

    lecturers = ds.state["lecturers"]
    collapsed = len({n.casefold() for n in lecturers}) == len(lecturers)
    reported = any(first in line or second in line for line in messages(ds.report))
    assert collapsed or reported, (
        f"two spellings of one teacher were accepted silently; "
        f"lecturers={lecturers}, report={messages(ds.report)}")
    # And the user must not be told the import was clean while the roster
    # still holds the same teacher twice.
    assert collapsed or ds.report.is_valid is False, (
        f"a roster holding one teacher twice was reported as valid; "
        f"lecturers={lecturers}, report={messages(ds.report)}")


# ── Known-defect pins (later phases) ────────────────────────────────────────

@pytest.mark.xfail(strict=True, reason=(
    "ST-FUNC-009 — required_room_type is advertised in the template and the "
    "import schema but never consumed by the importer; fixed in a later phase"))
def test_required_room_type_constrains_the_class_to_matching_rooms(tmp_path):
    """ST-FUNC-009 — ``required_room_type`` must actually restrict rooms.

    The template tells the user to write "Laboratory" here; today the value is
    read, validated as a known column, and thrown away, so a physics lab can be
    scheduled into a lecture hall.
    """
    rows = [klass("C001", required_room_type=_room_type("lab"))]
    path = build_workbook(tmp_path / "req_type.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    required = ds.state["classes"][0]["required_classrooms"]
    assert "Lab 1" in required
    assert "Oda 1" not in required


def test_class_id_containing_a_space_is_not_silently_dropped(tmp_path):
    """ST-FUNC-010 — ``_read_sheet`` mistook ``'C 001'`` for a help-text row.

    ``_read_sheet`` used to discard the first data row whenever its ID
    contained a space, so a school that writes class IDs like ``9 A`` lost its
    first course with no error at all. Only the *first* data row was affected,
    which is why the space is placed there. Closed by matching the row against
    the strings the template actually writes instead of guessing from shape —
    the same change that stopped the Chinese template's help text from being
    imported as data, because these are one defect seen from two sides.
    """
    rows = [klass("C 001"), klass("C002")]
    path = build_workbook(tmp_path / "spaced_id.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert [e["class_id"] for e in ds.raw_classes] == ["C 001", "C002"]
    assert course_names(ds) == ["Ders C 001", "Ders C002"]


