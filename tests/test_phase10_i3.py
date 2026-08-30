"""Phase 10, item 3 / ST-DATA-010 — a multi-select drag out of the timetable.

The claim under test
--------------------
"A multi-lesson drag from the timetable moves only the lesson under the
cursor." ``_start_drag_gfx`` puts the whole selection into
``_dragging_classes`` but builds ``_drag_backup`` from — and calls
``mark_unplaced`` on — the primary alone, so ``_execute_drop``'s
``len(drag_group) > 1 and all(not c.get("placed") ...)`` guard is False and the
single-lesson path runs.

Why this module exists when ``tests/test_drag_and_drop.py`` already has
``test_a_multi_select_drag_is_one_undo_for_what_actually_moved``: that test
arms the drag with ``_arm_drag_from_grid``, a *hand copy* of
``_start_drag_gfx`` that assigns ``_dragging_classes = [cls] + list(also)``
itself. It therefore cannot see production stop carrying the selection, and it
cannot see how the selection got there. Everything below drives the real
selection entry point (``_select_class_gfx`` on real ``LessonItem``s pulled out
of the rendered scene) and the real starter (``_start_drag_gfx`` with the
module-level ``QDrag`` replaced so ``exec`` performs the drop instead of
blocking). Nothing here hand-assigns ``_dragging_classes``, ``_drag_backup``,
``_drag_undo_entry`` or ``_drag_undo_pushed``.

What it found
-------------
The described behaviour is real and reproduces exactly, and it is not a
defect. ``_execute_drop``'s multi-lesson branch is ``_execute_drop_anywhere``
-> ``_place_classes_batch``, which *solves* for positions and never receives
``(day, slot)`` at all — the drop target is not one of its arguments.
``test_unplacing_the_whole_drag_group_makes_the_drop_ignore_the_cell`` below
builds the handoff's sketched fix through production (``mark_unplaced``
monkeypatched at ``_start_drag_gfx``'s own call site, so the real starter
unplaces the whole group it assembled) and measures where the lessons land.
Measured at 3d87515, a drag of Fizik from monday 09:00 dropped on tuesday
11:00 left Fizik on **monday 10:00** and moved Kimya — which the user never
touched — out of R001 into R002. Neither lesson on the target cell, both
moved, one of them silently.

All four tests are GREEN today. That is the result: item 3 does not
reproduce as a *defect*, and the sketched fix is measurably worse than the
behaviour it replaces.

Findings touched: ST-DATA-010.
"""
import pytest

from PyQt6.QtCore import Qt

from scheduler_app.core.models import mark_placed, new_class

pytestmark = pytest.mark.ui


# ── the world ───────────────────────────────────────────────────────────────

def _seed(win):
    """Two placed lessons that are visible together in the default tab.

    Two *different* lecturers on purpose, so nothing below is decided by a
    lecturer clash. Both in R001 because they have to be: tab 0 renders
    ``_filter_classroom``, one classroom at a time, so two lessons only appear
    in the same view — and can only be Ctrl-clicked into one selection — when
    they share a room. That constraint is measured in the first test.
    """
    s = win.state_data
    s["days"] = ["monday", "tuesday"]
    s["slots"] = ["09:00", "10:00", "11:00"]
    s["classrooms"] = ["R001", "R002"]
    s["classroom_capacities"] = {"R001": 40, "R002": 40}
    s["lecturers"] = ["Ada Lovelace", "Grace Hopper"]
    s["classes"] = []

    fizik = new_class()
    fizik.update(name="Fizik", lecturer="Ada Lovelace", duration=1,
                 student_count=10)
    s["classes"].append(fizik)
    mark_placed(fizik, "monday", "09:00", "R001")

    kimya = new_class()
    kimya.update(name="Kimya", lecturer="Grace Hopper", duration=1,
                 student_count=10)
    s["classes"].append(kimya)
    mark_placed(kimya, "monday", "10:00", "R001")

    win.refresh_grid()
    return fizik, kimya


def _placement(cls):
    return (cls.get("placed"), cls.get("placed_day"),
            cls.get("placed_time"), cls.get("placed_classroom"))


def _by_name(win, name):
    """The lesson as it exists NOW — ``undo`` replaces ``state_data``."""
    return next(c for c in win.state_data["classes"] if c["name"] == name)


def _lesson_items(win):
    scene = win.grid_view1.scene()
    if scene is None:
        return {}
    return {it.cls["name"]: it
            for it in (getattr(scene, "lesson_items", []) or [])
            if it is not None and getattr(it, "cls", None) is not None}


def _ctrl_click_both(win, first_name, second_name):
    """Build the multi-selection through the REAL selection entry point.

    ``LessonItem.mousePressEvent`` -> ``_select_class_gfx(cls, item,
    modifiers)``. Ctrl-clicking the second lesson is what a user does, and it
    is the only thing that populates ``_selected_classes`` — the field
    ``_start_drag_gfx`` reads to decide whether this is a multi-drag.
    """
    items = _lesson_items(win)
    assert set(items) >= {first_name, second_name}, (
        "the scene rendered no lesson items for %r/%r — it has %r; nothing "
        "below would be selecting anything"
        % (first_name, second_name, sorted(items)))
    win._select_class_gfx(items[first_name].cls, items[first_name],
                          Qt.KeyboardModifier.NoModifier)
    win._select_class_gfx(items[second_name].cls, items[second_name],
                          Qt.KeyboardModifier.ControlModifier)
    names = [c["name"] for c in win._selected_classes]
    assert names == [first_name, second_name], (
        "the real selection path did not produce a two-lesson selection: %r"
        % (names,))
    return items


class _FakeDrag:
    """``QDrag`` that runs the drop instead of blocking on the mouse.

    ``_start_drag_gfx`` ends in ``drag.exec(...)``; in real life that is where
    the drop happens, so it is where the drop happens here. Same stand-in
    shape ``tests/test_drag_and_drop.py`` and ``tests/test_phase9_b1b2.py``
    use.
    """

    on_exec = None

    def __init__(self, parent):
        pass

    def setMimeData(self, mime):
        pass

    def setPixmap(self, pm):
        pass

    def setHotSpot(self, pt):
        pass

    def pixmap(self):
        from PyQt6.QtGui import QPixmap
        return QPixmap(1, 1)

    def exec(self, action):
        if type(self).on_exec is not None:
            type(self).on_exec()
        return action


@pytest.fixture
def win(make_app, monkeypatch):
    """A real SchedulerApp with refusal dialogs captured and toasts recorded."""
    from PyQt6.QtWidgets import QMessageBox
    from scheduler_app.ui.app import SchedulerApp

    w = make_app()
    refusals = []
    toasts = []

    def _capture(*args, **kwargs):
        refusals.append(args)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_capture))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(_capture))

    real_toast = SchedulerApp._show_toast

    def _record_toast(self, message, kind="info"):
        toasts.append((message, kind))
        return real_toast(self, message, kind)

    monkeypatch.setattr(SchedulerApp, "_show_toast", _record_toast)
    w.refusals = refusals
    w.toasts = toasts
    return w


# ── 1. the measurement ──────────────────────────────────────────────────────

def test_a_real_multi_select_grid_drag_moves_only_the_lesson_under_the_cursor(
        win, monkeypatch):
    """ST-DATA-010, driven end to end through production. GREEN TODAY.

    Every field the drop reads is set by ``_start_drag_gfx`` itself, and the
    selection is built by ``_select_class_gfx``. A failure of the first
    assertion means the selection never reached the drag at all (a different
    defect from the registered one); a failure of the *second* means the
    registered behaviour changed and
    ``tests/test_drag_and_drop.py::test_a_multi_select_drag_is_one_undo_for_
    what_actually_moved`` is now wrong too.
    """
    fizik, kimya = _seed(win)
    items = _ctrl_click_both(win, "Fizik", "Kimya")

    # Not decoration. Tab 0 is `_filter_classroom`, one room at a time, so
    # every lesson a user can Ctrl-click into one selection here is in the
    # SAME room — and two lessons in one room cannot share one cell. That is
    # the shape of the question "should a multi-drag move all N onto the
    # dropped cell?"
    rooms = {it.cls["placed_classroom"] for it in _lesson_items(win).values()}
    assert rooms == {"R001"}, (
        "the default timetable tab showed lessons from more than one room "
        "(%r); the argument that a same-tab multi-selection is same-room "
        "needs re-measuring" % (sorted(rooms),))

    captured = {}
    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)

    def _drop_on_the_far_corner():
        # Mid-gesture, before the tail clears everything: this is the only
        # moment at which "did the selection reach the drag?" is answerable.
        captured["group"] = [c["name"] for c in win._dragging_classes]
        captured["placed_during_drag"] = {
            c["name"]: c.get("placed") for c in win._dragging_classes}
        captured["backup"] = dict(win._drag_backup or {})
        win._execute_drop("tuesday", "11:00")

    monkeypatch.setattr(_FakeDrag, "on_exec", staticmethod(
        _drop_on_the_far_corner))
    win._start_drag_gfx(fizik, items["Fizik"])

    assert captured.get("group") == ["Fizik", "Kimya"], (
        "the production starter did not carry the selection into "
        "_dragging_classes: %r" % (captured.get("group"),))
    assert captured.get("placed_during_drag") == {
        "Fizik": False, "Kimya": True}, (
        "the starter unplaced something other than the primary alone: %r — "
        "the registered mechanism is that only the primary is unplaced, which "
        "is what makes _execute_drop's all(not placed) guard False"
        % (captured.get("placed_during_drag"),))
    assert captured.get("backup") == {
        "placed": True, "placed_day": "monday", "placed_time": "09:00",
        "placed_classroom": "R001"}, (
        "the drag backup is not the primary's placement alone: %r"
        % (captured.get("backup"),))

    assert win._drag_success, (
        "the drop did not commit; wrong failure — refusals %r"
        % (win.refusals,))
    assert _placement(_by_name(win, "Fizik")) == (
        True, "tuesday", "11:00", "R001"), (
        "the lesson under the cursor did not land on the dropped cell: %r"
        % (_placement(_by_name(win, "Fizik")),))
    assert _placement(_by_name(win, "Kimya")) == (
        True, "monday", "10:00", "R001"), (
        "the second selected lesson moved to %r. ST-DATA-010 says it does "
        "not move; if this fails the registered behaviour has changed"
        % (_placement(_by_name(win, "Kimya")),))

    # The user is not left guessing: the success toast names exactly the one
    # lesson that moved, and says where it went.
    moved = [m for m, _k in win.toasts
             if "Fizik" in m and "11:00" in m and "R001" in m]
    assert moved, (
        "no toast named the lesson that moved and where it went; the toasts "
        "were %r" % (win.toasts,))
    assert not any("Kimya" in m for m, _k in win.toasts), (
        "a toast mentioned the lesson that did not move: %r" % (win.toasts,))

    assert len(win._undo_stack) == 1, (
        "a whole multi-select drag must be exactly one Ctrl+Z; depth is %d"
        % len(win._undo_stack))
    win.undo()
    assert _placement(_by_name(win, "Fizik")) == (
        True, "monday", "09:00", "R001")
    assert _placement(_by_name(win, "Kimya")) == (
        True, "monday", "10:00", "R001"), (
        "one undo disturbed the lesson the drag never moved: %r"
        % (_placement(_by_name(win, "Kimya")),))


# ── 2. the contrast: the same selection, a drop target where N->N is defined ─

def test_the_same_multi_selection_dropped_on_the_sidebar_unplaces_all_of_them(
        win, monkeypatch):
    """The selection is not lost — it is honoured wherever N->N is meaningful.

    ``DraggableUnplacedList.dropEvent`` reads the same ``_dragging_classes``
    and unplaces every member. So "the drag forgets the selection" is the
    wrong diagnosis of ST-DATA-010: the drag carries it, and the *cell* drop
    handler is the one that declines to use it, because dropping N lessons on
    ONE cell has no definition.
    """
    fizik, kimya = _seed(win)
    items = _ctrl_click_both(win, "Fizik", "Kimya")

    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)

    def _drop_on_the_unplaced_sidebar():
        from scheduler_app.ui.app import DraggableUnplacedList
        DraggableUnplacedList.dropEvent(win.unplaced_list, _MimeDropEvent())

    monkeypatch.setattr(_FakeDrag, "on_exec", staticmethod(
        _drop_on_the_unplaced_sidebar))
    win._start_drag_gfx(fizik, items["Fizik"])

    assert win._drag_success, "the sidebar drop did not commit"
    assert _placement(_by_name(win, "Fizik")) == (False, None, None, None), (
        "the primary was not unplaced: %r"
        % (_placement(_by_name(win, "Fizik")),))
    assert _placement(_by_name(win, "Kimya")) == (False, None, None, None), (
        "the SECOND selected lesson was not unplaced: %r. If this fails, the "
        "multi-selection really is being dropped on the floor and item 3 is a "
        "defect after all" % (_placement(_by_name(win, "Kimya")),))

    assert len(win._undo_stack) == 1, (
        "the sidebar drop is one Ctrl+Z; depth is %d" % len(win._undo_stack))
    win.undo()
    assert _placement(_by_name(win, "Fizik")) == (
        True, "monday", "09:00", "R001")
    assert _placement(_by_name(win, "Kimya")) == (
        True, "monday", "10:00", "R001")


class _MimeDropEvent:
    """The two calls ``DraggableUnplacedList.dropEvent`` makes on its event."""

    class _Mime:
        def hasText(self):
            return True

        def text(self):
            return "class_drag:whatever"

    def mimeData(self):
        return self._Mime()

    def acceptProposedAction(self):
        pass


# ── 3. the handoff's sketched fix, built and measured ───────────────────────

def test_unplacing_the_whole_drag_group_makes_the_drop_ignore_the_cell(
        win, monkeypatch):
    """The sketched fix, through production, and what it actually does.

    The sketch is: make ``_start_drag_gfx`` back up and ``mark_unplaced`` the
    whole selection, so ``_execute_drop``'s ``all(not c.get("placed"))`` guard
    becomes True and the multi-lesson branch runs. That branch is
    ``_execute_drop_anywhere()`` -> ``_place_classes_batch(drag_group)``, and
    it is called with **no arguments** — ``(day, slot)`` never reaches it.

    Rather than argue that, this builds it: ``mark_unplaced`` is replaced at
    ``_start_drag_gfx``'s own call site so the real starter unplaces every
    member of the group it already assembled, and the real drop then runs on
    a real target cell. What is asserted is where the lessons ended up.
    """
    fizik, kimya = _seed(win)
    items = _ctrl_click_both(win, "Fizik", "Kimya")

    from scheduler_app.ui import app as app_module
    real_mark_unplaced = app_module.mark_unplaced

    def _unplace_the_whole_group(cls):
        real_mark_unplaced(cls)
        for other in list(getattr(win, "_dragging_classes", []) or []):
            if other is not cls:
                real_mark_unplaced(other)

    monkeypatch.setattr("scheduler_app.ui.app.mark_unplaced",
                        _unplace_the_whole_group)
    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)

    seen = {}

    def _drop_on_the_far_corner():
        seen["placed_during_drag"] = {
            c["name"]: c.get("placed") for c in win._dragging_classes}
        win._execute_drop("tuesday", "11:00")

    monkeypatch.setattr(_FakeDrag, "on_exec", staticmethod(
        _drop_on_the_far_corner))
    win._start_drag_gfx(fizik, items["Fizik"])

    assert seen.get("placed_during_drag") == {
        "Fizik": False, "Kimya": False}, (
        "the mutation did not take: the group was not fully unplaced, so the "
        "multi-lesson branch was not reached — %r"
        % (seen.get("placed_during_drag"),))

    landed = {n: _placement(_by_name(win, n)) for n in ("Fizik", "Kimya")}
    target = ("tuesday", "11:00")
    on_target = [n for n, p in landed.items() if (p[1], p[2]) == target]
    assert on_target == [], (
        "the sketched fix honoured the drop target for %r; measured "
        "landings were %r. If this fails the batch placer happens to have "
        "chosen the target cell and the argument below needs re-measuring"
        % (on_target, landed))

    # Measured 2026-08-29 at 3d87515. The user picked Fizik up off
    # monday 09:00 and dropped it on tuesday 11:00. Under the sketched fix
    # Fizik lands on monday 10:00 and Kimya, which the user never dragged,
    # is moved out of R001 into R002. Neither lesson is where it was and
    # neither is where the user aimed: the solver re-derived both from
    # scratch, because `_place_classes_batch` is "auto-place these", not
    # "put these here".
    assert landed == {
        "Fizik": (True, "monday", "10:00", "R001"),
        "Kimya": (True, "monday", "10:00", "R002")}, (
        "the sketched fix produced %r rather than the outcome measured at "
        "3d87515. The specific cells are the solver's business and may move; "
        "what must not change is the assertion above, that the cell the user "
        "dropped on is not among them" % (landed,))
    assert _placement(_by_name(win, "Fizik"))[1:3] != ("monday", "09:00"), (
        "the seed is wrong: Fizik never left its original cell, so this "
        "measures nothing")


# ── 4. is "both lessons onto this one cell" even expressible? ───────────────

def test_the_second_selected_lesson_can_follow_the_first_onto_that_cell_alone(
        win, monkeypatch):
    """Measures the alternative the register calls a product decision.

    "Move all N onto the dropped cell" is only a coherent option if the app
    can hold them there. This drops Fizik on tuesday 11:00 exactly as the test
    above does, then drives a SECOND, ordinary single drag of Kimya onto the
    same cell — the drop the "move all" semantics would have to perform for
    the second member of the selection — and records production's answer.

    Green today either way; the assertion is written against what was
    measured, so a change of answer shows up as a failure rather than as a
    stale claim in a report.
    """
    fizik, kimya = _seed(win)
    items = _ctrl_click_both(win, "Fizik", "Kimya")

    monkeypatch.setattr("scheduler_app.ui.app.QDrag", _FakeDrag)
    monkeypatch.setattr(_FakeDrag, "on_exec", staticmethod(
        lambda: win._execute_drop("tuesday", "11:00")))
    win._start_drag_gfx(fizik, items["Fizik"])
    assert _placement(_by_name(win, "Fizik")) == (
        True, "tuesday", "11:00", "R001"), (
        "the first drop did not land: %r" % (_placement(_by_name(win, "Fizik")),))

    win.refresh_grid()
    items = _lesson_items(win)
    assert "Kimya" in items, (
        "Kimya vanished from the tab after the first drop: %r"
        % (sorted(items),))
    win._select_class_gfx(items["Kimya"].cls, items["Kimya"],
                          Qt.KeyboardModifier.NoModifier)

    refusals_before = len(win.refusals)
    win._start_drag_gfx(items["Kimya"].cls, items["Kimya"])

    landed = _placement(_by_name(win, "Kimya"))
    assert landed == (True, "tuesday", "11:00", "R002"), (
        "dropping the second lesson on the cell the first now occupies gave "
        "%r; refusals raised: %d. Measured at 3d87515 it succeeded into the "
        "other room — so 'move all N onto the dropped cell' is expressible "
        "here, and the register's 'product decision' framing is the right "
        "one: the choice is a design choice, not an impossibility"
        % (landed, len(win.refusals) - refusals_before))
