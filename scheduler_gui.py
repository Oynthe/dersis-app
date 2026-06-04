#!/usr/bin/env python3
"""
Dersis — Modern PyQt6 Class Scheduling Tool
Entry point. All code lives in the scheduler_app package.

This is a fully offline desktop application: it requires no login, license
server, update endpoint, or any network connection. It opens directly into
the main window and every feature works locally.
"""

import sys
import traceback
import multiprocessing
from datetime import datetime
from PyQt6.QtWidgets import QApplication, QMessageBox
from scheduler_app.app import SchedulerApp
from scheduler_app import storage
from scheduler_app.translations import tr


def _crash_log_path():
    """Return the path to the crash log file."""
    return storage.crash_log_path()


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Write unhandled exceptions to a crash log and show crash report dialog."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log_path = _crash_log_path()
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
        app = QApplication.instance()
        if app is not None:
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
    multiprocessing.freeze_support()
    sys.excepthook = _global_exception_handler
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

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
    main()
