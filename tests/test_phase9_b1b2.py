"""Phase 9 B1 + B2 — the pre-emptive drag snapshot, on the paths that abandon it.

One root cause, two symptoms
----------------------------
``SchedulerApp._start_drag_gfx`` (``scheduler_app/ui/app.py``) pushes its undo
snapshot BEFORE the gesture is known to commit — it has to, because the
snapshot is the only record of where the lesson was before the pre-emptive
``mark_unplaced``. The push goes through ``_push_undo``, which does two things
that are correct for a real edit and wrong for a gesture that may evaporate:

* it ends in ``self._redo_stack.clear()``;
* at the ``_max_undo`` cap it evicts with ``self._undo_stack.pop(0)``.

The cancel tail of ``_start_drag_gfx`` restores ``_drag_backup`` and pops the
entry it pushed, so the *top* of the undo stack is put back. Nothing puts back
the cleared redo stack, and nothing puts back the evicted oldest entry.

**B1 (Medium).** Undo something, then start a drag and abandon it. The gesture
changed nothing on the timetable, but Ctrl+Y is now dead — the redo entry was
destroyed by a snapshot that was itself thrown away one statement later.

**B2 (Low).** With the undo stack at the 50-entry cap, the same abandoned
gesture returns the stack one entry short, and the entry it lost is the OLDEST
one, which no pop can bring back.

Both fire on a REFUSED drop as well as on Esc: ``_execute_drop`` returns
without setting ``_drag_success`` at each of its validation phases, and the
cancel tail is reached exactly as it is for Esc.

What this file asserts
----------------------
The CORRECT behaviour, so every case below except the control is red today.
The control (``test_a_committed_drag_still_clears_redo_...``) is green today
and must STAY green: the fix is a restructure of where the snapshot is taken,
and the control is what stops that restructure regressing the Phase 8 headline
fix ST-ARCH-012 — a whole drag is exactly one Ctrl+Z, labelled "move", and a
committed drag really does invalidate redo.

How the drag is driven
----------------------
Through the real ``_start_drag_gfx``, never a hand copy of it. Phase 8 measured
that hand-copied helpers which set ``_drag_undo_pushed`` themselves left the
production line that sets it executed by nothing, and deleting it kept the
suite green. ``QDrag`` is substituted (``_FakeDrag``) because ``drag.exec()``
blocks on a human; the substitution is where the drop happens in real life, so
it is where the drop — or the Esc — happens here.
"""
import pytest

from scheduler_app.core.models import mark_placed, new_class
from scheduler_app.translations import tr

pytestmark = pytest.mark.ui


# ── the world the gesture happens in ────────────────────────────────────────

def _seed(win):
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
    s["classes"].append(cls)
    mark_placed(cls, "monday", "09:00", "R001")
    return cls


def _placement(cls):
    return (cls.get("placed"), cls.get("placed_day"),
            cls.get("placed_time"), cls.get("placed_classroom"))


def _live(win):
    """The lesson as it exists NOW.

    ``_restore_state`` replaces ``state_data``'s contents wholesale, so any
    reference held across an ``undo()`` is a stale dict that no longer
    participates in the app's state. Re-fetch instead of remembering.
    """
    return win.state_data["classes"][0]


class _FakeDrag:
    """Stand-in for ``QDrag`` that hands control back mid-gesture.

    ``_start_drag_gfx`` ends in ``drag.exec(...)``, which blocks until the user
    releases the mouse. ``on_exec`` is the hook a test uses to say what the
    user did: call ``_execute_drop`` (a drop, legal or refused), or nothing at
    all (Esc / released over a non-target).
    """

    on_exec = None

    def __init__(self, parent):
        pass

    def setMimeData(self, mime):
        pass

    def setHotSpot(self, point):
        pass

    def setPixmap(self, pixmap):
        self._pixmap = pixmap

    def pixmap(self):
        """``_start_drag_unplaced`` reads back what it just set.

        A multi-lesson sidebar drag does
        ``drag.setHotSpot(QPoint(drag.pixmap().width() // 2, 12))``, so a
        stand-in that only swallowed ``setPixmap`` would raise there and the
        batch tests below would never reach the drop.
        """
        return self._pixmap

    def exec(self, action):
        hook = type(self).on_exec
        if hook is not None:
            hook()
        return action


class _StubItem:
    """The minimum ``LessonItem`` surface ``_start_drag_gfx`` touches.

    ``scene()`` returning None short-circuits the pixmap block (already
    wrapped in ``except Exception: pass``) and the missing ``set_ghost``
    skips the ghosting. Neither participates in the undo contract under test.
    """

    def scene(self):
        return None


def _run_drag(win, monkeypatch, cls, during=None):
    """Run one whole gesture through production ``_start_drag_gfx``."""
    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)
    monkeypatch.setattr(_FakeDrag, "on_exec", during)
    win._start_drag_gfx(cls, _StubItem())


@pytest.fixture
def win(make_app, monkeypatch):
    """A real SchedulerApp with the modal refusal dialogs captured, not shown."""
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


def _arm_a_pending_redo(win):
    """Do a real action and a real Ctrl+Z, leaving exactly one redo entry.

    Returns the lesson as it exists after the restore. The discriminator is
    ``lecturers``: the undone action changed it, so whether ``redo()`` works is
    visible in the state itself and not only in a stack depth.
    """
    win._push_undo("rename-lecturer")
    win.state_data["lecturers"] = ["Ada L."]
    win.undo()

    assert win.state_data["lecturers"] == ["Ada Lovelace"], (
        "the fixture is wrong: undo did not roll the lecturer edit back, so "
        "nothing below measures redo")
    assert len(win._redo_stack) == 1, (
        "the fixture is wrong: there is no pending redo entry to lose "
        "(depth %d)" % len(win._redo_stack))
    return _live(win)


# ── B1 — the redo stack ─────────────────────────────────────────────────────

def test_a_cancelled_drag_leaves_the_redo_stack_intact(win, monkeypatch):
    """B1 — an abandoned gesture must not be able to kill Ctrl+Y.

    The user undid something, then picked a lesson up and put it back down
    where they found it. The timetable is byte-for-byte what it was, so redo
    is still meaningful. Today ``_start_drag_gfx``'s pre-emptive ``_push_undo``
    has already run ``_redo_stack.clear()`` by the time the user presses Esc,
    and the cancel tail only pops the undo entry.
    """
    _seed(win)
    cls = _arm_a_pending_redo(win)
    assert _placement(cls) == (True, "monday", "09:00", "R001")

    seen = {}

    def _the_user_presses_escape():
        seen["redo_depth_mid_gesture"] = len(win._redo_stack)
        seen["undo_labels_mid_gesture"] = [e[0] for e in win._undo_stack]
        seen["placed_mid_gesture"] = cls["placed"]

    _run_drag(win, monkeypatch, cls, during=_the_user_presses_escape)

    # Anti-vacuity: the gesture really did go live, so the cancel path below
    # was actually exercised.
    #
    # This guard used to assert the mid-gesture undo stack held exactly
    # `[actions.unplace]`, i.e. that the speculative snapshot was VISIBLE ON
    # THE STACK while the drag was still in the air. That is unsatisfiable by
    # any correct fix, because it is the defect: a snapshot sitting on the
    # stack is precisely what let `_push_undo` clear redo (B1) and evict
    # `_undo_stack[0]` at the cap (B2) for a gesture that then evaporated.
    # Rewritten 2026-08-29 to keep the guard's STRENGTH and drop its coupling
    # to the implementation -- `mark_unplaced` runs one statement after the
    # snapshot is taken, so an unplaced lesson mid-gesture proves the starter
    # ran to `drag.exec()` just as well, and stays true whether the snapshot is
    # pushed or held. If the drag never went live the lesson is still placed
    # and this fails, which is the whole job.
    assert seen.get("placed_mid_gesture") is False, (
        "the gesture never reached the pre-emptive mark_unplaced, so the "
        "cancel path below was not exercised; mid-gesture placed=%r, "
        "undo labels %r"
        % (seen.get("placed_mid_gesture"),
           seen.get("undo_labels_mid_gesture")))

    # The gesture was a complete no-op on the timetable.
    assert _placement(_live(win)) == (True, "monday", "09:00", "R001"), (
        "a cancelled drag did not put the lesson back: %r"
        % (_placement(_live(win)),))
    assert win._undo_stack == [], (
        "a cancelled drag left its pre-emptive snapshot behind: %r"
        % ([e[0] for e in win._undo_stack],))

    assert len(win._redo_stack) == 1, (
        "a cancelled drag destroyed the pending redo entry: depth is %d, it "
        "was 1 (and was already %r mid-gesture, i.e. cleared by the "
        "pre-emptive _push_undo). The gesture changed nothing on the "
        "timetable, so Ctrl+Y must survive it."
        % (len(win._redo_stack), seen.get("redo_depth_mid_gesture")))

    win.redo()
    assert win.state_data["lecturers"] == ["Ada L."], (
        "redo after a cancelled drag did not re-apply the undone action; "
        "lecturers are %r, they should be ['Ada L.']"
        % (win.state_data["lecturers"],))


def test_a_refused_drop_leaves_the_redo_stack_intact(win, monkeypatch):
    """B1 — the same wound, opened by a refusal instead of by Esc.

    ``_execute_drop`` returns at its first validation phase for a day that is
    not on the grid, without setting ``_drag_success``, so ``_start_drag_gfx``
    takes exactly the same cancel tail. The user is shown a "cannot move
    there" box, nothing changes, and their redo history is gone anyway.
    """
    _seed(win)
    cls = _arm_a_pending_redo(win)

    def _the_user_drops_off_the_grid():
        win._execute_drop("saturday", "09:00")

    _run_drag(win, monkeypatch, cls, during=_the_user_drops_off_the_grid)

    # Anti-vacuity: this has to be a REFUSED drop, not a silent no-op.
    assert win.refusals, (
        "the off-grid drop was not refused, so this test never exercised the "
        "refusal path")
    assert win._drag_success is False, (
        "the drop committed; the refusal path was not taken")

    assert _placement(_live(win)) == (True, "monday", "09:00", "R001"), (
        "a refused drop did not put the lesson back: %r"
        % (_placement(_live(win)),))

    assert len(win._redo_stack) == 1, (
        "a REFUSED drop destroyed the pending redo entry: depth is %d, it was "
        "1. The drop was rejected and nothing moved, so Ctrl+Y must survive "
        "it." % len(win._redo_stack))

    win.redo()
    assert win.state_data["lecturers"] == ["Ada L."], (
        "redo after a refused drop did not re-apply the undone action; "
        "lecturers are %r" % (win.state_data["lecturers"],))


# ── B2 — the cap eviction ───────────────────────────────────────────────────

def _fill_undo_to_the_cap(win):
    """Push ``_max_undo`` identifiable entries. Returns their labels."""
    for i in range(win._max_undo):
        win._push_undo("action-%02d" % i)
    assert len(win._undo_stack) == win._max_undo, (
        "the fixture is wrong: the stack is %d deep, the cap is %d"
        % (len(win._undo_stack), win._max_undo))
    return [entry[0] for entry in win._undo_stack]


def test_a_cancelled_drag_at_the_undo_cap_keeps_the_whole_history(
        win, monkeypatch):
    """B2 — the eviction fires at drag START, and the pop cannot undo it.

    At the cap, ``_push_undo`` appends and then drops ``_undo_stack[0]``. The
    cancel tail pops the entry it pushed — off the TOP — so the stack comes
    back one short and the entry that is gone is the oldest one, which no
    amount of popping can restore. Identity matters as much as depth here: a
    fix that only kept the depth right by pushing a filler would still have
    lost the user's earliest undoable action.
    """
    cls = _seed(win)
    labels_before = _fill_undo_to_the_cap(win)

    _run_drag(win, monkeypatch, cls, during=None)  # the user presses Esc

    assert _placement(_live(win)) == (True, "monday", "09:00", "R001"), (
        "a cancelled drag did not put the lesson back: %r"
        % (_placement(_live(win)),))

    labels_after = [entry[0] for entry in win._undo_stack]
    assert len(labels_after) == win._max_undo, (
        "a cancelled drag changed the undo depth at the cap: %d entries, "
        "there were %d. The gesture was a no-op on the timetable."
        % (len(labels_after), win._max_undo))
    assert labels_after[0] == labels_before[0], (
        "a cancelled drag evicted the OLDEST undo entry at the cap: the "
        "stack now starts at %r, it started at %r — that action is "
        "unrecoverable" % (labels_after[0], labels_before[0]))
    assert labels_after == labels_before, (
        "a cancelled drag rewrote the undo history at the cap:\n  after : "
        "%r\n  before: %r" % (labels_after, labels_before))


def test_a_refused_drop_at_the_undo_cap_keeps_the_whole_history(
        win, monkeypatch):
    """B2 — the eviction on the refusal path, not only on Esc."""
    cls = _seed(win)
    labels_before = _fill_undo_to_the_cap(win)

    def _the_user_drops_off_the_grid():
        win._execute_drop("saturday", "09:00")

    _run_drag(win, monkeypatch, cls, during=_the_user_drops_off_the_grid)

    assert win.refusals, (
        "the off-grid drop was not refused, so this test never exercised the "
        "refusal path")

    labels_after = [entry[0] for entry in win._undo_stack]
    assert len(labels_after) == win._max_undo, (
        "a REFUSED drop changed the undo depth at the cap: %d entries, there "
        "were %d" % (len(labels_after), win._max_undo))
    assert labels_after == labels_before, (
        "a REFUSED drop rewrote the undo history at the cap:\n  after : "
        "%r\n  before: %r" % (labels_after, labels_before))


# ── the control — must be green before AND after the fix ────────────────────

def test_a_committed_drag_still_clears_redo_and_is_one_relabelled_entry(
        win, monkeypatch):
    """ST-ARCH-012 guard. GREEN TODAY. It must still be green after the fix.

    B1 and B2 are fixed by moving or deferring the pre-emptive snapshot, and
    the cheap ways to do that break this: a drag that COMMITS is a real edit,
    so it must invalidate redo, and it must land as exactly ONE undo entry
    holding the placement the lesson had BEFORE the gesture — not the unplaced
    state ``mark_unplaced`` leaves behind, which is the bug Phase 8 fixed by
    re-labelling the pre-emptive snapshot instead of pushing a fresh one.

    If this goes red while the four above go green, the fix traded one defect
    for a worse one.
    """
    _seed(win)
    cls = _arm_a_pending_redo(win)
    undo_depth_before = len(win._undo_stack)

    def _the_user_drops_on_a_legal_cell():
        win._execute_drop("tuesday", "10:00")

    _run_drag(win, monkeypatch, cls, during=_the_user_drops_on_a_legal_cell)

    assert not win.refusals, "a legal drop reported a refusal: %r" % (
        win.refusals,)
    assert _placement(_live(win)) == (True, "tuesday", "10:00", "R001"), (
        "the drag did not commit through the real starter: %r"
        % (_placement(_live(win)),))

    assert win._redo_stack == [], (
        "a drag that COMMITTED left %d redo entries alive; a real edit must "
        "invalidate redo, or Ctrl+Y replays a future that no longer exists"
        % len(win._redo_stack))

    assert len(win._undo_stack) == undo_depth_before + 1, (
        "a whole drag must be exactly one Ctrl+Z: depth went %d -> %d"
        % (undo_depth_before, len(win._undo_stack)))
    assert win._undo_stack[-1][0] == tr("actions.move").format(name="Fizik"), (
        "the committed drag's undo entry is labelled %r, not the move label; "
        "the pre-emptive 'unplace' snapshot was not re-labelled"
        % (win._undo_stack[-1][0],))
    assert win._drag_undo_pushed is False, (
        "the flag outlived the gesture; the next sidebar drag would pop an "
        "entry it never pushed")

    win.undo()
    assert _placement(_live(win)) == (True, "monday", "09:00", "R001"), (
        "one undo after a committed drag did not put the lesson back where "
        "the drag found it: %r" % (_placement(_live(win)),))


# ── B1 + B2 in the sibling gesture: _place_classes_batch ────────────────────
#
# `_place_classes_batch` had the same shape `_start_drag_gfx` had -- a
# `_push_undo` before the outcome was known -- and the Phase 9 commit message
# looked at it and analysed the wrong branch. It reasoned about the
# `0 placed / 0 unresolved` early return and called that unreachable from the
# UI. The reachable no-op is `0 placed / N unresolved`: it sails PAST that
# early return, raises an error toast, and returns with the speculative entry
# still on the stack. Two shipped gestures reach it -- Ctrl+P /
# "place all unplaced" (`place_class`), and a multi-selection dragged out of
# the sidebar and dropped off-cell (`_execute_drop_anywhere`).


def _seed_an_unplaceable_batch(win):
    """A world where every candidate in the batch is impossible to place.

    One PINNED lesson owns the grid's only cell for the only lecturer, and the
    two candidates require a classroom the setup does not contain, so
    ``get_physical_room_candidates`` yields nothing for either of them at any
    cell. ``place_batch`` therefore comes back `0 placed / 2 unresolved` --
    the branch that is reachable from the UI and that the early return does
    not catch.
    """
    s = win.state_data
    s["days"] = ["monday"]
    s["slots"] = ["09:00"]
    s["classrooms"] = ["R001", "R002"]
    s["classroom_capacities"] = {"R001": 40, "R002": 40}
    s["lecturers"] = ["Ada Lovelace"]
    s["classes"] = []

    blocker = new_class()
    blocker.update(name="Blok", lecturer="Ada Lovelace", duration=1,
                   student_count=10)
    blocker["pinned"] = True
    blocker["pinned_day"] = "monday"
    blocker["pinned_time"] = "09:00"
    blocker["pinned_classroom"] = "R001"
    s["classes"].append(blocker)
    mark_placed(blocker, "monday", "09:00", "R001")

    for i in range(2):
        cls = new_class()
        cls.update(name="Buyuk%d" % i, lecturer="Ada Lovelace", duration=1,
                   student_count=10)
        cls["required_classrooms"] = ["GHOST_ROOM"]
        s["classes"].append(cls)


def _seed_a_placeable_batch(win):
    """The same world with room to breathe: both candidates CAN be placed."""
    s = win.state_data
    s["days"] = ["monday", "tuesday"]
    s["slots"] = ["09:00", "10:00"]
    s["classrooms"] = ["R001", "R002"]
    s["classroom_capacities"] = {"R001": 40, "R002": 40}
    s["lecturers"] = ["Ada Lovelace"]
    s["classes"] = []

    for i in range(2):
        cls = new_class()
        cls.update(name="Serbest%d" % i, lecturer="Ada Lovelace", duration=1,
                   student_count=10)
        s["classes"].append(cls)


def _placements(win):
    """Every lesson's name and placement, order-independent."""
    return sorted(
        (c["name"], c.get("placed"), c.get("placed_day"),
         c.get("placed_time"), c.get("placed_classroom"))
        for c in win.state_data["classes"])


def test_a_batch_that_places_nothing_leaves_the_redo_stack_intact(win):
    """B1, in the Ctrl+P gesture.

    The teacher presses "place all unplaced" with lessons that cannot fit.
    They are told nothing was placed -- and Ctrl+Y is dead, because the
    snapshot taken on the way in already ran ``_redo_stack.clear()``.
    """
    _seed_an_unplaceable_batch(win)
    _arm_a_pending_redo(win)
    before = _placements(win)

    win.place_class()

    # Anti-vacuity: this has to be the no-op the finding is about. If the
    # batch actually placed something, everything below is measuring a
    # different gesture.
    assert _placements(win) == before, (
        "the seed is wrong: the batch changed the timetable, so this is not "
        "the 'placed nothing' case:\n  after : %r\n  before: %r"
        % (_placements(win), before))

    assert win._undo_stack == [], (
        "a batch placement that placed NOTHING left an undo entry behind: %r"
        % ([e[0] for e in win._undo_stack],))
    assert len(win._redo_stack) == 1, (
        "a batch placement that placed NOTHING destroyed the pending redo "
        "entry: depth is %d, it was 1. The gesture changed nothing on the "
        "timetable, so Ctrl+Y must survive it." % len(win._redo_stack))

    win.redo()
    assert win.state_data["lecturers"] == ["Ada L."], (
        "redo after a no-op batch did not re-apply the undone action; "
        "lecturers are %r" % (win.state_data["lecturers"],))


def test_a_batch_that_places_nothing_keeps_the_whole_history_at_the_cap(win):
    """B2, in the Ctrl+P gesture.

    At the 50-entry cap the speculative append evicts ``_undo_stack[0]``, and
    nothing here even attempts the ``pop()`` the drag path used to attempt --
    so the user's oldest undoable action is discarded for a gesture that moved
    nothing.
    """
    _seed_an_unplaceable_batch(win)
    labels_before = _fill_undo_to_the_cap(win)
    before = _placements(win)

    win.place_class()

    assert _placements(win) == before, (
        "the seed is wrong: the batch changed the timetable")

    labels_after = [entry[0] for entry in win._undo_stack]
    assert len(labels_after) == win._max_undo, (
        "a no-op batch changed the undo depth at the cap: %d entries, there "
        "were %d" % (len(labels_after), win._max_undo))
    assert labels_after[0] == labels_before[0], (
        "a no-op batch evicted the OLDEST undo entry at the cap: the stack "
        "now starts at %r, it started at %r -- that action is unrecoverable"
        % (labels_after[0], labels_before[0]))
    assert labels_after == labels_before, (
        "a no-op batch rewrote the undo history at the cap:\n  after : %r\n"
        "  before: %r" % (labels_after, labels_before))


def test_a_multi_drag_that_places_nothing_leaves_the_redo_stack_intact(
        win, monkeypatch):
    """B1, reached by a DRAG rather than by the menu.

    Multi-select in the unplaced sidebar, drag onto the timetable, release
    anywhere that is not a cell: ``_start_drag_unplaced`` ->
    ``_execute_drop_anywhere`` -> ``_place_classes_batch``. Driven through the
    real starter, so a fix that only guarded the menu path fails here.
    """
    _seed_an_unplaceable_batch(win)
    _arm_a_pending_redo(win)
    stuck = [c for c in win.state_data["classes"]
             if c["name"].startswith("Buyuk")]
    assert len(stuck) == 2, "the seed lost its candidates: %r" % (stuck,)
    before = _placements(win)

    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)
    monkeypatch.setattr(_FakeDrag, "on_exec",
                        lambda: win._execute_drop_anywhere())
    # The widget argument reaches nothing but `QDrag(widget)` for a
    # multi-lesson drag -- the single-lesson branch is the only one that calls
    # `widget.currentItem()` -- so the stand-in QDrag swallows it.
    win._start_drag_unplaced(stuck, None)

    assert _placements(win) == before, (
        "the seed is wrong: the multi-drag changed the timetable")
    assert win._undo_stack == [], (
        "a multi-drag that placed NOTHING left an undo entry behind: %r"
        % ([e[0] for e in win._undo_stack],))
    assert len(win._redo_stack) == 1, (
        "a multi-drag onto the grid that placed NOTHING destroyed the pending "
        "redo entry: depth is %d, it was 1" % len(win._redo_stack))


def test_a_batch_that_places_something_is_one_undo_that_puts_it_back(win):
    """The control for the three above. GREEN TODAY, must stay green.

    A batch that really does place lessons is a real edit: exactly one undo
    entry, labelled "bulk schedule", redo invalidated, and one Ctrl+Z takes
    the timetable back to before the batch ran. The cheap way to fix the
    no-op case -- never record anything -- breaks this.
    """
    _seed_a_placeable_batch(win)
    before = _placements(win)
    win._push_undo("earlier")
    win.undo()
    assert len(win._redo_stack) == 1, "the fixture armed no redo entry"
    undo_depth_before = len(win._undo_stack)

    win.place_class()

    placed_now = [c for c in win.state_data["classes"] if c["placed"]]
    assert len(placed_now) == 2, (
        "the seed is wrong: the batch placed %d of 2 lessons, so this is not "
        "the 'placed something' case" % len(placed_now))

    assert win._redo_stack == [], (
        "a batch that PLACED lessons left %d redo entries alive; a real edit "
        "must invalidate redo" % len(win._redo_stack))
    assert len(win._undo_stack) == undo_depth_before + 1, (
        "a whole batch must be exactly one Ctrl+Z: depth went %d -> %d"
        % (undo_depth_before, len(win._undo_stack)))
    assert win._undo_stack[-1][0] == tr("actions.bulk_schedule"), (
        "the batch's undo entry is labelled %r, not the bulk-schedule label"
        % (win._undo_stack[-1][0],))

    win.undo()
    assert _placements(win) == before, (
        "one undo after a batch placement did not put the timetable back:\n"
        "  after : %r\n  before: %r" % (_placements(win), before))
