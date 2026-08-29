"""Green and amber status text in the dialogs fails WCAG AA (Phase 10, item 15).

What this module is for
-----------------------
``tests/test_cell_contrast.py`` (ST-UI-005) holds the **timetable grid** to WCAG
2.1 AA, 4.5:1, and pins the retired hexes out of ``ui/renderer.py`` and
``data_io/exporter.py``. It does **not** look at ``ui/dialogs.py`` or
``ui/app.py`` at all -- neither module is imported, named, or scanned there. So
the grid was fixed in Phase 4/5 while the *dialogs* kept painting the exact
hexes that fix retired:

* ``#16A34A`` -- retired from the grid as ``CELL_FG_ROOM`` (1.55:1 -> #0F3D24)
* ``#D97706`` -- retired from the grid as the ``protected`` badge colour

Both are still live in ``ui/dialogs.py`` as *dialog* status text, on the pale
slate backgrounds those dialogs paint.

The formula is not re-implemented here
--------------------------------------
Every ratio below comes from ``test_cell_contrast.contrast_ratio`` -- the same
WCAG 2.1 relative-luminance function the grid is judged by. Two contrast
functions that disagree is worse than one that is absent, so this module
imports rather than copies, and re-runs that module's own instrument check
(``_self_check``) before trusting a single number.

The backgrounds are measured, not assumed
-----------------------------------------
The obvious mistake is to score these against ``#FFFFFF``. None of them sit on
white. Measured by rendering the real dialogs offscreen and reading the modal
pixel under each label (see ``_painted_background``):

===========================================  ==========  =========
site                                         background  ratio
===========================================  ==========  =========
BulkResultsDialog "all N placed" summary     ``#F8FAFC``  3.15:1
BulkResultsDialog grade / insight labels     ``#F1F5F9``  3.01:1
EditClassesDialog status column, even row    ``#FFFFFF``  3.30:1
EditClassesDialog status column, odd row     ``#F1F5F9``  3.01:1
SchedulerApp reschedule badge (``#D97706``)  ``#FEF3C7``  2.86:1
===========================================  ==========  =========

``#F1F5F9`` is ``QPalette.AlternateBase``/``Window`` from
``ui/app.py::apply_light_palette`` -- which ``scheduler_gui.py`` installs on the
QApplication before the window is built, so this module installs it too (and
restores it afterwards; ``qapp`` is session-scoped).

One measurement that contradicts the obvious guess: a *selected* row in
``EditClassesDialog`` does **not** paint the green on ``#DBEAFE``. The table
stylesheet's ``QTableWidget::item:selected { color: #1E293B; }`` wins over
``QTableWidgetItem.setForeground``, measured -- the selected row renders
``#1E293B`` on ``#DBEAFE``. So ``#DBEAFE`` is deliberately absent from the table
above; claiming it would have been a guess that the pixels refute.

Not every green/amber in these two files is a defect
----------------------------------------------------
``#92400E`` on ``#FEF3C7`` (the reschedule notice, 6.37:1) and on ``#FDE68A``
(the list selection, 5.69:1) already clear AA. ``ui/app.py`` therefore already
contains the accessible amber this item needs; the failing badge at
``ui/app.py:1988`` is the one place in that file that reaches for ``#D97706``
instead. That makes this a substitution, not a design task.

Threshold: 4.5:1 throughout. Every site here is a QLabel or table item at
8-11 pt, none bold-and->=14 pt, so none qualifies for the 3:1 large-text
allowance of WCAG 1.4.3. The one non-text-on-background case -- white on the
green "accept results" button -- is still *text*, so 4.5:1 applies to it too.
"""
import colorsys
import inspect
import os
import re

import pytest

from test_cell_contrast import (
    AA_NORMAL_TEXT, contrast_ratio, relative_luminance, _self_check,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIALOGS_PY = os.path.join(REPO, "scheduler_app", "ui", "dialogs.py")
APP_PY = os.path.join(REPO, "scheduler_app", "ui", "app.py")

# A CSS ``color:`` declaration with a literal hex. The negative lookbehind
# keeps ``background-color:``, ``border-color:``, ``gridline-color:`` and
# ``alternate-background-color:`` out -- those paint surfaces and borders,
# which WCAG 1.4.11 holds to 3:1, not text.
_COLOR_DECL = re.compile(r"(?<![-\w])color\s*:\s*(#[0-9A-Fa-f]{6})")
_BG_DECL = re.compile(r"(?<![-\w])background(?:-color)?\s*:\s*(#[0-9A-Fa-f]{6})")
_ANY_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def _is_green_or_amber(hex_color):
    """True for a saturated green/amber/orange *text* colour.

    Hue-based rather than a hardcoded list of the hexes that happen to be
    wrong today, so a tenth site added next month is caught as well. The
    luminance gate keeps pale fills (``#FDE68A`` at 0.79, ``#F59E0B`` at 0.44)
    out: those are backgrounds for dark text, and scoring them against white
    would be measuring a pairing the product never draws.

    The 14 degree floor is where orange stops and red begins in this palette:
    every red these two files use -- ``#DC2626``, ``#EF4444``, ``#991B1B`` --
    sits at hue 0, while the darkest accessible orange a fix would reach for
    (``#9A3412``) is at 15 and ``#C2410C`` at 17.5. A floor of 20 would have
    let a "fixed" grade-D orange slip out of the net entirely, which is the
    opposite of what this gate is for. Red is a separate item.
    """
    r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) / 255.0
               for i in (0, 2, 4))
    hue, sat, _val = colorsys.rgb_to_hsv(r, g, b)
    hue *= 360.0
    return (14.0 <= hue < 175.0 and sat > 0.35
            and relative_luminance(hex_color) < 0.35)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ══════════════════════════════════════════════════════════════════════════
#  Qt scaffolding
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def light_palette(qapp):
    """Install the palette ``scheduler_gui.py`` installs, then put it back.

    ``qapp`` is session-scoped; leaving a mutated palette behind would change
    what every later Qt module in the process renders.
    """
    from scheduler_app.ui.app import apply_light_palette

    saved = qapp.palette()
    apply_light_palette(qapp)
    try:
        yield qapp
    finally:
        qapp.setPalette(saved)


def _show(widget):
    """Realise a widget without putting it on a screen (Phase 9 B6 recipe)."""
    from PyQt6.QtCore import Qt

    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()
    return widget


def _painted_background(image, rect, foreground):
    """The colour actually under a run of text: modal pixel in ``rect``.

    Glyphs are a minority of the pixels in a label rect, so the mode is the
    background. The declared foreground is excluded outright so a very short
    label cannot make the text colour win its own background poll.
    """
    from collections import Counter

    rect = rect.intersected(image.rect())
    if rect.width() < 2 or rect.height() < 2:
        return None
    counts = Counter()
    for y in range(rect.top(), rect.bottom() + 1):
        for x in range(rect.left(), rect.right() + 1):
            counts[image.pixelColor(x, y).name().upper()] += 1
    counts.pop(foreground.upper(), None)
    return counts.most_common(1)[0][0] if counts else None


def _label_foregrounds(label):
    """Every hex this label declares as a text colour.

    Two carriers, both used by ``dialogs.py``: the widget stylesheet
    (``setStyleSheet("color: #16A34A; ...")``) and inline rich text
    (``<span style='color:#16A34A'>``). Read off the live widget, so a colour
    the production code computed at runtime -- the ``grade_color`` and
    ``status_color`` dict lookups -- is picked up exactly as painted.
    """
    found = []
    for source in (label.styleSheet(), label.text()):
        found.extend(m.group(1).upper() for m in _COLOR_DECL.finditer(source))
    return found


def _scan_dialog(qapp, dlg):
    """(label text, fg, measured bg) for every green/amber run in a dialog.

    Walks every tab: a label on a tab that is not current is not rendered, and
    grabbing once would silently skip most of this dialog's colour.
    """
    from PyQt6.QtCore import QRect
    from PyQt6.QtWidgets import QLabel, QTabWidget

    seen, out = set(), []

    def sweep():
        qapp.processEvents()
        image = dlg.grab().toImage()
        for label in dlg.findChildren(QLabel):
            if not label.isVisible():
                continue
            for fg in _label_foregrounds(label):
                if not _is_green_or_amber(fg) or (id(label), fg) in seen:
                    continue
                rect = QRect(label.mapTo(dlg, label.rect().topLeft()),
                             label.rect().size())
                bg = _painted_background(image, rect, fg)
                if bg is None:
                    continue
                seen.add((id(label), fg))
                out.append((label.text()[:44], fg, bg))

    sweep()
    for tabs in dlg.findChildren(QTabWidget):
        for i in range(tabs.count()):
            tabs.setCurrentIndex(i)
            sweep()
    return out


def _failures(samples):
    return ["%-46r %s on %s = %.2f:1" % (text, fg, bg, contrast_ratio(fg, bg))
            for text, fg, bg in samples
            if contrast_ratio(fg, bg) < AA_NORMAL_TEXT]


# ── production fixtures ─────────────────────────────────────────────────

def _class(name="Matematik", code="MAT101", placed=True):
    return {"name": name, "class_code": code, "lecturer": "Ogretmen",
            "duration": 1, "participants": 20,
            "targets": [{"year": "Y1", "branch": "A"}],
            "allowed_days": [], "allowed_times": [],
            "excluded_days": [], "excluded_times": [],
            "required_classrooms": [], "excluded_classrooms": [],
            "placed": placed, "pinned": False, "protection": "none",
            "is_online": False, "joint_class_group": "", "sequential": False}


def _state():
    return {"days": ["monday"], "slots": ["09:00", "10:00"],
            "classrooms": ["R001"], "classroom_capacities": {"R001": 30},
            "lecturers": ["Ogretmen"], "lecturer_availability": {},
            "years": {"Y1": ["A"]},
            "classes": [_class(), _class("Fizik", "FZK101", placed=False),
                        _class("Kimya", "KIM101", placed=False)]}


# ══════════════════════════════════════════════════════════════════════════
#  anti-vacuity
# ══════════════════════════════════════════════════════════════════════════

def test_the_contrast_instrument_is_the_grids_own_and_still_calibrated():
    """Item 15 -- one formula for the whole product, checked before use.

    If this module grew its own ``contrast_ratio`` the dialogs could be
    certified against a subtly different curve than the grid, and the two
    would drift without either failing. The import below is the guarantee;
    ``_self_check`` re-runs WCAG's own worked examples (21:1 and 1:1) so a
    regression in the shared helper cannot quietly relax every threshold here.
    """
    import test_cell_contrast

    assert os.path.samefile(
        os.path.dirname(os.path.abspath(test_cell_contrast.__file__)),
        os.path.dirname(os.path.abspath(__file__))), (
        "the helper must be the grid's, in tests/test_cell_contrast.py")
    _self_check()
    assert AA_NORMAL_TEXT == 4.5


def test_the_grid_contrast_suite_does_not_cover_the_dialogs():
    """Item 15 -- why this file has to exist at all.

    The whole premise is that the grid is held to a standard the dialogs are
    not. If someone later extends ``test_cell_contrast.py`` to scan
    ``ui/dialogs.py``, this assertion is the notice that the two modules now
    overlap and one of them should be retired.
    """
    src = _read(os.path.join(REPO, "tests", "test_cell_contrast.py")).lower()
    assert "dialog" not in src, (
        "test_cell_contrast.py now mentions the dialogs; check whether it "
        "actually covers them and de-duplicate with this module")


# ══════════════════════════════════════════════════════════════════════════
#  the live dialogs
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("grade", ["A", "C", "D"])
def test_the_results_dialog_status_text_clears_aa(light_palette, grade):
    """Item 15 -- the success/warning text a teacher reads after every solve.

    Drives the real ``BulkResultsDialog`` constructor, then measures both
    sides: the foreground off the widget the dialog built, the background off
    the pixels it painted. Nothing here plants a colour.

    Three grades because the quality line picks its colour from a dict --
    ``A`` green ``#16A34A``, ``C`` amber ``#D97706``, ``D`` orange ``#EA580C``
    (dialogs.py:3969) -- and a single grade would exercise one third of it.

    A failure means the line saying every class was placed, and the line
    saying the schedule scored 91/100, are the least legible text on the
    dialog.
    """
    from scheduler_app.ui.dialogs import BulkResultsDialog

    placed = [(_class(), "monday", "09:00", "R001")]
    analytics = {"grade": grade, "global_score": 91.0,
                 "insights": [{"type": "success", "message": "Tum dersler yerlesti"},
                              {"type": "warning", "message": "Ogretmen yuku dengesiz"},
                              {"type": "info", "message": "Bilgi"}]}
    explanation = {"verdict": "Daha iyi", "engine": "cpsat",
                   "improvements": [{"description": "Bosluk azaldi"}],
                   "degradations": [{"description": "Gec saat"}]}
    dlg = BulkResultsDialog(None, placed, [], rescheduled=True,
                            analytics=analytics,
                            reschedule_explanation=explanation)
    try:
        _show(dlg)
        samples = _scan_dialog(light_palette, dlg)
        assert samples, "no green/amber text found -- the probe measured nothing"
        assert not _failures(samples), (
            "BulkResultsDialog paints status text below WCAG AA %.1f:1:\n  "
            % AA_NORMAL_TEXT + "\n  ".join(_failures(samples)))
    finally:
        dlg.deleteLater()


def test_the_negotiation_report_status_text_clears_aa(light_palette):
    """Item 15 -- the per-class verdict in the negotiation tab.

    ``dialogs.py:4174-4177`` colours each unplaced class by how bad its
    situation is: ``ok`` ``#16A34A``, ``constrained`` ``#D97706``,
    ``infeasible`` ``#DC2626``. The green and the amber are the two that carry
    "this one is nearly fixable" -- exactly the rows a user acts on.

    ``_build_negotiation_tab`` is the production method the tab-change signal
    calls; it is invoked directly here only because no event loop is running.
    """
    from scheduler_app.ui.dialogs import BulkResultsDialog

    negotiation = {"diagnostic_summary": {"overall_assessment": "Degerlendirme"},
                   "class_reports": [
                       {"class_name": "Fizik", "status": "ok",
                        "summary": "Yer var", "suggestions": []},
                       {"class_name": "Kimya", "status": "constrained",
                        "summary": "Sikisik", "suggestions": []},
                       {"class_name": "Biyoloji", "status": "infeasible",
                        "summary": "Imkansiz", "suggestions": []}]}
    dlg = BulkResultsDialog(
        None, [(_class(), "monday", "09:00", "R001")],
        [(_class("Fizik", "FZK101", placed=False), "yer yok")],
        negotiation_result=negotiation)
    try:
        _show(dlg)
        dlg._build_negotiation_tab()
        samples = _scan_dialog(light_palette, dlg)
        assert samples, "no green/amber text found -- the probe measured nothing"
        assert not _failures(samples), (
            "the negotiation report paints status text below WCAG AA "
            "%.1f:1:\n  " % AA_NORMAL_TEXT + "\n  ".join(_failures(samples)))
    finally:
        dlg.deleteLater()


def test_the_class_table_status_column_clears_aa(light_palette):
    """Item 15 -- "placed"/"unplaced" in the class list, on both row colours.

    ``dialogs.py:4586-4588`` sets the item foreground directly, so the colour
    is read back off the item the production code populated. The table has
    ``setAlternatingRowColors(True)``, so half the rows are ``#F1F5F9`` and
    half ``#FFFFFF`` -- both are measured off the rendered viewport, because
    scoring only the white rows would certify a column that fails on every
    other line.
    """
    from PyQt6.QtWidgets import QAbstractItemView
    from scheduler_app.ui.dialogs import EditClassesDialog

    dlg = EditClassesDialog(None, _state())
    try:
        _show(dlg)
        dlg.resize(2200, 560)
        table = dlg.table
        light_palette.processEvents()
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.scrollToItem(table.item(0, 7))
        light_palette.processEvents()
        image = table.viewport().grab().toImage()

        samples = []
        for row in range(table.rowCount()):
            item = table.item(row, 7)
            fg = item.foreground().color().name().upper()
            if not _is_green_or_amber(fg):
                continue
            rect = table.visualRect(table.model().index(row, 7))
            bg = _painted_background(image, rect, fg)
            if bg is None:
                continue
            samples.append(("row %d %s" % (row, item.text()), fg, bg))

        assert len(samples) >= 2, (
            "expected both the placed (green) and unplaced (amber) status "
            "colours to be measured, got %r" % (samples,))
        assert not _failures(samples), (
            "EditClassesDialog's status column is below WCAG AA %.1f:1:\n  "
            % AA_NORMAL_TEXT + "\n  ".join(_failures(samples)))
    finally:
        dlg.deleteLater()


def test_the_accept_results_button_label_clears_aa(light_palette):
    """Item 15 -- white on the green "accept results" button.

    ``dialogs.py:4128-4129`` sets ``background: #16A34A; color: white``. Both
    halves come off the button the dialog built, so this is the pairing the
    product actually draws, not one this test invented. Button *labels* are
    text under WCAG 1.4.3, so the threshold is 4.5:1, not the 3:1 that governs
    the button's own outline.
    """
    from PyQt6.QtWidgets import QPushButton
    from scheduler_app.ui.dialogs import BulkResultsDialog

    dlg = BulkResultsDialog(None, [(_class(), "monday", "09:00", "R001")], [])
    try:
        _show(dlg)
        pairs = []
        for btn in dlg.findChildren(QPushButton):
            sheet = btn.styleSheet()
            bgs = _BG_DECL.findall(sheet)
            if not bgs or not _is_green_or_amber(bgs[0]):
                continue
            fg = "#FFFFFF" if "color: white" in sheet else None
            fg = fg or (_COLOR_DECL.search(sheet).group(1)
                        if _COLOR_DECL.search(sheet) else None)
            if fg:
                pairs.append((btn.text()[:40], fg.upper(), bgs[0].upper()))
        assert pairs, "no green/amber button found in BulkResultsDialog"
        assert not _failures(pairs), (
            "a button label is below WCAG AA %.1f:1:\n  " % AA_NORMAL_TEXT
            + "\n  ".join(_failures(pairs)))
    finally:
        dlg.deleteLater()


# ══════════════════════════════════════════════════════════════════════════
#  ui/app.py
# ══════════════════════════════════════════════════════════════════════════

def test_the_reschedule_badge_label_clears_aa():
    """Item 15 -- the single ``ui/app.py`` site: the amber reschedule badge.

    ``_update_impact_badge`` writes complete ``QToolButton`` rules, so the
    background is *stated beside* the foreground and neither has to be guessed:
    ``background: #FEF3C7 ... color: %s``. Both are read out of the
    method's own source, which also picks up the ``:hover`` background the
    badge switches to (``#FDE68A``) -- the version of this badge the user is
    looking at while they decide whether to click it.

    The foreground is a ``%s`` filled from ``core.constants``, so the source is
    resolved against that module before it is parsed. Without resolving, this
    test does not fail — it finds no green or amber at all and trips its own
    anti-vacuity assertion, which is the behaviour that is wanted: a probe that
    stops being able to see its subject must say so rather than pass.

    Only the green/amber pairing is asserted; ``#DC2626`` on ``#FEE2E2``
    (3.95:1) is the neighbouring red badge and belongs to a different item.
    """
    from scheduler_app.core import constants
    from scheduler_app.ui.app import SchedulerApp

    src = inspect.getsource(SchedulerApp._update_impact_badge)
    # Resolve `"... color: %s ..." % SOME_CONSTANT` against core.constants, so
    # hoisting a hex into a shared constant does not blind this test. Only
    # str-valued names that look like colours are substituted.
    for name in re.findall(r"%\s*([A-Z][A-Z0-9_]*)", src):
        value = getattr(constants, name, None)
        if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            src = src.replace("%s", value, 1)
    # Split into CSS rules so a ``color:`` is only ever paired with a
    # ``background:`` from the SAME rule block.
    pairs = []
    for rule in re.findall(r"\{([^{}]*)\}", src.replace('"\n', "").replace('"', "")):
        fgs = [m.group(1).upper() for m in _COLOR_DECL.finditer(rule)]
        bgs = [b.upper() for b in _BG_DECL.findall(rule)]
        for fg in fgs:
            if _is_green_or_amber(fg):
                for bg in bgs:
                    pairs.append(("impact badge", fg, bg))
    assert pairs, (
        "no green/amber foreground found in _update_impact_badge -- the probe "
        "is not reading the rule it thinks it is:\n" + src[:400])
    assert not _failures(pairs), (
        "the reschedule badge label is below WCAG AA %.1f:1:\n  "
        % AA_NORMAL_TEXT + "\n  ".join(_failures(pairs)))


def test_the_reschedule_badge_hover_background_is_also_measured():
    """Item 15 -- pins that the hover state above was actually found.

    Without this, a refactor that moves the ``:hover`` rule elsewhere would
    make the previous test quietly measure one background instead of two and
    still look like it passed.
    """
    from scheduler_app.ui.app import SchedulerApp

    src = inspect.getsource(SchedulerApp._update_impact_badge)
    assert "#FDE68A" in src.upper(), (
        "the amber badge's :hover background moved; re-point the badge test")


# ══════════════════════════════════════════════════════════════════════════
#  the net: anything green or amber, anywhere in the two files
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", [DIALOGS_PY, APP_PY],
                         ids=["dialogs.py", "app.py"])
def test_no_green_or_amber_text_literal_fails_even_against_white(path):
    """Item 15 -- the exhaustive net under the live tests.

    The tests above build five widgets; this one reads every ``color: #hex``
    in both files, so a green or amber on a screen nobody instantiated is
    still caught.

    It scores against ``#FFFFFF``, which is deliberately the *kindest*
    background either file paints -- every other surface here (``#F8FAFC``,
    ``#F1F5F9``, ``#FEF3C7``, ``#FDE68A``) is darker and yields a lower ratio
    for text this dark. So a failure here is unarguable: the colour is
    illegible on the most forgiving surface in the product, and worse
    everywhere it is actually drawn. Sites that pass here may still fail on
    their real background -- that is what the live tests above are for.
    """
    src = _read(path)
    offenders = []
    for match in _COLOR_DECL.finditer(src):
        hex_color = match.group(1).upper()
        if not _is_green_or_amber(hex_color):
            continue
        ratio = contrast_ratio(hex_color, "#FFFFFF")
        if ratio < AA_NORMAL_TEXT:
            line = src[:match.start()].count("\n") + 1
            offenders.append("%s:%d  %s = %.2f:1 on #FFFFFF  |  %s"
                             % (os.path.basename(path), line, hex_color,
                                ratio, src.splitlines()[line - 1].strip()[:70]))
    assert not offenders, (
        "green/amber text colours that fail WCAG AA %.1f:1 on white, the "
        "lightest surface these files paint:\n  " % AA_NORMAL_TEXT
        + "\n  ".join(offenders))


def test_the_retired_grid_greens_and_ambers_are_gone_from_the_dialogs():
    """Item 15 -- the dialogs must not keep using hexes the grid retired.

    ``#16A34A`` and ``#D97706`` were both removed from the timetable in Phase
    4/5 *for this exact reason* -- ``core/constants.py`` records them as
    ``CELL_FG_ROOM``'s and the protected badge's "was" values, at 1.55:1 and
    1.50:1 on a cell. ``test_cell_contrast.py`` pins them out of
    ``renderer.py`` and ``exporter.py``. It does not pin them out of
    ``dialogs.py``, so the dialogs went on painting them for two more phases.
    """
    src = _read(DIALOGS_PY)
    retired = {"#16A34A": "CELL_FG_ROOM (grid: 1.55:1)",
               "#D97706": "protected badge (grid: 1.50:1)"}
    offenders = []
    for literal, role in retired.items():
        for match in re.finditer(re.escape(literal), src, re.I):
            line = src[:match.start()].count("\n") + 1
            text = src.splitlines()[line - 1]
            if text.lstrip().startswith("#"):  # a hex in prose paints nothing
                continue
            offenders.append("dialogs.py:%d  %s (%s)  %s"
                             % (line, literal, role, text.strip()[:64]))
    assert not offenders, (
        "hexes the timetable retired for failing WCAG AA are still painted by "
        "the dialogs:\n  " + "\n  ".join(offenders))
