"""Dragging a lesson onto a cell, against the code the user actually runs.

Why this module exists
----------------------
Drag-and-drop is the app's primary editing gesture and, before this file, it
had **no test at all**. Two measurements, both reproduced in Phase 7:

1. Replace the body of ``SchedulerApp._execute_drop`` (``ui/app.py``:4546) with
   a bare ``return`` and the whole CI lane — 859 passed — stays **green**. Every
   drag the user makes could commit nothing and nothing in the suite would say
   so.

2. ``tests/test_validator_unification.py``'s ``_drop_verdict`` helper says it
   "mirrors ui/app.py::_execute_drop (:3910-3984) phase for phase". The line
   reference is hundreds of lines stale and the replica is missing two of
   production's inputs, so it answers differently from the code it claims to
   mirror:

   * it calls ``validate_drop`` with **no ``drag_backup``**, so the
     ``protection == "same_day"`` branch (``core/workflow.py``:696-699) can
     never fire from it. A protected lesson dropped on another day: production
     ``valid=False``, replica ``valid=True``;
   * it calls ``find_drop_classroom`` with **no ``preferred_rooms``**, while
     production derives them in ``_get_preferred_rooms`` (``ui/app.py``:4495)
     from ``notebook.currentIndex()`` and ``classroom_filter.currentData()``.
     Same lesson, same cell: replica picks the first candidate room, production
     honours the filter.

   So both ST-ARCH-004 regression guards run against a hand copy. That is
   Phase 6's own lesson — *ask which copy of the code the user runs* — one
   layer down, inside ``tests/``.

Every test below drives the real ``SchedulerApp`` through the ``make_app``
fixture, with no refactor of any kind. Nothing here asserts a pixel: the
offscreen platform has no Segoe UI and its advances run 1.5-2x native, so all
assertions are on state, on the undo stack, or on whether a refusal was
reported.

Simulating the drag
-------------------
``_execute_drop`` is the *end* of a gesture that Qt starts. The three fields it
reads — ``_dragging_cls``, ``_dragging_classes``, ``_drag_backup`` — are set by
``_start_drag_gfx`` (:4363-4373, dragging a placed lesson out of the grid) and
``_start_drag_unplaced`` (:4449-4457, dragging from the sidebar). Driving those
needs a live ``QDrag`` and a rendered ``LessonItem``, so the two helpers below
reproduce exactly the field assignments each of them makes, including
``_start_drag_gfx``'s pre-emptive undo snapshot and its ``mark_unplaced`` — the
lesson is already off the grid by the time the drop is validated, which is what
frees its own room and its own cell.

Findings guarded here: ST-ARCH-004, ST-ARCH-005, ST-ARCH-012.
"""
import pytest

from scheduler_app.core.models import (
    LOCATION_ONLINE,
    mark_placed,
    mark_unplaced,
    new_class,
)
from scheduler_app.translations import tr

pytestmark = pytest.mark.ui


# ── the world the drop happens in ───────────────────────────────────────────

def _seed(win, location_type=None):
    """A two-day, three-hour grid, two rooms, one placed lesson in R001."""
    s = win.state_data
    s["days"] = ["monday", "tuesday"]
    s["slots"] = ["09:00", "10:00", "11:00"]
    s["classrooms"] = ["R001", "R002"]
    s["classroom_capacities"] = {"R001": 40, "R002": 40}
    s["lecturers"] = ["Ada Lovelace"]
    s["classes"] = []

    cls = new_class()
    cls.update(name="Fizik", lecturer="Ada Lovelace", duration=1,
               student_count=10)
    if location_type:
        cls["location_type"] = location_type
    s["classes"].append(cls)
    mark_placed(cls, "monday", "09:00", "R001")
    return cls


def _arm_drag_from_grid(win, cls):
    """Reproduce `_start_drag_gfx` (app.py:4363-4373) for a placed lesson.

    Order matters and is production's: snapshot the placement, push the
    pre-emptive "unplace" undo entry, *then* unplace. `_execute_drop` pops that
    entry and replaces it with a "move" one, which is what makes a whole drag
    a single Ctrl+Z (ST-ARCH-012).
    """
    win._dragging_cls = cls
    win._dragging_classes = [cls]
    win._drag_backup = {
        "placed": cls["placed"],
        "placed_day": cls["placed_day"],
        "placed_time": cls["placed_time"],
        "placed_classroom": cls["placed_classroom"],
    }
    win._push_undo(tr("actions.unplace").format(name=cls["name"]))
    mark_unplaced(cls)
    win._drag_success = False


def _arm_drag_from_sidebar(win, cls):
    """Reproduce `_start_drag_unplaced` (app.py:4449-4457).

    The backup is all-None here, which is the case that leaves
    `_get_preferred_rooms` with nothing but the classroom filter to go on.
    """
    mark_unplaced(cls)
    win._dragging_cls = cls
    win._dragging_classes = [cls]
    win._drag_backup = {"placed": False, "placed_day": None,
                        "placed_time": None, "placed_classroom": None}
    win._drag_success = False


@pytest.fixture
def win(make_app, monkeypatch):
    """A real SchedulerApp with the modal refusal dialogs captured, not shown.

    `_execute_drop` reports every refusal through `QMessageBox.warning`, which
    blocks forever without a human. Capturing it is also the only way to assert
    the *second* half of a refusal: that the user was told.
    """
    from PyQt6.QtWidgets import QMessageBox

    w = make_app()
    refusals = []

    def _capture(*args, **kwargs):
        refusals.append(args)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_capture))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(_capture))
    w.refusals = refusals
    return w


def _placement(cls):
    return (cls.get("placed"), cls.get("placed_day"),
            cls.get("placed_time"), cls.get("placed_classroom"))


# ── the gesture, end to end ─────────────────────────────────────────────────

def test_a_legal_drop_moves_the_lesson(win):
    """ST-ARCH-004 — the whole point of the gesture.

    Mutation that proves this pins something: `return` as the first statement
    of `_execute_drop`. The lesson stays unplaced (the pre-emptive
    `mark_unplaced` is never undone) and this goes red, while the rest of the
    CI lane stays green.
    """
    cls = _seed(win)
    _arm_drag_from_grid(win, cls)

    win._execute_drop("tuesday", "10:00")

    assert _placement(cls) == (True, "tuesday", "10:00", "R001"), (
        "the drop committed nothing; the lesson is %r and the refusals seen "
        "were %r" % (_placement(cls), win.refusals))
    assert win._drag_success is True, (
        "_drag_success stayed False, so dragLeave will roll the drag back and "
        "the move the user made will vanish")
    assert not win.refusals, "a legal drop reported a refusal"


def test_an_off_grid_drop_is_refused_and_says_why(win):
    """ST-ARCH-004 — a refusal must be both a refusal and a message.

    A silent refusal is the worse bug of the two: the lesson has already been
    `mark_unplaced`'d by the drag start, so a drop that neither commits nor
    explains looks to the user like the lesson was deleted.
    """
    cls = _seed(win)
    _arm_drag_from_grid(win, cls)

    win._execute_drop("saturday", "09:00")

    assert not cls["placed"], "a day that is not on the grid was committed"
    assert win.refusals, "the drop was refused silently"


def test_same_day_protection_survives_a_drag(win):
    """ST-ARCH-004 — the rule `_drop_verdict` structurally cannot see.

    `protection == "same_day"` is enforced in `workflow.validate_drop`
    :696-699, and only when a `drag_backup` is passed. The replica in
    test_validator_unification.py passes none, so its 40-case matrix and its
    cell-by-cell sweep both answer `valid=True` here while production answers
    `valid=False`.

    Mutation: drop the `drag_backup=` argument from the `validate_drop` call in
    `_execute_drop` and this goes red — the exact drift the replica cannot
    detect, because it *is* the drift.
    """
    cls = _seed(win)
    cls["protection"] = "same_day"
    _arm_drag_from_grid(win, cls)

    win._execute_drop("tuesday", "10:00")

    assert not cls["placed"], (
        "a lesson protected to monday was dragged onto tuesday")
    assert win.refusals, "the refusal was silent"


def test_the_classroom_filter_is_honoured_by_a_drop(win):
    """ST-ARCH-004 — the second half of the replica's drift.

    Production derives an ordered room preference in `_get_preferred_rooms`
    (app.py:4495) from the classroom tab's filter combo; the replica calls
    `find_drop_classroom` with none. Both R001 and R002 fit this lesson, so the
    two answers differ: filtered to R002 production must land in R002, and
    anything that stops reading the filter lands in R001, the first candidate.

    Dragged from the sidebar on purpose — a lesson dragged out of the grid
    carries its old room as the *first* preference, which would mask this.

    Mutation: make `_get_preferred_rooms` return `[]` and this goes red.
    """
    from scheduler_app.ui.app import _encode_classroom_filter_room

    cls = _seed(win)
    win._update_filters()
    win.notebook.setCurrentIndex(0)
    idx = win.classroom_filter.findData(_encode_classroom_filter_room("R002"))
    assert idx >= 0, ("R002 is not in the classroom filter; this test would "
                      "pass without exercising the preference at all")
    win.classroom_filter.setCurrentIndex(idx)

    _arm_drag_from_sidebar(win, cls)
    win._execute_drop("tuesday", "10:00")

    assert cls["placed_classroom"] == "R002", (
        "the drop ignored the classroom filter and put the lesson in %r; both "
        "rooms fit, so this is the room preference being dropped on the floor"
        % (cls["placed_classroom"],))


def test_a_lesson_dragged_across_the_grid_keeps_its_own_room(win):
    """ST-ARCH-004 — the first entry of the preference list.

    `_get_preferred_rooms` puts `_drag_backup["placed_classroom"]` ahead of the
    filter, so moving a lesson to a different hour does not silently relocate
    it to another room. Without it a user who drags a lesson one hour later
    finds the room changed too, and the room-load analytics move with it.

    Mutation: delete the `_drag_backup` branch of `_get_preferred_rooms` and
    the lesson lands in R001 anyway *unless* R001 is not the first candidate —
    which is why the filter is pointed at R002 here, making the two answers
    differ.
    """
    from scheduler_app.ui.app import _encode_classroom_filter_room

    cls = _seed(win)
    mark_placed(cls, "monday", "09:00", "R002")
    win._update_filters()
    win.notebook.setCurrentIndex(0)
    win.classroom_filter.setCurrentIndex(
        win.classroom_filter.findData(_encode_classroom_filter_room("R001")))

    _arm_drag_from_grid(win, cls)
    win._execute_drop("tuesday", "11:00")

    assert cls["placed_classroom"] == "R002", (
        "the lesson was moved out of its own room into %r; the room it came "
        "from must outrank the filter" % (cls["placed_classroom"],))


def test_an_online_lesson_is_committed_without_a_room(win):
    """ST-ARCH-004 — `room is None` is an answer, not a failure.

    `get_room_candidates` yields the `None` sentinel for a lesson that needs no
    room, and `_execute_drop` only treats `None` as a failure when
    `needs_physical_room(cls)`. Before ST-ARCH-004 the drag path committed a
    physical classroom onto an online lesson while `apply_reschedule` stored
    `None` for the same lesson, so the same lesson showed a room or no room
    depending on who placed it.

    Mutation: drop the `and needs_physical_room(cls)` guard from either of the
    two `room is None` tests in `_execute_drop` and this goes red — the online
    lesson is refused with "no compatible classrooms".
    """
    cls = _seed(win, location_type=LOCATION_ONLINE)
    _arm_drag_from_grid(win, cls)

    win._execute_drop("tuesday", "10:00")

    assert cls["placed"], (
        "an online lesson was refused for want of a room: %r" % (win.refusals,))
    assert cls["placed_day"] == "tuesday"
    assert cls["placed_classroom"] in (None, ""), (
        "an online lesson was committed into physical room %r"
        % (cls["placed_classroom"],))


def test_a_lesson_that_needs_a_room_and_has_none_is_refused(win):
    """ST-ARCH-004 — the other side of the same branch.

    A face-to-face lesson whose only permitted room does not exist has no
    candidate at all. It must be refused and said so, not committed roomless —
    a roomless face-to-face lesson is a lesson the timetable claims is
    happening nowhere.
    """
    cls = _seed(win)
    cls["required_classrooms"] = ["R404"]
    _arm_drag_from_grid(win, cls)

    win._execute_drop("tuesday", "10:00")

    assert not cls["placed"], "a face-to-face lesson was placed with no room"
    assert win.refusals, "the refusal was silent"


def test_a_committed_drop_is_one_undo_entry(win):
    """ST-ARCH-012 — one gesture, one Ctrl+Z.

    The drag start pushes an "unplace" snapshot pre-emptively so a *failed*
    drag can be rolled back; a *successful* one pops it and pushes a "move"
    snapshot with identical data and a better label. Miss the pop and every
    drag costs the user two undos, the first of which appears to do nothing.

    Mutation: delete `self._undo_stack.pop()` from `_execute_drop` and the
    depth assertion below goes red at 2.
    """
    cls = _seed(win)
    depth_before = len(win._undo_stack)

    _arm_drag_from_grid(win, cls)
    assert len(win._undo_stack) == depth_before + 1, (
        "the drag start did not push its pre-emptive snapshot; this test "
        "would then be measuring nothing")

    win._execute_drop("tuesday", "10:00")

    assert len(win._undo_stack) == depth_before + 1, (
        "one drag left %d undo entries instead of one"
        % (len(win._undo_stack) - depth_before,))
    assert win._undo_stack[-1][0] == tr("actions.move").format(name=cls["name"]), (
        "the surviving entry is labelled %r, so the pop/push swapped the "
        "wrong one" % (win._undo_stack[-1][0],))

    # What that one entry *restores* is a separate question, and the answer is
    # wrong today: see the pin below.


@pytest.mark.xfail(
    strict=True,
    reason="ST-ARCH-012 (new instance, measured Phase 7 2026-08-28) — "
           "_execute_drop pops the pre-emptive snapshot and pushes a fresh one "
           "at app.py:4610-4616, but by then _start_drag_gfx's mark_unplaced "
           "has already run, so the 'move' snapshot captures the UNPLACED "
           "state. One Ctrl+Z after a successful drag therefore unplaces the "
           "lesson instead of returning it. The comment on those lines says "
           "'The snapshot data is identical - only the label differs'; "
           "measured, it is not")
def test_one_undo_after_a_drag_puts_the_lesson_back_where_it_was(win):
    """ST-ARCH-012: undoing a move must undo the move, not the whole placement.

    A failure (today) means a user who drags a lesson one hour to the right and
    changes their mind gets the lesson back in the unplaced sidebar, and has to
    find its original cell from memory. Measured: after the drag the class is
    ``(False, None, None, None)`` where it should be
    ``(True, 'monday', '09:00', 'R001')``.

    The mechanism is exact, and the contrast that proves it is one test up:
    ``test_a_refused_drop_leaves_the_undo_stack_alone`` passes, because the
    refusal path keeps ``_start_drag_gfx``'s snapshot — taken *before*
    ``mark_unplaced`` — and that one does restore the placement. Only the
    success path replaces it with one taken after.

    The fix belongs in ``ui/app.py`` and is not in this change's scope: either
    push the "move" snapshot from the placement captured in ``_drag_backup``
    rather than from live state, or relabel the popped entry in place instead
    of popping and re-pushing it.
    """
    cls = _seed(win)
    _arm_drag_from_grid(win, cls)
    win._execute_drop("tuesday", "10:00")
    assert win._drag_success, "the drop did not commit; wrong failure"

    win.undo()

    back = win.state_data["classes"][0]
    assert _placement(back) == (True, "monday", "09:00", "R001"), (
        "a single undo did not put the lesson back where the drag found it: "
        "%r" % (_placement(back),))


def test_a_refused_drop_leaves_the_undo_stack_alone(win):
    """ST-ARCH-012 — a refusal must not eat the pre-emptive snapshot either.

    `_execute_drop` returns early on every refusal path without popping. The
    snapshot stays for `dragLeaveEvent`/`_cancel_drag` to use, and it is what
    puts the lesson back on the grid after the user is told no. Popping it here
    instead would leave the lesson unplaced with nothing to restore it.
    """
    cls = _seed(win)
    depth_before = len(win._undo_stack)
    _arm_drag_from_grid(win, cls)

    win._execute_drop("saturday", "09:00")

    assert win.refusals
    assert len(win._undo_stack) == depth_before + 1, (
        "the refusal path changed the undo depth to %d; the pre-emptive "
        "snapshot is the only record of where the lesson was"
        % (len(win._undo_stack) - depth_before,))
    win.undo()
    assert _placement(win.state_data["classes"][0]) == (
        True, "monday", "09:00", "R001")
