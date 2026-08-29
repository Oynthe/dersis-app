"""Settings-container recovery and autosave failure reporting.

Two findings live at the *application* layer above ``storage.py``, both inside
``scheduler_app/ui/app.py`` (``_auto_load`` / ``_auto_save``, ~1808-1852):

**ST-DATA-014** — ``_auto_save`` is a read-modify-write.  It tries
``storage.load_encrypted(self._config_path)`` and, on *any* exception, falls back
to ``data = {}`` and then writes that dict straight over the user's settings
file.  A container that fails to decrypt — for a permanent reason (bit rot) or a
transient one (a momentarily wrong ``key.bin``, a half-flushed write, a second
instance mid-write, ST-DATA-001/012) — is therefore **destroyed** by the next
autosave, and autosave runs from ``refresh_grid()``, i.e. on essentially every
user action.  The bytes the user would have needed for recovery are gone, and
nothing on screen ever mentioned it.

**ST-DATA-005** — the whole body of ``_auto_save`` is wrapped in
``except Exception: pass``.  If the settings path cannot be written at all
(read-only file, full disk, roaming profile offline, AV lock), every save the
app claims to be doing silently does nothing, for the entire session.

These are **fail-now / pass-after** tests for the Phase 1 fix; deliberately no
``xfail``.  They assert the behaviour the fixed app must have.

Scope split: ``tests/test_storage_roundtrip.py`` owns the *storage* layer
(``load_encrypted`` detects damage and does not mutate the damaged file).  This
module owns the *caller*: what ``SchedulerApp`` does with that failure.  Nothing
here re-asserts a storage-layer contract.

Implementation-agnostic on purpose:

* "the user was told" is satisfied by **any** of a modal ``QMessageBox``, a
  toast, a ``WarningLogPanel`` entry, or a stdlib ``logging`` record at WARNING
  or above.  See ``FeedbackProbe``.  It is deliberately **not** satisfied by the
  exception escaping the call: ``_auto_save`` runs from ``refresh_grid()`` (a Qt
  slot chain) and from ``closeEvent`` (a Qt virtual override), where an escaping
  exception aborts the process rather than informing anyone.
* "the user was told" must also be true **synchronously** — by the time
  ``SchedulerApp.__init__`` / ``_auto_save`` / ``refresh_grid`` returns.  These
  tests never spin the Qt event loop, and that is deliberate, not an oversight:
  ``processEvents()`` also fires the ``QTimer.singleShot(100, self.refresh_grid)``
  that ``__init__`` (app.py) arms whenever a settings file loaded, and
  ``refresh_grid`` writes one ``WarningLogPanel`` entry per unplaced class.
  Measured: adding a ``processEvents()`` flush before the assertions made
  ``..._autosave_write_failure_reaches_the_user`` **pass on the unfixed tree**
  off that noise alone.  So a fix that defers its *entire* report to
  ``QTimer.singleShot(…)`` will fail these tests; it must write at least one
  synchronous channel (warning log, toast, status bar, ``logging``) and may
  defer only the modal.  See the risk note in the implementation plan.
* "the bytes were preserved" is satisfied by the original blob existing
  **anywhere** under ``~/Documents/Dersis`` — ``backups/``, a sibling
  ``.corrupt`` file, or the settings path itself if the fix chooses to refuse
  the write rather than replace the file.

Discrimination — every line below was re-measured on this tree by patching the
production functions at runtime from a pytest plugin (the production tree is
never modified).  3 of the 11 tests pass today and 8 fail.  Against:

* a fix that quarantines the container on *any* read failure → 10 pass and
  ``..._transient_read_failure_...`` still fails, because it destroys a
  perfectly good file on a transient ``OSError``.  Only a fix that separates
  ``EguFileError`` (the storage layer's verdict "this file is damaged") from an
  OS-level read error ("could not read it right now") passes all 11.
* that same fix with the once-per-session guard removed →
  ``..._do_not_open_a_modal_per_refresh`` is the single failure.
* that same fix applied to ``app.py`` only →
  ``..._write_flag_does_not_destroy_...`` is the single failure.
* deleting ``_auto_save``'s ``except Exception: pass`` and adding **nothing**
  else → all 8 of the currently-failing tests still fail.  While this module
  still counted an escaping exception as feedback, that one-line non-fix scored
  9 passed / 1 failed, and aborted the process in ``closeEvent`` on top.
* debouncing autosave out of ``refresh_grid`` (the ST-PERF-002 fix) with
  ``_auto_save`` left destructive → nothing fails that did not already fail.
  ``..._corrupt_settings_survive_...`` therefore calls ``_auto_save`` itself as
  well as ``refresh_grid``; without that line the ST-PERF-002 fix alone would
  have turned it green with ST-DATA-014 fully intact.
"""
import copy
import os
import stat
import sys

import pytest

pytestmark = [pytest.mark.ui]


# ── The container the app persists into ──────────────────────────────────────

# Flags SchedulerApp / FirstRunController own but _auto_save does not write.
# They only survive because of the read-modify-write; the fix must keep them.
FOREIGN_KEYS = {
    "tutorial_seen_or_skipped": True,
    "tutorial_seen": True,
    "initial_setup_prompt_handled": True,
    "language_chosen": True,
}

# An unmistakable marker: if this string is still reachable, the user's saved
# timetable is still on disk.
MARKER_CLASS = "KAYIP-DERS-ÇİZELGESİ-XYZZY"


def _settings_path():
    from scheduler_app import storage
    return storage.settings_path()


def _saved_schedule_state(make_preset):
    """A small, fully-shaped state carrying an identifiable class."""
    from scheduler_app.i18n.day_keys import normalize_state_day_keys
    from scheduler_app.core.models import normalize_state_classes

    state = make_preset("tiny")
    state["classes"][0]["name"] = MARKER_CLASS
    normalize_state_day_keys(state)
    normalize_state_classes(state)
    return state


def _write_good_settings(make_preset, language="tr", last_file=None):
    """Write a valid app_settings.egu holding a real schedule + first-run flags.

    Returns ``(path, payload, raw_bytes)``.
    """
    from scheduler_app import storage

    payload = dict(FOREIGN_KEYS)
    payload["state"] = _saved_schedule_state(make_preset)
    payload["last_file"] = last_file
    payload["language"] = language

    path = _settings_path()
    storage.save_encrypted(payload, path)
    raw = open(path, "rb").read()
    assert raw[:4] == b"EGU1", "fixture did not produce an EGU1 container"
    return path, payload, raw


def _corrupt_in_place(path):
    """Damage the ciphertext of an existing container; return the new bytes.

    The header stays intact so this is exactly what the app sees from a real
    partially-damaged settings file: ``load_encrypted`` raises ``EguFileError``
    from the checksum check, not ``FileNotFoundError``.
    """
    from scheduler_app import storage
    from scheduler_app.storage.storage import EguFileError

    blob = bytearray(open(path, "rb").read())
    # header = magic(4) + version(2) + salt(16) + iv(12) + payload_len(4) = 38
    victim = 38 + (len(blob) - 38 - 32) // 2
    blob[victim] ^= 0xFF
    damaged = bytes(blob)
    with open(path, "wb") as f:
        f.write(damaged)

    # Guard the fixture: the damage must actually be detected, otherwise every
    # assertion below would be vacuous.
    with pytest.raises(EguFileError):
        storage.load_encrypted(path)
    return damaged


def _walk_dersis(root):
    """Yield ``(path, bytes)`` for every file under the Dersis root."""
    for dirpath, _dirnames, filenames in os.walk(str(root)):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                with open(full, "rb") as f:
                    yield full, f.read()
            except OSError:
                continue


def _locate_blob(root, blob):
    """Return every file under *root* whose content is exactly *blob*."""
    return [p for p, data in _walk_dersis(root) if data == blob]


# ── Observable user feedback ─────────────────────────────────────────────────

def _app_log_records(caplog):
    """WARNING-or-worse records emitted *by DERSİS code*.

    Attributed by ``pathname`` rather than logger name so that both
    ``logging.getLogger("scheduler_app.…").warning(…)`` and a bare
    ``logging.warning(…)`` from inside the package count, while noise from
    PyQt / openpyxl / pandas does not masquerade as user feedback.
    """
    return [r for r in caplog.records
            if r.levelno >= 30
            and "scheduler_app" in (r.pathname or "").replace("\\", "/")]


class _Recorder:
    """Stand-in for a modal static; records instead of blocking."""

    def __init__(self, ret):
        self._ret = ret
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._ret

    def texts(self):
        out = []
        for args, kwargs in self.calls:
            out.extend(a for a in args if isinstance(a, str))
            out.extend(v for v in kwargs.values() if isinstance(v, str))
        return out


class FeedbackProbe:
    """Every channel the app could plausibly use to tell the user something.

    Written as a union on purpose: the finding is "the user is never told", so
    the test must not dictate *how* they are told.  ``modal_count`` is exposed
    separately because a modal fired from ``refresh_grid`` would itself be a
    bug (autosave runs on every refresh).

    Two things are deliberately **not** in the union:

    * **A raised exception.**  Every function under test here is called from a
      Qt slot or a Qt virtual override (``refresh_grid`` → ``_auto_save``,
      ``closeEvent`` → ``_auto_save``, ``QTimer`` → ``_write_flag``), where
      PyQt6 aborts the process instead of showing anything.  So the tests call
      those functions bare: an escaping exception fails the test outright.
      Counting it as feedback would let "delete the ``except Exception: pass``
      and add nothing" pass almost the whole module: measured against the
      pre-repair version of this file, that non-fix scored 9 passed / 1 failed.
    * **Any signal that a healthy app would have produced anyway.**  This one
      cannot be filtered here, because ``WarningLogPanel.log`` is also how
      ``_refresh_warnings`` reports *scheduling* problems — measured at one
      entry per unplaced class, so a 5-class state yields 5 entries per
      ``refresh_grid()`` with nothing wrong at all.  Any test that combines a
      ``refresh_grid()`` with a ``channels()`` assertion must therefore first
      make sure the state carries no unplaced classes; the two that do carry an
      explicit tripwire saying so.
    """

    def __init__(self):
        self.modals = {}          # name -> _Recorder
        self.toasts = []          # (message, kind)
        self.log_entries = []     # (message, kind) written to WarningLogPanel

    # -- queries -------------------------------------------------------------

    @property
    def modal_count(self):
        return sum(len(r.calls) for name, r in self.modals.items()
                   if name in ("information", "warning", "critical", "question",
                               "exec"))

    def channels(self, caplog):
        """Names of every channel that carried something."""
        hit = []
        for name, rec in self.modals.items():
            if rec.calls:
                hit.append(f"QMessageBox.{name}")
        if self.toasts:
            hit.append("toast")
        if self.log_entries:
            hit.append("warning_log")
        if _app_log_records(caplog):
            hit.append("logging")
        return hit

    def describe(self, caplog):
        return (f"channels={self.channels(caplog)} "
                f"modal_texts={[t for r in self.modals.values() for t in r.texts()]} "
                f"toasts={self.toasts} log={self.log_entries}")


@pytest.fixture
def feedback(monkeypatch, caplog):
    """Neutralize every modal and record every user-facing channel.

    An unpatched modal blocks the whole suite under the offscreen platform, so
    the patching is a hard requirement, not a convenience.
    """
    from PyQt6.QtWidgets import QMessageBox

    from scheduler_app.ui.app import SchedulerApp
    from scheduler_app.ui.widgets import WarningLogPanel

    probe = FeedbackProbe()
    for name, ret in (("information", QMessageBox.StandardButton.Ok),
                      ("warning", QMessageBox.StandardButton.Ok),
                      ("critical", QMessageBox.StandardButton.Ok),
                      ("question", QMessageBox.StandardButton.Yes)):
        rec = _Recorder(ret)
        probe.modals[name] = rec
        monkeypatch.setattr(QMessageBox, name, staticmethod(rec))
    # A hand-built QMessageBox().exec() would hang offscreen; count it too.
    exec_rec = _Recorder(QMessageBox.StandardButton.Ok)
    probe.modals["exec"] = exec_rec
    monkeypatch.setattr(QMessageBox, "exec", exec_rec)

    # Toasts are recorded, not constructed: a real Toast starts a 3 s QTimer
    # that would outlive the window the test tears down.
    monkeypatch.setattr(
        SchedulerApp, "_show_toast",
        lambda self, message, kind="info": probe.toasts.append((message, kind)))
    real_log = WarningLogPanel.log

    def spy_log(self, message, kind="info"):
        probe.log_entries.append((message, kind))
        return real_log(self, message, kind)

    monkeypatch.setattr(WarningLogPanel, "log", spy_log)

    caplog.set_level(0)
    return probe


# ── Window construction ──────────────────────────────────────────────────────

_TIER_REGISTRIES = (
    "_gated_widgets", "_gated_actions", "_on_tier_changed",
    "_export_submenu_refreshers",
)


@pytest.fixture
def make_window(qapp, dersis_home, monkeypatch):
    """Factory for a real, never-shown ``SchedulerApp``.

    A factory rather than a ready-made window because every test here has to
    arrange the on-disk settings container *before* ``__init__`` runs
    ``_auto_load``.

    Isolation notes (same reasoning as ``tests/test_import_ui_flow.py``):
    the first-run controller is disabled so its QTimer-driven ``_write_flag``
    calls cannot rewrite the settings file behind the test's back, and the
    process-wide ``TierEnforcement`` registries are snapshotted because
    ``SchedulerApp`` registers into them and never unregisters.
    """
    from scheduler_app.plans import TIER_INSTITUTIONAL
    from scheduler_app.i18n.day_keys import DAY_KEYS
    from scheduler_app.ui.first_run import FirstRunController
    from scheduler_app.ui.tier_enforcement import TierEnforcement

    monkeypatch.setattr(FirstRunController, "start", lambda self: None)

    enforcer = TierEnforcement.instance()
    prev_slug, prev_confirmed = enforcer._tier_slug, enforcer._tier_confirmed
    prev_registries = {
        name: list(getattr(enforcer, name))
        for name in _TIER_REGISTRIES if hasattr(enforcer, name)
    }
    enforcer._tier_slug, enforcer._tier_confirmed = TIER_INSTITUTIONAL, True

    from scheduler_app.ui.app import SchedulerApp

    built = []

    def _make(with_grid=True):
        win = SchedulerApp()
        if with_grid and not win.state_data.get("days"):
            # A brand-new profile has days == slots == [], i.e. a 0x0 grid, so
            # refresh_grid() would barely touch anything. Give the window the
            # grid a real user would have (the first-run wizard normally does).
            win.state_data["days"] = list(DAY_KEYS[:5])
            win.state_data["slots"] = ["09:00", "10:00", "11:00", "12:00"]
        built.append(win)
        return win

    try:
        yield _make
    finally:
        for win in built:
            win.close()
            win.deleteLater()
        qapp.processEvents()
        enforcer._tier_slug, enforcer._tier_confirmed = prev_slug, prev_confirmed
        for name, value in prev_registries.items():
            setattr(enforcer, name, value)


@pytest.fixture(autouse=True)
def _restore_locale(qapp):
    """Undo any language/direction change a settings file causes.

    ``_auto_load`` calls ``set_language(data["language"])`` and can flip the
    QApplication layout direction. Both are process-global and the suite's
    language pin is session-scoped, so without this every later module would
    inherit whatever this one loaded.
    """
    from scheduler_app.translations import get_language, set_language

    lang = get_language()
    direction = qapp.layoutDirection()
    try:
        yield
    finally:
        set_language(lang)
        qapp.setLayoutDirection(direction)
        assert qapp.layoutDirection() == direction
        assert get_language() == lang


# ═════════════════════════════════════════════════════════════════════════════
#  1. Guards — behaviour that is correct today and must survive the fix
# ═════════════════════════════════════════════════════════════════════════════

def test_autosave_roundtrips_state_last_file_and_language(
        make_window, feedback, make_preset, caplog):
    """Guard for the ST-DATA-014 fix — a normal autosave must still persist the
    schedule, the last-opened file and the UI language.

    A failure means the hardening broke the everyday path: the user's work stops
    being saved between sessions at all.
    """
    from scheduler_app import storage
    from scheduler_app.core.models import normalize_state_classes
    from scheduler_app.i18n.day_keys import normalize_state_day_keys

    win = make_window()
    win.state_data = _saved_schedule_state(make_preset)
    win.current_file = os.path.join(str(storage.sub_dir(storage.SAVES_DIR)),
                                    "2025-guz.egu")

    expected = copy.deepcopy(win.state_data)
    normalize_state_day_keys(expected)
    normalize_state_classes(expected)

    win._auto_save()

    data = storage.load_encrypted(_settings_path())
    assert data["state"] == expected
    assert data["last_file"] == win.current_file
    assert data["language"] == "tr"
    # And it must come back through the app's own reader, not just storage's.
    reloaded = make_window(with_grid=False)
    assert reloaded.state_data == expected
    assert reloaded.current_file == win.current_file


def test_absent_settings_file_on_first_run_is_not_an_error(
        make_window, feedback, dersis_home, caplog):
    """Guard for the ST-DATA-005/014 fix — a first run with no settings file must
    be silent, not reported as damage.

    A failure means every brand-new install greets the user with a corruption
    warning about a file that was simply never written yet.
    """
    path = _settings_path()
    assert not os.path.exists(path), "fixture leaked a settings file"

    win = make_window()

    assert feedback.channels(caplog) == [], (
        "a missing settings file was reported to the user as a problem: "
        + feedback.describe(caplog))

    # ...and the first autosave must create it, silently.
    win._auto_save()
    assert feedback.channels(caplog) == [], (
        "the first autosave complained: " + feedback.describe(caplog))
    assert os.path.exists(path), "first autosave did not create the settings file"


def test_autosave_preserves_keys_it_does_not_own(
        make_window, feedback, make_preset, caplog):
    """Guard for the ST-DATA-014 fix — autosave must not drop the first-run flags.

    The read-modify-write exists precisely to keep them. A failure means the
    tutorial and the language picker re-fire on every launch, because the flags
    saying "already handled" are wiped by the next grid refresh.
    """
    from scheduler_app import storage

    _write_good_settings(make_preset)

    win = make_window()
    win._auto_save()

    data = storage.load_encrypted(_settings_path())
    for key, value in FOREIGN_KEYS.items():
        assert data.get(key) == value, f"autosave dropped {key!r}"


# ═════════════════════════════════════════════════════════════════════════════
#  2. ST-DATA-014 — a corrupt container must be preserved and reported
# ═════════════════════════════════════════════════════════════════════════════

def test_corrupt_settings_survive_startup_and_autosave(
        make_window, feedback, make_preset, dersis_home, caplog):
    """ST-DATA-014 — a corrupt app_settings.egu must still exist, byte-for-byte,
    after the app has started and autosaved over it.

    A failure means the user's only copy of their timetable was overwritten by
    an empty one on the first grid refresh, so a recoverable problem (a wrong
    key.bin, a half-written file) became permanent data loss with no warning.
    """
    _write_good_settings(make_preset)
    damaged = _corrupt_in_place(_settings_path())

    win = make_window()

    # refresh_grid() is the real-world trigger: it autosaves on every call.
    win.refresh_grid()
    # ...and _auto_save() is driven directly as well, because ST-PERF-002's fix
    # debounces autosave *out of* refresh_grid. Verified: with that change alone
    # and _auto_save left destructive, the refresh_grid line stops touching the
    # file and this test goes green with ST-DATA-014 completely unfixed.
    win._auto_save()

    found = _locate_blob(dersis_home, damaged)
    assert found, (
        "the corrupt settings container was overwritten and no copy of its "
        f"bytes survives anywhere under {dersis_home}; the user's saved "
        "schedule is now unrecoverable")


def test_corrupt_settings_are_reported_to_the_user(
        make_window, feedback, make_preset, caplog):
    """ST-DATA-014 — the app must tell the user their settings file is damaged.

    A failure means the app opens on an empty timetable, quietly, and the user
    is left to conclude the program lost their work for no reason.
    """
    _write_good_settings(make_preset)
    _corrupt_in_place(_settings_path())

    win = make_window()

    # Tripwire against a wrong-reason pass: channels() counts WarningLogPanel
    # entries, and refresh_grid() writes one per *unplaced class* all on its own
    # (app.py::_run_auto_negotiation). With nothing loaded there are
    # no classes, so any channel that fires can only be about the settings file.
    # If a future fix restores the schedule from a backup instead, this assert
    # fires and whoever changed it must re-derive the signal check rather than
    # inherit a vacuous one.
    assert not win.state_data.get("classes"), (
        "a corrupt container left classes in state; refresh_grid's own "
        "scheduling warnings would satisfy channels() for the wrong reason")

    win.refresh_grid()
    win._auto_save()

    assert feedback.channels(caplog), (
        "a corrupt settings container produced no user-visible signal at all: "
        + feedback.describe(caplog))


def test_transient_read_failure_does_not_rebuild_settings_from_scratch(
        make_window, feedback, make_preset, monkeypatch, caplog):
    """ST-DATA-014 — a one-off read failure must not turn autosave into a wipe.

    This is the destructive read-modify-write in its purest form: the file on
    disk is perfectly good, one read fails, and today ``_auto_save`` falls back
    to ``data = {}`` and writes that over it. A failure means a momentary hiccup
    silently deletes every key the app did not happen to be writing, so the
    tutorial and the language picker re-fire on the next launch.

    The injected failure is an ``OSError``, deliberately **not** an
    ``EguFileError``: an AV scanner holding the file, a share that blinked, a
    second instance mid-``os.replace`` (ST-DATA-012). Only ``EguFileError`` is
    the storage layer's *verdict* that the container is genuinely damaged;
    anything else means "could not read it right now", and the one thing the app
    must never do with that is overwrite the file it just failed to read.
    """
    from scheduler_app import storage
    import scheduler_app.storage.storage as storage_mod

    _write_good_settings(make_preset)
    win = make_window()

    real_load = storage.load_encrypted
    state = {"failed": False}

    def flaky_load(path, *args, **kwargs):
        if not state["failed"] and os.path.abspath(path) == os.path.abspath(_settings_path()):
            state["failed"] = True
            raise OSError("simulated transient read failure")
        return real_load(path, *args, **kwargs)

    monkeypatch.setattr(storage, "load_encrypted", flaky_load)
    monkeypatch.setattr(storage_mod, "load_encrypted", flaky_load)

    win._auto_save()

    assert state["failed"], "the injected read failure never fired"
    # Read back through the *unpatched* function. ``monkeypatch.undo()`` is not
    # used: this test shares one monkeypatch instance with the ``feedback``
    # fixture, so undoing here would also un-neutralize every modal.
    data = real_load(_settings_path())
    missing = [k for k in FOREIGN_KEYS if k not in data]
    assert not missing, (
        f"one failed read wiped {missing} out of the settings container; "
        "_auto_save rebuilt it from {} and overwrote the original")


def test_write_flag_does_not_destroy_a_corrupt_settings_container(
        feedback, make_preset, dersis_home, caplog):
    """ST-DATA-014 at the second call site — ``first_run._write_flag`` must not
    clobber a damaged settings file either.

    ``_write_flag`` has the identical read-modify-write-and-swallow shape as
    ``_auto_save``, and on a container that fails to load every first-run flag
    reads as unset, so ``run_language_gate`` (called by ``scheduler_gui.main``,
    which runs it *before* ``SchedulerApp()``) and ``FirstRunController``
    both call it on the way in.  A fix confined to ``_auto_save`` therefore
    still loses the file. A failure means the corrupt container is destroyed
    before the user ever clicks anything.
    """
    from scheduler_app.ui.first_run import _write_flag

    _write_good_settings(make_preset)
    damaged = _corrupt_in_place(_settings_path())

    # Bare call: _write_flag is invoked from QTimer callbacks, so raising is a
    # crash rather than a report.
    _write_flag(_settings_path(), "initial_setup_prompt_handled", True)

    found = _locate_blob(dersis_home, damaged)
    assert found, (
        "_write_flag overwrote the corrupt settings container and no copy of "
        f"its bytes survives anywhere under {dersis_home}")


# ═════════════════════════════════════════════════════════════════════════════
#  3. ST-DATA-005 — a failing write must reach the user
# ═════════════════════════════════════════════════════════════════════════════

def test_autosave_write_failure_reaches_the_user(
        make_window, feedback, make_preset, monkeypatch, caplog):
    """ST-DATA-005 — when the settings file cannot be written, the user must
    find out.

    A failure means the app reports nothing while every autosave for the rest of
    the session is a no-op; the user closes DERSİS believing a whole afternoon
    of timetabling was saved.
    """
    from scheduler_app import storage
    import scheduler_app.storage.storage as storage_mod

    _write_good_settings(make_preset)
    win = make_window()

    def boom(data, path, *args, **kwargs):
        raise OSError("simulated read-only settings path")

    monkeypatch.setattr(storage, "save_encrypted", boom)
    monkeypatch.setattr(storage_mod, "save_encrypted", boom)

    win._auto_save()

    assert feedback.channels(caplog), (
        "autosave failed and swallowed it: " + feedback.describe(caplog))


def test_a_failing_autosave_never_crashes_refresh_or_close(
        make_window, feedback, make_preset, monkeypatch, caplog):
    """ST-DATA-005 — the failure must be *reported*, not merely *raised*.

    ``_auto_save`` is called from ``refresh_grid()`` and from
    ``closeEvent`` (both in app.py). Both are Qt entry points where an escaping
    exception aborts the process under a real platform plugin, so the cheapest
    conceivable fix — deleting ``except Exception: pass`` and adding nothing —
    is not a fix at all. Measured against the pre-repair version of this file,
    that one-line change scored 9 passed / 1 failed while aborting the process
    in ``closeEvent``.

    A failure means either the user still gets no warning, or DERSİS dies on a
    mouse click the moment the settings path becomes unwritable.
    """
    from PyQt6.QtGui import QCloseEvent

    from scheduler_app import storage
    import scheduler_app.storage.storage as storage_mod

    _write_good_settings(make_preset)
    win = make_window()
    # Same tripwire as the corrupt-report test, applied by construction:
    # _refresh_warnings logs one warning-log entry per unplaced class, which
    # would satisfy channels() with nothing at all wrong.
    win.state_data["classes"] = []

    def boom(data, path, *args, **kwargs):
        raise OSError("simulated read-only settings path")

    monkeypatch.setattr(storage, "save_encrypted", boom)
    monkeypatch.setattr(storage_mod, "save_encrypted", boom)

    # Every one of these is a bare call: an escaping exception fails the test.
    win.refresh_grid()              # calls _auto_save
    win._auto_save()                # also driven directly, so that debouncing
                                    # autosave out of refresh_grid (ST-PERF-002)
                                    # cannot turn the assertion below vacuous
    win.closeEvent(QCloseEvent())   # calls _auto_save

    assert feedback.channels(caplog), (
        "a failing autosave told the user nothing: " + feedback.describe(caplog))


def test_autosave_failure_on_a_genuinely_unwritable_file_reaches_the_user(
        make_window, feedback, make_preset, caplog):
    """ST-DATA-005 without any monkeypatching — a real read-only settings file
    must be reported.

    The companion test injects the failure; this one produces it from the OS, so
    a fix that only handles a hand-rolled exception type cannot pass both.
    A failure means the same silent data loss on the most common real cause:
    a settings file the current user no longer has write permission on.
    """
    path, _payload, _raw = _write_good_settings(make_preset)
    win = make_window()

    if not _make_unwritable(path):
        pytest.skip("this platform does not enforce the read-only bit here")
    try:
        win._auto_save()
        assert feedback.channels(caplog), (
            "a read-only settings file was silently ignored: "
            + feedback.describe(caplog))
    finally:
        _make_writable(path)


def test_repeated_autosave_failures_do_not_open_a_modal_per_refresh(
        make_window, feedback, make_preset, monkeypatch, caplog):
    """ST-DATA-005 — reporting the failure must not become its own bug.

    ``_auto_save`` runs from ``refresh_grid()``, which fires on every selection,
    drag, add and delete. A failure here means a user whose disk is full has to
    dismiss a modal dialog after literally every click, which is worse than the
    silence it replaced. One report per failure burst is enough; the other
    channels (toast, warning log, logging) are deliberately left unconstrained.
    """
    from scheduler_app import storage
    import scheduler_app.storage.storage as storage_mod

    _write_good_settings(make_preset)
    win = make_window()

    def boom(data, path, *args, **kwargs):
        raise OSError("simulated read-only settings path")

    monkeypatch.setattr(storage, "save_encrypted", boom)
    monkeypatch.setattr(storage_mod, "save_encrypted", boom)

    for _ in range(10):
        win._auto_save()

    assert feedback.channels(caplog), (
        "10 consecutive failed autosaves produced no signal at all: "
        + feedback.describe(caplog))
    assert feedback.modal_count <= 1, (
        f"{feedback.modal_count} modal dialogs for 10 failed autosaves; "
        "autosave fires on every grid refresh, so this must be rate-limited")


# ═════════════════════════════════════════════════════════════════════════════
#  ST-DATA-002 — a damaged feedback log must reach the user too
# ═════════════════════════════════════════════════════════════════════════════

def _damage_feedback_log(n_entries=6):
    """Write an EGL1 feedback log and flip one ciphertext bit in every record.

    The framing is left intact on purpose: ``log_entry_count`` walks the length
    prefixes without decrypting, so it still reports *n_entries* and
    ``PreferenceLearner``'s ``MIN_ENTRIES_TO_LEARN`` gate is still cleared —
    otherwise ``learn()`` would return before reaching the code under test and
    the assertions here would pass for the wrong reason.
    """
    import struct

    from scheduler_app import storage

    path = storage.feedback_log_path()
    storage.save_encrypted_lines(
        [{"event": "manual_move", "n": i} for i in range(n_entries)], path)
    blob = bytearray(open(path, "rb").read())
    assert bytes(blob[:4]) == storage.storage._LOG_MAGIC, (
        "the fixture did not produce an EGL1 log (%r)" % (bytes(blob[:4]),))
    off = struct.calcsize(storage.storage._LOG_HEADER_FMT)
    wrecked = 0
    while off + 4 <= len(blob):
        (rec_len,) = struct.unpack_from(
            storage.storage._LOG_RECLEN_FMT, blob, off)
        if rec_len <= 0 or off + 4 + rec_len > len(blob):
            break
        blob[off + 4 + 43] ^= 0x01
        wrecked += 1
        off += 4 + rec_len
    with open(path, "wb") as f:
        f.write(bytes(blob))

    assert wrecked == n_entries, "the fixture damaged %d of %d records" % (
        wrecked, n_entries)
    assert storage.log_entry_count(path) == n_entries, (
        "the damage broke the framing, so the learner's entry-count gate would "
        "reject the log before it ever tried to read it")
    assert storage.load_encrypted_lines(path) == [], (
        "the fixture left readable records; the branch under test is the one "
        "where nothing decrypts")
    return path


def _distinctive_text(key):
    """The longest placeholder-free run of a translation's own text.

    Matching on the *key's own* text rather than a hardcoded sentence, so a
    reworded message does not fail these tests but reporting the wrong message
    does.

    Deliberately the longest run between placeholders and NOT "everything
    before the first ``{``". That simpler version was written first and was
    **vacuous in Turkish**, which is the language this suite pins: the Turkish
    ``errors.feedback_log_damaged`` opens with ``{path}``, so the stem was the
    empty string and ``"" in anything`` is True. Measured — it made both tests
    below pass with the report deleted from the app entirely. The assertion
    here is what stops that from coming back.
    """
    import re

    from scheduler_app.translations import tr

    parts = [p.strip() for p in re.split(r"\{[^}]*\}", tr(key))]
    longest = max(parts, key=len) if parts else ""
    assert len(longest) >= 20, (
        "no placeholder-free run of %r is long enough to identify it (%r); "
        "matching on it would pass for the wrong reason" % (key, longest))
    return longest


def _all_user_text(feedback, caplog):
    """Every string that reached a user-visible channel, from all of them."""
    out = [m for m, _kind in feedback.toasts]
    out += [m for m, _kind in feedback.log_entries]
    out += [t for r in feedback.modals.values() for t in r.texts()]
    out += [r.getMessage() for r in _app_log_records(caplog)]
    return out


def test_a_damaged_feedback_log_reaches_the_user(make_window, feedback, caplog):
    """ST-DATA-002: the user must be told their history stopped being readable.

    A failure means DERSİS quietly stops learning from everything the user has
    ever corrected and the user never finds out — which is the finding, not the
    storage-layer return value. The storage half is guarded in
    ``tests/test_storage_roundtrip.py``; this is the half that reaches a person.
    """
    _damage_feedback_log()

    win = make_window(with_grid=False)

    # The module's own tripwire against a wrong-reason pass: channels() counts
    # WarningLogPanel entries, and refresh_grid() writes one per *unplaced
    # class* all on its own. With nothing loaded there are no classes, so any
    # channel that fires can only be about the feedback log.
    assert not win.state_data.get("classes"), (
        "classes in state would let refresh_grid's own scheduling warnings "
        "satisfy channels() for the wrong reason")

    assert feedback.channels(caplog), (
        "a damaged feedback log produced no user-visible signal at all: "
        + feedback.describe(caplog))
    assert any(_distinctive_text("errors.feedback_log_damaged") in t
               for t in _all_user_text(feedback, caplog)), (
        "something was reported, but not that the feedback history is "
        "unreadable: " + feedback.describe(caplog))


def test_a_damaged_feedback_log_and_corrupt_settings_both_reach_the_user(
        make_window, feedback, make_preset, caplog):
    """ST-DATA-002: the two startup reports must not overwrite each other.

    This is the test that pins the *placement* of the report, and nothing else
    does. ``_pending_settings_report`` is a SINGLE slot, and the learner runs
    from ``__init__`` before ``_auto_load()`` — so reporting the damaged log
    from the learn() call site (the obvious place) stashes a message that the
    settings report then silently overwrites. Emitting it from
    ``_flush_startup_settings_report`` instead, after ``_build_status()`` has
    created ``status_label``, makes ``_report_settings_problem`` take its
    immediate non-stashing branch and both messages land.

    A failure means a user with two damaged files is told about one of them.
    """
    _write_good_settings(make_preset)
    _corrupt_in_place(_settings_path())
    _damage_feedback_log()

    win = make_window(with_grid=False)

    assert not win.state_data.get("classes"), (
        "a corrupt container left classes in state; refresh_grid's own "
        "scheduling warnings would satisfy channels() for the wrong reason")

    texts = _all_user_text(feedback, caplog)
    assert any(_distinctive_text("errors.feedback_log_damaged") in t for t in texts), (
        "the damaged feedback log was not reported when the settings "
        "container was damaged too — the single-slot overwrite: "
        + feedback.describe(caplog))
    assert any(_distinctive_text("errors.settings_corrupt") in t for t in texts), (
        "the corrupt settings container stopped being reported once the "
        "feedback-log report was added: " + feedback.describe(caplog))


# ── read-only helpers ────────────────────────────────────────────────────────

def _make_unwritable(path):
    """Best-effort read-only. Returns True only if the OS actually enforces it.

    ``save_encrypted`` writes ``path + '.tmp'`` and then ``os.replace``s it over
    ``path``, so two different permissions can block it and they differ by
    platform.  On Windows the read-only bit on the *file* is enough (``os.replace``
    onto a read-only destination raises ``PermissionError``); on POSIX it is not —
    only the *directory* mode matters — so the parent is locked down too.  The
    probe below is deliberately non-destructive: it never writes through to
    ``path``, so a permissive environment (a POSIX CI container running as root)
    skips cleanly instead of silently shredding the fixture.
    """
    os.chmod(path, stat.S_IREAD)
    if sys.platform != "win32":
        os.chmod(os.path.dirname(path), 0o500)

    probe = path + ".probe"
    try:
        with open(probe, "wb") as f:
            f.write(b"x")
    except OSError:
        return True          # cannot even create the .tmp sibling
    os.remove(probe)

    # The sibling was creatable, so only the destination can stop the replace.
    # That is a Windows-only guarantee; on POSIX a writable dir wins.
    if sys.platform != "win32":
        return False
    try:
        open(path, "ab").close()
    except OSError:
        return True
    return False             # not enforced here (elevated process?)


def _make_writable(path):
    if sys.platform != "win32":
        os.chmod(os.path.dirname(path), 0o700)
    if os.path.exists(path):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
