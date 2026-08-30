"""Phase 10 item 16 — ``c["targets"].index(t)`` where ``enumerate`` is meant.

Four sites resolve a target's position with ``list.index``:

===============================================  ============================
site                                             what the index is used for
===============================================  ============================
``ui/renderer.py`` everything matrix           ``slot_offset_for_target`` → the block's row
``data_io/exporter.py`` XLSX everything       ``t_idx * dur`` → the sheet row
``data_io/exporter.py`` PDF everything        ``slot_offset_for_target`` → the table row
``ui/app.py`` the live CSV (File ▸ CSV)      ``slot_offset_for_target`` → the row's start time
===============================================  ============================

``.index`` compares dicts by ``==``, so a lesson carrying two *identical*
target dicts resolves both to 0.

``ui/renderer.py`` carries a comment claiming the four sites are deliberately
uniform, so that "duplicate targets resolve identically here and on screen"
(in ``_export_to_csv``'s own comment) and the screen cannot disagree with the exports. **That
claim is false and this module measures it.** The three grid surfaces already
give three different answers for the same class, because each one collapses the
duplicated claim differently:

* the screen lanes the two zero-offset blocks side by side inside one cell;
* the XLSX de-duplicates by *class identity* and prints the lesson once;
* the PDF de-duplicates by *entry identity*, finds two entries in one cell and
  prints the stacked "conflict" paragraph — the lesson twice, separated by a
  red ``---``, in grey instead of the year colour.

What the tests assert
---------------------
Two candidate fixes exist and this module deliberately admits **both**:

* swap all four ``.index`` calls for ``enumerate`` — the second sub-block then
  lands one duration later, on every surface;
* de-duplicate ``targets`` in ``core.models.normalize_class_data``, which every
  ``.egu`` load runs (``storage.load_encrypted``) — the class then has one
  target and one sub-block, on every surface, and files already on disk heal.

Both were built and measured; both turn all six tests green.

So nothing here asserts *which* hour the second sub-block occupies. The
invariant is the one both fixes satisfy and today's tree breaks:

1. no surface draws one lesson twice inside a single cell, and
2. the screen, the XLSX and the PDF agree on how many sub-blocks the lesson
   has and at which hours, and
3. the live CSV agrees with them and never emits the same (group, start time)
   pair twice.

``test_a_two_group_lesson_still_gets_two_consecutive_hours`` is the control: a
lesson with two *distinct* targets must keep its A-at-09:00 / B-at-10:00
layout, so a fix cannot pass by flattening sequential classes.

Reachability is measured, not assumed:
``test_a_lesson_the_dialogs_can_build_lands_in_distinct_cells`` drives
``SetupDialog._add_year`` and then ``BulkAddDialog._ok`` and hands whatever
they produce to the renderer, so no target list in that test is written by
hand.
"""
import csv
import os

import pytest

pytestmark = pytest.mark.ui

YEAR = "Y1"
ROOM = "A-101"
LECTURER = "Ogretmen"
DUP_LESSON = "BedenEgitimi"
TWO_GROUP_LESSON = "MuzikDersi"


# ── State builders ──────────────────────────────────────────────────────────

def _base_state(branches):
    from scheduler_app.core.models import new_state

    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    state["classrooms"] = [ROOM]
    state["classroom_capacities"] = {ROOM: 30}
    state["lecturers"] = [LECTURER]
    state["years"] = {YEAR: list(branches)}
    return state


def _place(state, code, name, targets, joint=False, slot="09:00"):
    from scheduler_app.core.models import mark_placed, new_class

    cls = new_class()
    cls["class_code"] = code
    cls["name"] = name
    cls["lecturer"] = LECTURER
    cls["targets"] = [dict(t) for t in targets]
    cls["joint_session"] = joint
    cls["duration"] = 1
    cls["participants"] = 20
    mark_placed(cls, "monday", slot, ROOM)
    state["classes"].append(cls)
    return cls


def _duplicate_target_state():
    """A placed lesson whose ``targets`` names Y1/A twice.

    Nothing here is hand-planted state the production code was supposed to
    write: ``targets`` is the user's own input, and
    ``test_reachability_bulk_add_dialog_emits_two_identical_targets`` drives the
    real dialog to produce exactly this list.

    ``normalize_state_classes`` is the last thing a state passes through on the
    ``.egu`` load path (``storage.load_encrypted``), so running it here is what
    keeps this module honest about *which* fix it will accept: a de-duplication
    added in ``core/models.normalize_class_data`` heals an already-saved file
    and turns these tests green, exactly as an ``enumerate`` swap at the four
    draw sites does. A de-duplication added only in ``BulkAddDialog._ok`` does
    not, and should not — it leaves every file already on disk wrong.
    """
    from scheduler_app.core.models import normalize_state_classes

    state = _base_state(["A", "B"])
    _place(state, "D001", DUP_LESSON,
           [{"year": YEAR, "branch": "A"}, {"year": YEAR, "branch": "A"}])
    return normalize_state_classes(state)


def _two_group_state():
    """The control: two *distinct* targets, non-joint. A at 09:00, B at 10:00."""
    from scheduler_app.core.models import normalize_state_classes

    state = _base_state(["A", "B"])
    _place(state, "D002", TWO_GROUP_LESSON,
           [{"year": YEAR, "branch": "A"}, {"year": YEAR, "branch": "B"}])
    return normalize_state_classes(state)


# ── Surface readers ─────────────────────────────────────────────────────────
#
# Each returns ``{(branch, start_time): n_copies_drawn}`` for one lesson name,
# so the three grid surfaces become directly comparable.

def _screen_cells(state, name):
    from scheduler_app.ui.renderer import RendererAdapter

    branches = state["years"][YEAR]
    n_branches = len(branches)
    slots = state["slots"]
    out = {}
    for b in RendererAdapter.everything_blocks(state, YEAR):
        if b["cls"]["name"] != name:
            continue
        b_idx = b["col"] % n_branches
        key = (branches[b_idx], slots[b["row"]])
        out[key] = out.get(key, 0) + 1
    return out


def _xlsx_cells(state, name, tmp_path):
    from scheduler_app.data_io.exporter import export_schedule

    path = os.path.join(str(tmp_path), "everything.xlsx")
    export_schedule(state, "xlsx", path, mode="everything")

    import openpyxl
    ws = openpyxl.load_workbook(path)[YEAR]
    branches = state["years"][YEAR]
    out = {}
    for si, slot in enumerate(state["slots"]):
        for b_idx, br in enumerate(branches):
            value = ws.cell(row=si + 3, column=3 + b_idx).value
            n = str(value or "").count(name)
            if n:
                out[(br, slot)] = n
    return out


def _pdf_cells(state, name, tmp_path, monkeypatch):
    from scheduler_app.data_io.exporter import export_schedule
    import reportlab.platypus as platypus

    real_table = platypus.Table
    captured = []

    class _Capturing(real_table):
        def __init__(self, data, *args, **kwargs):
            # repeatRows=2 is unique to the everything table's two header rows;
            # the appendix table uses repeatRows=1.
            if kwargs.get("repeatRows") == 2:
                captured.append(data)
            super().__init__(data, *args, **kwargs)

    monkeypatch.setattr(platypus, "Table", _Capturing)
    export_schedule(state, "pdf", os.path.join(str(tmp_path), "e.pdf"),
                    mode="everything")
    monkeypatch.undo()

    assert len(captured) == 1, (
        "expected exactly one everything-table per year, captured %d"
        % len(captured))
    data = captured[0]
    branches = state["years"][YEAR]
    out = {}
    for si, slot in enumerate(state["slots"]):
        row = data[si + 2]
        for b_idx, br in enumerate(branches):
            cell = row[2 + b_idx]
            text = getattr(cell, "text", None) or (
                cell if isinstance(cell, str) else "")
            n = text.count(name)
            if n:
                out[(br, slot)] = n
    return out


def _all_grid_surfaces(state, name, tmp_path, monkeypatch):
    return {
        "screen (ui/renderer.py)": _screen_cells(state, name),
        "xlsx (data_io/exporter.py)": _xlsx_cells(state, name, tmp_path),
        "pdf (data_io/exporter.py)": _pdf_cells(
            state, name, tmp_path, monkeypatch),
    }


def _fmt(surfaces):
    return "\n".join(
        "    %-34s %s" % (label, sorted(cells.items()))
        for label, cells in surfaces.items())


# ── The live CSV (ui/app.py) ───────────────────────────────────────────

class _Recorder:
    def __init__(self, ret):
        self._ret = ret
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._ret

    @property
    def called(self):
        return bool(self.calls)

    def texts(self):
        out = []
        for args, kwargs in self.calls:
            out.extend(a for a in args if isinstance(a, str))
            out.extend(v for v in kwargs.values() if isinstance(v, str))
        return out


@pytest.fixture
def message_boxes(monkeypatch):
    """Neutralize every modal ``export_csv`` can raise, recording each."""
    from PyQt6.QtWidgets import QMessageBox

    recorders = {
        "information": _Recorder(QMessageBox.StandardButton.Ok),
        "warning": _Recorder(QMessageBox.StandardButton.Ok),
        "critical": _Recorder(QMessageBox.StandardButton.Ok),
        "question": _Recorder(QMessageBox.StandardButton.Yes),
    }
    for name, rec in recorders.items():
        monkeypatch.setattr(QMessageBox, name, staticmethod(rec))
    return recorders


@pytest.fixture
def csv_window(make_app):
    """A ``SchedulerApp`` with CSV export unlocked.

    ``FEATURE_EXPORT_CSV`` is off on the Free tier, so an unpinned tier makes
    ``export_csv`` return on its first line and the assertions below would be
    measuring an empty file. Set by direct assignment rather than
    ``set_tier()``, which sweeps gates belonging to windows other tests have
    already destroyed.
    """
    from scheduler_app.plans import TIER_INSTITUTIONAL
    from scheduler_app.ui.tier_enforcement import TierEnforcement

    enforcer = TierEnforcement.instance()
    previous = (enforcer._tier_slug, enforcer._tier_confirmed)
    enforcer._tier_slug, enforcer._tier_confirmed = TIER_INSTITUTIONAL, True
    try:
        yield make_app()
    finally:
        enforcer._tier_slug, enforcer._tier_confirmed = previous


def _csv_rows(window, state, name, tmp_path, monkeypatch, message_boxes):
    """Drive the real File ▸ CSV action and return ``[(branch, start), ...]``."""
    from PyQt6.QtWidgets import QFileDialog
    from scheduler_app.translations import tr

    path = os.path.join(str(tmp_path), "live.csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    window.state_data.clear()
    window.state_data.update(state)
    window.export_csv()

    assert not message_boxes["critical"].called, (
        "export_csv reported a failure: %s" % message_boxes["critical"].texts())
    assert os.path.exists(path), "export_csv wrote no file"

    with open(path, "rb") as fh:
        raw = fh.read()
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    header, body = rows[0], rows[1:]
    name_col = header.index(tr("labels.class_item"))
    branch_col = header.index(tr("labels.branch"))
    start_col = header.index(tr("labels.start_time"))
    return [(r[branch_col], r[start_col]) for r in body if r[name_col] == name]


# ── Reachability, end to end through the two real dialogs ───────────────────

def test_a_lesson_the_dialogs_can_build_lands_in_distinct_cells(qapp,
                                                                monkeypatch):
    """Setup dialog → bulk-add dialog → grid, with no hand-written targets.

    Two production entry points, neither of which de-duplicates:

    * ``SetupDialog._add_year`` splits the branches prompt on "," and stores
      the result verbatim (``ui/dialogs.py``), so a user typing ``A, B, A``
      gets ``["A", "B", "A"]``;
    * ``BulkAddDialog`` builds one target checkbox *per branch entry* —
      ``_target_labels`` is a list, not a set (``ui/dialogs.py``) — so that
      year yields three columns, two of them labelled ``A``, and the row's
      "select all" box (one click) makes ``_ok`` append
      ``{"year": "9", "branch": "A"}`` twice.

    ``AddClassDialog`` cannot do this: its ``target_vars`` is a dict keyed by
    ``(year, branch)``, so the repeat collapses there. The bulk dialog is the
    reach, and a ``.egu`` written before any of this carries it regardless.

    The assertion is on the *grid*, not on the dialog's output, so it is
    satisfied both by de-duplicating the targets and by giving the third
    sub-block its own hour.
    """
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QDialog, QTableWidgetItem
    from scheduler_app.core.models import (
        mark_placed, new_state, normalize_state_classes)
    from scheduler_app.ui import dialogs
    from scheduler_app.ui.renderer import RendererAdapter

    answers = iter(["9", "A, B, A"])

    class _StubInput:
        """Stands in for the modal text prompt; types what the user types."""

        def __init__(self, *args, **kwargs):
            self.result = next(answers)

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs, "TextInputDialog", _StubInput)
    setup = dialogs.SetupDialog(None, new_state())
    try:
        setup._add_year()
        years = dict(setup._years_data)
    finally:
        setup.deleteLater()
    monkeypatch.undo()

    state = _base_state(["A", "B", "A"])
    state["years"] = years

    bulk = dialogs.BulkAddDialog(None, state)
    try:
        bulk._add_row()
        bulk.table.setItem(0, bulk._col_name, QTableWidgetItem(DUP_LESSON))
        lecturer = bulk.table.cellWidget(0, bulk._col_lecturer)
        assert isinstance(lecturer, QComboBox)
        lecturer.setCurrentIndex(1)
        bulk.table.cellWidget(
            0, bulk._col_all_targets).findChild(QCheckBox).setChecked(True)
        bulk._ok()
        assert bulk.result, "BulkAddDialog._ok produced no classes"
        cls = bulk.result[0]
    finally:
        bulk.deleteLater()

    mark_placed(cls, "monday", "09:00", ROOM)
    state["classes"].append(cls)
    normalize_state_classes(state)

    cells = {}
    for block in RendererAdapter.everything_blocks(state, "9"):
        if block["cls"]["name"] != DUP_LESSON:
            continue
        key = (block["col"], block["row"])
        cells[key] = cells.get(key, 0) + 1

    doubled = sorted(k for k, n in cells.items() if n > 1)
    assert not doubled, (
        "the grid draws %s twice in cell(s) %s — targets the dialogs produced: "
        "%r; (column, row) counts: %s"
        % (DUP_LESSON, doubled, cls["targets"], sorted(cells.items())))


# ── The defect ──────────────────────────────────────────────────────────────

def test_no_grid_surface_draws_one_lesson_twice_in_a_single_cell(
        qapp, tmp_path, monkeypatch):
    """A cell holds a lesson once or not at all — never twice.

    Today the screen lanes two zero-offset blocks into cell (Y1/A, 09:00) and
    the PDF prints the lesson twice inside one table cell, stacked behind the
    red ``---`` separator that is meant for two *different* clashing lessons.
    """
    state = _duplicate_target_state()
    surfaces = _all_grid_surfaces(state, DUP_LESSON, tmp_path, monkeypatch)

    doubled = {
        label: sorted(k for k, n in cells.items() if n > 1)
        for label, cells in surfaces.items()
    }
    doubled = {k: v for k, v in doubled.items() if v}
    assert not doubled, (
        "a lesson is drawn more than once inside one cell:\n%s\n"
        "cells per surface:\n%s"
        % ("\n".join("    %s at %s" % (k, v) for k, v in doubled.items()),
           _fmt(surfaces)))


def test_the_three_grid_surfaces_agree_about_a_duplicate_target_lesson(
        qapp, tmp_path, monkeypatch):
    """Screen, XLSX and PDF must place the same lesson in the same cells.

    This is the claim ``ui/renderer.py``'s own comment makes for keeping
    ``.index`` at all four sites. Measured, the three surfaces disagree: the
    screen and the PDF draw two sub-blocks, the XLSX draws one.
    """
    state = _duplicate_target_state()
    surfaces = _all_grid_surfaces(state, DUP_LESSON, tmp_path, monkeypatch)

    layouts = {label: sorted(cells.items()) for label, cells in surfaces.items()}
    distinct = {repr(v) for v in layouts.values()}
    assert len(distinct) == 1, (
        "the three grid surfaces disagree about where %s is drawn:\n%s"
        % (DUP_LESSON, _fmt(surfaces)))


def test_the_grid_draws_every_hour_the_scheduling_engine_reserves(
        qapp, tmp_path, monkeypatch):
    """The draw sites must not contradict ``core.logic.occupied_slots_of``.

    ``total_duration`` (``core/logic.py``) is ``duration * len(targets)`` for
    a non-joint class, so the engine reserves the lecturer, the room and the
    group for **two** hours for a lesson carrying Y1/A twice — the conflict
    detector, the optimizer and the drag-and-drop occupancy all work from that
    number. Three of the four draw sites then put the whole lesson in one hour.
    The reservation and the picture cannot both be right.

    Either fix reconciles them: ``enumerate`` moves the second sub-block into
    the hour that is already reserved, de-duplication drops the reservation.
    """
    from scheduler_app.core.logic import occupied_slots_of

    state = _duplicate_target_state()
    cls = state["classes"][0]
    reserved = sorted({slot for _day, slot in occupied_slots_of(state, cls)})

    surfaces = _all_grid_surfaces(state, DUP_LESSON, tmp_path, monkeypatch)
    for label, cells in surfaces.items():
        drawn = sorted({slot for _branch, slot in cells})
        assert drawn == reserved, (
            "%s draws %s at %s, but the engine reserves %s for it "
            "(total_duration=%d over %d targets):\n%s"
            % (label, DUP_LESSON, drawn, reserved,
               len(reserved), len(cls["targets"]), _fmt(surfaces)))


def test_the_live_csv_agrees_with_the_grid_and_repeats_no_group_hour(
        csv_window, tmp_path, monkeypatch, message_boxes):
    """``ui/app.py`` — the CSV a user actually gets from File ▸ CSV.

    Two identical targets both resolve to offset 0, so the file states the
    same group at the same hour twice: a colleague reading it sees a duplicate
    line and no second session.
    """
    state = _duplicate_target_state()
    rows = _csv_rows(csv_window, state, DUP_LESSON, tmp_path, monkeypatch,
                     message_boxes)

    assert len(rows) == len(set(rows)), (
        "the CSV repeats a (group, start time) pair for %s: %r"
        % (DUP_LESSON, rows))

    screen = _screen_cells(state, DUP_LESSON)
    assert sorted(rows) == sorted(screen.keys()), (
        "the CSV and the screen disagree about %s:\n"
        "    csv    %s\n    screen %s"
        % (DUP_LESSON, sorted(rows), sorted(screen.keys())))


def test_a_two_group_lesson_still_gets_two_consecutive_hours(
        qapp, tmp_path, monkeypatch):
    """Control — must be green before and after the fix.

    A non-joint lesson with two *distinct* targets is the shape the offset
    exists for: group A at 09:00, group B at 10:00, on every surface. A fix
    that flattens sequential classes, or that de-duplicates by year alone,
    breaks this.
    """
    state = _two_group_state()
    surfaces = _all_grid_surfaces(state, TWO_GROUP_LESSON, tmp_path, monkeypatch)
    expected = {("A", "09:00"): 1, ("B", "10:00"): 1}
    for label, cells in surfaces.items():
        assert cells == expected, (
            "%s moved a genuine two-group lesson:\n%s" % (label, _fmt(surfaces)))
