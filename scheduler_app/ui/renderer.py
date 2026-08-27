"""QGraphicsView-based timetable renderer.

Replaces the old QGridLayout/QFrame rendering with a QGraphicsScene/QGraphicsView
pipeline. The scheduling engine and business rules remain untouched — this module
is purely a rendering and interaction layer.

Classes
-------
RendererAdapter      — reads schedule state, produces layout blocks
LessonItem           — interactive lesson cell (click, drag, context menu)
EmptySlotItem        — drop-target cell for empty slots
MatrixLessonItem     — read-only lesson cell for "Show Everything" view
HeaderItem           — day / time / corner header cell
TimetableScene       — assembles items into a grid
TimetableView        — QGraphicsView with scroll and drop support
"""

from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsItem, QMenu, QApplication, QLabel,
)
from PyQt6.QtCore import Qt, QRectF, QPointF, QMimeData, QPoint
from PyQt6.QtGui import (
    QColor, QPen, QBrush, QFont, QPainter, QDrag, QPixmap, QTransform,
)

from scheduler_app.constants import (
    MIN_CELL_W, MIN_CELL_H, EMPTY_BG, HEADER_BG_DARK, TIME_BG, CORNER_BG,
    MATRIX_BORDER, MATRIX_DAY_BG, MATRIX_DAY_FG, MATRIX_BRANCH_BG,
    MATRIX_BRANCH_FG, MATRIX_SESSION_BG, MATRIX_TIME_BG, MATRIX_CELL_FG,
    MATRIX_CORNER_BG,
    CELL_FG_CODE, CELL_FG_NAME, CELL_FG_LECTURER, CELL_FG_ROOM,
    CELL_FG_BRANCH, CELL_FG_SEQUENTIAL,
)
from scheduler_app.translations import tr
from scheduler_app.logic import (
    get_placed_classes, total_duration, classroom_of,
    get_year_color, lighten_color, build_virtual_classroom_day_layout,
    assign_component_lanes, find_schedule_conflicts, conflict_partner_index,
)
from scheduler_app.models import (
    get_protection_label, effective_day, effective_time,
    is_sequential_class, slot_offset_for_target, cls_key,
)
from scheduler_app.ui.badge_formatter import get_badge, badge_text
from scheduler_app.ui.cell_formatter import tooltip_text

# ── Layout constants ─────────────────────────────────────────────────

COL_TIME_W = 85
COL_DAY_W = MIN_CELL_W       # 150
ROW_HEADER_H = 38
ROW_SLOT_H = MIN_CELL_H      # 70
GRID_GAP = 1

FILTER_MODE_DEFAULT = "default_filtered"
FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP = "virtual_classroom_overlap"

# "Show Everything" layout
COL_SESSION_W = 35
COL_ETIME_W = 65
COL_BRANCH_W = 120
ROW_DAY_HDR_H = 25
ROW_BRANCH_HDR_H = 22
ROW_ESLOT_H = 80


# ═════════════════════════════════════════════════════════════════════
#  ADAPTER — schedule state  →  layout blocks
# ═════════════════════════════════════════════════════════════════════

class RendererAdapter:
    """Pure-data adapter: reads schedule state and emits layout blocks."""

    @staticmethod
    def _filtered_entries(state, filter_fn):
        """Return normalized placed-class entries for a filtered timetable."""
        placed = get_placed_classes(state)
        days = state["days"]
        slots = state["slots"]
        entries = []

        for order, cls in enumerate(placed):
            if not filter_fn(cls):
                continue
            day = effective_day(cls)
            start = effective_time(cls)
            if day not in days or start not in slots:
                continue
            row = slots.index(start)
            span = min(total_duration(cls), len(slots) - row)
            if span <= 0:
                continue
            col = days.index(day)
            yr_name = cls["targets"][0]["year"] if cls["targets"] else ""
            base_color = get_year_color(state, yr_name)
            bg_color = lighten_color(base_color, 0.45)
            entries.append({
                "cls": cls,
                "col": col,
                "row": row,
                "end_row": row + span,
                "span": span,
                "base_color": base_color,
                "bg_color": bg_color,
                "day": day,
                "slot": start,
                "order": order,
                "lane": 0,
                "lane_count": 1,
            })
        return entries

    @staticmethod
    def _default_filtered_blocks(state, filter_fn):
        """Yield layout blocks for the legacy single-column filtered timetable.

        ST-UI-001. This used to write every entry into an
        ``occupied[(row, col)]`` dict keyed by cell, then keep only the entries
        still marked ``"start"``. A second lesson claiming a cell therefore
        **overwrote** the first and produced no block at all — and because it is
        still ``placed``/``pinned`` it is absent from the unplaced panel too, so
        it became unreachable from the entire UI: no item to click, select,
        edit, unplace or drag.

        Worse, ``"start"`` and ``"span"`` overwrote each other, so a long lesson
        against one starting underneath it either *overdrew* (both blocks
        emitted, painted on top of one another) or dropped one, depending purely
        on the order of ``state["classes"]``. That is the shape of the one real
        collision the audit's ``large`` preset produces.

        Every entry now becomes a block. Contested runs are split into lanes
        *inside the same column*, so the grid geometry, ``cell_at``,
        ``cell_rect``, the drop highlight and the empty-slot loop are all
        untouched, and an uncontested lesson keeps the full column width.
        """
        entries = RendererAdapter._filtered_entries(state, filter_fn)

        by_col = {}
        for entry in entries:
            by_col.setdefault(entry["col"], []).append(entry)

        blocks = []
        for col_entries in by_col.values():
            assign_component_lanes(col_entries)
            blocks.extend(col_entries)

        occ_set = set()
        for b in blocks:
            for d in range(b["span"]):
                occ_set.add((b["row"] + d, b["col"]))
        blocks.sort(key=lambda b: (b["col"], b["row"], b["lane"], b["order"]))
        return blocks, occ_set

    @staticmethod
    def _virtual_overlap_filtered_blocks(state, filter_fn):
        """Yield filtered blocks for the virtual classroom day-subcolumn layout."""
        layout = build_virtual_classroom_day_layout(state, filter_fn)
        return layout["blocks"], layout["occupied_subcolumns"]

    @staticmethod
    def _stamp_conflicts(blocks, conflict_partners, label_index=None):
        """Mark blocks whose class cannot coexist with something, anywhere.

        ST-UI-001. Deliberately **not** geometric, because splitting a cell and
        labelling a conflict are different questions with different answers:

        * a cell may hold two blocks and be perfectly legal — two online lessons
          share an hour without contending for anything;
        * a real double-booking may show only one block in the current view —
          one student group in two different rooms puts one lesson on each
          room's tab.

        So the split answers "how many lessons are in this cell" and this
        answers "can these lessons coexist". A geometric label would raise a
        false alarm on the first case and stay silent on the second.
        """
        label_index = label_index or {}
        for b in blocks:
            partners = conflict_partners.get(cls_key(b["cls"]), ())
            b["conflict"] = bool(partners)
            b["conflict_partners"] = tuple(partners)
            # Resolved here, once per block, from an index built once per
            # sweep. Resolving names inside the item constructor instead is
            # O(len(state["classes"])) per conflicted item -- on the
            # pathological preset ~1200 items x 1200 classes per scene
            # rebuild, and cls_key() *mutates* a class dict that has no uid,
            # so it is not even a read-only scan. That is the ST-PERF-003
            # mistake in a new place.
            b["conflict_labels"] = tuple(
                label_index[k] for k in partners if k in label_index)

    @staticmethod
    def filtered_layout(state, filter_fn, mode=FILTER_MODE_DEFAULT,
                        conflict_partners=None):
        """Return filtered blocks plus any mode-specific geometry metadata.

        *conflict_partners* is a :func:`conflict_partner_index` mapping. Pass it
        in when one refresh feeds several views; omit it and it is computed
        here, so the adapter stays usable on its own (the sweep costs ~1.5 ms on
        a fully-placed 250-class grid, against the 306-563 ms repaint
        ST-UI-009 was about, so it needs no memoising).
        """
        if conflict_partners is None:
            conflict_partners = conflict_partner_index(
                find_schedule_conflicts(state))
        label_index = (build_class_label_index(state)
                       if conflict_partners else {})
        if mode == FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP:
            layout = build_virtual_classroom_day_layout(state, filter_fn)
            result = {
                "blocks": layout["blocks"],
                "occ": layout["occupied_subcolumns"],
                "day_groups": layout["day_groups"],
                "total_subcolumns": layout["total_subcolumns"],
                "virtual_day_subcolumns": True,
            }
        else:
            blocks, occ = RendererAdapter._default_filtered_blocks(
                state, filter_fn)
            result = {
                "blocks": blocks,
                "occ": occ,
                "day_groups": [],
                "total_subcolumns": len(state.get("days", [])),
                "virtual_day_subcolumns": False,
            }
        RendererAdapter._stamp_conflicts(
            result["blocks"], conflict_partners, label_index)
        return result

    @staticmethod
    def filtered_blocks(state, filter_fn, mode=FILTER_MODE_DEFAULT,
                        conflict_partners=None):
        """Yield layout blocks for a filtered (single-grid) timetable."""
        layout = RendererAdapter.filtered_layout(
            state, filter_fn, mode=mode, conflict_partners=conflict_partners)
        return layout["blocks"], layout["occ"]

    @staticmethod
    def everything_blocks(state, year, conflict_partners=None):
        """Yield layout blocks for the everything-matrix of *year*.

        ST-UI-001: this had the same cell-keyed ``occupied`` dict as the
        filtered view, and dropped a colliding lesson the same way. Entries are
        now laned per column, so every lesson that belongs on this matrix gets
        a block.
        """
        placed = get_placed_classes(state)
        days = state["days"]
        slots = state["slots"]
        branches = state["years"].get(year, [])
        if not branches:
            return []
        n_branches = len(branches)

        entries = []
        order = 0
        for c in placed:
            c_day = effective_day(c)
            c_start = effective_time(c)
            if c_day not in days or c_start not in slots:
                continue
            d_idx = days.index(c_day)
            start_si = slots.index(c_start)
            dur = c["duration"]

            # Deliberately still `targets.index(t)`, not `enumerate`. `.index`
            # compares dicts by ==, so a non-joint class carrying two identical
            # target dicts resolves both to 0 and draws both sub-blocks at the
            # same offset. `enumerate` would move the second — but the same
            # `.index(t)` line lives in the PDF everything table
            # (exporter.py) and the XLSX everything matrix (app.py), so
            # changing it here alone would make the screen disagree with both
            # exports for that class. Three surfaces, three answers is the
            # failure this whole task is about. Fix all three together or none.
            for t in c["targets"]:
                if t["year"] != year or t["branch"] not in branches:
                    continue
                t_idx = c["targets"].index(t)
                b_idx = branches.index(t["branch"])
                actual_start = start_si + slot_offset_for_target(c, t_idx)
                if actual_start >= len(slots):
                    continue
                span = min(dur, len(slots) - actual_start)
                if span <= 0:
                    continue
                entries.append({
                    "cls": c,
                    "col": d_idx * n_branches + b_idx,
                    "row": actual_start,
                    "end_row": actual_start + span,
                    "span": span,
                    "order": order,
                    "lane": 0,
                    "lane_count": 1,
                })
                order += 1

        by_col = {}
        for e in entries:
            by_col.setdefault(e["col"], []).append(e)

        yr_color = get_year_color(state, year)
        bg = lighten_color(yr_color, 0.6)
        blocks = []
        for col_entries in by_col.values():
            assign_component_lanes(col_entries)
            for e in col_entries:
                e["base_color"] = yr_color
                e["bg_color"] = bg
                e["room"] = classroom_of(e["cls"])
                blocks.append(e)
        blocks.sort(key=lambda b: (b["col"], b["row"], b["lane"], b["order"]))

        if conflict_partners is None:
            conflict_partners = conflict_partner_index(
                find_schedule_conflicts(state))
        RendererAdapter._stamp_conflicts(
            blocks, conflict_partners,
            build_class_label_index(state) if conflict_partners else {})
        return blocks


# ── Adaptive height helpers ──────────────────────────────────────────

def _measure_text_height(text, font, width, wrap=True):
    """Return the pixel height needed to render *text* in *font* within *width*."""
    from PyQt6.QtGui import QFontMetrics
    fm = QFontMetrics(font)
    if wrap:
        flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap)
    else:
        flags = int(Qt.AlignmentFlag.AlignHCenter)
    br = fm.boundingRect(0, 0, int(width), 9999, flags, text)
    return br.height()


def _needed_height_for_class(cls, cell_w, is_matrix=False, conflict=False):
    """Compute the minimum pixel height to display *cls* content.

    *cell_w* is the available cell width (before internal margin/padding).

    *conflict* reserves the strip the ÇAKIŞMA pill will occupy, so the pill gets
    its own space instead of being painted over the protection badge — see
    :func:`_conflict_pill_band`.
    """
    m = 4 if is_matrix else 6
    pad = 3 if is_matrix else 4
    inner_w = cell_w - 2 * m - 2 * pad
    if inner_w < 20:
        inner_w = 20

    total = m  # top margin

    code = cls.get("class_code", "")
    if code:
        f = QFont("Segoe UI", 7 if is_matrix else 8)
        f.setBold(True)
        total += _measure_text_height(code, f, inner_w, wrap=False) + 1

    # name
    f_name = QFont("Segoe UI", 8 if is_matrix else 9)
    f_name.setBold(True)
    total += _measure_text_height(cls["name"], f_name, inner_w, wrap=True) + 2

    # lecturer
    f_lec = QFont("Segoe UI", 8)
    f_lec.setBold(False)
    total += _measure_text_height(cls["lecturer"], f_lec, inner_w, wrap=True) + 2

    # room
    room = classroom_of(cls)
    if room:
        f_room = QFont("Segoe UI", 7 if is_matrix else 8)
        total += _measure_text_height(room, f_room, inner_w, wrap=False) + 1

    # protection / pinned badge — measure actual label text
    bt = badge_text(cls)
    if bt:
        f_badge = QFont("Segoe UI", 7)
        f_badge.setBold(True)
        total += _measure_text_height(bt, f_badge, inner_w, wrap=False) + 2

    if conflict:
        total += _conflict_pill_band(cell_w)

    total += m  # bottom margin
    return total


def _filtered_block_width(block, mode):
    """Return the width used to render a filtered timetable block.

    ST-UI-001: a contested run is split into ``lane_count`` lanes inside the
    same column, so the lanes plus the gaps between them still add up to
    ``COL_DAY_W`` and the column geometry is unchanged.
    """
    n = max(1, block.get("lane_count", 1))
    if n == 1:
        return COL_DAY_W
    return (COL_DAY_W - (n - 1) * GRID_GAP) / n


def _paint_selection_ring(painter, rect):
    """A black ring just inside the border, for a lesson whose border is red.

    ST-UI-001. Selection used to be signalled by widening the border by 1 px in
    the same colour. On a conflicted lesson that colour is already red and
    already 3 px, so selecting one was very nearly invisible -- the two states
    competed for a single channel. The conflict border now has a constant width
    and selection draws its own ring.
    """
    painter.setPen(QPen(QColor("#000000"), 1, Qt.PenStyle.DashLine))
    painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
    painter.drawRoundedRect(rect.adjusted(5, 5, -5, -5), 4, 4)


CONFLICT_BORDER = "#DC2626"


def class_display_label(cls):
    """``[CODE] Name``, or just the name when there is no code."""
    code = cls.get("class_code", "")
    return f"[{code}] {cls['name']}" if code else cls["name"]


def build_class_label_index(state):
    """``{cls_key: "[CODE] Name"}`` for every class, built once per sweep."""
    return {cls_key(c): class_display_label(c)
            for c in state.get("classes", [])}


def _conflict_tooltip(base_tip, conflict, partner_labels):
    """Append the conflict partners to *base_tip* when there are any.

    The partner names are the point: "this lesson clashes" is not actionable,
    "this lesson clashes with Fizik I" is. *partner_labels* is resolved once
    per sweep by RendererAdapter._stamp_conflicts, never per item.
    """
    if not conflict or not partner_labels:
        return base_tip
    nl = "\n"
    bullets = nl.join("  • " + name for name in partner_labels)
    return (base_tip + nl + nl + tr("conflicts.tooltip_header")
            + nl + bullets)


def _conflict_pill_geometry(rect):
    """Where the ÇAKIŞMA pill goes in *rect*, as ``(QRectF | None, label, font)``.

    Split out from the painter so the *height* calculation and the *paint* agree
    by construction. They used not to, and the badge paid for it: see
    :func:`_conflict_pill_band`.

    Bottom-right, and that is the whole point. Both paint methods draw the class
    code first, centred, at ``rect.y() + 6`` — and at ``COL_DAY_W`` 150 a full
    pill spans roughly x 86..146 against a centred five-character code at
    x 57..92, so a top-right pill overlaps it; at ``lane_count`` 2 the lane is
    74 px and the pill covers the code completely. The class code is the
    identifier the warning log, the exports and every test key on, and it would
    be destroyed on exactly the cells that matter most.

    A pill wider than its lane also bleeds onto the neighbouring lesson, so the
    user reads the label on the wrong one — hence measure, shorten, then give up
    and let the red border carry the signal alone. (The bleed is the graphics
    *item* overflowing its own rect, not ``QPainter.drawText`` failing to clip;
    ``drawText`` does clip. The earlier note here blamed the wrong mechanism,
    which matters because it is what the next reader reasons from.)
    """
    from PyQt6.QtGui import QFontMetrics

    font = QFont("Segoe UI", 7)
    font.setBold(True)
    fm = QFontMetrics(font)
    label = tr("badges.conflict")
    pill_w = fm.horizontalAdvance(label) + 10
    if rect.width() < pill_w + 8:
        label = tr("badges.conflict_short")
        pill_w = fm.horizontalAdvance(label) + 10
    if rect.width() < pill_w + 2:
        # nothing legible fits; the red border still carries the signal
        return None, label, font
    pill_h = fm.height() + 2
    if rect.height() < pill_h + 8:
        return None, label, font
    return (QRectF(rect.right() - pill_w - 4,
                   rect.bottom() - pill_h - 3, pill_w, pill_h),
            label, font)


def _conflict_pill_band(cell_w):
    """Vertical strip at the bottom of a conflicted cell the pill will occupy.

    ST-UI-005 / ST-UI-001. The pill is drawn *last*, over everything, and the
    protection badge is the last line of cell text — so on a lesson that is both
    conflicted and pinned they landed on top of each other. Measured before this
    fix, badge rect against pill rect:

        lane_count 1  cell 150.00x93   badge y 75..87   pill y 79..90   67x8 px
        lane_count 2  cell  74.50x124  badge y 106..118 pill y 110..121 13x8 px
        lane_count 3  cell  49.33x139  badge y 121..133 pill y 125..136 13x8 px

    The pin marker was destroyed on exactly the cells where it explains the most
    — an infeasible pin is committed deliberately (ST-SCHED-002) and the badge is
    what tells the user the clash is theirs rather than the planner's.

    Reserving the strip in both the height calculation and the paint keeps the
    badge; it is not enough to move the badge, because the cell is grown to fit
    its content and the pill would then overlap whatever became last.
    """
    probe = QRectF(0, 0, cell_w, 10_000)
    pill, _label, _font = _conflict_pill_geometry(probe)
    return 0.0 if pill is None else pill.height() + 3


def _paint_conflict_pill(painter, rect):
    """Draw the ÇAKIŞMA pill in the bottom-right of *rect*."""
    pill, label, font = _conflict_pill_geometry(rect)
    if pill is None:
        return
    painter.setPen(QPen(Qt.PenStyle.NoPen))
    painter.setBrush(QBrush(QColor(CONFLICT_BORDER)))
    painter.drawRoundedRect(pill, 5, 5)
    painter.setFont(font)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)


# ═════════════════════════════════════════════════════════════════════
#  GRAPHICS ITEMS
# ═════════════════════════════════════════════════════════════════════

class HeaderItem(QGraphicsRectItem):
    """Non-interactive header cell (day, time, corner)."""

    def __init__(self, rect, text, bg_color, fg_color,
                 font_size=10, bold=True):
        super().__init__(rect)
        self._text = text
        self._bg = QColor(bg_color)
        self._fg = QColor(fg_color)
        self._font_size = font_size
        self._bold = bold
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(self._bg))

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = self.rect()
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(QBrush(self._bg))
        painter.drawRoundedRect(rect, 4, 4)
        font = QFont("Segoe UI", self._font_size)
        font.setBold(self._bold)
        painter.setFont(font)
        painter.setPen(self._fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


class LessonItem(QGraphicsRectItem):
    """Interactive lesson block in the filtered timetable."""

    def __init__(self, cls, state, base_color, bg_color, rect,
                 app, day, slot, conflict=False, conflict_partners=(),
                 conflict_labels=()):
        super().__init__(rect)
        self.cls = cls
        self.state = state
        self._base_color = base_color
        self._bg_color = bg_color
        self.app = app
        self.day = day
        self.slot = slot
        self._drag_start = None
        self._selected = False

        self._is_sequential = is_sequential_class(cls)

        self._ghost = False
        # ST-UI-001: set when this lesson cannot coexist with another one
        # somewhere on the timetable — a validator verdict, not "there are two
        # blocks in this cell".
        self._conflict = bool(conflict)
        self._conflict_partners = tuple(conflict_partners)
        self._conflict_labels = tuple(conflict_labels)

        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)

        # Tooltip with class details
        self.setToolTip(_conflict_tooltip(
            tooltip_text(cls), self._conflict, self._conflict_labels))

    def set_ghost(self, enabled):
        """Toggle ghost (semi-transparent) mode during drag."""
        self._ghost = enabled
        self.setOpacity(0.3 if enabled else 1.0)
        self.update()

    # ── painting ─────────────────────────────────────────────────

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self._is_sequential:
            self._paint_sequential(painter)
        else:
            self._paint_joint(painter)
        # ST-UI-001: last, so it is never painted over by the cell's own text.
        if self._conflict:
            _paint_conflict_pill(painter, self.rect())
            if self._selected:
                _paint_selection_ring(painter, self.rect())

    def _paint_joint(self, painter):
        rect = self.rect()
        if self._conflict:
            # ST-UI-001: a conflicted lesson is red whichever tab it is on,
            # including tabs where the other half of the clash is not
            # visible. Width is CONSTANT: selection gets its own black ring
            # below, because a 1 px width change in the same red was the
            # only thing distinguishing a selected conflicted lesson from an
            # unselected one -- two signals competing for one channel.
            bw = 3
            bc = QColor(CONFLICT_BORDER)
        else:
            bw = 3 if self._selected else 2
            bc = QColor("#000000") if self._selected else QColor(self._base_color)
        painter.setPen(QPen(bc, bw))
        painter.setBrush(QBrush(QColor(self._bg_color)))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

        cls = self.cls
        m = 6
        pad = 4
        x, w = rect.x() + m + pad, rect.width() - 2 * m - 2 * pad
        y = rect.y() + m
        bottom_limit = rect.bottom() - m
        if self._conflict:
            # Keep the cell's own text out of the ÇAKIŞMA pill's strip; the pill
            # is painted last and would otherwise cover the protection badge.
            bottom_limit -= _conflict_pill_band(rect.width())
        center = Qt.AlignmentFlag.AlignHCenter
        wrap_center = center | Qt.TextFlag.TextWordWrap
        fm = painter.fontMetrics()

        # class_code (if present)
        code = cls.get("class_code", "")
        if code:
            f = QFont("Segoe UI", 8); f.setBold(True)
            painter.setFont(f); painter.setPen(QColor(CELL_FG_CODE))
            painter.drawText(QRectF(x, y, w, 14), center, code)
            y += 14

        # name (wrapped, centered)
        f = QFont("Segoe UI", 9); f.setBold(True)
        painter.setFont(f); painter.setPen(QColor(CELL_FG_NAME))
        name_rect = QRectF(x, y, w, bottom_limit - y)
        br = painter.fontMetrics().boundingRect(
            int(x), int(y), int(w), int(bottom_limit - y),
            int(wrap_center), cls["name"])
        painter.drawText(name_rect, wrap_center, cls["name"])
        y += br.height() + 2

        # lecturer
        if y < bottom_limit:
            f.setBold(False); f.setPointSize(8); painter.setFont(f)
            painter.setPen(QColor(CELL_FG_LECTURER))
            lec_rect = QRectF(x, y, w, bottom_limit - y)
            br = painter.fontMetrics().boundingRect(
                int(x), int(y), int(w), int(bottom_limit - y),
                int(wrap_center), cls["lecturer"])
            painter.drawText(lec_rect, wrap_center, cls["lecturer"])
            y += br.height() + 2

        # room / location
        room = classroom_of(cls)
        if room and y < bottom_limit:
            painter.setPen(QColor(CELL_FG_ROOM))
            painter.drawText(QRectF(x, y, w, 13), center, room)
            y += 13

        # pinned / protection badge
        badge = self._protection_badge()
        if badge and y < bottom_limit:
            f.setPointSize(7); f.setBold(True); painter.setFont(f)
            painter.setPen(QColor(badge[1]))
            painter.drawText(QRectF(x, y, w, 12), center, badge[0])

    def _protection_badge(self):
        """Return (text, color) for the protection/pinned badge, or None."""
        emoji, label, color = get_badge(self.cls)
        if emoji:
            return (f"{emoji} {label}" if label else emoji, color)
        return None

    def _paint_sequential(self, painter):
        rect = self.rect()
        cls = self.cls
        n = len(cls["targets"])
        if self._conflict:
            # ST-UI-001: a conflicted lesson is red whichever tab it is on,
            # including tabs where the other half of the clash is not
            # visible. Width is CONSTANT: selection gets its own black ring
            # below, because a 1 px width change in the same red was the
            # only thing distinguishing a selected conflicted lesson from an
            # unselected one -- two signals competing for one channel.
            bw = 3
            bc = QColor(CONFLICT_BORDER)
        else:
            bw = 3 if self._selected else 2
            bc = QColor("#000000") if self._selected else QColor(self._base_color)

        # outer border + base fill
        painter.setPen(QPen(bc, bw))
        painter.setBrush(QBrush(QColor(self._base_color)))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

        inner = rect.adjusted(bw, bw, -bw, -bw)
        sec_h = inner.height() / n

        for i, t in enumerate(cls["targets"]):
            t_bg = lighten_color(get_year_color(self.state, t["year"]), 0.50)
            sy = inner.y() + i * sec_h
            sh = sec_h if i < n - 1 else inner.bottom() - sy
            sr = QRectF(inner.x(), sy, inner.width(), sh)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(t_bg)))
            painter.drawRect(sr)

            if i > 0:
                painter.setPen(QPen(QColor(self._base_color), 1))
                painter.drawLine(QPointF(inner.x(), sy),
                                 QPointF(inner.right(), sy))

            mx, my, mw = sr.x() + 5, sr.y() + 3, sr.width() - 10
            center = Qt.AlignmentFlag.AlignHCenter

            # class_code
            code = cls.get("class_code", "")
            if code:
                f = QFont("Segoe UI", 7); f.setBold(True)
                painter.setFont(f); painter.setPen(QColor(CELL_FG_CODE))
                painter.drawText(QRectF(mx, my, mw, 11), center, code)
                my += 11

            f = QFont("Segoe UI", 7); f.setBold(True)
            painter.setFont(f); painter.setPen(QColor(CELL_FG_BRANCH))
            painter.drawText(QRectF(mx, my, mw, 11), center, t["branch"])
            my += 11

            f.setPointSize(9)
            painter.setFont(f); painter.setPen(QColor(CELL_FG_NAME))
            painter.drawText(QRectF(mx, my, mw, 14),
                             center | Qt.TextFlag.TextWordWrap, cls["name"])
            my += 14

            f.setPointSize(8); f.setBold(False)
            painter.setFont(f); painter.setPen(QColor(CELL_FG_LECTURER))
            painter.drawText(QRectF(mx, my, mw, 12), center, cls["lecturer"])
            my += 14

            if i == n - 1:
                badge = self._protection_badge()
                if badge:
                    f.setPointSize(7); f.setBold(True); painter.setFont(f)
                    painter.setPen(QColor(badge[1]))
                    painter.drawText(QRectF(mx, my, mw, 11),
                                     Qt.AlignmentFlag.AlignLeft, badge[0])
                    my += 11

            if i == n - 1:
                f.setPointSize(7); f.setBold(True); painter.setFont(f)
                painter.setPen(QColor(CELL_FG_SEQUENTIAL))
                painter.drawText(QRectF(mx, my, mw, 11),
                                 Qt.AlignmentFlag.AlignLeft, tr("badges.sequential"))

    # ── selection helper ─────────────────────────────────────────

    def mark_selected(self, selected):
        self._selected = selected
        self.update()

    # ── interaction ──────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            modifiers = event.modifiers()
            keep_multi = (
                self._selected
                and len(getattr(self.app, "_selected_cells", [])) > 1
                and not (modifiers & Qt.KeyboardModifier.ControlModifier)
                and not (modifiers & Qt.KeyboardModifier.ShiftModifier)
            )
            if not keep_multi:
                self.app._select_class_gfx(self.cls, self, modifiers)
            from scheduler_app.models import is_immovable
            if not is_immovable(self.cls):
                self._drag_start = event.pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.app._edit_class(self.cls)

    def mouseMoveEvent(self, event):
        from scheduler_app.models import is_immovable
        if (self._drag_start is not None
                and not is_immovable(self.cls)
                and (event.pos() - self._drag_start).manhattanLength() > 10):
            self.app._start_drag_gfx(self.cls, self)
            self._drag_start = None
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        event.accept()

    def contextMenuEvent(self, event):
        from scheduler_app.models import is_immovable, PROTECTION_LEVELS
        menu = QMenu()
        _ctx_code = self.cls.get("class_code", "")
        _ctx_name = f"[{_ctx_code}] {self.cls['name']}" if _ctx_code else self.cls["name"]
        title = menu.addAction("\U0001F4D6  " + _ctx_name)
        title.setEnabled(False)
        menu.addSeparator()
        edit_menu = menu.addMenu("\u270E  " + tr("menus.edit"))
        edit_class_act = edit_menu.addAction(tr("dialogs.edit_class.title"))
        edit_class_act.triggered.connect(lambda: self.app._edit_class(self.cls))
        edit_lecturer_act = edit_menu.addAction(f"{tr('menus.edit')} {tr('labels.lecturer')}")
        edit_lecturer_act.triggered.connect(
            lambda: self.app._edit_lecturer_from_class(self.cls))
        if not is_immovable(self.cls) and self.cls["placed"]:
            unplace_act = menu.addAction("\u25B2  " + tr("buttons.unplace"))
            unplace_act.triggered.connect(
                lambda: self.app._unplace_specific(self.cls))
        if not is_immovable(self.cls):
            move_act = menu.addAction("\u2194  " + tr("tooltips.drag_to_move"))
            move_act.setEnabled(False)
        # Protection submenu (only for non-pinned placed classes)
        if not self.cls["pinned"] and self.cls["placed"]:
            prot_menu = menu.addMenu("\U0001F6E1  " + tr("protection.title"))
            current = self.cls.get("protection", "none")
            for level in PROTECTION_LEVELS:
                label = get_protection_label(level)
                act = prot_menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(level == current)
                act.triggered.connect(
                    lambda checked, lv=level: self.app._set_protection(
                        self.cls, lv))
        menu.addSeparator()
        remove_act = menu.addAction("\u2715  " + tr("buttons.remove"))
        remove_act.triggered.connect(
            lambda: self.app._remove_specific(self.cls))
        menu.exec(event.screenPos())


class EmptySlotItem(QGraphicsRectItem):
    """Empty timetable cell (double-click to add class)."""

    _IDLE = QColor(EMPTY_BG)

    def __init__(self, day, slot, rect, app):
        super().__init__(rect)
        self.day = day
        self.slot = slot
        self.app = app
        self._selected = False
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(self._IDLE))
        self.setToolTip(tr("tooltips.double_click_add"))

    def mark_selected(self, selected):
        self._selected = selected
        self.update()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._selected:
            painter.setPen(QPen(QColor("#1D4ED8"), 2))
        else:
            painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(QBrush(self._IDLE))
        painter.drawRoundedRect(self.rect(), 4, 4)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.app:
            self.app._select_empty_slot(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.app._add_class_at(self.day, self.slot)

    def contextMenuEvent(self, event):
        if self.app:
            self.app._show_empty_slot_context_menu(
                self.day, self.slot, event.screenPos())
            event.accept()
            return
        super().contextMenuEvent(event)


class MatrixLessonItem(QGraphicsRectItem):
    """Interactive lesson cell for the 'Show Everything' matrix view."""

    def __init__(self, rect, cls, room, border_color, bg_color, app=None,
                 conflict=False, conflict_partners=(), conflict_labels=()):
        super().__init__(rect)
        self.cls = cls
        self.app = app
        self._room = room
        self._bc = QColor(border_color)
        self._bg = QColor(bg_color)
        self._selected = False
        self._drag_start = None
        self._ghost = False
        self._conflict = bool(conflict)          # ST-UI-001
        self._conflict_partners = tuple(conflict_partners)
        self._conflict_labels = tuple(conflict_labels)
        self.setPen(QPen(self._bc, 2))
        self.setBrush(QBrush(self._bg))
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Tooltip
        self.setToolTip(_conflict_tooltip(
            tooltip_text(cls, include_groups=False, include_duration=False),
            self._conflict, self._conflict_labels))

    def mark_selected(self, selected):
        self._selected = selected
        self.update()

    def set_ghost(self, enabled):
        self._ghost = enabled
        self.setOpacity(0.3 if enabled else 1.0)
        self.update()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = self.rect()
        if self._conflict:
            bw = 3                                # ST-UI-001, constant
            bc = QColor(CONFLICT_BORDER)
        else:
            bw = 3 if self._selected else 2
            bc = QColor("#000000") if self._selected else self._bc
        painter.setPen(QPen(bc, bw))
        painter.setBrush(QBrush(self._bg))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

        cls = self.cls
        m = 4
        pad = 3
        x, w = rect.x() + m + pad, rect.width() - 2 * m - 2 * pad
        y = rect.y() + m
        bottom_limit = rect.bottom() - m
        if self._conflict:
            bottom_limit -= _conflict_pill_band(rect.width())
        center = Qt.AlignmentFlag.AlignHCenter
        wrap_center = center | Qt.TextFlag.TextWordWrap

        # class_code (if present)
        code = cls.get("class_code", "")
        if code and y < bottom_limit:
            f = QFont("Segoe UI", 7); f.setBold(True)
            painter.setFont(f); painter.setPen(QColor(CELL_FG_CODE))
            painter.drawText(QRectF(x, y, w, 12), center, code)
            y += 12

        # name (wrapped, centered)
        f = QFont("Segoe UI", 8); f.setBold(True)
        painter.setFont(f); painter.setPen(QColor(CELL_FG_NAME))
        name_avail = QRectF(x, y, w, bottom_limit - y)
        br = painter.fontMetrics().boundingRect(
            int(x), int(y), int(w), int(bottom_limit - y),
            int(wrap_center), cls["name"])
        painter.drawText(name_avail, wrap_center, cls["name"])
        y += br.height() + 1

        # lecturer
        if y < bottom_limit:
            f.setBold(False); painter.setFont(f)
            painter.setPen(QColor(CELL_FG_LECTURER))
            lec_avail = QRectF(x, y, w, bottom_limit - y)
            br = painter.fontMetrics().boundingRect(
                int(x), int(y), int(w), int(bottom_limit - y),
                int(wrap_center), cls["lecturer"])
            painter.drawText(lec_avail, wrap_center, cls["lecturer"])
            y += br.height() + 1

        # room
        if self._room and y < bottom_limit:
            painter.setPen(QColor(CELL_FG_ROOM))
            painter.drawText(QRectF(x, y, w, 13), center, self._room)
            y += 13

        # Protection / pinned badge
        if y < bottom_limit:
            emoji, label, color = get_badge(cls)
            if emoji:
                f.setPointSize(7); f.setBold(True); painter.setFont(f)
                painter.setPen(QColor(color))
                painter.drawText(QRectF(x, y, w, 11), center,
                                 f"{emoji} {label}" if label else emoji)

        # ST-UI-001: last, so the cell's own text never paints over it.
        if self._conflict:
            _paint_conflict_pill(painter, rect)
            if self._selected:
                _paint_selection_ring(painter, rect)

    # ── interaction ──────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.app:
            modifiers = event.modifiers()
            keep_multi = (
                self._selected
                and len(getattr(self.app, "_selected_cells", [])) > 1
                and not (modifiers & Qt.KeyboardModifier.ControlModifier)
                and not (modifiers & Qt.KeyboardModifier.ShiftModifier)
            )
            if not keep_multi:
                self.app._select_class_gfx(self.cls, self, modifiers)
            from scheduler_app.models import is_immovable
            if not is_immovable(self.cls):
                self._drag_start = event.pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.app:
            self.app._edit_class(self.cls)

    def mouseMoveEvent(self, event):
        from scheduler_app.models import is_immovable
        if (self._drag_start is not None
                and self.app
                and not is_immovable(self.cls)
                and (event.pos() - self._drag_start).manhattanLength() > 10):
            self.app._start_drag_gfx(self.cls, self)
            self._drag_start = None
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        event.accept()

    def contextMenuEvent(self, event):
        if not self.app:
            return
        from scheduler_app.models import is_immovable, PROTECTION_LEVELS
        menu = QMenu()
        _ctx_code = self.cls.get("class_code", "")
        _ctx_name = f"[{_ctx_code}] {self.cls['name']}" if _ctx_code else self.cls["name"]
        title = menu.addAction("\U0001F4D6  " + _ctx_name)
        title.setEnabled(False)
        menu.addSeparator()
        edit_menu = menu.addMenu("\u270E  " + tr("menus.edit"))
        edit_class_act = edit_menu.addAction(tr("dialogs.edit_class.title"))
        edit_class_act.triggered.connect(lambda: self.app._edit_class(self.cls))
        edit_lecturer_act = edit_menu.addAction(f"{tr('menus.edit')} {tr('labels.lecturer')}")
        edit_lecturer_act.triggered.connect(
            lambda: self.app._edit_lecturer_from_class(self.cls))
        if not is_immovable(self.cls) and self.cls["placed"]:
            unplace_act = menu.addAction("\u25B2  " + tr("buttons.unplace"))
            unplace_act.triggered.connect(
                lambda: self.app._unplace_specific(self.cls))
        if not is_immovable(self.cls):
            move_act = menu.addAction("\u2194  " + tr("tooltips.drag_to_move"))
            move_act.setEnabled(False)
        if not self.cls["pinned"] and self.cls["placed"]:
            prot_menu = menu.addMenu("\U0001F6E1  " + tr("protection.title"))
            current = self.cls.get("protection", "none")
            for level in PROTECTION_LEVELS:
                label = get_protection_label(level)
                act = prot_menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(level == current)
                act.triggered.connect(
                    lambda checked, lv=level: self.app._set_protection(
                        self.cls, lv))
        menu.addSeparator()
        remove_act = menu.addAction("\u2715  " + tr("buttons.remove"))
        remove_act.triggered.connect(
            lambda: self.app._remove_specific(self.cls))
        menu.exec(event.screenPos())


# ═════════════════════════════════════════════════════════════════════
#  SCENE
# ═════════════════════════════════════════════════════════════════════

class TimetableScene(QGraphicsScene):
    """Assembles header + lesson + empty-slot items into a timetable grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lesson_items = []
        self.empty_items = []
        # Grid geometry for view-level drop handling
        self._grid_days = []       # list of day names
        self._grid_slots = []      # list of slot names
        self._grid_x0 = 0         # x origin of first data column
        self._grid_y0 = 0         # y origin of first data row
        self._grid_col_w = 0      # width of each column (+ gap)
        self._grid_row_h = 0      # height of each row (+ gap)
        self._grid_gap = GRID_GAP
        self._grid_mode = "filtered"  # "filtered" or "everything"
        self._app = None          # app ref for view-level drops
        self._grid_day_groups = []

    def cell_at(self, scene_pos):
        """Return (day, slot) at *scene_pos*, or (None, None) if outside grid."""
        if self._grid_mode != "filtered":
            return None, None
        x, y = scene_pos.x(), scene_pos.y()

        # Use per-row Y offsets if available, else fall back to fixed
        row_y = getattr(self, "_grid_row_y", None)
        row_heights = getattr(self, "_grid_row_heights", None)
        if row_y and row_heights:
            row = -1
            for ri in range(len(row_y)):
                ry = row_y[ri]
                rh = row_heights[ri]
                if ry <= y <= ry + rh:
                    row = ri
                    break
            if row < 0:
                return None, None
        else:
            row_step = self._grid_row_h + self._grid_gap
            if row_step <= 0:
                return None, None
            row = int((y - self._grid_y0) / row_step)

        if not (0 <= row < len(self._grid_slots)):
            return None, None

        if row_y and row_heights:
            cy = row_y[row]
            rh = row_heights[row]
        else:
            row_step = self._grid_row_h + self._grid_gap
            cy = self._grid_y0 + row * row_step
            rh = self._grid_row_h

        day_groups = getattr(self, "_grid_day_groups", None) or []
        if day_groups:
            for group in day_groups:
                gx = group["x"]
                gw = group["width"]
                if gx <= x <= gx + gw and cy <= y <= cy + rh:
                    return group["day"], self._grid_slots[row]
            return None, None

        col_step = self._grid_col_w + self._grid_gap
        if col_step <= 0:
            return None, None
        col = int((x - self._grid_x0) / col_step)
        if 0 <= col < len(self._grid_days):
            cx = self._grid_x0 + col * col_step
            if cx <= x <= cx + self._grid_col_w and cy <= y <= cy + rh:
                return self._grid_days[col], self._grid_slots[row]
        return None, None

    def cell_rect(self, col, row):
        """Return QRectF for data cell at grid (col, row)."""
        row_y = getattr(self, "_grid_row_y", None)
        row_heights = getattr(self, "_grid_row_heights", None)
        if row_y and row_heights and 0 <= row < len(row_y):
            y = row_y[row]
            rh = row_heights[row]
        else:
            row_step = self._grid_row_h + self._grid_gap
            y = self._grid_y0 + row * row_step
            rh = self._grid_row_h
        day_groups = getattr(self, "_grid_day_groups", None) or []
        if day_groups and 0 <= col < len(day_groups):
            group = day_groups[col]
            return QRectF(group["x"], y, group["width"], rh)
        col_step = self._grid_col_w + self._grid_gap
        x = self._grid_x0 + col * col_step
        return QRectF(x, y, self._grid_col_w, rh)

    # ── filtered view ────────────────────────────────────────────

    def _build_filtered_default(self, state, app, days, slots, blocks, occ, g):
        nd, ns = len(days), len(slots)

        row_heights = [ROW_SLOT_H] * ns
        for b in blocks:
            block_w = _filtered_block_width(b, FILTER_MODE_DEFAULT)
            if b["span"] == 1:
                needed = _needed_height_for_class(
                    b["cls"], block_w, conflict=bool(b.get("conflict")))
                if needed > row_heights[b["row"]]:
                    row_heights[b["row"]] = needed

        for b in blocks:
            if b["span"] > 1:
                block_w = _filtered_block_width(b, FILTER_MODE_DEFAULT)
                needed = _needed_height_for_class(
                    b["cls"], block_w, conflict=bool(b.get("conflict")))
                existing = sum(row_heights[b["row"]:b["row"] + b["span"]]) + (b["span"] - 1) * g
                if needed > existing:
                    extra = needed - existing
                    per_row = extra / b["span"]
                    for r in range(b["row"], b["row"] + b["span"]):
                        row_heights[r] += per_row

        row_y = []
        y_cur = ROW_HEADER_H + g
        for rh in row_heights:
            row_y.append(y_cur)
            y_cur += rh + g

        self._grid_days = list(days)
        self._grid_slots = list(slots)
        self._grid_x0 = COL_TIME_W + g
        self._grid_y0 = ROW_HEADER_H + g
        self._grid_col_w = COL_DAY_W
        self._grid_row_h = ROW_SLOT_H
        self._grid_gap = g
        self._grid_mode = "filtered"
        self._grid_row_y = list(row_y)
        self._grid_row_heights = list(row_heights)
        self._grid_day_groups = []

        total_w = COL_TIME_W + g + nd * (COL_DAY_W + g)
        total_h = y_cur

        self.addRect(0, 0, total_w, total_h,
                     QPen(Qt.PenStyle.NoPen), QBrush(QColor("#CBD5E1")))
        self.addItem(HeaderItem(
            QRectF(0, 0, COL_TIME_W, ROW_HEADER_H),
            "", CORNER_BG, "white"))

        for j, day in enumerate(days):
            x = COL_TIME_W + g + j * (COL_DAY_W + g)
            self.addItem(HeaderItem(
                QRectF(x, 0, COL_DAY_W, ROW_HEADER_H),
                tr(f"weekdays.{day}"), HEADER_BG_DARK, "white", 10))

        for i, slot in enumerate(slots):
            y = row_y[i]
            rh = row_heights[i]
            self.addItem(HeaderItem(
                QRectF(0, y, COL_TIME_W, rh),
                slot, TIME_BG, "white", 9))
            for j in range(nd):
                if (i, j) in occ:
                    continue
                x = COL_TIME_W + g + j * (COL_DAY_W + g)
                ei = EmptySlotItem(days[j], slot,
                                   QRectF(x, y, COL_DAY_W, rh),
                                   app)
                self.addItem(ei)
                self.empty_items.append(ei)

        for b in blocks:
            # ST-UI-001: lane 0 sits where the single block used to, and a
            # timetable with no collisions has lane_count 1 everywhere, so this
            # is the old geometry exactly.
            lane_w = _filtered_block_width(b, FILTER_MODE_DEFAULT)
            x = (COL_TIME_W + g + b["col"] * (COL_DAY_W + g)
                 + b["lane"] * (lane_w + g))
            y = row_y[b["row"]]
            h = sum(row_heights[b["row"]:b["row"] + b["span"]]) + (b["span"] - 1) * g
            item = LessonItem(
                b["cls"], state, b["base_color"], b["bg_color"],
                QRectF(x, y, lane_w, h), app, b["day"], b["slot"],
                conflict=b.get("conflict", False),
                conflict_partners=b.get("conflict_partners", ()),
                conflict_labels=b.get("conflict_labels", ()))
            self.addItem(item)
            self.lesson_items.append(item)

        self.setSceneRect(0, 0, total_w, total_h)

    def _build_filtered_virtual_subcolumns(self, state, app, days, slots, layout, g):
        blocks = layout["blocks"]
        occ = layout["occ"]
        day_groups = [dict(group) for group in layout["day_groups"]]
        ns = len(slots)

        row_heights = [ROW_SLOT_H] * ns
        for b in blocks:
            if b["span"] == 1:
                needed = _needed_height_for_class(
                    b["cls"], COL_DAY_W, conflict=bool(b.get("conflict")))
                if needed > row_heights[b["row"]]:
                    row_heights[b["row"]] = needed

        for b in blocks:
            if b["span"] > 1:
                needed = _needed_height_for_class(
                    b["cls"], COL_DAY_W, conflict=bool(b.get("conflict")))
                existing = sum(row_heights[b["row"]:b["row"] + b["span"]]) + (b["span"] - 1) * g
                if needed > existing:
                    extra = needed - existing
                    per_row = extra / b["span"]
                    for r in range(b["row"], b["row"] + b["span"]):
                        row_heights[r] += per_row

        row_y = []
        y_cur = ROW_HEADER_H + g
        for rh in row_heights:
            row_y.append(y_cur)
            y_cur += rh + g

        x_cur = COL_TIME_W + g
        for group in day_groups:
            width = group["lane_count"] * COL_DAY_W + (group["lane_count"] - 1) * g
            group["x"] = x_cur
            group["width"] = width
            x_cur += width + g

        total_w = x_cur
        total_h = y_cur

        self._grid_days = list(days)
        self._grid_slots = list(slots)
        self._grid_x0 = COL_TIME_W + g
        self._grid_y0 = ROW_HEADER_H + g
        self._grid_col_w = COL_DAY_W
        self._grid_row_h = ROW_SLOT_H
        self._grid_gap = g
        self._grid_mode = "filtered"
        self._grid_row_y = list(row_y)
        self._grid_row_heights = list(row_heights)
        self._grid_day_groups = list(day_groups)

        self.addRect(0, 0, total_w, total_h,
                     QPen(Qt.PenStyle.NoPen), QBrush(QColor("#CBD5E1")))
        self.addItem(HeaderItem(
            QRectF(0, 0, COL_TIME_W, ROW_HEADER_H),
            "", CORNER_BG, "white"))

        for group in day_groups:
            self.addItem(HeaderItem(
                QRectF(group["x"], 0, group["width"], ROW_HEADER_H),
                tr(f"weekdays.{group['day']}"), HEADER_BG_DARK, "white", 10))

        for i, slot in enumerate(slots):
            y = row_y[i]
            rh = row_heights[i]
            self.addItem(HeaderItem(
                QRectF(0, y, COL_TIME_W, rh),
                slot, TIME_BG, "white", 9))
            for group in day_groups:
                for lane in range(group["lane_count"]):
                    subcolumn = group["subcolumn_start"] + lane
                    if (i, subcolumn) in occ:
                        continue
                    x = group["x"] + lane * (COL_DAY_W + g)
                    ei = EmptySlotItem(
                        group["day"], slot,
                        QRectF(x, y, COL_DAY_W, rh),
                        app,
                    )
                    self.addItem(ei)
                    self.empty_items.append(ei)

        group_by_day = {group["day"]: group for group in day_groups}
        for b in blocks:
            group = group_by_day[b["day"]]
            x = group["x"] + b["lane"] * (COL_DAY_W + g)
            y = row_y[b["row"]]
            h = sum(row_heights[b["row"]:b["row"] + b["span"]]) + (b["span"] - 1) * g
            # ST-UI-001. `filtered_layout` stamps the conflict flags on BOTH
            # modes' blocks; this branch used to drop them on the floor, so the
            # Online / Lecturer-office tab drew a genuine clash — the same
            # lecturer twice, or one student group twice — with a normal
            # year-coloured border and a tooltip that said nothing, while every
            # other tab painted it red. Labelling is view-independent by
            # design; this was the one view that did not honour it.
            item = LessonItem(
                b["cls"], state, b["base_color"], b["bg_color"],
                QRectF(x, y, COL_DAY_W, h), app, b["day"], b["slot"],
                conflict=b.get("conflict", False),
                conflict_partners=b.get("conflict_partners", ()),
                conflict_labels=b.get("conflict_labels", ()))
            self.addItem(item)
            self.lesson_items.append(item)

        self.setSceneRect(0, 0, total_w, total_h)

    def build_filtered(self, state, filter_fn, app, mode=FILTER_MODE_DEFAULT,
                       conflict_partners=None):
        self.clear()
        self.lesson_items.clear()
        self.empty_items.clear()
        self._app = app
        self._grid_day_groups = []

        days = state["days"]
        slots = state["slots"]
        if not days or not slots:
            return

        g = GRID_GAP
        layout = RendererAdapter.filtered_layout(
            state, filter_fn, mode=mode, conflict_partners=conflict_partners)
        if layout["virtual_day_subcolumns"]:
            self._build_filtered_virtual_subcolumns(
                state, app, days, slots, layout, g)
            return
        self._build_filtered_default(
            state, app, days, slots, layout["blocks"], layout["occ"], g)

    # ── everything view ──────────────────────────────────────────

    def build_everything(self, state, app=None, conflict_partners=None):
        """Build the full 'Show Everything' matrix for all years."""
        self.clear()
        self.lesson_items.clear()
        self.empty_items.clear()
        self._grid_mode = "everything"
        self._app = app
        self._grid_day_groups = []

        days = state["days"]
        slots = state["slots"]
        years = state["years"]
        if not days or not slots or not years:
            return

        g = GRID_GAP
        y_offset = 0
        max_w = 0
        ns = len(slots)

        for yr_idx, yr in enumerate(sorted(years.keys())):
            branches = years[yr]
            if not branches:
                continue
            nb = len(branches)

            if yr_idx > 0:
                y_offset += 15  # spacer

            # year label
            lbl = self.addSimpleText(yr, QFont("Segoe UI", 13, QFont.Weight.Bold))
            lbl.setBrush(QBrush(QColor("#1E40AF")))
            lbl.setPos(8, y_offset)
            y_offset += 30

            data_x = COL_SESSION_W + g + COL_ETIME_W + g
            total_cols = nb * len(days)
            grid_w = data_x + total_cols * (COL_BRANCH_W + g)
            hdr_h = ROW_DAY_HDR_H + g + ROW_BRANCH_HDR_H + g
            max_w = max(max_w, grid_w)

            # blocks
            eblocks = RendererAdapter.everything_blocks(
                state, yr, conflict_partners=conflict_partners)
            e_occ = set()
            for b in eblocks:
                for d in range(b["span"]):
                    e_occ.add((b["row"] + d, b["col"]))

            # ── Compute adaptive row heights ──
            row_heights = [ROW_ESLOT_H] * ns  # start with minimum
            for b in eblocks:
                if b["span"] == 1:
                    n_lanes = max(1, b.get("lane_count", 1))
                    lane_w = ((COL_BRANCH_W - (n_lanes - 1) * g) / n_lanes
                              if n_lanes > 1 else COL_BRANCH_W)
                    needed = _needed_height_for_class(
                        b["cls"], lane_w, is_matrix=True,
                        conflict=bool(b.get("conflict")))
                    if needed > row_heights[b["row"]]:
                        row_heights[b["row"]] = needed

            # For multi-span blocks, check if total height is enough
            for b in eblocks:
                if b["span"] > 1:
                    n_lanes = max(1, b.get("lane_count", 1))
                    lane_w = ((COL_BRANCH_W - (n_lanes - 1) * g) / n_lanes
                              if n_lanes > 1 else COL_BRANCH_W)
                    needed = _needed_height_for_class(
                        b["cls"], lane_w, is_matrix=True,
                        conflict=bool(b.get("conflict")))
                    existing = sum(row_heights[b["row"]:b["row"] + b["span"]]) + (b["span"] - 1) * g
                    if needed > existing:
                        extra = needed - existing
                        per_row = extra / b["span"]
                        for r in range(b["row"], b["row"] + b["span"]):
                            row_heights[r] += per_row

            # Build cumulative Y offsets for each row
            row_y = []
            y_cur = hdr_h
            for rh in row_heights:
                row_y.append(y_cur)
                y_cur += rh + g

            grid_h = y_cur

            # background
            self.addRect(0, y_offset, grid_w, grid_h,
                         QPen(Qt.PenStyle.NoPen), QBrush(QColor(MATRIX_BORDER)))

            # corners
            self.addItem(HeaderItem(
                QRectF(0, y_offset, COL_SESSION_W,
                       ROW_DAY_HDR_H + g + ROW_BRANCH_HDR_H),
                "", MATRIX_CORNER_BG, MATRIX_DAY_FG))
            self.addItem(HeaderItem(
                QRectF(COL_SESSION_W + g, y_offset, COL_ETIME_W,
                       ROW_DAY_HDR_H + g + ROW_BRANCH_HDR_H),
                tr("labels.time"), MATRIX_CORNER_BG, MATRIX_DAY_FG, 8))

            # day headers
            for d_idx, day in enumerate(days):
                x = data_x + d_idx * nb * (COL_BRANCH_W + g)
                w = nb * (COL_BRANCH_W + g) - g
                self.addItem(HeaderItem(
                    QRectF(x, y_offset, w, ROW_DAY_HDR_H),
                    tr(f"weekdays.{day}"), MATRIX_DAY_BG, MATRIX_DAY_FG, 9))

            # branch sub-headers
            for d_idx in range(len(days)):
                for b_idx, br in enumerate(branches):
                    col = d_idx * nb + b_idx
                    x = data_x + col * (COL_BRANCH_W + g)
                    self.addItem(HeaderItem(
                        QRectF(x, y_offset + ROW_DAY_HDR_H + g,
                               COL_BRANCH_W, ROW_BRANCH_HDR_H),
                        br, MATRIX_BRANCH_BG, MATRIX_BRANCH_FG, 8))

            # slot rows
            for si, slot in enumerate(slots):
                ry = y_offset + row_y[si]
                rh = row_heights[si]
                self.addItem(HeaderItem(
                    QRectF(0, ry, COL_SESSION_W, rh),
                    str(si + 1), MATRIX_SESSION_BG, "#333333", 9))
                self.addItem(HeaderItem(
                    QRectF(COL_SESSION_W + g, ry, COL_ETIME_W, rh),
                    slot, MATRIX_TIME_BG, "white", 8))
                for col in range(total_cols):
                    if (si, col) not in e_occ:
                        x = data_x + col * (COL_BRANCH_W + g)
                        ec = QGraphicsRectItem(
                            QRectF(x, ry, COL_BRANCH_W, rh))
                        ec.setPen(QPen(Qt.PenStyle.NoPen))
                        ec.setBrush(QBrush(QColor("white")))
                        self.addItem(ec)

            # lesson blocks
            for b in eblocks:
                # ST-UI-001: lanes inside the branch column, so a contested
                # cell shows every lesson in it and an uncontested one is
                # exactly where it always was.
                n_lanes = max(1, b.get("lane_count", 1))
                lane_w = ((COL_BRANCH_W - (n_lanes - 1) * g) / n_lanes
                          if n_lanes > 1 else COL_BRANCH_W)
                x = (data_x + b["col"] * (COL_BRANCH_W + g)
                     + b.get("lane", 0) * (lane_w + g))
                ry = y_offset + row_y[b["row"]]
                h = sum(row_heights[b["row"]:b["row"] + b["span"]]) + (b["span"] - 1) * g
                item = MatrixLessonItem(
                    QRectF(x, ry, lane_w, h),
                    b["cls"], b.get("room", ""),
                    b["base_color"], b["bg_color"], app,
                    conflict=b.get("conflict", False),
                    conflict_partners=b.get("conflict_partners", ()),
                    conflict_labels=b.get("conflict_labels", ()))
                self.addItem(item)
                self.lesson_items.append(item)

            y_offset += grid_h

        if max_w > 0:
            self.setSceneRect(0, 0, max_w, y_offset)


# ═════════════════════════════════════════════════════════════════════
#  VIEW
# ═════════════════════════════════════════════════════════════════════

class TimetableView(QGraphicsView):
    """Scrollable, drop-enabled view for a TimetableScene."""

    _VALID_BG = QColor(187, 247, 208, 140)   # semi-transparent green
    _VALID_BD = QColor(22, 163, 74)
    _INVALID_BG = QColor(254, 202, 202, 140)  # semi-transparent red
    _INVALID_BD = QColor(220, 38, 38)

    ZOOM_MIN = 25
    ZOOM_MAX = 300
    ZOOM_DEFAULT = 100
    ZOOM_STEP = 10

    def __init__(self, scene=None, parent=None):
        super().__init__(scene or TimetableScene(), parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setAcceptDrops(True)
        self.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setStyleSheet("background: #CBD5E1; border: none;")

        # Zoom state
        self._zoom_pct = self.ZOOM_DEFAULT
        self._zoom_callback = None  # optional callback(pct) for slider sync

        # Overlay highlight state
        self._drop_highlight = None   # (day, slot, valid) or None

    # ── zoom ───────────────────────────────────────────────────────

    def zoom_pct(self):
        return self._zoom_pct

    def set_zoom(self, pct):
        pct = max(self.ZOOM_MIN, min(self.ZOOM_MAX, pct))
        self._zoom_pct = pct
        scale = pct / 100.0
        self.setTransform(QTransform().scale(scale, scale))
        if self._zoom_callback:
            self._zoom_callback(pct)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.set_zoom(self._zoom_pct + self.ZOOM_STEP)
            elif delta < 0:
                self.set_zoom(self._zoom_pct - self.ZOOM_STEP)
            event.accept()
            return
        super().wheelEvent(event)

    # ── drag overlay ──────────────────────────────────────────────

    def _scene_cell_at(self, viewport_pos):
        """Map a viewport position to (day, slot) using scene geometry."""
        sc = self.scene()
        if sc is None:
            return None, None
        sp = self.mapToScene(viewport_pos)
        return sc.cell_at(sp)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md and md.hasText() and md.text().startswith("class_drag:"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        md = event.mimeData()
        if not (md and md.hasText() and md.text().startswith("class_drag:")):
            super().dragMoveEvent(event)
            return
        event.acceptProposedAction()
        day, slot = self._scene_cell_at(event.position().toPoint())
        sc = self.scene()
        app = sc._app if sc else None
        if day is not None and slot is not None and app is not None:
            valid = app._check_drop_valid(day, slot)
            new_hl = (day, slot, valid)
        else:
            new_hl = None
        if new_hl != self._drop_highlight:
            self._drop_highlight = new_hl
            self.viewport().update()

    def dragLeaveEvent(self, event):
        if self._drop_highlight is not None:
            self._drop_highlight = None
            self.viewport().update()
        # Don't call super — avoids Qt warning when a drag that started
        # inside this view leaves without a preceding dragEnterEvent.
        event.accept()

    def dropEvent(self, event):
        md = event.mimeData()
        if not (md and md.hasText() and md.text().startswith("class_drag:")):
            super().dropEvent(event)
            return
        self._drop_highlight = None
        self.viewport().update()
        day, slot = self._scene_cell_at(event.position().toPoint())
        sc = self.scene()
        app = sc._app if sc else None
        if app is None:
            event.ignore()
            return
        if day is not None and slot is not None:
            event.acceptProposedAction()
            app._execute_drop(day, slot)
            return
        drag_group = list(getattr(app, "_dragging_classes", []) or [])
        if len(drag_group) > 1 and all(not c.get("placed") for c in drag_group):
            # Multi-drag from Unplaced panel: allow dropping anywhere on timetable
            # to trigger batch auto-placement for the selected classes.
            event.acceptProposedAction()
            app._execute_drop_anywhere()
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_highlight is None:
            return
        day, slot, valid = self._drop_highlight
        sc = self.scene()
        if sc is None or not sc._grid_days or not sc._grid_slots:
            return
        if day not in sc._grid_days or slot not in sc._grid_slots:
            return
        col = sc._grid_days.index(day)
        row = sc._grid_slots.index(slot)
        cell_rect = sc.cell_rect(col, row)
        # Map scene rect to viewport coordinates
        tl = self.mapFromScene(cell_rect.topLeft())
        br = self.mapFromScene(cell_rect.bottomRight())
        from PyQt6.QtCore import QRect
        vp_rect = QRect(tl, br)
        bg = self._VALID_BG if valid else self._INVALID_BG
        bd = self._VALID_BD if valid else self._INVALID_BD
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(bg))
        p.setPen(QPen(bd, 2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(vp_rect, 6, 6)
        p.end()
