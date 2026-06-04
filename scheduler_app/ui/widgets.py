"""Reusable PyQt6 widgets: Toast, MultiSelectButton, WarningLogPanel."""

from PyQt6.QtWidgets import (
    QLabel, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QMenu, QWidgetAction, QCheckBox, QScrollArea, QFrame,
    QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import QTimer, Qt, QPropertyAnimation, QEasingCurve, QEvent
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
        lbl.setStyleSheet(f"color: {fg}; font-weight: bold; font-size: 10pt; border: none;")
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(350)
        layout.addWidget(lbl)
        self.adjustSize()

        # Position at bottom-right of parent
        pw = parent.width()
        ph = parent.height()
        tw = self.width()
        th = self.height()
        self.move(pw - tw - 20, ph - th - 20)

        self.setWindowFlags(Qt.WindowType.ToolTip)
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


class WarningLogPanel(QFrame):
    """Persistent expandable warning/status log at bottom of main window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages = []
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

    def log(self, message, kind="info"):
        """Add a message to the log."""
        colors = {
            "info": "#1E40AF", "success": "#166534",
            "warning": "#92400E", "error": "#991B1B",
        }
        icons = {
            "info": "\u2139\uFE0F", "success": "\u2705", "warning": "\u26A0\uFE0F", "error": "\u274C",
        }
        color = colors.get(kind, colors["info"])
        icon = icons.get(kind, "i")
        self._messages.append((message, kind))
        self._latest_label.setText(message)
        self._latest_label.setStyleSheet(
            f"font-size: 9pt; color: {color}; font-weight: bold;")
        self._icon_label.setText(icon)
        self._icon_label.setStyleSheet(
            f"font-size: 9pt; font-weight: bold; color: {color};")
        # Update expanded log
        lines = []
        for msg, k in self._messages:
            c = colors.get(k, colors["info"])
            lines.append(f'<span style="color:{c}">{msg}</span>')
        self._log_area.setHtml("<br>".join(lines))
        # Auto-scroll to bottom
        sb = self._log_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear(self):
        self._messages.clear()
        self._latest_label.setText("—")
        self._latest_label.setStyleSheet("font-size: 9pt; color: #475569;")
        self._icon_label.setText("")
        self._log_area.clear()

    def _toggle_expand(self):
        self._expanded = not self._expanded
        self._log_area.setVisible(self._expanded)
        self._toggle_btn.setText(f"\u25B2 {tr('buttons.collapse')}" if self._expanded else f"\u25BC {tr('buttons.expand')}")
        self.setMaximumHeight(160 if self._expanded else 30)

    def retranslate(self):
        """Update button texts after language change."""
        self._toggle_btn.setText(f"\u25B2 {tr('buttons.collapse')}" if self._expanded else f"\u25BC {tr('buttons.expand')}")
        self._clear_btn.setText(f"\u2715 {tr('buttons.clear')}")
