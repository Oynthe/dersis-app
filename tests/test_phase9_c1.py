"""C1 — an imported workbook's lecturer day constraints must actually bind.

``data_io/importer.py._process_teachers`` builds ``allowed_days`` /
``excluded_days`` with ``_parse_comma_list``, a bare ``str.split(",")``. No day
value is ever converted to one of the seven keys in
``i18n/day_keys.DAY_KEYS``, and ``ui/app.py._import_from_excel`` merges
``dataset.state["lecturer_availability"]`` into the live state verbatim. Every
engine reader — ``apply_lecturer_availability_filters`` and
``lecturer_available_at`` in ``core/models.py`` — compares those strings against
``state["days"]``, which holds keys. ``"Pazartesi" != "monday"``, so:

  * a non-empty ``allowed_days`` intersects nothing and the lecturer has **zero**
    available days: none of their classes can be placed by anything;
  * a non-empty ``excluded_days`` subtracts nothing and the lecturer is bookable
    on the day the workbook said they cannot work.

This is not a hypothetical spelling. The app's own ``generate_excel_template``
writes ``tr("weekdays.<key>")`` into those two cells (``data_io/template.py``
lines 48-61), so the file the user is *told* to fill in carries exactly the
values that fail, in whatever language they generated it in.

Two tests, one for each half of the exposure:

``..._binds_immediately``
    the state is repaired later, by ``normalize_state_day_keys`` running inside
    ``flush_auto_save``'s fingerprint. That is a 1500 ms debounce
    (``AUTOSAVE_DEBOUNCE_MS``), restarted by every ``refresh_grid``. Measured:
    the constraint is wrong the instant ``_import_from_excel`` returns and right
    again ~1.5 s later. A user cannot normally reach into that window — the
    import's own modal success dialog runs a nested event loop and the timer
    fires inside it — so this half is latent, not a live crash. It is asserted
    anyway because "correct only after a timer" is not a contract any caller
    can rely on, and because the second test below cannot be fixed without
    fixing this one.

``..._survives_an_import_before_setup``
    the reachable, permanent half. The workbook has no days/slots sheet
    (``data_io/schema.WORKBOOK_SHEETS``), so the week can only come from Setup —
    and nothing forces Setup to run *first*. Import into a schedule whose week
    is still empty and the repair pass turns destructive: ``allowed`` is
    ``set()``, so ``normalize_state_day_keys`` prunes every day out of every
    availability record and ``_auto_save`` writes the emptied roster to disk.
    The user then configures Mon-Fri, solves, and every "only Mon/Wed/Fri",
    every "not on Friday" the workbook carried is simply gone, with nothing in
    the import report about it.

Both go through the real ``SchedulerApp._import_from_excel`` rather than the
importer alone, on purpose: they must stay true wherever the fix lands (the
importer, the merge, or the normalizer), and they must not canonize the shape
``load_scheduler_data_from_excel`` happens to return today.
"""
import os

import pytest

pytestmark = [pytest.mark.ui, pytest.mark.excel]

# ``SchedulerApp`` registers into the process-wide ``TierEnforcement`` singleton
# and never unregisters; snapshot/restore or this module leaks dead QActions.
_TIER_REGISTRIES = (
    "_gated_widgets", "_gated_actions", "_on_tier_changed",
    "_export_submenu_refreshers",
)

# The template's T002 ("only these three days") and T003 ("not Friday").
_ALLOW_ONLY = ("monday", "wednesday", "friday")
_EXCLUDE = "friday"


class _Recorder:
    """Stand-in for a modal static; records instead of blocking."""

    def __init__(self, ret):
        self._ret = ret
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._ret


@pytest.fixture
def message_boxes(monkeypatch):
    """Neutralize every modal the import path can raise.

    ``UpgradeDialog.exec`` is included because ``require_entity_limit`` calls it
    directly: if the tier below ever stopped being Institutional an unpatched
    ``exec()`` would hang the run rather than fail it.
    """
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from scheduler_app.ui.tier_enforcement import UpgradeDialog

    recorders = {}
    for name in ("information", "warning", "critical", "question"):
        ret = (QMessageBox.StandardButton.Yes if name == "question"
               else QMessageBox.StandardButton.Ok)
        recorders[name] = _Recorder(ret)
        monkeypatch.setattr(QMessageBox, name, staticmethod(recorders[name]))
    monkeypatch.setattr(UpgradeDialog, "exec",
                        _Recorder(QDialog.DialogCode.Rejected.value))
    return recorders


@pytest.fixture
def window(qapp, dersis_home, monkeypatch):
    """A real, never-shown ``SchedulerApp`` with a brand-new (empty) schedule.

    The first-run controller is disabled — it arms QTimers that would otherwise
    outlive the test — and the tier is pinned to Institutional by assignment
    rather than ``set_tier()``, which would sweep gates belonging to windows
    other tests already destroyed.
    """
    from scheduler_app.plans import TIER_INSTITUTIONAL
    from scheduler_app.ui.first_run import FirstRunController
    from scheduler_app.ui.tier_enforcement import TierEnforcement

    monkeypatch.setattr(FirstRunController, "start", lambda self: None)

    enforcer = TierEnforcement.instance()
    prev = (enforcer._tier_slug, enforcer._tier_confirmed)
    saved = {n: list(getattr(enforcer, n))
             for n in _TIER_REGISTRIES if hasattr(enforcer, n)}
    enforcer._tier_slug, enforcer._tier_confirmed = TIER_INSTITUTIONAL, True

    from scheduler_app.ui.app import SchedulerApp

    win = SchedulerApp()
    try:
        yield win
    finally:
        # Drain the deferred-warning queue before closing: a modal armed here
        # would otherwise fire inside whatever test pumps the loop next.
        del getattr(win, "_deferred_warnings", [])[:]
        win.close()
        win.deleteLater()
        qapp.processEvents()
        enforcer._tier_slug, enforcer._tier_confirmed = prev
        for name, value in saved.items():
            setattr(enforcer, name, value)


@pytest.fixture
def import_template(monkeypatch, tmp_path):
    """Generate the app's own import template and pin the Open dialog to it."""
    from PyQt6.QtWidgets import QFileDialog

    from scheduler_app.data_io.template import generate_excel_template

    path = os.path.join(str(tmp_path), "scheduler_template.xlsx")
    generate_excel_template(path)
    assert os.path.exists(path), "template generator produced no file"
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    return path


def _teachers_with_day_rules(path):
    """(allow_only_name, exclude_name) — the two template rows that carry days.

    Read out of the workbook rather than hard-coded, so a rename of the example
    staff in ``template.py`` cannot make this module silently assert nothing.
    """
    from scheduler_app.data_io.importer import load_scheduler_data_from_excel

    avail = load_scheduler_data_from_excel(path).state["lecturer_availability"]
    allow_only = [n for n, av in avail.items() if av["allowed_days"]]
    exclude = [n for n, av in avail.items() if av["excluded_days"]]
    assert len(allow_only) == 1 and len(exclude) == 1, (
        "the generated template no longer carries exactly one allowed_days row "
        f"and one excluded_days row: {avail}")
    return allow_only[0], exclude[0]


def _week(window):
    """Give the window the Mon-Fri grid a configured schedule would have."""
    from scheduler_app.i18n.day_keys import DAY_KEYS

    window.state_data["days"] = list(DAY_KEYS[:5])
    window.state_data["slots"] = ["09:00", "10:00", "11:00", "12:00"]


def _available_days(window, lecturer):
    from scheduler_app.core.models import apply_lecturer_availability_filters

    days, _ = apply_lecturer_availability_filters(
        window.state_data, lecturer,
        list(window.state_data["days"]), list(window.state_data["slots"]))
    return days


def test_an_imported_day_constraint_binds_immediately(
        window, message_boxes, import_template):
    """The workbook's day rules must be in force when the import returns.

    Measured on this tree (language pinned to ``tr`` by conftest), straight
    after ``_import_from_excel``:

        'Prof.Emile Laurent': allowed_days=['Pazartesi', 'Çarşamba', 'Cuma']
            -> available days = []            (should be Mon/Wed/Fri)
        'Dr. Min-seo Parkı':  excluded_days=['Cuma']
            -> available days include 'friday' (should not)

    Not an academic point about the field's shape: those two lists are what
    ``lecturer_available_at`` and the CP-SAT model read. An empty day set makes
    every one of that lecturer's classes unplaceable by drag, greedy pass and
    solver alike; an ignored exclusion books the other lecturer on the one day
    they told the app they cannot teach.
    """
    allow_only, exclude = _teachers_with_day_rules(import_template)
    _week(window)

    window._import_from_excel()

    assert message_boxes["information"].calls, "the import did not succeed"
    assert window.state_data["lecturers"], "nothing was imported"

    assert _available_days(window, allow_only) == list(_ALLOW_ONLY), (
        f"{allow_only!r} has availability "
        f"{window.state_data['lecturer_availability'][allow_only]} and the "
        f"engine reads that as available days "
        f"{_available_days(window, allow_only)}")
    assert _EXCLUDE not in _available_days(window, exclude), (
        f"{exclude!r} excluded {_EXCLUDE} in the workbook but the engine still "
        f"offers {_available_days(window, exclude)}")


def test_an_imported_day_constraint_survives_an_import_before_setup(
        window, message_boxes, import_template, qapp):
    """Importing before the week is configured must not erase the day rules.

    The workbook has no days sheet, so the week comes from Setup — and the
    import menu is live whether or not Setup has run. Import first and the
    autosave debounce runs ``normalize_state_day_keys`` against
    ``state["days"] == []``: ``allowed`` is the empty set, so every day is
    pruned out of every availability record and ``_auto_save`` persists the
    emptied roster. Measured after 2.5 s of event loop:

        'Prof.Emile Laurent': allowed_days=[]   (was Pazartesi/Çarşamba/Cuma)
        'Dr. Min-seo Parkı':  excluded_days=[]  (was Cuma)

    The hours survive, which is what makes it so quiet: the Setup lecturer
    table still shows an availability record for both staff, just one that no
    longer restricts a single day. The user configures Mon-Fri, reschedules,
    and a lecturer is booked on the day their own workbook row ruled out.
    """
    from PyQt6.QtTest import QTest

    allow_only, exclude = _teachers_with_day_rules(import_template)
    assert window.state_data["days"] == [], "fixture no longer starts unconfigured"

    window._import_from_excel()
    # Let the 1500 ms autosave debounce fire, exactly as it would while the
    # user reads the import report.
    QTest.qWait(2500)

    # Only now does the user run Setup and lay out the week.
    _week(window)

    assert _available_days(window, allow_only) == list(_ALLOW_ONLY), (
        f"{allow_only!r}'s allowed days were erased by the import: "
        f"{window.state_data['lecturer_availability'].get(allow_only)}")
    assert _EXCLUDE not in _available_days(window, exclude), (
        f"{exclude!r}'s excluded day was erased by the import: "
        f"{window.state_data['lecturer_availability'].get(exclude)}")
