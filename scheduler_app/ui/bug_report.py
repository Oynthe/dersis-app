"""Bug report and crash report dialogs for the DERSİS desktop app.

Provides:
    - BugReportDialog: polished dark-themed dialog for manual bug reports
    - CrashReportDialog: minimal safe dialog for crash/exception reporting
    - BugReportButton: subtle status-bar bug icon widget
"""

import platform

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QTextEdit, QMessageBox, QApplication,
)
from PyQt6.QtCore import Qt, QUrl, QUrlQuery
from PyQt6.QtGui import QColor, QPainter, QPen, QCursor, QDesktopServices

from scheduler_app.core.text_safety import redact_user_paths
from scheduler_app.translations import tr
from scheduler_app._version import __version__ as APP_VERSION

# Bug/crash reports are composed locally and handed to the user's default
# email client via a mailto: link. The app never transmits anything itself.
BUG_REPORT_EMAIL = "dersis.app@gmail.com"
BUG_REPORT_SUBJECT = "DERSİS Bug Report"


# ── Dark-themed stylesheet for bug report dialogs ───────────────────

_BUG_DIALOG_STYLE = """
QDialog {
    background: #0f172a;
    color: #e2e8f0;
    font-family: "Segoe UI", -apple-system, sans-serif;
}
QLabel {
    color: #cbd5e1;
    font-family: "Segoe UI", -apple-system, sans-serif;
    font-size: 9pt;
}
QLabel#headingLabel {
    color: #e2e8f0;
    font-size: 11pt;
    font-weight: bold;
}
QLabel#subheadingLabel {
    color: #94a3b8;
    font-size: 8pt;
}
QLabel#errorLabel {
    color: #f87171;
    font-size: 8pt;
}
QLineEdit {
    background: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 9pt;
    font-family: "Segoe UI", -apple-system, sans-serif;
}
QLineEdit:focus {
    border-color: #6366f1;
}
QLineEdit:disabled {
    background: #1e293b;
    color: #64748b;
}
QTextEdit {
    background: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 9pt;
    font-family: "Segoe UI", -apple-system, sans-serif;
}
QTextEdit:focus {
    border-color: #6366f1;
}
QComboBox {
    background: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 9pt;
    min-width: 120px;
}
QComboBox:focus {
    border-color: #6366f1;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    selection-background-color: #6366f1;
}
QPushButton {
    font-size: 9pt;
    font-family: "Segoe UI", -apple-system, sans-serif;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 600;
}
QPushButton#submitBtn {
    background: #6366f1;
    color: white;
    border: none;
}
QPushButton#submitBtn:hover {
    background: #4f46e5;
}
QPushButton#submitBtn:disabled {
    background: #334155;
    color: #64748b;
}
QPushButton#cancelBtn {
    background: transparent;
    color: #94a3b8;
    border: 1px solid #334155;
}
QPushButton#cancelBtn:hover {
    background: #1e293b;
    color: #e2e8f0;
}
QPushButton#tracebackToggle {
    background: transparent;
    color: #94a3b8;
    border: none;
    padding: 4px 8px;
    font-size: 8pt;
    font-weight: normal;
    text-align: left;
}
QPushButton#tracebackToggle:hover {
    color: #e2e8f0;
}
"""


# ── Helpers ─────────────────────────────────────────────────────────

def _make_heading(text):
    lbl = QLabel(text)
    lbl.setObjectName('headingLabel')
    return lbl


def _make_subheading(text):
    lbl = QLabel(text)
    lbl.setObjectName('subheadingLabel')
    return lbl


def _open_mailto(subject, body, parent=None):
    """Open the user's default email client with a prefilled message.

    Returns True if a mail client was launched. If none is available, the
    body is copied to the clipboard and a friendly dialog tells the user
    which address to write to. Nothing is sent automatically.

    ST-SEC-008: this is the **only** function in DERSİS that puts text on a
    path off the machine, so it is the only place the account name has to be
    removed. One call covers both dialogs, the ``mailto:`` URL, and the
    clipboard fallback below. The crash log on disk and the ``log_path`` the
    crash dialog displays are deliberately left raw — they never leave the
    machine, and they are the only unredacted copy a local maintainer has.
    """
    body = redact_user_paths(body)

    url = QUrl(f"mailto:{BUG_REPORT_EMAIL}")
    query = QUrlQuery()
    query.addQueryItem("subject", subject)
    query.addQueryItem("body", body)
    url.setQuery(query)

    if QDesktopServices.openUrl(url):
        return True

    # No mail client configured — fall back to a manual instruction.
    try:
        QApplication.clipboard().setText(body)
    except Exception:
        pass
    QMessageBox.information(
        parent,
        BUG_REPORT_SUBJECT,
        "Could not open your email app automatically.\n\n"
        f"Please email your report to:\n{BUG_REPORT_EMAIL}\n\n"
        "The report text has been copied to your clipboard.",
    )
    return False


# ── BugReportDialog ─────────────────────────────────────────────────

class BugReportDialog(QDialog):
    """Polished dark-themed bug report dialog.

    Parameters
    ----------
    parent : QWidget or None
    current_module : str
        Name of the currently active screen/module.
    prefill_traceback : str
        Pre-filled traceback for crash reports.
    prefill_title : str
        Pre-filled title.
    report_type : str
        'manual' or 'crash'.
    """

    def __init__(self, parent=None, *, current_module='',
                 prefill_traceback='', prefill_title='',
                 report_type='manual'):
        super().__init__(parent)
        self._report_type = report_type

        self.setWindowTitle(tr('bug_report.title') if report_type == 'manual'
                            else tr('bug_report.crash_title'))
        self.setMinimumSize(520, 560)
        self.resize(560, 640)
        self.setStyleSheet(_BUG_DIALOG_STYLE)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        # Header
        title_label = _make_heading(
            tr('bug_report.heading') if report_type == 'manual'
            else tr('bug_report.crash_heading')
        )
        layout.addWidget(title_label)

        subtitle = _make_subheading(
            tr('bug_report.subtitle') if report_type == 'manual'
            else tr('bug_report.crash_subtitle')
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # Form
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Title
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText(tr('bug_report.title_placeholder'))
        self._title_input.setMaxLength(300)
        if prefill_title:
            self._title_input.setText(prefill_title)
        form.addRow(tr('bug_report.field_title'), self._title_input)

        # Severity
        self._severity_combo = QComboBox()
        self._severity_combo.addItems([
            tr('bug_report.severity_low'),
            tr('bug_report.severity_medium'),
            tr('bug_report.severity_high'),
            tr('bug_report.severity_critical'),
        ])
        self._severity_combo.setCurrentIndex(1)  # Medium default
        if report_type == 'crash':
            self._severity_combo.setCurrentIndex(2)  # High for crashes
        form.addRow(tr('bug_report.field_severity'), self._severity_combo)

        # Description / What happened
        self._desc_input = QTextEdit()
        self._desc_input.setPlaceholderText(tr('bug_report.desc_placeholder'))
        self._desc_input.setMaximumHeight(80)
        form.addRow(tr('bug_report.field_description'), self._desc_input)

        # Expected behavior
        self._expected_input = QTextEdit()
        self._expected_input.setPlaceholderText(tr('bug_report.expected_placeholder'))
        self._expected_input.setMaximumHeight(60)
        form.addRow(tr('bug_report.field_expected'), self._expected_input)

        # Steps to reproduce
        self._steps_input = QTextEdit()
        self._steps_input.setPlaceholderText(tr('bug_report.steps_placeholder'))
        self._steps_input.setMaximumHeight(60)
        form.addRow(tr('bug_report.field_steps'), self._steps_input)

        # Traceback (visible only for crash reports)
        if prefill_traceback or report_type == 'crash':
            self._traceback_input = QTextEdit()
            self._traceback_input.setPlaceholderText(
                tr('bug_report.traceback_placeholder'))
            self._traceback_input.setMaximumHeight(100)
            self._traceback_input.setReadOnly(bool(prefill_traceback))
            if prefill_traceback:
                self._traceback_input.setPlainText(prefill_traceback)
            form.addRow(tr('bug_report.field_traceback'), self._traceback_input)
        else:
            self._traceback_input = None

        # Auto-filled metadata (read-only)
        meta_label = _make_subheading(tr('bug_report.auto_metadata'))
        form.addRow('', meta_label)

        self._version_label = QLineEdit(APP_VERSION)
        self._version_label.setReadOnly(True)
        self._version_label.setEnabled(False)
        form.addRow(tr('bug_report.field_version'), self._version_label)

        self._os_label = QLineEdit(f"{platform.system()} {platform.release()}")
        self._os_label.setReadOnly(True)
        self._os_label.setEnabled(False)
        form.addRow(tr('bug_report.field_os'), self._os_label)

        # Store current module
        self._current_module = current_module

        layout.addLayout(form)

        # Error label
        self._error_label = QLabel('')
        self._error_label.setObjectName('errorLabel')
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._cancel_btn = QPushButton(tr('buttons.cancel'))
        self._cancel_btn.setObjectName('cancelBtn')
        self._cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._cancel_btn)

        self._submit_btn = QPushButton(tr('bug_report.submit'))
        self._submit_btn.setObjectName('submitBtn')
        self._submit_btn.clicked.connect(self._on_submit)
        btn_layout.addWidget(self._submit_btn)

        layout.addLayout(btn_layout)

    def _on_submit(self):
        """Compose the report locally and hand it to the user's email client."""
        title = self._title_input.text().strip()
        if not title:
            self._show_error(tr('bug_report.error_title_required'))
            self._title_input.setFocus()
            return

        severity_map = {0: 'low', 1: 'medium', 2: 'high', 3: 'critical'}
        severity = severity_map.get(self._severity_combo.currentIndex(), 'medium')
        description = self._desc_input.toPlainText().strip()
        expected = self._expected_input.toPlainText().strip()
        steps = self._steps_input.toPlainText().strip()
        os_str = f"{platform.system()} {platform.release()} ({platform.machine()})"

        lines = [
            f"App version: {APP_VERSION}",
            f"Operating system: {os_str}",
            f"Severity: {severity}",
        ]
        if self._current_module:
            lines.append(f"Module: {self._current_module}")
        lines += [
            f"Title: {title}",
            "",
            "What happened?",
            description or "(describe what happened)",
            "",
            "Steps to reproduce:",
            steps or "(list the steps to reproduce)",
            "",
            "Expected result:",
            expected or "(what you expected to happen)",
            "",
            "Actual result:",
            "(what actually happened)",
        ]
        if self._traceback_input is not None:
            tb = self._traceback_input.toPlainText().strip()
            if tb:
                lines += ["", "Traceback:", tb]

        _open_mailto(BUG_REPORT_SUBJECT, "\n".join(lines), self)
        self.accept()

    def _show_error(self, text):
        self._error_label.setText(text)
        self._error_label.setVisible(True)


# ── CrashReportDialog ──────────────────────────────────────────────

class CrashReportDialog(QDialog):
    """Minimal safe crash report dialog.

    Used when the main UI may be unstable. Shows the crash info and
    offers to send a report. Intentionally minimal to avoid triggering
    further errors.
    """

    def __init__(self, exc_type_name, exc_message, traceback_text,
                 log_path='', parent=None):
        super().__init__(parent)
        self._traceback = traceback_text
        self._exc_type = exc_type_name
        self._exc_message = exc_message

        self.setWindowTitle(tr('app.crash_title'))
        self.setMinimumSize(520, 400)
        self.resize(560, 480)
        self.setStyleSheet(_BUG_DIALOG_STYLE)
        flags = Qt.WindowType.Dialog
        if parent is None:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 16, 20, 16)

        # Header
        header = _make_heading(tr('app.crash_body'))
        header.setWordWrap(True)
        layout.addWidget(header)

        # Error summary
        error_text = f"{exc_type_name}: {exc_message}"
        error_label = QLabel(error_text)
        error_label.setStyleSheet(
            'color: #f87171; font-family: "SF Mono", "Fira Code", monospace; '
            'font-size: 8.5pt; padding: 8px; background: #1e293b; '
            'border-radius: 4px;')
        error_label.setWordWrap(True)
        error_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(error_label)

        if log_path:
            path_label = _make_subheading(
                f"{tr('app.crash_details')}\n{log_path}")
            path_label.setWordWrap(True)
            path_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(path_label)

        # Collapsible traceback section
        if traceback_text:
            self._tb_toggle = QPushButton(tr('bug_report.show_traceback'))
            self._tb_toggle.setObjectName('tracebackToggle')
            self._tb_toggle.setCursor(
                QCursor(Qt.CursorShape.PointingHandCursor))
            self._tb_toggle.clicked.connect(self._toggle_traceback)
            layout.addWidget(self._tb_toggle)

            self._tb_view = QTextEdit()
            self._tb_view.setReadOnly(True)
            self._tb_view.setPlainText(traceback_text)
            self._tb_view.setMaximumHeight(120)
            self._tb_view.setStyleSheet(
                'font-family: "SF Mono", "Fira Code", monospace; '
                'font-size: 7.5pt; background: #1e293b; color: #e2e8f0; '
                'border: 1px solid #334155; border-radius: 4px;')
            self._tb_view.setVisible(False)
            layout.addWidget(self._tb_view)
            self._tb_expanded = False

        # Optional user note
        note_label = _make_subheading(tr('bug_report.crash_note_label'))
        layout.addWidget(note_label)

        self._note_input = QTextEdit()
        self._note_input.setPlaceholderText(
            tr('bug_report.crash_note_placeholder'))
        self._note_input.setMaximumHeight(60)
        layout.addWidget(self._note_input)

        layout.addStretch()

        # Status label
        self._status_label = QLabel('')
        self._status_label.setObjectName('subheadingLabel')
        layout.addWidget(self._status_label)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        close_btn = QPushButton(tr('buttons.close'))
        close_btn.setObjectName('cancelBtn')
        close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(close_btn)

        self._report_btn = QPushButton(tr('bug_report.report_crash'))
        self._report_btn.setObjectName('submitBtn')
        self._report_btn.clicked.connect(self._send_crash_report)
        btn_layout.addWidget(self._report_btn)

        layout.addLayout(btn_layout)

    def _toggle_traceback(self):
        self._tb_expanded = not self._tb_expanded
        self._tb_view.setVisible(self._tb_expanded)
        self._tb_toggle.setText(
            tr('bug_report.hide_traceback') if self._tb_expanded
            else tr('bug_report.show_traceback'))

    def _send_crash_report(self):
        """Compose the crash details and open the user's email client."""
        user_note = self._note_input.toPlainText().strip()
        os_str = f"{platform.system()} {platform.release()} ({platform.machine()})"
        lines = [
            f"App version: {APP_VERSION}",
            f"Operating system: {os_str}",
            "",
            f"Error: {self._exc_type}: {self._exc_message}",
        ]
        if user_note:
            lines += ["", "User note:", user_note]
        tb = self._traceback or ''
        if tb:
            # Keep the mail body to a sane length; the full trace is in the log.
            tb_short = tb if len(tb) <= 4000 else tb[-4000:]
            lines += ["", "Traceback (most recent):", tb_short]

        _open_mailto(BUG_REPORT_SUBJECT, "\n".join(lines), self)
        self.accept()


# ── BugReportButton (status bar widget) ─────────────────────────────

class BugReportButton(QPushButton):
    """Small polished bug icon for the status bar.

    Emits clicked() signal. Styled to be subtle and non-intrusive,
    sitting in the permanent widget area of the status bar.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 22)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(tr('bug_report.tooltip'))
        self.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 2px;
            }
            QPushButton:hover {
                background: rgba(99, 102, 241, 0.15);
            }
            QPushButton:pressed {
                background: rgba(99, 102, 241, 0.25);
            }
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw a subtle bug icon
        color = QColor('#94a3b8') if not self.underMouse() else QColor('#a5b4fc')
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)

        cx, cy = 13, 11
        # Body (oval)
        p.drawEllipse(cx - 5, cy - 3, 10, 8)
        # Head
        p.drawEllipse(cx - 3, cy - 6, 6, 4)
        # Legs (3 pairs)
        p.drawLine(cx - 5, cy - 1, cx - 8, cy - 3)
        p.drawLine(cx + 5, cy - 1, cx + 8, cy - 3)
        p.drawLine(cx - 5, cy + 1, cx - 8, cy + 2)
        p.drawLine(cx + 5, cy + 1, cx + 8, cy + 2)
        p.drawLine(cx - 5, cy + 3, cx - 8, cy + 5)
        p.drawLine(cx + 5, cy + 3, cx + 8, cy + 5)
        # Antennae
        p.drawLine(cx - 2, cy - 6, cx - 4, cy - 9)
        p.drawLine(cx + 2, cy - 6, cx + 4, cy - 9)

        p.end()
