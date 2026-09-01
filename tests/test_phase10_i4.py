"""I4 / ST-UI-012 — one long class name inflates that hour's row across the
whole grid.

The defect
----------
``ui/renderer.py::_needed_height_for_class`` grows a cell to fit whatever text
it holds, with no upper bound at all. Its three consumers then take the
**maximum** over every block in a row and make that the row's height:

    scheduler_app/ui/renderer.py  _build_filtered_default
    scheduler_app/ui/renderer.py  _build_filtered_virtual_subcolumns
    scheduler_app/ui/renderer.py  build_everything ("Show Everything")

all three spelled the same way::

    needed = _needed_height_for_class(b["cls"], width, ...)
    if needed > row_heights[b["row"]]:
        row_heights[b["row"]] = needed          # <- no cap

A row is one *hour*, and it spans the entire grid — every day column in the
filtered timetable, every day x branch column in the matrix. So a single long
name in a single cell is charged to every other cell in that hour, on every
day. ``grep -n elide scheduler_app/ui/renderer.py`` returns nothing: the name
is never shortened, in the measurement or in the paint.

Measured, on this machine — the grids these tests build
-------------------------------------------------------
"Show Everything", one year, 4 branches x 5 days = **20 cells in the hour
row**, 8 hours, 160 lessons. 19 of the 20 cells in the affected row are called
"Matematik"; one carries a 200-character name.

                            real Windows platform     offscreen
    normal cell height             80 px                80 px
    inflated row height           248 px               340 px
    tallest / median              3.10x                4.25x
    blank cell height bought    3 192 px             4 940 px
    whole scene              727 -> 895 (+23%)    727 -> 987 (+36%)

The filtered timetable, same state, 5 day columns instead of 20:

    normal / inflated             79 / 223 px          70 / 357 px
    tallest / median              2.82x                5.10x
    whole scene              679 -> 823 (+21%)    607 -> 894 (+47%)

The growth is linear and unbounded, not a plateau — measured at COL_DAY_W=150
on the real platform: 100 chars -> 143 px, 200 -> 239, 400 -> 447, 800 -> 847,
1600 -> 1647, 3200 -> 3247 px. Nothing anywhere caps a class name either
(``grep -rn setMaxLength scheduler_app/`` finds one field, and it is the bug
report title), and the Excel importer copies the Classes sheet's name column
through verbatim.

The worst case is a *sequential* (non-joint) class, where the height buys
literally nothing. ``LessonItem._paint_sequential`` draws the name into a
**fixed 14 px band** (``renderer.py``), so it only ever shows one line.
Measured: the same sequential cell is charged 223 px instead of 79 px (real
platform; 357 px instead of 63 px offscreen) because of a 200-character name,
and the painted QImage at that height is **pixel-identical** to the one
produced by the first 25 characters of the name (12 offscreen). 144 px of grid
height, on every day column of that hour, for text that is never drawn.

Why the assertions are ratios and not pixel counts
--------------------------------------------------
This is a geometry item, so it was measured on the real Windows platform
(``QT_QPA_PLATFORM=windows``) as well as offscreen. Offscreen Qt has no Segoe
UI — ``QFontInfo(QFont("Segoe UI", 9)).family()`` is ``''`` — and its
fixed-pitch fallback wraps roughly twice as often, which is why the same grid
measures 248 px real and 340 px offscreen. This module must run in CI, which is
offscreen, so **every assertion here is a ratio between two quantities measured
in the same process by the same fonts**: the tallest row against the median
row, or the same row's height at two different name lengths. No absolute pixel
budget appears anywhere.

``MAX_ROW_RATIO`` is 2.5, and the window is real on both platforms. Today the
matrix measures 3.10x (real) / 4.25x (offscreen) and the filtered timetable
2.82x / 5.10x; with the fix below — a cap at twice the module's own base row
constant, ``2 * ROW_SLOT_H`` and ``2 * ROW_ESLOT_H`` — they measure 1.77x-1.88x
real and 2.00x offscreen. The narrowest margin either side of the line is the
real platform's filtered figure, 13% above it today.

``test_the_row_stops_growing_when_the_name_keeps_growing`` is the assertion
that carries the weight, because it names no constant at all: it only demands
that a 16x longer name does not buy a 12x taller row. *Any* bound satisfies it
— a max row height, a wrapped-text elide, a character cap — and nothing that
leaves the growth unbounded does.

What turns these green (built and measured, both platforms)
-----------------------------------------------------------
Three changes in ``ui/renderer.py``, +5 McCabe on a module that is **not**
ratcheted (``tests/test_ui_complexity_ratchet.py`` ceilings cover ``ui/app.py``
and ``ui/dialogs.py`` only; renderer.py measures 396 today):

1. ``MAX_CELL_H = 2 * ROW_SLOT_H`` / ``MAX_MATRIX_CELL_H = 2 * ROW_ESLOT_H``,
   and ``_needed_height_for_class`` returns ``min(total, cap)``  (+1).
2. ``_elide_to_height(text, fm, width, height, flags)`` — the longest prefix
   that wraps into the band, plus "…" — used for the two wrapped name draws,
   ``LessonItem._paint_joint`` and ``MatrixLessonItem.paint``  (+4).
3. ``_paint_sequential``'s 14 px band elides *sideways* with
   ``QFontMetrics.elidedText``, not with ``_elide_to_height``  (+0). Measured:
   ``_elide_to_height`` returns a bare "…" there, because at lineSpacing 16 not
   even one wrapped line fits in 14 px.

All six tests here pass under it on the real platform and offscreen, and so do
``tests/test_cell_layout.py``, ``tests/test_cell_contrast.py`` and
``tests/test_grid_keyboard.py`` (45 tests).

Eliding is safe here; truncating the data would not be
------------------------------------------------------
Checked before recommending it: the full name is already one hover away from
every cell. ``LessonItem.__init__`` (renderer.py) and
``MatrixLessonItem.__init__`` (renderer.py) both set a tooltip built from
``ui/cell_formatter.py::tooltip_text``, which puts ``cls["name"]`` in whole.
``test_the_full_name_is_still_reachable_from_the_cell`` pins that, so a "fix"
that bounds the row by shortening the stored name — instead of shortening what
is *painted* — turns this module red rather than green.
"""
import html
import statistics

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")

pytestmark = pytest.mark.ui


# ── The scenario ────────────────────────────────────────────────────────────

NORMAL_NAME = "Matematik"

# 200 characters. A real Turkish faculty course title, not a stress string:
# long titles like this are exactly what a university types into the Classes
# sheet, and nothing in the app shortens or rejects one.
LONG_NAME = ("Uluslararasi Iliskiler ve Karsilastirmali Siyaset Bilimi Bolumu "
             "Lisansustu Programi Kapsaminda Yurutulen Cagdas Turk Dis "
             "Politikasi ve Bolgesel Guvenlik Calismalari Semineri Uygulamali "
             "Grup Dersi 2026")

# 16x longer again. Not realistic — its job is to show the growth has no
# ceiling, which is the thing a fix has to introduce.
HUGE_NAME = " ".join([LONG_NAME] * 16)

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
SLOTS = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00"]
BRANCHES = ["A", "B", "C", "D"]

# Where the one long name goes.
LONG_ROW = 3            # the 12:00 hour
LONG_DAY = "wednesday"
LONG_BRANCH = "A"

MAX_ROW_RATIO = 2.5
"""How much taller than the median row the tallest row may be.

See the module docstring: today 3.10x / 4.25x (matrix) and 2.82x / 5.10x
(filtered); a two-row cap gives 1.77x-1.88x real, 2.00x offscreen.
"""

GROWTH_TOLERANCE = 1.25
"""How much a 16x longer name may add to the row it lands in.

Deliberately generous. A cap at any height satisfies it; only unbounded growth
does not.
"""


def _state(long_name=None, branches=BRANCHES, sequential=False):
    """A fully placed timetable: one class in every cell of every hour.

    Every class in a given hour gets its own lecturer and its own room, so the
    conflict validator finds nothing and no cell is carrying a ÇAKIŞMA pill —
    the only thing that differs between the grids compared below is the one
    class name.
    """
    from scheduler_app.core.models import new_state, new_class

    state = new_state()
    state["days"] = list(DAYS)
    state["slots"] = list(SLOTS)
    state["years"] = {"Year-1": list(branches)}
    state["lecturers"] = []
    state["classrooms"] = []

    for day_i, day in enumerate(DAYS):
        for br_i, branch in enumerate(branches):
            seat = day_i * len(branches) + br_i
            state["lecturers"].append("Lect-%02d" % seat)
            state["classrooms"].append("R%02d" % seat)

    for row, slot in enumerate(SLOTS):
        for day_i, day in enumerate(DAYS):
            for br_i, branch in enumerate(branches):
                seat = day_i * len(branches) + br_i
                cls = new_class()
                cls["class_code"] = "C%02d%02d" % (row, seat)
                cls["name"] = NORMAL_NAME
                cls["lecturer"] = "Lect-%02d" % seat
                cls["targets"] = [{"year": "Year-1", "branch": branch}]
                cls["duration"] = 1
                cls["placed"] = True
                cls["placed_day"] = day
                cls["placed_time"] = slot
                cls["placed_classroom"] = "R%02d" % seat
                if (long_name is not None and row == LONG_ROW
                        and day == LONG_DAY and branch == LONG_BRANCH):
                    cls["name"] = long_name
                    if sequential:
                        cls["joint_session"] = False
                        cls["targets"] = [
                            {"year": "Year-1", "branch": b}
                            for b in branches[:2]]
                state["classes"].append(cls)
    return state


# ── Measurement ─────────────────────────────────────────────────────────────
#
# Both readings come off the items the scene actually put on the grid, not off
# `_needed_height_for_class` — a fix is free to bound the height anywhere
# between the helper and the row, and this must see it wherever it lands.
#
# `SchedulerApp._render_everything` / `._render_grid` (ui/app.py) are
# two lines each: build a TimetableScene, call the builder below with
# `self.state_data`, hand the scene to the view. This is that call.

def _matrix_cell_heights(state):
    """Painted height of every lesson cell in the "Show Everything" matrix."""
    from scheduler_app.ui.renderer import TimetableScene

    scene = TimetableScene()
    scene.build_everything(state, None)
    return [item.rect().height() for item in scene.lesson_items]


def _filtered_cell_heights(state, branch="A"):
    """Painted height of every lesson cell in the filtered timetable."""
    from scheduler_app.ui.renderer import TimetableScene

    def in_branch(cls):
        return any(t["branch"] == branch for t in cls["targets"])

    scene = TimetableScene()
    scene.build_filtered(state, in_branch, None)
    return [item.rect().height() for item in scene.lesson_items]


def _sequential_class(name):
    """A non-joint class: ``LessonItem`` paints it through _paint_sequential."""
    from scheduler_app.core.models import new_class

    cls = new_class()
    cls["class_code"] = "C0300"
    cls["name"] = name
    cls["lecturer"] = "Lect-00"
    cls["joint_session"] = False
    cls["targets"] = [{"year": "Year-1", "branch": "A"},
                      {"year": "Year-1", "branch": "B"}]
    cls["placed_classroom"] = "R00"
    return cls


def _render_sequential(name, height):
    """Paint one sequential ``LessonItem`` of *height* into a QImage."""
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter
    from scheduler_app.core.models import new_state
    from scheduler_app.ui.renderer import COL_DAY_W, LessonItem

    state = new_state()
    state["years"] = {"Year-1": ["A", "B"]}

    item = LessonItem(_sequential_class(name), state, "#3B82F6", "#93bafa",
                      QRectF(0, 0, COL_DAY_W, height), None,
                      LONG_DAY, SLOTS[LONG_ROW])
    image = QImage(int(COL_DAY_W) + 1, int(height) + 1,
                   QImage.Format.Format_ARGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    try:
        item.paint(painter, None, None)
    finally:
        painter.end()
    return image


def _shortest_prefix_that_looks_the_same(name, height):
    """Shortest prefix of *name* that renders identically at *height*.

    Everything past it is text the user is charged for and never sees. Bisected
    rather than hard-coded because the cut-off depends on the platform's font —
    it is 25 characters with a real Segoe UI and fewer offscreen.
    """
    whole = _render_sequential(name, height)
    lo, hi = 1, len(name)
    while lo < hi:
        mid = (lo + hi) // 2
        if _render_sequential(name[:mid], height) == whole:
            hi = mid
        else:
            lo = mid + 1
    return name[:lo]


def _shape(heights):
    """(tallest, median, ratio, how many cells sit at the tallest height)."""
    tallest = max(heights)
    median = statistics.median(heights)
    at_top = sum(1 for h in heights if h == tallest)
    return tallest, median, tallest / median, at_top


def _explain(view, heights, name):
    tallest, median, ratio, at_top = _shape(heights)
    return "\n".join([
        "One long class name inflated its whole hour row.",
        "  view          : %s" % view,
        "  grid          : %d lesson cells, %d hours x %d columns"
        % (len(heights), len(SLOTS), len(heights) // len(SLOTS)),
        "  one name of   : %d characters, in the %s hour"
        % (len(name), SLOTS[LONG_ROW]),
        "  median cell   : %.1f px" % median,
        "  tallest cell  : %.1f px  (%.2fx the median, budget %.2fx)"
        % (tallest, ratio, MAX_ROW_RATIO),
        "  cells forced  : %d of %d are now %.1f px tall, and %d of them hold "
        "a %d-character name" % (at_top, len(heights), tallest,
                                 at_top - 1, len(NORMAL_NAME)),
        "  blank bought  : %.0f px of empty cell height across that row"
        % ((at_top - 1) * (tallest - median)),
        "",
        "renderer.py::_needed_height_for_class returns whatever the text asks",
        "for, and all three consumers do `row_heights[b['row']] = needed` with",
        "no upper bound, so the tallest cell in an hour sets the height of",
        "every cell in that hour, on every day column of the grid.",
    ])


# ── The defect ──────────────────────────────────────────────────────────────

def test_one_long_name_does_not_inflate_its_whole_hour_row(qapp):
    """ST-UI-012, "Show Everything": 20 cells in one hour, one long name.

    Nineteen of the twenty cells in the 12:00 row are called "Matematik" and
    need a fraction of the height they are given. The twentieth is the only
    reason the row is that tall.
    """
    heights = _matrix_cell_heights(_state(long_name=LONG_NAME))
    _, _, ratio, _ = _shape(heights)

    assert ratio <= MAX_ROW_RATIO, _explain(
        "Show Everything (build_everything)", heights, LONG_NAME)


def test_the_filtered_timetable_inflates_the_same_way(qapp):
    """The single-branch timetable a user spends the day in has it too.

    Different builder (``_build_filtered_default``), same three lines, so a fix
    that bounds one view and not the other leaves this red.
    """
    heights = _filtered_cell_heights(_state(long_name=LONG_NAME))
    _, _, ratio, _ = _shape(heights)

    assert ratio <= MAX_ROW_RATIO, _explain(
        "filtered timetable (_build_filtered_default)", heights, LONG_NAME)


def test_a_sequential_class_is_charged_for_height_it_never_paints(qapp):
    """The same inflation, on a cell that cannot show the text it paid for.

    ``LessonItem._paint_sequential`` draws the name into a **fixed 14 px band**
    (renderer.py), so a sequential class shows one line of it whatever the
    cell's height. ``_needed_height_for_class`` does not know that and measures
    the name wrapped, so the row grows anyway.

    This does not argue from the code: it renders the cell at the height the
    renderer asked for, finds the shortest prefix of the name that produces a
    **pixel-identical** image, and then asks what height that prefix would have
    been charged. Whatever the cell paints, it must not be charged for more
    than that. Measured on the real Windows platform: 223 px charged, and the
    first 25 characters of the 200-character name render identically — 79 px
    would have bought the same picture.
    """
    from scheduler_app.ui.renderer import COL_DAY_W, _needed_height_for_class

    cls = _sequential_class(LONG_NAME)
    charged = _needed_height_for_class(cls, COL_DAY_W)

    visible = _shortest_prefix_that_looks_the_same(LONG_NAME, charged)
    enough = _needed_height_for_class(_sequential_class(visible), COL_DAY_W)

    assert charged <= enough * MAX_ROW_RATIO, "\n".join([
        "A sequential cell is charged for text it never paints.",
        "  name              : %d characters" % len(LONG_NAME),
        "  charged height    : %.1f px" % charged,
        "  visibly identical : the first %d characters render the same cell,"
        " pixel for pixel" % len(visible),
        "  that prefix costs : %.1f px  (%.2fx cheaper, budget %.2fx)"
        % (enough, charged / enough, MAX_ROW_RATIO),
        "  shown             : %r" % visible,
        "",
        "_paint_sequential draws the name into QRectF(mx, my, mw, 14) — one",
        "line — so every character past the first line is invisible. It is",
        "still measured, still inflates the row, and still costs every other",
        "cell in that hour, on every day column of the grid, the same height.",
    ])


def test_the_row_stops_growing_when_the_name_keeps_growing(qapp):
    """A row height must have a ceiling; today it has none.

    This is the assertion with no constant in it. It compares the same row of
    the same grid at two name lengths and asks only that the longer one does
    not keep buying height. Any bound at all — a max row height, an elide, a
    character cap — satisfies it.
    """
    long_heights = _matrix_cell_heights(_state(long_name=LONG_NAME))
    huge_heights = _matrix_cell_heights(_state(long_name=HUGE_NAME))

    long_tallest = max(long_heights)
    huge_tallest = max(huge_heights)

    assert huge_tallest <= long_tallest * GROWTH_TOLERANCE, "\n".join([
        "The row height grows without bound with the length of one name.",
        "  %5d-character name -> tallest cell %8.1f px"
        % (len(LONG_NAME), long_tallest),
        "  %5d-character name -> tallest cell %8.1f px  (%.1fx)"
        % (len(HUGE_NAME), huge_tallest, huge_tallest / long_tallest),
        "  budget: %.2fx" % GROWTH_TOLERANCE,
        "",
        "_needed_height_for_class has no ceiling, so the grid a user scrolls",
        "is a linear function of the longest class name anyone ever typed.",
    ])


# ── Guards: green today, and they are what keep the tests above honest ──────

def test_an_all_normal_grid_has_no_tall_row(qapp):
    """The same grid, the same measurement, no long name in it.

    Without this, a broken measurement — reading the wrong items, a median of
    zero — would fail the tests above for a reason that has nothing to do with
    the defect. This one proves the failures are caused by the one long name
    and by nothing else.
    """
    for view, heights in (
            ("Show Everything", _matrix_cell_heights(_state())),
            ("filtered", _filtered_cell_heights(_state()))):
        tallest, median, ratio, _ = _shape(heights)
        assert ratio == 1.0, (
            "%s: an all-%r grid already has a row %.2fx the median (%.1f px "
            "against %.1f px), so this module's measurement is wrong, not the "
            "renderer" % (view, NORMAL_NAME, ratio, tallest, median))


def test_the_full_name_is_still_reachable_from_the_cell(qapp):
    """Both cell types carry the whole name on their tooltip.

    This is what makes eliding the painted name a safe fix and truncating the
    stored name an unsafe one. If a fix bounds the row by shortening
    ``cls["name"]`` itself, or drops the tooltip, this goes red.
    """
    from PyQt6.QtCore import QRectF
    from scheduler_app.ui.renderer import (
        COL_BRANCH_W, COL_DAY_W, LessonItem, MatrixLessonItem)

    state = _state(long_name=LONG_NAME)
    cls = [c for c in state["classes"] if c["name"] == LONG_NAME][0]
    escaped = html.escape(LONG_NAME)

    lesson = LessonItem(cls, state, "#3B82F6", "#93bafa",
                        QRectF(0, 0, COL_DAY_W, 70), None,
                        LONG_DAY, SLOTS[LONG_ROW])
    matrix = MatrixLessonItem(QRectF(0, 0, COL_BRANCH_W, 80), cls,
                              cls["placed_classroom"], "#3B82F6", "#93bafa")

    for label, item in (("LessonItem", lesson), ("MatrixLessonItem", matrix)):
        assert escaped in item.toolTip(), (
            "%s no longer carries the full %d-character class name on its "
            "tooltip, so shortening what the cell paints would destroy the "
            "only other copy the user can reach.\ntooltip was: %r"
            % (label, len(LONG_NAME), item.toolTip()))
