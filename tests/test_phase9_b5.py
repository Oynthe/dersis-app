"""B5 / ST-FUNC-009 — a room type declared by a *later* row of a joint group.

``_process_classes`` resolves ``required_room_type`` into ``required_classrooms``
per row, but ``_resolve_joint_groups``
(``scheduler_app/data_io/importer.py``) merges a group by keeping ``classes[0]``
as the primary, copying only ``targets`` off the other members and deleting
them from state. ``required_classrooms`` is never merged, so the room
constraint of every non-first row of a joint group is thrown away — an empty
``required_classrooms`` is exactly what ``get_physical_room_candidates`` reads
as "any room", which is the ST-FUNC-009 inversion the Phase 8 fix exists to
prevent. A joint lab session whose lab row is not first can still be scheduled
into a lecture hall.

The workbook builder here is a deliberate copy of the one in
``tests/test_import_roundtrip.py`` rather than an import of it: this module has
to stand on its own while other agents edit that file in parallel.
"""

import pytest

pytest.importorskip("pandas", reason="the Excel importer needs pandas")
pytest.importorskip("openpyxl", reason="workbook fixtures need openpyxl")

import openpyxl  # noqa: E402

from scheduler_app.core.models import get_physical_room_candidates  # noqa: E402
from scheduler_app.data_io import schema  # noqa: E402
from scheduler_app.data_io.importer import load_scheduler_data_from_excel  # noqa: E402

pytestmark = pytest.mark.excel

SHEET_IDS = ("teachers", "rooms", "branches", "classes")

# Free text the app never translates — the importer matches the Classes sheet
# against the Rooms sheet of the *same* workbook, so both sides are literals.
ROOM_TYPE_LECTURE = "Derslik"
ROOM_TYPE_LAB = "Laboratuvar"

DEFAULT_TEACHERS = [
    {"teacher_id": "T001", "name": "Ada Lovelace"},
    {"teacher_id": "T002", "name": "Bora Yildiz"},
]
# Two labs, so "the type narrows the list" and "the type replaces the list"
# cannot produce the same answer; one lecture hall, which is the room a joint
# lab session must never be allowed into.
DEFAULT_ROOMS = [
    {"room_id": "R001", "name": "Oda 1", "capacity": 30,
     "room_type": ROOM_TYPE_LECTURE},
    {"room_id": "R002", "name": "Lab 1", "capacity": 20,
     "room_type": ROOM_TYPE_LAB},
    {"room_id": "R003", "name": "Lab 2", "capacity": 20,
     "room_type": ROOM_TYPE_LAB},
]
DEFAULT_BRANCHES = [
    {"branch_id": "B001", "name": "Grup A"},
    {"branch_id": "B002", "name": "Grup B"},
]


def build_workbook(path, *, classes, teachers=None, rooms=None, branches=None):
    """Write a workbook whose sheet titles and headers come from the schema."""
    rows_by_sheet = {
        "teachers": DEFAULT_TEACHERS if teachers is None else teachers,
        "rooms": DEFAULT_ROOMS if rooms is None else rooms,
        "branches": DEFAULT_BRANCHES if branches is None else branches,
        "classes": list(classes),
    }

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_id in SHEET_IDS:
        ws = wb.create_sheet(schema.get_workbook_sheet_title(sheet_id))
        fields = [f for f, _, _ in schema.WORKBOOK_SHEETS[sheet_id]["columns"]]
        headers = schema.get_workbook_sheet_header_map(sheet_id)
        for col, field in enumerate(fields, start=1):
            ws.cell(row=1, column=col, value=headers[field])
        excel_row = 2
        for row in rows_by_sheet[sheet_id]:
            for col, field in enumerate(fields, start=1):
                if field in row:
                    ws.cell(row=excel_row, column=col, value=row[field])
            excel_row += 1

    wb.save(str(path))
    return str(path)


def klass(class_id, **overrides):
    """A minimal, fully valid Classes row; override any field by keyword."""
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


def joint_rows(*, lab_row_index, joint="J1", course="Ortak Fizik",
               first_type=None, second_type=None):
    """Two rows of one joint group; ``lab_row_index`` says which declares the lab.

    ``lab_row_index`` is 0 or 1. ``first_type``/``second_type`` override it
    outright for the two-different-types case.
    """
    types = [first_type, second_type]
    if first_type is None and second_type is None:
        types = [None, None]
        types[lab_row_index] = ROOM_TYPE_LAB
    rows = []
    for i, (cid, bid) in enumerate((("C001", "B001"), ("C002", "B002"))):
        extra = {}
        if types[i]:
            extra["required_room_type"] = types[i]
        rows.append(klass(cid, branch_id=bid, course_name=course,
                          joint_class_group=joint, **extra))
    return rows


def merged_joint_class(dataset):
    """The single surviving class of a two-row joint group.

    Asserts the merge itself happened first, so a failure below is never a
    failure of the fixture.
    """
    classes = dataset.state["classes"]
    assert len(classes) == 1, (
        "fixture drift: the two rows of joint group J1 did not merge into one "
        f"session; state holds {[c['name'] for c in classes]}")
    merged = classes[0]
    assert merged["joint_session"] is True
    branches = {t["branch"] for t in merged.get("targets", [])}
    assert branches == {"Grup A", "Grup B"}, (
        f"fixture drift: the merged session lost a branch target; got {branches}")
    return merged


def test_a_joint_group_keeps_a_room_type_declared_by_its_second_row(tmp_path):
    """B5 / ST-FUNC-009 — the lab row is not first, and its lab is discarded.

    Two rows of one joint group; the SECOND one carries
    ``required_room_type = Laboratuvar``. ``_process_classes`` resolves that row
    to ``['Lab 1', 'Lab 2']`` correctly, and then ``_resolve_joint_groups``
    keeps ``classes[0]`` — whose ``required_classrooms`` is empty — copies only
    ``targets`` off the second row and deletes it. The merged session therefore
    imports with no room constraint at all, and an empty
    ``required_classrooms`` means "any room" to
    ``get_physical_room_candidates``: the physics lab is free to land in the
    lecture hall Oda 1, which is the headline case ST-FUNC-009 is about and the
    one the user has been told is fixed.

    Row order in a spreadsheet carries no meaning here — nothing in the
    template tells a user that the row declaring the lab must be typed first —
    so the merged session must carry the same constraint it would have carried
    had the two rows been swapped.
    """
    path = build_workbook(tmp_path / "joint_lab_second.xlsx",
                          classes=joint_rows(lab_row_index=1))
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    merged = merged_joint_class(ds)

    assert sorted(merged["required_classrooms"]) == ["Lab 1", "Lab 2"], (
        "the joint session lost the room type declared by its second row: "
        f"required_classrooms={merged['required_classrooms']!r}, expected the "
        f"two {ROOM_TYPE_LAB} rooms ['Lab 1', 'Lab 2']")

    candidates = get_physical_room_candidates(ds.state, merged)
    assert "Oda 1" not in candidates, (
        f"a joint {ROOM_TYPE_LAB} session can still be scheduled into the "
        f"lecture hall Oda 1; candidate rooms are {candidates}")


def test_a_joint_group_keeps_a_room_type_declared_by_its_first_row(tmp_path):
    """Control for the test above — the same workbook with the rows swapped.

    This is the arrangement the merge happens to handle: the lab row is
    ``classes[0]``, so its ``required_classrooms`` survives by accident of
    position. If this one fails too, the defect is not about row order and the
    room type is being lost somewhere else entirely.
    """
    path = build_workbook(tmp_path / "joint_lab_first.xlsx",
                          classes=joint_rows(lab_row_index=0))
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    merged = merged_joint_class(ds)

    assert sorted(merged["required_classrooms"]) == ["Lab 1", "Lab 2"], (
        "the control case is broken as well: a room type on the FIRST row of a "
        f"joint group is also being lost; got {merged['required_classrooms']!r}")
    assert "Oda 1" not in get_physical_room_candidates(ds.state, merged)


def test_a_joint_merge_never_widens_a_group_whose_rows_declare_room_types(
        tmp_path):
    """Forward guard — two rows, two *different* types, and no way to widen.

    ``Derslik`` resolves to ``['Oda 1']`` and ``Laboratuvar`` to
    ``['Lab 1', 'Lab 2']``; the two do not intersect. A fix for the test above
    that merges by plain intersection would write ``[]`` here, and ``[]`` is
    read by ``get_physical_room_candidates`` as "any room" — so the one session
    in the file that two rows constrained would become the least constrained
    thing in it. That inversion is the ST-FUNC-009 finding itself, and Phase 8
    shipped exactly it once inside ``_process_classes`` before catching it, so
    it is guarded here before the fix is written rather than after.

    This test passes today (nothing merges, so the primary's ``['Oda 1']``
    stands); it exists to stay passing. It deliberately does not dictate
    *which* of the two types wins — only that the answer is never "all of
    them".
    """
    rows = joint_rows(lab_row_index=0, first_type=ROOM_TYPE_LECTURE,
                      second_type=ROOM_TYPE_LAB)
    path = build_workbook(tmp_path / "joint_two_types.xlsx", classes=rows)
    ds = load_scheduler_data_from_excel(path)

    assert ds.report.is_valid, ds.report.summary()
    merged = merged_joint_class(ds)

    assert merged["required_classrooms"], (
        "merging two disagreeing room types emptied required_classrooms, which "
        "means 'any room': the joint session is now allowed into every room in "
        f"the building — {get_physical_room_candidates(ds.state, merged)}")
    assert len(get_physical_room_candidates(ds.state, merged)) < 3, (
        "the merged session is allowed into every room in the building despite "
        "both of its rows naming a room type")
