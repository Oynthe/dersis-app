"""The timetable must be reachable without a mouse, and say what it is on.

ST-UI-004 (High) · ``ui/renderer.py``
    "Timetable grid is mouse-only and invisible to assistive tech." For a tool
    aimed at school and public-sector administrators, the core surface required
    a mouse for selection, movement, context menus and empty-cell interaction,
    and reported nothing to a screen reader.

What the register gets wrong, and it changes the design
------------------------------------------------------
The finding's evidence is two greps, and the inference drawn from them is
backwards. ``TimetableView`` **already has focus**: it inherits
``QAbstractScrollArea``'s StrongFocus (measured ``focusPolicy() == 11``) and is
already reachable by Tab. The problem is the opposite of "it cannot be focused"
— the arrows are already *consumed*, to scroll the viewport, so a naive
``keyPressEvent`` that calls ``super()`` moves the cursor **and** scrolls. Every
handled key here therefore accepts and returns.

The addressable unit is (col, row, LANE), not (col, row)
--------------------------------------------------------
Phase 4 splits a contested cell into lanes inside one column. ``cell_at``
answers ``(day, slot)``, so both halves of a double-booking share one address —
measured, two ``LessonItem``s at x=85.5 and x=161.0 both round-trip to
``('monday', '09:00')``. A cursor keyed on the cell could never reach lane 1,
which would reintroduce **ST-UI-001** — a lesson that is on the timetable and
cannot be got to — for keyboard users only. It occurs on real data: on the
``large`` preset, 1 of 16 room tabs carries laned blocks.

What is NOT achievable, stated plainly
--------------------------------------
The audit proposes "each lesson/empty cell gets an accessible name". That cannot
be built in this toolkit at any effort:

* ``QGraphicsItem`` is not a ``QObject`` and has no ``setAccessibleName`` —
  calling it raises ``AttributeError`` rather than silently doing nothing.
* PyQt6 exposes **no** ``QAccessible`` bindings at all (verified across QtCore,
  QtGui, QtWidgets, QtTest, QtPrintSupport, QtSvg, QtNetwork — only an
  unrelated ``QAccessibilityHints``), so the standard Qt answer for
  custom-painted content, a per-cell ``QAccessibleInterface``, cannot be
  written.

So the achievable thing is the **view** carrying the description of its cursor
cell: one AT node that changes, not N nodes. That is less than was asked for and
more than the zero accessibility calls the package makes today.

Why these tests assert relations, not pixels
--------------------------------------------
``QT_QPA_PLATFORM=offscreen`` has no Segoe UI (``QFontInfo(...).family()`` is
``''``), advances run ~2x native, and that changes which cell rows get *dropped*,
not merely their size. Nothing below pins an absolute coordinate.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

from PyQt6.QtCore import QEvent, Qt  # noqa: E402
from PyQt6.QtGui import QFocusEvent, QKeyEvent  # noqa: E402

from scheduler_app.core.logic import (  # noqa: E402
    classroom_of, conflict_partner_index, find_schedule_conflicts,
)
from scheduler_app.core.models import (  # noqa: E402
    new_class, new_state, mark_placed,
)
from scheduler_app.ui.renderer import (  # noqa: E402
    FILTER_MODE_DEFAULT, LessonItem, TimetableScene, TimetableView,
)

pytestmark = pytest.mark.ui


def _contested_state():
    """Three days x three hours, with two lessons double-booked in R001."""
    state = new_state()
    state["days"] = ["monday", "tuesday", "wednesday"]
    state["slots"] = ["09:00", "10:00", "11:00"]
    state["classrooms"] = ["R001", "R002"]
    state["years"] = {"Year-1": ["A", "B"]}
    state["lecturers"] = ["Lect-01", "Lect-02"]

    def mk(name, code, lecturer, branch):
        cls = new_class()
        cls["name"] = name
        cls["lecturer"] = lecturer
        cls["class_code"] = code
        cls["targets"] = [{"year": "Year-1", "branch": branch}]
        return cls

    first = mk("Fizik", "AAA111", "Lect-01", "A")
    second = mk("Kimya", "ZZZ999", "Lect-02", "B")
    state["classes"] = [first, second]
    for cls in (first, second):
        mark_placed(cls, "monday", "09:00", "R001")
    return state


def _scene_for(state):
    """A fresh scene, built the way ``_render_grid`` builds one."""
    scene = TimetableScene()
    scene.build_filtered(
        state, lambda c: classroom_of(c) == "R001", app=None,
        mode=FILTER_MODE_DEFAULT,
        conflict_partners=conflict_partner_index(
            find_schedule_conflicts(state)))
    return scene


def _view_on(state, qapp):
    view = TimetableView()
    view.setScene(_scene_for(state))
    return view


def _press(view, key, mods=Qt.KeyboardModifier.NoModifier):
    event = QKeyEvent(QEvent.Type.KeyPress, key, mods)
    view.keyPressEvent(event)
    return event.isAccepted()


def _code_at_cursor(view):
    item = view.cursor_item()
    if item is None or not isinstance(item, LessonItem):
        return None
    return item.cls.get("class_code")


# ── the coordinate system ──────────────────────────────────────────────

def test_a_contested_cell_exposes_one_lane_per_lesson(qapp):
    """ST-UI-004 / ST-UI-001 — both halves of a clash must be addressable.

    A failure means a keyboard user cannot reach one of two double-booked
    lessons: it is on the timetable, and for them it does not exist.
    """
    scene = _scene_for(_contested_state())
    assert scene.cursor_lane_count(0, 0) == 2, (
        "the contested cell exposes %d lanes, expected 2"
        % scene.cursor_lane_count(0, 0))
    codes = [t.cls.get("class_code") for t in scene.cursor_targets(0, 0)]
    assert sorted(codes) == ["AAA111", "ZZZ999"], codes


def test_cell_at_cannot_tell_the_two_apart_which_is_why_lanes_exist(qapp):
    """ST-UI-004 — anti-vacuity: prove the lane axis is load-bearing.

    If ``cell_at`` could distinguish the two lessons, the whole lane mechanism
    would be unnecessary and the test above would be pinning nothing. This
    records *why* the cursor is not keyed on the cell.
    """
    scene = _scene_for(_contested_state())
    addresses = {scene.cell_at(item.sceneBoundingRect().center())
                 for item in scene.cursor_targets(0, 0)}
    assert len(addresses) == 1, (
        "cell_at now distinguishes the contested lessons (%r); the cursor could "
        "be simplified" % (addresses,))


def test_a_multi_hour_lesson_is_reachable_from_every_hour_it_covers(qapp):
    """ST-UI-004 — arrowing down through a 2-hour block must not fall past it."""
    state = _contested_state()
    state["classes"] = state["classes"][:1]
    state["classes"][0]["duration"] = 2
    mark_placed(state["classes"][0], "monday", "09:00", "R001")

    scene = _scene_for(state)
    covered = [row for row in range(3)
               if any(isinstance(t, LessonItem)
                      for t in scene.cursor_targets(0, row))]
    assert covered == [0, 1], (
        "a 2-hour lesson should be addressable from both its rows, got %r"
        % (covered,))


# ── movement ───────────────────────────────────────────────────────────

def test_focusing_the_grid_puts_the_cursor_somewhere(qapp):
    """ST-UI-004 — Tab into the grid and there is a cursor to move."""
    view = _view_on(_contested_state(), qapp)
    assert view._cursor is None
    view.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    assert view._cursor is not None, "focusing the grid left no cursor"
    assert view.cursor_item() is not None


@pytest.mark.parametrize("key", [Qt.Key.Key_Right, Qt.Key.Key_Down])
def test_arrow_keys_are_consumed_not_forwarded_to_the_scroller(key, qapp):
    """ST-UI-004 — the arrows already scrolled; they must not do both.

    ``QAbstractScrollArea`` consumes arrows to scroll the viewport. A
    ``keyPressEvent`` that falls through to ``super()`` would move the cursor
    *and* scroll the grid out from under it.

    The view is deliberately made SMALLER than its scene. The first version of
    this test used the default size, where the whole grid fits, both scrollbars
    have range 0, and "nothing scrolled" is true no matter what the handler
    does — it passed with the handler forwarding every key to ``super()``.

    ``show()`` is needed and is safe: measured, ``resize`` alone leaves the
    viewport at its unlaid-out 638x478 and both scrollbar maxima at 0, and only
    a real layout pass gives them range (123 and 353). The platform is
    ``offscreen``, so nothing is displayed — this is the one place in the suite
    that needs a laid-out widget rather than a measured one.
    """
    view = _view_on(_contested_state(), qapp)
    view.resize(200, 150)
    view.show()
    qapp.processEvents()
    view.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))

    bars = (view.verticalScrollBar(), view.horizontalScrollBar())
    assert all(b.maximum() > 0 for b in bars), (
        "the scene fits the viewport, so this test cannot observe a scroll "
        "(maxima %r)" % ([b.maximum() for b in bars],))

    try:
        # The view IS expected to scroll a little: `set_cursor` calls
        # `ensureVisible` so the cursor cannot move off screen. What must not
        # happen is scrolling *beyond* that, which is what forwarding the key
        # to QAbstractScrollArea adds. So the reference is the same cursor move
        # performed without a key event at all.
        start = view._cursor
        target = ((start[0] + 1, start[1]) if key == Qt.Key.Key_Right
                  else (start[0], start[1] + 1))

        view.set_cursor(*target)
        expected = tuple(b.value() for b in bars)

        view.set_cursor(*start[:2], lane=start[2])
        for bar, value in zip(bars, expected):
            bar.setValue(bar.value())          # settle
        assert _press(view, key) is True, "arrow key was not accepted"
        actual = tuple(b.value() for b in bars)

        assert view._cursor[:2] == target, "the cursor did not move"
        assert actual == expected, (
            "pressing the key scrolled to %r where moving the cursor alone "
            "scrolls to %r — the key was also forwarded to "
            "QAbstractScrollArea" % (actual, expected))
    finally:
        view.close()


def test_arrows_walk_the_grid(qapp):
    """ST-UI-004 — the basic promise of the finding."""
    view = _view_on(_contested_state(), qapp)
    view.set_cursor(0, 0, 0)
    _press(view, Qt.Key.Key_Right)
    assert view._cursor[:2] == (1, 0)
    _press(view, Qt.Key.Key_Down)
    assert view._cursor[:2] == (1, 1)
    _press(view, Qt.Key.Key_Left)
    assert view._cursor[:2] == (0, 1)
    _press(view, Qt.Key.Key_Up)
    assert view._cursor[:2] == (0, 0)


def test_alt_arrow_reaches_the_second_lesson_in_a_contested_cell(qapp):
    """ST-UI-004 — the whole reason the cursor carries a lane.

    A failure is ST-UI-001 for keyboard users: a lesson that is drawn on the
    timetable and cannot be selected, edited, unplaced or even announced.
    """
    view = _view_on(_contested_state(), qapp)
    view.set_cursor(0, 0, 0)
    first = _code_at_cursor(view)
    _press(view, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    second = _code_at_cursor(view)

    assert first is not None and second is not None
    assert first != second, (
        "Alt+Right did not move to the other lane; both reads gave %r" % first)
    assert sorted([first, second]) == ["AAA111", "ZZZ999"]


def test_the_cursor_does_not_run_off_the_grid(qapp):
    """ST-UI-004 — clamping, so holding an arrow down cannot crash or escape."""
    view = _view_on(_contested_state(), qapp)
    view.set_cursor(0, 0, 0)
    for _ in range(10):
        _press(view, Qt.Key.Key_Left)
        _press(view, Qt.Key.Key_Up)
    assert view._cursor[:2] == (0, 0)
    for _ in range(10):
        _press(view, Qt.Key.Key_Right)
        _press(view, Qt.Key.Key_Down)
    assert view._cursor[:2] == (2, 2)
    assert view.cursor_item() is not None


def test_moving_the_cursor_does_not_select(qapp):
    """ST-UI-004 — arrows move; selecting is an explicit act.

    ``_select_class_gfx`` rebuilds the whole open-slots sidebar, so selecting on
    every keystroke would make the grid unusable to hold an arrow down in. The
    app object is None here, so a cursor move that tried to select would raise
    — which is the assertion.
    """
    view = _view_on(_contested_state(), qapp)
    view.set_cursor(0, 0, 0)
    for key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Left):
        _press(view, key)          # must not touch app state
    assert view.cursor_item() is not None


# ── surviving the rebuild ──────────────────────────────────────────────

def test_the_cursor_follows_its_lesson_across_a_scene_rebuild(qapp):
    """ST-UI-004 — ``_render_grid`` builds a NEW scene on every refresh.

    Anti-vacuity, and this is the trap: items of a swapped-out scene are not
    deleted while any Python reference to that scene survives, so a test that
    keeps the old scene in a local would pass even for a cursor holding a raw
    item reference. No reference is kept — the scene is built inline — and the
    assertion is that the resolved item belongs to the CURRENT scene, which an
    item-reference cursor cannot satisfy.
    """
    state = _contested_state()
    view = TimetableView()
    view.setScene(_scene_for(state))          # no local reference kept
    view.set_cursor(0, 0, 1)
    before = _code_at_cursor(view)
    assert before is not None

    view.setScene(_scene_for(state))          # a brand-new scene

    item = view.cursor_item()
    assert item is not None, "the cursor lost its item across the rebuild"
    assert item.scene() is view.scene(), (
        "the cursor resolved to an item from the OLD scene")
    assert _code_at_cursor(view) == before, (
        "the cursor changed which lesson it means: %r -> %r"
        % (before, _code_at_cursor(view)))


def test_the_cursor_follows_a_lesson_that_moved(qapp):
    """ST-UI-004 — identity beats coordinate.

    After a drag or a reschedule the lesson under the cursor is somewhere else.
    Holding the coordinate would silently point at a different class; the cursor
    re-anchors by ``cls_key``.
    """
    state = _contested_state()
    view = TimetableView()
    view.setScene(_scene_for(state))
    view.set_cursor(0, 0, 1)
    followed = _code_at_cursor(view)

    moved = next(c for c in state["classes"]
                 if c.get("class_code") == followed)
    mark_placed(moved, "wednesday", "11:00", "R001")
    view.setScene(_scene_for(state))

    assert _code_at_cursor(view) == followed, (
        "the cursor stayed on the coordinate instead of following %r" % followed)
    item = view.cursor_item()
    assert (item.day, item.slot) == ("wednesday", "11:00")


def test_the_cursor_parks_where_there_are_no_coordinates(qapp):
    """ST-UI-004 — the everything matrix has its own geometry and no cursor yet.

    Deferred deliberately rather than half-built: that view is
    session/time/branch columns with day header rows, and one block per matching
    *target*, so a class with two targets has two blocks and no single address.
    Parking is honest; pointing into a stale index would not be.
    """
    state = _contested_state()
    view = TimetableView()
    view.setScene(_scene_for(state))
    view.set_cursor(0, 0, 0)
    assert view._cursor is not None

    scene = TimetableScene()
    scene.build_everything(state, app=None)
    view.setScene(scene)
    assert view._cursor is None, "the cursor survived into a scene it cannot address"
    assert view.cursor_item() is None


# ── what a screen reader gets ──────────────────────────────────────────

def test_the_view_announces_the_lesson_under_the_cursor(qapp):
    """ST-UI-004 — the achievable half of AT exposure.

    Today ``accessibleName()`` is ``''`` and the package makes zero
    accessibility calls, so a screen reader on this grid says nothing at all.
    """
    from scheduler_app.translations import tr

    view = _view_on(_contested_state(), qapp)
    view.set_cursor(0, 0, 0)
    said = view.accessibleDescription()
    item = view.cursor_item()

    assert said, "the view reports nothing for the cursor cell"
    assert item.cls["name"] in said, "the class name is not announced: %r" % said
    assert item.cls["lecturer"] in said, "the lecturer is not announced"
    assert tr("labels.classroom") in said, (
        "the room is not announced, and it is the field the audit calls the "
        "most important in the cell: %r" % said)


def test_the_announcement_names_which_lane_of_a_contested_cell(qapp):
    """ST-UI-004 — "there are two here and you are on the second"."""
    view = _view_on(_contested_state(), qapp)
    view.set_cursor(0, 0, 0)
    first = view.accessibleDescription()
    view.set_cursor(0, 0, 1)
    second = view.accessibleDescription()

    assert first != second, "both lanes announce identically"
    assert "2" in second, (
        "the announcement does not say which of the two lessons this is: %r"
        % second)


def test_an_empty_cell_announces_itself_as_empty(qapp):
    """ST-UI-004 — silence on an empty cell is indistinguishable from a bug."""
    from scheduler_app.translations import tr

    view = _view_on(_contested_state(), qapp)
    view.set_cursor(1, 1, 0)
    said = view.accessibleDescription()
    assert tr("labels.empty_slot") in said, said


def test_the_announcement_is_the_same_vocabulary_the_tooltip_uses(qapp):
    """ST-UI-004 / ST-UI-002 — one description per cell, not two.

    Guards against a second sentence builder drifting from ``tooltip_text``, the
    way three "placed" definitions drifted before Phase 4 unified them.
    """
    from scheduler_app.ui.cell_formatter import tooltip_text

    view = _view_on(_contested_state(), qapp)
    view.set_cursor(0, 0, 0)
    said = view.accessibleDescription()
    for line in tooltip_text(view.cursor_item().cls).splitlines():
        if line.strip():
            assert line in said, (
                "the tooltip says %r but the announcement does not carry it"
                % line)


def test_the_grid_reports_no_accessible_text_before_the_fix_is_reachable(qapp):
    """ST-UI-004 — anti-vacuity for the announcement tests.

    A view with no cursor must describe nothing, so the assertions above are
    testing the cursor rather than some ambient default.
    """
    view = _view_on(_contested_state(), qapp)
    assert view._cursor is None
    assert not view.accessibleDescription()
