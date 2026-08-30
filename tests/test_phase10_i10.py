"""Item 10 / ST-UI-017 — the Open-Slots row advertises a click it cannot receive.

The defect
----------
``ui/app.py::_refresh_open_slots`` builds every free-slot row as::

    row = QWidget()
    row.setObjectName("slotRow")
    row.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    row.setStyleSheet(
        "QWidget#slotRow {"
        "  background: #FFFFFF; border-radius: 6px;"
        "  padding: 8px 10px; margin: 1px 0px; }"
        "QWidget#slotRow:hover {"
        "  background: #ECFDF5; }")

— a pointing hand and a hover highlight, on a **bare** ``QWidget``. Not a
subclass, so there is no ``mousePressEvent``; no event filter is installed;
``QWidget`` has no ``clicked`` signal for anything to connect to; and the row
carries no dynamic property recording *which* slot it is, so even a later
handler would have nothing to read. The user is shown the two universal
"this is a button" cues and nothing happens.

What is asserted, and why it survives either remedy
---------------------------------------------------
The contract, not the implementation: **a widget that dresses itself as
clickable must do something when it is clicked.** Written as

    (it does not advertise a click) OR (a click changes something)

so removing the cursor and the hover rule turns these green, and wiring the
click turns these green, and today — advertising with no handler — is the one
combination that is red.

Nothing here asserts that the cursor *must* be a pointing hand, that the row
*must* be a ``QWidget``, or that any particular method must be called. A probe
that pinned today's structure would go red on the very fix it exists to ask
for.

The control
-----------
``test_the_harness_can_see_a_press_reach_the_row`` installs an event filter on
the real row inside the real panel and confirms ``QTest.mouseClick`` delivers a
``MouseButtonPress`` to it. Without that control, "the click changed nothing"
could equally mean "the click never arrived", and the finding would be
unfalsifiable.
"""
import copy

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

from PyQt6.QtCore import QEvent, QObject, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

pytestmark = pytest.mark.ui


DAY_KEYS = ["monday", "tuesday"]
SLOTS = ["09:00", "10:00"]
ROOMS = ["R001", "R002"]


def _state():
    """A grid small enough to enumerate by hand, with one unplaced lesson.

    One unplaced class matters: it is what makes a wired click have somewhere
    to go, so "the click did nothing" cannot be blamed on an empty app.
    """
    from scheduler_app.core.models import new_state, new_class

    s = new_state()
    s["days"] = list(DAY_KEYS)
    s["slots"] = list(SLOTS)
    s["classrooms"] = list(ROOMS)
    s["classroom_capacities"] = {r: 30 for r in ROOMS}
    s["lecturers"] = ["Lect-1"]
    s["years"] = {"Year-1": ["A"]}
    c = new_class()
    c["name"] = "Fizik"
    c["class_code"] = "FZK"
    c["lecturer"] = "Lect-1"
    c["duration"] = 1
    c["targets"] = [{"year": "Year-1", "branch": "A"}]
    s["classes"].append(c)
    return s


@pytest.fixture
def win(make_app):
    """A real window whose Open-Slots panel has been built from real content.

    ``state_data`` is updated in place, never replaced: the
    ``SchedulingWorkflow`` built in ``__init__`` holds that exact dict.
    """
    w = make_app()
    w.state_data.clear()
    w.state_data.update(_state())
    w.refresh_grid()
    return w


def _rows(w):
    layout = w._open_slots_layout
    found = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        widget = item.widget() if item is not None else None
        if widget is not None and widget.objectName() == "slotRow":
            found.append(widget)
    return found


def _labels(row):
    return [c.text() for c in row.findChildren(QLabel)]


def _advertises_a_click(row):
    """The two universal "this is pressable" cues, either of which is a promise."""
    return (row.cursor().shape() == Qt.CursorShape.PointingHandCursor
            or ":hover" in row.styleSheet())


def _describe(row):
    return (
        "row=%s(objectName=%r) cursor=%s hover=%r labels=%r "
        "dynamic_properties=%r size=%r"
        % (type(row).__name__, row.objectName(), row.cursor().shape().name,
           ":hover" in row.styleSheet(), _labels(row),
           [bytes(n).decode() for n in row.dynamicPropertyNames()],
           (row.width(), row.height())))


def _snapshot(w):
    """Everything a click on a slot row could plausibly move."""
    return {
        "state": copy.deepcopy(w.state_data),
        "status": w.status_label.text(),
        "selected_class": id(w._selected_class) if w._selected_class else None,
        "selected_classes": [id(c) for c in w._selected_classes],
        "selected_empty_slot": id(w._selected_empty_slot)
        if w._selected_empty_slot is not None else None,
        "selected_cell": id(w._selected_cell) if w._selected_cell else None,
        "tab": w.notebook.currentIndex(),
        "sidebar_tab": w._sidebar_current_tab,
        "undo": len(w._undo_stack),
        "redo": len(w._redo_stack),
        "hint": w._open_slots_filter_hint.text(),
        "hint_hidden": w._open_slots_filter_hint.isHidden(),
        "n_rows": len(_rows(w)),
        "unplaced_selection": len(w.unplaced_list.selected_classes()),
    }


class _Spy(QObject):
    """Records the event types that reach the object it is filtering."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def eventFilter(self, obj, event):
        self.seen.append(event.type())
        return False


# ══════════════════════════════════════════════════════════════════════
#  Guards and controls — these must be green for the rest to mean anything
# ══════════════════════════════════════════════════════════════════════

def test_the_panel_really_builds_rows(win):
    """Everything below is vacuous if the panel drew nothing."""
    rows = _rows(win)
    assert rows, "the open-slots panel drew no slotRow widgets"
    # 2 days x 2 slots x 2 rooms, nothing placed.
    assert len(rows) == 8, f"expected 8 free-slot rows, drew {len(rows)}"
    assert _labels(rows[0]) == ["09:00", "R001"], _describe(rows[0])


def test_the_harness_can_see_a_press_reach_the_row(win, qapp):
    """Control — a click really is delivered to this widget.

    Without this, "clicking changed nothing" would be indistinguishable from
    "the click never arrived", and the finding could not be falsified.
    """
    row = _rows(win)[0]
    spy = _Spy()
    row.installEventFilter(spy)
    try:
        QTest.mouseClick(row, Qt.MouseButton.LeftButton,
                         pos=row.rect().center())
        qapp.processEvents()
    finally:
        row.removeEventFilter(spy)
    assert QEvent.Type.MouseButtonPress in spy.seen, (
        "QTest.mouseClick did not deliver a press to the row; seen=%r  %s"
        % (spy.seen, _describe(row)))
    assert QEvent.Type.MouseButtonRelease in spy.seen, spy.seen


def test_the_cursor_and_the_hover_rule_move_together(win):
    """Whatever is decided, the two cues must not disagree.

    Green today (both present), green if the affordance is dropped (both gone),
    green if the click is wired (both kept).
    """
    row = _rows(win)[0]
    hand = row.cursor().shape() == Qt.CursorShape.PointingHandCursor
    hover = ":hover" in row.styleSheet()
    assert hand == hover, (
        "half the affordance is present: pointing hand=%s, hover rule=%s  %s"
        % (hand, hover, _describe(row)))


# ══════════════════════════════════════════════════════════════════════
#  The contract
# ══════════════════════════════════════════════════════════════════════

def test_a_row_that_asks_for_a_press_must_consume_one(win):
    """ST-UI-017, at the event level — RED today.

    The press is handed straight to the widget with ``sendEvent`` and comes
    back **un-accepted**: ``QWidget``'s default ``mousePressEvent`` ignores it,
    and nothing — no override, no event filter on the row, none on the
    application — wanted it. That is the mechanism behind every assertion
    below, stated once.
    """
    row = _rows(win)[0]
    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        row.rect().center().toPointF(),
        row.mapToGlobal(row.rect().center()).toPointF(),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)
    ev.setAccepted(True)
    QApplication.sendEvent(row, ev)

    assert ev.isAccepted() or not _advertises_a_click(row), (
        "the row advertises a click and then ignores the press: it is exactly "
        "QWidget (%s), it has no clicked signal (%s), no context-menu "
        "connection (receivers=%d, policy=%s), and no dynamic property saying "
        "which slot it is. %s"
        % (type(row) is QWidget, hasattr(row, "clicked"),
           row.receivers(row.customContextMenuRequested),
           row.contextMenuPolicy().name, _describe(row)))


def test_a_row_that_looks_clickable_must_do_something_when_clicked(win, qapp):
    """ST-UI-017 — RED today. Either wire the click or drop the affordance.

    A real ``QTest.mouseClick`` on the row widget, so nothing here depends on a
    signal existing.
    """
    row = _rows(win)[0]
    before = _snapshot(win)
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=row.rect().center())
    qapp.processEvents()
    after = _snapshot(win)
    changed = {k for k in before if before[k] != after[k]}

    assert changed or not _advertises_a_click(row), (
        "the row carries a pointing hand and a QWidget#slotRow:hover rule, and "
        "a real left click on it changed nothing at all: state_data, the status "
        "bar, both selections, the notebook tab, the sidebar page, the "
        "undo/redo depths, the filter hint and the row list are all identical "
        "before and after. %s" % _describe(row))


def test_every_row_in_the_panel_keeps_the_same_promise(win, qapp):
    """The whole panel, not one row: 8 rows, 16 clicks, nothing moves."""
    rows = _rows(win)
    advertising = [r for r in rows if _advertises_a_click(r)]
    before = _snapshot(win)
    for row in rows:
        QTest.mouseClick(row, Qt.MouseButton.LeftButton,
                         pos=row.rect().center())
        QTest.mouseClick(row, Qt.MouseButton.LeftButton,
                         pos=row.rect().center())
    qapp.processEvents()
    after = _snapshot(win)
    changed = {k for k in before if before[k] != after[k]}

    assert changed or not advertising, (
        "%d of %d rows advertise a click; %d left clicks across all of them "
        "produced no observable change of any kind"
        % (len(advertising), len(rows), 2 * len(rows)))


def test_a_double_click_is_inert_too(win, qapp):
    """An empty *grid* cell responds to a double click; this row does not.

    Worth pinning separately: "make it a double-click target, like the grid"
    is one of the obvious wirings, and a fix that only handled the single
    click would leave the second-most-likely gesture dead.
    """
    row = _rows(win)[0]
    before = _snapshot(win)
    QTest.mouseDClick(row, Qt.MouseButton.LeftButton, pos=row.rect().center())
    qapp.processEvents()
    after = _snapshot(win)
    changed = {k for k in before if before[k] != after[k]}

    assert changed or not _advertises_a_click(row), (
        "a double click on an open-slots row changed nothing either. %s"
        % _describe(row))
