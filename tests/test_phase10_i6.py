"""Phase 10, item 6 — Bulk Add, then Cancel in the results dialog.

The shape
---------
``SchedulerApp.bulk_add_classes`` (``scheduler_app/ui/app.py`` ~3435)::

    snap_before = capture_snapshot(self.state_data)
    self._push_undo(tr("actions.bulk_schedule"))
    if self._schedule_new_classes(new_classes):
        ...

``_push_undo`` is ``_commit_undo_entry``: append, evict ``_undo_stack[0]`` at
the 50-entry cap, and ``_redo_stack.clear()``. Both of the last two are
irreversible. It fires ABOVE ``_schedule_new_classes``, whose rejected exit
runs ``SchedulingWorkflow.rollback_schedule`` — which restores the placements
and removes the new classes, and restores neither stack.

So: open Bulk Add, fill it in, press OK, look at the results, press Cancel.
Nothing is added. Nothing moves. The redo stack is gone anyway, and at the cap
so is the user's oldest undoable action.

This is Phase 9's B1/B2 at a fourth site (``_start_drag_gfx`` and
``_place_classes_batch`` were the first two, ``add_class`` — item 7 — is the
third).

What is driven, and what is simulated
-------------------------------------
Production ``bulk_add_classes`` is called. Both dialogs are constructed for
real; only ``.exec()`` is replaced, because it blocks on a human:

* ``BulkAddDialog.exec`` returns ``Accepted`` and leaves ``self.result``
  holding the class dicts the user "entered" — the gesture's INPUT.
* ``BulkResultsDialog.exec`` returns ``Rejected`` — the Cancel button, and
  the only thing this file is about.

Every undo/redo number asserted below is produced by ``ui/app.py``.
"""
import copy

import pytest

from scheduler_app.core.models import mark_placed, new_class
from scheduler_app.workflow import snapshot_placements

pytestmark = pytest.mark.ui


# ── the world ───────────────────────────────────────────────────────────────

def _new_cls(name, **kw):
    cls = new_class()
    cls.update(
        name=name,
        lecturer="Ada Lovelace",
        duration=1,
        participants=10,
        targets=[{"year": "Year-1", "branch": "A"}],
    )
    cls.update(kw)
    return cls


def _seed(win):
    """One day, two hours, one room, one lesson already at 09:00.

    Two more lessons for the same group therefore cannot all fit, which is
    what drives ``optimized_batch_schedule`` past its fast path and opens the
    results dialog.
    """
    s = win.state_data
    s["days"] = ["monday"]
    s["slots"] = ["09:00", "10:00"]
    s["classrooms"] = ["R001"]
    s["classroom_capacities"] = {"R001": 40}
    s["lecturers"] = ["Ada Lovelace"]
    s["years"] = {"Year-1": ["A"]}
    s["classes"] = []

    existing = _new_cls("Fizik")
    s["classes"].append(existing)
    mark_placed(existing, "monday", "09:00", "R001")
    return existing


@pytest.fixture
def win(make_app, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from scheduler_app.ui.tier_enforcement import TierEnforcement
    from scheduler_app.plans import TIER_INSTITUTIONAL

    w = make_app()
    monkeypatch.setattr(TierEnforcement.instance(), "_tier_slug",
                        TIER_INSTITUTIONAL)

    boxes = []

    def _capture(*args, **kwargs):
        boxes.append(args[1:3] if len(args) > 2 else args)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_capture))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(_capture))
    w.boxes = boxes
    return w


def _the_user_enters_and_then_cancels(monkeypatch, classes):
    """Accept Bulk Add with *classes*, then reject the results dialog.

    Returns a dict recording that each dialog was actually reached, so a
    scenario that silently stopped short cannot look like a clean run.
    """
    from PyQt6.QtWidgets import QDialog
    from scheduler_app.ui.dialogs import BulkAddDialog, BulkResultsDialog

    seen = {"add": 0, "results": 0}

    def _add_exec(self):
        seen["add"] += 1
        self.result = classes
        return QDialog.DialogCode.Accepted

    def _results_exec(self):
        seen["results"] += 1
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(BulkAddDialog, "exec", _add_exec)
    monkeypatch.setattr(BulkResultsDialog, "exec", _results_exec)
    return seen


def _two_classes():
    return [_new_cls("Kimya"), _new_cls("Biyoloji")]


def _arm_a_pending_redo(win):
    """One real action plus one real Ctrl+Z, leaving exactly one redo entry."""
    win._push_undo("rename-lecturer")
    win.state_data["lecturers"] = ["Ada L."]
    win.undo()
    assert win.state_data["lecturers"] == ["Ada Lovelace"], (
        "fixture wrong: undo did not roll the edit back")
    assert len(win._redo_stack) == 1, (
        "fixture wrong: no pending redo entry (depth %d)"
        % len(win._redo_stack))
    assert win._undo_stack == [], (
        "fixture wrong: undo stack is %r"
        % ([e[0] for e in win._undo_stack],))


def _fill_undo_to_the_cap(win):
    for i in range(win._max_undo):
        win._push_undo("action-%02d" % i)
    assert len(win._undo_stack) == win._max_undo
    return [e[0] for e in win._undo_stack]


# ── the scenario itself ─────────────────────────────────────────────────────

def test_the_scenario_really_opens_both_dialogs(win, monkeypatch):
    """Anti-vacuity guard for every test below.

    If ``optimized_batch_schedule`` had placed both new lessons on its fast
    path, ``_schedule_new_classes`` would have returned at ``single_success``
    and the Cancel button would never have existed.
    """
    _seed(win)
    seen = _the_user_enters_and_then_cancels(monkeypatch, _two_classes())

    win.bulk_add_classes()

    assert seen["add"] == 1, "Bulk Add never opened"
    assert seen["results"] == 1, (
        "the results dialog never opened, so no test in this file exercises "
        "the Cancel path; classes are %r"
        % ([(c["name"], c["placed"]) for c in win.state_data["classes"]],))


def test_a_cancelled_bulk_add_leaves_the_timetable_byte_identical(
        win, monkeypatch):
    """The premise: ``rollback_schedule`` really does undo the whole batch."""
    _seed(win)
    _the_user_enters_and_then_cancels(monkeypatch, _two_classes())

    before = copy.deepcopy(win.state_data)
    win.bulk_add_classes()
    after = copy.deepcopy(win.state_data)

    assert after == before, (
        "the rollback did not restore the state, so this file's premise is "
        "wrong. classes before %r, after %r; placements before %r, after %r"
        % ([c["name"] for c in before["classes"]],
           [c["name"] for c in after["classes"]],
           snapshot_placements(before), snapshot_placements(after)))


# ── item 6 proper ───────────────────────────────────────────────────────────

def test_a_cancelled_bulk_add_leaves_the_redo_stack_intact(win, monkeypatch):
    """The user undid something, bulk-added, then pressed Cancel.

    Nothing was added, nothing moved. Ctrl+Y must still work.
    """
    _seed(win)
    _arm_a_pending_redo(win)
    _the_user_enters_and_then_cancels(monkeypatch, _two_classes())

    redo_before = len(win._redo_stack)
    undo_before = [e[0] for e in win._undo_stack]

    win.bulk_add_classes()

    redo_after = len(win._redo_stack)
    undo_after = [e[0] for e in win._undo_stack]

    assert redo_after == redo_before == 1, (
        "a cancelled Bulk Add destroyed the pending redo entry: depth "
        "%d -> %d. The batch was rolled back and the timetable is unchanged, "
        "so Ctrl+Y must survive it. (undo labels %r -> %r)"
        % (redo_before, redo_after, undo_before, undo_after))

    win.redo()
    assert win.state_data["lecturers"] == ["Ada L."], (
        "redo after a cancelled Bulk Add did not re-apply the undone action; "
        "lecturers are %r" % (win.state_data["lecturers"],))


def test_a_cancelled_bulk_add_leaves_no_undo_entry(win, monkeypatch):
    """One Ctrl+Z after a cancelled bulk add must reach the action before it."""
    _seed(win)
    _the_user_enters_and_then_cancels(monkeypatch, _two_classes())

    win._push_undo("rename-lecturer")
    win.state_data["lecturers"] = ["Ada L."]

    win.bulk_add_classes()

    assert [e[0] for e in win._undo_stack] == ["rename-lecturer"], (
        "a cancelled Bulk Add left a no-op entry on the undo stack: %r"
        % ([e[0] for e in win._undo_stack],))

    win.undo()
    assert win.state_data["lecturers"] == ["Ada Lovelace"], (
        "one Ctrl+Z after a cancelled Bulk Add did not reach the previous "
        "real action: lecturers are %r" % (win.state_data["lecturers"],))


def test_a_cancelled_bulk_add_at_the_undo_cap_keeps_the_whole_history(
        win, monkeypatch):
    """At the cap the append evicts ``_undo_stack[0]``, unrecoverably."""
    _seed(win)
    _the_user_enters_and_then_cancels(monkeypatch, _two_classes())

    labels_before = _fill_undo_to_the_cap(win)

    win.bulk_add_classes()

    labels_after = [e[0] for e in win._undo_stack]
    assert labels_after == labels_before, (
        "a cancelled Bulk Add changed the undo history at the cap.\n"
        "  depth %d -> %d\n  oldest %r -> %r\n  newest %r -> %r"
        % (len(labels_before), len(labels_after),
           labels_before[0], labels_after[0] if labels_after else None,
           labels_before[-1], labels_after[-1] if labels_after else None))


# ── the control: an ACCEPTED bulk add must still record one entry ───────────

def test_an_accepted_bulk_add_still_records_exactly_one_undo_entry(
        win, monkeypatch):
    """Green today and must stay green.

    The fix moves the commit below ``_schedule_new_classes``; this is what
    stops it moving so far that a real batch add becomes un-undoable, or that
    a real one stops invalidating redo.
    """
    from PyQt6.QtWidgets import QDialog
    from scheduler_app.ui.dialogs import BulkAddDialog, BulkResultsDialog

    _seed(win)
    classes = _two_classes()

    def _add_exec(self):
        self.result = classes
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BulkAddDialog, "exec", _add_exec)
    monkeypatch.setattr(BulkResultsDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)

    _arm_a_pending_redo(win)
    placements_before = snapshot_placements(win.state_data)

    win.bulk_add_classes()

    names = [c["name"] for c in win.state_data["classes"]]
    assert "Kimya" in names and "Biyoloji" in names, (
        "the accepted batch did not add the classes: %r" % (names,))
    assert snapshot_placements(win.state_data) != placements_before, (
        "the accepted batch changed no placement, so this control is not "
        "measuring a real edit")

    assert len(win._undo_stack) == 1, (
        "an accepted Bulk Add must leave exactly one undo entry; stack is %r"
        % ([e[0] for e in win._undo_stack],))
    assert win._redo_stack == [], (
        "an accepted Bulk Add is a real edit and must invalidate redo; "
        "depth is %d" % len(win._redo_stack))

    win.undo()
    names_after = [c["name"] for c in win.state_data["classes"]]
    assert names_after == ["Fizik"], (
        "one Ctrl+Z did not take the whole batch back out: %r" % (names_after,))
