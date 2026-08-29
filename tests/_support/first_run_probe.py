"""Drive the real ``scheduler_gui.main()`` once, in a fresh process.

Used by ``tests/test_first_run_migration.py``. It must run in a **subprocess**:
``main()`` constructs its own ``QApplication``, and Qt permits exactly one per
process, so it cannot be driven from a suite that already holds the session
``qapp`` fixture (attempting it hangs rather than raising).

Only three things are stubbed, and each is stubbed because it would block or
because it costs seconds — never because it is inconvenient:

* ``LanguageDialog`` — a modal ``exec()`` on a virgin HOME blocks forever, even
  offscreen (measured: ``timeout 25`` -> exit 124).
* ``SchedulerApp`` — building the real window is the thing ``make_app`` already
  covers, and it is ~1.3 s.
* ``QApplication.exec`` / ``sys.exit`` — the event loop and the process exit.

Everything that matters to the property under test is real: the single-instance
lock, ``ensure_dirs``, ``migrate_legacy_files`` and ``run_language_gate``.

HOME/USERPROFILE must already point at a sandbox in ``os.environ`` before this
runs — ``scheduler_app.storage`` binds its root at import time.

Prints one JSON object to stdout: the decrypted settings container, or an
``{"error": ...}`` payload.
"""
import json
import os
import sys


def main():
    lang = os.environ.get("DERSIS_PROBE_LANG", "tr")

    from PyQt6.QtWidgets import QDialog

    import scheduler_app.ui.first_run as first_run
    import scheduler_app.ui.app as ui_app
    import scheduler_gui
    from scheduler_app.storage import storage

    class _AcceptedDialog:
        chosen_language = lang

        def exec(self):
            return QDialog.DialogCode.Accepted

    class _StubWindow:
        def __init__(self, *a, **kw):
            pass

        def show(self):
            pass

    first_run.LanguageDialog = _AcceptedDialog
    ui_app.SchedulerApp = _StubWindow
    scheduler_gui.sys.exit = lambda code=0: None

    # Point the legacy-config lookup at the caller's fake install directory.
    # Faking ``sys.frozen`` instead would also change what
    # ``multiprocessing.freeze_support()`` does, and the property under test is
    # the *ordering* of the migration, not how the old path is resolved.
    frozen_dir = os.environ.get("DERSIS_PROBE_FROZEN_DIR")
    if frozen_dir:
        storage._old_app_config_path = lambda: os.path.join(
            frozen_dir, "scheduler_config.json")

    from PyQt6.QtWidgets import QApplication
    QApplication.exec = lambda self: 0

    scheduler_gui.main()

    try:
        settings = storage.load_encrypted(storage.settings_path())
    except Exception as exc:  # pragma: no cover - reported, not swallowed
        settings = {"error": "%s: %s" % (type(exc).__name__, exc)}
    sys.stdout.write(json.dumps(settings, ensure_ascii=False, default=str))


if __name__ == "__main__":
    # Mandatory: the optimizer uses multiprocessing, and an unguarded module
    # body fork-bombs Windows.
    main()
