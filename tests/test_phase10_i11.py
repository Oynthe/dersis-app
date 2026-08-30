"""Item 11 / ST-UI-018 — the crash and bug-report dialogs are dark-themed.

The defect
----------
``ui/bug_report.py`` carries its own ``_BUG_DIALOG_STYLE``: a slate-900 dialog
(``background: #0f172a``) with slate-800 inputs (``background: #1e293b`` at
**eight** sites) and near-white ink (``color: #e2e8f0`` at nine). Its own module
docstring says "polished **dark**-themed dialog". DERSİS is a light-theme app,
and these are the two dialogs a user meets at the worst possible moment — the
crash one is what item 1's Ctrl+C bug puts in front of them.

Why the dialogs are measured rather than grepped
------------------------------------------------
A test that asserted ``"#0f172a" not in source`` would pin the *spelling* of the
fix, not the result: it passes for a dialog recoloured to ``#0F172B``, and fails
for a correct fix that moves the palette into a constant. Everything below
renders the **real dialog** through Qt's real style machinery
(``WA_DontShowOnScreen`` + ``show()``, the measurement discipline
``tests/test_phase9_b6.py`` establishes) and reads the colours back **out of the
pixels the user would see**. The contrast arithmetic is imported from
``tests/test_cell_contrast.py`` rather than rewritten, so there is one WCAG
implementation in the suite and it is already pinned against WCAG's worked
examples.

What was measured on this tree (offscreen, Qt 6.11.0, Fusion, light palette)
---------------------------------------------------------------------------
Dominant painted background, and the ink inside each labelled region:

    a standard DERSİS dialog (ui/dialogs.DIALOG_STYLESHEET)
        ground #F8FAFC, body ink #1E293B      -> dark-on-light, 13.98:1
    CrashReportDialog
        ground #0F172A, heading ink #E2E8F0   -> LIGHT-on-DARK
    BugReportDialog
        ground #0F172A, inputs #1E293B        -> LIGHT-on-DARK

    crash ground #0F172A vs app dialog ground #F8FAFC   17.06:1
    crash ground #0F172A vs main window     #F1F5F9     16.30:1
    crash input  #1E293B vs app input       #FFFFFF     14.63:1

17:1 is the contrast WCAG asks of *text against its own background*. Two
dialogs of one application are that far apart.

Is the app really light-only? Measured, not assumed
---------------------------------------------------
``ui/app.py::_APP_STYLESHEET_TEMPLATE`` declares **43** ``background``
properties. Exactly four resolve below L=0.20, and not one of them is a surface
that carries reading content: ``QStatusBar`` (``#1E293B``, the chrome strip),
``QMenuBar::item:selected`` (``#475569``, a hover highlight) and the two
``QScrollBar::handle:*:hover`` states (``#64748B``). Every content surface —
window, menu, tab pane, combo, spin box, list, tree, header — is white or
slate-50/100/200. On top of that
``scheduler_gui.main`` calls ``ui/app.py::apply_light_palette``, whose own
docstring says "Dersis ships a light-only stylesheet", pinning Window ``#F1F5F9``
and Text ``#1E293B``. Two of the tests below assert exactly that, and are green.

The trap in this fix
--------------------
``BugReportButton`` is in the same module and must **not** be relit. It lives in
the status bar — the one dark surface in the app — and paints its icon
``#94A3B8`` on ``#1E293B`` at **5.71:1**. Recoloured to a light-theme ink it
would break. ``test_the_status_bar_bug_icon_is_left_alone`` is green today and
goes red if the fix sweeps the whole module.

The second trap: the greys do not survive an inversion. ``#94A3B8`` (subheading)
reads 6.96:1 on the dark ``#0F172A`` ground and **2.45:1** on ``#F8FAFC``;
``#F87171`` (error text) reads 6.45:1 dark and **2.64:1** light. Swapping the two
background colours and leaving the ink alone produces a light dialog that fails
WCAG AA — the ink has to be re-picked, not merely inverted.
"""
import collections
import re

import pytest

from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QLabel, QLineEdit, QTextEdit, QVBoxLayout,
    QWidget,
)

# One WCAG implementation in the suite, already pinned against WCAG's own
# worked examples by ``test_the_contrast_formula_matches_wcags_worked_examples``.
from test_cell_contrast import (
    AA_NORMAL_TEXT, contrast_ratio, relative_luminance, _self_check,
)

pytestmark = pytest.mark.ui

# WCAG 2.1 SC 1.4.11: 3:1 is the ratio at which two areas read as *different*
# UI surfaces. Below it they read as the same ground. That is the threshold for
# "this dialog belongs to the same theme as the rest of the app".
SAME_SURFACE = 3.0


# ── reading colours back out of a rendered widget ───────────────────────────

def _render(widget, size=None):
    """Lay a dialog out exactly as a shown one, without putting it on screen."""
    if size:
        widget.resize(*size)
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()
    QApplication.processEvents()
    return widget.grab().toImage().convertToFormat(
        QImage.Format.Format_RGB32)


def _histogram(img):
    counts = collections.Counter()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    raw = bytes(ptr)
    stride = img.bytesPerLine()
    for y in range(img.height()):
        row = raw[y * stride:y * stride + img.width() * 4]
        for x in range(0, len(row), 4):
            counts["#%02X%02X%02X" % (row[x + 2], row[x + 1], row[x])] += 1
    return counts


def _ground(img):
    """The colour the surface is painted in: the most common pixel."""
    return _histogram(img).most_common(1)[0][0]


def _ink(img, ground):
    """The text colour: the pixel furthest from the ground in contrast."""
    best, ratio = None, 1.0
    for colour, n in _histogram(img).items():
        r = contrast_ratio(colour, ground)
        if r > ratio:
            best, ratio = colour, r
    return best, ratio


def _crop(img, dialog, child):
    return img.copy(QRect(child.mapTo(dialog, QPoint(0, 0)), child.size()))


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def light_app(qapp):
    """The palette the shipped app actually runs under.

    ``scheduler_gui.main`` calls ``apply_light_palette(app)`` before the window
    is built, so a dialog measured without it is not the dialog the user gets.
    Restored afterwards: the QApplication is session-scoped.
    """
    from scheduler_app.ui.app import apply_light_palette

    before = qapp.palette()
    apply_light_palette(qapp)
    try:
        yield qapp
    finally:
        qapp.setPalette(before)


@pytest.fixture
def app_ground(light_app):
    """The ground a normal DERSİS dialog is painted on — measured, not typed.

    Read off a real ``QDialog`` wearing ``ui/dialogs.DIALOG_STYLESHEET()``, so
    the oracle moves if the app's theme ever moves.
    """
    from scheduler_app.ui.dialogs import DIALOG_STYLESHEET

    ref = QDialog()
    ref.setStyleSheet(DIALOG_STYLESHEET())
    lay = QVBoxLayout(ref)
    lay.addWidget(QLabel("Ornek metin"))
    lay.addWidget(QLineEdit("Ornek"))
    img = _render(ref, (400, 300))
    return _ground(img)


# ── (a) the app is light. Measured. ─────────────────────────────────────────

def test_the_contrast_instrument_is_the_suites_own():
    """Anti-vacuity: every number below comes out of this one function."""
    _self_check()


def test_dersis_pins_a_light_palette_at_startup(light_app):
    """Green today — the application's own declaration of its theme.

    ``apply_light_palette`` is production code, called from
    ``scheduler_gui.main`` before the main window exists, and its docstring
    says in as many words that "Dersis ships a light-only stylesheet".
    """
    from PyQt6.QtGui import QPalette

    pal = light_app.palette()
    window = pal.color(QPalette.ColorRole.Window).name().upper()
    base = pal.color(QPalette.ColorRole.Base).name().upper()
    text = pal.color(QPalette.ColorRole.Text).name().upper()

    assert relative_luminance(text) < relative_luminance(window), (
        "the application palette is not light: text %s (L=%.3f) is not darker "
        "than window %s (L=%.3f)"
        % (text, relative_luminance(text), window, relative_luminance(window)))
    assert contrast_ratio(text, base) >= AA_NORMAL_TEXT, (
        "%s on %s is %.2f:1" % (text, base, contrast_ratio(text, base)))


def test_the_only_dark_surfaces_in_the_app_stylesheet_are_chrome():
    """Green today — the census behind "light-only", so it is not an assumption.

    43 ``background`` declarations in ``_APP_STYLESHEET_TEMPLATE``. Exactly four
    are dark, and every one of them is chrome or a control state — a status
    strip, a menu hover, two scrollbar-handle hovers. No content surface is
    dark. If a fifth ever appears this fails and the premise of this whole
    module has to be re-argued rather than quietly inherited.
    """
    import inspect
    from scheduler_app.ui import app as app_mod

    src = inspect.getsource(app_mod)
    start = src.index('_APP_STYLESHEET_TEMPLATE = """')
    sheet = src[start + 30:src.index('"""', start + 32)]

    selector, dark, total = None, {}, 0
    for line in sheet.splitlines():
        s = line.strip()
        if s.endswith("{"):
            selector = s[:-1].strip()
        elif s.startswith("background") and ":" in s:
            total += 1
            m = re.search(r"#([0-9A-Fa-f]{6})", s)
            if m and relative_luminance("#" + m.group(1)) < 0.20:
                dark[selector] = "#" + m.group(1).upper()

    assert total >= 40, (
        "only %d background declarations found — the stylesheet was not "
        "parsed" % total)
    assert set(dark) == {"QStatusBar", "QMenuBar::item:selected",
                         "QScrollBar::handle:vertical:hover",
                         "QScrollBar::handle:horizontal:hover"}, (
        "the set of dark surfaces in the app has changed: %r" % (dark,))


def test_a_standard_dersis_dialog_is_dark_ink_on_a_light_ground(app_ground):
    """Green today — the oracle the two dialogs below are compared against."""
    from scheduler_app.ui.dialogs import DIALOG_STYLESHEET

    ref = QDialog()
    ref.setStyleSheet(DIALOG_STYLESHEET())
    lay = QVBoxLayout(ref)
    label = QLabel("Ornek metin")
    lay.addWidget(label)
    img = _render(ref, (400, 300))

    ground = _ground(_crop(img, ref, label))
    ink, ratio = _ink(_crop(img, ref, label), ground)
    assert relative_luminance(ink) < relative_luminance(ground), (
        "a normal DERSİS dialog paints %s on %s" % (ink, ground))
    assert ratio >= AA_NORMAL_TEXT, "%s on %s is %.2f:1" % (ink, ground, ratio)
    assert contrast_ratio(ground, app_ground) < SAME_SURFACE


# ── (c) the defect ──────────────────────────────────────────────────────────

def _crash_dialog():
    from scheduler_app.ui.bug_report import CrashReportDialog
    return CrashReportDialog(
        "IndexError", "list index out of range",
        "Traceback (most recent call last):\n"
        "  File \"ui/app.py\", line 5408, in _copy_to_clipboard\n"
        "IndexError: list index out of range\n",
        log_path="C:\\Users\\x\\Documents\\Dersis\\logs\\crash.log")


def test_the_crash_dialog_is_painted_in_the_applications_theme(app_ground):
    """RED today — #0F172A against the app's #F8FAFC is 17.06:1.

    A failure means the dialog DERSİS shows a user at the moment it fails is
    visibly not the same application: a slate-900 panel with white text, in a
    product whose every other surface is white.
    """
    dlg = _crash_dialog()
    ground = _ground(_render(dlg, (560, 480)))
    ratio = contrast_ratio(ground, app_ground)
    assert ratio < SAME_SURFACE, (
        "CrashReportDialog is painted %s; a DERSİS dialog is painted %s. "
        "That is %.2f:1 — two different surfaces, not one theme."
        % (ground, app_ground, ratio))


def test_the_bug_report_dialog_is_painted_in_the_applications_theme(app_ground):
    """RED today — the manual report dialog shares ``_BUG_DIALOG_STYLE``."""
    from scheduler_app.ui.bug_report import BugReportDialog

    dlg = BugReportDialog(report_type="manual")
    ground = _ground(_render(dlg, (560, 640)))
    ratio = contrast_ratio(ground, app_ground)
    assert ratio < SAME_SURFACE, (
        "BugReportDialog is painted %s; a DERSİS dialog is painted %s "
        "(%.2f:1)" % (ground, app_ground, ratio))


def test_every_text_surface_in_the_crash_dialog_is_dark_on_light(app_ground):
    """RED today — every labelled region of the dialog is inverted.

    Per-widget rather than whole-dialog, because two of the eight ``#1e293b``
    sites are *inline* stylesheets on single widgets (the red error chip and the
    traceback view) that a fix to ``_BUG_DIALOG_STYLE`` alone would leave
    behind. Buttons are excluded: a primary accent button is *meant* to differ
    from the ground.
    """
    dlg = _crash_dialog()
    dlg._toggle_traceback()   # the traceback view starts collapsed, and it
                              # carries one of the two inline #1e293b sites
    img = _render(dlg, (560, 560))

    offenders = []
    for child in dlg.findChildren(QWidget):
        if not isinstance(child, (QLabel, QLineEdit, QTextEdit, QComboBox)):
            continue
        if not child.isVisible() or child.width() < 8 or child.height() < 8:
            continue
        crop = _crop(img, dlg, child)
        ground = _ground(crop)
        name = "%s#%s" % (type(child).__name__, child.objectName() or "-")

        if contrast_ratio(ground, app_ground) >= SAME_SURFACE:
            offenders.append(
                "%-28s ground %s is %.2f:1 from the app's %s"
                % (name, ground, contrast_ratio(ground, app_ground),
                   app_ground))
            continue
        ink, ratio = _ink(crop, ground)
        if ink is None or ratio < 1.5:
            continue
        if relative_luminance(ink) >= relative_luminance(ground):
            offenders.append(
                "%-28s paints light ink %s on dark ground %s"
                % (name, ink, ground))

    assert not offenders, (
        "the crash dialog's text surfaces do not belong to the light theme:\n  "
        + "\n  ".join(offenders))


# ── the trap: one thing in this module must NOT be relit ────────────────────

def test_the_status_bar_bug_icon_is_left_alone(light_app):
    """Green today, and must stay green.

    ``BugReportButton`` is the one part of ``ui/bug_report.py`` that is painted
    on the app's single **dark** surface — ``QStatusBar { background: #1E293B }``.
    Its icon colour is correct as it stands (5.71:1, clearing WCAG 1.4.11's 3:1
    for a non-text graphic). A fix that sweeps every colour in the module into a
    light palette breaks it, and this is where that shows up.
    """
    import inspect
    from scheduler_app.ui import app as app_mod
    from scheduler_app.ui import bug_report

    src = inspect.getsource(app_mod)
    m = re.search(r"QStatusBar\s*\{[^}]*?background:\s*(#[0-9A-Fa-f]{6})", src)
    assert m, "could not read the status bar background out of ui/app.py"
    bar = m.group(1).upper()

    button_src = inspect.getsource(bug_report.BugReportButton.paintEvent)
    icons = re.findall(r"QColor\('(#[0-9A-Fa-f]{6})'\)", button_src)
    assert icons, "BugReportButton no longer names its icon colours"

    for colour in icons:
        ratio = contrast_ratio(colour, bar)
        assert ratio >= SAME_SURFACE, (
            "the status-bar bug icon %s is %.2f:1 on the status bar %s — "
            "WCAG 1.4.11 needs 3:1 for a graphic. The status bar is DARK; "
            "this widget is not part of the light-theme fix."
            % (colour, ratio, bar))
