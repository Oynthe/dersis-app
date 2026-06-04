"""Hierarchical interactive tutorial with spotlight overlay and guided tour.

The overlay dims the entire window and paints four semi-transparent rectangles
around a spotlight cutout so the target widget remains fully visible.  Steps
are organised into numbered *sections* (language, setup, adding classes, …)
with a progress bar at the top of the card.  Each step may carry an optional
*action* callback that fires when the step becomes active (e.g. switching to
a specific tab, opening a panel, scrolling to a widget).
"""

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QGraphicsDropShadowEffect, QProgressBar, QScrollArea,
)
from PyQt6.QtCore import Qt, QRect, QRectF, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath

from scheduler_app.translations import tr


class TutorialOverlay(QWidget):
    """Full-window overlay that highlights one target widget per step.

    Each step is a dict:
        widget   – QWidget or None (None → no spotlight, card centred)
        title    – translation key for heading
        body     – translation key for body text
        section  – int section index (for progress bar)
        action   – optional callable() run when the step activates
        widget_fn – optional callable() -> QWidget evaluated lazily
    """

    finished = pyqtSignal()  # emitted on skip or complete

    _DIM = QColor(15, 23, 42, 170)   # dark blue-gray, ~67 %
    _MARGIN = 14                       # px padding around target widget
    _CARD_MAX_W = 440
    _CARD_MIN_W = 340
    _CORNER_R = 10                     # spotlight corner radius

    SECTION_NAME_KEYS = [
        "tutorial.sec_welcome",
        "tutorial.sec_interface",
        "tutorial.sec_setup",
        "tutorial.sec_classes",
        "tutorial.sec_placement",
        "tutorial.sec_views",
        "tutorial.sec_panels",
        "tutorial.sec_optimization",
        "tutorial.sec_dashboard",
        "tutorial.sec_data",
        "tutorial.sec_shortcuts",
    ]

    def __init__(self, parent, steps, section_names=None):
        super().__init__(parent)
        self._steps = steps
        self._current = 0
        self._section_names = section_names or self.SECTION_NAME_KEYS

        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ── info card ──────────────────────────────────────────────
        self._card = QWidget(self)
        self._card.setObjectName("tutorial_card")
        self._card.setStyleSheet(
            "#tutorial_card {"
            "  background: white; border-radius: 12px;"
            "  border: 2px solid #3B82F6;"
            "}")
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 90))
        self._card.setGraphicsEffect(shadow)

        cl = QVBoxLayout(self._card)
        cl.setContentsMargins(24, 18, 24, 16)
        cl.setSpacing(8)

        # Section / progress header
        self._section_lbl = QLabel()
        self._section_lbl.setStyleSheet(
            "color: #3B82F6; font-size: 8.5pt; font-weight: bold;"
            " background: transparent; margin-bottom: 0px;")
        cl.addWidget(self._section_lbl)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setStyleSheet(
            "QProgressBar { background: #E2E8F0; border: none; border-radius: 2px; }"
            "QProgressBar::chunk { background: #3B82F6; border-radius: 2px; }")
        cl.addWidget(self._progress)

        self._step_lbl = QLabel()
        self._step_lbl.setStyleSheet(
            "color: #94A3B8; font-size: 8pt;"
            " background: transparent; margin-top: 4px; margin-bottom: 2px;")
        cl.addWidget(self._step_lbl)

        self._title_lbl = QLabel()
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet(
            "color: #1E293B; font-size: 13pt; font-weight: bold;"
            " background: transparent;")
        cl.addWidget(self._title_lbl)

        self._body_lbl = QLabel()
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setStyleSheet(
            "color: #475569; font-size: 9.5pt; background: transparent;"
            " line-height: 1.45; margin-top: 2px;")
        cl.addWidget(self._body_lbl)

        # button row
        br = QHBoxLayout()
        br.setSpacing(8)

        self._skip_btn = QPushButton(tr("tutorial.skip"))
        self._skip_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #94A3B8;"
            "  border: none; font-size: 9pt; padding: 6px 12px; }"
            "QPushButton:hover { color: #64748B; }")
        self._skip_btn.clicked.connect(self._skip)
        br.addWidget(self._skip_btn)

        br.addStretch()

        self._back_btn = QPushButton(tr("tutorial.back"))
        self._back_btn.setStyleSheet(
            "QPushButton { background: #F1F5F9; color: #475569;"
            "  border: 1px solid #CBD5E1; border-radius: 6px;"
            "  font-size: 9pt; padding: 6px 16px; }"
            "QPushButton:hover { background: #E2E8F0; }")
        self._back_btn.clicked.connect(self._back)
        br.addWidget(self._back_btn)

        self._next_btn = QPushButton(tr("tutorial.next"))
        self._next_btn.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white; border: none;"
            "  border-radius: 6px; font-size: 9pt; font-weight: bold;"
            "  padding: 6px 20px; }"
            "QPushButton:hover { background: #2563EB; }")
        self._next_btn.clicked.connect(self._next)
        br.addWidget(self._next_btn)

        cl.addLayout(br)

        self._spot = QRect()   # spotlight rect (empty = no target)
        self._apply_step()

    # ── navigation ────────────────────────────────────────────────

    def _next(self):
        if self._current >= len(self._steps) - 1:
            self._finish()
            return
        self._current += 1
        self._apply_step()

    def _back(self):
        if self._current > 0:
            self._current -= 1
            self._apply_step()

    def _skip(self):
        self._finish()

    def _finish(self):
        self.finished.emit()
        self.close()
        self.deleteLater()

    # ── step rendering ────────────────────────────────────────────

    def _resolve_widget(self, step):
        """Return the target widget for a step, evaluating widget_fn if needed."""
        if "widget_fn" in step and step["widget_fn"] is not None:
            try:
                return step["widget_fn"]()
            except Exception:
                return None
        return step.get("widget")

    def _apply_step(self):
        step = self._steps[self._current]
        total = len(self._steps)

        # Run action callback if present (e.g. switch tab)
        action = step.get("action")
        if action:
            try:
                action()
            except Exception:
                pass

        # Allow a brief moment for the UI to update after actions
        QTimer.singleShot(50, self._render_step)

    def _render_step(self):
        step = self._steps[self._current]
        total = len(self._steps)
        section = step.get("section", 0)

        # Section label and progress
        n_sections = len(self._section_names)
        if section < n_sections:
            sec_name = tr(self._section_names[section])
        else:
            sec_name = ""
        self._section_lbl.setText(
            f"\u25CF  {sec_name}" if sec_name else "")

        self._progress.setMaximum(total)
        self._progress.setValue(self._current + 1)

        self._step_lbl.setText(
            tr("tutorial.step_counter").format(n=self._current + 1, total=total))
        self._title_lbl.setText(tr(step["title"]))
        self._body_lbl.setText(tr(step["body"]))

        self._back_btn.setVisible(self._current > 0)

        is_last = self._current >= total - 1
        self._next_btn.setText(
            tr("tutorial.finish") if is_last else tr("tutorial.next"))
        self._skip_btn.setVisible(not is_last)

        # Compute spotlight rectangle
        widget = self._resolve_widget(step)
        if widget is not None and widget.isVisible():
            m = self._MARGIN
            tl = widget.mapTo(self.parentWidget(), widget.rect().topLeft())
            self._spot = QRect(
                tl.x() - m, tl.y() - m,
                widget.width() + 2 * m, widget.height() + 2 * m)
            # Ensure spotlight is within visible window bounds
            win = self.rect()
            self._spot = self._spot.intersected(win)
        else:
            self._spot = QRect()

        self._position_card()
        self.update()

    def retranslate(self):
        """Re-render the current step with updated translations."""
        self._skip_btn.setText(tr("tutorial.skip"))
        self._back_btn.setText(tr("tutorial.back"))
        self._render_step()

    def _position_card(self):
        self._card.adjustSize()
        cw = min(self._CARD_MAX_W,
                 max(self._CARD_MIN_W, self._card.sizeHint().width()))
        layout = self._card.layout()
        ch = layout.totalHeightForWidth(cw)
        if ch <= 0:
            ch = self._card.sizeHint().height()
        self._card.setFixedSize(cw, ch)

        win = self.rect()

        if self._spot.isNull():
            x = (win.width() - cw) // 2
            y = (win.height() - ch) // 2
        else:
            sr = self._spot
            gap = 18

            # Prefer below, then above, then beside
            y = sr.bottom() + gap
            if y + ch > win.height() - 10:
                y = sr.top() - ch - gap
            if y < 10:
                y = max(10, sr.center().y() - ch // 2)

            x = sr.center().x() - cw // 2
            x = max(10, min(x, win.width() - cw - 10))

        self._card.move(int(x), int(y))

    # ── painting ──────────────────────────────────────────────────

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self.rect()

        if self._spot.isNull():
            p.fillRect(full, self._DIM)
        else:
            sr = self._spot
            dim = self._DIM
            # top strip
            p.fillRect(QRect(0, 0, full.width(), sr.top()), dim)
            # bottom strip
            p.fillRect(QRect(0, sr.bottom(), full.width(),
                             full.height() - sr.bottom()), dim)
            # left strip
            p.fillRect(QRect(0, sr.top(), sr.left(), sr.height()), dim)
            # right strip
            p.fillRect(QRect(sr.right(), sr.top(),
                             full.width() - sr.right(), sr.height()), dim)
            # blue highlight border
            pen = QPen(QColor(59, 130, 246, 220), 3)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(sr), self._CORNER_R, self._CORNER_R)

        p.end()

    # ── events ────────────────────────────────────────────────────

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.setGeometry(self.parentWidget().rect())
        self._render_step()

    def mousePressEvent(self, event):  # noqa: N802
        event.accept()

    def keyPressEvent(self, event):  # noqa: N802
        k = event.key()
        if k == Qt.Key.Key_Escape:
            self._skip()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self._next()
        elif k == Qt.Key.Key_Left:
            self._back()
        else:
            event.accept()

    def show(self):
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        super().show()
        self.setFocus()
