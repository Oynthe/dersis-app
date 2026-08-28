"""Reusable PyQt6 widgets: Toast, MultiSelectButton, WarningLogPanel."""
import html

from PyQt6.QtWidgets import (
    QLabel, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QMenu, QWidgetAction, QCheckBox, QScrollArea, QFrame,
    QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import (
    QTimer, Qt, QPropertyAnimation, QEasingCurve, QEvent, QPoint,
)
from PyQt6.QtGui import QColor, QAction, QPainter, QBrush, QPen

from scheduler_app.translations import tr


class Toast(QWidget):
    """Non-blocking popup notification that auto-dismisses."""

    COLORS = {
        "info": ("#1E40AF", "#DBEAFE"),
        "success": ("#166534", "#DCFCE7"),
        "warning": ("#92400E", "#FEF3C7"),
        "error": ("#991B1B", "#FEE2E2"),
    }

    def __init__(self, parent, message, duration=3000, kind="info"):
        super().__init__(parent)
        fg, bg = self.COLORS.get(kind, self.COLORS["info"])

        self.setStyleSheet(
            f"background: {bg}; border: 2px solid {fg}; border-radius: 6px;")

        _toast_icons = {
            "info": "\u2139\uFE0F", "success": "\u2705",
            "warning": "\u26A0\uFE0F", "error": "\u274C",
        }
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        icon_text = _toast_icons.get(kind, "")
        lbl = QLabel(f"{icon_text}  {message}" if icon_text else message)
        # ST-UI-007. A QLabel defaults to AutoText, which means Qt decides
        # PER STRING whether to parse markup — True for `<` plus a tag it knows,
        # False otherwise. So a toast about a class called "9-A <B> Subesi"
        # renders as markup while the next one renders literally, decided by the
        # user's own data. Measured: QLabel('R&D <b>Lab</b>') has sizeHint width
        # 84 as AutoText and 168 as PlainText — Qt was eating half the message.
        # Removing Qt's choice is the fix here, NOT escaping: html.escape on a
        # string Qt would have shown literally puts '&amp;' on the screen.
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setStyleSheet(f"color: {fg}; font-weight: bold; font-size: 10pt; border: none;")
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(350)
        layout.addWidget(lbl)
        self.adjustSize()

        # Position at bottom-right of parent.
        #
        # ST-UI-010: the flag goes on FIRST, and the move is mapped through the
        # parent. ``Qt.WindowType.ToolTip`` makes this a *top-level window*, and
        # ``QWidget.move`` on a window takes **global** coordinates — while
        # ``pw - tw - 20`` is expressed in the parent's **local** ones. Moving
        # before the flag (which is what this did) therefore pinned the toast to
        # one fixed point on the *screen* rather than to the window's corner:
        # measured at 929,650 for every window origin, i.e. displaced by exactly
        # the window's own screen offset — (-502, -332) for a window at
        # (500, 330) — and off the window altogether once it is moved right or
        # onto a second monitor. The two agreed only for a window at the display
        # origin, which is why it reads as correct on a maximised single screen.
        self.setWindowFlags(Qt.WindowType.ToolTip)
        pw = parent.width()
        ph = parent.height()
        tw = self.width()
        th = self.height()
        self.move(parent.mapToGlobal(QPoint(pw - tw - 20, ph - th - 20)))

        self.show()
        self.raise_()

        QTimer.singleShot(duration, self._fade_out)

    def _fade_out(self):
        try:
            self.close()
            self.deleteLater()
        except RuntimeError:
            pass


class MultiSelectButton(QPushButton):
    """A button that shows a dropdown with checkboxes for multi-selection."""

    def __init__(self, items=None, parent=None, labels=None):
        super().__init__(parent)
        self._items = list(items or [])
        self._labels = labels or {}  # {item_key: display_label}
        self._checked = set()
        self._menu = None
        self.setStyleSheet(
            "text-align: left; padding: 2px 6px; font-size: 8pt;")
        self._update_text()
        self.clicked.connect(self._toggle_menu)

    def set_items(self, items, labels=None):
        self._items = list(items)
        if labels is not None:
            self._labels = labels
        self._checked &= set(items)
        self._update_text()

    def checked_items(self):
        return [it for it in self._items if it in self._checked]

    def set_checked(self, items):
        self._checked = set(items) & set(self._items)
        self._update_text()

    def _display(self, item):
        return self._labels.get(item, item)

    def _update_text(self):
        if not self._checked:
            self.setText("—")
        elif len(self._checked) <= 2:
            self.setText(", ".join(self._display(c) for c in sorted(self._checked)))
        else:
            self.setText(f"{len(self._checked)} {tr('labels.selected')}")
        self.setToolTip(
            ", ".join(self._display(c) for c in sorted(self._checked))
            if self._checked else "")

    def _toggle_menu(self):
        if self._menu is not None and self._menu.isVisible():
            self._menu.close()
            return
        self._show_menu()

    def _show_menu(self):
        menu = QMenu(self)
        self._menu = menu
        menu.installEventFilter(self)
        for item in self._items:
            cb = QCheckBox(self._display(item))
            cb.setChecked(item in self._checked)
            cb.installEventFilter(self)
            cb.stateChanged.connect(
                lambda state, it=item: self._toggle(it, state))
            wa = QWidgetAction(menu)
            wa.setDefaultWidget(cb)
            menu.addAction(wa)
        if self._items:
            menu.addSeparator()
            clear_act = menu.addAction(tr("buttons.clear_all"))
            clear_act.triggered.connect(self._clear_all)
        menu.addSeparator()
        done_act = menu.addAction(tr("buttons.ok"))
        done_act.triggered.connect(menu.close)
        menu.aboutToHide.connect(self._on_menu_closed)
        menu.popup(self.mapToGlobal(self.rect().bottomLeft()))

    def _on_menu_closed(self):
        self._menu = None

    def _toggle(self, item, state):
        if state == Qt.CheckState.Checked.value:
            self._checked.add(item)
        else:
            self._checked.discard(item)
        self._update_text()

    def _clear_all(self):
        self._checked.clear()
        self._update_text()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape):
            if self._menu is not None and self._menu.isVisible():
                self._menu.close()
                return True
        return super().eventFilter(obj, event)


# Palette and glyphs for a log entry. Module constants because they used to be
# rebuilt inside log(), which ran once per appended message.
_LOG_COLORS = {
    "info": "#1E40AF", "success": "#166534",
    "warning": "#92400E", "error": "#991B1B",
}
_LOG_ICONS = {
    "info": "\u2139\uFE0F", "success": "\u2705",
    "warning": "\u26A0\uFE0F", "error": "\u274C",
}


class WarningLogPanel(QFrame):
    """Persistent expandable warning/status log at bottom of main window.

    ST-PERF-003. Every entry used to be appended to one list that was never
    cleared, while the panel rebuilt its entire HTML from that list on every
    refresh. Twelve refreshes of an unchanged 250-class timetable grew the list
    138 -> 1656 entries, the rendered document 404 -> 8099 characters, process
    RSS by 480 MB, and per-refresh time from 2.1 s to 4.8 s. The log was
    describing a timetable that no longer existed, at ever-increasing cost.

    The fix is to separate two genuinely different kinds of message:

    **sticky**  things that happened once and belong to history — a save
                failure, an import result, a user action. Appended.
    **derived** findings recomputed from the current timetable on every
                refresh: conflicts, unplaced classes, negotiation notes.
                Replaced wholesale, because the previous set describes a
                timetable that has moved on.

    ``_messages`` keeps its name — the finding and the roadmap's completion
    criterion both name it — and is now the concatenation of the two.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sticky = []
        self._derived = []
        self._expanded = False

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "WarningLogPanel { background: #F8FAFC; border-top: 1px solid #CBD5E1; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(0)

        # Header row: latest message + expand/collapse button
        header = QHBoxLayout()
        header.setSpacing(6)
        self._icon_label = QLabel("")
        self._icon_label.setFixedWidth(16)
        header.addWidget(self._icon_label)
        self._latest_label = QLabel("—")
        # ST-UI-007, same reason as Toast above. This label shows the newest
        # warning, which interpolates class and branch names the user typed.
        # `_line()` already escapes on the way into the QTextEdit body; the
        # header is the half that was left.
        self._latest_label.setTextFormat(Qt.TextFormat.PlainText)
        self._latest_label.setStyleSheet("font-size: 9pt; color: #475569;")
        self._latest_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(self._latest_label)
        self._toggle_btn = QPushButton(f"\u25BC {tr('buttons.expand')}")
        self._toggle_btn.setFixedWidth(80)
        self._toggle_btn.setStyleSheet(
            "font-size: 8pt; border-radius: 4px; padding: 2px 6px;")
        self._toggle_btn.clicked.connect(self._toggle_expand)
        header.addWidget(self._toggle_btn)
        self._clear_btn = QPushButton(f"\u2715 {tr('buttons.clear')}")
        self._clear_btn.setFixedWidth(60)
        self._clear_btn.setStyleSheet(
            "font-size: 8pt; border-radius: 4px; padding: 2px 6px;")
        self._clear_btn.clicked.connect(self.clear)
        header.addWidget(self._clear_btn)
        layout.addLayout(header)

        # Expandable log area
        self._log_area = QTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setStyleSheet(
            "font-size: 8pt; color: #334155; background: #F1F5F9; "
            "border: 1px solid #E2E8F0;")
        self._log_area.setMaximumHeight(120)
        self._log_area.setVisible(False)
        layout.addWidget(self._log_area)

        self.setMaximumHeight(30)

    @property
    def _messages(self):
        """History followed by the findings derived from the current state."""
        return self._sticky + self._derived

    def log(self, message, kind="info"):
        """Append one message to the sticky history.

        For anything recomputed from the timetable on every refresh, use
        :meth:`set_derived` instead — appending those is ST-PERF-003.
        """
        self._sticky.append((message, kind))
        self._append_rendered(message, kind)
        self._set_header(message, kind)

    def set_derived(self, messages):
        """Replace every finding derived from the current timetable.

        *messages* is an ordered sequence of ``(text, kind)``. Replacing rather
        than appending is the point: the previous set described a timetable that
        has since changed, so keeping it is both wrong and unbounded.
        """
        new = [(str(m), k) for m, k in messages]
        if new == self._derived:
            return  # nothing to redraw; the common case on a repaint
        self._derived = new
        self._render_all()
        combined = self._messages
        if combined:
            self._set_header(*combined[-1])
        else:
            self._reset_header()

    def clear(self):
        """User pressed Clear: empty both stores."""
        self._sticky.clear()
        self._derived.clear()
        self._reset_header()
        self._log_area.clear()

    # ── Rendering ────────────────────────────────────────────────────────

    @staticmethod
    def _line(message, kind):
        # escape(): these strings carry user-controlled class and branch names
        # straight into rich text (ST-UI-007). The panel was already doing this
        # unescaped; re-committing that into new code is avoidable even though
        # the finding itself belongs to a later phase.
        color = _LOG_COLORS.get(kind, _LOG_COLORS["info"])
        return f'<span style="color:{color}">{html.escape(str(message))}</span>'

    def _append_rendered(self, message, kind):
        """Add one line without re-rendering the whole document."""
        self._log_area.append(self._line(message, kind))
        sb = self._log_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _render_all(self):
        self._log_area.setHtml(
            "<br>".join(self._line(m, k) for m, k in self._messages))
        sb = self._log_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_header(self, message, kind):
        color = _LOG_COLORS.get(kind, _LOG_COLORS["info"])
        self._latest_label.setText(str(message))
        self._latest_label.setStyleSheet(
            f"font-size: 9pt; color: {color}; font-weight: bold;")
        self._icon_label.setText(_LOG_ICONS.get(kind, "i"))
        self._icon_label.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {color};")

    def _reset_header(self):
        self._latest_label.setText("—")
        self._latest_label.setStyleSheet("font-size: 9pt; color: #475569;")
        self._icon_label.setText("")

    def _toggle_expand(self):
        self._expanded = not self._expanded
        self._log_area.setVisible(self._expanded)
        self._toggle_btn.setText(f"\u25B2 {tr('buttons.collapse')}" if self._expanded else f"\u25BC {tr('buttons.expand')}")
        self.setMaximumHeight(160 if self._expanded else 30)

    def retranslate(self):
        """Update button texts after language change."""
        self._toggle_btn.setText(f"\u25B2 {tr('buttons.collapse')}" if self._expanded else f"\u25BC {tr('buttons.expand')}")
        self._clear_btn.setText(f"\u2715 {tr('buttons.clear')}")
