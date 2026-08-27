"""Global pytest configuration for the DERSİS regression suite.

**CRITICAL ORDERING.** ``scheduler_app.storage`` binds ``~/Documents/Dersis``
into a module-level constant at *import* time. Every test therefore has to run
with ``HOME``/``USERPROFILE`` already pointing somewhere disposable. pytest
imports the root ``conftest.py`` before it collects any test module, so the
redirection below is the earliest safe hook. Nothing in this file may import
``scheduler_app`` at module scope.

Fixtures
--------
``dersis_home``   fresh per-test ``Documents/Dersis`` tree; storage is rebound
                  to it and the cached master key is cleared on both sides.
``qapp``          session-wide ``QApplication`` (offscreen), language pre-seeded.
``make_state`` / ``make_preset``  deterministic dataset builders.
"""
import os
import sys
import tempfile

# ── 1. Sandbox HOME before ANY scheduler_app import ──────────────────────────
_SESSION_HOME = tempfile.mkdtemp(prefix="dersis_pytest_")
os.environ["HOME"] = _SESSION_HOME
os.environ["USERPROFILE"] = _SESSION_HOME
# os.path.expanduser() on Windows also consults HOMEDRIVE + HOMEPATH.
_drive, _tail = os.path.splitdrive(_SESSION_HOME)
os.environ["HOMEDRIVE"] = _drive
os.environ["HOMEPATH"] = _tail
os.makedirs(os.path.join(_SESSION_HOME, "Documents"), exist_ok=True)

# ── 2. Headless Qt unless the caller asked for something else ────────────────
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ── 3. Repo root + tests/ importable ─────────────────────────────────────────
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
for _p in (_REPO_ROOT, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

SESSION_HOME = _SESSION_HOME
REPO_ROOT = _REPO_ROOT


# ── Language ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _pinned_language():
    """Pin the UI language so localized sheet names / labels are deterministic.

    The Excel importer looks sheets up by their *translated* title, so an
    unpinned language makes template round-trip tests non-reproducible.
    """
    from scheduler_app.translations import set_language
    set_language("tr")
    yield


# ── Storage sandbox ─────────────────────────────────────────────────────────

@pytest.fixture
def dersis_home(tmp_path, monkeypatch):
    """Rebind scheduler_app storage to a fresh, empty Dersis root for one test."""
    from scheduler_app.storage import storage

    root = tmp_path / "Documents" / "Dersis"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "_ROOT_DIR", str(root))
    # The master key is cached process-wide; a stale one would point at the
    # previous test's key.bin and silently make decryption "work".
    storage._cached_key = None
    storage.ensure_dirs()
    try:
        yield root
    finally:
        storage._cached_key = None


# ── Qt ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def make_app(qapp, dersis_home, monkeypatch):
    """Factory for a real, isolated, never-shown ``SchedulerApp``.

    Constructing ``SchedulerApp()`` bare leaks across tests, and the failure is
    the nastiest kind: **a timer that outlives the test that armed it**.

    ``SchedulerApp.__init__`` starts ``FirstRunController``, which drives
    ``QTimer``-based ``_write_flag`` calls. Those fire on a later iteration of
    the event loop — by which time ``dersis_home`` has rebound
    ``storage._ROOT_DIR`` to a *different* test's directory. The write then
    lands in that test's settings file, and if the test in question is one that
    deliberately plants a corrupt container (``test_settings_recovery``), the
    stray write quarantines it and raises a toast **inside an unrelated test**,
    failing an assertion about a channel nobody used.

    Observed exactly that: three modules building ``SchedulerApp`` bare made
    ``test_absent_settings_file_on_first_run_is_not_an_error`` fail with a
    corruption toast naming another test's ``backups/`` path. Running it alone
    passed. This mirrors the isolation ``test_settings_recovery.make_window``
    and ``test_import_ui_flow`` already do by hand.

    ``TierEnforcement`` is a process-wide singleton that ``SchedulerApp``
    registers into and never unregisters, so its registries are snapshotted too.
    """
    from scheduler_app.ui.first_run import FirstRunController
    from scheduler_app.ui.tier_enforcement import TierEnforcement

    monkeypatch.setattr(FirstRunController, "start", lambda self: None)

    # These names are load-bearing and were wrong on first writing: the guard
    # was `hasattr(...)`, so three misspelled names silently snapshotted and
    # restored NOTHING while looking like isolation. Kept in one tuple, matching
    # the list test_settings_recovery.make_window uses, and asserted non-empty
    # below so a rename cannot make this quietly do nothing again.
    registry_names = ("_gated_widgets", "_gated_actions", "_on_tier_changed",
                      "_export_submenu_refreshers")

    enforcer = TierEnforcement.instance()
    registries = [n for n in registry_names if hasattr(enforcer, n)]
    assert registries, (
        "TierEnforcement exposes none of %r — this fixture is restoring "
        "nothing. Update registry_names." % (registry_names,)
    )
    saved = {n: list(getattr(enforcer, n)) for n in registries}

    built = []

    def _factory():
        from scheduler_app.ui.app import SchedulerApp
        win = SchedulerApp()
        built.append(win)
        return win

    try:
        yield _factory
    finally:
        for win in built:
            try:
                win.close()
                win.deleteLater()
            except Exception:
                pass
        for name, value in saved.items():
            setattr(enforcer, name, value)


@pytest.fixture(scope="session")
def qapp():
    """One offscreen QApplication for the whole session (Qt allows only one)."""
    pytest.importorskip("PyQt6.QtWidgets", reason="PyQt6 not installed")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setStyle("Fusion")
    yield app
    # Deliberately not calling app.quit(): tearing the QApplication down
    # mid-session breaks any later Qt test in the same process.


# ── Dataset builders ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def make_state():
    from _support.dataset_gen import make_state as _f
    return _f


@pytest.fixture(scope="session")
def make_preset():
    from _support.dataset_gen import make_preset as _f
    return _f
