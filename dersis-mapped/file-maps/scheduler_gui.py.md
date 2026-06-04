# File: `scheduler_gui.py`

## 1. File Role

The single end-user entry point for the **fully offline** DERSİS desktop app (~95 lines). It bootstraps the Qt application, installs a crash excepthook, runs the first-run language gate, sets the local "institutional" tier so every feature is unlocked, instantiates `SchedulerApp` (the main window), and enters the Qt event loop. It performs **no** login, license check, version check, heartbeat, update check, or any network call.

## 2. Why this file matters

**Critical.** Without it there is no app. It composes the few startup steps in a specific order (excepthook first, language before any widget, tier before the window).

## 3. Imports and Dependencies

- **stdlib**: `sys`, `traceback`, `multiprocessing`, `datetime.datetime`.
- **Third-party**: `PyQt6.QtWidgets` (`QApplication`, `QMessageBox`).
- **Internal (eager)**: `scheduler_app.app::SchedulerApp`, `scheduler_app.storage`, `scheduler_app.translations::tr`.
- **Internal (lazy, inside `main()`)**: `scheduler_app.first_run::run_language_gate`, `scheduler_app.ui.tier_enforcement::TierEnforcement`, `scheduler_app.plans::TIER_INSTITUTIONAL`.
- **Internal (lazy, inside the excepthook)**: `scheduler_app.ui.bug_report::CrashReportDialog`.

No `requests`/`packaging` auto-install block, no `auth.*` imports, no circular dependencies.

## 4. Main Symbols

| Symbol | Lines (approx.) | Purpose |
|--------|------|---------|
| module docstring | 1–9 | States this is a fully offline app (no login/license/update/network). |
| `_crash_log_path()` | 21–23 | Thin wrapper around `storage.crash_log_path()`. |
| `_global_exception_handler(exc_type, exc_value, exc_tb)` | 26–69 | Writes uncaught exceptions to `crash_log.txt`; if a `QApplication` exists, opens `CrashReportDialog`; falls back to `QMessageBox.critical`; always calls `sys.__excepthook__`. Skips `KeyboardInterrupt`. |
| `main()` | 72–91 | The full (short) startup sequence — see Runtime Behavior. |
| `if __name__ == "__main__": main()` | 94–95 | Standard guard. |

## 5. Block-by-block code map

| Lines | Block | What it does |
|-------|-------|--------------|
| 1–18 | docstring + imports | Identify the offline entry point; pull in `QApplication`, `storage`, `SchedulerApp`, `tr`. |
| 21–23 | `_crash_log_path` | Path helper (indirection over `storage`). |
| 26–69 | `_global_exception_handler` | Format traceback → append to crash log → open `CrashReportDialog` (or `QMessageBox` fallback) → `sys.__excepthook__`. Robust against partial init via nested try/except. |
| 72–76 | `main()` head | `multiprocessing.freeze_support()`; install `sys.excepthook`; create `QApplication(sys.argv)`; `app.setStyle("Fusion")`. |
| 78–80 | language gate | `run_language_gate()` — first-run language picker (local only). |
| 82–86 | unlock tier | `TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)` so every feature is unlocked with no server lookup. |
| 88–91 | window + loop | `SchedulerApp()` → `window.show()` → `sys.exit(app.exec())`. |
| 94–95 | guard | Allows import-without-launch. |

## 6. Runtime Behavior

Executed once per launch:

1. `multiprocessing.freeze_support()` runs first so `ParallelScorerPool` works on Windows frozen builds.
2. The global exception hook is installed early so even initialisation crashes are captured.
3. `QApplication` is created with the Fusion style.
4. The **language gate** runs before the main window is built, so all widgets pick up the right strings on first paint.
5. The local **institutional** tier is set, unlocking all features (no entity limits).
6. `SchedulerApp` is constructed and shown; control passes to it.
7. `app.exec()` drives the event loop until the window closes.

There is no auth gate, version block, heartbeat thread, or update check.

## 7. Data Flow

- **In**: command-line `sys.argv`; persisted UI settings/saves under `~/Documents/Dersis/` (read by `SchedulerApp`/`storage`, not by this file).
- **Out**: `~/Documents/Dersis/logs/crash_log.txt` (on crash). No session/device files are written.

## 8. UI Flow

Triggers at most two dialogs:

1. `LanguageSelectorDialog` (first run only, via `run_language_gate`).
2. `CrashReportDialog` (only on an unhandled exception; `QMessageBox.critical` as a last-ditch fallback).

After `window.show()`, control passes entirely to `SchedulerApp` in `ui/app.py`.

## 9. Error Handling and Edge Cases

- Crash during init: caught by `_global_exception_handler`, written to the log, dialog shown, app exits via `sys.__excepthook__`.
- If the crash dialog itself fails to construct, the handler falls back to a plain `QMessageBox.critical`, and if even that fails it silently proceeds to `sys.__excepthook__`.
- `KeyboardInterrupt` bypasses the custom handler (delegated to the default hook).

## 10. Integration Points

- Calls into: `scheduler_app.app.SchedulerApp`, `scheduler_app.first_run.run_language_gate`, `scheduler_app.ui.tier_enforcement.TierEnforcement`, `scheduler_app.plans.TIER_INSTITUTIONAL`, `scheduler_app.storage`, `scheduler_app.translations.tr`, `scheduler_app.ui.bug_report.CrashReportDialog`.
- Called by: the OS / shell / Windows installer shortcut. No internal caller.

## 11. Risks and Maintenance Notes

- The startup order is **specific**: excepthook first, `run_language_gate()` before any widget, tier set before `SchedulerApp()`. Reordering could break first-paint translations or feature gating.
- `_global_exception_handler` must remain robust against partial initialisation (it may run before the main window exists).
- To run the app from source: `python scheduler_gui.py`.

## 12. Mini Summary for Future Claude Instances

`scheduler_gui.py` is the offline launcher: `freeze_support` → crash excepthook → `QApplication`(Fusion) → language gate → institutional tier → `SchedulerApp` → `exec()`. No auth, no network. It holds no business logic — it just composes startup. If you change ordering, step through `main()` to verify.
