#!/usr/bin/env python3
"""
Dersis — Modern PyQt6 Class Scheduling Tool
Entry point. All code lives in the scheduler_app package.

This is a fully offline desktop application: it requires no login, license
server, update endpoint, or any network connection. It opens directly into
the main window and every feature works locally.
"""

# Only the standard library is imported at module load. The heavy imports
# (PyQt6, scheduler_app) happen inside main(), so that a packaging/import
# problem is caught by the exception hook below and surfaced as a logged,
# visible error instead of silently closing the window under pythonw.exe.
import os
import sys
import tempfile
import traceback
from datetime import datetime

_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _startup_log_path():
    """Return a best-effort writable path for a startup error log.

    Tries the user's Documents/Dersis/logs first, then the app directory, then
    the system temp dir. Uses only the standard library so it works even when
    scheduler_app cannot be imported.
    """
    for directory in (
        os.path.join(os.path.expanduser("~"), "Documents", "Dersis", "logs"),
        _APP_DIR,
        tempfile.gettempdir(),
    ):
        try:
            os.makedirs(directory, exist_ok=True)
            return os.path.join(directory, "startup_error.log")
        except Exception:
            continue
    return None


def _report_startup_failure(tb_text):
    """Persist and surface a fatal *startup* error (before the Qt app exists).

    Relies on the standard library only, so it works even when PyQt6 or
    scheduler_app fail to import — which is exactly the case that, without this,
    closes the window with no message at all under pythonw.exe (the reported
    ``ModuleNotFoundError: No module named 'scheduler_app'``).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = _startup_log_path()
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"STARTUP FAILURE at {timestamp}\n")
                f.write(f"{'='*60}\n")
                f.write(tb_text)
                f.write("\n")
        except Exception:
            log_path = None

    lines = [ln for ln in tb_text.strip().splitlines() if ln.strip()]
    summary = lines[-1] if lines else "Unknown error."
    body = "Dersis could not start.\n\n" + summary
    if log_path:
        body += f"\n\nDetails were written to:\n{log_path}"

    # Native message box (Windows) — no Qt dependency required.
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, body, "Dersis — Startup Error", 0x10)
    except Exception:
        pass
    # Also echo to stderr for developers running via the console python.exe
    # (harmless no-op when launched via pythonw.exe, which has no console).
    try:
        sys.stderr.write(tb_text)
    except Exception:
        pass


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Top-level hook for any unhandled exception.

    Two paths, chosen by whether the Qt application is already running:
      * No QApplication yet  → bootstrap failure → log + native message box.
      * QApplication running  → write the crash log and show the in-app crash
        report dialog (with the QMessageBox fallback), as before.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

    # Detect whether Qt is up. If importing PyQt6 itself fails, treat it as a
    # bootstrap failure.
    app = None
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None

    if app is None:
        _report_startup_failure(tb_text)
        return

    # ── Qt is running: rich crash reporting ──
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        from scheduler_app import storage
        log_path = storage.crash_log_path()
    except Exception:
        log_path = _startup_log_path()

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"CRASH at {timestamp}\n")
            f.write(f"{'='*60}\n")
            f.write(tb_text)
            f.write("\n")
    except Exception:
        pass

    try:
        from PyQt6.QtWidgets import QMessageBox
        from scheduler_app.translations import tr
        # Try the crash report dialog which offers an email-based report
        try:
            from scheduler_app.ui.bug_report import CrashReportDialog
            dlg = CrashReportDialog(
                exc_type_name=exc_type.__name__,
                exc_message=str(exc_value),
                traceback_text=tb_text,
                log_path=log_path,
            )
            dlg.exec()
        except Exception:
            # Fallback to basic QMessageBox if crash dialog itself fails
            QMessageBox.critical(
                None, tr("app.crash_title"),
                f"{tr('app.crash_body')}\n\n"
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"{tr('app.crash_details')}\n{log_path}")
    except Exception:
        pass

    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    import multiprocessing
    multiprocessing.freeze_support()

    from PyQt6.QtWidgets import QApplication
    from scheduler_app.app import SchedulerApp, apply_light_palette

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Pin a light palette so the light-only stylesheet stays readable even when
    # the OS is in dark mode (Qt 6.5+ otherwise supplies light text, which makes
    # menus and lists render white-on-white).
    apply_light_palette(app)

    # ST-DATA-012: claim the data directory BEFORE the language gate, which
    # itself reads and writes settings/app_settings.egu — the very file two
    # concurrent copies were clobbering last-writer-wins. After
    # freeze_support() too, so spawned optimizer workers in a frozen build
    # never fight over the lock.
    import atexit
    from PyQt6.QtWidgets import QMessageBox
    from scheduler_app.single_instance import acquire_single_instance_lock
    from scheduler_app.translations import tr as _tr

    _instance_lock = acquire_single_instance_lock()
    if _instance_lock is None:
        QMessageBox.information(None, _tr("app.already_running_title"),
                                _tr("app.already_running_body"))
        sys.exit(0)
    atexit.register(_instance_lock.release)

    # Carry a pre-Dersis installation forward BEFORE anything writes settings.
    # The language gate below calls _write_flag, which CREATES
    # settings/app_settings.egu — and storage._migrate_json_file refuses to
    # migrate when its destination already exists, side-lining the source into
    # backups/ on its way out. Called after the lock (which does ensure_dirs)
    # and before the gate, this is the only window in which the legacy
    # scheduler_config.json can still be recovered. SchedulerApp.__init__ also
    # calls it, harmlessly: every migration step is a no-op once it has run.
    from scheduler_app import storage as _storage
    _storage.migrate_legacy_files()

    # First-run language selection (local only — no network).
    from scheduler_app.first_run import run_language_gate
    run_language_gate()

    # Fully offline build: every feature is unlocked locally. There is no
    # login, license server, heartbeat, or update check.
    from scheduler_app.ui.tier_enforcement import TierEnforcement
    from scheduler_app.plans import TIER_INSTITUTIONAL
    TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)

    window = SchedulerApp()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    # Install the exception hook before importing/initialising anything heavy so
    # that even an import-time failure (e.g. scheduler_app not on sys.path) is
    # reported instead of vanishing silently under pythonw.exe.
    sys.excepthook = _global_exception_handler
    main()
