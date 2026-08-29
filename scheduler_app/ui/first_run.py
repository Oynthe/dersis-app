"""First-run state machine: language selection → tutorial → (optional) setup.

Persistent flags are stored in the application's config JSON file via
get/set helpers.  The controller is instantiated once during __init__ and
drives the onboarding flow through deferred QTimer callbacks so nothing
blocks the main-window construction.

The language gate (run_language_gate) is intentionally separate and runs
*before* the main window is constructed so the user never sees the app
behind the dialog.
"""

import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer, QSize

from scheduler_app.translations import tr, set_language, TRANSLATIONS
from scheduler_app.icons import (
    flag_gb, flag_tr, flag_de, flag_fr, flag_es, flag_cn, flag_ru,
    flag_br, flag_se, flag_dk, flag_it, flag_nl, flag_pl, flag_in, flag_id,
    flag_az, flag_za, flag_sa, flag_ir, flag_jp, flag_kr, flag_pt,
)
from scheduler_app import storage


# ── Persistent flags ──────────────────────────────────────────────────────

# Set when a corrupt settings container is quarantined during the language
# gate, which runs before any main window exists (scheduler_gui.py calls
# run_language_gate() at 172 and SchedulerApp() at 180). SchedulerApp reads this
# once it has a window and tells the user (ST-DATA-014).
LAST_QUARANTINE = None


def _read_config(path):
    """Read the settings container. Never raises for an absent file.

    ST-DATA-014: this used to swallow every exception and return ``{}``, after
    which ``_write_flag`` wrote that ``{}`` straight back over the user's
    settings — so a container that failed to decrypt for ANY reason took the
    saved schedule with it. The three outcomes are now distinguished:

    absent            -> ``{}``; this is first run.
    EguFileError      -> genuinely unreadable: quarantine the bytes, then ``{}``.
    anything else     -> a transient failure (a locked file, an I/O error).
                         Propagate. Quarantining a perfectly good file because
                         the disk hiccuped is data loss dressed up as recovery.
    """
    global LAST_QUARANTINE
    if not os.path.exists(path):
        return {}
    try:
        data = storage.load_encrypted(path)
    except storage.EguFileError:
        try:
            LAST_QUARANTINE = storage.quarantine_corrupt(path)
        except Exception:
            raise  # could not even preserve it — do not report a recovery
        return {}
    return data if isinstance(data, dict) else {}


def _write_flag(path, key, value):
    """Persist one flag. Returns True on success.

    Must never raise: every caller is a Qt slot or a QTimer callback, where an
    exception aborts the process under a real platform plugin.
    """
    try:
        data = _read_config(path)
    except Exception:
        return False  # never overwrite a container we could not read
    data[key] = value
    try:
        storage.save_encrypted(data, path)
        return True
    except Exception:
        return False


# ── Shared language data ─────────────────────────────────────────────────

LANGUAGE_LIST = [
    ("tr", "languages.turkish", flag_tr),
    ("az", "languages.azerbaijani", flag_az),
    ("ar", "languages.arabic", flag_sa),
    ("zh", "languages.chinese", flag_cn),
    ("da", "languages.danish", flag_dk),
    ("nl", "languages.dutch", flag_nl),
    ("en", "languages.english", flag_gb),
    ("fr", "languages.french", flag_fr),
    ("de", "languages.german", flag_de),
    ("hi", "languages.hindi", flag_in),
    ("id", "languages.indonesian", flag_id),
    ("it", "languages.italian", flag_it),
    ("ja", "languages.japanese", flag_jp),
    ("ko", "languages.korean", flag_kr),
    ("fa", "languages.persian", flag_ir),
    ("pl", "languages.polish", flag_pl),
    ("pt_BR", "languages.portuguese_br", flag_br),
    ("pt_PT", "languages.portuguese_pt", flag_pt),
    ("ru", "languages.russian", flag_ru),
    ("af", "languages.south_african", flag_za),
    ("es", "languages.spanish", flag_es),
    ("sv", "languages.swedish", flag_se),
]


def _english_name(name_key):
    """Return the English translation of a language name key."""
    return TRANSLATIONS.get("en", {}).get(name_key, "")


# ── Language selection dialog ─────────────────────────────────────────────

class LanguageDialog(QDialog):
    """Shared language selection dialog used for both first-run gate and
    the top-menu Languages control.

    Features:
    - Type-to-search with live filtering (native + English names)
    - Scrollable language list with flag icons
    - Keyboard navigation (arrows, Enter, Escape)
    - Double-click to apply
    - Visually marks the currently active language

    Parameters:
        parent: optional parent widget
        current_language: language code to highlight as active (e.g. 'en')
    """

    def __init__(self, parent=None, current_language=None):
        super().__init__(parent)
        self._current_language = current_language
        self._result_lang = current_language or "en"
        self._all_items = []  # [(code, display_text, english_name, icon_fn)]

        self.setWindowTitle(tr("dialogs.language.title"))
        self.setFixedSize(440, 480)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.CustomizeWindowHint)
        self.setStyleSheet("QDialog { background: #F8FAFC; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(6)

        # Heading
        heading = QLabel(tr("dialogs.language.heading"))
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet(
            "font-size: 15pt; font-weight: bold; color: #1E293B;"
            " background: transparent;")
        layout.addWidget(heading)

        # Subtitle
        subtitle = QLabel(tr("dialogs.language.description"))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            "font-size: 9pt; color: #64748B; background: transparent;"
            " margin-bottom: 4px;")
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        # Search field
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("dialogs.language.search_placeholder"))
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(38)
        self._search.setStyleSheet(
            "QLineEdit {"
            "  background: white; color: #1E293B;"
            "  border: 1.5px solid #CBD5E1; border-radius: 8px;"
            "  font-size: 10.5pt; padding: 6px 12px;"
            "}"
            "QLineEdit:focus { border-color: #3B82F6; }")
        self._search.textChanged.connect(self._filter_list)
        layout.addWidget(self._search)

        layout.addSpacing(4)

        # Language list
        self._list = QListWidget()
        self._list.setIconSize(QSize(24, 24))
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.setStyleSheet(
            "QListWidget {"
            "  background: white; color: #1E293B;"
            "  border: 1.5px solid #CBD5E1; border-radius: 8px;"
            "  font-size: 10.5pt; padding: 4px;"
            "  outline: none;"
            "}"
            "QListWidget::item {"
            "  padding: 7px 10px; border-radius: 5px;"
            "}"
            "QListWidget::item:selected {"
            "  background: #EFF6FF; color: #1E293B;"
            "}"
            "QListWidget::item:hover {"
            "  background: #F1F5F9;"
            "}")
        self._list.doubleClicked.connect(self._confirm)
        layout.addWidget(self._list, 1)  # stretch=1 to fill space

        layout.addSpacing(8)

        # Confirm / Cancel buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._cancel_btn = QPushButton(tr("buttons.cancel"))
        self._cancel_btn.setFixedSize(100, 38)
        self._cancel_btn.setStyleSheet(
            "QPushButton { background: #E2E8F0; color: #475569; border: none;"
            "  border-radius: 8px; font-size: 10pt; font-weight: bold; }"
            "QPushButton:hover { background: #CBD5E1; }"
            "QPushButton:pressed { background: #94A3B8; }")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        btn_row.addSpacing(8)

        self._confirm_btn = QPushButton(tr("buttons.apply"))
        self._confirm_btn.setFixedSize(100, 38)
        self._confirm_btn.setStyleSheet(
            "QPushButton { background: #3B82F6; color: white; border: none;"
            "  border-radius: 8px; font-size: 10pt; font-weight: bold; }"
            "QPushButton:hover { background: #2563EB; }"
            "QPushButton:pressed { background: #1D4ED8; }")
        self._confirm_btn.clicked.connect(self._confirm)
        self._confirm_btn.setDefault(True)
        btn_row.addWidget(self._confirm_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Build language items data and populate the list
        for code, name_key, icon_fn in LANGUAGE_LIST:
            display = tr(name_key)
            en_name = _english_name(name_key)
            self._all_items.append((code, display, en_name, icon_fn))

        self._populate_list()

        # Focus the search field when dialog opens
        QTimer.singleShot(0, self._search.setFocus)

    # ── list population / filtering ───────────────────────────────

    def _populate_list(self, filter_text=""):
        """Rebuild the list widget, optionally filtered by search text."""
        self._list.clear()
        needle = filter_text.lower()
        select_row = -1

        for idx, (code, display, en_name, icon_fn) in enumerate(
                self._all_items):
            if needle:
                if (needle not in display.lower()
                        and needle not in en_name.lower()):
                    continue

            item = QListWidgetItem(icon_fn(), display)
            item.setData(Qt.ItemDataRole.UserRole, code)
            item.setSizeHint(QSize(0, 36))

            # Mark the currently active language
            if code == self._current_language:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setText(display + "  \u2713")
                select_row = self._list.count()

            self._list.addItem(item)

        # Select the active language row, or first row if not found
        if self._list.count() > 0:
            row = select_row if select_row >= 0 else 0
            self._list.setCurrentRow(row)

    def _filter_list(self, text):
        """Live-filter the language list as the user types."""
        self._populate_list(text)

    # ── selection / confirm ───────────────────────────────────────

    def _confirm(self):
        item = self._list.currentItem()
        if item:
            self._result_lang = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    @property
    def chosen_language(self):
        return self._result_lang

    # ── keyboard handling ─────────────────────────────────────────

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            # Forward arrow keys to the list widget
            self._list.setFocus()
            self._list.keyPressEvent(event)
        else:
            # Keep typing in search field
            if not self._search.hasFocus():
                self._search.setFocus()
                self._search.keyPressEvent(event)
            else:
                super().keyPressEvent(event)


# ── Pre-show language gate ────────────────────────────────────────────────

def run_language_gate():
    """Check if language has been chosen; if not, show the dialog.

    This must be called BEFORE the main window is constructed so the
    user never sees the app behind the dialog. Returns the chosen
    language code (e.g. 'en').
    """
    cfg_path = storage.settings_path()
    cfg = _read_config(cfg_path)

    if cfg.get("language_chosen"):
        # Already chosen — apply the persisted language and return
        lang = cfg.get("language", "en")
        set_language(lang)
        return lang

    # First run — show standalone language dialog (no parent)
    dlg = LanguageDialog()
    if dlg.exec() == QDialog.DialogCode.Accepted:
        lang = dlg.chosen_language
    else:
        lang = "en"

    set_language(lang)
    _write_flag(cfg_path, "language_chosen", True)
    _write_flag(cfg_path, "language", lang)
    return lang


# ── First-run controller ─────────────────────────────────────────────────

class FirstRunController:
    """Drives the post-show first-run onboarding sequence.

    The language gate runs separately before the main window is shown.
    This controller handles the remaining steps: tutorial and setup.

    Lifecycle (called from SchedulerApp.__init__):
        1.  __init__  — stores references
        2.  start()   — called via QTimer.singleShot after the window is
                        fully constructed; begins the state machine.

    State machine:
        check_tutorial  → (if needed) show TutorialOverlay → mark seen
        check_setup     → (if needed) offer setup dialog
        done
    """

    def __init__(self, app):
        self._app = app                 # SchedulerApp instance
        self._cfg = app._config_path    # path to scheduler_config.json

    # ── public entry point ────────────────────────────────────────

    def start(self):
        """Begin the first-run pipeline (called once)."""
        self._step_tutorial()

    # ── step 1: tutorial ──────────────────────────────────────────

    def _step_tutorial(self):
        cfg = _read_config(self._cfg)
        if cfg.get("tutorial_seen_or_skipped"):
            self._step_setup()
            return

        self._app._show_tutorial_controlled(self._on_tutorial_done)

    def _on_tutorial_done(self):
        _write_flag(self._cfg, "tutorial_seen_or_skipped", True)
        # also mark the old key for backward compat
        _write_flag(self._cfg, "tutorial_seen", True)
        QTimer.singleShot(200, self._step_setup)

    # ── step 2: setup ─────────────────────────────────────────────

    def _step_setup(self):
        cfg = _read_config(self._cfg)
        if cfg.get("initial_setup_prompt_handled"):
            return

        s = self._app.state_data
        needs_setup = (
            not s["days"] or not s["slots"]
            or not s["classrooms"] or not s["years"])

        _write_flag(self._cfg, "initial_setup_prompt_handled", True)

        if needs_setup:
            from PyQt6.QtWidgets import QMessageBox
            resp = QMessageBox.question(
                self._app, tr("dialogs.welcome.title"),
                tr("dialogs.welcome.setup_prompt"))
            if resp == QMessageBox.StandardButton.Yes:
                self._app.edit_setup()
