"""Dragging a lesson onto a cell, against the code the user actually runs.

Why this module exists
----------------------
Drag-and-drop is the app's primary editing gesture and, before this file, it
had **no test at all**. Two measurements, both reproduced in Phase 7:

1. Replace the body of ``SchedulerApp._execute_drop`` (``ui/app.py``) with
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
     production derives them in ``_get_preferred_rooms`` (``ui/app.py``)
     from ``notebook.currentIndex()`` and ``classroom_filter.currentData()``.
     Same lesson, same cell: replica picks the first candidate room, production
     honours the filter.

   So both ST-ARCH-004 regression guards run against a hand copy. That is
   Phase 6's own lesson — *ask which copy of the code the user runs* — one
   layer down, inside ``tests/``.

Symbol names, never ``file:line``
---------------------------------
Measurement 2 above is about a stale line reference, and this module then
carried seven of its own. Re-measured 2026-08-29 at ca781f1, every
``ui/app.py`` line number written here was wrong, by +33, +33, +33, +33, +41,
+47 and +117 lines — and the worst of them had been *refreshed* one phase
earlier, which is the point: re-numbering restores exactly the failure it is
meant to fix. They are gone; the symbols are stable and greppable, so name
those. (The two references to other files — ``core/workflow.py``:696-699 for
the ``same_day`` branch and ``ui/renderer.py``:2052 for ``dragLeaveEvent`` —
were re-measured too and are both exact, so they stand as written.)

Every test below drives the real ``SchedulerApp`` through the ``make_app``
fixture, with no refactor of any kind. Nothing here asserts a pixel: the
offscreen platform has no Segoe UI and its advances run 1.5-2x native, so all
assertions are on state, on the undo stack, or on whether a refusal was
reported.

Simulating the drag
-------------------
``_execute_drop`` is the *end* of a gesture that Qt starts. The fields it
reads — ``_dragging_cls``, ``_dragging_classes``, ``_drag_backup``,
``_drag_undo_pushed`` and ``_drag_undo_entry`` — are set by ``_start_drag_gfx``
(dragging a placed lesson out of the grid) and ``_start_drag_unplaced``
(dragging from the sidebar). Driving those needs a live ``QDrag`` and a
rendered ``LessonItem``, so the two helpers below reproduce exactly the field
assignments each of them makes, including ``_start_drag_gfx``'s *held*
pre-gesture snapshot and its ``mark_unplaced`` — the lesson is already off the
grid by the time the drop is validated, which is what frees its own room and
its own cell.

The helpers mirror production or they measure nothing: ``_drag_undo_pushed``
and ``_drag_undo_entry`` are what tell ``_execute_drop`` that the pre-gesture
snapshot is this drag's to consume, so a helper that forgot to set them would
send every grid drag down the branch meant for sidebar drags. Phase 9 moved
that snapshot off the undo stack and into ``_drag_undo_entry`` — a gesture the
user abandons must not clear redo or evict at the cap — and the helper moved
with it. A helper still calling ``_push_undo`` would certify a path production
no longer has.

Findings guarded here: ST-ARCH-004, ST-ARCH-005, ST-ARCH-012.
"""
import copy

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


def _add_class(win, name):
    """A second, unplaced lesson in the world `_seed` built."""
    cls = new_class()
    cls.update(name=name, lecturer="Ada Lovelace", duration=1,
               student_count=10)
    win.state_data["classes"].append(cls)
    return cls


def _arm_drag_from_grid(win, cls, also=()):
    """Reproduce `_start_drag_gfx` in `ui/app.py` for a placed lesson.

    Order matters and is production's: back the placement up, take the
    "unplace" snapshot, raise `_drag_undo_pushed`, *then* unplace.
    `_execute_drop` records that snapshot under the "move" label rather than
    taking a fresh one, which is what makes a whole drag a single Ctrl+Z that
    actually restores the placement (ST-ARCH-012).

    The snapshot is HELD in `_drag_undo_entry`, not pushed. It went straight
    onto the undo stack until Phase 9, and that is what made an abandoned
    gesture destructive: `_push_undo` clears the redo stack and, at the
    50-entry cap, evicts `_undo_stack[0]`, and the `pop()` on the cancel path
    put back neither. `test_a_cancelled_grid_drag_leaves_the_undo_stack_as_it
    _found_it` and `test_a_cancelled_grid_drag_does_not_destroy_the_redo_stack`
    drive that through the real starter.

    `also` carries the extra lessons of a multi-selection into
    `_dragging_classes`. Production backs up and unplaces only the primary —
    see `_start_drag_gfx`, which builds `_drag_backup` from `cls` alone — so
    this helper does the same.
    """
    win._dragging_cls = cls
    win._dragging_classes = [cls] + list(also)
    win._drag_backup = {
        "placed": cls["placed"],
        "placed_day": cls["placed_day"],
        "placed_time": cls["placed_time"],
        "placed_classroom": cls["placed_classroom"],
    }
    win._drag_undo_entry = (tr("actions.unplace").format(name=cls["name"]),
                            copy.deepcopy(win.state_data))
    win._drag_undo_pushed = True
    mark_unplaced(cls)
    win._drag_success = False


def _arm_drag_from_sidebar(win, cls):
    """Reproduce `_start_drag_unplaced` in `ui/app.py`.

    The backup is all-None here, which is the case that leaves
    `_get_preferred_rooms` with nothing but the classroom filter to go on.

    `_drag_undo_pushed` is False and `_drag_undo_entry` is None, and that is
    the whole point: this starter takes NO pre-gesture snapshot, so whatever is
    on top of the stack belongs to some earlier action and `_execute_drop` must
    push one of its own instead of consuming anything.
    """
    mark_unplaced(cls)
    win._dragging_cls = cls
    win._dragging_classes = [cls]
    win._drag_backup = {"placed": False, "placed_day": None,
                        "placed_time": None, "placed_classroom": None}
    win._drag_undo_entry = None
    win._drag_undo_pushed = False
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
    (`ui/app.py`) from the classroom tab's filter combo; the replica calls
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

    The drag start takes an "unplace" snapshot and holds it so a *failed* drag
    can be rolled back; a *successful* one records that same snapshot under the
    "move" label. Take a fresh snapshot instead and every drag costs the user
    two undos, the first of which appears to do nothing.

    Mutation: replace the record branch in `_execute_drop` with a bare
    `self._push_undo(move_label)` and the placement assertion in
    `test_one_undo_after_a_drag_puts_the_lesson_back_where_it_was` goes red
    (the fresh snapshot holds the lesson as unplaced). Before Phase 9 the
    depth assertion below caught that mutation too; it no longer can, because
    the snapshot is not on the stack for a second push to double.
    """
    cls = _seed(win)
    depth_before = len(win._undo_stack)

    _arm_drag_from_grid(win, cls)
    assert len(win._undo_stack) == depth_before, (
        "the drag start recorded its snapshot instead of holding it; a "
        "gesture the user can still abandon must not touch the stacks")
    assert win._drag_undo_entry is not None, (
        "the drag start did not take its pre-gesture snapshot; this test "
        "would then be measuring nothing")

    win._execute_drop("tuesday", "10:00")

    assert len(win._undo_stack) == depth_before + 1, (
        "one drag left %d undo entries instead of one"
        % (len(win._undo_stack) - depth_before,))
    assert win._undo_stack[-1][0] == tr("actions.move").format(name=cls["name"]), (
        "the surviving entry is labelled %r, so the pop/push swapped the "
        "wrong one" % (win._undo_stack[-1][0],))

    # What that one entry *restores* is a separate question, answered by
    # test_one_undo_after_a_drag_puts_the_lesson_back_where_it_was below.


def test_one_undo_after_a_drag_puts_the_lesson_back_where_it_was(win):
    """ST-ARCH-012: undoing a move must undo the move, not the whole placement.

    A failure means a user who drags a lesson one hour to the right and changes
    their mind gets the lesson back in the unplaced sidebar instead of its old
    cell, and has to find that cell from memory.

    Pinned strict-xfail through Phase 7 and fixed in Phase 8. The old
    ``_execute_drop`` popped ``_start_drag_gfx``'s snapshot — the only record
    of where the lesson was — and pushed a fresh one taken *after*
    ``mark_unplaced``, so the surviving "move" entry stored
    ``(False, None, None, None)``. It now re-labels the entry already on the
    stack instead of re-snapshotting live state.

    The contrast that proves the mechanism is one test down:
    ``test_a_refused_drop_leaves_the_undo_stack_alone`` passed even while this
    one failed, because the refusal path never reached the pop/push.
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
    """ST-ARCH-012 — a refusal must not consume the pre-gesture snapshot.

    `_execute_drop` returns early on every refusal path without recording
    anything, and leaves `_drag_undo_pushed` raised and `_drag_undo_entry`
    held, so the snapshot is still the drag's. What actually puts the lesson
    back on the grid is `_start_drag_gfx`'s own restore loop (`for k, v in
    self._drag_backup.items(): cls[k] = v`); the held snapshot is a second,
    belt-and-braces copy of the same placement, and it is what a *committed*
    drop turns into the user's Ctrl+Z. Recording it here — or dropping it —
    would leave a refused drop indistinguishable from a real edit.

    Phase 9 moved that snapshot off the undo stack, so what this test pins
    moved with it: the assertion used to be `depth == depth_before + 1`,
    which said the pre-emptive push was still standing. It is now that the
    refusal touched NEITHER stack. `tests/test_phase9_b1b2.py` holds the
    other half — the redo stack a refused drop used to destroy.

    (This docstring used to credit `dragLeaveEvent`/`_cancel_drag` with the
    restore. There is no `_cancel_drag` anywhere in `scheduler_app`, and
    `dragLeaveEvent` — `ui/renderer.py`:2052 — only clears the drop highlight.
    The assertions were right; the explanation was not.)
    """
    cls = _seed(win)
    win._push_undo("rename-lecturer")   # an earlier, unrelated action
    depth_before = len(win._undo_stack)
    _arm_drag_from_grid(win, cls)

    win._execute_drop("saturday", "09:00")

    assert win.refusals
    assert len(win._undo_stack) == depth_before, (
        "the refusal path changed the undo depth by %d; a drop the app "
        "rejected is not an edit and must record nothing"
        % (len(win._undo_stack) - depth_before,))
    assert win._drag_undo_pushed is True and win._drag_undo_entry is not None, (
        "the refusal consumed the pre-gesture snapshot; it is the only record "
        "of where the lesson was, and _start_drag_gfx's tail still needs it")

    held_label, held_state = win._drag_undo_entry
    assert held_label == tr("actions.unplace").format(name="Fizik")
    assert _placement(held_state["classes"][0]) == (
        True, "monday", "09:00", "R001"), (
        "the held snapshot no longer holds the pre-gesture placement: %r"
        % (_placement(held_state["classes"][0]),))


# ── the sidebar drag, which pushed nothing and popped anyway ────────────────

def test_a_sidebar_drag_does_not_eat_the_previous_undo_entry(win):
    """ST-ARCH-012 — dragging from the sidebar destroyed an unrelated undo.

    A failure means silent, permanent data loss from the user's point of view:
    they unplace a lesson, then drag a *different* lesson out of the sidebar
    onto the grid, and the unplace can never be undone again by any number of
    Ctrl+Z presses. The undo stack does not even get shorter — the drag's own
    entry takes the destroyed one's place — so nothing on screen says a thing.

    `_start_drag_unplaced` pushes no pre-emptive snapshot, but `_execute_drop`
    used to pop unconditionally (`if self._undo_stack: self._undo_stack.pop()`)
    and so removed whatever action happened to be on top. `_drag_undo_pushed`
    is what makes that pop conditional on there being something of this drag's
    to consume.
    """
    cls = _seed(win)
    other = _add_class(win, "Kimya")

    # A real, unrelated user action through production code, not a hand-built
    # stack entry: this is the snapshot the drag used to eat.
    win._unplace_specific(cls)
    assert len(win._undo_stack) == 1, (
        "the prior action did not push an undo entry; this test would then be "
        "measuring nothing")

    _arm_drag_from_sidebar(win, other)
    win._execute_drop("tuesday", "10:00")
    assert win._drag_success, (
        "the sidebar drop did not commit; wrong failure — refusals %r"
        % (win.refusals,))

    assert len(win._undo_stack) == 2, (
        "the sidebar drag left %d undo entries; it pushes one of its own and "
        "must not consume the one already there"
        % (len(win._undo_stack),))
    assert win._undo_stack[0][0] == tr("actions.unplace").format(
        name=cls["name"]), (
        "the earlier action's entry is now labelled %r, so the drag took its "
        "place on the stack instead of stacking on top of it"
        % (win._undo_stack[0][0],))

    # Two undos: the drag, then the unplace it must not have destroyed.
    win.undo()
    win.undo()
    # `undo` replaces every class dict wholesale, so re-resolve by index.
    assert _placement(win.state_data["classes"][0]) == (
        True, "monday", "09:00", "R001"), (
        "the unplace before the drag could not be undone: the lesson is %r. "
        "Its snapshot was destroyed by the drag."
        % (_placement(win.state_data["classes"][0]),))


def test_a_sidebar_drag_is_one_undo_of_its_own(win):
    """ST-ARCH-012 — one Ctrl+Z after a sidebar drag reverts one action.

    A failure means Ctrl+Z is unpredictable: one press either does too little
    (the lesson stays on the grid) or too much (it also silently reverts the
    action the user took *before* the drag, which they never asked to undo).

    This is the half that a bare relabel — reusing whatever entry is on top
    without checking whose it is — gets wrong. It fixes the grid drag, which
    is what makes it tempting, and on a sidebar drag it hijacks the previous
    action's snapshot so one undo rolls back two user actions. The redo
    assertion is the second half of the same trap: a relabel that never calls
    `_push_undo` never clears redo either, so a redo entry left over from
    before the drag survives, and Ctrl+Y then re-applies a state that predates
    a placement it knows nothing about.

    The arrangement below is built so that assertion can actually fail. Every
    real action clears redo, so the only way a redo entry can still be pending
    when a drag starts is that the user's last keystroke was Ctrl+Z — hence
    the two unplaces and the undo of the second one.
    """
    cls = _seed(win)
    other = _add_class(win, "Kimya")
    mark_placed(other, "monday", "10:00", "R002")
    third = _add_class(win, "Biyoloji")

    win._unplace_specific(cls)          # undo: [unplace Fizik]
    win._unplace_specific(other)        # undo: [unplace Fizik, unplace Kimya]
    win.undo()                          # undo: [unplace Fizik], redo: [Kimya]
    assert len(win._undo_stack) == 1 and len(win._redo_stack) == 1, (
        "the arrangement is wrong: undo %d / redo %d. Without a pending redo "
        "entry the redo assertion below cannot fail and pins nothing"
        % (len(win._undo_stack), len(win._redo_stack)))

    # `undo` replaced every class dict; the seeded references are orphans now.
    third = win.state_data["classes"][2]
    _arm_drag_from_sidebar(win, third)
    win._execute_drop("tuesday", "10:00")
    assert win._drag_success, (
        "the sidebar drop did not commit; wrong failure — refusals %r"
        % (win.refusals,))

    assert len(win._redo_stack) == 0, (
        "the drop left %d redo entries; a committed action must clear redo, "
        "or Ctrl+Y re-applies a state taken before the drag"
        % (len(win._redo_stack),))

    win.undo()

    dragged = win.state_data["classes"][2]
    earlier = win.state_data["classes"][0]
    assert _placement(dragged) == (False, None, None, None), (
        "one undo did not take the dragged lesson back off the grid: %r"
        % (_placement(dragged),))
    assert _placement(earlier) == (False, None, None, None), (
        "one undo reverted TWO actions: the unplace that happened before the "
        "drag was rolled back as well, and the lesson is %r"
        % (_placement(earlier),))


def test_a_multi_select_drag_is_one_undo_for_what_actually_moved(win):
    """ST-ARCH-012 — a multi-select drag moves one lesson, and undoes one.

    Pins today's behaviour, which is deliberately left alone by the Phase 8
    undo fix: `_start_drag_gfx` puts a whole selection in `_dragging_classes`
    but backs up and unplaces only the primary, so `_execute_drop`'s
    `all(not c.get("placed"))` guard is False and the single-lesson path runs.
    The other selected lessons do not move.

    That is arguably wrong for the user — they selected three lessons and one
    moved, with no toast saying so — but choosing between "move all", "refuse"
    and "say only one moved" is a product decision, not a bug fix, so this test
    records what the app does rather than what it should do. What it *must* not
    do is lose the others: a failure of the second half means one Ctrl+Z after
    a multi-select drag leaves the timetable in a state the user never made.
    """
    primary = _seed(win)
    secondary = _add_class(win, "Kimya")
    mark_placed(secondary, "monday", "10:00", "R002")

    _arm_drag_from_grid(win, primary, also=[secondary])
    win._execute_drop("tuesday", "11:00")
    assert win._drag_success, (
        "the drop did not commit; wrong failure — refusals %r" % (win.refusals,))

    assert _placement(primary) == (True, "tuesday", "11:00", "R001"), (
        "the dragged lesson did not land where it was dropped: %r"
        % (_placement(primary),))
    assert _placement(secondary) == (True, "monday", "10:00", "R002"), (
        "a lesson that was only along for the selection was moved to %r; the "
        "single-lesson drop path must touch nothing but the primary"
        % (_placement(secondary),))

    win.undo()

    back_primary = win.state_data["classes"][0]
    back_secondary = win.state_data["classes"][1]
    assert _placement(back_primary) == (True, "monday", "09:00", "R001"), (
        "one undo did not restore the lesson that moved: %r"
        % (_placement(back_primary),))
    assert _placement(back_secondary) == (True, "monday", "10:00", "R002"), (
        "one undo disturbed a lesson the drag never moved: %r"
        % (_placement(back_secondary),))


# ── the production starter, not a hand copy of it ───────────────────────────

class _FakeDrag:
    """Stand-in for ``QDrag`` that runs the drop instead of blocking on Qt.

    ``_start_drag_gfx`` ends in ``drag.exec(...)``, which blocks until the user
    releases the mouse and is what makes the real starter untestable in a
    headless run. Substituting the module-level ``QDrag`` name lets the gesture
    run end to end through production code: ``exec`` is where the drop happens
    in real life, so it is where the drop happens here.
    """

    def __init__(self, parent):
        self._on_exec = None

    def setMimeData(self, mime):
        pass

    def setPixmap(self, pm):
        pass

    def setHotSpot(self, pt):
        pass

    def exec(self, action):
        if self._on_exec is not None:
            self._on_exec()
        return action


class _StubItem:
    """The minimum ``LessonItem`` surface ``_start_drag_gfx`` touches.

    ``scene()`` returning None short-circuits the pixmap block (which is
    already wrapped in ``except Exception: pass``), and the absence of
    ``set_ghost`` skips the ghosting. Neither participates in the undo
    contract under test.
    """

    def scene(self):
        return None


def test_the_real_start_drag_gfx_wires_up_the_undo_entry(win, monkeypatch):
    """ST-ARCH-012 — the production starter must set the flag the fix reads.

    THIS TEST EXISTS BECAUSE EVERY OTHER TEST IN THIS MODULE HAND-COPIES
    ``_start_drag_gfx`` INSTEAD OF CALLING IT. Measured on the tree that
    shipped the fix: deleting ``self._drag_undo_pushed = True`` from production
    ``_start_drag_gfx`` left this whole module — and ``test_full_state_undo.py``
    — green, exit 0. The helpers set the flag themselves, so the production
    line that sets it was executed by nothing. The fix worked and its wiring
    was unverified, which is exactly how Phase 7's headline fix shipped
    inert: "the test passed because it stubbed the path".

    A failure means one Ctrl+Z after a real drag does not put the lesson back
    where the user found it — the defect ST-ARCH-012 is about — even though the
    hand-copied tests above still pass.
    """
    cls = _seed(win)
    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)

    captured = {}
    real_exec = _FakeDrag.exec

    def _drop_during_exec(self, action):
        # Mid-gesture: this is the state the fix depends on, and the state no
        # other test in this module ever observes.
        captured["flag_during_drag"] = win._drag_undo_pushed
        captured["depth_during_drag"] = len(win._undo_stack)
        held = win._drag_undo_entry
        captured["held_during_drag"] = (
            None if held is None
            else (held[0], _placement(held[1]["classes"][0])))
        win._execute_drop("tuesday", "10:00")
        return action

    monkeypatch.setattr(_FakeDrag, "exec", _drop_during_exec)

    win._start_drag_gfx(cls, _StubItem())

    assert captured.get("flag_during_drag") is True, (
        "production _start_drag_gfx did not set _drag_undo_pushed before the "
        "drag went live, so _execute_drop cannot tell whose snapshot it is "
        "holding")
    # Phase 9. This assertion was `depth_during_drag == 1` — the pre-emptive
    # snapshot sitting on the undo stack while the gesture was still
    # speculative, which is exactly what let an abandoned drag clear redo and
    # evict at the cap. What has to be true mid-gesture is that the snapshot
    # EXISTS and holds the pre-gesture placement, not that it is on a stack.
    assert captured.get("held_during_drag") == (
        tr("actions.unplace").format(name="Fizik"),
        (True, "monday", "09:00", "R001")), (
        "production _start_drag_gfx did not hold the pre-gesture snapshot, or "
        "took it after its own mark_unplaced: %r"
        % (captured.get("held_during_drag"),))
    assert captured.get("depth_during_drag") == 0, (
        "the snapshot went onto the undo stack while the drag was still "
        "live; depth was %r. A gesture the user can still abandon must not "
        "be able to clear redo or evict at the cap"
        % (captured.get("depth_during_drag"),))

    live = win.state_data["classes"][0]
    assert _placement(live) == (True, "tuesday", "10:00", "R001"), (
        "the drag did not commit through the real starter: %r"
        % (_placement(live),))
    assert len(win._undo_stack) == 1, (
        "a whole drag must be exactly one Ctrl+Z; depth is %d"
        % len(win._undo_stack))
    assert win._undo_stack[-1][0] == tr("actions.move").format(name="Fizik")
    assert win._drag_undo_pushed is False, (
        "the flag outlived the gesture; the next sidebar drag would pop an "
        "entry it never pushed")

    win.undo()

    back = win.state_data["classes"][0]
    assert _placement(back) == (True, "monday", "09:00", "R001"), (
        "one undo after a REAL drag did not put the lesson back where the "
        "drag found it: %r" % (_placement(back),))


def test_a_cancelled_grid_drag_leaves_the_undo_stack_as_it_found_it(
        win, monkeypatch):
    """ST-ARCH-012, the other half — the cancel path of the real starter.

    The test above drops inside ``exec`` and so only ever exercises the
    COMMIT half of the fix. The cancel half — ``_start_drag_gfx``'s
    ``if self._drag_undo_pushed and self._undo_stack: self._undo_stack.pop()``
    and the ``_drag_undo_pushed = False`` after it — was pinned by nothing:
    measured 2026-08-29 at bd12e58, replacing that ``if``/``pop`` pair with
    ``pass``, or deleting the flag reset, left ``tests/test_drag_and_drop.py``,
    ``tests/test_full_state_undo.py`` and
    ``tests/test_unplaced_panel_identity.py`` green — 24 passed, exit 0 — for
    either mutation.

    Here the substituted ``QDrag.exec`` returns WITHOUT dropping, which is
    what Qt does when the user presses Esc or releases over a non-target.
    Production must then undo its own pre-emptive bookkeeping: put the
    placement back from ``_drag_backup``, and discard the ``unplace``
    snapshot it took before ``mark_unplaced``.

    The discriminator is the pre-drag edit to ``lecturers``. The phantom
    snapshot, if it survives, was taken AFTER that edit, so restoring it is
    a no-op the user cannot see — the lesson's placement looks right either
    way. Only the earlier action's snapshot carries the old lecturer list,
    so ``undo`` reaching it is what proves the phantom is gone.

    A failure means every cancelled or refused drag leaves a phantom entry
    on the stack and the user's next Ctrl+Z undoes a gesture they abandoned
    instead of the action they actually took.

    Phase 9 closed the two things this docstring used to record as measured
    and deliberately un-asserted — a cancelled drag cleared the redo stack,
    and at the 50-entry ``_max_undo`` cap it evicted the oldest undo entry,
    neither of which the ``pop()`` on this path could put back. Both were
    symptoms of the snapshot going onto the stack at drag START; it is now
    held in ``_drag_undo_entry`` and recorded only at a commit point, so the
    cancel path has nothing to compensate for. The mid-gesture assertion
    below moved with that: it used to read
    ``["rename-lecturer", unplace-label]`` — the phantom being visible on the
    stack — and it is now that the stack is untouched while the snapshot is
    held. ``test_a_cancelled_grid_drag_does_not_destroy_the_redo_stack``
    below and ``tests/test_phase9_b1b2.py`` assert the redo and cap halves
    outright.
    """
    cls = _seed(win)

    # An earlier, unrelated action — the one the user's next Ctrl+Z must
    # reach. Its snapshot is the only one holding the old lecturer list.
    win._push_undo("rename-lecturer")
    win.state_data["lecturers"] = ["Ada L."]

    labels_before = [entry[0] for entry in win._undo_stack]
    assert labels_before == ["rename-lecturer"]

    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)

    captured = {}

    def _cancel_during_exec(self, action):
        # Mid-gesture, before the user gives up: production has already taken
        # its snapshot and taken the lesson off the grid. If these do not
        # hold, the drag never really ran and the assertions after it would
        # pass vacuously.
        captured["labels_during_drag"] = [e[0] for e in win._undo_stack]
        captured["held_during_drag"] = (
            None if win._drag_undo_entry is None else win._drag_undo_entry[0])
        captured["placement_during_drag"] = _placement(cls)
        return action  # the user pressed Esc: no drop

    monkeypatch.setattr(_FakeDrag, "exec", _cancel_during_exec)

    win._start_drag_gfx(cls, _StubItem())

    assert captured.get("held_during_drag") == tr(
        "actions.unplace").format(name="Fizik"), (
        "the gesture never got as far as the pre-gesture snapshot, so the "
        "cancel path below was not exercised; held entry was %r"
        % (captured.get("held_during_drag"),))
    assert captured.get("labels_during_drag") == ["rename-lecturer"], (
        "the live gesture put its snapshot on the undo stack; labels were %r. "
        "Holding it is the whole of the Phase 9 fix — on the stack it clears "
        "redo and evicts at the cap for a drag that may never happen"
        % (captured.get("labels_during_drag"),))
    assert captured.get("placement_during_drag") == (
        False, None, None, None), (
        "the lesson was not taken off the grid before the drag went live, "
        "so there is nothing for the cancel path to restore: %r"
        % (captured.get("placement_during_drag"),))

    live = win.state_data["classes"][0]
    assert _placement(live) == (True, "monday", "09:00", "R001"), (
        "a cancelled drag did not put the lesson back where it was: %r"
        % (_placement(live),))

    assert [entry[0] for entry in win._undo_stack] == labels_before, (
        "a cancelled drag left its pre-emptive snapshot on the undo stack; "
        "labels are %r, they were %r"
        % ([entry[0] for entry in win._undo_stack], labels_before))

    assert win._drag_undo_pushed is False, (
        "the flag outlived the cancelled gesture, so it no longer states "
        "whose snapshot the app is holding")
    assert win._drag_undo_entry is None, (
        "the abandoned gesture's snapshot outlived it; the next drop would "
        "record a state from a drag the user gave up on")

    win.undo()

    assert win.state_data["lecturers"] == ["Ada Lovelace"], (
        "one Ctrl+Z after a cancelled drag did not reach the action the "
        "user actually took; lecturers are %r"
        % (win.state_data["lecturers"],))


def test_a_cancelled_grid_drag_does_not_destroy_the_redo_stack(
        win, monkeypatch):
    """Phase 9 B1 through the real starter — Esc must not kill Ctrl+Y.

    The user undid something, then picked a lesson up and put it back down
    where they found it. The timetable is byte-for-byte what it was, so the
    undone action is still there to redo. Until Phase 9 it was not:
    ``_start_drag_gfx`` pushed through ``_push_undo``, whose last statement is
    ``_redo_stack.clear()``, before ``drag.exec()`` — and the cancel path's
    ``pop()`` put back the undo entry and nothing else.

    ``tests/test_phase9_b1b2.py`` carries this case too, and its Esc test is
    blocked on a mid-gesture assertion that pins the OLD mechanism (the
    snapshot being visible on the undo stack while the drag is live). This one
    asserts the same outcome through the same production path with the
    anti-vacuity check written against the state rather than the stack, so the
    fix is pinned either way. The refused-drop half of B1 and both cap cases
    of B2 pass in that file unmodified.
    """
    cls = _seed(win)

    # A real action and a real Ctrl+Z, leaving exactly one redo entry. The
    # discriminator is `lecturers`: whether redo still works is visible in
    # the state, not only in a stack depth.
    win._push_undo("rename-lecturer")
    win.state_data["lecturers"] = ["Ada L."]
    win.undo()
    assert win.state_data["lecturers"] == ["Ada Lovelace"]
    assert len(win._redo_stack) == 1, (
        "the arrangement is wrong: there is no pending redo entry to lose")

    # `undo` replaced every class dict; the seeded reference is an orphan now.
    cls = win.state_data["classes"][0]

    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)

    captured = {}

    def _cancel_during_exec(self, action):
        captured["held_during_drag"] = (
            None if win._drag_undo_entry is None else win._drag_undo_entry[0])
        captured["redo_depth_during_drag"] = len(win._redo_stack)
        return action  # the user pressed Esc: no drop

    monkeypatch.setattr(_FakeDrag, "exec", _cancel_during_exec)

    win._start_drag_gfx(cls, _StubItem())

    assert captured.get("held_during_drag") == tr(
        "actions.unplace").format(name="Fizik"), (
        "the gesture never went live, so nothing below is measured: held "
        "entry was %r" % (captured.get("held_during_drag"),))
    assert captured.get("redo_depth_during_drag") == 1, (
        "the redo stack was already destroyed while the drag was still live "
        "(depth %r), which is the defect itself"
        % (captured.get("redo_depth_during_drag"),))

    assert _placement(win.state_data["classes"][0]) == (
        True, "monday", "09:00", "R001"), (
        "a cancelled drag did not put the lesson back: %r"
        % (_placement(win.state_data["classes"][0]),))
    assert len(win._redo_stack) == 1, (
        "a cancelled drag destroyed the pending redo entry (depth %d). The "
        "gesture changed nothing on the timetable, so Ctrl+Y must survive it"
        % len(win._redo_stack))

    win.redo()
    assert win.state_data["lecturers"] == ["Ada L."], (
        "redo after a cancelled drag did not re-apply the undone action; "
        "lecturers are %r" % (win.state_data["lecturers"],))


def test_a_drag_onto_the_unplaced_sidebar_is_one_undo_entry(win, monkeypatch):
    """The third commit point, and the one no test had ever reached.

    Dropping a placed lesson onto the unplaced list is how the user unplaces
    by gesture. ``DraggableUnplacedList.dropEvent`` calls ``mark_unplaced``
    and sets ``_drag_success``; it deliberately records no undo entry of its
    own, because ``_start_drag_gfx``'s pre-gesture snapshot is already the
    right one and a fresh one taken here would deep-copy the lesson as
    ALREADY unplaced.

    That worked by accident until Phase 9: the snapshot was pushed at drag
    start, so a successful sidebar drop simply left it there under its
    "unplace" label. With the snapshot held instead, something has to record
    it, and the only code that knows the gesture committed is the tail of
    ``_start_drag_gfx``. Nothing in this suite exercised that path — measured
    2026-08-29: no test in ``tests/`` referenced ``dropEvent``,
    ``status.class_unplaced_drag`` or the list widget's drop handling at all —
    so the whole gesture could have silently stopped being undoable.
    """
    from PyQt6.QtCore import QMimeData

    cls = _seed(win)
    win._push_undo("rename-lecturer")   # an earlier action, then a Ctrl+Z, so
    win.state_data["lecturers"] = ["Ada L."]   # there is a redo entry to lose
    win.undo()
    assert len(win._redo_stack) == 1
    cls = win.state_data["classes"][0]
    depth_before = len(win._undo_stack)

    mime = QMimeData()
    mime.setText("class_drag:%s" % cls.get("id", ""))

    class _Event:
        def mimeData(self):
            return mime

        def acceptProposedAction(self):
            pass

    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)

    def _drop_on_the_sidebar(self, action):
        win.unplaced_list.dropEvent(_Event())
        return action

    monkeypatch.setattr(_FakeDrag, "exec", _drop_on_the_sidebar)

    win._start_drag_gfx(cls, _StubItem())

    live = win.state_data["classes"][0]
    assert _placement(live) == (False, None, None, None), (
        "the drop onto the sidebar did not unplace the lesson: %r"
        % (_placement(live),))
    assert len(win._undo_stack) == depth_before + 1, (
        "unplacing by drag left %d undo entries instead of one; the held "
        "snapshot was dropped on the floor or recorded twice"
        % (len(win._undo_stack) - depth_before,))
    assert win._undo_stack[-1][0] == tr("actions.unplace").format(
        name="Fizik"), (
        "the entry is labelled %r; a drop onto the sidebar is an unplace, not "
        "a move" % (win._undo_stack[-1][0],))
    assert win._redo_stack == [], (
        "unplacing by drag left %d redo entries; it is a real edit and must "
        "invalidate redo" % len(win._redo_stack))

    win.undo()
    assert _placement(win.state_data["classes"][0]) == (
        True, "monday", "09:00", "R001"), (
        "one Ctrl+Z after unplacing by drag did not put the lesson back where "
        "it was: %r" % (_placement(win.state_data["classes"][0]),))
