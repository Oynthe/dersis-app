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
