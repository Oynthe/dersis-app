"""Phase 10 item 5 — opening Edit Classes and closing it changed nothing, and
it still destroyed the user's Redo history.

The defect
----------
``SchedulerApp.edit_classes`` (``scheduler_app/ui/app.py``) does::

    snap_before = capture_snapshot(self.state_data)
    self._push_undo(tr("actions.edit").format(name="classes"))
    dlg = EditClassesDialog(self, self.state_data, edit_callback=self._edit_class)
    dlg.exec()

The snapshot is genuinely needed — ``EditClassesDialog._delete_selected``
removes dicts straight out of ``state["classes"]`` and reports nothing back —
but it is taken and *committed* before the dialog has been shown, so it fires
for a user who opened the list to read it and pressed Escape.

``_push_undo``'s own docstring forbids exactly this shape:

    "Snapshot AND commit in one statement, so this is only for a change that
    is already certain. A gesture that can still be abandoned — the Setup
    dialog, a drag — must hold its own ``copy.deepcopy`` and call
    ``_commit_undo_entry`` at the commit point instead"

and the function it delegates to says what the commit costs when the action
never happens (``_commit_undo_entry``, module scope in the same file):

    "Both statements after the append are irreversible ... at ``max_undo`` the
    eviction drops ``undo_stack[0]``, and the only compensation anyone ever
    wrote for a speculative push is a ``pop()`` — which takes from the TOP. The
    oldest entry does not come back. ... ``redo_stack.clear()`` throws away a
    future the user can still ask for with Ctrl+Y, and nothing anywhere
    reconstructs it."

Why the Escape is simulated by returning from ``exec``
------------------------------------------------------
``EditClassesDialog`` defines exactly ``__init__``, ``_build_table``,
``_populate_row``, ``_apply_filter``, ``_update_count``, ``_selected_rows``,
``_on_double_click``, ``_edit_selected``, ``_edit_single``, ``_delete_selected``
and ``_export_to_excel`` — no ``reject``, no ``done``, no ``closeEvent``, no
``keyPressEvent``. So Escape reaches ``QDialog``'s default handler, ``exec``
returns ``Rejected``, and **no dialog code of its own runs**. A stub ``exec``
that returns ``Rejected`` is therefore faithful to a user pressing Escape (and
to the window's X). The dialog itself is still the real one: ``edit_classes``
constructs it, ``__init__`` builds the whole table, only the modal loop is
short-circuited — which is where the human would be.

What is red today and what must stay green
------------------------------------------
Red today (the defect): the three ``_escape`` tests.
Green today and must STAY green (the guards on any fix):

* ``test_a_deletion_made_inside_the_dialog_is_still_undoable`` — the snapshot
  exists for ``_delete_selected``; a fix that simply deletes the push would
  make the dialog's deletions permanent.
* ``test_undo_after_a_delete_then_an_edit_walks_backwards_in_time`` — the one
  that kills the obvious fix. ``EditClassesDialog`` calls back into
  ``SchedulerApp._edit_class``, which pushes its OWN undo entry while the
  dialog is still open, so an outer entry committed after ``exec()`` returns
  lands ABOVE entries that were recorded later in time. See the docstring
  there for the measurement.
* ``test_only_capture_snapshot_sees_every_change_the_dialog_can_make`` — which
  comparison the fix's gate has to use.

Findings guarded here: Phase 10 item 5.
"""
import copy

import pytest

from PyQt6.QtWidgets import QDialog, QMessageBox

from scheduler_app.core.models import mark_placed, new_class
from scheduler_app.core.schedule_impact_analyzer import capture_snapshot
from scheduler_app.core.workflow import snapshot_placements
from scheduler_app.ui.dialogs import EditClassesDialog

pytestmark = pytest.mark.ui


# ── the world the gesture happens in ────────────────────────────────────────

def _add(state, name, lecturer="Ada Lovelace", **kw):
    cls = new_class()
    cls.update(name=name, class_code=name.upper(), lecturer=lecturer,
               duration=1, participants=10,
               targets=[{"year": "Y1", "branch": "A"}])
    cls.update(kw)
    state["classes"].append(cls)
    return cls


def _seed(win):
    """Two days, three hours, two rooms; one placed, one unplaced, one pinned.

    All three shapes are here on purpose: ``snapshot_placements`` only sees
    placed-and-not-pinned lessons, so the unplaced and the pinned class are
    what tell the two candidate comparisons apart in
    ``test_only_capture_snapshot_sees_every_change_the_dialog_can_make``.
    """
    s = win.state_data
    s["days"] = ["monday", "tuesday"]
    s["slots"] = ["09:00", "10:00", "11:00"]
    s["classrooms"] = ["R001", "R002"]
    s["classroom_capacities"] = {"R001": 40, "R002": 40}
    s["lecturers"] = ["Ada Lovelace", "Grace Hopper"]
    s["lecturer_availability"] = {}
    s["years"] = {"Y1": ["A"]}
    s["classes"] = []

    placed = _add(s, "Fizik")
    mark_placed(placed, "monday", "09:00", "R001")

    _add(s, "Kimya", lecturer="Grace Hopper")          # unplaced

    pinned = _add(s, "Tarih", lecturer="Grace Hopper",
                  pinned=True, pinned_day="tuesday", pinned_time="10:00",
                  pinned_classroom="R002")
    mark_placed(pinned, "tuesday", "10:00", "R002")

    win.refresh_grid()
    return s


def _by_name(win, name):
    """The lesson as it exists NOW.

    ``_restore_state`` replaces ``state_data``'s contents wholesale, so a dict
    held across an ``undo()`` is a corpse. Re-fetch instead of remembering.
    """
    for cls in win.state_data["classes"]:
        if cls["name"] == name:
            return cls
    return None


@pytest.fixture
def win(make_app, monkeypatch):
    """A real ``SchedulerApp`` with the modal boxes captured rather than shown."""
    w = make_app()
    boxes = []

    def _info(*args, **kwargs):
        boxes.append(("information", args[1:3]))
        return QMessageBox.StandardButton.Ok

    def _question(*args, **kwargs):
        boxes.append(("question", args[1:3]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "information", staticmethod(_info))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_info))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    w.boxes = boxes
    _seed(w)
    return w


# ── driving the real gesture ────────────────────────────────────────────────

def _run_edit_classes(win, monkeypatch, during=None):
    """Run the whole production ``edit_classes`` gesture.

    ``during`` is what the user does while the modal is up; ``None`` is
    Escape — see the module docstring for why that is faithful.
    """
    seen = {}

    def _stub_exec(self):
        seen["dialog"] = self
        seen["rows"] = self.table.rowCount()
        if during is not None:
            during(self)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(EditClassesDialog, "exec", _stub_exec)
    win.edit_classes()
    assert "dialog" in seen, (
        "edit_classes never reached dlg.exec(); nothing below measured the "
        "gesture")
    return seen


def _arm_a_pending_redo(win):
    """A real action through a real command, then a real Ctrl+Z.

    ``_unplace_specific`` is a shipped context-menu command: it pushes its own
    undo entry, unplaces, and repaints. Returns nothing — re-fetch with
    ``_by_name``.
    """
    win._unplace_specific(_by_name(win, "Fizik"))
    assert _by_name(win, "Fizik")["placed"] is False, (
        "the fixture is wrong: the unplace command did nothing")
    win.undo()

    assert _by_name(win, "Fizik")["placed"] is True, (
        "the fixture is wrong: undo did not put the lesson back, so nothing "
        "below measures redo")
    assert len(win._redo_stack) == 1, (
        "the fixture is wrong: there is no pending redo entry to lose "
        "(depth %d)" % len(win._redo_stack))


# ── the defect ──────────────────────────────────────────────────────────────

def test_escaping_edit_classes_leaves_the_redo_stack_intact(win, monkeypatch):
    """Item 5 — reading the class list must not be able to kill Ctrl+Y.

    The user undid something, then opened Edit Class(es) to look at it and
    pressed Escape. Not one byte of ``state_data`` moved, so the undone action
    is still theirs to re-apply. Today ``_push_undo`` has already run
    ``_redo_stack.clear()`` before the dialog was even shown.
    """
    _arm_a_pending_redo(win)
    before = copy.deepcopy(win.state_data)

    seen = _run_edit_classes(win, monkeypatch)

    # Anti-vacuity: the dialog really was built over the real class list.
    assert seen["rows"] == 3, (
        "the dialog did not list the seeded classes (%d rows), so this test "
        "opened nothing" % seen["rows"])
    # And the gesture really was a no-op — if `validate_placements_after_edit`
    # had unplaced something, the premise of every assertion below is gone.
    assert win.state_data == before, (
        "the Escape gesture changed the state, so it is not the no-op this "
        "test is about")

    assert len(win._redo_stack) == 1, (
        "opening Edit Class(es) and pressing Escape destroyed the pending "
        "redo entry: depth is %d, it was 1. Nothing was edited, so Ctrl+Y "
        "must survive it." % len(win._redo_stack))

    win.redo()
    assert _by_name(win, "Fizik")["placed"] is False, (
        "redo after an escaped Edit Class(es) did not re-apply the undone "
        "unplace")


def test_escaping_edit_classes_does_not_leave_a_no_op_undo_entry(
        win, monkeypatch):
    """Item 5 — and it must not add a Ctrl+Z that undoes nothing either.

    An entry whose snapshot equals the live state is a step the user has to
    press Ctrl+Z twice to get past, labelled as an edit they never made.
    """
    depth_before = len(win._undo_stack)
    labels_before = [e[0] for e in win._undo_stack]

    _run_edit_classes(win, monkeypatch)

    assert len(win._undo_stack) == depth_before, (
        "an escaped Edit Class(es) pushed a no-op undo entry: stack went "
        "%r -> %r" % (labels_before, [e[0] for e in win._undo_stack]))


def test_escaping_edit_classes_at_the_undo_cap_keeps_the_oldest_action(
        win, monkeypatch):
    """Item 5 — at the 50-entry cap the no-op push evicts, irreversibly.

    ``_commit_undo_entry`` appends and then drops ``_undo_stack[0]``. Identity
    matters as much as depth: the entry that goes is the user's OLDEST
    recoverable action, and no pop puts it back.
    """
    for i in range(win._max_undo):
        win._push_undo("action-%02d" % i)
    labels_before = [e[0] for e in win._undo_stack]
    assert len(labels_before) == win._max_undo, (
        "the fixture is wrong: the stack is %d deep, the cap is %d"
        % (len(labels_before), win._max_undo))

    _run_edit_classes(win, monkeypatch)

    labels_after = [e[0] for e in win._undo_stack]
    assert labels_after == labels_before, (
        "an escaped Edit Class(es) at the cap evicted the oldest undo entry: "
        "the stack started at %r and now starts at %r"
        % (labels_before[0], labels_after[0]))


# ── the guards a fix must not break ─────────────────────────────────────────

def test_a_deletion_made_inside_the_dialog_is_still_undoable(win, monkeypatch):
    """Green today, and the reason the snapshot exists at all.

    ``_delete_selected`` calls ``self.state["classes"].remove(cls)`` directly
    and tells ``edit_classes`` nothing. Deleting the push instead of gating it
    would make every deletion made from this dialog permanent.
    """
    def _the_user_deletes_the_first_row(dlg):
        dlg.table.selectRow(0)
        dlg._delete_selected()

    _run_edit_classes(win, monkeypatch, during=_the_user_deletes_the_first_row)

    assert _by_name(win, "Fizik") is None, (
        "the deletion never happened, so this test guards nothing")
    assert win._undo_stack, "a real deletion left nothing to undo"

    win.undo()
    assert _by_name(win, "Fizik") is not None, (
        "undo after a deletion made in Edit Class(es) did not bring the class "
        "back")


def test_undo_after_a_delete_then_an_edit_walks_backwards_in_time(
        win, monkeypatch):
    """Green today — and the test that rules out the obvious fix.

    ``EditClassesDialog`` is handed ``edit_callback=self._edit_class``, and
    ``SchedulerApp._edit_class`` calls ``self._push_undo(...)`` itself. So a
    single visit to this dialog can record entries *while the modal is up*.

    Deferring the outer commit to after ``exec()`` — the ``edit_setup`` shape —
    puts the pre-dialog snapshot ON TOP of an entry that was recorded later,
    and the stack stops being a timeline. Measured on a build of exactly that
    fix, deleting 'Fizik' and then renaming 'Kimya' in one visit:

        undo #1  -> Fizik back,    Kimya = 'Kimya'      (pre-dialog state)
        undo #2  -> Fizik DELETED, Kimya = 'Kimya'      (post-delete state)

    The second Ctrl+Z re-deleted the class the first one had just restored.
    Today's unconditional-but-early push gets the ORDER right (its entry is
    below ``_edit_class``'s), which is the one thing about it that is correct,
    and this test is what keeps a fix from trading the order away for the
    gate.
    """
    def _the_user_deletes_one_and_renames_another(dlg):
        dlg.table.selectRow(0)             # Fizik
        dlg._delete_selected()
        # `_delete_selected` rebuilt the table, so row 0 is Kimya now.
        renamed = dlg._class_refs[0]
        assert renamed["name"] == "Kimya", (
            "the fixture is wrong: row 0 after the delete is %r"
            % renamed["name"])
        monkeypatch.setattr(
            "scheduler_app.ui.app._class_form_result",
            lambda parent, state, edit_cls: dict(edit_cls, name="Kimya II"))
        dlg._edit_single(0)

    _run_edit_classes(win, monkeypatch,
                      during=_the_user_deletes_one_and_renames_another)

    assert _by_name(win, "Fizik") is None, "the delete did not happen"
    assert _by_name(win, "Kimya II") is not None, "the rename did not happen"

    win.undo()
    first = (_by_name(win, "Fizik") is not None,
             _by_name(win, "Kimya") is not None)
    win.undo()
    second = (_by_name(win, "Fizik") is not None,
              _by_name(win, "Kimya") is not None)

    assert second[0] or not first[0], (
        "the second Ctrl+Z re-deleted a class the first one restored: after "
        "undo #1 (Fizik present, Kimya un-renamed) = %r, after undo #2 = %r. "
        "The undo stack is no longer a timeline — an entry recorded before "
        "the dialog opened is sitting above one recorded while it was open."
        % (first, second))


# ── which comparison the gate has to use ────────────────────────────────────

@pytest.mark.parametrize("what", ["placed", "unplaced", "pinned", "renamed"])
def test_only_capture_snapshot_sees_every_change_the_dialog_can_make(
        win, monkeypatch, what):
    """The gate's comparison, decided by measurement rather than by taste.

    ``_place_classes_batch`` gates on ``snapshot_placements``. That is right
    for a solver pass, which can only move lessons, and wrong here:
    ``snapshot_placements`` is ``{cls_key: (day, time, room)}`` over
    ``placed and not pinned`` classes only, and ``cls_key`` is the stable
    ``class_uid``. So it cannot see a deleted class that was never placed, it
    cannot see a deleted class that was pinned, and it cannot see a rename.

    ``capture_snapshot`` carries ``_name`` and ``_class_code`` per class and
    one entry per class, so all four register. Each case below is driven
    through the dialog's real mutation path.
    """
    name = {"placed": "Fizik", "unplaced": "Kimya",
            "pinned": "Tarih", "renamed": "Kimya"}[what]

    def _the_user_acts(dlg):
        row = next(r for r, c in enumerate(dlg._class_refs)
                   if c["name"] == name)
        if what == "renamed":
            monkeypatch.setattr(
                "scheduler_app.ui.app._class_form_result",
                lambda parent, state, edit_cls: dict(edit_cls,
                                                     name="Kimya II"))
            dlg._edit_single(row)
        else:
            dlg.table.selectRow(row)
            dlg._delete_selected()

    snap_before = capture_snapshot(win.state_data)
    places_before = snapshot_placements(win.state_data)

    _run_edit_classes(win, monkeypatch, during=_the_user_acts)

    assert capture_snapshot(win.state_data) != snap_before, (
        "capture_snapshot did not notice a %s change made in the dialog; it "
        "cannot be the gate" % what)

    # Recorded, not asserted as a requirement: this is why the other candidate
    # is unusable. Only the 'placed' case moves snapshot_placements.
    moved = snapshot_placements(win.state_data) != places_before
    assert moved == (what == "placed"), (
        "snapshot_placements changed=%r for the %s case; the recorded "
        "measurement says %r. Keys before: %r"
        % (moved, what, what == "placed", sorted(places_before)))
