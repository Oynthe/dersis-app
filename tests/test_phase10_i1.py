"""Item 1 — Ctrl+C on the Dashboard tab makes DERSİS announce it has crashed.

The defect
----------
``ui/app.py::_copy_to_clipboard`` splits on the active tab. Tab 3 ("Show
everything") has its own branch; **every other tab** falls into an ``else``
that indexes a three-element list by the tab number::

    filter_fn = [self._filter_classroom, self._filter_group,
                 self._filter_lecturer][tab_idx]

The notebook has **five** tabs. The Dashboard is index 4, so the expression is
``[a, b, c][4]`` and raises ``IndexError: list index out of range`` before
anything reaches the clipboard.

Why the user sees a crash report rather than nothing
----------------------------------------------------
Measured in this environment (Qt 6.11.0 / PyQt 6.11.0), in a subprocess, with a
``QShortcut``-activated slot that raises:

* with the **default** ``sys.excepthook``: the slot is entered and the process
  **dies inside the emit** — the line after the emit never runs. PyQt6 routes an
  unhandled slot exception to ``qFatal()``.
* with a **custom** ``sys.excepthook`` installed: the hook is called with the
  exception and the process **survives** (exit 0).

``scheduler_gui.py`` installs ``_global_exception_handler`` as ``sys.excepthook``
before ``main()``, so the shipped app takes the second path: crash log written,
``CrashReportDialog`` shown, application still running, clipboard untouched.
That is the user-visible defect — not a silent kill, and not a no-op.

What these tests pin
--------------------
The *user-reachable* path: a real ``QShortcut``, driven with a real
``QTest.keyClick`` of Ctrl+C on an activated window, exactly as
``ui/app.py`` wires it (``QKeySequence("Ctrl+C")``, ``WindowShortcut``
context). Nothing here calls ``_copy_to_clipboard`` by name, and nothing plants
a tab index by hand that the app would not itself produce — Ctrl+5 is the
shortcut the app registers for the Dashboard and is what selects it here.

Two of the five tests below are GREEN today on purpose:

``test_the_dashboard_is_the_fifth_tab``
    the geography the defect depends on. If the tab count ever stops being 5,
    or the Dashboard stops being index 4, the other tests are measuring
    something else and this one says so first.
``test_ctrl_c_reaches_the_clipboard_from_a_timetable_tab``
    the anti-vacuity control. If ``QTest.keyClick`` did not actually fire the
    shortcut, the two red tests below would stay red after any fix and would be
    pinning the harness, not the product. This one proves the key press really
    travels shortcut → slot → clipboard.

What the Dashboard should copy
------------------------------
"Everything" — the same matrix tab 3 produces. Both export surfaces in
``ui/app.py`` already say so in as many words::

    _export_to_excel: {0: "classroom", 1: "group", 2: "lecturer",
                       3: "everything", 4: "everything"}
    _export_to_pdf  : the same dict, verbatim

and the Dashboard itself is unfiltered: ``DashboardWidget.refresh(state)`` is
handed the whole state and reports on every class in it, so there is no subset
for a filter function to select. ``test_the_excel_export_already_treats_the_
dashboard_as_everything`` drives the real export from both tabs and compares the
two workbooks, so the claim is measured rather than read off the source.
"""
import sys

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PyQt6.QtTest import QTest

from scheduler_app.core.models import mark_placed, new_class

pytestmark = pytest.mark.ui


# ── the world the copy happens in ───────────────────────────────────────────

def _seed(win):
    """Two days, three sessions, one room, one placed lesson, one year/branch.

    The smallest state for which ``_copy_to_clipboard`` does not early-return:
    it needs ``days``, ``slots`` and at least one *placed* class, and the
    everything-matrix branch additionally walks ``years``.
    """
    s = win.state_data
    s["days"] = ["monday", "tuesday"]
    s["slots"] = ["09:00", "10:00", "11:00"]
    s["classrooms"] = ["R001"]
    s["classroom_capacities"] = {"R001": 40}
    s["lecturers"] = ["Ada Lovelace"]
    s["years"] = {"Year-1": ["A"]}
    s["classes"] = []

    cls = new_class()
    cls.update(name="Fizik", class_code="FZ101", lecturer="Ada Lovelace",
               duration=1, participants=10,
               targets=[{"year": "Year-1", "branch": "A"}])
    s["classes"].append(cls)
    mark_placed(cls, "monday", "09:00", "R001")
    win.refresh_grid()
    return cls


def _activate(win):
    """Make the window shortcut-eligible without putting it on a screen.

    A ``WindowShortcut`` fires only for the *active* window. Under the
    offscreen plugin ``requestActivate()`` is a no-op (measured: still
    ``isActiveWindow() == False``, shortcut never fires), so the window is
    activated explicitly. ``WA_DontShowOnScreen`` keeps this honest on a real
    platform too — no native window, no flash, and the shortcut map still sees
    an active window.
    """
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    QApplication.setActiveWindow(win)
    QApplication.processEvents()
    assert win.isActiveWindow(), (
        "the window is not active, so no WindowShortcut can fire and every "
        "assertion below would be measuring the harness")


def _press(win, key):
    QTest.keyClick(win, key, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()


@pytest.fixture
def crashes(monkeypatch):
    """Record what ``sys.excepthook`` is handed, the way scheduler_gui does.

    Load-bearing, not decoration: with the *default* hook in place PyQt6 calls
    ``qFatal()`` on an unhandled slot exception and takes the pytest process
    with it (measured — the emit never returns). Installing a hook is also what
    production does, so this is the shipped configuration, not a test-only
    escape.
    """
    recorded = []

    def _hook(exc_type, exc_value, exc_tb):
        recorded.append("%s: %s" % (exc_type.__name__, exc_value))

    monkeypatch.setattr(sys, "excepthook", _hook)
    return recorded


@pytest.fixture
def win(make_app, crashes):
    w = make_app()
    _seed(w)
    _activate(w)
    assert not crashes, (
        "building and seeding the window already raised through the exception "
        "hook, so nothing below can attribute a crash to Ctrl+C: %r" % (crashes,))
    return w


# ── the geography the defect lives in ───────────────────────────────────────

def test_the_dashboard_is_the_fifth_tab(win):
    """Green today: the premise every other test in this module rests on."""
    assert win.notebook.count() == 5, (
        "the notebook has %d tabs, not 5 — the tab indices below no longer "
        "mean what this module says they mean" % win.notebook.count())
    assert win.notebook.indexOf(win.dashboard_widget) == 4, (
        "the Dashboard is tab %d, not tab 4"
        % win.notebook.indexOf(win.dashboard_widget))

    _press(win, Qt.Key.Key_5)
    assert win.notebook.currentIndex() == 4, (
        "Ctrl+5 — the shortcut ui/app.py registers for the Dashboard — "
        "selected tab %d" % win.notebook.currentIndex())


# ── the anti-vacuity control ────────────────────────────────────────────────

def test_ctrl_c_reaches_the_clipboard_from_a_timetable_tab(win, crashes):
    """Green today. Proves the key press really travels to the clipboard.

    Without this, a harness in which ``QTest.keyClick`` quietly fired nothing
    would produce exactly the same red as the real defect, and would stay red
    after a correct fix.
    """
    QApplication.clipboard().setText("")
    _press(win, Qt.Key.Key_1)
    assert win.notebook.currentIndex() == 0
    _press(win, Qt.Key.Key_C)

    assert not crashes, (
        "Ctrl+C on the By-Classroom tab reached the exception hook: %r"
        % (crashes,))
    assert QApplication.clipboard().text(), (
        "Ctrl+C on tab 0 put nothing on the clipboard — the shortcut never "
        "reached _copy_to_clipboard, so this module is measuring itself")


# ── the defect ──────────────────────────────────────────────────────────────

def test_ctrl_c_on_the_dashboard_tab_does_not_crash(win, crashes):
    """RED today — IndexError: list index out of range.

    A failure means a user who pressed Ctrl+C on the Dashboard was told DERSİS
    had crashed: ``scheduler_gui._global_exception_handler`` appends a CRASH
    block to the crash log and puts ``CrashReportDialog`` in front of them,
    inviting them to email a bug report — for a keystroke that should have
    copied their timetable.
    """
    _press(win, Qt.Key.Key_5)
    assert win.notebook.currentIndex() == 4

    _press(win, Qt.Key.Key_C)

    assert not crashes, (
        "Ctrl+C on the Dashboard tab raised through the application's "
        "exception hook — the user gets the crash-report dialog and a crash "
        "log entry: %s" % "; ".join(crashes))


def test_ctrl_c_on_the_dashboard_tab_copies_the_whole_timetable(win, crashes):
    """RED today — nothing reaches the clipboard at all.

    The Dashboard reports on the *entire* schedule, and both export surfaces
    already map tab 4 to "everything", so the clipboard must carry the same
    text tab 3 produces. Asserting equality with tab 3's own output rather than
    a literal keeps this true for any future change to the matrix format.
    """
    _press(win, Qt.Key.Key_4)
    assert win.notebook.currentIndex() == 3
    QApplication.clipboard().setText("")
    _press(win, Qt.Key.Key_C)
    everything = QApplication.clipboard().text()
    assert everything.strip(), (
        "tab 3 (Show everything) copied nothing, so there is no oracle to "
        "compare the Dashboard against")

    _press(win, Qt.Key.Key_5)
    assert win.notebook.currentIndex() == 4
    QApplication.clipboard().setText("")
    _press(win, Qt.Key.Key_C)
    dashboard = QApplication.clipboard().text()

    assert dashboard == everything, (
        "Ctrl+C on the Dashboard did not copy the whole timetable.\n"
        "  exception hook saw : %s\n"
        "  clipboard holds    : %r\n"
        "  tab 3 would give   : %r"
        % ("; ".join(crashes) or "nothing", dashboard[:120], everything[:120]))


# ── why "everything" is the right answer for tab 4 ──────────────────────────

@pytest.mark.excel
def test_the_excel_export_already_treats_the_dashboard_as_everything(
        win, crashes, tmp_path, monkeypatch):
    """Green today. The export surface has already decided what tab 4 means.

    Driven through ``_export_to_excel`` rather than read off its ``mode`` dict,
    so this measures the workbook a user would actually receive.
    """
    pytest.importorskip("openpyxl")
    import openpyxl

    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda *a, **k: None))

    def _export_from(tab_index, name):
        target = tmp_path / name
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(target), "")))
        win.notebook.setCurrentIndex(tab_index)
        QApplication.processEvents()
        win._export_to_excel()
        assert target.exists(), (
            "no workbook was written for tab %d" % tab_index)
        book = openpyxl.load_workbook(str(target))
        return [ws.title for ws in book.worksheets], [
            [[c.value for c in row] for row in ws.iter_rows()]
            for ws in book.worksheets]

    from_everything = _export_from(3, "tab3.xlsx")
    from_dashboard = _export_from(4, "tab4.xlsx")

    assert not crashes, ("the export itself raised: %s" % "; ".join(crashes))
    assert from_dashboard == from_everything, (
        "the Excel export does NOT treat the Dashboard as 'everything' after "
        "all — tab 4 produced sheets %r and tab 3 produced %r. The premise "
        "behind copying 'everything' on tab 4 is wrong; re-derive it."
        % (from_dashboard[0], from_everything[0]))


# ── the dialog the defect puts in front of the user (links to item 11) ──────

def test_an_unhandled_exception_puts_the_crash_report_dialog_on_screen(
        dersis_home, qapp, monkeypatch):
    """Green today. Confirms the crash-dialog claim is real, not folklore.

    ``scheduler_gui._global_exception_handler`` is called directly with a
    synthetic ``IndexError`` — the same exception ``_copy_to_clipboard``
    produces — and the dialog it constructs is captured rather than executed.
    """
    import scheduler_gui
    from scheduler_app import storage
    from scheduler_app.ui import bug_report

    shown = []
    monkeypatch.setattr(bug_report.CrashReportDialog, "exec",
                        lambda self: shown.append(self) or 0)
    monkeypatch.setattr(sys, "__excepthook__",
                        lambda *a: None)

    try:
        [1, 2, 3][4]
    except IndexError:
        scheduler_gui._global_exception_handler(*sys.exc_info())

    assert shown, (
        "an unhandled exception raised while Qt is up did NOT reach "
        "CrashReportDialog")
    dlg = shown[0]
    assert dlg._exc_type == "IndexError"
    assert "list index out of range" in dlg._exc_message

    log = storage.crash_log_path()
    with open(log, "r", encoding="utf-8") as fh:
        body = fh.read()
    assert "CRASH at" in body and "IndexError" in body, (
        "the crash was not written to %s" % log)
