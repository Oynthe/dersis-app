"""One place where an import validation report becomes a readable dialog.

ST-UI-B6 (Phase 9). ``DataValidationReport`` grows one line per offending
spreadsheet row — by design, and ~10 tests in
``tests/test_import_roundtrip.py`` pin that row-attributed form, because
"row 214 names a room type you do not have" is the only version of the message
a user can act on. What was broken is not the report, it is the widget it was
poured into: ``QMessageBox`` lays its label out at full height, with no scroll
area and no maximum, so the height of the box is *linear in the number of
warnings*.

Measured on this machine before the fix (Windows platform plugin, Segoe UI,
1536x912 available), 500 classes naming a room type no room carries:

    dialog                500 x 24110 px   OK button's bottom edge at y=24098
    the screen it opens on              912 px

— 26x too tall, so the OK button sits 23 000 px below the bottom of the
display. The same workbook under ``QT_QPA_PLATFORM=offscreen`` measures
400x56100: offscreen Qt has no Segoe UI, its fallback is fixed-pitch, and it
wraps these ~170-character warnings about twice as often. Both numbers are the
same defect; neither is *the* number, which is why nothing here is a pixel
budget and everything is measured against the screen at run time.

Do not try to see this with ``sizeHint()``. For the same report it says
973x8106 — a 3x under-report — because ``QMessageBox`` clamps its own width,
and therefore re-wraps and grows, inside its show handler.
``tests/test_phase9_b6.py`` measures with ``WA_DontShowOnScreen`` + ``show()``
for exactly that reason.

Why two widgets and not one
---------------------------
A small report keeps the plain ``QMessageBox`` it has always had: it is the
right widget for four lines, and two shipped contracts depend on the statics
being the thing that gets called —
``tests/test_import_ui_flow.py::test_import_reports_success_to_the_user``
(``QMessageBox.information`` carries ``status.import_successful``) and
``::test_rejected_import_warns_and_leaves_state_untouched``
(``QMessageBox.warning`` is called for a workbook the importer refused; that
report is 1 error + 3 warnings — 6 lines, 180 characters, measured).
A report too big for a message box goes to ``ValidationReportDialog``, which
scrolls, so nothing is dropped, summarised away or hidden behind a "show more".
Measured after the split, same 500-warning workbook, all 500 lines still in it:
**560x520 offscreen, 720x520 on the real platform** — the size of the display,
not of the report.
"""

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit,
    QVBoxLayout,
)

from scheduler_app.translations import tr


# ── when a plain message box is still the right widget ──────────────────────

MAX_PLAIN_BOX_LINES = 12
MAX_PLAIN_BOX_CHARS = 600
"""The largest report still handed to ``QMessageBox``.

Both caps bind, because either one alone is defeatable: 600 characters spread
over 60 ten-character lines is 60 forced line breaks (~1200 px offscreen, off
the bottom of its 800 px screen), and 12 lines of 400 characters each wrap to
far more than 12.

Together they were measured against the tallest shapes that get through the
door, on the harsher of the two platforms this repo runs on (offscreen Qt has
no Segoe UI; its fixed-pitch fallback wraps roughly twice as often as a real
font, so the offscreen figure is the binding one):

                                                       offscreen      real
    599 chars / 12 lines, one 589-char line + 11 blanks    530 px    389 px
    599 chars / 12 lines, twelve 49-char lines of words    558 px    261 px
    599 chars / 12 lines, twelve 49-char unbroken lines    222 px    261 px
    the workbook test_import_ui_flow.py rejects
      (1 error + 3 warnings, 180 chars, 6 lines)           208 px    165 px
                                            screen         800 px    912 px

— 242 px of headroom in the worst case on the platform that has least of it.
The caps are not the largest that would fit: a message box is not a document
viewer, and a report that wants 600 characters is better off in something that
scrolls anyway.
"""


def _fits_a_plain_box(detail: str) -> bool:
    """True when *detail* is small enough that a message box stays on screen."""
    if not detail:
        return True
    return (len(detail) <= MAX_PLAIN_BOX_CHARS
            and detail.count("\n") + 1 <= MAX_PLAIN_BOX_LINES)


# ── the dialog a big report gets instead ────────────────────────────────────

PREFERRED_WIDTH = 720
PREFERRED_HEIGHT = 520
SCREEN_FRACTION = 0.7
"""Never larger than this share of the screen, whatever the report holds.

The point of the whole module: the dialog's size is a function of the display,
not of the number of warnings. 500 warnings and 2 warnings produce the same
box; only the scrollbar differs.
"""


class ValidationReportDialog(QDialog):
    """A headline, the whole report in a scroll area, and an OK button.

    Deliberately *not* a ``QMessageBox`` with ``setDetailedText``: that hides
    the report behind a "Show Details..." button the user has to know to press,
    and this dialog is only ever built when there is a lot to say.

    ``QPlainTextEdit`` rather than a ``QLabel`` in a ``QScrollArea`` — it is the
    widget Qt optimises for many lines of plain text, it is selectable and
    copyable (a school forwarding 500 bad rows to whoever maintains the
    workbook wants Ctrl+C), and it is read-only, so it cannot be mistaken for
    somewhere to fix the data.
    """

    def __init__(self, parent, title, headline, detail):
        super().__init__(parent)
        # Deferred: `ui.dialogs` is a 3 000-statement module and this one is
        # imported by `ui.app` at module scope. Keeping the edge out of import
        # time keeps `test_import_layering`'s acyclic contract trivially true
        # no matter which way `dialogs` grows.
        from scheduler_app.ui.dialogs import DIALOG_STYLESHEET

        self.setStyleSheet(DIALOG_STYLESHEET())
        self.setWindowTitle(title)
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)
        if headline:
            label = QLabel(headline)
            label.setWordWrap(True)
            layout.addWidget(label)

        body = QPlainTextEdit(detail)
        body.setReadOnly(True)
        # Stretch factor 1: the report is what grows when the user enlarges the
        # dialog, not the headline and not the button row.
        layout.addWidget(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, self)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        # Qt's own button text follows QTranslator, which this app does not
        # install; every other dialog in the codebase labels its buttons from
        # `tr`, so this one does too or it is the only English button in a
        # Turkish window.
        ok.setText(tr("buttons.ok"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._size_to_screen()

    def _size_to_screen(self):
        """Fit the display this dialog will open on, and never exceed it."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        self.resize(
            min(PREFERRED_WIDTH, int(available.width() * SCREEN_FRACTION)),
            min(PREFERRED_HEIGHT, int(available.height() * SCREEN_FRACTION)),
        )
        # A hard stop as well as a preferred size: a user is free to drag this
        # dialog bigger, up to the screen, and no further.
        self.setMaximumSize(available.width(), available.height())


# ── the one entry point ─────────────────────────────────────────────────────

def show_validation_report(parent, title, headline, detail, kind="information"):
    """Put an import report in front of the user, bounded by the screen.

    *headline* is the one-line verdict (may be empty), *detail* the report body
    (may be empty). Both import paths in ``ui/app.py`` go through here — the
    success path and the rejection path have the identical unbounded shape, and
    fixing only the one a probe happens to cover leaves the twin open.

    The statics are looked up at call time on purpose: every test in this repo
    that asserts "the user was told" monkeypatches ``QMessageBox.information``
    or ``.warning``, and binding them at import time would make this function
    invisible to all of them.
    """
    if _fits_a_plain_box(detail):
        static = (QMessageBox.warning if kind == "warning"
                  else QMessageBox.information)
        static(parent, title,
               "\n\n".join(part for part in (headline, detail) if part))
        return
    ValidationReportDialog(parent, title, headline, detail).exec()
