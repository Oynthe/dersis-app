"""Phase 10, item 7 — Add Class, then Cancel in the results dialog.

The shape
---------
``SchedulerApp.add_class`` (``scheduler_app/ui/app.py`` ~3402) does::

    snap_before = capture_snapshot(self.state_data)
    self._push_undo(tr("actions.add").format(name=cls["name"]))
    split_classes = split_non_joint(cls)
    if self._schedule_new_classes(split_classes):
        ...

``_push_undo`` is ``_commit_undo_entry`` — append, evict at the cap, and
``_redo_stack.clear()``. It fires ABOVE ``_schedule_new_classes``, which has
five exits and only two of them change any state:

===  ==============================  ==================================
 #   exit                            did state change?
===  ==============================  ==================================
 1   ``single_success``              yes — the class is added and placed
 2   ``single_failed`` + pinned      **no** — the workflow removed the
                                     class again; returns False
 3   ``single_failed`` + unpinned    **no** — same removal; returns True
                                     under a comment that claims
                                     "class stays in state (unplaced)"
 4   results dialog accepted         yes — ``apply_schedule_result``
 5   results dialog **rejected**     **no** — ``rollback_schedule``
                                     removes the new classes and calls
                                     ``restore_placements``
===  ==============================  ==================================

Exits 2, 3 and 5 leave the timetable byte-identical and still cost the user
their whole redo stack, plus ``_undo_stack[0]`` once the stack is at the
50-entry cap — neither of which any ``pop()`` can put back. This is Phase 9's
B1/B2 at a third site.

What is driven, and what is simulated
-------------------------------------
The production ``add_class`` is called. Only the two modal dialogs are
simulated, because both block on a human:

* ``_class_form_result`` is replaced by a function returning the class dict the
  user "typed". That is the gesture's INPUT, not the state under observation.
* ``BulkResultsDialog`` is constructed for real; only ``.exec()`` is replaced,
  and it returns ``Rejected`` — the Cancel button.

Nothing here hand-writes an undo entry, a redo entry or a placement: every
number asserted below is produced by ``ui/app.py``.
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
    """One day, two hours, one room, one lesson already at 09:00."""
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


def _empty_seed(win):
    """The same grid with a room nothing can use and no lessons on it."""
    s = win.state_data
    s["days"] = ["monday"]
    s["slots"] = ["09:00", "10:00"]
    s["classrooms"] = ["R001"]
    s["classroom_capacities"] = {"R001": 40}
    s["lecturers"] = ["Ada Lovelace"]
    s["years"] = {"Year-1": ["A"]}
    s["classes"] = []


@pytest.fixture
def win(make_app, monkeypatch):
    """A real SchedulerApp with the modal boxes captured rather than shown."""
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


def _type_this_class(monkeypatch, cls):
    """Make the class form come back with *cls*, as if the user typed it."""
    monkeypatch.setattr("scheduler_app.ui.app._class_form_result",
                        lambda parent, state, edit_cls: cls)


def _the_user_presses_cancel(monkeypatch):
    """Reject the results dialog. Returns a list that records the rejection."""
    from PyQt6.QtWidgets import QDialog
    from scheduler_app.ui.dialogs import BulkResultsDialog

    shown = []

    def _exec(self):
        shown.append((len(self._placed_rows) if hasattr(self, "_placed_rows")
                      else None))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(BulkResultsDialog, "exec", _exec)
    return shown


def _arm_a_pending_redo(win):
    """One real action plus one real Ctrl+Z, leaving exactly one redo entry.

    ``lecturers`` is the discriminator: whether Ctrl+Y still works is visible
    in the state itself, not only in a stack depth.
    """
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


# ── the scenario itself, asserted before anything is measured on it ─────────

def test_the_scenario_really_reaches_the_results_dialog(win, monkeypatch):
    """Anti-vacuity guard for every test below.

    A single-class add normally never opens ``BulkResultsDialog``: the fast
    path (``single_success``) and the negotiation path (``single_failed``)
    both return first. It opens when Phase 1 of ``optimized_batch_schedule``
    cannot place the new lesson but Phase 2 can, by relocating one that was
    already on the grid — which is what this grid forces. If this test ever
    goes red the others are measuring a path the user never sees.
    """
    _seed(win)
    _type_this_class(monkeypatch, _new_cls("Kimya", allowed_times=["09:00"]))
    shown = _the_user_presses_cancel(monkeypatch)

    win.add_class()

    assert shown, (
        "the results dialog never opened, so no test in this file exercises "
        "the Cancel path; classes are %r"
        % ([(c["name"], c["placed"]) for c in win.state_data["classes"]],))


# ── the timetable really is untouched ───────────────────────────────────────

def test_a_cancelled_add_leaves_the_timetable_byte_identical(win, monkeypatch):
    """The premise every assertion below rests on.

    ``rollback_schedule`` removes the new classes and calls
    ``restore_placements``. If it left anything behind, "the gesture changed
    nothing" would be false and an undo entry would be defensible.
    """
    _seed(win)
    _type_this_class(monkeypatch, _new_cls("Kimya", allowed_times=["09:00"]))
    _the_user_presses_cancel(monkeypatch)

    before = copy.deepcopy(win.state_data)
    win.add_class()
    after = copy.deepcopy(win.state_data)

    assert after == before, (
        "the rollback did not restore the state, so this file's premise is "
        "wrong. classes before %r, after %r; placements before %r, after %r"
        % ([c["name"] for c in before["classes"]],
           [c["name"] for c in after["classes"]],
           snapshot_placements(before), snapshot_placements(after)))


# ── item 7 proper ───────────────────────────────────────────────────────────

def test_a_cancelled_add_leaves_the_redo_stack_intact(win, monkeypatch):
    """The user undid something, then added a class and pressed Cancel.

    Nothing was added, nothing moved. Ctrl+Y must still work.
    """
    _seed(win)
    _arm_a_pending_redo(win)
    _type_this_class(monkeypatch, _new_cls("Kimya", allowed_times=["09:00"]))
    _the_user_presses_cancel(monkeypatch)

    redo_before = len(win._redo_stack)
    undo_before = [e[0] for e in win._undo_stack]

    win.add_class()

    redo_after = len(win._redo_stack)
    undo_after = [e[0] for e in win._undo_stack]

    assert redo_after == redo_before == 1, (
        "a cancelled Add Class destroyed the pending redo entry: depth "
        "%d -> %d. The add was rolled back and the timetable is unchanged, "
        "so Ctrl+Y must survive it. (undo labels %r -> %r)"
        % (redo_before, redo_after, undo_before, undo_after))

    win.redo()
    assert win.state_data["lecturers"] == ["Ada L."], (
        "redo after a cancelled Add Class did not re-apply the undone "
        "action; lecturers are %r" % (win.state_data["lecturers"],))


def test_a_cancelled_add_leaves_no_undo_entry(win, monkeypatch):
    """One Ctrl+Z after a cancelled add must undo the action BEFORE it."""
    _seed(win)
    _type_this_class(monkeypatch, _new_cls("Kimya", allowed_times=["09:00"]))
    _the_user_presses_cancel(monkeypatch)

    win._push_undo("rename-lecturer")
    win.state_data["lecturers"] = ["Ada L."]

    win.add_class()

    assert [e[0] for e in win._undo_stack] == ["rename-lecturer"], (
        "a cancelled Add Class left a no-op entry on the undo stack: %r"
        % ([e[0] for e in win._undo_stack],))

    win.undo()
    assert win.state_data["lecturers"] == ["Ada Lovelace"], (
        "one Ctrl+Z after a cancelled add did not reach the previous real "
        "action: lecturers are %r, expected ['Ada Lovelace']"
        % (win.state_data["lecturers"],))


def test_a_cancelled_add_at_the_undo_cap_keeps_the_whole_history(
        win, monkeypatch):
    """At the 50-entry cap the append evicts ``_undo_stack[0]``.

    Identity, not only depth: the entry that is lost is the user's OLDEST
    undoable action, and no ``pop()`` takes from that end.
    """
    _seed(win)
    _type_this_class(monkeypatch, _new_cls("Kimya", allowed_times=["09:00"]))
    _the_user_presses_cancel(monkeypatch)

    labels_before = _fill_undo_to_the_cap(win)

    win.add_class()

    labels_after = [e[0] for e in win._undo_stack]
    assert labels_after == labels_before, (
        "a cancelled Add Class changed the undo history at the cap.\n"
        "  depth %d -> %d\n  oldest %r -> %r\n  newest %r -> %r"
        % (len(labels_before), len(labels_after),
           labels_before[0], labels_after[0] if labels_after else None,
           labels_before[-1], labels_after[-1] if labels_after else None))


# ── the same wound at exit 3 of the five ────────────────────────────────────

def test_an_unplaceable_single_add_leaves_the_stacks_alone(win, monkeypatch):
    """``single_failed`` + unpinned: the workflow removes the class again.

    ``SchedulingWorkflow.schedule_new_classes`` does
    ``self.state["classes"].remove(cls)`` on this exit, so the state is
    unchanged when control returns — even though ``_schedule_new_classes``
    returns True under a comment reading "class stays in state (unplaced)".
    The undo entry ``add_class`` pushed above it therefore records nothing,
    and the redo stack it cleared is gone for a gesture that added no class.
    """
    _empty_seed(win)
    _arm_a_pending_redo(win)
    cls = _new_cls("Kimya", required_classrooms=["Lab X"])
    _type_this_class(monkeypatch, cls)

    before = copy.deepcopy(win.state_data)
    win.add_class()
    after = copy.deepcopy(win.state_data)

    # Anti-vacuity: this is the exit under test, not some other one.
    assert after["classes"] == before["classes"], (
        "the class survived the add, so this is not the single_failed exit: "
        "classes are %r"
        % ([(c["name"], c["placed"]) for c in after["classes"]],))

    assert len(win._redo_stack) == 1, (
        "an add that added nothing destroyed the pending redo entry: depth "
        "is %d, it was 1" % len(win._redo_stack))
    assert win._undo_stack == [], (
        "an add that added nothing left an undo entry behind: %r"
        % ([e[0] for e in win._undo_stack],))
