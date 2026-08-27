"""Quality Analytics Dashboard — visual display of schedule metrics.

Pure PyQt6 implementation using painted bar charts, tables, and summary cards.
No external plotting libraries required.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QGridLayout, QFrame, QSizePolicy, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget,
)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QFont, QPen, QBrush

from scheduler_app.translations import tr
from scheduler_app.models import effective_day, effective_time, effective_room
from scheduler_app.ui.day_keys import day_label
from scheduler_app.analytics import compute_all_metrics
from scheduler_app.timetable_scorer import TimetableScorer
from scheduler_app.schedule_analytics import ScheduleAnalytics


# ── Tiny painted bar chart ────────────────────────────────────────────

class BarChartWidget(QWidget):
    """Horizontal bar chart drawn with QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []  # list of (label, value)
        self._max_val = 1
        self._bar_color = QColor("#3B82F6")
        self._suffix = ""
        self.setMinimumHeight(40)

    def set_data(self, data, color="#3B82F6", suffix=""):
        self._data = list(data)
        self._max_val = max((v for _, v in self._data), default=1) or 1
        self._bar_color = QColor(color)
        self._suffix = suffix
        self.setMinimumHeight(max(40, len(self._data) * 28 + 10))
        self.update()

    def paintEvent(self, event):
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        label_w = min(140, int(w * 0.30))
        bar_area = w - label_w - 70
        row_h = 24
        y = 5

        font = QFont("Segoe UI", 8)
        p.setFont(font)

        for label, value in self._data:
            # Label
            p.setPen(QPen(QColor("#334155")))
            p.drawText(QRectF(4, y, label_w - 8, row_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       str(label))
            # Bar
            bar_w = max(2, int(bar_area * value / self._max_val))
            p.setBrush(QBrush(self._bar_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(label_w, y + 3, bar_w, row_h - 6), 3, 3)
            # Value label
            p.setPen(QPen(QColor("#64748B")))
            val_text = f"{value}{self._suffix}"
            p.drawText(QRectF(label_w + bar_w + 4, y, 60, row_h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       val_text)
            y += row_h + 4
        p.end()


# ── Summary card ──────────────────────────────────────────────────────

class MetricCard(QFrame):
    """Small summary card showing a number and a label."""

    def __init__(self, title="", value="", color="#3B82F6", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"MetricCard {{ background: white; border: 1px solid #E2E8F0;"
            f" border-radius: 10px; border-left: 4px solid {color}; }}")
        self.setFixedHeight(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(2)
        self._title = QLabel(title)
        self._title.setStyleSheet("font-size: 8pt; color: #64748B; border: none;")
        layout.addWidget(self._title)
        self._value = QLabel(str(value))
        self._value.setStyleSheet(
            f"font-size: 18pt; font-weight: bold; color: {color}; border: none;")
        layout.addWidget(self._value)

    def set_value(self, value, title=None):
        self._value.setText(str(value))
        if title is not None:
            self._title.setText(title)


# ── Section panel ─────────────────────────────────────────────────────

class SectionPanel(QFrame):
    """Titled section panel with a content area."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "SectionPanel { background: white; border: 1px solid #E2E8F0;"
            " border-radius: 8px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        heading = QLabel(title)
        heading.setStyleSheet(
            "font-size: 10pt; font-weight: bold; color: #1E293B;"
            " border: none; padding-bottom: 4px;")
        layout.addWidget(heading)
        self._heading = heading
        self._content_layout = layout

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)

    def set_title(self, title):
        self._heading.setText(title)


# ── Quality gauge widget ─────────────────────────────────────────────

class QualityGaugeWidget(QWidget):
    """Circular gauge showing schedule quality score out of 100."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0
        self._label = ""
        self.setMinimumHeight(200)
        self.setMinimumWidth(200)

    def set_score(self, score, label):
        self._score = max(0, min(100, score))
        self._label = label
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        side = min(w, h) - 20
        cx = w / 2
        cy = h / 2 + 10
        radius = side / 2

        # Background arc (full)
        arc_rect = QRectF(cx - radius, cy - radius, side, side)
        pen = QPen(QColor("#E2E8F0"), 14)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(arc_rect, 225 * 16, -270 * 16)

        # Score color
        if self._score >= 80:
            color = QColor("#10B981")
        elif self._score >= 60:
            color = QColor("#3B82F6")
        elif self._score >= 40:
            color = QColor("#F59E0B")
        else:
            color = QColor("#EF4444")

        # Filled arc
        pen = QPen(color, 14)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        span = int(-270 * 16 * self._score / 100)
        p.drawArc(arc_rect, 225 * 16, span)

        # Score text
        p.setPen(QPen(QColor("#0F172A")))
        font = QFont("Segoe UI", 28, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRectF(cx - 60, cy - 30, 120, 50),
                   Qt.AlignmentFlag.AlignCenter, str(self._score))

        # Label text
        p.setPen(QPen(color))
        font = QFont("Segoe UI", 11, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRectF(cx - 60, cy + 20, 120, 25),
                   Qt.AlignmentFlag.AlignCenter, self._label)

        # "/100" text
        p.setPen(QPen(QColor("#94A3B8")))
        font = QFont("Segoe UI", 9)
        p.setFont(font)
        p.drawText(QRectF(cx - 30, cy + 42, 60, 20),
                   Qt.AlignmentFlag.AlignCenter, "/ 100")

        p.end()


# ── Main Dashboard Widget ────────────────────────────────────────────

class DashboardWidget(QWidget):
    """Analytics dashboard that replaces the timetable grid when active."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = None

        # Scroll wrapper
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: #F8FAFC; }"
            "QWidget#dashContent { background: #F8FAFC; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("dashContent")
        scroll.setWidget(content)
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(16, 12, 16, 12)
        self._layout.setSpacing(14)

        # ── Header ──
        header = QLabel("\U0001F4CA  " + tr("dashboard.title"))
        header.setStyleSheet(
            "font-size: 14pt; font-weight: bold; color: #0F172A; padding: 4px 0;")
        self._layout.addWidget(header)
        self._header = header

        # ── Summary cards row ──
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._card_placed = MetricCard(tr("labels.placed"), "0", "#10B981")
        self._card_unplaced = MetricCard(tr("labels.unplaced"), "0", "#EF4444")
        self._card_rooms = MetricCard(tr("dashboard.avg_room_use"), "0%", "#3B82F6")
        self._card_gaps = MetricCard(tr("dashboard.total_gaps"), "0", "#F59E0B")
        for c in [self._card_placed, self._card_unplaced, self._card_rooms, self._card_gaps]:
            cards_row.addWidget(c)
        self._layout.addLayout(cards_row)

        # ── Tabs for metric sections ──
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #E2E8F0; border-radius: 6px;"
            " background: #F8FAFC; }"
            "QTabBar::tab { padding: 6px 14px; font-size: 9pt; }"
            "QTabBar::tab:selected { font-weight: bold; color: #1E40AF; }"
            "QTabBar::scroller { width: 56px; }"
            "QTabBar QToolButton { background: #F1F5F9; border: 1px solid #E2E8F0;"
            " border-radius: 4px; padding: 2px; margin: 2px 1px;"
            " width: 22px; height: 22px; min-width: 22px; max-width: 22px;"
            " min-height: 22px; max-height: 22px; }"
            "QTabBar QToolButton:hover { background: #DBEAFE; border-color: #93C5FD; }")
        self._layout.addWidget(self._tabs)

        # Tab 1: Rooms
        rooms_tab = QWidget()
        rooms_l = QVBoxLayout(rooms_tab)
        rooms_l.setContentsMargins(8, 8, 8, 8)
        self._rooms_section = SectionPanel(tr("dashboard.room_utilization"))
        self._rooms_chart = BarChartWidget()
        self._rooms_section.add_widget(self._rooms_chart)
        rooms_l.addWidget(self._rooms_section)
        self._underused_section = SectionPanel(tr("dashboard.underused_classrooms"))
        self._underused_label = QLabel("—")
        self._underused_label.setStyleSheet("color: #64748B; font-size: 9pt; border: none;")
        self._underused_label.setWordWrap(True)
        self._underused_section.add_widget(self._underused_label)
        rooms_l.addWidget(self._underused_section)
        rooms_l.addStretch()
        self._tabs.addTab(rooms_tab, "\U0001F3E0  " + tr("tabs.rooms"))

        # Tab 2: Lecturers
        lec_tab = QWidget()
        lec_l = QVBoxLayout(lec_tab)
        lec_l.setContentsMargins(8, 8, 8, 8)
        self._lec_gap_section = SectionPanel(tr("dashboard.lecturer_gap_distribution"))
        self._lec_gap_chart = BarChartWidget()
        self._lec_gap_section.add_widget(self._lec_gap_chart)
        lec_l.addWidget(self._lec_gap_section)
        self._overloaded_section = SectionPanel(tr("dashboard.overloaded_lecturers"))
        self._overloaded_table = QTableWidget()
        self._overloaded_table.setColumnCount(5)
        self._overloaded_table.setHorizontalHeaderLabels([
            tr("labels.lecturer"), tr("dashboard.total_slots"), tr("dashboard.days_active"),
            tr("dashboard.max_per_day"), tr("dashboard.gaps_header"),
        ])
        self._overloaded_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 5):
            self._overloaded_table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.ResizeToContents)
        self._overloaded_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._overloaded_table.setMaximumHeight(200)
        self._overloaded_section.add_widget(self._overloaded_table)
        lec_l.addWidget(self._overloaded_section)
        lec_l.addStretch()
        self._tabs.addTab(lec_tab, "\U0001F468\u200D\U0001F3EB  " + tr("setup.lecturers"))

        # Tab 3: Students
        stu_tab = QWidget()
        stu_l = QVBoxLayout(stu_tab)
        stu_l.setContentsMargins(8, 8, 8, 8)
        self._stu_gap_section = SectionPanel(tr("dashboard.student_idle_time"))
        self._stu_gap_chart = BarChartWidget()
        self._stu_gap_section.add_widget(self._stu_gap_chart)
        stu_l.addWidget(self._stu_gap_section)
        stu_l.addStretch()
        self._tabs.addTab(stu_tab, "\U0001F393  " + tr("dashboard.students"))

        # Tab 4: Schedule load
        load_tab = QWidget()
        load_l = QVBoxLayout(load_tab)
        load_l.setContentsMargins(8, 8, 8, 8)
        self._days_section = SectionPanel(tr("dashboard.busiest_days"))
        self._days_chart = BarChartWidget()
        self._days_section.add_widget(self._days_chart)
        load_l.addWidget(self._days_section)
        self._slots_section = SectionPanel(tr("dashboard.busiest_time_slots"))
        self._slots_chart = BarChartWidget()
        self._slots_section.add_widget(self._slots_chart)
        load_l.addWidget(self._slots_section)
        load_l.addStretch()
        self._tabs.addTab(load_tab, "\U0001F4C5  " + tr("dashboard.schedule_load"))

        # Tab 5: Schedule Quality
        quality_tab = QWidget()
        quality_l = QVBoxLayout(quality_tab)
        quality_l.setContentsMargins(8, 8, 8, 8)
        self._quality_section = SectionPanel(tr("dashboard.overall_score"))
        self._quality_gauge = QualityGaugeWidget()
        self._quality_section.add_widget(self._quality_gauge)
        quality_l.addWidget(self._quality_section)
        self._breakdown_section = SectionPanel(tr("dashboard.score_breakdown"))
        self._breakdown_chart = BarChartWidget()
        self._breakdown_section.add_widget(self._breakdown_chart)
        quality_l.addWidget(self._breakdown_section)
        quality_l.addStretch()
        self._tabs.addTab(quality_tab, "\u2B50  " + tr("dashboard.schedule_quality"))

        self._layout.addStretch()

    def refresh(self, state):
        """Recompute metrics from current state and update all panels."""
        self._state = state
        if not state or not state.get("classes"):
            self._card_placed.set_value("—")
            self._card_unplaced.set_value("—")
            self._card_rooms.set_value("—")
            self._card_gaps.set_value("—")
            return

        m = compute_all_metrics(state)

        # ── Summary cards ──
        self._card_placed.set_value(m["placed_count"])
        self._card_unplaced.set_value(m["unplaced_count"])
        utils = m["room_utilization"]
        avg_util = round(sum(utils.values()) / len(utils), 1) if utils else 0
        self._card_rooms.set_value(f"{avg_util}%")
        total_gaps = sum(m["lec_gaps_total"].values()) + sum(m["student_gaps_total"].values())
        self._card_gaps.set_value(total_gaps)

        # ── Room utilization chart ──
        room_data = sorted(utils.items(), key=lambda x: -x[1])
        self._rooms_chart.set_data(room_data, color="#3B82F6", suffix="%")

        # ── Underused rooms ──
        underused = m["underused_rooms"]
        if underused:
            lines = [f"{r}: {u}%" for r, u in underused]
            self._underused_label.setText("\n".join(lines))
        else:
            self._underused_label.setText(tr("dashboard.all_above_threshold"))

        # ── Lecturer gaps chart ──
        lec_gaps = sorted(m["lec_gaps_total"].items(), key=lambda x: -x[1])
        gap_suffix = " " + tr("labels.gaps")
        self._lec_gap_chart.set_data(lec_gaps, color="#F59E0B", suffix=gap_suffix)

        # ── Overloaded table ──
        loads = m["lecturer_load"]
        sorted_loads = sorted(loads.items(), key=lambda x: -x[1]["max_day"])
        self._overloaded_table.setRowCount(len(sorted_loads))
        for i, (lec, info) in enumerate(sorted_loads):
            self._overloaded_table.setItem(i, 0, QTableWidgetItem(lec))
            self._overloaded_table.setItem(
                i, 1, QTableWidgetItem(str(info["total_slots"])))
            self._overloaded_table.setItem(
                i, 2, QTableWidgetItem(str(info["days_active"])))
            item_max = QTableWidgetItem(str(info["max_day"]))
            if info["max_day"] >= 6:
                item_max.setBackground(QColor("#FEE2E2"))
                item_max.setForeground(QColor("#991B1B"))
            self._overloaded_table.setItem(i, 3, item_max)
            self._overloaded_table.setItem(
                i, 4, QTableWidgetItem(str(info["gaps"])))

        # ── Student gaps chart ──
        stu_gaps = sorted(m["student_gaps_total"].items(), key=lambda x: -x[1])
        self._stu_gap_chart.set_data(stu_gaps, color="#8B5CF6", suffix=gap_suffix)

        # ── Busiest days ──
        days_data = [(day_label(d), n) for d, n in m["busiest_days"].items()]
        self._days_chart.set_data(days_data, color="#10B981")

        # ── Busiest slots ──
        slots_data = list(m["busiest_slots"].items())
        self._slots_chart.set_data(slots_data, color="#06B6D4")

        # ── Schedule quality score ──
        self._refresh_quality(state)

    def _refresh_quality(self, state):
        """Compute and display the schedule quality score."""
        classes = state.get("classes", [])
        placements = []
        for cls in classes:
            day = effective_day(cls)
            slot = effective_time(cls)
            if day and slot:
                # ST-UI-003. This used to be `cls.get("room", "")`. No class
                # dict has a "room" key, so every tuple carried "" — and ""
                # is not the _ROOM_UNSET sentinel, so it won as a
                # room_override and *overrode* the correct placed_classroom /
                # pinned_classroom fallback. Every room-derived number
                # downstream collapsed to zero. `effective_room` is the same
                # currency logic.analyze_schedule and apply_reschedule use, so
                # this screen now scores the timetable the rest of the app does.
                room = effective_room(cls)
                placements.append((cls, day, slot, room))

        if not placements:
            self._quality_gauge.set_score(0, tr("quality.poor"))
            self._breakdown_chart.set_data([], color="#94A3B8")
            return

        # Use ScheduleAnalytics for the global score (same as placement results)
        try:
            sa = ScheduleAnalytics(state)
            report = sa.analyze(placements)
            quality = int(round(report.get("global_score", 0)))
            grade = report.get("grade", "")
        except Exception:
            quality = 0
            grade = ""

        if quality >= 80:
            label = tr("quality.excellent")
        elif quality >= 60:
            label = tr("quality.good")
        elif quality >= 40:
            label = tr("quality.fair")
        else:
            label = tr("quality.poor")

        if grade:
            label = f"{label} ({grade})"

        self._quality_gauge.set_score(quality, label)

        # Breakdown chart — use TimetableScorer for per-category penalties
        try:
            scorer = TimetableScorer(state)
            breakdown = scorer.score_detailed(placements)
        except Exception:
            breakdown = {}

        category_labels = {
            "lecturer_gaps": tr("dashboard.lecturer_gaps"),
            "student_gaps": tr("dashboard.student_gaps"),
            "fragmentation": tr("dashboard.fragmentation"),
            "day_balance": tr("dashboard.day_balance"),
            "room_switching": tr("dashboard.room_switching"),
            "time_quality": tr("dashboard.time_quality"),
        }
        chart_data = []
        for key, label_text in category_labels.items():
            val = round(abs(breakdown.get(key, 0)), 1)
            chart_data.append((label_text, val))
        self._breakdown_chart.set_data(chart_data, color="#8B5CF6")

    def retranslate(self):
        """Update all translatable text (called when language changes)."""
        self._header.setText("\U0001F4CA  " + tr("dashboard.title"))
        self._card_placed.set_value(self._card_placed._value.text(),
                                     title=tr("labels.placed"))
        self._card_unplaced.set_value(self._card_unplaced._value.text(),
                                       title=tr("labels.unplaced"))
        self._card_rooms.set_value(self._card_rooms._value.text(),
                                    title=tr("dashboard.avg_room_use"))
        self._card_gaps.set_value(self._card_gaps._value.text(),
                                   title=tr("dashboard.total_gaps"))
        self._tabs.setTabText(0, "\U0001F3E0  " + tr("tabs.rooms"))
        self._tabs.setTabText(1, "\U0001F468\u200D\U0001F3EB  " + tr("setup.lecturers"))
        self._tabs.setTabText(2, "\U0001F393  " + tr("dashboard.students"))
        self._tabs.setTabText(3, "\U0001F4C5  " + tr("dashboard.schedule_load"))
        self._tabs.setTabText(4, "\u2B50  " + tr("dashboard.schedule_quality"))
        self._rooms_section.set_title(tr("dashboard.room_utilization"))
        self._underused_section.set_title(tr("dashboard.underused_classrooms"))
        self._lec_gap_section.set_title(tr("dashboard.lecturer_gap_distribution"))
        self._overloaded_section.set_title(tr("dashboard.overloaded_lecturers"))
        self._overloaded_table.setHorizontalHeaderLabels([
            tr("labels.lecturer"), tr("dashboard.total_slots"), tr("dashboard.days_active"),
            tr("dashboard.max_per_day"), tr("dashboard.gaps_header"),
        ])
        self._stu_gap_section.set_title(tr("dashboard.student_idle_time"))
        self._days_section.set_title(tr("dashboard.busiest_days"))
        self._slots_section.set_title(tr("dashboard.busiest_time_slots"))
        self._quality_section.set_title(tr("dashboard.overall_score"))
        self._breakdown_section.set_title(tr("dashboard.score_breakdown"))
        # Re-run refresh to update chart labels
        if self._state:
            self.refresh(self._state)
