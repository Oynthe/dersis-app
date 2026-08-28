"""Small UI-consistency defects: the app must not lie about where things are.

Phase 5 (UI consistency & accessibility). This module collects the low-level
affordance and feedback defects that are too small to earn a module each but are
each visible to a user in normal operation.

ST-UI-010 (Medium) · ``widgets.py`` ``Toast.__init__``
    The toast positioned itself from the **parent's local size** and only
    afterwards set ``Qt.WindowType.ToolTip``. That flag makes the widget a
    *top-level window*, and ``QWidget.move`` on a window takes **global** screen
    coordinates — so the local bottom-right corner was reinterpreted as a screen
    point. The toast was therefore pinned to one fixed spot on the display
    instead of hugging the window's corner.

    Why this matters to a user: every confirmation the app gives — "class
    placed", "file saved", "import finished" — arrives through this widget. On a
    window that is not at the display origin the message appears somewhere else
    entirely, and on a multi-monitor desk it can land on the other screen or off
    the desktop. The user performs an action and sees no feedback at all.

    Measured before the fix, parent 1150x720:

        window origin (0, 0)      toast at (929, 650)   offset (  -2,   -2)
        window origin (500, 330)  toast at (929, 650)   offset (-502, -332)
        window origin (1200, 100) toast at (929, 650)   offset (-1202, -102)

    — the same absolute screen point every time, displaced by exactly the
    window's own origin. The audit reported the (500, 330) case as "500 px
    measured"; it reproduces exactly.

Why the obvious test is vacuous, and what these assert instead
-------------------------------------------------------------
"Assert the toast is inside the window" **passes against the broken code** at
the very origin the audit measured: a window at (500, 330) spans x 500..1650, and
the stuck toast at x 929 is comfortably inside it. A containment assertion would
have certified the bug.

The property that is actually broken is *relative*: the toast's offset from the
window's bottom-right corner must not depend on where the window is. So the tests
below compare two or more window origins against each other, which cannot pass
while the position is computed in the wrong coordinate space.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QWidget  # noqa: E402

from scheduler_app.ui.widgets import Toast  # noqa: E402

pytestmark = pytest.mark.ui

# Far enough apart that a stuck toast cannot accidentally satisfy the assertion
# for more than one of them.
ORIGINS = [(0, 0), (500, 330), (1200, 100)]

PARENT_W, PARENT_H = 1150, 720
MARGIN = 20  # the inset Toast.__init__ uses on both axes


def _corner_gap(qapp, origin):
    """Return the toast's gap from its parent's bottom-right corner, in px.

    Both quantities are taken in *global* coordinates, which is the only frame
    in which the two are comparable once the toast has become a window.
    """
    parent = QWidget()
    parent.resize(PARENT_W, PARENT_H)
    parent.move(*origin)
    toast = Toast(parent, "Ders yerlestirildi", duration=60_000, kind="success")
    try:
        parent_br = parent.mapToGlobal(QPoint(parent.width(), parent.height()))
        toast_br = toast.pos() + QPoint(toast.width(), toast.height())
        return (parent_br.x() - toast_br.x(), parent_br.y() - toast_br.y())
    finally:
        toast.close()
        toast.deleteLater()
        parent.close()
        parent.deleteLater()
        qapp.processEvents()


def test_toast_sits_in_the_same_place_whatever_the_window_origin(qapp):
    """ST-UI-010 — the toast must follow its window, not the screen.

    A failure means a user who has not maximised the app, or who works on a
    second monitor, gets no visible confirmation for any action.
    """
    gaps = {origin: _corner_gap(qapp, origin) for origin in ORIGINS}
    distinct = set(gaps.values())
    assert len(distinct) == 1, (
        "the toast's offset from its window's corner changes with the window's "
        "screen position, so it is being placed in screen coordinates: %r" % (gaps,)
    )


def test_toast_hugs_the_bottom_right_corner_of_its_window(qapp):
    """ST-UI-010 — and the one place it sits is the intended one.

    Guards the direction of the fix: pinning the *relative* offset alone would
    also be satisfied by a toast consistently placed in the wrong corner.
    """
    for origin in ORIGINS:
        gap_x, gap_y = _corner_gap(qapp, origin)
        assert (gap_x, gap_y) == (MARGIN, MARGIN), (
            "toast at window origin %r sits %r from the bottom-right corner, "
            "expected (%d, %d)" % (origin, (gap_x, gap_y), MARGIN, MARGIN)
        )


def test_toast_is_a_top_level_window_so_the_coordinate_space_matters(qapp):
    """ST-UI-010 — anti-vacuity guard for the two tests above.

    Both assertions are only meaningful because ``Qt.WindowType.ToolTip`` makes
    the toast a window, which is what changes ``move()`` from parent-local to
    global. If a future change drops that flag the toast becomes an ordinary
    child widget, ``mapToGlobal`` becomes the wrong call, and the tests above
    would keep passing while describing nothing. Pin the premise.
    """
    parent = QWidget()
    parent.resize(PARENT_W, PARENT_H)
    parent.move(400, 250)
    toast = Toast(parent, "Kontrol", duration=60_000)
    try:
        assert toast.isWindow(), (
            "Toast is no longer a top-level window; the positioning tests in "
            "this module assume the global coordinate space that flag implies"
        )
    finally:
        toast.close()
        toast.deleteLater()
        parent.close()
        parent.deleteLater()
        qapp.processEvents()


# ═══════════════════════════════════════════════════════════════════════
#  The app must honour the cell it is pointing at
# ═══════════════════════════════════════════════════════════════════════
#
# Two defects with one shape: a surface names a specific day/hour/room and then
# does not use it. Neither is in the findings register; both were found while
# measuring Phase 5.
#
# ST-ARCH-004 (the open-slots panel) · ``app.py`` ``_refresh_open_slots``
#     ``find_valid_options`` returns ``(day, slot, None)`` for a lesson that
#     needs no physical room — ``get_room_candidates``' sentinel, which Phase 3
#     already taught the drag path to read. This panel re-keyed ``None`` to
#     ``""`` and then tested membership against ``state["classrooms"]``, which
#     can never contain ``""``. So it reported "no valid placements" for **every
#     online lesson**, while ``PlaceClassDialog`` — same function, same state —
#     listed them. Measured on a 2-day × 3-slot grid: 6 options, 0 rows drawn.
#
# ``_add_class_at`` · ``app.py``
#     Took ``day`` and ``slot`` and discarded both, going straight to automatic
#     placement. Reached from a double-click on an empty cell and from a context
#     menu **headed by that cell's own name** ("📅 Çarşamba 10:00") — whose other
#     command, ``_place_unplaced_class_at_slot``, does honour it. One menu, two
#     commands, disagreeing about whether its title meant anything.

import pytest  # noqa: E402,F811

from scheduler_app.core.models import (  # noqa: E402
    new_state, new_class, cls_key, LOCATION_ONLINE,
)
from scheduler_app.core.logic import find_valid_options  # noqa: E402


def _online_state():
    state = new_state()
    state["days"] = ["monday", "tuesday"]
    state["slots"] = ["09:00", "10:00", "11:00"]
    state["classrooms"] = ["R001", "R002"]
    state["years"] = {"Year-1": ["A"]}
    state["lecturers"] = ["L1"]

    cls = new_class()
    cls["name"] = "Uzaktan Fizik"
    cls["lecturer"] = "L1"
    cls["location_type"] = LOCATION_ONLINE
    cls["targets"] = [{"year": "Year-1", "branch": "A"}]
    state["classes"] = [cls]
    return state, cls


def test_an_online_lesson_has_somewhere_to_go_and_the_panel_says_so(
        qapp, dersis_home, make_app):
    """ST-ARCH-004 — the open-slots panel must not contradict the placer.

    A failure means a user with an online class is told, in the panel built to
    answer "where can this go?", that it can go nowhere — while the placement
    dialog on the same class offers a full grid.
    """
    from tests.test_warning_log_growth import _open_slot_rows

    state, cls = _online_state()
    options = find_valid_options(state, cls)
    assert options, "fixture is wrong: the class has no valid options at all"
    assert all(opt[2] is None for opt in options), (
        "fixture is wrong: an online lesson's options should carry the None "
        "room sentinel, got %r" % (options[:3],))

    window = make_app()
    window.state_data = state
    window._selected_cells = []
    window.unplaced_list.clear()
    window.unplaced_list.addItem(cls["name"])
    # ST-ARCH-015: the sidebar addresses classes by uid, not by position.
    window._unplaced_uids = [cls_key(cls)]
    window.unplaced_list.setCurrentRow(0)
    window._switch_sidebar_tab(1)
    window._open_slots_fp = None
    window._refresh_open_slots()

    rows = _open_slot_rows(window)
    assert len(rows) == len(options), (
        "the panel drew %d rows for a class with %d valid placements"
        % (len(rows), len(options)))


def test_the_panel_names_the_online_resource_instead_of_an_empty_room(
        qapp, dersis_home, make_app):
    """ST-ARCH-004 — a None room is a resource, not a missing value."""
    from tests.test_warning_log_growth import _open_slot_rows
    from scheduler_app.core.models import get_effective_room_resource_for_class

    state, cls = _online_state()
    window = make_app()
    window.state_data = state
    window.unplaced_list.clear()
    window.unplaced_list.addItem(cls["name"])
    # ST-ARCH-015: the sidebar addresses classes by uid, not by position.
    window._unplaced_uids = [cls_key(cls)]
    window.unplaced_list.setCurrentRow(0)
    window._switch_sidebar_tab(1)
    window._open_slots_fp = None
    window._refresh_open_slots()

    expected = get_effective_room_resource_for_class(cls, room_override=None)
    rows = _open_slot_rows(window)
    assert rows, "no rows to inspect"
    assert all(room == expected for _day, _time, room in rows), (
        "expected every row to name %r, got %r" % (expected, rows[:3]))
    assert all(room.strip() for _d, _t, room in rows), (
        "a row rendered an empty room label")


def test_adding_a_class_on_a_chosen_cell_puts_it_on_that_cell(
        qapp, dersis_home, make_app):
    """The cell in the menu title is the cell the lesson lands on.

    Exercises ``_place_at_requested_cell`` directly — the branch
    ``_add_class_at`` consults before falling back to automatic placement —
    because driving it through ``_add_class_at`` would need a modal
    ``AddClassDialog``.
    """
    state, cls = _online_state()
    state["classes"] = []
    window = make_app()
    window.state_data = state

    state["classes"].append(cls)
    placed = window._place_at_requested_cell(cls, "tuesday", "11:00")

    assert placed is True, "the requested cell was legal but was not used"
    assert (cls["placed_day"], cls["placed_time"]) == ("tuesday", "11:00"), (
        "class landed on %r, not the requested tuesday/11:00"
        % ((cls["placed_day"], cls["placed_time"]),))


def test_add_class_at_actually_consults_the_requested_cell(
        qapp, dersis_home, make_app, monkeypatch):
    """The helper existing is not enough — ``_add_class_at`` must call it.

    Without this, deleting the call leaves every other test in this section
    green: they exercise ``_place_at_requested_cell`` directly. Measured — that
    is exactly what happened on the first writing, so the wiring is pinned
    separately from the behaviour.

    ``AddClassDialog`` is replaced rather than driven: it is modal, and what is
    under test is where the class lands afterwards.
    """
    from scheduler_app.ui import app as app_module

    state, cls = _online_state()
    state["classes"] = []
    window = make_app()
    window.state_data = state

    class _StubDialog:
        DialogCode = app_module.AddClassDialog.DialogCode

        def __init__(self, *a, **kw):
            self.result = cls

        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr(app_module, "AddClassDialog", _StubDialog)
    monkeypatch.setattr(window, "_show_toast", lambda *a, **kw: None)

    window._add_class_at("tuesday", "11:00")

    placed = [c for c in state["classes"] if c.get("placed")]
    assert placed, "_add_class_at placed nothing at all"
    assert (placed[0]["placed_day"], placed[0]["placed_time"]) == (
        "tuesday", "11:00"), (
        "_add_class_at ignored the cell it was given and placed the lesson at "
        "%r instead" % ((placed[0]["placed_day"], placed[0]["placed_time"]),))


def test_an_illegal_cell_is_declined_rather_than_forced(
        qapp, dersis_home, make_app):
    """Anti-regression: honouring the cell must not bypass the validator.

    Phase 3's rule is that every placement path reaches one verdict
    (ST-ARCH-004). ``_place_at_requested_cell`` therefore filters
    ``find_valid_options`` rather than calling ``mark_placed`` outright — it
    returns False for a cell the drag path would also refuse, and the caller
    falls back to automatic placement.
    """
    state, cls = _online_state()
    window = make_app()
    window.state_data = state

    assert window._place_at_requested_cell(cls, "friday", "09:00") is False, (
        "a day that is not on the grid was accepted")
    assert cls["placed"] is False
    assert window._place_at_requested_cell(cls, "monday", "23:00") is False, (
        "an hour that is not on the grid was accepted")
    assert cls["placed"] is False
