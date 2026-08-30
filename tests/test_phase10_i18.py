"""Phase 10, item 18 — a multi-lesson drop reports success even when it placed nothing.

The shape
---------
``SchedulerApp._execute_drop_anywhere`` (``scheduler_app/ui/app.py`` ~5027)::

    def _execute_drop_anywhere(self):
        drag_group = list(getattr(self, "_dragging_classes", []) or [])
        if len(drag_group) > 1 and all(not c.get("placed") for c in drag_group):
            self._place_classes_batch(drag_group)
            self._drag_success = True

``self._drag_success = True`` is unconditional. ``_place_classes_batch`` may
have placed every candidate, some of them, or — the case here — none, and
moved nothing that was already on the grid. The flag says "success" either way.

Why there is nothing to reproduce, and what this file pins instead
------------------------------------------------------------------
``_drag_success`` has exactly two readers (measured at 3d87515: ``ui/app.py``
lines 4885 and 4973). Neither turns today's wrong value into anything the user
sees:

* ``_start_drag_gfx``'s tail (4885) belongs to a GRID drag. Its multi-selection
  branch sets ``_dragging_classes`` to lessons that are all ``placed``, and it
  then unplaces only the one under the cursor — so ``all(not c["placed"])`` in
  ``_execute_drop_anywhere`` is False for it and the branch above never runs.
* ``_start_drag_unplaced``'s tail (4973) is the sidebar drag that DOES reach
  it, and its success toast is guarded by ``len(drag_classes) == 1`` while
  ``_execute_drop_anywhere`` needs ``len(drag_group) > 1``. The two conditions
  are disjoint — by accident, in two different methods, with nothing naming
  the coincidence.

The only remaining call site (``ui/renderer.py``, the drop handler that calls
``_execute_drop_anywhere``) repeats the same ``len(...) > 1 and
all(not placed)`` guard, so no third route exists.

So a probe that watched for a false toast would pass today and pin the
accident rather than the rule. What is asserted here is the INVARIANT:

    ``_drag_success`` is true after a gesture if and only if the gesture
    changed the timetable.

``test_a_batch_drop_that_placed_nothing_is_not_a_success`` is RED on this tree.
The other two are green and are what stops the fix being written wrongly:

* ``..._that_placed_something...`` kills the fix "just delete the line".
* ``..._that_only_relocated_an_existing_lesson...`` kills the fix
  ``if placed_count:``. ``placed_count`` counts only the dragged candidates,
  while Phase 2 of ``optimized_batch_schedule`` (``core/facade.py`` ~200)
  re-solves every already-placed unpinned lesson from scratch — so a batch can
  place none of its candidates and still have MOVED lessons the user was
  looking at. ``result.rescheduled`` is no better: ``core/facade.py`` returns
  it hard-coded ``True`` at the end of Phase 2 (line 244) regardless of what
  Phase 2 achieved. Both claims re-verified at this commit; see the module
  docstring of the sibling ``_place_classes_batch`` gate.

How the gesture is driven
-------------------------
Through the real ``_start_drag_unplaced``, never a hand copy. ``QDrag`` is
substituted because ``drag.exec()`` blocks on a human; the substitution is
where the drop happens in real life, so it is where the drop happens here, and
it calls the same ``_execute_drop_anywhere`` that ``ui/renderer.py``'s
``dropEvent`` calls.
"""
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


def _grid(win, days, slots):
    s = win.state_data
    s["days"] = list(days)
    s["slots"] = list(slots)
    s["classrooms"] = ["R001"]
    s["classroom_capacities"] = {"R001": 40}
    s["lecturers"] = ["Ada Lovelace"]
    s["years"] = {"Year-1": ["A"]}
    s["classes"] = []
    return s


class _FakeDrag:
    """Stand-in for ``QDrag``: hands control back mid-gesture.

    ``_start_drag_unplaced`` ends in ``drag.exec(...)``, which blocks until the
    user releases the mouse. ``on_exec`` is the hook that says what they did.
    """

    on_exec = None

    def __init__(self, parent):
        self._pixmap = None

    def setMimeData(self, mime):
        pass

    def setHotSpot(self, point):
        pass

    def setPixmap(self, pixmap):
        self._pixmap = pixmap

    def pixmap(self):
        # A multi-lesson sidebar drag reads back what it just set, to compute
        # the hot spot.
        return self._pixmap

    def exec(self, action):
        hook = type(self).on_exec
        if hook is not None:
            hook()
        return action


class _StubList:
    """The minimum surface ``_start_drag_unplaced`` touches on its widget."""

    def currentItem(self):
        return None


@pytest.fixture
def win(make_app, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from scheduler_app.ui.tier_enforcement import TierEnforcement
    from scheduler_app.plans import TIER_INSTITUTIONAL

    w = make_app()
    monkeypatch.setattr(TierEnforcement.instance(), "_tier_slug",
                        TIER_INSTITUTIONAL)
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    return w


def _drag_them_onto_the_timetable(win, monkeypatch, classes):
    """One whole sidebar multi-drag, dropped off-cell, through production code.

    Returns ``(placements_before_the_drop, batch_returns)``. The second element
    is every ``(placed_count, unresolved_count, rescheduled)`` triple
    ``_place_classes_batch`` handed back — the only three numbers a
    count-based fix could gate on, recorded so the tests can say what such a
    fix would have decided. The real method still runs; the spy only watches
    it.
    """
    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)
    seen = {}
    batch_returns = []
    real_batch = win._place_classes_batch

    def _spy(candidates):
        out = real_batch(candidates)
        batch_returns.append(out)
        return out

    monkeypatch.setattr(win, "_place_classes_batch", _spy)

    def _the_user_drops_off_a_cell():
        seen["before"] = snapshot_placements(win.state_data)
        win._execute_drop_anywhere()

    monkeypatch.setattr(_FakeDrag, "on_exec", _the_user_drops_off_a_cell)
    win._start_drag_unplaced(classes, _StubList())
    assert "before" in seen, (
        "the gesture never reached drag.exec(), so nothing below was driven")
    assert len(batch_returns) == 1, (
        "_place_classes_batch ran %d times, expected exactly 1"
        % len(batch_returns))
    return seen["before"], batch_returns[0]


# ── the defect ──────────────────────────────────────────────────────────────

def test_a_batch_drop_that_placed_nothing_is_not_a_success(win, monkeypatch):
    """RED today. Two lessons dragged out, nothing placed, nothing moved.

    Both candidates require a room the school does not have, so
    ``CandidateGenerator`` yields nothing for either of them in Phase 1 and
    the Phase 2 re-solve has nothing else to move. The batch is a complete
    no-op on the timetable — ``_place_classes_batch``'s own placement
    comparison agrees and records no undo entry — and ``_execute_drop_anywhere``
    reports the gesture as a success regardless.
    """
    s = _grid(win, ["monday"], ["09:00"])
    a = _new_cls("Kimya", required_classrooms=["Lab X"])
    b = _new_cls("Biyoloji", required_classrooms=["Lab X"])
    s["classes"].extend([a, b])

    before, batch = _drag_them_onto_the_timetable(win, monkeypatch, [a, b])
    after = snapshot_placements(win.state_data)

    # `rescheduled` is True for a batch that achieved literally nothing —
    # `core/facade.py` returns it hard-coded at the end of Phase 2. Recorded
    # here so nobody reaches for it as the gate.
    assert batch == (0, 2, True), (
        "the batch did not report 0 placed / 2 unresolved / rescheduled: %r"
        % (batch,))

    # Anti-vacuity: the gesture really was a no-op, and it really ran the
    # batch (the Phase 9 gate in _place_classes_batch is the second witness —
    # it records an undo entry when and only when the placements moved).
    assert after == before == {}, (
        "the batch placed something after all, so this test is not measuring "
        "the no-op case: placements %r -> %r" % (before, after))
    assert win._undo_stack == [], (
        "_place_classes_batch recorded an undo entry, so it saw the "
        "placements change: %r" % ([e[0] for e in win._undo_stack],))
    assert [c["placed"] for c in win.state_data["classes"]] == [False, False], (
        "a candidate ended up placed: %r"
        % ([(c["name"], c["placed"]) for c in win.state_data["classes"]],))

    assert win._drag_success is False, (
        "a multi-lesson drop that placed nothing and moved nothing reported "
        "success: _drag_success is %r. _execute_drop_anywhere sets it "
        "unconditionally on the line after _place_classes_batch, whatever the "
        "batch did. Placements %r -> %r." % (win._drag_success, before, after))


# ── the two controls that constrain the fix ─────────────────────────────────

def test_a_batch_drop_that_placed_something_is_a_success(win, monkeypatch):
    """Green today, must stay green. Kills the fix "delete the line"."""
    s = _grid(win, ["monday"], ["09:00", "10:00"])
    a = _new_cls("Kimya")
    b = _new_cls("Biyoloji")
    s["classes"].extend([a, b])

    before, _batch = _drag_them_onto_the_timetable(win, monkeypatch, [a, b])
    after = snapshot_placements(win.state_data)

    assert before == {} and len(after) == 2, (
        "the batch did not place both lessons, so this control is not "
        "measuring a successful gesture: %r -> %r" % (before, after))

    assert win._drag_success is True, (
        "a multi-lesson drop that placed both lessons reported failure: "
        "_drag_success is %r, placements %r -> %r"
        % (win._drag_success, before, after))


def test_a_batch_drop_that_only_relocated_an_existing_lesson_is_a_success(
        win, monkeypatch):
    """Green today, must stay green. Kills the fix ``if placed_count:``.

    Neither dragged lesson can be placed — both need a room that does not
    exist — so ``placed_count`` is 0. Phase 2 of ``optimized_batch_schedule``
    nevertheless re-solves the lesson that was already on the grid and moves
    it. The user's timetable changed; the gesture was not a no-op; and no
    count in ``PlaceBatchResult`` says so.
    """
    s = _grid(win, ["monday", "tuesday"], ["09:00", "10:00"])
    existing = _new_cls("Fizik")
    s["classes"].append(existing)
    mark_placed(existing, "tuesday", "10:00", "R001")

    a = _new_cls("Kimya", required_classrooms=["Lab X"])
    b = _new_cls("Biyoloji", required_classrooms=["Lab X"])
    s["classes"].extend([a, b])

    before, batch = _drag_them_onto_the_timetable(win, monkeypatch, [a, b])
    after = snapshot_placements(win.state_data)

    # Anti-vacuity: no candidate was placed, and yet the timetable moved.
    assert [c["placed"] for c in (a, b)] == [False, False], (
        "a candidate was placed, so `placed_count` is not 0 and this control "
        "no longer distinguishes the two fixes")
    assert batch[0] == 0, (
        "`placed_count` is %d, not 0 — this control no longer shows that a "
        "count-based gate is wrong" % batch[0])
    assert after != before, (
        "the existing lesson was not relocated, so this control is vacuous. "
        "placements %r -> %r. Re-seed it so Phase 2 has a strictly better "
        "slot to move the existing lesson into." % (before, after))
    assert [e[0] for e in win._undo_stack] != [], (
        "_place_classes_batch did not record an undo entry although the "
        "placements changed; its Phase 9 gate is the same comparison this "
        "test says _drag_success needs")

    assert win._drag_success is True, (
        "a multi-lesson drop that relocated an existing lesson reported "
        "failure: _drag_success is %r. _place_classes_batch returned "
        "(placed_count, unresolved_count, rescheduled) = %r, so a "
        "`placed_count > 0` gate decides False here — and the placements went "
        "%r -> %r, which is the user's timetable changing under them."
        % (win._drag_success, batch, before, after))
