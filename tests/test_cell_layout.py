"""Two things drawn in a cell must not be drawn on top of each other.

Phase 5 (UI consistency). The timetable cell paints its content top-down and
then paints markers over it, and nothing reconciled the two.

The defect (found while measuring P3, unfiled before now)
---------------------------------------------------------
Phase 4 added the ÇAKIŞMA pill at the **bottom-right** of a conflicted cell,
chosen by measurement against the class code at the *top*. The protection badge
is the **last line of cell text**, so it is also at the bottom — and the pill is
painted last, over everything. Measured before the fix:

    lane_count 1  cell 150.00x93   badge y  75..87   pill y  79..90   67x8 px
    lane_count 2  cell  74.50x124  badge y 106..118  pill y 110..121  13x8 px
    lane_count 3  cell  49.33x139  badge y 121..133  pill y 125..136  13x8 px

— an overlap at **every** lane count, not an edge case.

Why it matters to a user: the marker destroyed is 📌 SABİT, on a lesson that is
both pinned and in conflict. That is precisely the ST-SCHED-002 case the product
exists to surface — DERSİS commits an infeasible pin deliberately, because the
pin is an instruction the user typed, and the badge is what tells them the clash
is theirs rather than the planner's. The app covered up its own explanation.

The fix, and why it is a reservation rather than a move
------------------------------------------------------
Moving the badge does not work: the cell is *grown to fit its content*
(``_needed_height_for_class``), so whatever ends up last is flush with the
bottom margin and collides with the pill again. The pill's strip has to be
reserved in the height calculation **and** subtracted from the text
``bottom_limit``, from one shared geometry helper — otherwise the two
calculations drift, which is how they disagreed in the first place.

A note on measuring layout in this suite
----------------------------------------
``QT_QPA_PLATFORM=offscreen`` has **no Segoe UI**: ``QFontInfo(...).family()``
is ``''`` and ``exactMatch()`` is False, so glyph advances differ from a real
desktop by roughly 2x (measured: ``'Matematik I'`` 132 px offscreen vs 61 px
native). Every assertion below is therefore a *relation between two quantities
measured the same way* — "the conflicted cell is taller than the plain one by
exactly the band", "the pill starts at or below where text stops" — never an
absolute pixel count, which would be pinning the headless font.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

from PyQt6.QtCore import QRectF  # noqa: E402

from scheduler_app.core.models import new_class  # noqa: E402
from scheduler_app.ui.renderer import (  # noqa: E402
    _conflict_pill_band, _conflict_pill_geometry, _needed_height_for_class,
    COL_DAY_W,
)

pytestmark = pytest.mark.ui

# Lane widths _filtered_block_width produces for a contested column.
LANE_WIDTHS = [
    pytest.param(COL_DAY_W, id="1-lane"),
    pytest.param(74.5, id="2-lane"),
    pytest.param(49.33, id="3-lane"),
]

CELL_MARGIN = 6  # the `m` both paint methods use


def _pinned_class():
    cls = new_class()
    cls["name"] = "Fizik I"
    cls["lecturer"] = "Lect-01"
    cls["class_code"] = "AAA111"
    cls["pinned"] = True
    cls["targets"] = [{"year": "Year-1", "branch": "A"}]
    cls["placed_classroom"] = "R001"
    return cls


@pytest.mark.parametrize("width", LANE_WIDTHS)
def test_a_conflicted_cell_reserves_room_for_its_own_pill(width, qapp):
    """The height calculation must know the pill is coming.

    A failure means the cell is sized for its text alone and the pill lands on
    whatever the last line happens to be.
    """
    cls = _pinned_class()
    plain = _needed_height_for_class(cls, width, conflict=False)
    conflicted = _needed_height_for_class(cls, width, conflict=True)
    band = _conflict_pill_band(width)

    assert band > 0, (
        "no strip is reserved at width %.2f, so the pill has nowhere of its "
        "own to go" % width)
    assert conflicted == plain + band, (
        "conflicted cell height %d != plain %d + band %.1f"
        % (conflicted, plain, band))


def _render(cls, width, conflict):
    """Paint one LessonItem into an image and return (image, rect).

    Reads what the widget actually draws rather than recomputing the geometry —
    an assertion built from the same helpers the fix uses would be ``f(x)==f(x)``
    (the first version of this test was exactly that, and it stayed green with
    the reservation deleted).
    """
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtCore import QRectF as _R
    from scheduler_app.core.models import new_state
    from scheduler_app.ui.renderer import LessonItem

    height = _needed_height_for_class(cls, width, conflict=conflict)
    rect = _R(0, 0, width, height)

    state = new_state()
    state["years"] = {"Year-1": ["A"]}
    item = LessonItem(cls, state, "#3B82F6", "#93bafa", rect, None,
                      "monday", "09:00", conflict=conflict)

    image = QImage(int(width) + 1, int(height) + 1,
                   QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    try:
        item.paint(painter, None, None)
    finally:
        painter.end()
    return image, rect


def _rows_containing(image, hex_colour, min_run=1):
    """Rows holding a horizontal run of *min_run* pixels of *hex_colour*.

    ``min_run`` matters: a conflicted cell's **border** is drawn in the same red
    as the pill, so a naive "any pixel of this colour" test marks every row and
    the assertion below becomes trivially true. (It did — the first version of
    this helper failed against the working fix for that reason.) The border is
    3 px; the pill body is tens of pixels wide, so a run threshold separates
    them without hard-coding either geometry.
    """
    from PyQt6.QtGui import QColor
    target = QColor(hex_colour).rgb() & 0x00FFFFFF
    rows = set()
    for y in range(image.height()):
        run = 0
        for x in range(image.width()):
            if (image.pixel(x, y) & 0x00FFFFFF) == target:
                run += 1
                if run >= min_run:
                    rows.add(y)
                    break
            else:
                run = 0
    return rows


@pytest.mark.parametrize("width", LANE_WIDTHS)
def test_the_pill_does_not_paint_over_the_pinned_badge(width, qapp):
    """ST-SCHED-002 / ST-UI-001 — the pin marker must survive the conflict mark.

    Renders the real item and looks for the two colours in the pixels. The pill
    fills with ``CONFLICT_BORDER``; the pinned badge is drawn in
    ``badge_formatter._PINNED_COLOR``. If the pill is painted over the badge,
    the badge's rows are inside the pill's rows — or gone entirely.
    """
    from scheduler_app.i18n.badge_formatter import _PINNED_COLOR
    from scheduler_app.ui.renderer import CONFLICT_BORDER

    cls = _pinned_class()
    image, _rect = _render(cls, width, conflict=True)

    # 10 px of solid red in one row means the pill's body, not the 3 px border.
    pill_rows = _rows_containing(image, CONFLICT_BORDER, min_run=10)
    badge_rows = _rows_containing(image, _PINNED_COLOR)

    assert pill_rows, (
        "no pill body found at width %.2f — this test would pass vacuously; "
        "check whether the pill is still drawn at this size" % width)
    assert badge_rows, (
        "the pinned badge is not drawn at all on a conflicted cell at width "
        "%.2f — the pill has covered it" % width)
    assert not (badge_rows & pill_rows), (
        "the pinned badge and the conflict pill share rows %r at width %.2f, "
        "so the pill (painted last) covers the badge"
        % (sorted(badge_rows & pill_rows), width))


def test_an_unconflicted_cell_pays_nothing_for_this(qapp):
    """Anti-regression: the reservation must not inflate every ordinary cell.

    A fix that made all cells taller would cost every user vertical space to
    solve a problem only conflicted cells have — and conflicted cells are rare
    (measured on the `large` preset: 1 of 16 room tabs).
    """
    cls = _pinned_class()
    for width, _ in ((COL_DAY_W, None), (74.5, None), (49.33, None)):
        assert (_needed_height_for_class(cls, width, conflict=False)
                == _needed_height_for_class(cls, width)), (
            "the default changed; unconflicted cells must be unaffected")


def test_the_band_and_the_painter_agree_by_construction(qapp):
    """Anti-vacuity: the two must come from one source, not two constants.

    If a future edit reintroduces a second hard-coded pill height, these tests
    would keep passing while the cell and the pill disagreed again.
    """
    for width in (COL_DAY_W, 74.5, 49.33):
        tall = QRectF(0, 0, width, 10_000)
        pill, _label, _font = _conflict_pill_geometry(tall)
        assert pill is not None
        assert _conflict_pill_band(width) == pill.height() + 3, (
            "the reserved band (%.1f) is not the pill's own height (+3 gap) at "
            "width %.2f" % (_conflict_pill_band(width), width))
