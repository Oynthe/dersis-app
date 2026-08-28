"""ST-FUNC-006 / ST-FUNC-013 against the CSV writer a user actually reaches.

``tests/test_export_smoke.py`` covers ``export_schedule(state, "csv", ...)``.
Measured in Phase 7, that function has **no production caller**: the File menu
is wired at ``ui/app.py:1000`` to ``SchedulerApp.export_csv``
(``ui/app.py:2685``), a second writer with its own ``csv.writer``. So the
suite was proving CSV correctness -- encoding included -- in a function no user
can reach, which is ST-ARCH-003 repeating one format later.

Both halves of ST-FUNC-006 lived only in the live writer:

* ``open(fname, "w", newline="")`` with no ``encoding=``, so the file was
  written in the host codepage. Measured on the audit machine:
  ``locale.getpreferredencoding(False) == "cp1254"``. On a cp1252 host
  ``"Işık Öğretmen".encode(...)`` raises ``UnicodeEncodeError``, which the
  writer's bare ``except Exception`` turns into an unexplained "export failed".
* ``day = effective_day(c)``, the raw internal key ``"monday"``, written into a
  file whose header row is Turkish.

And the same function carried an instance of ST-FUNC-013: it iterated
``c["targets"]`` directly, so a lesson placed before its class groups were
assigned emitted no row at all.

Every state here is hand-built and fully placed; the optimizer is never
invoked.
"""
import codecs
import csv

import pytest

pytestmark = pytest.mark.ui

LECTURER = "Şükrü Işık Öğretmen"
YEAR = "1. Sınıf"
ROOM = "A-101"
# Both of these carry letters that are unrepresentable in cp1252, which is what
# a colleague on a non-Turkish Windows would have been writing the file in.
LESSON = "İş Sağlığı ve Güvenliği"
GROUPLESS_LESSON = "Serbest Etüt"


# ── Helpers ─────────────────────────────────────────────────────────────────

class _Recorder:
    """Stand-in for a modal QMessageBox static; records instead of blocking."""

    def __init__(self, ret):
        self._ret = ret
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._ret

    @property
    def called(self):
        return bool(self.calls)

    def texts(self):
        out = []
        for args, kwargs in self.calls:
            out.extend(a for a in args if isinstance(a, str))
            out.extend(v for v in kwargs.values() if isinstance(v, str))
        return out


def _turkish_state(with_groupless=False):
    """A placed, Turkish-named timetable -- the thing a school exports."""
    from scheduler_app.core.models import mark_placed, new_class, new_state

    state = new_state()
    state["days"] = ["monday", "tuesday"]
    state["slots"] = ["09:00", "10:00"]
    state["classrooms"] = [ROOM]
    state["classroom_capacities"] = {ROOM: 30}
    state["lecturers"] = [LECTURER]
    state["years"] = {YEAR: ["A"]}

    def place(code, name, day, slot, targets):
        cls = new_class()
        cls["class_code"] = code
        cls["name"] = name
        cls["lecturer"] = LECTURER
        cls["targets"] = targets
        cls["duration"] = 1
        cls["participants"] = 20
        mark_placed(cls, day, slot, ROOM)
        state["classes"].append(cls)

    place("D001", LESSON, "monday", "09:00", [{"year": YEAR, "branch": "A"}])
    if with_groupless:
        # new_class() initializes targets to [] (core/models.py:578) and
        # neither class-editor path requires one, so this is the default state
        # of every lesson placed before its groups were ticked.
        place("D900", GROUPLESS_LESSON, "tuesday", "10:00", [])
    return state


@pytest.fixture
def message_boxes(monkeypatch):
    """Neutralize every modal ``export_csv`` can raise.

    ``critical`` is recorded rather than discarded on purpose: the writer wraps
    itself in a bare ``except Exception``, so without this a test could only
    tell a successful export from a swallowed crash by the file's absence.
    """
    from PyQt6.QtWidgets import QMessageBox

    recorders = {
        "information": _Recorder(QMessageBox.StandardButton.Ok),
        "warning": _Recorder(QMessageBox.StandardButton.Ok),
        "critical": _Recorder(QMessageBox.StandardButton.Ok),
        "question": _Recorder(QMessageBox.StandardButton.Yes),
    }
    for name, rec in recorders.items():
        monkeypatch.setattr(QMessageBox, name, staticmethod(rec))
    return recorders


@pytest.fixture
def export_to(monkeypatch):
    """Return a callable pinning ``QFileDialog.getSaveFileName`` to one path."""
    from PyQt6.QtWidgets import QFileDialog

    def _pin(path):
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(path), "")),
        )

    return _pin


@pytest.fixture
def window(make_app):
    """A ``SchedulerApp`` with CSV export unlocked.

    ``FEATURE_EXPORT_CSV`` is False on the Free tier, so an unpinned tier makes
    ``export_csv`` return at its first line and every assertion below would be
    testing nothing. The tier is set by direct assignment rather than
    ``set_tier()`` because ``set_tier()`` sweeps every registered gate,
    including ones belonging to windows other tests already destroyed.
    """
    from scheduler_app.plans import TIER_INSTITUTIONAL
    from scheduler_app.ui.tier_enforcement import TierEnforcement

    enforcer = TierEnforcement.instance()
    previous = (enforcer._tier_slug, enforcer._tier_confirmed)
    enforcer._tier_slug, enforcer._tier_confirmed = TIER_INSTITUTIONAL, True
    try:
        yield make_app()
    finally:
        enforcer._tier_slug, enforcer._tier_confirmed = previous


def _run_export(window, state, path, message_boxes):
    """Drive the real menu action end to end and return the written bytes."""
    window.state_data.clear()
    window.state_data.update(state)

    window.export_csv()

    assert not message_boxes["critical"].called, (
        "the export reported a failure: "
        f"{message_boxes['critical'].texts()}"
    )
    assert message_boxes["information"].called, \
        "the export neither failed nor reported success -- it did nothing"
    assert path.exists(), "export_csv wrote no file"
    return path.read_bytes()


# ── ST-FUNC-006 ─────────────────────────────────────────────────────────────

def test_live_csv_export_is_utf8_and_localizes_the_day(
        window, export_to, message_boxes, tmp_path):
    """ST-FUNC-006 — the exported CSV must be UTF-8 and speak Turkish throughout.

    A failure means the file a school emails to a colleague is written in the
    exporting machine's codepage -- unreadable, or on a cp1252 host not written
    at all -- and its day column reads "monday" under a header that says "Gün".

    Both halves are asserted here because both were produced by the same two
    lines, and because a fix to either one alone still leaves the file broken
    for the recipient.
    """
    out = tmp_path / "timetable.csv"
    export_to(out)

    raw = _run_export(window, _turkish_state(), out, message_boxes)

    # Strict decode: host-codepage bytes for "ı" (0xFD in cp1254) are not
    # valid UTF-8 and would raise here.
    text = raw.decode("utf-8-sig")
    assert LESSON in text, f"{LESSON!r} did not survive the export"
    assert LECTURER in text, f"{LECTURER!r} did not survive the export"

    # The BOM is what stops Excel from re-reading a UTF-8 CSV in the local
    # codepage, which is the whole reason a Turkish school notices at all.
    assert raw.startswith(codecs.BOM_UTF8), \
        "no UTF-8 BOM: Excel will open this in the recipient's codepage"

    rows = list(csv.reader(text.splitlines()))
    header, data = rows[0], rows[1:]
    assert data, "the export produced a header and nothing else"

    from scheduler_app.translations import tr
    day_col = header.index(tr("labels.day"))
    days_seen = {r[day_col] for r in data}
    localized = {tr("weekdays.monday"), tr("weekdays.tuesday")}
    assert days_seen <= localized, (
        f"the day column leaked internal keys: {sorted(days_seen - localized)}"
        f" (expected e.g. {tr('weekdays.monday')!r} under the header "
        f"{header[day_col]!r})"
    )


# ── ST-FUNC-013 ─────────────────────────────────────────────────────────────

def test_live_csv_export_keeps_a_lesson_with_no_target_groups(
        window, export_to, message_boxes, tmp_path):
    """ST-FUNC-013 — a lesson with no class group must still reach the CSV.

    A failure means a lesson the user placed but has not yet ticked a
    year/branch for is simply absent from the file, with nothing saying so.
    ``new_class()`` sets ``targets`` to ``[]`` and no editor requires
    otherwise, so this is reachable from the default state of every class.
    """
    out = tmp_path / "groupless.csv"
    export_to(out)

    raw = _run_export(window, _turkish_state(with_groupless=True), out,
                      message_boxes)
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    names = {r[0] for r in rows[1:]}

    assert LESSON in names, "the control lesson is missing -- the test is broken"
    assert GROUPLESS_LESSON in names, (
        f"the group-less lesson was dropped from the CSV; it holds only "
        f"{sorted(names)}"
    )
