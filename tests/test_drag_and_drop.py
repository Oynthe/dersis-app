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
``_execute_drop`` is the *end* of a gesture that Qt starts. The three fields it
reads — ``_dragging_cls``, ``_dragging_classes``, ``_drag_backup`` and
``_drag_undo_pushed`` — are set by ``_start_drag_gfx`` (dragging a placed
lesson out of the grid) and ``_start_drag_unplaced`` (dragging from the
sidebar). Driving those needs a live ``QDrag`` and a rendered
``LessonItem``, so the two helpers below reproduce exactly the field
assignments each of them makes, including ``_start_drag_gfx``'s pre-emptive
undo snapshot and its ``mark_unplaced`` — the lesson is already off the grid by
the time the drop is validated, which is what frees its own room and its own
cell.

The helpers mirror production or they measure nothing: ``_drag_undo_pushed`` is
what tells ``_execute_drop`` whether the entry on top of the undo stack is this
drag's, so a helper that forgot to set it would send every grid drag down the
branch meant for sidebar drags.

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


def _add_class(win, name):
    """A second, unplaced lesson in the world `_seed` built."""
    cls = new_class()
    cls.update(name=name, lecturer="Ada Lovelace", duration=1,
               student_count=10)
    win.state_data["classes"].append(cls)
    return cls


def _arm_drag_from_grid(win, cls, also=()):
    """Reproduce `_start_drag_gfx` in `ui/app.py` for a placed lesson.

    Order matters and is production's: snapshot the placement, push the
    pre-emptive "unplace" undo entry, raise `_drag_undo_pushed`, *then*
    unplace. `_execute_drop` re-labels that entry rather than popping and
    re-pushing it, which is what makes a whole drag a single Ctrl+Z that
    actually restores the placement (ST-ARCH-012).

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
    win._push_undo(tr("actions.unplace").format(name=cls["name"]))
    win._drag_undo_pushed = True
    mark_unplaced(cls)
    win._drag_success = False


def _arm_drag_from_sidebar(win, cls):
    """Reproduce `_start_drag_unplaced` in `ui/app.py`.

    The backup is all-None here, which is the case that leaves
    `_get_preferred_rooms` with nothing but the classroom filter to go on.

    `_drag_undo_pushed` is False and that is the whole point: this starter
    pushes NO undo entry, so whatever is on top of the stack belongs to some
    earlier action and `_execute_drop` must not touch it.
    """
    mark_unplaced(cls)
    win._dragging_cls = cls
    win._dragging_classes = [cls]
    win._drag_backup = {"placed": False, "placed_day": None,
                        "placed_time": None, "placed_classroom": None}
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

    The drag start pushes an "unplace" snapshot pre-emptively so a *failed*
    drag can be rolled back; a *successful* one re-labels that same entry
    "move" in place. Push a second entry instead and every drag costs the user
    two undos, the first of which appears to do nothing.

    Mutation: replace the relabel branch in `_execute_drop` with a bare
    `self._push_undo(move_label)` and the depth assertion below goes red at 2.
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
    """ST-ARCH-012 — a refusal must not eat the pre-emptive snapshot either.

    `_execute_drop` returns early on every refusal path without popping, and
    leaves `_drag_undo_pushed` raised, so the entry is still the drag's to
    consume. What actually puts the lesson back on the grid is
    `_start_drag_gfx`'s own restore loop (`for k, v in
    self._drag_backup.items(): cls[k] = v`); the undo entry is a second,
    belt-and-braces copy of the same placement, and it is the one the user
    reaches with Ctrl+Z. Popping it here instead would leave the user with no
    undo record of a lesson the app had already unplaced.

    (This docstring used to credit `dragLeaveEvent`/`_cancel_drag` with the
    restore. There is no `_cancel_drag` anywhere in `scheduler_app`, and
    `dragLeaveEvent` — `ui/renderer.py`:2052 — only clears the drop highlight.
    The assertions were right; the explanation was not.)
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
        win._execute_drop("tuesday", "10:00")
        return action

    monkeypatch.setattr(_FakeDrag, "exec", _drop_during_exec)

    win._start_drag_gfx(cls, _StubItem())

    assert captured.get("flag_during_drag") is True, (
        "production _start_drag_gfx did not set _drag_undo_pushed before the "
        "drag went live, so _execute_drop cannot tell whose snapshot is on "
        "top of the stack")
    assert captured.get("depth_during_drag") == 1, (
        "the pre-emptive snapshot was not pushed; depth was %r"
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
