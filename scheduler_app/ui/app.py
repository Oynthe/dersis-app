"""Main application window: SchedulerApp (QMainWindow subclass) — PyQt6."""

import base64
import copy
import json
import csv
import hashlib
import os

from PyQt6.QtWidgets import (
    QMainWindow, QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QComboBox, QLabel, QPushButton, QFrame, QScrollArea, QMenu, QToolBar,
    QFileDialog, QMessageBox, QListWidget, QSplitter, QSizePolicy, QToolButton,
    QDialog, QAbstractItemView, QSlider, QWidgetAction, QStackedWidget,
)
from PyQt6.QtCore import Qt, QPoint, QMimeData, QTimer, QSize
from PyQt6.QtGui import (
    QAction, QKeySequence, QColor, QPainter, QDrag, QCursor, QShortcut, QIcon,
)

from scheduler_app.renderer import (
    TimetableView, TimetableScene, FILTER_MODE_DEFAULT,
    FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP,
)
from scheduler_app.dashboard import DashboardWidget

try:
    # ST-ARCH-003: the workbook writer moved to data_io/exporter.py and took
    # the styling imports with it. This name is still read -- `_export_to_excel`
    # checks `openpyxl is None` to tell the user the dependency is missing
    # before it opens a file dialog -- so the guard stays.
    import openpyxl
except ImportError:
    openpyxl = None

from scheduler_app.constants import OPEN_SLOTS_FG_ROOM
from scheduler_app.translations import tr, get_language, set_language, is_rtl
from scheduler_app.core.text_safety import csv_safe
from scheduler_app.i18n.day_keys import (
    normalize_state_day_keys, day_label, display_day, format_day_time,
)
from scheduler_app.models import (
    new_state, split_non_joint, LOCATION_FACE_TO_FACE, LOCATION_ONLINE,
    LOCATION_LECTURER_OFFICE, get_location_label, is_virtual_location_type,
    normalize_state_classes, effective_day, effective_time, mark_placed,
    mark_unplaced, needs_physical_room, get_effective_room_resource_for_class,
    cls_key,
)
from scheduler_app.logic import (
    get_placed_classes, occupied_slots_of, classroom_of,
    find_schedule_conflicts, conflict_partner_index, schedule_counts,
    find_valid_options,
)
# ST-ARCH-010: moved out of `logic.py` with the rest of the optimization
# bridge; see `scheduler_app/core/facade.py`.
from scheduler_app.core.facade import apply_negotiation_suggestion
from scheduler_app.feedback_logger import FeedbackLogger
from scheduler_app.preference_learner import PreferenceLearner
from scheduler_app import storage
from scheduler_app.workflow import SchedulingWorkflow, snapshot_placements
from scheduler_app.core.schedule_impact_analyzer import (
    capture_snapshot, analyze_impact, ImpactLevel,
)
from scheduler_app.dialogs import (
    SetupDialog, AddClassDialog,
    PlaceClassDialog, SelectClassDialog, MultiSelectClassDialog,
    PostAddDialog,
    BulkAddDialog, BulkResultsDialog, EditClassesDialog,
    _ensure_excel_deps,
)
from scheduler_app.widgets import Toast, WarningLogPanel, YearLegend
from scheduler_app.ui.bug_report import BugReportButton, BugReportDialog
from scheduler_app.icons import (
    icon_add_class, icon_placement, icon_reschedule, icon_setup,
    icon_add_single, icon_bulk_add, icon_place, icon_unplace, icon_delete,
    icon_new, icon_open, icon_save, icon_export, icon_edit,
    get_arrow_dir,
)


_CLASSROOM_FILTER_ROOM_PREFIX = "room::"
_CLASSROOM_FILTER_VIRTUAL_PREFIX = "virtual::"


def _encode_classroom_filter_room(room):
    return f"{_CLASSROOM_FILTER_ROOM_PREFIX}{room}"


def _encode_classroom_filter_virtual(location_type):
    return f"{_CLASSROOM_FILTER_VIRTUAL_PREFIX}{location_type}"


def _decode_classroom_filter_value(value):
    text = str(value or "")
    if text.startswith(_CLASSROOM_FILTER_VIRTUAL_PREFIX):
        return "virtual", text[len(_CLASSROOM_FILTER_VIRTUAL_PREFIX):]
    if text.startswith(_CLASSROOM_FILTER_ROOM_PREFIX):
        return "room", text[len(_CLASSROOM_FILTER_ROOM_PREFIX):]
    return None, text


def _make_sidebar_icon(color="#475569"):
    """Create a sidebar-panel toggle icon (rectangle with left vertical bar)."""
    from PyQt6.QtGui import QPixmap, QPen
    pm = QPixmap(18, 18)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Outer rectangle
    p.drawRoundedRect(3, 3, 12, 12, 2, 2)
    # Left sidebar vertical line
    p.drawLine(7, 3, 7, 15)
    p.end()
    return QIcon(pm)


def _make_zoom_icon(kind):
    """Create a QIcon with a painted minus or plus symbol."""
    from PyQt6.QtGui import QPixmap, QPen
    pm = QPixmap(16, 16)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#475569"), 2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    # Horizontal bar (minus/plus both have it)
    p.drawLine(4, 8, 12, 8)
    if kind == "plus":
        p.drawLine(8, 4, 8, 12)
    p.end()
    return QIcon(pm)


# ── Global Stylesheet ──────────────────────────────────────────────────────

def _build_stylesheet():
    """Build the global stylesheet with generated arrow image paths."""
    arrows = get_arrow_dir()
    down = os.path.join(arrows, "down.png").replace("\\", "/")
    up = os.path.join(arrows, "up.png").replace("\\", "/")
    left = os.path.join(arrows, "left.png").replace("\\", "/")
    right = os.path.join(arrows, "right.png").replace("\\", "/")
    return (_APP_STYLESHEET_TEMPLATE
            .replace("ARROW_DOWN", down).replace("ARROW_UP", up)
            .replace("ARROW_LEFT", left).replace("ARROW_RIGHT", right))

_APP_STYLESHEET_TEMPLATE = """
/* ── Main Window ── */
QMainWindow {
    background: #F1F5F9;
}

/* ── Menu Bar ── */
QMenuBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1E293B, stop:1 #334155);
    color: #E2E8F0;
    padding: 2px 0px;
    font-size: 10pt;
    font-family: "Segoe UI", sans-serif;
}
QMenuBar::item {
    padding: 6px 14px;
    border-radius: 4px;
    margin: 1px 2px;
}
QMenuBar::item:selected {
    background: #475569;
    color: white;
}
QMenu {
    background: #FFFFFF;
    color: #1E293B;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 4px;
    font-size: 9pt;
    font-family: "Segoe UI", sans-serif;
}
QMenu::item {
    padding: 6px 28px 6px 12px;
    border-radius: 4px;
    margin: 1px 2px;
}
QMenu::item:selected {
    background: #EFF6FF;
    color: #1E40AF;
}
QMenu::separator {
    height: 1px;
    background: #E2E8F0;
    margin: 4px 8px;
}
QMenu::icon {
    padding-left: 8px;
}

/* ── Toolbar ── */
QToolBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #F8FAFC, stop:1 #E2E8F0);
    border-bottom: 1px solid #CBD5E1;
    spacing: 6px;
    padding: 4px 8px;
}
QToolBar::separator {
    width: 1px;
    background: #CBD5E1;
    margin: 4px 6px;
}
QToolButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFFFFF, stop:1 #F1F5F9);
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 5px 14px;
    font-size: 9pt;
    font-weight: bold;
    color: #334155;
    font-family: "Segoe UI", sans-serif;
}
QToolButton:hover {
    background: #EFF6FF;
    border-color: #93C5FD;
    color: #1E40AF;
}
QToolButton:pressed, QToolButton::menu-button:pressed {
    background: #DBEAFE;
}
QToolButton::menu-indicator {
    /* ST-UI-017: `image: none` was here, which made a toolbar button that
       opens a MENU pixel-identical to one that performs an action. The user
       could not tell which of the two a button was until they clicked it.
       The width/position lines stay, so the layout is unchanged. */
    subcontrol-position: right center;
    subcontrol-origin: padding;
    width: 12px;
}

/* ── Tabs ── */
QTabWidget::pane {
    border: 1px solid #CBD5E1;
    border-radius: 0px 0px 8px 8px;
    background: white;
    top: -1px;
}
QTabBar::tab {
    background: #E2E8F0;
    border: 1px solid #CBD5E1;
    border-bottom: none;
    border-radius: 6px 6px 0px 0px;
    padding: 7px 18px;
    margin-right: 2px;
    font-size: 9pt;
    font-family: "Segoe UI", sans-serif;
    color: #64748B;
}
QTabBar::tab:selected {
    background: white;
    color: #1E40AF;
    font-weight: bold;
    border-bottom: 2px solid #3B82F6;
}
QTabBar::tab:hover:!selected {
    background: #F1F5F9;
    color: #334155;
}

/* ── TabBar scroll buttons ── */
QTabBar::scroller {
    width: 56px;
}
QTabBar QToolButton {
    background: #F1F5F9;
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    padding: 2px;
    margin: 2px 1px;
    width: 22px;
    height: 22px;
    min-width: 22px;
    max-width: 22px;
    min-height: 22px;
    max-height: 22px;
    icon-size: 12px;
}
QTabBar QToolButton:hover {
    background: #DBEAFE;
    border-color: #93C5FD;
}
QTabBar QToolButton::left-arrow {
    image: url(ARROW_LEFT);
    width: 12px;
    height: 12px;
}
QTabBar QToolButton::right-arrow {
    image: url(ARROW_RIGHT);
    width: 12px;
    height: 12px;
}

/* ── ComboBox ── */
QComboBox {
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    padding: 4px 8px;
    padding-right: 28px;
    background: white;
    font-size: 9pt;
    min-width: 100px;
    min-height: 22px;
    font-family: "Segoe UI", sans-serif;
}
QComboBox:hover {
    border-color: #93C5FD;
}
QComboBox:focus {
    border-color: #3B82F6;
}
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: 22px;
    border-left: 1px solid #CBD5E1;
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
    background: #F1F5F9;
}
QComboBox::down-arrow {
    image: url(ARROW_DOWN);
    width: 12px;
    height: 12px;
}
QComboBox QAbstractItemView {
    border: 1px solid #CBD5E1;
    background: white;
    color: #1E293B;
    selection-background-color: #EFF6FF;
    selection-color: #1E40AF;
}

/* ── SpinBox ── */
QSpinBox {
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    padding: 4px 8px;
    padding-right: 22px;
    background: white;
    font-size: 9pt;
    min-height: 22px;
    font-family: "Segoe UI", sans-serif;
}
QSpinBox:hover {
    border-color: #93C5FD;
}
QSpinBox:focus {
    border-color: #3B82F6;
}
QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #CBD5E1;
    border-bottom: 1px solid #CBD5E1;
    border-top-right-radius: 4px;
    background: #F1F5F9;
}
QSpinBox::up-button:hover {
    background: #E2E8F0;
}
QSpinBox::up-arrow {
    image: url(ARROW_UP);
    width: 10px;
    height: 10px;
}
QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border-left: 1px solid #CBD5E1;
    border-bottom-right-radius: 4px;
    background: #F1F5F9;
}
QSpinBox::down-button:hover {
    background: #E2E8F0;
}
QSpinBox::down-arrow {
    image: url(ARROW_DOWN);
    width: 10px;
    height: 10px;
}

/* ── Push Button (general) ── */
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFFFFF, stop:1 #F1F5F9);
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 5px 14px;
    font-size: 9pt;
    color: #334155;
    font-family: "Segoe UI", sans-serif;
}
QPushButton:hover {
    background: #EFF6FF;
    border-color: #93C5FD;
    color: #1E40AF;
}
QPushButton:pressed {
    background: #DBEAFE;
}

/* ── Splitter ── */
QSplitter::handle {
    background: #CBD5E1;
    width: 3px;
    margin: 2px;
    border-radius: 1px;
}
QSplitter::handle:hover {
    background: #94A3B8;
}

/* ── ScrollBar ── */
QScrollBar:vertical {
    background: #F1F5F9;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #94A3B8;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #64748B;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: #F1F5F9;
    height: 10px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #94A3B8;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #64748B;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* ── Status Bar ── */
QStatusBar {
    background: #1E293B;
    color: #CBD5E1;
    font-size: 9pt;
    font-family: "Segoe UI", sans-serif;
    padding: 2px 8px;
}
QStatusBar QLabel {
    color: #CBD5E1;
    font-size: 9pt;
}

/* ── Tree Widget ── */
QTreeWidget {
    font-size: 9pt;
    font-family: "Segoe UI", sans-serif;
    alternate-background-color: #F0FDF4;
}
QTreeWidget::item:hover {
    background: #ECFDF5;
}
QTreeWidget::item:selected {
    background: #D1FAE5;
    color: #065F46;
}
QHeaderView::section {
    background: #F1F5F9;
    border: 1px solid #E2E8F0;
    padding: 4px;
    font-weight: bold;
    font-size: 8pt;
    color: #475569;
}

/* ── List Widget ── */
QListWidget {
    background: white;
    color: #1E293B;
    font-size: 9pt;
    font-family: "Segoe UI", sans-serif;
    border-radius: 6px;
}
QListWidget::item {
    padding: 4px 8px;
    border-radius: 3px;
    margin: 1px 2px;
}
QListWidget::item:hover {
    background: #FEF9C3;
}
QListWidget::item:selected {
    background: #FDE68A;
    color: #92400E;
}

/* ── Labels ── */
QLabel {
    font-family: "Segoe UI", sans-serif;
}
"""


# ── Light palette ───────────────────────────────────────────────────────────

def apply_light_palette(app):
    """Pin a deterministic light palette on the QApplication.

    Dersis ships a light-only stylesheet but sets no palette. Qt 6.5+ adopts
    the OS colour scheme by default, so on Windows running in *dark mode* the
    default palette supplies light text. Every stylesheet rule that sets a
    light background without also setting a text colour (drop-down menus,
    list/table rows, line edits, ...) then renders light-on-light and is
    unreadable — legible only once a row is selected and the highlight colour
    kicks in. Forcing a light palette makes the UI render correctly regardless
    of the operating system's light/dark setting.
    """
    from PyQt6.QtGui import QPalette, QColor

    text      = QColor("#1E293B")   # slate-800 — primary text
    disabled  = QColor("#94A3B8")   # slate-400
    base      = QColor("#FFFFFF")   # input / list / table background
    alt_base  = QColor("#F1F5F9")   # slate-100 — alternating rows
    window    = QColor("#F1F5F9")   # slate-100 — window background
    button    = QColor("#FFFFFF")
    highlight = QColor("#3B82F6")   # blue-500
    hl_text   = QColor("#FFFFFF")

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, window)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, alt_base)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, button)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#DC2626"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, disabled)
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1E293B"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#F8FAFC"))
    pal.setColor(QPalette.ColorRole.Link, highlight)
    pal.setColor(QPalette.ColorRole.LinkVisited, QColor("#7C3AED"))
    pal.setColor(QPalette.ColorRole.Highlight, highlight)
    pal.setColor(QPalette.ColorRole.HighlightedText, hl_text)

    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, disabled)

    app.setPalette(pal)


# ── Drop-enabled tab button (forwards class_drag drops to unplaced list) ──

class _UnplacedTabButton(QPushButton):
    """Tab button that accepts class_drag drops to unplace classes."""

    def __init__(self, text, app, parent=None):
        super().__init__(text, parent)
        self._app = app
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md and md.hasText() and md.text().startswith("class_drag:"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        md = event.mimeData()
        if md and md.hasText() and md.text().startswith("class_drag:"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        md = event.mimeData()
        if md and md.hasText() and md.text().startswith("class_drag:"):
            # Auto-switch to Unplaced tab and delegate to the list widget
            self._app._switch_sidebar_tab(1)
            self._app.unplaced_list.dropEvent(event)
            return
        super().dropEvent(event)


# ── Draggable list for unplaced classes ────────────────────────────────────

class DraggableUnplacedList(QListWidget):
    """QListWidget that supports dragging unplaced classes onto the grid."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._drag_start_pos = None
        self._pre_drag_rows = []
        self.setDragEnabled(False)  # We handle drag manually
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return
        if item not in self.selectedItems():
            self.clearSelection()
            item.setSelected(True)

        selected = self.selected_classes()
        if not selected:
            return
        cls = selected[0]

        menu = QMenu(self)
        _code = cls.get("class_code", "")
        _display = f"[{_code}] {cls['name']}" if _code else cls["name"]
        title_txt = _display if len(selected) == 1 else f"{len(selected)} {tr('status.classes')}"
        title = menu.addAction("\U0001F4D6  " + title_txt)
        title.setEnabled(False)
        menu.addSeparator()

        edit_menu = menu.addMenu("\u270E  " + tr("menus.edit"))
        edit_class_act = edit_menu.addAction(tr("dialogs.edit_class.title"))
        edit_class_act.setEnabled(len(selected) == 1)
        edit_class_act.triggered.connect(lambda: self.app._edit_class(cls))
        edit_lecturer_act = edit_menu.addAction(f"{tr('menus.edit')} {tr('labels.lecturer')}")
        edit_lecturer_act.setEnabled(len(selected) == 1)
        edit_lecturer_act.triggered.connect(
            lambda: self.app._edit_lecturer_from_class(cls))

        remove_act = menu.addAction("\u2715  " + tr("buttons.remove"))
        remove_act.triggered.connect(lambda: self.app._remove_classes(selected))
        menu.exec(self.viewport().mapToGlobal(pos))

    # ── drop support (receive placed classes from timetable) ────────

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md and md.hasText() and md.text().startswith("class_drag:"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        md = event.mimeData()
        if md and md.hasText() and md.text().startswith("class_drag:"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        md = event.mimeData()
        if not (md and md.hasText() and md.text().startswith("class_drag:")):
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        dragging_cls = self.app._dragging_cls
        drag_list = list(getattr(self.app, "_dragging_classes", []) or [])
        if not drag_list and dragging_cls is not None:
            drag_list = [dragging_cls]
        # The drag originator (_start_drag_gfx) pre-emptively calls
        # mark_unplaced on the primary class before drag.exec(), so
        # cls.get("placed") is already False for that class.  Use the
        # backup to detect that the drag originated from a placed slot.
        backup = getattr(self.app, "_drag_backup", None)
        from_placed = backup is not None and backup.get("placed")
        unique = []
        seen = set()
        for cls in drag_list:
            if cls is None:
                continue
            cid = cls_key(cls)
            if cid in seen:
                continue
            seen.add(cid)
            if cls.get("pinned"):
                continue
            if cls.get("placed") or from_placed:
                unique.append(cls)
        if not unique:
            return
        # Undo snapshot was already pushed by _start_drag_gfx before the
        # pre-emptive mark_unplaced, so it captures the correct placed state.
        for cls in unique:
            mark_unplaced(cls)
        self.app._drag_success = True
        if len(unique) == 1:
            self.app._show_toast(tr("status.class_unplaced_drag"), "success")
        else:
            self.app._show_toast(
                tr("status.classes_unplaced_count").format(n=len(unique)),
                "success",
            )

    # ── outgoing drag (drag unplaced classes onto the grid) ───────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self._pre_drag_rows = [self.row(it) for it in self.selectedItems()]
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_start_pos is not None
                and (event.pos() - self._drag_start_pos).manhattanLength()
                > QApplication.startDragDistance()):
            row = self.row(self.itemAt(self._drag_start_pos))
            self._drag_start_pos = None
            # ST-ARCH-015: resolve through the uid map, never a stored position.
            dragged = self._classes_from_rows([row])
            if not dragged:
                return
            cls = dragged[0]
            selected = self._classes_from_rows(
                self.row(sel_item) for sel_item in self.selectedItems()
            )
            # Qt may collapse selection on press. If drag began from a row that
            # was part of a larger selection, preserve that pre-press selection.
            if (len(selected) <= 1 and self._pre_drag_rows
                    and row in self._pre_drag_rows):
                pre_selected = self._classes_from_rows(self._pre_drag_rows)
                if len(pre_selected) > 1:
                    selected = pre_selected
            if cls in selected and len(selected) > 1:
                self.app._start_drag_unplaced(selected, self)
            else:
                self.app._start_drag_unplaced([cls], self)
            self._pre_drag_rows = []
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        self._pre_drag_rows = []
        super().mouseReleaseEvent(event)

    def _classes_from_rows(self, rows):
        """Resolve sidebar rows to class dicts, tolerating a stale panel.

        ST-ARCH-015. This used to index ``state_data["classes"]`` with a
        position stored when the panel was last built, guarding only the *row*
        against ``len(_unplaced_indices)``. Both failure modes were live:

        * the list shrank and the stored position was past the end, which
          raised ``IndexError`` inside a Qt slot — and PyQt6 answers an
          unhandled exception in a slot with ``qFatal``, so the process died
          at ``0xC0000409`` with no dialog and no traceback;
        * the list shrank at the *front* and the stored position still
          resolved, silently returning a **different class** than the one the
          user had highlighted, which is worse than the crash.

        Identity, not position: ``class_uid`` exists precisely so it survives
        list mutations (``models.cls_key``). A uid that no longer resolves is
        not a selection, because that class is genuinely gone.
        """
        by_uid = self.app._unplaced_class_by_uid()
        uids = self.app._unplaced_uids
        classes = []
        for sel_row in rows:
            if not (0 <= sel_row < len(uids)):
                continue
            sel_cls = by_uid.get(uids[sel_row])
            if sel_cls is not None and sel_cls not in classes:
                classes.append(sel_cls)
        return classes

    def selected_classes(self):
        return self._classes_from_rows(
            self.row(sel_item) for sel_item in self.selectedItems()
        )

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.selectAll()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Delete):
            selected = self.selected_classes()
            if selected:
                self.app._remove_classes(selected)
                event.accept()
                return
        super().keyPressEvent(event)


# ── Main Application Window ─────────────────────────────────────────────────

# How long refreshes are allowed to coalesce before the settings container is
# rewritten (ST-PERF-002). Long enough that a burst of clicks costs one write,
# short enough that a crash loses at most this much; every exit path flushes.
AUTOSAVE_DEBOUNCE_MS = 1500

# ST-UI-013. The size the window opened at for every user on every launch,
# because nothing ever saved or restored a geometry. Measured natively at that
# exact size, Turkish, 5 days x 8 periods: scene 841x607 into a viewport
# 769x457 — both scrollbars, before the user has touched anything. It survives
# only as the fallback a maximized first run starts from, so that un-maximizing
# lands somewhere sane.
DEFAULT_WINDOW_W = 1150
DEFAULT_WINDOW_H = 720

# Keys in the settings container (the same one the language flag uses).
WINDOW_GEOMETRY_KEY = "window_geometry"
SIDEBAR_INTENT_KEY = "sidebar_intent"

# How much of a restored window frame must land on a connected screen before
# the geometry is believed. A laptop undocked from a second monitor restores a
# frame nobody can reach; a quarter on screen is enough to drag back.
MIN_ON_SCREEN_FRACTION = 0.25

# Extra width demanded before an auto-collapsed sidebar reopens, so a window
# sitting exactly on the threshold does not flicker as it is dragged.
SIDEBAR_REOPEN_HYSTERESIS = 24


class SchedulerApp(QMainWindow):
    def __init__(self, session=None, server_url=''):
        super().__init__()
        self._session = session or {}
        self._server_url = server_url
        self.setWindowTitle(tr("app.title"))
        _icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "docs", "dersis.png")
        if os.path.exists(_icon_path):
            self.setWindowIcon(QIcon(_icon_path))
        # Provisional only — _restore_window_geometry() overrides this at the
        # end of __init__, once there are widgets for a restored size to fit.
        self.resize(DEFAULT_WINDOW_W, DEFAULT_WINDOW_H)
        self.setMinimumSize(850, 550)

        # Apply global stylesheet
        QApplication.instance().setStyleSheet(_build_stylesheet())

        # Ensure Dersis directory tree exists and migrate legacy files
        storage.ensure_dirs()
        storage.migrate_legacy_files()

        self.state_data = new_state()
        self.current_file = None
        self._config_path = storage.settings_path()
        # Settings problems already reported this session, so the user is told
        # once per kind rather than once per refresh (ST-DATA-005).
        self._settings_problems = set()
        self._pending_settings_report = None
        self._deferred_warnings = []
        # ST-PERF-002: autosave is coalesced rather than run once per repaint.
        # Created here, before _auto_load(), because that runs long before
        # _build_main().
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self.flush_auto_save)
        self._autosave_pending = False
        self._autosave_fingerprint = None
        self._active_tutorial = None

        # ST-ARCH-015: the unplaced sidebar addresses classes by uid, never by
        # position — a position goes stale the moment the classes list shrinks.
        self._unplaced_uids = []

        # Selection state
        self._selected_class = None
        self._selected_cell = None       # LessonItem (graphics) reference
        self._selected_classes = []      # selected class objects (possibly multiple)
        self._open_slots_fp = None       # ST-PERF-006 rebuild guard
        self._selected_cells = []        # selected graphics items
        self._selection_anchor = None    # anchor graphics item for Shift+Click
        self._selected_empty_slot = None
        self._conflicts = []             # ST-UI-001, refreshed per repaint
        self._conflict_partners = {}

        # Drag-and-drop state
        self._dragging_cls = None
        self._dragging_classes = []
        self._drag_backup = None
        self._drag_success = False

        # Undo / Redo stacks (snapshot-based)
        self._undo_stack = []   # list of (label, classes_snapshot)
        self._redo_stack = []   # list of (label, classes_snapshot)
        self._max_undo = 50

        # AI-assisted optimization: feedback & learning
        self._feedback_logger = FeedbackLogger()
        self._preference_learner = PreferenceLearner()
        # Run a learning pass on startup to pick up any pending feedback
        self._preference_learner.learn()

        # Schedule impact analysis state (observer layer)
        self._has_baseline = False  # True once initial setup is done
        self._reschedule_required_flag = False
        self._reschedule_recommended_flag = False
        self._structural_change_count = 0
        self._impact_reasons = []  # human-readable trigger descriptions

        # Business-logic workflow (UI-free orchestration layer)
        self._workflow = SchedulingWorkflow(
            self.state_data, self._get_learned_weights,
            feedback_logger=self._feedback_logger,
            preference_learner=self._preference_learner)

        # Widget-scoped shortcuts (e.g., Ctrl+A inside specific views)
        self._ui_shortcuts = []
        # Keep refs so old translated menu actions can be disposed safely.
        self._menu_actions = []

        loaded = self._auto_load()
        if loaded:
            self._has_baseline = True
        self._workflow.state = self.state_data

        self._build_menu()
        self._build_toolbar()
        self._build_main()
        self._build_status()

        # ST-UI-013: after the widgets exist, so the restored sidebar intent
        # has something to act on, and before show(), so the user never sees
        # the window jump from the default size to the remembered one. Before
        # the flush below, because this reads the settings container too and a
        # problem it found would otherwise have nowhere to go.
        self._restore_window_geometry()

        # _auto_load runs above, before any of these widgets exist, so a
        # settings problem found during load had nowhere to go. Flush it now
        # that there is a window — synchronously, so the report has landed by
        # the time __init__ returns (ST-DATA-005/014). Also covers a container
        # quarantined even earlier, by the language gate in first_run.
        self._flush_startup_settings_report()

        # Shortcuts — only register here for keys that have NO menu QAction.
        # Keys that ARE on menu actions (Ctrl+Shift+A/B/P, Ctrl+P, Ctrl+R,
        # Ctrl+N/O/S) are handled via QAction.setShortcutContext(WindowShortcut)
        # in _add_action, so they fire regardless of focus without conflict.
        self._shortcuts = []
        for seq, slot in [
            (QKeySequence.StandardKey.Delete, self._delete_selected),
            (QKeySequence("Ctrl+C"),  self._copy_to_clipboard),
            (QKeySequence("Ctrl+E"),  self._edit_selected_class),
            (QKeySequence("F5"),      self.refresh_grid),
            (QKeySequence("Ctrl+1"),  lambda: self._switch_tab(0)),
            (QKeySequence("Ctrl+2"),  lambda: self._switch_tab(1)),
            (QKeySequence("Ctrl+3"),  lambda: self._switch_tab(2)),
            (QKeySequence("Ctrl+4"),  lambda: self._switch_tab(3)),
            (QKeySequence("Ctrl+5"),  lambda: self._switch_tab(4)),
        ]:
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)
            self._shortcuts.append(sc)  # prevent garbage collection

        if loaded:
            QTimer.singleShot(100, self.refresh_grid)

        # First-run onboarding (tutorial → setup).
        # Language gate runs before window.show() in scheduler_gui.main().
        from scheduler_app.first_run import FirstRunController
        self._first_run = FirstRunController(self)
        QTimer.singleShot(400 if not loaded else 500, self._first_run.start)

    # ── Menu ──────────────────────────────────────────────────────────────

    def _build_menu(self):
        menubar = self.menuBar()
        for action in self._menu_actions:
            try:
                action.setShortcut(QKeySequence())
            except Exception:
                pass
            action.deleteLater()
        self._menu_actions = []
        menubar.clear()

        # File
        file_menu = menubar.addMenu(tr("menubar.file"))
        self._add_action(file_menu, tr("menus.file_new"), self.new_schedule, "Ctrl+N", icon_new())
        self._add_action(file_menu, tr("menus.file_open"), self.open_file, "Ctrl+O", icon_open())
        file_menu.addSeparator()
        self._add_action(file_menu, tr("buttons.save"), self.save_file, "Ctrl+S", icon_save())
        self._add_action(file_menu, tr("menus.file_save_as"), self.save_as, None, icon_save())
        file_menu.addSeparator()
        from scheduler_app.ui.tier_enforcement import gate_menu_action, gate_export_submenu
        from scheduler_app.plans import (
            FEATURE_EXPORT_EXCEL, FEATURE_EXPORT_PDF, FEATURE_EXPORT_CSV,
            FEATURE_BULK_SCHEDULING,
        )
        self._export_submenu = file_menu.addMenu(icon_export(), tr("buttons.export"))
        _act_excel = self._add_action(self._export_submenu, tr("menus.export_excel"), self._export_to_excel)
        gate_menu_action(_act_excel, FEATURE_EXPORT_EXCEL)
        _act_pdf = self._add_action(self._export_submenu, tr("menus.export_pdf"), self._export_to_pdf)
        gate_menu_action(_act_pdf, FEATURE_EXPORT_PDF)
        _act_csv = self._add_action(self._export_submenu, tr("menus.file_export_csv"), self.export_csv)
        gate_menu_action(_act_csv, FEATURE_EXPORT_CSV)
        # Disable entire Export submenu when ALL export features are locked
        gate_export_submenu(self._export_submenu)
        file_menu.addSeparator()
        self._add_action(file_menu, tr("menus.import_excel"), self._import_from_excel)
        self._add_action(file_menu, tr("menus.generate_template"), self._generate_excel_template)
        file_menu.addSeparator()
        self._add_action(file_menu, tr("menus.file_exit"), self._on_quit)

        # Edit
        edit_menu = menubar.addMenu(tr("menubar.edit"))
        self._add_action(edit_menu, tr("actions.undo"), self.undo, "Ctrl+Z")
        self._add_action(edit_menu, tr("actions.redo"), self.redo, "Ctrl+Y")
        edit_menu.addSeparator()
        classes_sub = edit_menu.addMenu(icon_add_class(), tr("menus.classes"))
        self._add_action(classes_sub, tr("toolbar.add_single"), self.add_class, "Ctrl+Shift+A", icon_add_single())
        _act_bulk = self._add_action(classes_sub, tr("toolbar.bulk_add"), self.bulk_add_classes, "Ctrl+Shift+B", icon_bulk_add())
        gate_menu_action(_act_bulk, FEATURE_BULK_SCHEDULING)
        classes_sub.addSeparator()
        self._add_action(classes_sub, tr("menus.edit_classes"), self.edit_classes, "Ctrl+Shift+E", icon_edit())
        place_sub = edit_menu.addMenu(icon_placement(), tr("toolbar.class_placement"))
        self._add_action(
            place_sub, tr("toolbar.place_all_unplaced"),
            self.place_class, "Ctrl+P", icon_place(),
        )
        self._add_action(
            place_sub, tr("dialogs.place.title"),
            self.place_single_class, "Ctrl+Shift+P", icon_place(),
        )
        self._add_action(place_sub, tr("buttons.unplace"), self.unplace_class, "Ctrl+U", icon_unplace())
        self._add_action(place_sub, tr("buttons.delete"), self.remove_class, None, icon_delete())
        place_sub.addSeparator()
        self._add_action(place_sub, tr("buttons.reschedule"), self.reschedule, "Ctrl+R", icon_reschedule())
        edit_menu.addSeparator()
        self._add_action(edit_menu, tr("menus.edit_setup"), self.edit_setup, None, icon_setup())

        # View
        view_menu = menubar.addMenu(tr("menubar.view"))
        self._add_action(view_menu, tr("menus.view_by_classroom"), lambda: self._switch_tab(0))
        self._add_action(view_menu, tr("menus.view_by_group"), lambda: self._switch_tab(1))
        self._add_action(view_menu, tr("menus.view_by_lecturer"), lambda: self._switch_tab(2))
        self._add_action(view_menu, tr("tabs.show_everything"), lambda: self._switch_tab(3))
        view_menu.addSeparator()
        self._add_action(view_menu, tr("tabs.dashboard"), lambda: self._switch_tab(4))
        view_menu.addSeparator()
        self._toggle_sidebar_action = QAction(tr("menus.toggle_sidebar"), view_menu)
        self._toggle_sidebar_action.setCheckable(True)
        self._toggle_sidebar_action.setChecked(True)
        # ST-UI-013: the sidebar is worth a flat 314 px of grid — two day
        # columns at 1000 px — and until now the only ways to reclaim them
        # were a 26 px icon and an unaccelerated menu item. Ctrl+B is free;
        # Ctrl+Shift+B (Bulk Add) is the one already taken.
        self._toggle_sidebar_action.setShortcut(QKeySequence("Ctrl+B"))
        self._toggle_sidebar_action.setShortcutContext(
            Qt.ShortcutContext.WindowShortcut)
        self._toggle_sidebar_action.triggered.connect(self._toggle_sidebar_panel)
        view_menu.addAction(self._toggle_sidebar_action)
        self._menu_actions.append(self._toggle_sidebar_action)

        # Language — opens the shared LanguageDialog, with current flag icon
        from scheduler_app.first_run import LANGUAGE_LIST
        current_lang = get_language()
        lang_icon = None
        for code, _name_key, icon_fn in LANGUAGE_LIST:
            if code == current_lang:
                lang_icon = icon_fn()
                break
        lang_menu = menubar.addMenu(tr("menubar.language"))
        if lang_icon:
            lang_menu.setIcon(lang_icon)
        self._add_action(lang_menu, tr("dialogs.language.title"),
                         self._open_language_dialog)

        # Help
        help_menu = menubar.addMenu(tr("menubar.help"))
        self._add_action(help_menu, tr("tutorial.title"), self._show_tutorial)
        self._add_action(help_menu, tr("dialogs.about.features_title"), self._show_features)
        help_menu.addSeparator()
        self._add_action(help_menu, tr("menus.help_about"), self._show_about)

    def _add_action(self, menu, text, callback, shortcut=None, icon=None):
        action = QAction(text, menu)
        if icon:
            action.setIcon(icon)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        menu.addAction(action)
        self._menu_actions.append(action)
        return action

    # ── Toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = self.addToolBar("main")
        tb.setObjectName("main_toolbar")
        self._toolbar = tb
        tb.setMovable(False)
        tb.setIconSize(QSize(24, 24))

        # "Classes" dropdown menu button
        add_btn = QToolButton()
        add_btn.setIcon(icon_add_class())
        add_btn.setText(tr("toolbar.classes"))
        add_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add_menu = QMenu(add_btn)
        add_menu.addAction(icon_add_single(), tr("toolbar.add_single") + "\tCtrl+Shift+A", self.add_class)
        _tb_bulk_act = add_menu.addAction(icon_bulk_add(), tr("toolbar.bulk_add") + "\tCtrl+Shift+B", self.bulk_add_classes)
        from scheduler_app.ui.tier_enforcement import gate_menu_action
        from scheduler_app.plans import FEATURE_BULK_SCHEDULING, FEATURE_EXPORT_PDF, FEATURE_EXPORT_EXCEL, FEATURE_EXPORT_CSV
        gate_menu_action(_tb_bulk_act, FEATURE_BULK_SCHEDULING)
        add_menu.addSeparator()
        add_menu.addAction(icon_edit(), tr("toolbar.edit_classes") + "\tCtrl+Shift+E", self.edit_classes)
        add_btn.setMenu(add_menu)
        add_btn.setToolTip(tr("menus.classes") + " (Ctrl+Shift+A)")
        tb.addWidget(add_btn)
        self._tb_add_btn = add_btn

        # "Class Placement" dropdown menu button
        place_btn = QToolButton()
        place_btn.setIcon(icon_placement())
        place_btn.setText(tr("toolbar.placement"))
        place_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        place_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        place_menu = QMenu(place_btn)
        place_menu.addAction(
            icon_place(),
            tr("toolbar.place_all_unplaced") + "\tCtrl+P",
            self.place_class,
        )
        place_menu.addAction(
            icon_place(),
            tr("dialogs.place.title") + "\tCtrl+Shift+P",
            self.place_single_class,
        )
        place_menu.addAction(icon_unplace(), tr("buttons.unplace") + "\tCtrl+U", self.unplace_class)
        place_menu.addAction(icon_delete(), tr("buttons.delete"), self.remove_class)
        place_menu.addSeparator()
        place_menu.addAction(icon_reschedule(), tr("buttons.reschedule") + "\tCtrl+R", self.reschedule)
        place_btn.setMenu(place_menu)
        place_btn.setToolTip(tr("toolbar.place_all_unplaced") + " (Ctrl+P)")
        tb.addWidget(place_btn)
        self._tb_place_btn = place_btn

        tb.addSeparator()
        setup_btn = QToolButton()
        setup_btn.setIcon(icon_setup())
        setup_btn.setText(tr("toolbar.setup"))
        setup_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        setup_btn.clicked.connect(self.edit_setup)
        tb.addWidget(setup_btn)
        self._tb_setup_btn = setup_btn

        # ── Reschedule impact badge (observer layer) ──
        tb.addSeparator()
        badge_container = QWidget()
        badge_layout = QHBoxLayout(badge_container)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        badge_layout.setSpacing(2)

        self._impact_badge = QToolButton()
        self._impact_badge.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._impact_badge.clicked.connect(self.reschedule)
        self._impact_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        badge_layout.addWidget(self._impact_badge)

        self._impact_info_btn = QToolButton()
        self._impact_info_btn.setText("\u24D8")  # ⓘ
        self._impact_info_btn.setCursor(Qt.CursorShape.WhatsThisCursor)
        self._impact_info_btn.setStyleSheet(
            "QToolButton { border: none; font-size: 11pt;"
            " color: #6B7280; padding: 2px; }"
            "QToolButton:hover { color: #374151; }")
        badge_layout.addWidget(self._impact_info_btn)

        self._impact_dismiss_btn = QToolButton()
        self._impact_dismiss_btn.setText("\u2715")  # ✕
        self._impact_dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._impact_dismiss_btn.setStyleSheet(
            "QToolButton { border: none; font-size: 9pt;"
            " color: #9CA3AF; padding: 2px; }"
            "QToolButton:hover { color: #EF4444; }")
        self._impact_dismiss_btn.clicked.connect(self._dismiss_impact_badge)
        badge_layout.addWidget(self._impact_dismiss_btn)

        badge_container.setVisible(False)
        self._impact_badge_container = badge_container
        badge_action = QWidgetAction(tb)
        badge_action.setDefaultWidget(badge_container)
        self._impact_badge_action = badge_action
        tb.addAction(badge_action)

        # ── Spacer + upgrade CTA + user avatar (right-aligned) ──
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        # Upgrade CTA button — starts hidden; shown only for free-plan users
        # once the tier has been confirmed by the server.
        self._upgrade_btn = QPushButton(tr('upgrade.cta.button'))
        self._upgrade_btn.setObjectName('upgradeBtn')
        self._upgrade_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upgrade_btn.setStyleSheet(
            'QPushButton#upgradeBtn {'
            '  background: #3B82F6; color: white; font-size: 9pt;'
            '  font-weight: 600; border: none; border-radius: 6px;'
            '  padding: 5px 14px;'
            '}'
            'QPushButton#upgradeBtn:hover { background: #2563EB; }'
        )
        self._upgrade_btn.clicked.connect(self._open_upgrade_page)
        self._upgrade_btn.setVisible(False)
        tb.addWidget(self._upgrade_btn)

        # Auto-update visibility whenever the tier changes (from any source:
        # startup init, heartbeat, account dialog, avatar fetch, etc.)
        # Register only once (toolbar may be rebuilt on language change).
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        if not getattr(self, '_tier_cb_registered', False):
            TierEnforcement.instance().on_tier_changed(
                self._update_upgrade_btn_visibility)
            self._tier_cb_registered = True
        # Apply current state in case set_tier was already called before
        # this window was constructed.
        self._update_upgrade_btn_visibility()

    def _open_upgrade_page(self):
        """Open the pricing page in the user's browser (offline build: no-op)."""
        import webbrowser
        from scheduler_app.ui.tier_enforcement import PRICING_PAGE_URL
        if PRICING_PAGE_URL:
            webbrowser.open(PRICING_PAGE_URL)

    def _update_upgrade_btn_visibility(self):
        """Show the toolbar upgrade button only for free-plan users."""
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        enforcer = TierEnforcement.instance()
        is_free = enforcer.tier_slug == 'free'
        self._upgrade_btn.setVisible(is_free and enforcer.tier_confirmed)
        if hasattr(self, '_upgrade_banner'):
            self._upgrade_banner.setVisible(is_free and enforcer.tier_confirmed)

    # (Account avatar / profile fetch removed — fully offline build.)

    # ── Main area ─────────────────────────────────────────────────────────

    def _build_main(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(5, 0, 5, 0)
        outer_layout.setSpacing(0)

        # Offline warning banner (hidden by default, text set dynamically)
        self._offline_banner = QLabel('')
        self._offline_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._offline_banner.setStyleSheet(
            'background: #FEF3C7; color: #92400E; font-size: 9pt; '
            'font-weight: bold; padding: 6px; border-bottom: 1px solid #F59E0B;')
        self._offline_banner.setVisible(False)
        outer_layout.addWidget(self._offline_banner)

        # Upgrade banner — shown for Free users, conversion-focused
        self._upgrade_banner = QWidget()
        self._upgrade_banner.setStyleSheet(
            'QWidget { background: qlineargradient('
            'x1:0,y1:0,x2:1,y2:0,stop:0 #EFF6FF,stop:1 #DBEAFE);'
            'border-bottom: 1px solid #BFDBFE; }')
        ub_layout = QHBoxLayout(self._upgrade_banner)
        ub_layout.setContentsMargins(16, 6, 16, 6)
        ub_layout.setSpacing(12)
        ub_text = QLabel(tr('upgrade.cta.banner'))
        ub_text.setStyleSheet(
            'font-size: 9pt; color: #1E40AF; font-weight: 500; background: transparent;')
        ub_layout.addWidget(ub_text, 1)
        ub_btn = QPushButton(tr('upgrade.cta.button'))
        ub_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ub_btn.setStyleSheet(
            'QPushButton { background: #3B82F6; color: white; font-size: 8pt;'
            ' font-weight: 600; border: none; border-radius: 4px;'
            ' padding: 4px 12px; }'
            'QPushButton:hover { background: #2563EB; }')
        ub_btn.clicked.connect(self._open_upgrade_page)
        ub_layout.addWidget(ub_btn)
        ub_dismiss = QPushButton('\u2715')
        ub_dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        ub_dismiss.setStyleSheet(
            'QPushButton { background: transparent; color: #64748B;'
            ' border: none; font-size: 11pt; padding: 2px 4px; }'
            'QPushButton:hover { color: #1E293B; }')
        ub_dismiss.clicked.connect(lambda: self._upgrade_banner.setVisible(False))
        ub_layout.addWidget(ub_dismiss)
        self._upgrade_banner.setVisible(False)  # starts hidden; callback updates it
        outer_layout.addWidget(self._upgrade_banner)

        # Horizontal splitter: notebook + open-slots panel + unplaced panel
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        outer_layout.addWidget(self.splitter, 1)

        # Notebook
        self.notebook = QTabWidget()
        self.splitter.addWidget(self.notebook)

        # Tab 1 — By Classroom
        self.tab_classroom = QWidget()
        tcl = QVBoxLayout(self.tab_classroom)
        tcl.setContentsMargins(5, 5, 5, 5)
        fb1 = QHBoxLayout()
        self._classroom_label = QLabel("\U0001F3E0  " + tr("filters.classroom"))
        fb1.addWidget(self._classroom_label)
        self.classroom_filter = QComboBox()
        self.classroom_filter.setMinimumWidth(150)
        self.classroom_filter.currentIndexChanged.connect(lambda: self._render_current_tab())
        fb1.addWidget(self.classroom_filter)
        fb1.addStretch()
        self._export_btn1 = QToolButton()
        self._export_btn1.setText("\u21D7  " + tr("buttons.export"))
        self._export_btn1.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._export_menu1 = QMenu()
        self._export_menu1.addAction(tr("menus.export_excel"), self._export_to_excel)
        self._export_menu1.addAction(tr("menus.export_pdf"), self._export_to_pdf)
        self._export_btn1.setMenu(self._export_menu1)
        fb1.addWidget(self._export_btn1)
        tcl.addLayout(fb1)
        self.grid_view1 = TimetableView()
        tcl.addWidget(self.grid_view1)
        self.notebook.addTab(self.tab_classroom, "\U0001F3E0  " + tr("menus.view_by_classroom"))

        # Tab 2 — By Student Group
        self.tab_group = QWidget()
        tgl = QVBoxLayout(self.tab_group)
        tgl.setContentsMargins(5, 5, 5, 5)
        fb2 = QHBoxLayout()
        self._group_label = QLabel("\U0001F393  " + tr("filters.group"))
        fb2.addWidget(self._group_label)
        self.group_filter = QComboBox()
        self.group_filter.setMinimumWidth(150)
        self.group_filter.currentIndexChanged.connect(lambda: self._render_current_tab())
        fb2.addWidget(self.group_filter)
        fb2.addStretch()
        # ST-UI-006: year colour was the grid's primary grouping cue and
        # nothing explained it. Goes in the filter row that already exists, so
        # it costs the grid zero height -- measured natively, a 12-year legend
        # is 738 px wide and 23 px tall, and the row is 23 px anyway.
        self.year_legend = YearLegend()
        fb2.addWidget(self.year_legend)
        fb2.addSpacing(10)
        self._export_btn2 = QToolButton()
        self._export_btn2.setText("\u21D7  " + tr("buttons.export"))
        self._export_btn2.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._export_menu2 = QMenu()
        self._export_menu2.addAction(tr("menus.export_excel"), self._export_to_excel)
        self._export_menu2.addAction(tr("menus.export_pdf"), self._export_to_pdf)
        self._export_btn2.setMenu(self._export_menu2)
        fb2.addWidget(self._export_btn2)
        tgl.addLayout(fb2)
        self.grid_view2 = TimetableView()
        tgl.addWidget(self.grid_view2)
        self.notebook.addTab(self.tab_group, "\U0001F393  " + tr("menus.view_by_group"))

        # Tab 3 — By Lecturer
        self.tab_lecturer = QWidget()
        tll = QVBoxLayout(self.tab_lecturer)
        tll.setContentsMargins(5, 5, 5, 5)
        fb3 = QHBoxLayout()
        self._lecturer_label = QLabel("\U0001F468\u200D\U0001F3EB  " + tr("filters.lecturer"))
        fb3.addWidget(self._lecturer_label)
        self.lecturer_filter = QComboBox()
        self.lecturer_filter.setMinimumWidth(150)
        self.lecturer_filter.currentIndexChanged.connect(lambda: self._render_current_tab())
        fb3.addWidget(self.lecturer_filter)
        fb3.addStretch()
        self._export_btn3 = QToolButton()
        self._export_btn3.setText("\u21D7  " + tr("buttons.export"))
        self._export_btn3.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._export_menu3 = QMenu()
        self._export_menu3.addAction(tr("menus.export_excel"), self._export_to_excel)
        self._export_menu3.addAction(tr("menus.export_pdf"), self._export_to_pdf)
        self._export_btn3.setMenu(self._export_menu3)
        fb3.addWidget(self._export_btn3)
        tll.addLayout(fb3)
        self.grid_view3 = TimetableView()
        tll.addWidget(self.grid_view3)
        self.notebook.addTab(self.tab_lecturer, "\U0001F468\u200D\U0001F3EB  " + tr("menus.view_by_lecturer"))

        # Tab 4 — Show Everything
        self.tab_everything = QWidget()
        tel = QVBoxLayout(self.tab_everything)
        tel.setContentsMargins(5, 5, 5, 5)
        fb4 = QHBoxLayout()
        self._export_btn4 = QToolButton()
        self._export_btn4.setText("\u21D7  " + tr("buttons.export"))
        self._export_btn4.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._export_menu4 = QMenu()
        self._export_menu4.addAction(tr("menus.export_excel"), self._export_to_excel)
        self._export_menu4.addAction(tr("menus.export_pdf"), self._export_to_pdf)
        self._export_btn4.setMenu(self._export_menu4)
        fb4.addWidget(self._export_btn4)
        fb4.addStretch()
        tel.addLayout(fb4)
        self.grid_view4 = TimetableView()
        tel.addWidget(self.grid_view4)
        self.notebook.addTab(self.tab_everything, "\U0001F4CB  " + tr("tabs.show_everything"))

        # Tab 5 — Dashboard
        self.dashboard_widget = DashboardWidget()
        self.notebook.addTab(self.dashboard_widget, "\U0001F4CA  " + tr("tabs.dashboard"))

        # Ctrl+A in timetable views: select all visible classes in that view.
        # This avoids relying on a global window-level Ctrl+A that can
        # interfere with side-panel list shortcuts.
        for gv in [self.grid_view1, self.grid_view2,
                    self.grid_view3, self.grid_view4]:
            sc_view = QShortcut(QKeySequence.StandardKey.SelectAll, gv)
            sc_view.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc_view.activated.connect(lambda v=gv: self._select_all_in_view(v))
            self._ui_shortcuts.append(sc_view)

        # Wire zoom callbacks for Ctrl+Wheel sync
        for gv in [self.grid_view1, self.grid_view2,
                    self.grid_view3, self.grid_view4]:
            gv._zoom_callback = self._on_view_zoom_changed

        # Connect tab-change signal AFTER all tabs and filters are created
        self.notebook.currentChanged.connect(lambda: self._render_current_tab())

        # ── Collapsible Right Sidebar (tabbed: Open Slots + Unplaced) ──
        self._sidebar_panel = QWidget()
        self._sidebar_panel.setObjectName("sidebar")
        self._sidebar_panel.setStyleSheet(
            "QWidget#sidebar { background: #F8FAFC; border-radius: 8px; }")
        sidebar_layout = QVBoxLayout(self._sidebar_panel)
        sidebar_layout.setContentsMargins(6, 6, 6, 6)
        sidebar_layout.setSpacing(4)

        # Header row: title + collapse button
        sidebar_header_row = QHBoxLayout()
        sidebar_header_row.setContentsMargins(0, 0, 0, 0)
        sidebar_header_row.setSpacing(2)
        self._sidebar_title = QLabel(tr("panels.sidebar"))
        self._sidebar_title.setStyleSheet(
            "QLabel { font-weight: bold; font-size: 10pt; color: #334155;"
            "  padding: 4px 6px; }")
        sidebar_header_row.addWidget(self._sidebar_title, 1)
        self._sidebar_collapse_btn = QPushButton()
        self._sidebar_collapse_btn.setIcon(_make_sidebar_icon("#475569"))
        self._sidebar_collapse_btn.setFixedSize(26, 26)
        self._sidebar_collapse_btn.setToolTip(tr("panels.collapse_sidebar"))
        self._sidebar_collapse_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #CBD5E1;"
            "  border-radius: 5px; padding: 2px; }"
            "QPushButton:hover { background: #E2E8F0; }")
        self._sidebar_collapse_btn.clicked.connect(
            lambda: self._collapse_panel("sidebar", by_user=True))
        sidebar_header_row.addWidget(self._sidebar_collapse_btn)
        sidebar_layout.addLayout(sidebar_header_row)

        # Tab buttons row
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(4)
        self._sidebar_tab_open_slots = QPushButton(
            "\u2B50  " + tr("panels.open_slots"))
        self._sidebar_tab_unplaced = _UnplacedTabButton(
            "\u26A0  " + tr("panels.unplaced_classes"), self)
        _tab_active_ss = (
            "QPushButton { font-weight: bold; font-size: 9pt; color: #1E40AF;"
            "  background: #DBEAFE; border: 1px solid #93C5FD;"
            "  border-radius: 6px; padding: 5px 10px; }"
            "QPushButton:hover { background: #BFDBFE; }")
        _tab_inactive_ss = (
            "QPushButton { font-weight: bold; font-size: 9pt; color: #64748B;"
            "  background: #F1F5F9; border: 1px solid #E2E8F0;"
            "  border-radius: 6px; padding: 5px 10px; }"
            "QPushButton:hover { background: #E2E8F0; }")
        self._sidebar_tab_active_ss = _tab_active_ss
        self._sidebar_tab_inactive_ss = _tab_inactive_ss
        self._sidebar_tab_open_slots.setStyleSheet(_tab_active_ss)
        self._sidebar_tab_unplaced.setStyleSheet(_tab_inactive_ss)
        self._sidebar_tab_open_slots.clicked.connect(
            lambda: self._switch_sidebar_tab(0))
        self._sidebar_tab_unplaced.clicked.connect(
            lambda: self._switch_sidebar_tab(1))
        tab_row.addWidget(self._sidebar_tab_open_slots, 1)
        tab_row.addWidget(self._sidebar_tab_unplaced, 1)
        sidebar_layout.addLayout(tab_row)

        # Stacked content: page 0 = open slots, page 1 = unplaced
        self._sidebar_stack = QStackedWidget()

        # -- Open Slots page --
        osp_page = QWidget()
        osp_page.setObjectName("osp")
        osp_page.setStyleSheet(
            "QWidget#osp { background: #F0FDF4; border-radius: 6px; }")
        osp_lay = QVBoxLayout(osp_page)
        osp_lay.setContentsMargins(4, 4, 4, 4)
        osp_lay.setSpacing(4)
        self._open_slots_filter_hint = QLabel("")
        # ST-UI-007: the hint names the selected lesson, so its text is the
        # user's. See _refresh_open_slots for the measurement.
        self._open_slots_filter_hint.setTextFormat(Qt.TextFormat.PlainText)
        self._open_slots_filter_hint.setStyleSheet(
            "QLabel { font-size: 7pt; color: #4B5563; background: #E0F2FE;"
            "  border: 1px solid #BAE6FD; border-radius: 4px;"
            "  padding: 3px 6px; }")
        self._open_slots_filter_hint.setWordWrap(True)
        self._open_slots_filter_hint.hide()
        osp_lay.addWidget(self._open_slots_filter_hint)
        self._open_slots_scroll = QScrollArea()
        self._open_slots_scroll.setWidgetResizable(True)
        self._open_slots_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._open_slots_scroll.setStyleSheet(
            "QScrollArea { background: #F0FFF0;"
            "  border: 1px solid #A7F3D0; border-radius: 6px; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar::handle:vertical { background: #A7F3D0;"
            "  border-radius: 3px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            "  { height: 0; }")
        self._open_slots_container = QWidget()
        self._open_slots_container.setStyleSheet("background: transparent;")
        self._open_slots_layout = QVBoxLayout(self._open_slots_container)
        self._open_slots_layout.setContentsMargins(6, 6, 6, 6)
        self._open_slots_layout.setSpacing(2)
        self._open_slots_scroll.setWidget(self._open_slots_container)
        osp_lay.addWidget(self._open_slots_scroll)
        # Keep attribute for backward compat checks (hasattr guards)
        self.open_slots_tree = self._open_slots_scroll
        self._sidebar_stack.addWidget(osp_page)

        # -- Unplaced Classes page --
        upl_page = QWidget()
        upl_page.setObjectName("upl")
        upl_page.setStyleSheet(
            "QWidget#upl { background: #FFFBEB; border-radius: 6px; }")
        upl_lay = QVBoxLayout(upl_page)
        upl_lay.setContentsMargins(4, 4, 4, 4)
        upl_lay.setSpacing(4)
        self.unplaced_list = DraggableUnplacedList(self)
        self.unplaced_list.setStyleSheet(
            "QListWidget { background: #FEFCE8; border: 1px solid #FCD34D;"
            "  border-radius: 6px; }"
            "QListWidget::item { padding: 4px 8px; border-radius: 3px;"
            "  margin: 1px 2px; }"
            "QListWidget::item:hover { background: #FEF3C7; }"
            "QListWidget::item:selected { background: #FDE68A; color: #92400E; }")
        self.unplaced_list.itemDoubleClicked.connect(self._on_unplaced_dblclick)
        self.unplaced_list.itemSelectionChanged.connect(self._refresh_open_slots)
        sc_unplaced = QShortcut(
            QKeySequence.StandardKey.SelectAll, self.unplaced_list)
        sc_unplaced.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_unplaced.activated.connect(self.unplaced_list.selectAll)
        self._ui_shortcuts.append(sc_unplaced)
        upl_lay.addWidget(self.unplaced_list)
        self._sidebar_stack.addWidget(upl_page)

        sidebar_layout.addWidget(self._sidebar_stack, 1)

        self._sidebar_panel.setMinimumWidth(0)
        self.splitter.addWidget(self._sidebar_panel)

        self.splitter.setStretchFactor(0, 1)  # notebook
        self.splitter.setStretchFactor(1, 0)  # sidebar

        # ── Sidebar expand button — overlay child of the sidebar panel ──
        self._sidebar_expand_btn = QPushButton(self._sidebar_panel)
        self._sidebar_expand_btn.setIcon(_make_sidebar_icon("#475569"))
        self._sidebar_expand_btn.setFixedSize(28, 28)
        self._sidebar_expand_btn.setToolTip(tr("panels.sidebar"))
        self._sidebar_expand_btn.setStyleSheet(
            "QPushButton { background: #E2E8F0; border: 1px solid #CBD5E1;"
            "  border-radius: 6px; padding: 2px; }"
            "QPushButton:hover { background: #CBD5E1; }")
        self._sidebar_expand_btn.clicked.connect(
            lambda: self._expand_panel("sidebar", by_user=True))
        self._sidebar_expand_btn.setVisible(False)

        # Sidebar collapse state tracking
        self._sidebar_is_collapsed = False
        self._sidebar_saved_width = 350
        self._sidebar_current_tab = 0
        self._collapsed_width = 36
        self._collapse_threshold = 60
        self._in_collapse_sync = False
        # ST-UI-013. Who last decided whether the sidebar is open:
        #   "auto"   — nobody has said; the window width decides
        #   "open"   — the user opened it and it stays open
        #   "closed" — the user closed it and it stays closed
        # A plain resizeEvent breakpoint without this was built and measured:
        # at 1000 px the user clicks Expand and the next 1 px of drag closes it
        # again. Hysteresis does not help, because the window is genuinely
        # still below the threshold. Persisted, so the decision outlives the
        # session that made it.
        self._sidebar_intent = "auto"

        # Detect splitter-drag collapse
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        # Set initial splitter sizes: give notebook the bulk, sidebar ~350
        QTimer.singleShot(0, self._init_splitter_sizes)

        # ── Zoom bar (Office-style) ──
        zoom_bar = QFrame()
        zoom_bar.setFixedHeight(26)
        zoom_bar.setStyleSheet(
            "QFrame { background: #F1F5F9; border-top: 1px solid #E2E8F0; }")
        zl = QHBoxLayout(zoom_bar)
        zl.setContentsMargins(8, 0, 8, 0)
        zl.setSpacing(6)
        zl.addStretch()
        self._zoom_out_btn = QPushButton()
        self._zoom_out_btn.setFixedSize(22, 20)
        self._zoom_out_btn.setIcon(_make_zoom_icon("minus"))
        self._zoom_out_btn.setStyleSheet(
            "QPushButton { background: transparent;"
            " border: 1px solid #CBD5E1; border-radius: 3px; padding: 0; }"
            "QPushButton:hover { background: #E2E8F0; }")
        self._zoom_out_btn.clicked.connect(lambda: self._set_zoom(
            self._zoom_slider.value() - 10))
        zl.addWidget(self._zoom_out_btn)
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(25, 300)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setSingleStep(10)
        self._zoom_slider.setPageStep(25)
        self._zoom_slider.setFixedWidth(180)
        self._zoom_slider.setToolTip(tr("tooltips.zoom"))
        self._zoom_slider.setStyleSheet(
            "QSlider::groove:horizontal {"
            "  height: 4px; background: #CBD5E1; border-radius: 2px; }"
            "QSlider::handle:horizontal {"
            "  width: 14px; height: 14px; margin: -5px 0;"
            "  background: white; border: 1px solid #94A3B8; border-radius: 7px; }"
            "QSlider::handle:horizontal:hover {"
            "  border-color: #3B82F6; }"
            "QSlider::sub-page:horizontal {"
            "  background: #3B82F6; border-radius: 2px; }")
        self._zoom_slider.valueChanged.connect(self._set_zoom)
        zl.addWidget(self._zoom_slider)
        self._zoom_in_btn = QPushButton()
        self._zoom_in_btn.setFixedSize(22, 20)
        self._zoom_in_btn.setIcon(_make_zoom_icon("plus"))
        self._zoom_in_btn.setStyleSheet(
            "QPushButton { background: transparent;"
            " border: 1px solid #CBD5E1; border-radius: 3px; padding: 0; }"
            "QPushButton:hover { background: #E2E8F0; }")
        self._zoom_in_btn.clicked.connect(lambda: self._set_zoom(
            self._zoom_slider.value() + 10))
        zl.addWidget(self._zoom_in_btn)
        self._zoom_label = QLabel(tr("labels.percent_value", value=100))
        self._zoom_label.setFixedWidth(40)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setStyleSheet(
            "font-size: 8pt; color: #475569; border: none;")
        zl.addWidget(self._zoom_label)
        outer_layout.addWidget(zoom_bar)

        # Warning log panel at bottom
        self.warning_log = WarningLogPanel()
        outer_layout.addWidget(self.warning_log)

    # ── Status bar ────────────────────────────────────────────────────────

    def _build_status(self):
        self.status_label = QLabel(tr("status.ready"))
        self.statusBar().addWidget(self.status_label, 1)

        # Bug report button (right side of status bar)
        self._bug_report_btn = BugReportButton()
        self._bug_report_btn.clicked.connect(self._open_bug_report)
        self.statusBar().addPermanentWidget(self._bug_report_btn)

    # ── Bug report ──────────────────────────────────────────────────────

    def _open_bug_report(self):
        """Open the bug report dialog with current context auto-filled."""
        current_module = ''
        if hasattr(self, 'notebook'):
            idx = self.notebook.currentIndex()
            if idx >= 0:
                current_module = self.notebook.tabText(idx)

        dlg = BugReportDialog(
            self,
            current_module=current_module,
        )
        dlg.exec()

    # ── Schedule Impact Analysis (observer layer) ────────────────────────

    def _update_impact_badge(self):
        """Update the reschedule badge visibility and style."""
        badge = getattr(self, "_impact_badge", None)
        if badge is None:
            return
        container = getattr(self, "_impact_badge_container", None)
        action = getattr(self, "_impact_badge_action", None)
        info_btn = getattr(self, "_impact_info_btn", None)

        # Build tooltip from stored reasons
        tooltip = "\n".join(self._impact_reasons) if self._impact_reasons else ""
        if info_btn:
            info_btn.setToolTip(tooltip)

        if self._reschedule_required_flag:
            badge.setText(tr("impact.reschedule_required"))
            badge.setStyleSheet(
                "QToolButton { background: #FEE2E2; border: 1px solid #EF4444;"
                " border-radius: 6px; padding: 5px 14px; font-size: 9pt;"
                " font-weight: bold; color: #DC2626; }"
                "QToolButton:hover { background: #FECACA; }")
            if container:
                container.setVisible(True)
            if action:
                action.setVisible(True)
        elif self._reschedule_recommended_flag or self._structural_change_count > 0:
            badge.setText(tr("impact.reschedule_recommended"))
            badge.setStyleSheet(
                "QToolButton { background: #FEF3C7; border: 1px solid #F59E0B;"
                " border-radius: 6px; padding: 5px 14px; font-size: 9pt;"
                " font-weight: bold; color: #D97706; }"
                "QToolButton:hover { background: #FDE68A; }")
            if container:
                container.setVisible(True)
            if action:
                action.setVisible(True)
        else:
            if container:
                container.setVisible(False)
            if action:
                action.setVisible(False)

    def _clear_impact_flags(self):
        """Clear reschedule flags and hide badge (e.g. after reschedule)."""
        self._reschedule_required_flag = False
        self._reschedule_recommended_flag = False
        self._structural_change_count = 0
        self._impact_reasons = []
        self._update_impact_badge()

    def mark_current_state_as_baseline(self):
        """Mark the current state as the baseline for impact analysis.

        After this call, only *subsequent* changes will trigger the
        reschedule recommender.  Called after initial setup, file load,
        and reschedule to prevent false positives.
        """
        self._has_baseline = True
        self._clear_impact_flags()

    def _note_structural_change(self, before_snapshot, *, description=""):
        """Analyze the impact of adding/deleting classes and show badge if warranted.

        Unlike constraint-change analysis, this does NOT show a modal dialog.
        The badge is only shown when the analyzer determines reschedule is
        needed (violations or optimization opportunities).

        Args:
            before_snapshot: Snapshot captured before the change.
            description: Human-readable description of what triggered the change.
        """
        # No baseline yet (initial setup) → skip analysis
        if not self._has_baseline:
            return

        # No classes left → nothing to reschedule
        if not self.state_data.get("classes"):
            self._clear_impact_flags()
            return

        after_snapshot = capture_snapshot(self.state_data)
        result = analyze_impact(before_snapshot, after_snapshot, self.state_data)

        if result.level == ImpactLevel.NO_RESCHEDULE_NEEDED:
            return

        # Build trigger description for tooltip
        reasons = []
        if description:
            reasons.append(description)
        if result.hard_violations:
            reasons.append(tr("impact.trigger.hard_violations"))
            for v in result.hard_violations[:5]:
                reasons.append(f"  \u2022 {v}")
        if result.soft_impact_reasons:
            reasons.append(tr("impact.trigger.soft_changes"))
            for r in result.soft_impact_reasons[:5]:
                reasons.append(f"  \u2022 {r}")
        self._impact_reasons.extend(reasons)

        self._structural_change_count += 1
        if result.level == ImpactLevel.RESCHEDULE_REQUIRED:
            self._reschedule_required_flag = True
        elif result.level == ImpactLevel.RESCHEDULE_RECOMMENDED:
            if not self._reschedule_required_flag:
                self._reschedule_recommended_flag = True
        self._update_impact_badge()

    def _dismiss_impact_badge(self):
        """Dismiss the impact badge. Warn if level is 'required'."""
        if self._reschedule_required_flag:
            resp = QMessageBox.warning(
                self,
                tr("impact.dismiss_required_title"),
                tr("impact.dismiss_required_warning"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if resp != QMessageBox.StandardButton.Yes:
                return
        self._clear_impact_flags()

    def _run_impact_analysis(self, before_snapshot):
        """Run impact analysis after a save and show prompt if needed.

        Args:
            before_snapshot: Snapshot captured before the change via
                             capture_snapshot().
        """
        # No baseline yet (initial setup) → skip analysis
        if not self._has_baseline:
            return

        after_snapshot = capture_snapshot(self.state_data)
        result = analyze_impact(before_snapshot, after_snapshot, self.state_data)

        if result.level == ImpactLevel.NO_RESCHEDULE_NEEDED:
            return

        # Update global flags (required > recommended)
        if result.level == ImpactLevel.RESCHEDULE_REQUIRED:
            self._reschedule_required_flag = True
        elif result.level == ImpactLevel.RESCHEDULE_RECOMMENDED:
            if not self._reschedule_required_flag:
                self._reschedule_recommended_flag = True

        self._update_impact_badge()

        # Prompt user
        if result.level == ImpactLevel.RESCHEDULE_REQUIRED:
            msg = tr("impact.prompt_required")
        else:
            msg = tr("impact.prompt_recommended")

        resp = QMessageBox.question(
            self, tr("impact.prompt_title"), msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if resp == QMessageBox.StandardButton.Yes:
            self.reschedule()

    # ── Undo / Redo ──────────────────────────────────────────────────────

    def _push_undo(self, label=""):
        """Snapshot the whole application state before a change.

        ST-ARCH-012. This used to deep-copy ``state_data["classes"]`` and
        nothing else, which made Setup permanently un-undoable: Setup rewrites
        the day, slot, classroom, lecturer and year axes, so restoring only the
        classes puts their old placements back onto the NEW grid and
        resurrects exactly the orphans ST-DATA-003 is about. Phase 4 built that
        and withdrew it as "a data-corruption bug wearing a safety label".

        The obvious objection to snapshotting everything is cost, and it does
        not survive measurement. Deep-copying the whole state against the
        classes alone:

            normal (80 classes)   0.788 -> 0.811 ms   +2.9%   6.1 -> 6.3 MB
            large  (250 classes)  2.543 -> 2.583 ms   +1.6%  18.7 -> 19.1 MB

        over the entire 50-entry stack, because the classes list is ~97% of the
        bytes either way. The audit's framing -- "O(classes) deepcopy per
        action stacked on the per-refresh encryption write" -- is stale: Phase 2
        removed that write.
        """
        snapshot = copy.deepcopy(self.state_data)
        self._undo_stack.append((label, snapshot))
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
        # Any new action clears the redo stack
        self._redo_stack.clear()

    def _restore_state(self, snapshot):
        """Replace the live state's CONTENTS with *snapshot*, in place.

        ST-ARCH-012. Rebinding ``self.state_data`` would be the natural way to
        write this and it silently breaks the app: ``SchedulingWorkflow`` holds
        an alias to the same dict (``self._workflow.state``, re-bound at three
        places), and so does the autosave debounce timer, which reads
        ``self.state_data`` when it fires. Rebinding leaves the workflow
        pointing at the pre-undo state, so the grid shows one timetable and
        every validator answers about another.

        The old classes-only undo was accidentally safe here -- it assigned
        into ``state_data["classes"]`` rather than rebinding the dict. Widening
        the snapshot removes that accident, so the in-place contract has to be
        explicit.
        """
        self.state_data.clear()
        self.state_data.update(copy.deepcopy(snapshot))

    def _after_undo_restore(self, label, key):
        """Re-sync the window after the state was replaced wholesale."""
        self._clear_class_selection()
        self._clear_empty_slot_selection()
        # A full-state restore can change the grid AXES, not just the lessons
        # on them, so the filters have to be rebuilt from the restored lists
        # before anything paints. refresh_grid -> _render_current_tab ->
        # _update_filters does that; invalidating the open-slots fingerprint
        # keeps the sidebar from reusing a cache keyed on the old axes.
        self.invalidate_open_slots()
        self.refresh_grid()
        desc = tr(key).format(action=label) if label else tr(
            "actions.undo" if key == "actions.undo_action" else "actions.redo")
        self._show_toast(desc, "info")

    def undo(self):
        """Restore the previous application state."""
        if not self._undo_stack:
            return
        label, snapshot = self._undo_stack.pop()
        # Save current state to redo stack
        current = copy.deepcopy(self.state_data)
        self._redo_stack.append((label, current))
        self._restore_state(snapshot)
        self._after_undo_restore(label, "actions.undo_action")

    def redo(self):
        """Re-apply an undone action."""
        if not self._redo_stack:
            return
        label, snapshot = self._redo_stack.pop()
        # Save current state to undo stack (without clearing redo)
        current = copy.deepcopy(self.state_data)
        self._undo_stack.append((label, current))
        self._restore_state(snapshot)
        self._after_undo_restore(label, "actions.redo_action")

    # ── Auto-save / Auto-load ─────────────────────────────────────────────

    @staticmethod
    def _get_config_path():
        return storage.settings_path()

    def _flush_startup_settings_report(self):
        """Surface a settings problem detected before the window existed."""
        from scheduler_app.ui import first_run as _first_run
        quarantined = getattr(_first_run, "LAST_QUARANTINE", None)
        if quarantined:
            _first_run.LAST_QUARANTINE = None
            self._report_settings_problem(
                "corrupt", tr("errors.settings_corrupt").format(
                    backup=quarantined, err=tr("errors.egu_could_not_decrypt")))
        pending = self._pending_settings_report
        if not pending:
            return
        self._pending_settings_report = None
        # Reaches the panel through _show_toast's mirror below, synchronously —
        # which is what satisfies this method's "at least one channel before it
        # returns" contract.
        try:
            self._show_toast(pending, "error")
        except Exception:
            pass
        self._deferred_warning(tr("status.settings_problem_title"), pending)

    def _deferred_warning(self, title, text):
        """Show a modal warning on the next event-loop turn, owned by this window.

        Deliberately NOT QTimer.singleShot(0, lambda: ...): a context-less
        single shot outlives the window and then fires against a destroyed
        widget, which corrupts the heap. Measured as 6/6 interpreter aborts
        once the off-thread solve started pumping the event loop hard.
        Qt's (msec, context, slot) overload is not exposed by PyQt6, so the
        equivalent is a real QTimer parented to self — destroyed with the
        window, and so never delivered to a dead one.
        """
        self._deferred_warnings.append((title, text))
        timer = QTimer(self)
        timer.setSingleShot(True)
        # A BOUND METHOD, never a lambda. PyQt disconnects a bound-method slot
        # when its QObject is destroyed; a lambda capturing self is just a
        # callable, stays connected, and fires into a half-destroyed window —
        # an access violation during teardown, not an exception.
        timer.timeout.connect(self._drain_deferred_warnings)
        timer.start(0)

    def _drain_deferred_warnings(self):
        pending, self._deferred_warnings = self._deferred_warnings, []
        for title, text in pending:
            QMessageBox.warning(self, title, text)

    def _report_settings_problem(self, kind, message):
        """Tell the user their settings could not be read or written.

        Rate-limited per *kind* per session, deliberately. Autosave fires on
        every ``refresh_grid``, so one dialog per failure would be worse than
        the silence it replaces (ST-UI-009 measured 306-563 ms per refresh
        action), and one warning-log entry per failure would feed the unbounded
        ``warning_log._messages`` growth measured in ST-PERF-003.

        Synchronous by design: the message must have reached at least one
        channel by the time the caller returns. Only the modal is deferred,
        because ``_auto_load`` runs from ``__init__`` before ``_build_main()``
        has created the widgets it would otherwise need.
        """
        first_time = kind not in self._settings_problems
        self._settings_problems.add(kind)
        if not first_time:
            return
        # As above: _show_toast mirrors into the warning panel, so there is no
        # separate log write here.
        if getattr(self, "status_label", None) is None:
            # Called from _auto_load, which runs before _build_main() creates
            # the widgets. Stash it; __init__ flushes once the window exists.
            self._pending_settings_report = message
            return
        try:
            self._show_toast(message, "error")
        except Exception:
            pass
        self._deferred_warning(tr("status.settings_problem_title"), message)

    def _read_settings_container(self):
        """Read the settings container, distinguishing the three outcomes.

        ST-DATA-014: ``_auto_save`` used to do a read-modify-write whose read
        fell back to ``{}`` on ANY exception and then wrote that ``{}`` over the
        user's settings, so one unreadable load destroyed the saved schedule —
        and autosave runs on every refresh.

        absent        -> ``{}`` (first run)
        EguFileError  -> genuinely unreadable: quarantine the bytes, report, ``{}``
        anything else -> transient (a locked file, an I/O error): report and
                         re-raise. Quarantining a perfectly good file because the
                         disk hiccuped is data loss dressed up as recovery.
        """
        path = self._config_path
        if not os.path.exists(path):
            return {}
        try:
            data = storage.load_encrypted(path)
        except storage.EguFileError as exc:
            try:
                dst = storage.quarantine_corrupt(path)
            except Exception:
                self._report_settings_problem(
                    "unreadable", tr("errors.settings_unreadable").format(
                        path=path, err=exc))
                raise
            # settings_path() resolves a legacy .uva only while the .egu is
            # absent, so moving the file can change what it returns.
            self._config_path = storage.settings_path()
            self._report_settings_problem(
                "corrupt", tr("errors.settings_corrupt").format(
                    backup=dst, err=exc))
            return {}
        except Exception as exc:
            self._report_settings_problem(
                "unreadable", tr("errors.settings_unreadable").format(
                    path=path, err=exc))
            raise
        return data if isinstance(data, dict) else {}

    def _auto_load(self):
        if not os.path.exists(self._config_path):
            return False
        try:
            data = self._read_settings_container()
            if not data:
                return False
            self.state_data = data.get("state", new_state())
            normalize_state_day_keys(self.state_data)
            normalize_state_classes(self.state_data)
            if "lecturers" not in self.state_data:
                self.state_data["lecturers"] = []
            if "classroom_capacities" not in self.state_data:
                self.state_data["classroom_capacities"] = {}
            self.current_file = data.get("last_file", None)
            lang = data.get("language", "en")
            set_language(lang)
            if is_rtl(lang):
                QApplication.instance().setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            return bool(self.state_data.get("days"))
        except Exception:
            return False

    def _auto_save(self):
        """Persist state. Returns True on success.

        ST-DATA-005 / ST-DATA-014. Two rules, both learned the hard way:

        1. Never write a container we failed to READ. The old code fell back to
           ``data = {}`` and wrote it, so one bad read destroyed the schedule.
        2. Never raise. This runs from a Qt slot chain (``refresh_grid``) and
           from the ``closeEvent`` virtual; an exception there aborts the
           process under a real platform plugin. Failures are reported, not
           thrown.
        """
        try:
            data = self._read_settings_container()
        except Exception:
            return False  # already reported; do NOT overwrite an unreadable file
        try:
            normalize_state_day_keys(self.state_data)
            normalize_state_classes(self.state_data)
            data["state"] = self.state_data
            data["last_file"] = self.current_file
            data["language"] = get_language()
        except Exception as exc:
            self._report_settings_problem(
                "normalize", tr("errors.settings_write_failed").format(err=exc))
            return False
        try:
            storage.save_encrypted(data, self._config_path)
        except Exception as exc:
            self._report_settings_problem(
                "write", tr("errors.settings_write_failed").format(err=exc))
            return False
        # A successful write re-arms the reports, so a disk that recovers and
        # then fails again is not silently ignored for the rest of the session.
        self._settings_problems.clear()
        return True

    def _state_fingerprint(self):
        """A hash of exactly what would be written, or None if unhashable.

        ST-PERF-002. Two things here are load-bearing:

        1. The normalize calls. ``_auto_save`` mutates ``state_data`` on its way
           out, so a fingerprint taken without them never matches the one taken
           after a save and the delta check would never fire.
        2. It hashes the WHOLE payload. Cheaper fingerprints — class names, the
           class count, or ``state["classes"]`` alone — all look green and all
           silently drop real edits: a drag mutates one class dict in place
           (same count, same names) and a Setup room change touches
           ``state["classrooms"]`` and nothing else. Losing a user's edit to a
           performance optimisation is a far worse bug than the one being fixed.
        """
        try:
            normalize_state_day_keys(self.state_data)
            normalize_state_classes(self.state_data)
            payload = {
                "state": self.state_data,
                "last_file": self.current_file,
                "language": get_language(),
            }
            blob = json.dumps(payload, sort_keys=True, default=str,
                              ensure_ascii=False)
        except Exception:
            return None  # unhashable: fall through to a real write attempt
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def request_auto_save(self):
        """Ask for a save soon. Repeated calls coalesce into one write."""
        self._autosave_pending = True
        # start() on a running single-shot timer restarts it — that IS the
        # coalescing: a burst of refreshes produces exactly one write.
        self._autosave_timer.start(AUTOSAVE_DEBOUNCE_MS)

    def flush_auto_save(self):
        """Write now if anything actually changed. Returns True if state is safe.

        Called from the debounce timer, from closeEvent, and before any path
        that rebinds ``state_data`` — the timer reads that attribute live, so a
        pending write must not be allowed to land against a different schedule.
        """
        self._autosave_timer.stop()
        fp = self._state_fingerprint()
        if fp is not None and fp == self._autosave_fingerprint:
            self._autosave_pending = False
            return True  # byte-identical to what is already on disk
        if not self._autosave_pending and self._autosave_fingerprint is not None:
            return True
        ok = self._auto_save()
        if ok:
            self._autosave_pending = False
            self._autosave_fingerprint = fp
        # A failed write deliberately leaves _autosave_pending set and does NOT
        # re-arm the timer: it is retried on the next refresh or on close, which
        # is the same reach the un-debounced version had.
        return ok

    def _update_status(self):
        """Render the status bar from the one placement vocabulary (ST-UI-002).

        This used to compute ``n_unplaced = n_total - n_pinned - n_placed``,
        which double-subtracts a class carrying both flags, and to render pinned
        as a segment BESIDE placed. So its numbers summed to more than the class
        count whenever a pin was also placed — ``4 sabit + 77 yerleşti + 3
        yerleşmedi`` against 80 — and to a negative unplaced count when enough
        of them were. Pinned is a subset annotation now, because that is what it
        is, and the count comes from the same function the dashboard card
        calls. (`BulkResultsDialog` deliberately does NOT: it reports the
        OPERATION — "this run placed 56 of the 60 you asked for" — and is shown
        BEFORE the user accepts, so a state trio rendered there would describe
        the pre-solve timetable.)
        """
        s = self.state_data
        counts = schedule_counts(s)
        fname = os.path.basename(self.current_file) if self.current_file else tr("app.untitled")
        placed_txt = f"{counts['scheduled']} {tr('status.placed')}"
        if counts["pinned_of_scheduled"]:
            placed_txt += (
                f"  \U0001F4CC "
                + tr("status.pinned_subset").format(
                    n=counts["pinned_of_scheduled"]))
        status = (
            f"\U0001F4C4 {fname}   \u2502   "
            f"\U0001F4DA {counts['total']} {tr('status.classes')}   \u2502   "
            f"\u2705 {placed_txt}   \u2502   "
            f"\u23F3 {counts['unscheduled']} {tr('status.unplaced')}")
        if counts["protected_of_scheduled"]:
            status += (
                f"   \u2502   \U0001F6E1 {counts['protected_of_scheduled']} "
                f"{tr('labels.protected')}")
        if counts["off_grid_of_scheduled"]:
            # ST-DATA-003. These are scheduled but drawn nowhere, and the
            # unplaced panel excludes them too (same `not placed and not
            # pinned` predicate), so without this line the user reads a placed
            # count higher than the number of lessons on the grid and has no
            # way to find the difference.
            status += (
                f"   \u2502   \u26A0 "
                + tr("status.off_grid_subset").format(
                    n=counts["off_grid_of_scheduled"]))
        self.status_label.setText(status)

    # ── Filter updates ────────────────────────────────────────────────────

    def _update_filters(self):
        s = self.state_data

        # ST-UI-006: rebuilt from the live year list, so it can never claim a
        # mapping the palette does not have. Cheap -- it early-returns unless
        # the year list itself changed.
        if hasattr(self, "year_legend"):
            self.year_legend.update_years(s)

        self.classroom_filter.blockSignals(True)
        cur = self.classroom_filter.currentData()
        if cur is None:
            cur = self.classroom_filter.currentText().strip() or None
        self.classroom_filter.clear()
        for room in s["classrooms"]:
            self.classroom_filter.addItem(room, _encode_classroom_filter_room(room))
        for location_type in (LOCATION_ONLINE, LOCATION_LECTURER_OFFICE):
            self.classroom_filter.addItem(
                get_location_label(location_type),
                _encode_classroom_filter_virtual(location_type),
            )
        idx = self.classroom_filter.findData(cur)
        if idx < 0 and isinstance(cur, str):
            idx = self.classroom_filter.findText(cur)
        self.classroom_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.classroom_filter.blockSignals(False)

        groups = []
        for yr in sorted(s["years"].keys()):
            for br in s["years"][yr]:
                groups.append(f"{yr} / {br}")
        self.group_filter.blockSignals(True)
        cur = self.group_filter.currentText()
        self.group_filter.clear()
        self.group_filter.addItems(groups)
        idx = self.group_filter.findText(cur)
        self.group_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.group_filter.blockSignals(False)

        # Build lecturer filter: use defined list + any from classes
        lec_set = set(s.get("lecturers", []))
        if s["classes"]:
            lec_set.update(c["lecturer"] for c in s["classes"] if c["lecturer"])
        all_lecs = sorted(lec_set)
        self.lecturer_filter.blockSignals(True)
        cur = self.lecturer_filter.currentText()
        self.lecturer_filter.clear()
        self.lecturer_filter.addItems(all_lecs)
        idx = self.lecturer_filter.findText(cur)
        self.lecturer_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.lecturer_filter.blockSignals(False)

    def _check_setup(self):
        """Prompt for setup when schedule has no basic config (e.g. after New)."""
        s = self.state_data
        if not s["days"] or not s["slots"] or not s["classrooms"] or not s["years"]:
            if QMessageBox.question(
                    self, tr("dialogs.welcome.title"),
                    tr("dialogs.welcome.setup_prompt")
            ) == QMessageBox.StandardButton.Yes:
                self.edit_setup()

    def _switch_tab(self, idx):
        self.notebook.setCurrentIndex(idx)

    # ── Zoom ──────────────────────────────────────────────────────────

    def _set_zoom(self, pct):
        """Apply zoom level to all timetable views and sync the slider."""
        pct = max(25, min(300, pct))
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(pct)
        self._zoom_slider.blockSignals(False)
        self._zoom_label.setText(tr("labels.percent_value", value=pct))
        for view in [self.grid_view1, self.grid_view2,
                     self.grid_view3, self.grid_view4]:
            view.set_zoom(pct)

    def _on_view_zoom_changed(self, pct):
        """Callback from TimetableView Ctrl+Wheel zoom — sync slider."""
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(pct)
        self._zoom_slider.blockSignals(False)
        self._zoom_label.setText(tr("labels.percent_value", value=pct))
        # Sync other views to same zoom
        for view in [self.grid_view1, self.grid_view2,
                     self.grid_view3, self.grid_view4]:
            if view.zoom_pct() != pct:
                view.set_zoom(pct)

    # ══════════════════════════════════════════════════════════════════════
    #  GRID RENDERING  (QGraphicsView-based)
    # ══════════════════════════════════════════════════════════════════════

    def refresh_grid(self):
        """Full refresh: render + update all panels + request an auto-save.

        ST-PERF-002: this used to decrypt, re-encrypt and rewrite the whole
        settings container on every call — 16.8 ms at 80 classes, 33.6 ms at
        250, on an action as ordinary as clicking a lesson.
        """
        self._render_current_tab()
        self._update_side_panels()
        self.request_auto_save()

    def _render_current_tab(self):
        """Render only the visible timetable tab (no side effects)."""
        if not hasattr(self, 'lecturer_filter'):
            return  # Still building UI
        # Scene items are rebuilt every refresh; clear stale graphics selections.
        self._clear_class_selection()
        self._clear_empty_slot_selection()
        self._update_filters()
        self._update_status()

        # ST-UI-001: this sweep feeds whichever timetable tab is being drawn.
        # The warning log runs its OWN sweep in `_conflict_log_entries`, on
        # purpose — `_run_auto_negotiation` can move classes between the two,
        # so reusing this result there would describe a timetable that no
        # longer exists. (An earlier version of this comment claimed one sweep
        # served both and therefore guaranteed they agree. There are two, and
        # the second one is why they agree.)
        #
        # Measured at ~1.5 ms on a fully-placed 250-class grid and 9 ms at 600,
        # against the 306-563 ms repaint ST-UI-009 was about, so neither is
        # memoised.
        self._conflicts = find_schedule_conflicts(self.state_data)
        self._conflict_partners = conflict_partner_index(self._conflicts)

        tab_idx = self.notebook.currentIndex()
        if tab_idx == 0:
            self._render_grid(
                self.grid_view1,
                self._filter_classroom,
                mode=self._filtered_render_mode(tab_idx),
            )
        elif tab_idx == 1:
            self._render_grid(
                self.grid_view2,
                self._filter_group,
                mode=self._filtered_render_mode(tab_idx),
            )
        elif tab_idx == 2:
            self._render_grid(
                self.grid_view3,
                self._filter_lecturer,
                mode=self._filtered_render_mode(tab_idx),
            )
        elif tab_idx == 3:
            self._render_everything(self.grid_view4)
        elif tab_idx == 4:
            self.dashboard_widget.refresh(self.state_data)

    def _update_side_panels(self):
        """Update unplaced panel, open slots, and warnings."""
        if not hasattr(self, 'lecturer_filter'):
            return
        self._refresh_unplaced_panel()
        self._refresh_open_slots()
        self._refresh_warnings()

    def _filter_classroom(self, cls):
        selected = self.classroom_filter.currentData()
        kind, value = _decode_classroom_filter_value(selected)
        if kind == "virtual":
            return cls.get("location_type", LOCATION_FACE_TO_FACE) == value
        if kind == "room":
            return classroom_of(cls) == value
        return classroom_of(cls) == self.classroom_filter.currentText()

    def _filter_group(self, cls):
        group = self.group_filter.currentText()
        if " / " not in group:
            return False
        yr, br = group.split(" / ", 1)
        return any(t["year"] == yr and t["branch"] == br for t in cls["targets"])

    def _filter_lecturer(self, cls):
        return cls["lecturer"] == self.lecturer_filter.currentText()

    def _filtered_render_mode(self, tab_idx):
        """Return the renderer mode for the active filtered timetable tab."""
        selected = self.classroom_filter.currentData()
        kind, value = _decode_classroom_filter_value(selected)
        if tab_idx == 0 and kind == "virtual" and is_virtual_location_type(value):
            return FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP
        return FILTER_MODE_DEFAULT

    def _render_grid(self, view, filter_fn, mode=FILTER_MODE_DEFAULT):
        """Build a filtered timetable in the given TimetableView."""
        scene = TimetableScene()
        scene.build_filtered(
            self.state_data, filter_fn, self, mode=mode,
            conflict_partners=getattr(self, "_conflict_partners", None))
        view.setScene(scene)

    def _render_everything(self, view):
        """Build the 'Show Everything' matrix in the given TimetableView."""
        scene = TimetableScene()
        scene.build_everything(
            self.state_data, self,
            conflict_partners=getattr(self, "_conflict_partners", None))
        view.setScene(scene)

    # ══════════════════════════════════════════════════════════════════════
    #  UNPLACED PANEL
    # ══════════════════════════════════════════════════════════════════════

    def _unplaced_class_by_uid(self):
        """Live uid -> class dict map for the unplaced sidebar.

        ST-ARCH-015. Built from ``state_data["classes"]`` at read time, so it
        cannot go stale the way a stored position can.
        """
        return {cls_key(c): c for c in self.state_data["classes"]}

    def _refresh_unplaced_panel(self):
        self.unplaced_list.clear()
        self._unplaced_uids = []
        for c in self.state_data["classes"]:
            if not c["pinned"] and not c["placed"]:
                code = c.get("class_code", "")
                label = f"[{code}] {c['name']}" if code else c["name"]
                self.unplaced_list.addItem(f"{label}  ({c['lecturer']})")
                self._unplaced_uids.append(cls_key(c))

    def _switch_sidebar_tab(self, index):
        """Switch sidebar tab (0 = Open Slots, 1 = Unplaced Classes)."""
        self._sidebar_current_tab = index
        self._sidebar_stack.setCurrentIndex(index)
        # Update tab button styles
        if index == 0:
            self._sidebar_tab_open_slots.setStyleSheet(
                self._sidebar_tab_active_ss)
            self._sidebar_tab_unplaced.setStyleSheet(
                self._sidebar_tab_inactive_ss)
            self._sidebar_title.setText(tr("panels.open_slots"))
        else:
            self._sidebar_tab_open_slots.setStyleSheet(
                self._sidebar_tab_inactive_ss)
            self._sidebar_tab_unplaced.setStyleSheet(
                self._sidebar_tab_active_ss)
            self._sidebar_title.setText(tr("panels.unplaced_classes"))

    def _toggle_sidebar_panel(self, checked):
        """Toggle entire sidebar visibility from View menu."""
        if checked:
            self._expand_panel("sidebar", by_user=True)
        else:
            self._collapse_panel("sidebar", by_user=True)

    def _init_splitter_sizes(self):
        """Set initial splitter sizes once the window is laid out."""
        total = self.splitter.width()
        sw = 350
        nb = total - sw
        if nb < 400:
            nb = 400
        # This setSizes() emits splitterMoved exactly as a drag does, and a
        # startup layout is not the user saying anything about the sidebar.
        self._in_collapse_sync = True
        try:
            self.splitter.setSizes([nb, sw])
        finally:
            self._in_collapse_sync = False
        # ST-UI-013: decide once, here, rather than waiting for the first
        # resize the user happens to perform.
        self._apply_sidebar_intent()

    def _on_splitter_moved(self):
        """Detect when a splitter drag shrinks the sidebar below threshold."""
        if self._in_collapse_sync:
            return
        sizes = self.splitter.sizes()
        thresh = self._collapse_threshold
        if not self._sidebar_is_collapsed and sizes[1] <= thresh:
            self._sidebar_saved_width = max(self._sidebar_saved_width, 150)
            self._in_collapse_sync = True
            # Dragging the handle shut is the user closing the sidebar, and it
            # has to stick for the same reason the button does.
            self._collapse_panel("sidebar", by_user=True)
            self._in_collapse_sync = False
        elif not self._sidebar_is_collapsed and sizes[1] > thresh:
            self._sidebar_saved_width = sizes[1]

    def _collapse_panel(self, which, *, by_user=False):
        """Collapse the sidebar by shrinking it in the splitter.

        ``by_user`` records *whose* decision this was. Only a person's decision
        becomes an intent that outranks the window width (ST-UI-013).
        """
        if which != "sidebar":
            return
        if by_user:
            self._sidebar_intent = "closed"
        if self._sidebar_is_collapsed:
            return
        cw = self._collapsed_width
        sizes = self.splitter.sizes()
        if sizes[1] > self._collapse_threshold:
            self._sidebar_saved_width = max(sizes[1], 150)
        self._sidebar_is_collapsed = True
        # Hide sidebar contents
        self._sidebar_title.setVisible(False)
        self._sidebar_collapse_btn.setVisible(False)
        self._sidebar_tab_open_slots.setVisible(False)
        self._sidebar_tab_unplaced.setVisible(False)
        self._sidebar_stack.setVisible(False)
        self._sidebar_panel.setStyleSheet(
            "QWidget#sidebar { background: transparent; }")
        # Show overlay expand button
        self._sidebar_expand_btn.setVisible(True)
        self._sidebar_panel.setFixedWidth(cw)
        reclaimed = sizes[1] - cw
        sizes[0] += reclaimed
        sizes[1] = cw
        self.splitter.setSizes(sizes)
        self._toggle_sidebar_action.setChecked(False)
        self._update_collapsed_handles()
        self._position_expand_buttons()

    def _expand_panel(self, which, *, by_user=False):
        """Expand the collapsed sidebar back to its previous width.

        Note for anyone adding a width cap here later: the two lines below that
        reset ``setMinimumWidth(0)`` / ``setMaximumWidth(16777215)`` throw any
        such cap away on the first expand, so it has to be re-applied.
        """
        if which != "sidebar":
            return
        if by_user:
            self._sidebar_intent = "open"
        if not self._sidebar_is_collapsed:
            return
        sizes = self.splitter.sizes()
        self._sidebar_is_collapsed = False
        self._sidebar_expand_btn.setVisible(False)
        # Restore sidebar contents
        self._sidebar_title.setVisible(True)
        self._sidebar_collapse_btn.setVisible(True)
        self._sidebar_tab_open_slots.setVisible(True)
        self._sidebar_tab_unplaced.setVisible(True)
        self._sidebar_stack.setVisible(True)
        self._sidebar_panel.setStyleSheet(
            "QWidget#sidebar { background: #F8FAFC; border-radius: 8px; }")
        # Remove fixed-width constraint
        self._sidebar_panel.setMinimumWidth(0)
        self._sidebar_panel.setMaximumWidth(16777215)
        w = self._sidebar_saved_width
        sizes[0] = max(sizes[0] - w + sizes[1], 100)
        sizes[1] = w
        self.splitter.setSizes(sizes)
        self._toggle_sidebar_action.setChecked(True)
        self._update_collapsed_handles()

    def _position_expand_buttons(self):
        """Position overlay expand button at the top of the sidebar."""
        if not self._sidebar_is_collapsed:
            return
        top_y = 6
        btn_w = 28
        x = (self._sidebar_panel.width() - btn_w) // 2
        self._sidebar_expand_btn.move(max(x, 0), top_y)
        self._sidebar_expand_btn.raise_()

    def _update_collapsed_handles(self):
        """Hide splitter handle when sidebar is collapsed."""
        for i in range(1, self.splitter.count()):
            handle = self.splitter.handle(i)
            if not handle:
                continue
            if self._sidebar_is_collapsed:
                handle.setFixedWidth(0)
            else:
                handle.setMinimumWidth(0)
                handle.setMaximumWidth(16777215)
                handle.resize(self.splitter.handleWidth(), handle.height())

    # ── The window fits the screen, the sidebar fits the grid (ST-UI-013) ──

    def _splitter_handle_width(self):
        """The live handle width. ``handleWidth()`` is -1 under Fusion."""
        handle = self.splitter.handle(1) if self.splitter.count() > 1 else None
        if handle is not None and handle.width() > 0:
            return handle.width()
        return max(self.splitter.handleWidth(), 0)

    def _grid_content_width(self):
        """Notebook width that would show the visible timetable whole.

        Read straight off the scene that is already on screen. This runs from
        ``resizeEvent``, which fires on every frame of a window drag, so it must
        never re-render anything — ``sceneRect()`` is current by the time any
        resize arrives.
        """
        views = [self.grid_view1, self.grid_view2,
                 self.grid_view3, self.grid_view4]
        idx = self.notebook.currentIndex()
        view = views[idx] if 0 <= idx < len(views) else self.grid_view1
        scene = view.scene()
        if scene is None:
            return 0
        zoom = getattr(view, "_zoom_pct", 100) or 100
        scene_w = scene.sceneRect().width() * zoom / 100.0
        # Everything between the notebook's own edge and the viewport the scene
        # is painted into: tab-widget frame, page margins, vertical scrollbar.
        chrome = max(self.notebook.width() - view.viewport().width(), 0)
        return int(round(scene_w + chrome))

    def _sidebar_open_width(self):
        """The width the sidebar will actually take when it is open.

        ``_sidebar_saved_width`` is only what we would *ask* for. The splitter
        floors it at the panel's own ``minimumSizeHint``, which is
        locale-dependent — ``12 + minSizeHint(open-slots) + 4 +
        minSizeHint(unplaced)``, both buttons bold, padded and emoji-prefixed,
        giving ko 210 ... tr 301 ... ru 362 natively — and exceeds the
        hard-coded 350 in pl and ru, where it silently widens the sidebar.

        Measured cost of using the remembered number instead: switching to
        Azerbaijani after Arabic decided with 350 against a panel that then
        took 564, and the sidebar kept 201 px the tab bar needed.

        The hint reads as the layout's minimum, so it only means anything while
        the panel's contents are visible; collapsed, the remembered width is
        the best estimate there is, and ``_apply_sidebar_intent`` re-checks
        once the panel is open again.
        """
        return max(self._sidebar_saved_width,
                   self._sidebar_panel.minimumSizeHint().width())

    def _sidebar_needed_width(self):
        """Splitter width at which the sidebar costs the user nothing.

        Two things compete for the notebook and the wider one wins:

        *The tab bar.* It does not elide (``ElideNone``); a tab that does not
        fit is a tab behind a scroll arrow, and the Quality Dashboard is the
        last one. Its size hint spans 319 px across the 22 shipped locales —
        ko 913 to id 1232 once the 359 px of sidebar and chrome are added — so
        any constant here is 48 px short in Turkish and 132 short in
        Indonesian.

        *The timetable.* 5 days x 8 periods wants 1212 px with the sidebar
        open and 898 with it closed; 6 x 10 wants 1377 and 1063.

        Both are measured live, which is what makes this locale-correct and
        day-count-correct by construction rather than by a table someone has to
        maintain.
        """
        tab_w = self.notebook.tabBar().sizeHint().width()
        return (max(tab_w, self._grid_content_width())
                + self._sidebar_open_width() + self._splitter_handle_width())

    def _apply_sidebar_intent(self):
        """Give the grid the sidebar's 314 px when it needs them — if allowed.

        Does nothing at all unless nobody has expressed an intent. The measured
        cost of getting that wrong: with a bare breakpoint, expanding the
        sidebar at 1000 px survives exactly until the window moves one pixel.
        """
        if self._sidebar_intent != "auto":
            return
        if self._in_collapse_sync or not self.isVisible():
            return
        # A window that has never been shown reports a splitter at its own
        # 640 px size hint no matter what resize() was called with, so nothing
        # measured off it is worth acting on.
        total = self.splitter.width()
        if total <= self._collapsed_width * 4:
            return  # not laid out yet — _init_splitter_sizes has not run
        needed = self._sidebar_needed_width()
        if not self._sidebar_is_collapsed:
            if total < needed:
                self._collapse_panel("sidebar")
        elif total > needed + 2 * self._splitter_handle_width() \
                + SIDEBAR_REOPEN_HYSTERESIS:
            self._expand_panel("sidebar")
            # How wide the panel insists on being is only readable once its
            # contents are visible, so the estimate that opened it may have
            # been the previous language's. Re-check exactly once; the branch
            # above cannot run again from here.
            if self.splitter.width() < self._sidebar_needed_width():
                self._collapse_panel("sidebar")

    @staticmethod
    def _rect_is_on_a_screen(frame, screen_rects,
                             minimum=MIN_ON_SCREEN_FRACTION):
        """True when enough of *frame* lands on one of *screen_rects*.

        A geometry saved while a second monitor was plugged in restores to
        coordinates no screen covers once it is unplugged, and the window opens
        where nobody can reach it. Each screen is measured separately and the
        best one wins: a frame straddling two monitors is on screen once, not
        half on screen twice.
        """
        area = frame.width() * frame.height()
        if area <= 0:
            return False
        best = 0
        for rect in screen_rects:
            overlap = frame.intersected(rect)
            best = max(best, overlap.width() * overlap.height())
        return best >= area * minimum

    def _available_screen_rects(self):
        app = QApplication.instance()
        if app is None:
            return []
        return [screen.availableGeometry() for screen in app.screens()]

    def _restore_window_geometry(self):
        """Reopen where the user left the window, or maximized on a first run.

        The alternative, and what shipped until now, is a hard-coded 1150x720
        on every launch on every machine — measured to put an 841x607 timetable
        into a 769x457 viewport in Turkish at 5 days x 8 periods, and to clear
        the Turkish tab bar by exactly 0 px while failing id, pl and ru.
        """
        try:
            data = self._read_settings_container() or {}
        except Exception:
            data = {}     # already reported; a bad container costs a size only
        intent = data.get(SIDEBAR_INTENT_KEY)
        if intent in ("auto", "open", "closed"):
            self._sidebar_intent = intent
        restored = False
        blob = data.get(WINDOW_GEOMETRY_KEY)
        if isinstance(blob, str) and blob:
            try:
                restored = bool(self.restoreGeometry(base64.b64decode(blob)))
            except Exception:
                restored = False   # truncated, re-encoded, hand-edited: ignore
        if restored and not self._rect_is_on_a_screen(
                self.frameGeometry(), self._available_screen_rects()):
            restored = False
        if not restored:
            self.resize(DEFAULT_WINDOW_W, DEFAULT_WINDOW_H)
            self.setWindowState(
                self.windowState() | Qt.WindowState.WindowMaximized)

    def _save_window_geometry(self):
        """Remember the window and the sidebar decision for the next launch."""
        from scheduler_app.first_run import _write_flag
        try:
            blob = base64.b64encode(bytes(self.saveGeometry())).decode("ascii")
        except Exception:
            return False
        ok = _write_flag(self._config_path, WINDOW_GEOMETRY_KEY, blob)
        ok = _write_flag(self._config_path, SIDEBAR_INTENT_KEY,
                         self._sidebar_intent) and ok
        return ok

    def _on_unplaced_dblclick(self, item):
        row = self.unplaced_list.row(item)
        # ST-ARCH-015: identity, not position — see _classes_from_rows.
        picked = self.unplaced_list._classes_from_rows([row])
        if not picked:
            return
        cls = picked[0]
        dlg = PostAddDialog(self, self.state_data, cls)
        if dlg.exec() == PostAddDialog.DialogCode.Accepted:
            if dlg.result and dlg.result != "skip":
                day, slot, room = dlg.result
                mark_placed(cls, day, slot, room)
                self._show_toast(tr("status.class_placed"), "success")
                self.refresh_grid()

    # ══════════════════════════════════════════════════════════════════════
    #  FILE OPERATIONS
    # ══════════════════════════════════════════════════════════════════════

    def _flush_before_state_swap(self):
        """Land any pending write before state_data points somewhere else.

        The debounce timer reads ``self.state_data`` when it fires, so without
        this the pending write would persist the NEW schedule and the previous
        one's last edit would never reach disk.
        """
        self.flush_auto_save()

    def new_schedule(self):
        if QMessageBox.question(
                self, tr("menus.file_new"),
                tr("dialogs.new_schedule.confirm")
        ) == QMessageBox.StandardButton.Yes:
            # ST-ARCH-011/ST-PERF-002: land the pending debounced write BEFORE
            # state_data points somewhere else. This guard was written and
            # never called; without it an edit made inside the 1.5 s window and
            # followed by File > New is lost, because the timer fires later and
            # persists the empty schedule instead.
            self._flush_before_state_swap()
            self.state_data = new_state()
            self._workflow.state = self.state_data
            self.current_file = None
            self._has_baseline = False
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._clear_impact_flags()
            self.refresh_grid()
            self._check_setup()

    def open_file(self):
        fname, _ = QFileDialog.getOpenFileName(
            self, tr("dialogs.file.open_title"), storage.sub_dir(storage.SAVES_DIR),
            f"{tr('labels.dersis_files')} (*.egu);;{tr('labels.legacy_schedule_files')} (*.uva);;{tr('labels.all_files')} (*)")
        if not fname:
            return
        is_legacy = fname.lower().endswith(".uva")
        # Same swap hazard as new_schedule: flush before rebinding, or the
        # previous schedule's last edit is written onto the opened one.
        self._flush_before_state_swap()
        try:
            self.state_data = storage.load_encrypted(fname)
            self._workflow.state = self.state_data
            normalize_state_day_keys(self.state_data)
            normalize_state_classes(self.state_data)
            # Ensure backward compat for older files missing keys
            if "lecturers" not in self.state_data:
                self.state_data["lecturers"] = []
            if "classroom_capacities" not in self.state_data:
                self.state_data["classroom_capacities"] = {}
            self.current_file = fname
            self._undo_stack.clear()
            self._redo_stack.clear()
            self.mark_current_state_as_baseline()
            self.refresh_grid()
            if is_legacy:
                QMessageBox.information(
                    self, tr("dialogs.file.legacy_title"),
                    tr("dialogs.file.legacy_migration_note"))
        except storage.EguFileError as e:
            QMessageBox.warning(self, tr("dialogs.error.title"), str(e))
        except Exception as e:
            QMessageBox.critical(self, tr("dialogs.error.title"), f"{tr('errors.failed_to_load')}\n{e}")

    def save_file(self):
        if self.current_file:
            # Auto-migrate legacy .uva → .egu on save
            if self.current_file.lower().endswith(".uva"):
                self.current_file = self.current_file[:-4] + ".egu"
            self._do_save(self.current_file)
        else:
            self.save_as()

    def save_as(self):
        # Tier enforcement: check schedule limit when saving as a new file
        if not self.current_file:
            from scheduler_app.ui.tier_enforcement import TierEnforcement
            from scheduler_app.plans import ENTITY_SCHEDULES
            enforcer = TierEnforcement.instance()
            saves_dir = storage.sub_dir(storage.SAVES_DIR)
            import glob as _glob
            existing = len(_glob.glob(os.path.join(saves_dir, "*.egu")))
            if not enforcer.require_entity_limit(ENTITY_SCHEDULES, existing, self):
                return

        default_name = storage.new_save_path()
        fname, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.file.save_title"), default_name,
            f"{tr('labels.dersis_files')} (*.egu);;{tr('labels.all_files')} (*)")
        if fname:
            self._do_save(fname)

    def _do_save(self, fname):
        normalize_state_day_keys(self.state_data)
        normalize_state_classes(self.state_data)
        try:
            storage.save_encrypted(self.state_data, fname)
            self.current_file = fname
            self._update_status()
        except Exception as e:
            QMessageBox.critical(self, tr("dialogs.error.title"), f"{tr('errors.failed_to_save')}\n{e}")

    def export_csv(self):
        # Tier enforcement: CSV export requires feature
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        from scheduler_app.plans import FEATURE_EXPORT_CSV
        if not TierEnforcement.instance().require_feature(FEATURE_EXPORT_CSV, self):
            return
        placed = get_placed_classes(self.state_data)
        if not placed:
            QMessageBox.information(self, tr("buttons.export"), tr("warnings.no_placed_to_export"))
            return
        fname, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.export.csv_title"), storage.sub_dir(storage.EXPORTS_DIR),
            f"{tr('labels.csv_files')} (*.csv);;{tr('labels.all_files')} (*)")
        if not fname:
            return
        try:
            # ST-FUNC-006. This is the CSV a user actually gets -- the menu is
            # wired here, not to data_io.export_schedule(..., "csv", ...),
            # which has no production caller at all. Both halves of the finding
            # lived on these two lines and nowhere else:
            #
            #   * no `encoding=`, so the file was written in the OS codepage.
            #     Measured on this machine: locale.getpreferredencoding(False)
            #     is cp1254, so colleagues abroad got mojibake -- and on a
            #     cp1252 host "Işık Öğretmen".encode() raises
            #     UnicodeEncodeError, which the bare `except Exception` below
            #     turns into an unexplained "export failed".
            #   * the raw internal day key ("monday") in a Turkish-headed file.
            #
            # utf-8-sig because the file is opened in Excel, which reads a
            # BOM-less UTF-8 CSV in the local codepage; the suite's own
            # read_csv_rows already decodes with utf-8-sig.
            with open(fname, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([tr("labels.class_item"), tr("labels.lecturer"), tr("labels.year"), tr("labels.branch"),
                                 tr("labels.day"), tr("labels.start_time"), tr("labels.duration"), tr("labels.classroom")])
                for c in placed:
                    # display_day rather than tr("weekdays.<key>"): it falls
                    # back to the stored value verbatim for a day the grid no
                    # longer defines, instead of printing the lookup key.
                    day = display_day(effective_day(c))
                    start = effective_time(c)
                    room = classroom_of(c)
                    # ST-FUNC-013. `targets` is [] for every freshly created
                    # class (core/models.py:578) and no class editor requires
                    # one, so iterating it alone wrote NO row for a lesson the
                    # user had placed -- the same silent drop the PDF had, in
                    # the file that gets emailed. A flat file has room for it.
                    for t in c["targets"] or [{"year": "", "branch": ""}]:
                        # ST-UI-008. The .csv is emailed to colleagues and a
                        # leading "=" is executed by their spreadsheet. Safe to
                        # prefix here and ONLY here: nothing in DERSIS reads a
                        # CSV back (zero csv.reader / read_csv call sites, and
                        # the import filters offer only *.xlsx).
                        writer.writerow([
                            csv_safe(c["name"]), csv_safe(c["lecturer"]),
                            csv_safe(t["year"]), csv_safe(t["branch"]),
                            day, csv_safe(start), c["duration"],
                            csv_safe(room)])
            QMessageBox.information(self, tr("status.exported"),
                                   f"{tr('status.exported_to')} {os.path.basename(fname)}")
        except Exception as e:
            QMessageBox.critical(self, tr("dialogs.error.title"), f"{tr('errors.failed_to_export')}\n{e}")

    # ══════════════════════════════════════════════════════════════════════
    #  EDIT OPERATIONS
    # ══════════════════════════════════════════════════════════════════════

    def _get_learned_weights(self):
        """Return the current learned scoring weights."""
        return self._preference_learner.get_weights()

    def _auto_place_and_apply(self, cls):
        """Automatically place a class using AI-assisted optimization.

        Delegates to SchedulingWorkflow.auto_place() for business logic.
        Returns True if the class was placed.
        """
        result = self._workflow.auto_place(cls)

        if not result.success:
            return False

        if result.relocated:
            lines = []
            for r in result.relocated:
                lines.append(
                    f"  {r['name']}: "
                    f"{format_day_time(r['old_day'], r['old_time'])} ({r['old_room']}) "
                    f"-> {format_day_time(r['new_day'], r['new_time'])} ({r['new_room']})")
            msg = tr("status.class_placed_relocated") + "\n\n" + "\n".join(lines)
            if result.explanation:
                msg += "\n\n" + tr("labels.placement_quality") + " " + result.explanation["summary"]
            QMessageBox.information(
                self, tr("dialogs.reorganized.title"), msg)
        elif result.explanation:
            self._show_toast(
                tr("status.class_placed") + " — " +
                result.explanation["summary"], "success")

        return True

    def add_class(self):
        if not self.state_data["years"]:
            QMessageBox.information(self, tr("menus.classes"),
                                   tr("errors.setup_years_first"))
            return
        # Tier enforcement: check class limit
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        from scheduler_app.plans import ENTITY_CLASSES
        enforcer = TierEnforcement.instance()
        current_count = len(self.state_data.get("classes", []))
        if not enforcer.require_entity_limit(ENTITY_CLASSES, current_count, self):
            return
        dlg = AddClassDialog(self, self.state_data)
        if dlg.exec() != AddClassDialog.DialogCode.Accepted or not dlg.result:
            return
        cls = dlg.result
        # ST-UI-020: the lecturer combo is editable, so this name may be
        # one the user just typed. Register it BEFORE the undo snapshot,
        # or undo restores a class pointing at a lecturer the restored
        # state does not list -- and the next Setup OK unplaces it.
        cls["lecturer"] = SchedulingWorkflow.register_lecturer(
            self.state_data, cls.get("lecturer")) or ""
        snap_before = capture_snapshot(self.state_data)
        self._push_undo(tr("actions.add").format(name=cls["name"]))
        split_classes = split_non_joint(cls)
        if self._schedule_new_classes(split_classes):
            desc = tr("impact.trigger.classes_added").format(n=len(split_classes))
            self._note_structural_change(snap_before, description=desc)
        self.refresh_grid()

    def bulk_add_classes(self):
        # Tier enforcement: bulk scheduling requires feature
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        from scheduler_app.plans import ENTITY_CLASSES, FEATURE_BULK_SCHEDULING
        enforcer = TierEnforcement.instance()
        if not enforcer.require_feature(FEATURE_BULK_SCHEDULING, self):
            return
        if not self.state_data["years"]:
            QMessageBox.information(self, tr("dialogs.bulk_add.title"),
                                   tr("errors.setup_years_first"))
            return
        # Tier enforcement: check class limit before bulk add
        current_count = len(self.state_data.get("classes", []))
        if not enforcer.require_entity_limit(ENTITY_CLASSES, current_count, self):
            return
        dlg = BulkAddDialog(self, self.state_data)
        if dlg.exec() != BulkAddDialog.DialogCode.Accepted or not dlg.result:
            return

        raw_classes = dlg.result
        new_classes = []
        for rc in raw_classes:
            new_classes.extend(split_non_joint(rc))

        snap_before = capture_snapshot(self.state_data)
        self._push_undo(tr("actions.bulk_schedule"))
        if self._schedule_new_classes(new_classes):
            desc = tr("impact.trigger.classes_added").format(n=len(new_classes))
            self._note_structural_change(snap_before, description=desc)
        self.refresh_grid()

    def _schedule_new_classes(self, new_classes):
        """Unified scheduling for both single and bulk class addition.

        Delegates to SchedulingWorkflow for business logic.
        Shows BulkResultsDialog for user confirmation.
        On rejection, rolls back all changes.
        """
        if not new_classes:
            return False

        existing_snapshots = snapshot_placements(self.state_data)
        result = self._workflow.schedule_new_classes(new_classes)

        # All new classes placed successfully — no dialog needed
        if result.single_success:
            self._show_toast(tr("status.class_placed"), "success")
            return True

        # Single-class failed — show negotiation hints
        if result.single_failed:
            cls = new_classes[0]
            report = result.negotiation_report or {}
            suggestions = report.get("suggestions", [])
            if cls["pinned"]:
                detail = ""
                if suggestions:
                    detail = "\n\n" + report.get("summary", "")
                    for s in suggestions[:3]:
                        detail += f"\n  → {s['description']}"
                QMessageBox.warning(
                    self, tr("dialogs.error.title"),
                    tr("errors.no_valid_arrangement") + detail)
                return False
            else:
                msg = tr("status.class_added_no_slot")
                if suggestions:
                    msg += f"\n{suggestions[0]['description']}"
                self._show_toast(msg, "info")
                if hasattr(self, 'warning_log') and suggestions:
                    for sug in suggestions[:3]:
                        self.warning_log.log(
                            f"{cls['name']}: {sug['description']}", "info")
                return True  # class stays in state (unplaced)

        # Multi-class or rescheduled — show results dialog
        results_dlg = BulkResultsDialog(
            self, result.placed, result.unplaced, result.rescheduled)
        accepted = (results_dlg.exec() == BulkResultsDialog.DialogCode.Accepted
                    and results_dlg.result)

        if accepted:
            self._workflow.apply_schedule_result(result)
            msg = f"{len(result.placed)} {tr('status.classes_placed_count')}"
            if result.rescheduled:
                msg += f" ({tr('status.schedule_reorganized')})"
            self._show_toast(msg, "success")
            return True
        else:
            self._workflow.rollback_schedule(new_classes, existing_snapshots)
            self._feedback_logger.log_batch_result(
                len(result.placed), len(result.unplaced),
                result.rescheduled, False)
            self._show_toast(tr("status.bulk_cancelled"), "info")
            return False

    def edit_classes(self):
        """Open the Edit Class(es) dialog."""
        if not self.state_data.get("classes"):
            QMessageBox.information(self, tr("dialogs.edit_classes.title"),
                                   tr("dialogs.edit_classes.no_classes"))
            return
        snap_before = capture_snapshot(self.state_data)
        self._push_undo(tr("actions.edit").format(name="classes"))
        dlg = EditClassesDialog(self, self.state_data,
                                edit_callback=self._edit_class)
        dlg.exec()
        # Validate placements via workflow
        invalidated = SchedulingWorkflow.validate_placements_after_edit(
            self.state_data)
        if invalidated:
            self._show_toast(
                tr("status.placements_cleared_count", n=len(invalidated)),
                "warning")
        self.refresh_grid()
        self._run_impact_analysis(snap_before)

    def place_class(self):
        """Default placement workflow: place all currently unplaced classes."""
        self.place_all_unplaced_classes()

    def _place_classes_batch(self, candidates):
        """Auto-place a batch of candidate classes via workflow."""
        if not candidates:
            return 0, 0, False

        self._push_undo(tr("actions.bulk_schedule"))
        result = self._workflow.place_batch(candidates)

        if result.placed_count == 0 and result.unresolved_count == 0:
            return 0, 0, False

        msg = f"{result.placed_count} {tr('status.classes_placed_count')}"
        if result.unresolved_count:
            msg += f" {result.unresolved_count} {tr('status.could_not_place')}"
        if result.rescheduled:
            msg += f" ({tr('status.schedule_reorganized')})"
        kind = "success" if result.unresolved_count == 0 else (
            "warning" if result.placed_count > 0 else "error"
        )
        self._show_toast(msg, kind)

        self._clear_class_selection()
        self._clear_empty_slot_selection()
        self.refresh_grid()
        if not self._has_baseline and result.placed_count > 0:
            self.mark_current_state_as_baseline()
        return result.placed_count, result.unresolved_count, result.rescheduled

    def place_all_unplaced_classes(self):
        candidates = [
            c for c in self.state_data["classes"]
            if not c["pinned"] and not c["placed"]
        ]
        if not candidates:
            QMessageBox.information(
                self,
                tr("toolbar.place_all_unplaced"),
                tr("warnings.no_unplaced_classes"),
            )
            return
        self._place_classes_batch(candidates)

    def place_single_class(self):
        dlg = PlaceClassDialog(self, self.state_data)
        if not getattr(dlg, "unplaced", None):
            return
        if dlg.exec() == PlaceClassDialog.DialogCode.Accepted and dlg.result:
            idx, day, slot, room = dlg.result
            cls = self.state_data["classes"][idx]
            self._push_undo(tr("actions.place").format(name=cls["name"]))
            mark_placed(cls, day, slot, room)
            self.refresh_grid()

    def unplace_class(self):
        selected = [
            c for c in self._selected_classes
            if c.get("placed") and not c.get("pinned")
        ]
        if selected:
            self._push_undo(tr("actions.unplace").format(name=selected[0]["name"]))
            for cls in selected:
                mark_unplaced(cls)
            self._clear_class_selection()
            self._show_toast(
                tr("status.classes_unplaced_count").format(n=len(selected)), "success")
            self.refresh_grid()
            return

        placed = [(i, c) for i, c in enumerate(self.state_data["classes"])
                  if c["placed"] and not c["pinned"]]
        if not placed:
            QMessageBox.information(self, tr("buttons.unplace"), tr("warnings.no_flexible_classes"))
            return
        dlg = MultiSelectClassDialog(
            self, tr("dialogs.unplace_classes.title"), placed,
            show_placement=True, action_label=tr("buttons.unplace_selected"))
        if dlg.exec() == MultiSelectClassDialog.DialogCode.Accepted and dlg.result:
            self._push_undo(tr("actions.unplace").format(
                name=self.state_data["classes"][dlg.result[0]]["name"]))
            for idx in dlg.result:
                cls = self.state_data["classes"][idx]
                mark_unplaced(cls)
            self._show_toast(
                tr("status.classes_unplaced_count").format(n=len(dlg.result)), "success")
            self.refresh_grid()

    def remove_class(self):
        if not self.state_data["classes"]:
            QMessageBox.information(self, tr("buttons.remove"), tr("warnings.no_classes_to_remove"))
            return
        all_cls = [(i, c) for i, c in enumerate(self.state_data["classes"])]
        dlg = MultiSelectClassDialog(
            self, tr("dialogs.delete_classes.title"), all_cls,
            show_placement=True, action_label=tr("buttons.delete_selected"))
        if dlg.exec() == MultiSelectClassDialog.DialogCode.Accepted and dlg.result:
            snap_before = capture_snapshot(self.state_data)
            self._push_undo(tr("actions.delete").format(
                name=self.state_data["classes"][dlg.result[0]]["name"]))
            had_placed = any(self.state_data["classes"][i].get("placed")
                            for i in dlg.result)
            n_deleted = len(dlg.result)
            # Remove in reverse index order to avoid shifting
            for idx in sorted(dlg.result, reverse=True):
                self.state_data["classes"].pop(idx)
            self._show_toast(
                tr("status.classes_deleted_count").format(n=n_deleted), "success")
            self._clear_class_selection()
            self._clear_empty_slot_selection()
            if had_placed:
                desc = tr("impact.trigger.classes_deleted").format(n=n_deleted)
                self._note_structural_change(snap_before, description=desc)
            self.refresh_grid()

    def reschedule(self):
        """Hybrid re-optimization using multi-start LNS + optional CP-SAT."""
        placed_cls = [c for c in self.state_data["classes"]
                      if c["placed"] and not c["pinned"]]
        if not placed_cls:
            QMessageBox.information(
                self, tr("buttons.reschedule"),
                tr("warnings.no_flexible_to_reschedule"))
            return

        # Check if OR-Tools is available for deep optimization option
        try:
            from scheduler_app.cpsat_scheduler import HAS_ORTOOLS
        except ImportError:
            HAS_ORTOOLS = False

        # Show reschedule dialog with optional optimization goals
        from scheduler_app.dialogs import RescheduleDialog
        from scheduler_app.optimization_goals import goals_to_weights

        resched_dlg = RescheduleDialog(self, has_ortools=HAS_ORTOOLS)
        if resched_dlg.exec() != RescheduleDialog.DialogCode.Accepted:
            return
        use_cpsat = (resched_dlg.result_mode == "deep")

        # The solve now runs on a worker thread, so a failure inside it arrives
        # on the `failed` signal rather than as an exception here
        # (see _on_solve_failed).
        self._do_reschedule(resched_dlg, use_cpsat,
                            weights=self._get_learned_weights())

    def _on_solve_failed(self, exc):
        """The worker raised. Log it and offer a bug report, as before."""
        tb = getattr(self._solver_task, "failure_traceback", "") or repr(exc)
        try:
            from datetime import datetime
            log_path = storage.crash_log_path()
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"RESCHEDULE CRASH at {datetime.now()}\n")
                f.write(f"{'='*60}\n")
                f.write(tb + "\n")
        except Exception:
            pass
        self._end_solve_ui()
        from scheduler_app.ui.bug_report import CrashReportDialog
        dlg = CrashReportDialog(
            exc_type_name='RescheduleError',
            exc_message=tr('errors.optimization_error'),
            traceback_text=tb,
            log_path=storage.crash_log_path(),
            parent=self,
        )
        dlg.exec()

    # ── Solve lifecycle (ST-PERF-001) ────────────────────────────────────

    def _begin_solve_ui(self):
        """Put the window into "solving" mode: progress visible, actions off.

        Disabling the actions is not cosmetic. The whole point of the worker is
        that the window stays clickable, so a user WILL press Generate again —
        and two solves sharing one state dict and one apply_reschedule is how
        this change would corrupt a timetable.
        """
        from PyQt6.QtWidgets import QProgressDialog
        from PyQt6.QtCore import Qt as _Qt

        self.statusBar().showMessage(tr("status.optimizing"))
        dlg = QProgressDialog(tr("status.optimizing"), tr("buttons.cancel"),
                              0, 1000, self)
        dlg.setWindowModality(_Qt.WindowModality.WindowModal)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumDuration(0)
        dlg.canceled.connect(self._on_solve_cancel_requested)
        dlg.setValue(0)
        self._solve_progress_dialog = dlg

    def _end_solve_ui(self):
        """Leave "solving" mode, whatever the outcome."""
        dlg = getattr(self, "_solve_progress_dialog", None)
        if dlg is not None:
            self._solve_progress_dialog = None
            dlg.close()
            dlg.deleteLater()
        self.statusBar().clearMessage()

    def _on_solve_cancel_requested(self):
        task = getattr(self, "_solver_task", None)
        if task is not None:
            task.cancel()
        self.statusBar().showMessage(tr("status.reschedule_cancelled"))

    def _on_solve_progress(self, progress):
        """Update the bar. Runs on the GUI thread — no event pumping needed."""
        dlg = getattr(self, "_solve_progress_dialog", None)
        if dlg is not None:
            dlg.setValue(int(progress.fraction * 1000))
        self.statusBar().showMessage(tr("status.optimizing_run").format(
            r=progress.run + 1, t=progress.total_runs,
            i=progress.iteration, q=progress.best_score))

    def _on_solve_cancelled(self):
        self._end_solve_ui()
        self._show_toast(tr("status.reschedule_cancelled"), "info")

    def _do_reschedule(self, resched_dlg, use_cpsat, weights):
        """Inner reschedule logic, separated for error handling."""
        from scheduler_app.optimization_goals import goals_to_weights

        snapshots = snapshot_placements(self.state_data)

        # Merge learned weights with user goal overrides
        if resched_dlg.result_goals is not None:
            goal_weights = goals_to_weights(resched_dlg.result_goals)
            weights.update(goal_weights)

        # ST-PERF-001. The solve used to run inline on the GUI thread, via a
        # synchronous call into the workflow whose progress callback pumped the
        # Qt event loop by hand. That callback WAS the freeze: the window only
        # repainted because the solver reached back into Qt about three times a
        # second, and between two pumps the app answered nothing — 25-120 s of
        # "Not Responding" at a realistic department size, escapable only by
        # killing the process and losing the schedule.
        from scheduler_app.ui.solver_task import SolverTask

        existing = getattr(self, "_solver_task", None)
        if existing is not None and existing.is_running:
            return  # a solve is already running; the UI is disabled anyway

        self._solve_snapshots = snapshots
        task = SolverTask(self._workflow, weights, use_cpsat=use_cpsat,
                          parent=self)
        self._solver_task = task
        task.progress.connect(self._on_solve_progress)
        task.finished.connect(self._on_solve_finished)
        task.failed.connect(self._on_solve_failed)
        task.cancelled.connect(self._on_solve_cancelled)
        self._begin_solve_ui()
        task.start()

    def _on_solve_finished(self, result):
        """The worker produced a schedule. Everything from here is GUI-thread."""
        snapshots = getattr(self, "_solve_snapshots", None)
        self._end_solve_ui()

        # Show results with analytics
        # ST-SCHED-014. `summary` is available BEFORE the dialog (unlike
        # apply_reschedule's rejected list, which cannot exist until after the
        # modal returns), so the one sentence that names WHY the whole instance
        # cannot be built goes on the dialog itself. It answers a different
        # question from the per-class reasons beside it: "you are asking for 14
        # class-hours and the building has 8 room-hours" is something no list of
        # unplaced classes can ever say, and it is the only kind of problem that
        # rearranging lessons cannot fix.
        summary = result.summary or {}
        infeasibility = summary.get("infeasibility")
        results_dlg = BulkResultsDialog(
            self, result.placed, result.unplaced, bool(result.changes),
            analytics=result.analytics,
            reschedule_explanation=result.explanation,
            negotiation_source=lambda: result.negotiation_result,
            infeasibility=infeasibility)
        accepted = (results_dlg.exec() == BulkResultsDialog.DialogCode.Accepted
                    and results_dlg.result)

        if accepted:
            self._push_undo(tr("actions.reschedule"))
            # ST-SCHED-001. This return value was discarded. Each entry is a
            # placement the optimizer proposed and the COMMIT step refused --
            # a different category from result.unplaced, which the solver knew
            # it could not place. A rejection here means the state changed
            # between optimizing and applying, so the user asked for a
            # timetable and silently got a different one.
            rejected = self._workflow.apply_reschedule(result)
            self._report_rejected_placements(rejected)
            self._clear_impact_flags()

            moved = len(result.changes)
            msg = tr("status.reschedule_complete").format(n=moved)
            if result.unplaced:
                msg += f" {len(result.unplaced)} {tr('status.could_not_place')}"

            # Add optimization summary
            if result.summary:
                imp = result.summary.get("improvement", {})
                parts = []
                if imp.get("lecturer_gaps", 0) > 0.1:
                    parts.append(tr("labels.lecturer_gaps") + f" -{imp['lecturer_gaps']:.1f}")
                if imp.get("student_gaps", 0) > 0.1:
                    parts.append(tr("labels.student_gaps") + f" -{imp['student_gaps']:.1f}")
                if imp.get("fragmentation", 0) > 0.1:
                    parts.append(tr("labels.fragmentation") + f" -{imp['fragmentation']:.1f}")
                if parts:
                    msg += "\n" + tr("labels.improvements") + " " + ", ".join(parts)
                runs = result.summary.get("runs_completed", 1)
                elapsed = result.summary.get("total_time", 0)
                engine_info = f"{runs} " + tr("labels.runs")
                if result.summary.get("cpsat_used"):
                    cpsat_st = result.summary.get("cpsat_status_label") or result.summary.get("cpsat_status", "")
                    engine_info += f" + CP-SAT ({cpsat_st})"
                msg += f"\n({engine_info}, {elapsed:.1f}s)"
                # ST-SCHED-013: say so when this timetable cannot be
                # regenerated from the same settings, rather than letting the
                # silence imply that it can.
                note = self._reproducibility_note(result.summary)
                if note:
                    msg += f"\n⚠ {note}"

            if result.analytics:
                msg += f"\n{tr('analytics.schedule_quality')}: {result.analytics['global_score']:.0f}/100 ({tr('labels.grade')}: {result.analytics['grade']})"

            self._show_toast(msg, "success" if not result.unplaced else "warning")
            # Log negotiation diagnostics
            if result.negotiation_result and hasattr(self, 'warning_log'):
                for report in result.negotiation_result.get("class_reports", []):
                    self.warning_log.log(
                        f"{report['class_name']}: {report['summary']}",
                        "warning")
                    for sug in report.get("suggestions", [])[:2]:
                        self.warning_log.log(
                            f"  {report['class_name']}: "
                            f"{tr('labels.suggestion')}: {sug['description']}",
                            "info")
        else:
            self._workflow.reject_reschedule(snapshots, result.changes)
            self._show_toast(tr("status.reschedule_cancelled"), "info")
        self.refresh_grid()

    def _edit_class(self, cls):
        snap_before = capture_snapshot(self.state_data)
        dlg = AddClassDialog(self, self.state_data, edit_cls=cls)
        if dlg.exec() != AddClassDialog.DialogCode.Accepted or not dlg.result:
            return
        # ST-UI-020: see add_class. The edited name is in dlg.result.
        dlg.result["lecturer"] = SchedulingWorkflow.register_lecturer(
            self.state_data, dlg.result.get("lecturer")) or ""
        self._push_undo(tr("actions.edit").format(name=cls["name"]))
        edit_result = SchedulingWorkflow.apply_class_edit(
            self.state_data, cls, dlg.result)
        if edit_result.placement_cleared:
            self._show_toast(tr("status.class_unplaced_after_edit"), "warning")
        self._show_toast(tr("status.class_updated"), "success")
        self.refresh_grid()
        self._run_impact_analysis(snap_before)

    def _unplace_specific(self, cls):
        self._push_undo(tr("actions.unplace").format(name=cls["name"]))
        mark_unplaced(cls)
        self.refresh_grid()

    def _remove_specific(self, cls):
        self._remove_classes([cls])

    def _remove_classes(self, classes):
        # Deduplicate candidates
        uniq = []
        seen = set()
        for cls in classes:
            if cls is None:
                continue
            cid = cls_key(cls)
            if cid in seen:
                continue
            seen.add(cid)
            if cls in self.state_data["classes"]:
                uniq.append(cls)
        if not uniq:
            return
        title = uniq[0]["name"] if len(uniq) == 1 else f"{len(uniq)} {tr('status.classes')}"
        if QMessageBox.question(
                self, tr("dialogs.confirm.title"),
                f"{tr('buttons.delete')}: '{title}'?") != QMessageBox.StandardButton.Yes:
            return
        snap_before = capture_snapshot(self.state_data)
        self._push_undo(tr("actions.delete").format(name=uniq[0]["name"]))
        had_placed = any(c.get("placed") for c in uniq)
        removed = SchedulingWorkflow.remove_classes(self.state_data, uniq)
        if removed == 1:
            self._show_toast(tr("status.class_removed"), "success")
        elif removed > 1:
            self._show_toast(
                tr("status.classes_deleted_count").format(n=removed), "success")
        if removed and had_placed:
            desc = tr("impact.trigger.classes_deleted").format(n=removed)
            self._note_structural_change(snap_before, description=desc)
        self._clear_class_selection()
        self._clear_empty_slot_selection()
        self.refresh_grid()

    def _edit_lecturer_from_class(self, cls):
        snap_before = capture_snapshot(self.state_data)
        lecturer_name = (cls.get("lecturer") or "").strip()
        dlg = SetupDialog(self, self.state_data, focus_lecturer=lecturer_name)
        dlg.exec()
        if dlg.result:
            self._reconcile_after_setup()
        self.refresh_grid()
        if dlg.result:
            self._run_impact_analysis(snap_before)

    def _reconcile_after_setup(self):
        """Repair placements orphaned by a setup change, and say how many.

        Must run BEFORE the repaint: refresh_grid -> _update_side_panels ->
        _refresh_open_slots reaches slot_index, which cannot resolve a slot the
        user has just deleted (ST-DATA-003).
        """
        affected = SchedulingWorkflow.reconcile_placements(self.state_data)
        if affected:
            self._show_toast(
                tr("status.placements_cleared_count", n=len(affected)),
                "warning")
        return affected

    def edit_setup(self):
        snap_before = capture_snapshot(self.state_data)
        is_initial = not self._has_baseline
        # ST-UI-014's second clause / ST-ARCH-012. Setup IS undoable now, and
        # the history of this line is worth keeping.
        #
        # Phase 4 built this and withdrew it. Back then `_push_undo` copied
        # `state_data["classes"]` and nothing else, while Setup rewrites days,
        # slots, classrooms, lecturers and years — so "Undo: setup change"
        # restored the classes WITH THEIR OLD PLACEMENTS onto the NEW grid and
        # resurrected exactly the orphans ST-DATA-003 is about. A
        # half-transaction undo is not a partial fix; it is a data-corruption
        # bug wearing a safety label, and it was right to pull it.
        #
        # What makes it safe now is that the snapshot covers the axes too, so
        # the placements and the grid they refer to are restored together.
        #
        # The snapshot is taken only when the dialog is ACCEPTED. Phase 4's
        # version pushed before `exec()` and popped on cancel, which silently
        # destroyed the redo stack on a cancelled Setup and, at the 50-entry
        # cap, evicted an undo step that popping could not put back.
        dlg = SetupDialog(self, self.state_data)
        before_setup = copy.deepcopy(self.state_data)
        dlg.exec()
        if dlg.result:
            self._undo_stack.append(
                (tr("actions.setup"), before_setup))
            if len(self._undo_stack) > self._max_undo:
                self._undo_stack.pop(0)
            self._redo_stack.clear()
            self._reconcile_after_setup()
        self.refresh_grid()
        if dlg.result:
            if is_initial:
                self.mark_current_state_as_baseline()
            else:
                self._run_impact_analysis(snap_before)

    def _open_slots_selected_class(self):
        """The class the open-slots panel filters to, or None.

        Shared with the fingerprint so the two cannot disagree: the panel also
        honours a single selection in the unplaced list, and a fingerprint that
        only looked at ``_selected_class`` would freeze the panel whenever the
        user picked something there.
        """
        selected = getattr(self, "_selected_class", None)
        if selected is None and hasattr(self, "unplaced_list"):
            upl = self.unplaced_list.selected_classes()
            if len(upl) == 1:
                selected = upl[0]
        return selected

    def _open_slots_fingerprint(self):
        """Everything the open-slots panel's contents depend on.

        ST-PERF-006. Getting this wrong is worse than not having it, and the
        tempting version is the wrong one: a fingerprint of just the grid shape
        (days / slots / classrooms) is stable for an entire editing session, so
        the panel would be built once and then frozen, showing occupied slots as
        free. Occupancy has to be in here, and so does the selection — the panel
        filters itself to the selected class, so a selection change genuinely
        changes what it shows (which is also what ST-UI-009 needs).
        """
        st = self.state_data
        placed = tuple(
            (c.get("placed_day"), c.get("placed_time"),
             c.get("placed_classroom"), c.get("duration"),
             bool(c.get("pinned")), c.get("pinned_day"),
             c.get("pinned_time"), c.get("pinned_classroom"))
            for c in st.get("classes", [])
            if c.get("placed") or c.get("pinned"))
        selected = self._open_slots_selected_class()
        return (
            tuple(st.get("days", [])),
            tuple(st.get("slots", [])),
            tuple(st.get("classrooms", [])),
            len(st.get("classes", [])),
            placed,
            id(selected) if selected is not None else None,
            id(getattr(self, "_selected_empty_slot", None) or 0),
        )

    def invalidate_open_slots(self):
        """Force the next _refresh_open_slots to rebuild."""
        self._open_slots_fp = None

    def _refresh_open_slots(self):
        """Update the live open-slots panel (grouped by day).

        When a single class is selected, filters to show only slots
        where that class can be validly placed.
        """
        if not hasattr(self, '_open_slots_layout'):
            return

        # ST-PERF-006: this rebuilds hundreds of widgets and re-runs placement
        # analysis (359 widgets and a 4.5 s pass at 250 classes), and
        # refresh_grid reaches it twice per call. Skip it outright when nothing
        # it displays has changed.
        fp = self._open_slots_fingerprint()
        if fp == getattr(self, "_open_slots_fp", None):
            return
        self._open_slots_fp = fp

        layout = self._open_slots_layout
        # Clear existing widgets
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        s = self.state_data
        if not s["days"] or not s["slots"] or not s["classrooms"]:
            self._open_slots_filter_hint.hide()
            return

        # Determine if contextual filtering applies
        selected_cls = self._open_slots_selected_class()

        if selected_cls is not None:
            # Contextual mode: use find_valid_options for the selected class
            # ST-ARCH-004 / ST-UI-017. Iterate the options themselves; never
            # re-key them through state["classrooms"].
            #
            # `get_room_candidates` answers ``[None]`` for a lesson that needs
            # no physical room, so an online option is ``(day, slot, None)``.
            # Mapping that to ``""`` and then testing membership against the
            # classroom list could never match: ``""`` is not a classroom. So
            # this panel told **every online lesson it had nowhere to go**,
            # while `PlaceClassDialog` — same `find_valid_options`, same state —
            # listed its slots. Measured on a 2-day/3-slot grid: 6 valid
            # options, 0 rows drawn, and the panel rendered
            # "no valid placements".
            #
            # This is the same `None`-room sentinel Phase 3 taught the drag path
            # to read (ST-ARCH-004, `find_drop_classroom` returning None for an
            # online lesson is a sentinel, not a failure). One surface was
            # missed.
            free_by_day = {}
            for day, slot, room in find_valid_options(s, selected_cls):
                free_by_day.setdefault(day, []).append((slot, room))
            for entries in free_by_day.values():
                entries.sort(key=lambda p: (
                    s["slots"].index(p[0]) if p[0] in s["slots"] else 0,
                    p[1] or ""))

            self._open_slots_filter_hint.setText(
                f"\u25C9 {tr('panels.filtered_for')}: {selected_cls['name']}")
            self._open_slots_filter_hint.show()
        else:
            # Default mode: show all open slots
            placed = get_placed_classes(s)
            occupied_set = set()
            for c in placed:
                room = classroom_of(c)
                for day, slot in occupied_slots_of(s, c):
                    occupied_set.add((day, slot, room))

            free_by_day = {}
            for day in s["days"]:
                for slot in s["slots"]:
                    for room in s["classrooms"]:
                        if (day, slot, room) not in occupied_set:
                            free_by_day.setdefault(day, []).append(
                                (slot, room))

            self._open_slots_filter_hint.hide()

        if not free_by_day:
            if selected_cls is not None:
                no_slots = QLabel(tr("warnings.no_valid_placements"))
                no_slots.setStyleSheet(
                    "QLabel { font-size: 7.5pt; color: %s;"
                    "  padding: 12px; background: transparent; }"
                    % OPEN_SLOTS_FG_ROOM)
                no_slots.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(no_slots)
            layout.addStretch()
            return

        for day in s["days"]:
            entries = free_by_day.get(day, [])
            if not entries:
                continue

            # Day header
            header = QLabel(display_day(day).upper())
            header.setStyleSheet(
                "QLabel { font-size: 7pt; font-weight: 600;"
                "  color: #6B7280; letter-spacing: 1px;"
                "  padding: 6px 4px 2px 4px; background: transparent; }")
            layout.addWidget(header)

            # Slot rows
            for slot_time, room in entries:
                row = QWidget()
                row.setObjectName("slotRow")
                row.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                row.setStyleSheet(
                    "QWidget#slotRow {"
                    "  background: #FFFFFF; border-radius: 6px;"
                    "  padding: 8px 10px; margin: 1px 0px; }"
                    "QWidget#slotRow:hover {"
                    "  background: #ECFDF5; }")
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(10, 6, 10, 6)
                row_layout.setSpacing(0)

                # ST-UI-007. A QLabel defaults to AutoText, so Qt decides PER
                # STRING whether to parse markup -- and both of these carry
                # free text the school typed into Setup. Measured offscreen on
                # this panel, sizeHint width before -> after forcing PlainText:
                # a slot labelled "09:00 <b>x</b>" 77 -> 154, a room called
                # "Lab <b>A</b>" 50 -> 120. Qt was drawing "Lab A", so the
                # panel disagreed with the classroom list about the room's
                # name and the user had no way to tell which was right.
                #
                # Removing Qt's choice is the fix, NOT escaping: html.escape on
                # a string Qt would have shown literally puts '&amp;' on the
                # screen, and "R&D Lab" is a plausible room name. Same remedy
                # as the toast in ui/widgets.py.
                time_label = QLabel(slot_time)
                time_label.setTextFormat(Qt.TextFormat.PlainText)
                time_label.setStyleSheet(
                    "QLabel { font-size: 8pt; font-weight: 700;"
                    "  color: #111827; background: transparent; }")
                row_layout.addWidget(time_label)

                row_layout.addStretch()

                # A None room is the "needs no classroom" sentinel, not a
                # missing value: render it as the resource the rest of the app
                # names it by ("Çevrimiçi", "Ofis (Öğr. Elem.)"), which is what
                # the classroom filter and the cell already show.
                room_label = QLabel(
                    get_effective_room_resource_for_class(
                        selected_cls, room_override=room)
                    if selected_cls is not None and not room else (room or ""))
                room_label.setTextFormat(Qt.TextFormat.PlainText)
                room_label.setStyleSheet(
                    "QLabel { font-size: 7.5pt; color: %s;"
                    "  background: transparent; }" % OPEN_SLOTS_FG_ROOM)
                row_layout.addWidget(room_label)

                layout.addWidget(row)

        layout.addStretch()

    def _refresh_warnings(self):
        """Compute workload warnings and auto-negotiation diagnostics.

        ST-PERF-003: these are *derived* from the current timetable, so they are
        handed to the panel as a set that replaces the previous one. Appending
        them — which is what used to happen — meant the log accumulated 138 more
        entries on every refresh and went on describing a timetable that no
        longer existed.
        """
        if not hasattr(self, 'warning_log'):
            return
        s = self.state_data
        placed = get_placed_classes(s)
        if not s["days"] or not s["slots"]:
            self.warning_log.set_derived([])
            return

        derived = []

        if placed:
            for yr in sorted(s["years"].keys()):
                for br in s["years"][yr]:
                    day_counts = {}
                    for day in s["days"]:
                        count = 0
                        for c in placed:
                            if not any(t["year"] == yr and t["branch"] == br
                                       for t in c["targets"]):
                                continue
                            occ = occupied_slots_of(s, c)
                            count += sum(1 for d, sl in occ if d == day)
                        day_counts[day] = count

                    heavy = [d for d, n in day_counts.items()
                             if n >= len(s["slots"]) * 0.75]
                    light = [d for d, n in day_counts.items()
                             if n == 0 and sum(day_counts.values()) > 0]
                    if heavy:
                        derived.append((
                            f"{yr}/{br}: {tr('warnings.heavy_days_short')} "
                            f"{', '.join(day_label(d) for d in heavy)}",
                            "warning"))
                    if light:
                        derived.append((
                            f"{yr}/{br}: {tr('warnings.empty_days_short')} "
                            f"{', '.join(day_label(d) for d in light)}",
                            "warning"))

        # Auto-negotiation: analyze unplaced classes and collect suggestions.
        # It can mutate state (auto-apply), so publish AFTER it returns.
        derived.extend(self._run_auto_negotiation())

        # ST-UI-001. Recomputed here rather than reused from
        # _render_current_tab because _run_auto_negotiation can move classes.
        # Appended last so a conflict becomes the panel's headline (set_derived
        # shows the final entry), and capped because the panel re-renders its
        # whole document from this list: the pathological preset reaches five
        # figures of pairs, several times the 1656 entries ST-PERF-003 was
        # about. The CAP IS ON THE LOG, NOT ON DETECTION — every conflicted
        # lesson is still marked in the grid.
        derived.extend(self._conflict_log_entries())

        self.warning_log.set_derived(derived)

    @staticmethod
    def _reproducibility_note(summary):
        """A line for the result toast when the solve cannot be reproduced.

        ST-SCHED-013. `summary['deterministic']` is
        `(not clock_capped) and (not cpsat_used)`, so it is False whenever the
        Thorough mode ran OR the time budget truncated the search. The second
        case matters most: the user did not choose it, and nothing else on
        screen distinguishes a timetable they can regenerate from one they
        cannot.

        Phase 1 was careful that the ENGINE never claims a reproducibility it
        cannot deliver, and then nothing surfaced the flag -- so the UI made the
        claim on its behalf by staying quiet.

        Silent on the normal case: a note that appears every time is a note
        nobody reads. A missing key is treated as reproducible, so an older or
        partial summary does not raise a false alarm.
        """
        if not summary or summary.get("deterministic", True):
            return ""
        return tr("status.not_reproducible_note")

    def _report_rejected_placements(self, rejected):
        """Say so when the commit step refused a placement the solver proposed.

        ST-SCHED-001 / ST-SCHED-002. `apply_reschedule` reports TWO different
        events through one list, and telling a user the wrong one is worse than
        telling them nothing:

        * ``committed=True`` -- a pinned or locked class that clashes where the
          USER put it. It is committed, because the pin is their instruction.
          Nothing failed. On the project's own dataset generator 13 of 13
          rejections are this kind, and the first version of this method
          reported every one as an error reading "could not be committed where
          the planner put it" -- about a lesson sitting exactly where the user
          pinned it, after a reschedule that went fine.
        * ``committed=False`` -- the commit step refused a placement the
          optimizer proposed, i.e. the state changed between optimizing and
          applying. That is nearly always a race or a defect, and is a
          different category from `result.unplaced`: the solver knew about
          those and the results dialog explains them.

        Only the second is an error, and only the second gets a toast. The
        first is a warning, and the grid already headlines it in red
        (ST-UI-001) -- which is the right surface for it.
        """
        if not rejected:
            return
        refused = [e for e in rejected if not e.get("committed")]
        clashing_pins = [e for e in rejected if e.get("committed")]

        for entry in clashing_pins:
            self.warning_log.log(
                tr("status.pinned_clash").format(
                    name=entry.get("name", "?"),
                    reason=entry.get("reason", "")),
                "warning")
        for entry in refused:
            self.warning_log.log(
                tr("status.placement_refused").format(
                    name=entry.get("name", "?"),
                    reason=entry.get("reason", "")),
                "error")
        if refused:
            self._show_toast(
                tr("status.placements_refused_toast").format(n=len(refused)),
                "warning")

    _MAX_CONFLICT_LOG_ENTRIES = 25

    _CONFLICT_KIND_KEYS = {
        "room": "conflicts.room",
        "lecturer": "conflicts.lecturer",
        "target": "conflicts.student_group",
    }

    @staticmethod
    def _conflict_label(cls):
        code = cls.get("class_code", "")
        return f"[{code}] {cls['name']}" if code else cls["name"]

    def _conflict_log_entries(self):
        """Warning-log lines for the conflicts found this refresh."""
        conflicts = self._conflicts = find_schedule_conflicts(self.state_data)
        self._conflict_partners = conflict_partner_index(conflicts)
        entries = []
        for rec in conflicts[:self._MAX_CONFLICT_LOG_ENTRIES]:
            kinds = ", ".join(
                tr(self._CONFLICT_KIND_KEYS[k]) for k in rec["kinds"]
                if k in self._CONFLICT_KIND_KEYS)
            entries.append((
                tr("conflicts.cell_pair").format(
                    day=day_label(rec["day"]), slot=rec["slot"],
                    a=self._conflict_label(rec["a"]),
                    b=self._conflict_label(rec["b"]),
                    kinds=kinds),
                "error"))
        extra = len(conflicts) - self._MAX_CONFLICT_LOG_ENTRIES
        if extra > 0:
            entries.append((tr("conflicts.more").format(n=extra), "error"))
        return entries

    def _run_auto_negotiation(self):
        """Analyze unplaced/constrained classes; RETURN the messages to show.

        This is the event-driven negotiation layer: it runs as part of every
        refresh cycle whenever unplaced classes or severe bottlenecks exist,
        pushing structured diagnostics and relaxation suggestions into the
        warning log panel without requiring any manual trigger.
        """
        if not hasattr(self, 'warning_log'):
            return []
        s = self.state_data
        unplaced = [c for c in s.get("classes", [])
                    if not c.get("placed") and not c.get("pinned")]
        if not unplaced:
            return []

        derived = []

        from scheduler_app.constraint_negotiator import ConstraintNegotiator
        neg = ConstraintNegotiator(s)

        auto_apply_enabled = self._get_negotiation_auto_apply()
        applied_any = False

        for cls in unplaced:
            report = neg.negotiate_class(cls)
            if not report["suggestions"]:
                derived.append((f"{cls['name']}: {report['summary']}", "warning"))
                continue

            # Collect the top suggestions for the warning panel
            top = report["suggestions"][0]
            derived.append((
                f"{cls['name']}: {report['summary']} "
                f"— {tr('labels.suggestion')}: {top['description']}",
                "warning"))
            for sug in report["suggestions"][1:3]:
                derived.append((
                    f"  {cls['name']}: {tr('labels.also')}: {sug['description']}",
                    "info"))

            # Auto-apply low-risk suggestions if enabled
            if auto_apply_enabled:
                for sug in report["suggestions"]:
                    if sug.get("disruption", 1.0) <= 0.3:
                        if not applied_any:
                            self._push_undo(tr("actions.auto_negotiate"))
                        if apply_negotiation_suggestion(cls, sug):
                            derived.append((
                                f"{cls['name']}: {tr('status.auto_applied')}: "
                                f"{sug['description']}", "success"))
                            applied_any = True
                            break

        if applied_any:
            # Re-trigger placement attempts for classes whose constraints
            # were just relaxed, without recursing into _refresh_warnings
            for cls in unplaced:
                if not cls.get("placed"):
                    self._auto_place_and_apply(cls)

        return derived

    def _get_negotiation_auto_apply(self):
        """Check if auto-apply of low-risk negotiation suggestions is enabled.

        Reads from settings/negotiation_settings.egu.
        Default is False (suggest only, never auto-modify constraints).
        """
        neg_path = storage.negotiation_settings_path()
        try:
            data = storage.load_encrypted(neg_path)
            return bool(data.get("auto_apply_low_risk", False))
        except Exception:
            return False

    # ══════════════════════════════════════════════════════════════════════
    #  SELECTION & DRAG-DROP
    # ══════════════════════════════════════════════════════════════════════

    def _clear_class_selection(self):
        for it in self._selected_cells:
            try:
                it.mark_selected(False)
            except (RuntimeError, AttributeError):
                pass
        self._selected_cells = []
        self._selected_classes = []
        self._selected_class = None
        self._selected_cell = None
        self._selection_anchor = None
        self._refresh_open_slots()

    def _clear_empty_slot_selection(self):
        if self._selected_empty_slot is None:
            return
        try:
            self._selected_empty_slot.mark_selected(False)
        except (RuntimeError, AttributeError):
            pass
        self._selected_empty_slot = None

    def _iter_scene_lesson_items(self, item):
        scene = item.scene() if item is not None else None
        if scene is None or not hasattr(scene, "lesson_items"):
            return []
        items = [it for it in scene.lesson_items if it is not None]
        # Keep Shift+Click range selection predictable by visual order.
        items.sort(
            key=lambda it: (
                round(it.sceneBoundingRect().top(), 3),
                round(it.sceneBoundingRect().left(), 3),
            )
        )
        return items

    def _apply_class_selection(self, items, anchor=None):
        deduped = []
        seen_item_ids = set()
        for it in items:
            if it is None:
                continue
            iid = id(it)
            if iid in seen_item_ids:
                continue
            seen_item_ids.add(iid)
            deduped.append(it)

        # ST-UI-009: re-clicking the lesson that is already selected used to
        # redo the whole selection pass and rebuild the side panels — measured
        # at 39 ms per click at 80 classes. Compared here, before
        # _selected_cells is overwritten below.
        unchanged = (deduped == self._selected_cells
                     and self._selection_anchor is (anchor if deduped else None))

        new_set = set(deduped)
        for old in self._selected_cells:
            if old not in new_set:
                try:
                    old.mark_selected(False)
                except (RuntimeError, AttributeError):
                    pass
        for it in deduped:
            try:
                it.mark_selected(True)
            except (RuntimeError, AttributeError):
                pass

        self._selected_cells = deduped
        seen_cls_ids = set()
        selected_classes = []
        for it in deduped:
            cls_obj = getattr(it, "cls", None)
            if cls_obj is None:
                continue
            cid = id(cls_obj)
            if cid in seen_cls_ids:
                continue
            seen_cls_ids.add(cid)
            selected_classes.append(cls_obj)

        self._selected_classes = selected_classes
        self._selected_class = selected_classes[0] if len(selected_classes) == 1 else None
        self._selected_cell = deduped[0] if len(deduped) == 1 else None
        self._selection_anchor = anchor if deduped else None

        if len(selected_classes) == 1:
            self.status_label.setText(
                f"{tr('status.selected')}: {selected_classes[0]['name']} — "
                f"{tr('status.press_delete')}")
        elif len(selected_classes) > 1:
            self.status_label.setText(
                f"{tr('status.selected')}: {len(selected_classes)} {tr('status.classes')} — "
                f"{tr('status.press_delete')}")
        else:
            self.status_label.setText(tr("status.ready"))
        if unchanged:
            return  # ST-UI-009: same selection, nothing for the panel to redo
        self._refresh_open_slots()

    def _select_class_gfx(self, cls, item, modifiers=None):
        """Select one or multiple lessons via graphics items."""
        if modifiers is None:
            modifiers = QApplication.keyboardModifiers()

        self._clear_empty_slot_selection()

        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        current_items = list(self._selected_cells)

        if shift:
            ordered = self._iter_scene_lesson_items(item)
            anchor = (self._selection_anchor
                      if self._selection_anchor in ordered
                      else (current_items[-1] if current_items and current_items[-1] in ordered else item))
            if anchor in ordered and item in ordered:
                a_idx = ordered.index(anchor)
                b_idx = ordered.index(item)
                lo, hi = sorted((a_idx, b_idx))
                ranged = ordered[lo:hi + 1]
                if ctrl:
                    merged = list(current_items)
                    for it in ranged:
                        if it not in merged:
                            merged.append(it)
                    self._apply_class_selection(merged, anchor=anchor)
                else:
                    self._apply_class_selection(ranged, anchor=anchor)
                return

        if ctrl:
            if item in current_items:
                current_items.remove(item)
                anchor = self._selection_anchor if current_items else None
                self._apply_class_selection(current_items, anchor=anchor)
            else:
                current_items.append(item)
                self._apply_class_selection(current_items, anchor=item)
            return

        self._apply_class_selection([item], anchor=item)

    def _select_empty_slot(self, item):
        """Highlight an empty timetable slot on left click."""
        # ST-UI-009: the identity check has to come first. Below
        # _clear_class_selection() it still returned early, but only after
        # paying for a full clear — 81 ms per re-click at 80 classes. Safe to
        # move: the two selections are mutually exclusive, so when this slot is
        # already selected there is no class selection left to clear.
        if item is not None and self._selected_empty_slot is item:
            return
        self._clear_class_selection()
        self._clear_empty_slot_selection()
        self._selected_empty_slot = item
        try:
            item.mark_selected(True)
        except (RuntimeError, AttributeError):
            pass
        self.status_label.setText(
            f"{tr('status.selected')}: {format_day_time(item.day, item.slot)}")

    def _show_empty_slot_context_menu(self, day, slot, global_pos):
        """Context menu for empty timetable slots."""
        menu = QMenu(self)
        title = menu.addAction(f"\U0001F4C5  {format_day_time(day, slot)}")
        title.setEnabled(False)
        menu.addSeparator()
        add_act = menu.addAction("\u2795  " + tr("menus.classes"))
        add_act.triggered.connect(lambda: self._add_class_at(day, slot))
        place_act = menu.addAction("\u25BC  " + tr("dialogs.place.title"))
        place_act.triggered.connect(lambda: self._place_unplaced_class_at_slot(day, slot))
        menu.exec(global_pos)

    def _place_unplaced_class_at_slot(self, day, slot):
        """Pick an unplaced class and place it directly into the chosen slot."""
        unplaced = [
            (i, c) for i, c in enumerate(self.state_data["classes"])
            if not c["pinned"] and not c["placed"]
        ]
        if not unplaced:
            QMessageBox.information(self, tr("dialogs.place.title"),
                                   tr("warnings.no_unplaced_classes"))
            return

        valid = []
        for idx, cls in unplaced:
            options = [
                opt for opt in find_valid_options(self.state_data, cls)
                if opt[0] == day and opt[1] == slot
            ]
            if options:
                valid.append((idx, cls, options))

        if not valid:
            QMessageBox.information(
                self, tr("dialogs.place.title"),
                tr("warnings.no_valid_placements"))
            return

        chooser_items = [(idx, cls) for idx, cls, _opts in valid]
        dlg = SelectClassDialog(self, tr("dialogs.place.title"), chooser_items, show_placement=False)
        if dlg.exec() != SelectClassDialog.DialogCode.Accepted or dlg.result is None:
            return

        selected_idx = dlg.result
        picked = next((v for v in valid if v[0] == selected_idx), None)
        if not picked:
            return
        _idx, cls, options = picked

        preferred_room = None
        if self.notebook.currentIndex() == 0:
            selected = self.classroom_filter.currentData()
            kind, value = _decode_classroom_filter_value(selected)
            if kind == "room":
                preferred_room = value
        chosen = options[0]
        if preferred_room:
            for opt in options:
                if opt[2] == preferred_room:
                    chosen = opt
                    break

        self._push_undo(tr("actions.place").format(name=cls["name"]))
        mark_placed(cls, chosen[0], chosen[1], chosen[2])
        self.refresh_grid()

    def _set_protection(self, cls, level):
        """Set the protection level of a class."""
        from scheduler_app.models import get_protection_label
        cls["protection"] = level
        self._show_toast(
            tr("protection.set_to").format(
                level=get_protection_label(level)),
            "success")
        self.refresh_grid()

    def _start_drag_gfx(self, cls, item):
        """Start a drag from a LessonItem (graphics-based)."""
        from scheduler_app.models import is_immovable
        if is_immovable(cls):
            if cls["pinned"]:
                self._show_toast(tr("warnings.pinned_cannot_move"), "warning")
            else:
                self._show_toast(
                    tr("warnings.locked_cannot_move"),
                    "warning")
            return

        selected_drag_group = [
            c for c in self._selected_classes
            if c.get("placed") and not c.get("pinned")
        ]
        if cls in selected_drag_group and len(selected_drag_group) > 1:
            self._dragging_classes = list(selected_drag_group)
        else:
            self._dragging_classes = [cls]

        self._drag_backup = {
            "placed": cls["placed"],
            "placed_day": cls["placed_day"],
            "placed_time": cls["placed_time"],
            "placed_classroom": cls["placed_classroom"],
        }
        # Push undo BEFORE the pre-emptive mark_unplaced so the snapshot
        # captures the original placed state.  Popped later if drag fails.
        self._push_undo(tr("actions.unplace").format(name=cls["name"]))
        mark_unplaced(cls)

        self._dragging_cls = cls
        self._drag_success = False

        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(f"class_drag:{cls_key(cls)}")
        drag.setMimeData(mime)

        # Render drag pixmap BEFORE unplacing (item still valid)
        try:
            scene = item.scene()
            if scene:
                rect = item.boundingRect()
                from PyQt6.QtGui import QPixmap
                from PyQt6.QtCore import QRectF
                w, h = max(1, int(rect.width())), max(1, int(rect.height()))
                pm = QPixmap(w, h)
                pm.fill(QColor(0, 0, 0, 0))
                p = QPainter(pm)
                src = QRectF(item.mapToScene(rect).boundingRect())
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                scene.render(p, QRectF(pm.rect()), src)
                p.end()
                scaled = pm.scaled(
                    int(pm.width() * 0.85), int(pm.height() * 0.85),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                drag.setPixmap(scaled)
                drag.setHotSpot(QPoint(scaled.width() // 2, 20))
        except Exception:
            pass  # proceed without drag pixmap

        # Ghost effect on the source item
        if hasattr(item, 'set_ghost'):
            try:
                item.set_ghost(True)
            except RuntimeError:
                pass

        drag.exec(Qt.DropAction.MoveAction)

        # Remove ghost effect
        if hasattr(item, 'set_ghost'):
            try:
                item.set_ghost(False)
            except RuntimeError:
                pass

        if not self._drag_success:
            for k, v in self._drag_backup.items():
                cls[k] = v
            # Drag was cancelled — discard the pre-emptive undo snapshot
            if self._undo_stack:
                self._undo_stack.pop()

        self._dragging_cls = None
        self._dragging_classes = []
        self._drag_backup = None
        self.refresh_grid()

    def _start_drag_unplaced(self, classes, widget):
        """Initiate a drag for one or many unplaced classes."""
        drag_classes = []
        seen = set()
        for cls in classes or []:
            if cls is None:
                continue
            cid = cls_key(cls)
            if cid in seen:
                continue
            seen.add(cid)
            drag_classes.append(cls)
        if not drag_classes:
            return

        self._drag_backup = {
            "placed": False,
            "placed_day": None,
            "placed_time": None,
            "placed_classroom": None,
        }
        self._dragging_cls = drag_classes[0]
        self._dragging_classes = drag_classes
        self._drag_success = False

        drag = QDrag(widget)
        mime = QMimeData()
        mime.setText(f"class_drag:{cls_key(drag_classes[0])}")
        drag.setMimeData(mime)

        if len(drag_classes) > 1:
            label = QLabel(f"{len(drag_classes)} {tr('status.classes')}")
            label.setStyleSheet(
                "background: #FEF3C7; border: 1px solid #F59E0B; "
                "padding: 4px 8px; border-radius: 3px; font-weight: bold;")
            label.adjustSize()
            drag.setPixmap(label.grab())
            drag.setHotSpot(QPoint(drag.pixmap().width() // 2, 12))
        else:
            item = widget.currentItem()
            if item:
                label = QLabel(item.text())
                label.setStyleSheet(
                    "background: #FEF3C7; border: 1px solid #F59E0B; "
                    "padding: 4px 8px; border-radius: 3px; font-weight: bold;")
                label.adjustSize()
                drag.setPixmap(label.grab())
                drag.setHotSpot(QPoint(drag.pixmap().width() // 2, 12))

        drag.exec(Qt.DropAction.MoveAction)

        if self._drag_success:
            if len(drag_classes) == 1:
                self._show_toast(
                    tr("status.class_placed_drag"), "success")

        self._dragging_cls = None
        self._dragging_classes = []
        self._drag_backup = None
        self.refresh_grid()

    def _get_preferred_rooms(self, cls):
        """Build an ordered preference list of rooms from UI context."""
        preferred = []
        if self._drag_backup and self._drag_backup.get("placed_classroom"):
            preferred.append(self._drag_backup["placed_classroom"])
        tab_idx = self.notebook.currentIndex()
        if tab_idx == 0:
            selected = self.classroom_filter.currentData()
            kind, value = _decode_classroom_filter_value(selected)
            if kind == "room":
                preferred.append(value)
        return preferred

    def _get_drop_classroom(self, cls, day, slot):
        """Determine the classroom for a drop via workflow.

        ST-ARCH-004: ``room is None`` is now the *correct* answer for an online
        or lecturer-office lesson — ``get_room_candidates`` yields the None
        sentinel for anything that needs no room — so it can no longer be read
        as "no compatible classroom". Only a lesson that actually needs a room
        fails when it does not get one.
        """
        preferred = self._get_preferred_rooms(cls)
        room, conflicts = SchedulingWorkflow.find_drop_classroom(
            self.state_data, cls, day, slot, preferred_rooms=preferred)
        if room is None and needs_physical_room(cls):
            return None, [tr("errors.no_compatible_classrooms")]
        return room, conflicts

    def _check_drop_valid(self, day, slot):
        """Quick check if dragging class can be placed at (day, slot)."""
        drag_group = list(getattr(self, "_dragging_classes", []) or [])
        if len(drag_group) > 1 and all(not c.get("placed") for c in drag_group):
            return True

        cls = self._dragging_cls
        if cls is None:
            return False
        preferred = self._get_preferred_rooms(cls)
        return SchedulingWorkflow.check_drop_valid(
            self.state_data, cls, day, slot,
            drag_backup=self._drag_backup,
            preferred_rooms=preferred)

    def _execute_drop_anywhere(self):
        """Handle drops that are not over a specific timetable cell."""
        drag_group = list(getattr(self, "_dragging_classes", []) or [])
        if len(drag_group) > 1 and all(not c.get("placed") for c in drag_group):
            self._place_classes_batch(drag_group)
            self._drag_success = True

    def _execute_drop(self, day, slot):
        """Finalize the drop: validate fully and either commit or reject."""
        drag_group = list(getattr(self, "_dragging_classes", []) or [])
        if len(drag_group) > 1 and all(not c.get("placed") for c in drag_group):
            self._execute_drop_anywhere()
            return

        cls = self._dragging_cls
        if cls is None:
            return

        # Phase 1: basic constraint validation
        validation = SchedulingWorkflow.validate_drop(
            self.state_data, cls, day, slot, drag_backup=self._drag_backup)
        if not validation.valid:
            reasons = self._format_drop_reasons(validation.reasons, cls)
            QMessageBox.warning(
                self, tr("dialogs.move_rejected.title"),
                tr("errors.cannot_move_to").format(
                    name=cls["name"], day=display_day(day), slot=slot)
                + "\n\n" + "\n".join(f"  - {r}" for r in reasons))
            return

        # Phase 2: find classroom. None is a valid outcome for a lesson that
        # needs no room (ST-ARCH-004) — only a face-to-face one has failed.
        room, conflicts = self._get_drop_classroom(cls, day, slot)
        if room is None and needs_physical_room(cls):
            QMessageBox.warning(
                self, tr("dialogs.move_rejected.title"),
                tr("errors.cannot_move_to").format(
                    name=cls["name"], day=display_day(day), slot=slot)
                + "\n\n  - " + tr("errors.no_compatible_classrooms"))
            return

        # Phase 3: classroom-level constraints
        constraint_check = SchedulingWorkflow.validate_drop_constraints(
            self.state_data, cls, day, slot, room)
        if not constraint_check.valid:
            reasons = self._format_drop_reasons(constraint_check.reasons, cls)
            QMessageBox.warning(
                self, tr("dialogs.move_rejected.title"),
                tr("errors.cannot_move_to").format(
                    name=cls["name"], day=display_day(day), slot=slot)
                + "\n\n" + "\n".join(f"  - {r}" for r in reasons))
            return

        if conflicts:
            self._feedback_logger.log_rejected_placement(
                cls, day, slot, room,
                reason="; ".join(conflicts))
            QMessageBox.warning(
                self, tr("dialogs.move_rejected.title"),
                tr("errors.cannot_move_to_room").format(
                    name=cls["name"], day=display_day(day), slot=slot, room=room)
                + "\n\n" + "\n".join(f"  - {c}" for c in conflicts))
            return

        # Log and commit
        old_day = self._drag_backup.get("placed_day")
        old_slot = self._drag_backup.get("placed_time")
        old_room = self._drag_backup.get("placed_classroom")
        self._workflow.log_manual_move(
            cls, old_day, old_slot, old_room, day, slot, room)

        # Replace the pre-emptive undo snapshot (pushed by _start_drag_gfx
        # with an "unplace" label) with a proper "move" snapshot.  The
        # snapshot data is identical — only the label differs.
        if self._undo_stack:
            self._undo_stack.pop()
        self._push_undo(tr("actions.move").format(name=cls["name"]))
        mark_placed(cls, day, slot, room)
        self._drag_success = True
        self._show_toast(
            tr("status.moved_to").format(
                name=cls["name"], day=display_day(day), slot=slot, room=room),
            "success")

    def _format_drop_reasons(self, reasons, cls):
        """Convert workflow reason tuples to translated strings."""
        formatted = []
        for r in reasons:
            kind = r[0]
            if kind == "restricted_to_day":
                formatted.append(tr("warnings.restricted_to_day").format(
                    d=display_day(r[1])))
            elif kind == "slot_not_in_grid":
                formatted.append(tr("validation.slot_not_in_grid").format(r[1]))
            elif kind == "day_not_in_grid":
                formatted.append(tr("validation.day_not_in_grid").format(
                    day_label(r[1])))
            elif kind == "not_enough_slots":
                formatted.append(tr("errors.not_enough_slots").format(
                    n=r[1], a=r[2], t=r[3]))
            elif kind == "day_not_allowed":
                formatted.append(tr("errors.day_not_allowed").format(
                    d=day_label(r[1]),
                    ds=", ".join(day_label(d) for d in r[2])))
            elif kind == "day_excluded":
                formatted.append(tr("errors.day_excluded").format(
                    d=day_label(r[1]),
                    ds=", ".join(day_label(d) for d in r[2])))
            elif kind == "time_not_allowed":
                formatted.append(tr("errors.time_not_allowed").format(
                    t=r[1], ts=", ".join(r[2])))
            elif kind == "time_excluded":
                formatted.append(tr("errors.time_excluded").format(
                    t=r[1], ts=", ".join(r[2])))
            elif kind == "classroom_not_required":
                formatted.append(tr("errors.classroom_not_required").format(
                    r=r[1], rs=", ".join(r[2])))
            elif kind == "classroom_excluded":
                formatted.append(tr("errors.classroom_excluded").format(r=r[1]))
            elif kind == "classroom_capacity":
                formatted.append(tr("errors.classroom_capacity").format(
                    r=r[1], c=r[2], p=r[3]))
            elif kind == "lecturer_unavailable":
                # ST-ARCH-004: the drag path now checks lecturer availability
                # across the whole block, so this reason can reach the dialog.
                formatted.append(tr("validation.lecturer_unavailable").format(
                    r[1], day_label(r[2]), r[3]))
            elif kind == "placement_invalid":
                formatted.append(tr("validation.placement_invalid"))
            else:
                formatted.append(str(r))
        return formatted

    def _edit_selected_class(self):
        if self._selected_classes:
            self._edit_class(self._selected_classes[0])
            return
        if self._selected_class:
            self._edit_class(self._selected_class)

    def _delete_selected(self):
        ul = getattr(self, "unplaced_list", None)
        if ul is not None:
            focus = QApplication.focusWidget()
            ul_focused = (
                focus is ul
                or (focus is not None and ul.isAncestorOf(focus))
                or ul.hasFocus()
                or ul.viewport().hasFocus()
            )
            if ul_focused:
                selected = ul.selected_classes()
                if selected:
                    self._remove_classes(selected)
                return

        if self._selected_classes:
            self._remove_classes(self._selected_classes)
            return
        if self._selected_class:
            self._remove_classes([self._selected_class])

    def _place_at_requested_cell(self, cls, day, slot):
        """Try to place *cls* in the cell the user actually pointed at.

        ``_add_class_at`` took ``day`` and ``slot`` and **discarded both**: it
        went straight to ``_auto_place_and_apply``, which puts the class
        wherever the placer likes. The user double-clicks an empty Wednesday
        10:00, or picks "Ders ekle" from a context menu *headed by*
        "📅 Çarşamba 10:00", and the lesson silently lands somewhere else.

        The sibling action in that same menu —
        ``_place_unplaced_class_at_slot`` — honours the cell, so one menu had
        two commands that disagreed about whether its own title meant anything.

        Placement goes through ``find_valid_options`` filtered to the requested
        cell, which is the unified validator path Phase 3 established
        (ST-ARCH-004): the keyboard, the drag and this all reach one verdict.
        A ``None`` room is kept as-is — it is ``get_room_candidates``' "needs no
        classroom" sentinel for an online lesson, not a failure.

        Returns True when the requested cell was used. False means the cell
        cannot legally hold this class, and the caller falls back to automatic
        placement rather than refusing outright.
        """
        if not day or not slot:
            return False
        options = [opt for opt in find_valid_options(self.state_data, cls)
                   if opt[0] == day and opt[1] == slot]
        if not options:
            return False

        # Prefer the room the user is currently looking at, exactly as
        # _place_unplaced_class_at_slot does, so the lesson appears on the tab
        # they are on rather than on a different one.
        chosen = options[0]
        if self.notebook.currentIndex() == 0:
            kind, value = _decode_classroom_filter_value(
                self.classroom_filter.currentData())
            if kind == "room":
                for opt in options:
                    if opt[2] == value:
                        chosen = opt
                        break
        mark_placed(cls, chosen[0], chosen[1], chosen[2])
        return True

    def _add_class_at(self, day, slot):
        """Add a class via double-click on empty slot — uses automatic placement."""
        if not self.state_data["years"]:
            QMessageBox.information(self, tr("menus.classes"),
                                   tr("errors.setup_years_first"))
            return
        # Tier enforcement: check class limit
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        from scheduler_app.plans import ENTITY_CLASSES
        enforcer = TierEnforcement.instance()
        current_count = len(self.state_data.get("classes", []))
        if not enforcer.require_entity_limit(ENTITY_CLASSES, current_count, self):
            return
        dlg = AddClassDialog(self, self.state_data)
        if dlg.exec() != AddClassDialog.DialogCode.Accepted or not dlg.result:
            return
        cls = dlg.result
        # ST-UI-020: the lecturer combo is editable, so this name may be
        # one the user just typed. Register it BEFORE the undo snapshot,
        # or undo restores a class pointing at a lecturer the restored
        # state does not list -- and the next Setup OK unplaces it.
        cls["lecturer"] = SchedulingWorkflow.register_lecturer(
            self.state_data, cls.get("lecturer")) or ""
        snap_before = capture_snapshot(self.state_data)
        self._push_undo(tr("actions.add").format(name=cls["name"]))
        split_classes = split_non_joint(cls)
        added_any = False
        for sc in split_classes:
            self.state_data["classes"].append(sc)

            if self._place_at_requested_cell(sc, day, slot):
                self._show_toast(tr("status.class_placed"), "success")
                added_any = True
            elif self._auto_place_and_apply(sc):
                self._show_toast(tr("status.class_placed"), "success")
                added_any = True
            else:
                if sc["pinned"]:
                    self.state_data["classes"].remove(sc)
                    from scheduler_app.constraint_negotiator import ConstraintNegotiator
                    neg = ConstraintNegotiator(self.state_data)
                    report = neg.negotiate_class(sc)
                    detail = ""
                    if report["suggestions"]:
                        detail = "\n\n" + report["summary"]
                        for s in report["suggestions"][:3]:
                            detail += f"\n  → {s['description']}"
                    QMessageBox.warning(
                        self, tr("dialogs.error.title"),
                        tr("errors.no_valid_arrangement") + detail)
                else:
                    # Auto-negotiation on placement failure
                    added_any = True
                    from scheduler_app.constraint_negotiator import ConstraintNegotiator
                    neg = ConstraintNegotiator(self.state_data)
                    report = neg.negotiate_class(sc)
                    msg = tr("status.class_added_no_slot")
                    if report["suggestions"]:
                        msg += f"\n{report['suggestions'][0]['description']}"
                    self._show_toast(msg, "info")
                    if hasattr(self, 'warning_log') and report["suggestions"]:
                        for sug in report["suggestions"][:3]:
                            self.warning_log.log(
                                f"{sc['name']}: {sug['description']}", "info")
        if added_any:
            desc = tr("impact.trigger.classes_added").format(n=len(split_classes))
            self._note_structural_change(snap_before, description=desc)
        self.refresh_grid()

    def _select_all_in_view(self, view):
        """Select all lesson items rendered in the given timetable view."""
        scene = view.scene() if view else None
        items = list(getattr(scene, "lesson_items", [])) if scene else []
        if not items:
            self._selected_class = None
            return
        self._clear_empty_slot_selection()
        self._apply_class_selection(items, anchor=items[0])

    def _select_all(self):
        focus = QApplication.focusWidget()
        if hasattr(self, "unplaced_list"):
            ul = self.unplaced_list
            ul_focused = (
                focus is ul
                or (focus is not None and ul.isAncestorOf(focus))
                or ul.hasFocus()
                or ul.viewport().hasFocus()
                or ul.underMouse()
                or ul.viewport().underMouse()
            )
            if ul_focused:
                ul.selectAll()
                return

        if hasattr(self, "_open_slots_scroll"):
            ost = self._open_slots_scroll
            ost_focused = (
                focus is ost
                or (focus is not None and ost.isAncestorOf(focus))
                or ost.hasFocus()
                or ost.underMouse()
            )
            if ost_focused:
                return

        tab_idx = self.notebook.currentIndex() if hasattr(self, "notebook") else -1
        if tab_idx in (0, 1, 2, 3):
            view = [self.grid_view1, self.grid_view2, self.grid_view3, self.grid_view4][tab_idx]
            self._select_all_in_view(view)
            return
        self._selected_class = None


    def _copy_to_clipboard(self):
        s = self.state_data
        if not s["days"] or not s["slots"]:
            return
        placed = get_placed_classes(s)
        if not placed:
            return

        lines = []
        tab_idx = self.notebook.currentIndex()

        if tab_idx == 3:
            days = s["days"]
            slots = s["slots"]
            for yr in sorted(s["years"].keys()):
                branches = s["years"][yr]
                if not branches:
                    continue
                lines.append(f"=== {yr} ===")
                hdr1 = ["", ""]
                for day in days:
                    hdr1.append(tr(f"weekdays.{day}"))
                    hdr1.extend([""] * (len(branches) - 1))
                lines.append("\t".join(hdr1))
                hdr2 = [tr("labels.session"), tr("labels.time")]
                for day in days:
                    for br in branches:
                        hdr2.append(br)
                lines.append("\t".join(hdr2))
                for si, slot in enumerate(slots):
                    row_data = [str(si + 1), csv_safe(slot)]
                    for d_idx, day in enumerate(days):
                        for b_idx, br in enumerate(branches):
                            cell_text = ""
                            for c in placed:
                                c_day = effective_day(c)
                                c_start = effective_time(c)
                                if c_day != day or c_start not in slots:
                                    continue
                                if not any(t["year"] == yr and t["branch"] == br
                                           for t in c["targets"]):
                                    continue
                                occ = occupied_slots_of(s, c)
                                if (day, slot) in occ:
                                    room = classroom_of(c)
                                    cell_text = csv_safe(
                                        f"{c['name']} / {c['lecturer']} / "
                                        f"{room}")
                                    break
                            row_data.append(cell_text)
                    lines.append("\t".join(row_data))
                lines.append("")
        else:
            header = [""] + [tr(f"weekdays.{day}") for day in s["days"]]
            lines.append("\t".join(header))
            filter_fn = [self._filter_classroom, self._filter_group,
                         self._filter_lecturer][tab_idx]
            filtered = [c for c in placed if filter_fn(c)]
            for slot in s["slots"]:
                row_data = [csv_safe(slot)]
                for day in s["days"]:
                    cell_text = ""
                    for c in filtered:
                        occ = occupied_slots_of(s, c)
                        if (day, slot) in occ:
                            cell_text = csv_safe(
                                f"{c['name']} ({c['lecturer']})")
                            break
                    row_data.append(cell_text)
                lines.append("\t".join(row_data))

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self._show_toast(tr("status.copied_to_clipboard"), "success")

    def _show_toast(self, message, kind="info"):
        Toast(self, message, duration=3000, kind=kind)
        if hasattr(self, 'warning_log'):
            self.warning_log.log(message, kind)

    # ══════════════════════════════════════════════════════════════════════
    #  EXCEL EXPORT
    # ══════════════════════════════════════════════════════════════════════


    def _export_to_excel(self):
        # Tier enforcement: Excel export requires feature
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        from scheduler_app.plans import FEATURE_EXPORT_EXCEL
        if not TierEnforcement.instance().require_feature(FEATURE_EXPORT_EXCEL, self):
            return
        if openpyxl is None:
            QMessageBox.critical(
                self, tr("dialogs.error.title"),
                tr("errors.openpyxl_required"))
            return

        s = self.state_data
        if not s["days"] or not s["slots"] or not s["years"]:
            QMessageBox.information(self, tr("buttons.export"), tr("warnings.no_schedule_data"))
            return

        tab_idx = self.notebook.currentIndex() if hasattr(self, "notebook") else 3
        mode = {0: "classroom", 1: "group", 2: "lecturer", 3: "everything", 4: "everything"}.get(tab_idx, "everything")
        default_name = {
            "classroom": "classrooms_schedule.xlsx",
            "group": "groups_schedule.xlsx",
            "lecturer": "lecturers_schedule.xlsx",
            "everything": "schedule.xlsx",
        }.get(mode, "schedule.xlsx")

        fname, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.export.excel_title"),
            os.path.join(storage.sub_dir(storage.EXPORTS_DIR), default_name),
            f"{tr('labels.excel_files')} (*.xlsx);;{tr('labels.all_files')} (*)")
        if not fname:
            return

        try:
            # ST-ARCH-003: one export entry point for all three formats. The
            # workbook writer used to live here, beside a second one in
            # data_io/exporter.py that nothing called; the two drifted, and a
            # fix applied to the unused copy never reached a user.
            from scheduler_app.data_io.exporter import export_schedule
            export_schedule(s, "xlsx", fname, mode=mode)
            QMessageBox.information(self, tr("status.exported"),
                                   f"{tr('status.exported_to')} {os.path.basename(fname)}")
        except Exception as e:
            QMessageBox.critical(self, tr("dialogs.error.title"), f"{tr('errors.failed_to_export')}\n{e}")

    def _export_to_pdf(self):
        # Tier enforcement: PDF export requires feature
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        from scheduler_app.plans import FEATURE_EXPORT_PDF
        if not TierEnforcement.instance().require_feature(FEATURE_EXPORT_PDF, self):
            return
        # ST-SEC-005: report the missing dependency, do not install it. DERSİS
        # reaches the network nowhere, and this was one of the three places it
        # used to. See `dialogs._ensure_excel_deps` for the full argument;
        # `errors.reportlab_required` already ends with the pip command, which
        # remains accurate advice for the only audience that can reach this
        # branch — a developer with an incomplete venv.
        try:
            import reportlab  # noqa: F401
        except ImportError:
            QMessageBox.warning(self, tr("dialogs.export.pdf_title"),
                                tr("errors.reportlab_required"))
            return

        s = self.state_data
        if not s["days"] or not s["slots"] or not s["years"]:
            QMessageBox.information(self, tr("buttons.export"), tr("warnings.no_schedule_data"))
            return

        tab_idx = self.notebook.currentIndex() if hasattr(self, "notebook") else 3
        mode = {0: "classroom", 1: "group", 2: "lecturer",
                3: "everything", 4: "everything"}.get(tab_idx, "everything")
        default_name = {
            "classroom": "classrooms_schedule.pdf",
            "group": "groups_schedule.pdf",
            "lecturer": "lecturers_schedule.pdf",
            "everything": "schedule.pdf",
        }.get(mode, "schedule.pdf")

        fname, _ = QFileDialog.getSaveFileName(
            self, tr("dialogs.export.pdf_title"),
            os.path.join(storage.sub_dir(storage.EXPORTS_DIR), default_name),
            f"{tr('labels.pdf_files')} (*.pdf);;{tr('labels.all_files')} (*)")
        if not fname:
            return

        try:
            from scheduler_app.data_io.exporter import export_schedule
            export_schedule(s, "pdf", fname, mode=mode)
            QMessageBox.information(self, tr("status.exported"),
                                   f"{tr('status.exported_to')} {os.path.basename(fname)}")
        except Exception as e:
            QMessageBox.critical(self, tr("dialogs.error.title"), f"{tr('errors.failed_to_export')}\n{e}")


    def _ensure_excel_deps(self):
        """Return True if pandas+openpyxl are importable.

        A one-line delegate to the module-level ``dialogs._ensure_excel_deps``,
        which had 13 callers to this copy's 2 while the two bodies were
        byte-identical modulo ``self``/``parent``. That is the
        ``data_io/exporter.py`` shape Phase 6 was burned by — two copies, one
        well-exercised, the next fix landing on the wrong one.
        ``DataEditorDialog`` in dialogs.py already delegated the same way; this
        now matches it.
        """
        return _ensure_excel_deps(self)

    # Every state key `_import_from_excel` may overwrite. The import rolls all
    # of them back together, so a failure cannot leave lecturers from the
    # workbook sitting next to classes from the old schedule (ST-FUNC-001).
    _IMPORT_MERGED_KEYS = (
        "lecturers", "lecturer_availability", "classrooms",
        "classroom_capacities", "years", "classes",
    )

    def _import_from_excel(self):
        if not self._ensure_excel_deps():
            return

        fname, _ = QFileDialog.getOpenFileName(
            self, tr("dialogs.import.excel_title"),
            storage.sub_dir(storage.EXPORTS_DIR),
            f"{tr('labels.excel_files')} (*.xlsx);;{tr('labels.all_files')} (*)")
        if not fname:
            return

        from scheduler_app.data_io.importer import load_scheduler_data_from_excel

        dataset = load_scheduler_data_from_excel(fname)
        report = dataset.report

        if not report.is_valid:
            QMessageBox.warning(
                self, tr("status.import_failed"),
                report.summary())
            return

        # Tier enforcement: check limits before importing
        from scheduler_app.ui.tier_enforcement import TierEnforcement
        from scheduler_app.plans import (
            ENTITY_LECTURERS, ENTITY_CLASSROOMS, ENTITY_CLASSES,
        )
        enforcer = TierEnforcement.instance()
        imported = dataset.state
        if imported["lecturers"] and not enforcer.require_entity_limit(
                ENTITY_LECTURERS, len(imported["lecturers"]) - 1, self):
            return
        if imported["classrooms"] and not enforcer.require_entity_limit(
                ENTITY_CLASSROOMS, len(imported["classrooms"]) - 1, self):
            return
        if imported["classes"]:
            total_classes = len(self.state_data.get("classes", [])) + len(imported["classes"])
            if not enforcer.require_entity_limit(
                    ENTITY_CLASSES, total_classes - 1, self):
                return

        # Merge imported data into current state.
        #
        # ST-FUNC-001: this used to merge first and only then call
        # `self._on_state_changed()` / `self.refresh()` -- two methods that do
        # not exist anywhere in the MRO -- so every *successful* import ended in
        # an AttributeError with the state already mutated and the screen never
        # repainted. The merge is now a transaction: anything that raises below,
        # including the repaint, restores the state the user had before.
        s = self.state_data
        rollback = {k: copy.deepcopy(s.get(k)) for k in self._IMPORT_MERGED_KEYS}
        try:
            if dataset.state["lecturers"]:
                s["lecturers"] = dataset.state["lecturers"]
                s["lecturer_availability"] = dataset.state.get("lecturer_availability", {})
            if dataset.state["classrooms"]:
                s["classrooms"] = dataset.state["classrooms"]
                s["classroom_capacities"] = dataset.state.get("classroom_capacities", {})
            if dataset.state["years"]:
                s["years"] = dataset.state["years"]
            if dataset.state["classes"]:
                s["classes"].extend(dataset.state["classes"])

            # An import that replaces the lecturer or room lists orphans any
            # placement referring to one the workbook omitted (ST-DATA-004).
            SchedulingWorkflow.reconcile_placements(s)
            self.refresh_grid()
            self._update_status()
        except Exception as exc:
            for key, value in rollback.items():
                s[key] = value
            try:
                self.refresh_grid()
            except Exception:
                pass  # the repaint is what failed; do not mask the real error
            QMessageBox.critical(self, tr("status.import_failed"), str(exc))
            return

        msg = tr("status.import_successful")
        if report.warnings:
            msg += "\n\n" + report.summary()
        QMessageBox.information(self, tr("dialogs.import.excel_title"), msg)

    def _generate_excel_template(self):
        if not self._ensure_excel_deps():
            return

        fname, _ = QFileDialog.getSaveFileName(
            self, tr("menus.generate_template"),
            os.path.join(storage.sub_dir(storage.EXPORTS_DIR), "scheduler_template.xlsx"),
            f"{tr('labels.excel_files')} (*.xlsx);;{tr('labels.all_files')} (*)")
        if not fname:
            return

        from scheduler_app.data_io.template import generate_excel_template

        try:
            generate_excel_template(fname)
            QMessageBox.information(
                self, tr("status.template_generated"),
                f"{tr('status.excel_template_saved')} {os.path.basename(fname)}")
        except Exception as e:
            QMessageBox.critical(self, tr("dialogs.error.title"), str(e))

    # ══════════════════════════════════════════════════════════════════════
    #  MISC
    # ══════════════════════════════════════════════════════════════════════

    def _show_tutorial(self):
        """Launch the tutorial (from Help menu — replay case)."""
        self._show_tutorial_controlled(None)

    def _show_tutorial_controlled(self, on_done_callback):
        """Launch the tutorial overlay, optionally calling back when done."""
        from scheduler_app.tutorial import TutorialOverlay
        steps = self._tutorial_steps()
        section_names = [
            "tutorial.sec_welcome", "tutorial.sec_interface", "tutorial.sec_setup",
            "tutorial.sec_classes", "tutorial.sec_placement", "tutorial.sec_views",
            "tutorial.sec_panels", "tutorial.sec_optimization", "tutorial.sec_dashboard",
            "tutorial.sec_data", "tutorial.sec_shortcuts",
        ]
        overlay = TutorialOverlay(self, steps, section_names)
        self._active_tutorial = overlay

        def _on_finished():
            self._active_tutorial = None
            if on_done_callback:
                on_done_callback()

        overlay.finished.connect(_on_finished)
        if not on_done_callback:
            from scheduler_app.first_run import _write_flag
            overlay.finished.connect(
                lambda: _write_flag(self._config_path,
                                    "tutorial_seen_or_skipped", True))
        overlay.show()

    def _find_toolbar_btn(self, *names):
        """Find a toolbar QToolButton whose text matches any of *names*."""
        for tb in self.findChildren(QToolBar):
            for btn in tb.findChildren(QToolButton):
                txt = btn.text().strip()
                for name in names:
                    if name in txt:
                        return btn
        return None

    def _get_toolbar(self):
        """Return the main toolbar widget."""
        for tb in self.findChildren(QToolBar):
            if tb.objectName() == "main_toolbar":
                return tb
        return None

    def _tutorial_steps(self):
        """Build the full hierarchical tutorial step list."""
        # Direct widget references (stored during _build_toolbar)
        setup_btn = getattr(self, "_tb_setup_btn", None)
        add_btn = getattr(self, "_tb_add_btn", None)
        place_btn = getattr(self, "_tb_place_btn", None)

        # Section indices (matching section_names order)
        S_WELCOME, S_INTERFACE, S_SETUP = 0, 1, 2
        S_ADDING, S_PLACEMENT, S_VIEWS = 3, 4, 5
        S_PANELS, S_OPTIMIZATION, S_DASHBOARD = 6, 7, 8
        S_DATA, S_SHORTCUTS = 9, 10

        return [
            # ── 0. Welcome ──
            {"widget": None, "title": "tutorial.welcome_title",
             "body": "tutorial.welcome_body", "section": S_WELCOME},

            # ── 1. Interface Overview ──
            {"widget": self.menuBar(), "title": "tutorial.menubar_title",
             "body": "tutorial.menubar_body", "section": S_INTERFACE},
            {"widget": self.notebook.tabBar(), "title": "tutorial.toolbar_title",
             "body": "tutorial.toolbar_body", "section": S_INTERFACE},
            {"widget": self.notebook, "title": "tutorial.tabs_title",
             "body": "tutorial.tabs_body", "section": S_INTERFACE,
             "action": lambda: self.notebook.setCurrentIndex(0)},

            # ── 2. Setup ──
            {"widget": setup_btn, "title": "tutorial.setup_title",
             "body": "tutorial.setup_body", "section": S_SETUP},
            {"widget": setup_btn, "title": "tutorial.setup_tabs_title",
             "body": "tutorial.setup_tabs_body", "section": S_SETUP},

            # ── 3. Classes ──
            {"widget": add_btn, "title": "tutorial.classes_title",
             "body": "tutorial.classes_body", "section": S_ADDING},
            {"widget": add_btn, "title": "tutorial.add_single_title",
             "body": "tutorial.add_single_body", "section": S_ADDING},
            {"widget": add_btn, "title": "tutorial.add_bulk_title",
             "body": "tutorial.add_bulk_body", "section": S_ADDING},
            {"widget": add_btn, "title": "tutorial.edit_classes_title",
             "body": "tutorial.edit_classes_body", "section": S_ADDING},
            {"widget": add_btn, "title": "tutorial.template_title",
             "body": "tutorial.template_body", "section": S_ADDING},

            # ── 4. Placement & Drag-and-Drop ──
            {"widget": place_btn, "title": "tutorial.place_title",
             "body": "tutorial.place_body", "section": S_PLACEMENT},
            {"widget": place_btn, "title": "tutorial.place_actions_title",
             "body": "tutorial.place_actions_body", "section": S_PLACEMENT},
            {"widget": self.grid_view1, "title": "tutorial.dragdrop_title",
             "body": "tutorial.dragdrop_body", "section": S_PLACEMENT,
             "action": lambda: self.notebook.setCurrentIndex(0)},

            # ── 5. Timetable View Modes ──
            {"widget": self.notebook, "title": "tutorial.views_title",
             "body": "tutorial.views_body", "section": S_VIEWS,
             "action": lambda: self.notebook.setCurrentIndex(0)},
            {"widget": self.tab_classroom, "title": "tutorial.view_classroom_title",
             "body": "tutorial.view_classroom_body", "section": S_VIEWS,
             "action": lambda: self.notebook.setCurrentIndex(0)},
            {"widget": self.tab_group, "title": "tutorial.view_group_title",
             "body": "tutorial.view_group_body", "section": S_VIEWS,
             "action": lambda: self.notebook.setCurrentIndex(1)},
            {"widget": self.tab_lecturer, "title": "tutorial.view_lecturer_title",
             "body": "tutorial.view_lecturer_body", "section": S_VIEWS,
             "action": lambda: self.notebook.setCurrentIndex(2)},
            {"widget": self.tab_everything, "title": "tutorial.view_everything_title",
             "body": "tutorial.view_everything_body", "section": S_VIEWS,
             "action": lambda: self.notebook.setCurrentIndex(3)},

            # ── 6. Side Panels ──
            {"widget": self._sidebar_panel, "title": "tutorial.unplaced_title",
             "body": "tutorial.unplaced_body", "section": S_PANELS,
             "action": lambda: (self.notebook.setCurrentIndex(0),
                                self._switch_sidebar_tab(1))},
            {"widget": self._sidebar_panel, "title": "tutorial.openslots_title",
             "body": "tutorial.openslots_body", "section": S_PANELS,
             "action": lambda: self._switch_sidebar_tab(0)},
            {"widget": self.warning_log, "title": "tutorial.warnings_title",
             "body": "tutorial.warnings_body", "section": S_PANELS},
            {"widget": self._zoom_slider, "title": "tutorial.zoom_title",
             "body": "tutorial.zoom_body", "section": S_PANELS},

            # ── 7. Optimization & Rescheduling ──
            {"widget": place_btn, "title": "tutorial.reschedule_title",
             "body": "tutorial.reschedule_body", "section": S_OPTIMIZATION},
            {"widget": None, "title": "tutorial.optimization_modes_title",
             "body": "tutorial.optimization_modes_body", "section": S_OPTIMIZATION},

            # ── 8. Quality Dashboard ──
            {"widget": self.dashboard_widget, "title": "tutorial.dashboard_title",
             "body": "tutorial.dashboard_body", "section": S_DASHBOARD,
             "action": lambda: self.notebook.setCurrentIndex(4)},
            {"widget": self.dashboard_widget, "title": "tutorial.dashboard_metrics_title",
             "body": "tutorial.dashboard_metrics_body", "section": S_DASHBOARD},

            # ── 9. Data Management ──
            {"widget": self.menuBar(), "title": "tutorial.file_menu_title",
             "body": "tutorial.file_menu_body", "section": S_DATA},
            {"widget": self.menuBar(), "title": "tutorial.import_export_title",
             "body": "tutorial.import_export_body", "section": S_DATA},
            {"widget": self._export_btn1, "title": "tutorial.export_btn_title",
             "body": "tutorial.export_btn_body", "section": S_DATA,
             "action": lambda: self.notebook.setCurrentIndex(0)},

            # ── 10. Keyboard Shortcuts ──
            {"widget": None, "title": "tutorial.shortcuts_title",
             "body": "tutorial.shortcuts_body", "section": S_SHORTCUTS},
            {"widget": None, "title": "tutorial.shortcuts_editing_title",
             "body": "tutorial.shortcuts_editing_body", "section": S_SHORTCUTS},

            # ── Done ──
            {"widget": None, "title": "tutorial.done_title",
             "body": "tutorial.done_body", "section": S_WELCOME},
        ]

    def _show_about(self):
        from PyQt6.QtGui import QPixmap
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("dialogs.about.title"))
        dlg.setFixedSize(460, 440)
        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'docs', 'dersis.png')
        if os.path.exists(logo_path):
            dlg.setWindowIcon(QIcon(logo_path))

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header — white background so logo blends in
        header = QFrame()
        header.setStyleSheet(
            'background: white; border: none; border-bottom: 1px solid #E2E8F0;')
        header.setFixedHeight(110)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setStyleSheet('background: transparent; border: none;')
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            scaled = pixmap.scaled(
                QSize(220, 100),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(scaled)
        else:
            logo_label.setText('DERSIS')
            logo_label.setStyleSheet(
                'font-size: 24pt; font-weight: 800; color: #1E293B; '
                'background: transparent;')
        hl.addWidget(logo_label)
        layout.addWidget(header)

        # Body
        body = QFrame()
        body.setStyleSheet('background: #F8FAFC; border: none;')
        bl = QVBoxLayout(body)
        bl.setContentsMargins(32, 20, 32, 16)
        bl.setSpacing(0)

        from scheduler_app._version import __version__ as current_ver
        version_lbl = QLabel(
            tr("dialogs.about.version").replace("{version}", current_ver))
        version_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_lbl.setStyleSheet(
            'font-size: 10pt; font-weight: 600; color: #1E293B; '
            'background: transparent; margin-bottom: 6px;')
        bl.addWidget(version_lbl)

        desc_lbl = QLabel(tr("dialogs.about.description"))
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(
            'font-size: 9pt; color: #64748B; background: transparent; '
            'margin-bottom: 14px;')
        bl.addWidget(desc_lbl)

        rights_lbl = QLabel(tr("dialogs.about.rights"))
        rights_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rights_lbl.setStyleSheet(
            'font-size: 8pt; color: #94A3B8; background: transparent; '
            'margin-bottom: 16px;')
        bl.addWidget(rights_lbl)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet('background: #E2E8F0; max-height: 1px; border: none;')
        bl.addWidget(sep)
        bl.addSpacing(14)

        # Dedication
        ded_lbl = QLabel(tr("dialogs.about.dedication"))
        ded_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ded_lbl.setWordWrap(True)
        ded_lbl.setStyleSheet(
            'font-size: 8.5pt; font-style: italic; color: #64748B; '
            'background: transparent;')
        bl.addWidget(ded_lbl)

        bl.addStretch()
        layout.addWidget(body, 1)

        # Footer
        footer = QFrame()
        footer.setStyleSheet('background: #F8FAFC; border-top: 1px solid #E2E8F0;')
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(24, 10, 24, 10)
        fl.addStretch()
        close_btn = QPushButton(tr('buttons.close'))
        close_btn.setFixedWidth(90)
        close_btn.setStyleSheet(
            'QPushButton { background: #1E293B; color: white; border: none;'
            '  border-radius: 8px; font-size: 9pt; font-weight: 600;'
            '  padding: 7px 16px; }'
            'QPushButton:hover { background: #334155; }')
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(dlg.accept)
        fl.addWidget(close_btn)
        layout.addWidget(footer)

        dlg.exec()

    def _show_features(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("dialogs.about.features_title"))
        dlg.setMinimumSize(520, 480)
        dlg.setMaximumSize(640, 720)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        content = QLabel(tr("dialogs.about.features_html"))
        content.setWordWrap(True)
        content.setTextFormat(Qt.TextFormat.RichText)
        content.setAlignment(Qt.AlignmentFlag.AlignTop)
        content.setContentsMargins(24, 20, 24, 20)
        content.setStyleSheet(
            "QLabel { background: white; color: #1E293B; font-size: 10pt; }")
        scroll.setWidget(content)
        lay.addWidget(scroll)

        close_btn = QPushButton(tr("buttons.close"))
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white; border: none;"
            "  border-radius: 6px; font-size: 9pt; font-weight: bold;"
            "  padding: 8px 16px; }"
            "QPushButton:hover { background: #2563EB; }")
        close_btn.clicked.connect(dlg.accept)
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        btn_lay.addWidget(close_btn)
        btn_lay.setContentsMargins(16, 8, 16, 12)
        lay.addLayout(btn_lay)

        dlg.exec()

    def _open_language_dialog(self):
        """Open the shared LanguageDialog from the top menu."""
        from scheduler_app.first_run import LanguageDialog
        dlg = LanguageDialog(parent=self, current_language=get_language())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            lang = dlg.chosen_language
            if lang != get_language():
                self._set_language(lang)

    def _set_language(self, lang):
        if lang == get_language():
            return
        set_language(lang)
        # Apply layout direction for RTL languages
        app = QApplication.instance()
        if is_rtl(lang):
            app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        else:
            app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        # Mark language as explicitly chosen so the dialog never reappears
        from scheduler_app.first_run import _write_flag
        _write_flag(self._config_path, "language_chosen", True)
        self.setWindowTitle(tr("app.title"))
        self._build_menu()
        for tb in self.findChildren(QToolBar):
            self.removeToolBar(tb)
        self._build_toolbar()
        self.notebook.setTabText(0, "\U0001F3E0  " + tr("menus.view_by_classroom"))
        self.notebook.setTabText(1, "\U0001F393  " + tr("menus.view_by_group"))
        self.notebook.setTabText(2, "\U0001F468\u200D\U0001F3EB  " + tr("menus.view_by_lecturer"))
        self.notebook.setTabText(3, "\U0001F4CB  " + tr("tabs.show_everything"))
        self.notebook.setTabText(4, "\U0001F4CA  " + tr("tabs.dashboard"))
        self.dashboard_widget.retranslate()
        # Refresh sidebar
        self._sidebar_tab_open_slots.setText(
            "\u2B50  " + tr("panels.open_slots"))
        self._sidebar_tab_unplaced.setText(
            "\u26A0  " + tr("panels.unplaced_classes"))
        # Update header title based on active tab
        if self._sidebar_current_tab == 0:
            self._sidebar_title.setText(tr("panels.open_slots"))
        else:
            self._sidebar_title.setText(tr("panels.unplaced_classes"))
        # Refresh filter labels and export buttons
        self._classroom_label.setText("\U0001F3E0  " + tr("filters.classroom"))
        self._group_label.setText("\U0001F393  " + tr("filters.group"))
        self._lecturer_label.setText("\U0001F468\u200D\U0001F3EB  " + tr("filters.lecturer"))
        self._export_btn1.setText("\u21D7  " + tr("buttons.export"))
        self._export_btn2.setText("\u21D7  " + tr("buttons.export"))
        self._export_btn3.setText("\u21D7  " + tr("buttons.export"))
        self._export_btn4.setText("\u21D7  " + tr("buttons.export"))
        for menu in (self._export_menu1, self._export_menu2,
                     self._export_menu3, self._export_menu4):
            actions = menu.actions()
            if len(actions) >= 2:
                actions[0].setText(tr("menus.export_excel"))
                actions[1].setText(tr("menus.export_pdf"))
        # Refresh open slots panel
        self._refresh_open_slots()
        # Refresh sidebar collapse/expand tooltips
        self._sidebar_collapse_btn.setToolTip(tr("panels.collapse_sidebar"))
        self._sidebar_expand_btn.setToolTip(tr("panels.sidebar"))
        # Refresh zoom bar tooltip
        self._zoom_slider.setToolTip(tr("tooltips.zoom"))
        # Refresh warning log panel
        self.warning_log.retranslate()
        # Retranslate active tutorial overlay if one is showing
        if getattr(self, "_active_tutorial", None) is not None:
            self._active_tutorial.retranslate()
        # Retranslate impact badge
        self._update_impact_badge()
        self.refresh_grid()
        # ST-UI-013: the tab bar's size hint is the locale-dependent number
        # that decides whether all five tabs are reachable — 913 px in Korean,
        # 1232 in Indonesian. Changing the language changes the threshold, so
        # the sidebar decision has to be taken again here.
        #
        # activate() first, and it is load-bearing: setText() on the two
        # sidebar buttons only *posts* a layout request, so the panel's
        # minimumSizeHint still reports the previous language's number until it
        # is applied. Measured without it, switching to Azerbaijani after
        # Arabic decided against 350 and got a 564 px panel, and 201 px of tab
        # bar went behind the scroll arrow.
        sidebar_layout = self._sidebar_panel.layout()
        if sidebar_layout is not None:
            sidebar_layout.activate()
        self._apply_sidebar_intent()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_sidebar_is_collapsed'):
            self._apply_sidebar_intent()
            self._position_expand_buttons()

    def _on_quit(self):
        if self.state_data["classes"]:
            resp = QMessageBox.question(
                self, tr("dialogs.quit.title"), tr("dialogs.quit.save_prompt"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if resp == QMessageBox.StandardButton.Cancel:
                return
            if resp == QMessageBox.StandardButton.Yes:
                self.save_file()
        self.close()

    def closeEvent(self, event):
        # ST-UI-013: before anything that can fail or ask a question, because
        # a geometry written for a close the user then cancels is simply the
        # current geometry, and costs nothing.
        self._save_window_geometry()
        # ST-DATA-005: quitting is the moment an unsaved change becomes
        # permanently lost, so a failure here is the one worth interrupting the
        # user for, even though _report_settings_problem has rate-limited it.
        if self.flush_auto_save():
            event.accept()
            return
        resp = QMessageBox.question(
            self, tr("status.settings_problem_title"),
            tr("errors.settings_quit_anyway"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if resp == QMessageBox.StandardButton.Yes:
            event.accept()
        else:
            event.ignore()
