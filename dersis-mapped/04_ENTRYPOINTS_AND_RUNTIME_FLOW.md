# 04 — Entrypoints and Runtime Flow

> **Captured 2026-06-04.** _Updated 2026-06-08: reflects the startup-crash fix — `scheduler_gui.py` was rewritten (~95 → ~190 lines) with stdlib-only top-level imports, lazy heavy imports, the excepthook installed in `__main__` before `main()`, `apply_light_palette()` after `setStyle`, and a bootstrap-failure path that writes `startup_error.log` + shows a native message box._

## Entry points

The repository declares exactly one runtime entry point for end-users: `scheduler_gui.py`. All other invocable scripts (build scripts, test scripts, installer image generator, dependency verifier) are tooling.

| Script | Audience | Purpose |
|--------|----------|---------|
| `scheduler_gui.py` | End user / installer | Launches the full GUI. |
| `verify_deps.py` | Build pipeline | Asserts every package is importable before Nuitka compile. |
| `installer/create_wizard_images.py` | Build pipeline | Generates `installer/wizard_*.bmp` from the logo. |
| `build_embed.bat` | Build pipeline | Builds the embeddable-Python distribution into `build\Dersis.dist\`. |
| `build_nuitka.bat` | Build pipeline | Compiles with Nuitka into `build\Dersis.dist\`. |
| `installer.iss` (via `iscc`) | Build pipeline | Wraps `build\Dersis.dist\` into `Output\Dersis_Setup.exe`. |

## Startup flow (`main()` in `scheduler_gui.py`)

`scheduler_gui.py` is now ~190 lines. It is a **fully offline** entry point: no dependency auto-install, no auth gate, no version/min-version block, no heartbeat thread, no update check, no avatar/profile fetch, and no `SERVER_URL`. Only the standard library is imported at module load (`os`, `sys`, `tempfile`, `traceback`, `datetime`); PyQt6 and `scheduler_app` are imported **lazily** inside `main()` (and inside the excepthook), so an import/packaging failure is caught and surfaced instead of closing the window silently under `pythonw.exe`. The excepthook is installed in the `__main__` guard **before** `main()` is called:

```python
def main():
    import multiprocessing
    multiprocessing.freeze_support()
    from PyQt6.QtWidgets import QApplication
    from scheduler_app.app import SchedulerApp, apply_light_palette
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    apply_light_palette(app)            # light palette → readable under OS dark mode
    from scheduler_app.first_run import run_language_gate
    run_language_gate()
    from scheduler_app.ui.tier_enforcement import TierEnforcement
    from scheduler_app.plans import TIER_INSTITUTIONAL
    TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)
    window = SchedulerApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    sys.excepthook = _global_exception_handler   # installed BEFORE main()
    main()
```

### Step 0 — Install the global exception hook (in `__main__`, before `main()`)
`sys.excepthook = _global_exception_handler` is set in the `if __name__ == "__main__":` guard **before** `main()` runs — and before any heavy import — so even an import-time failure (e.g. `scheduler_app` not on `sys.path`) is reported. The handler has **two paths**, chosen by whether a `QApplication` already exists:
- Lets `KeyboardInterrupt` pass through to the default hook.
- **No `QApplication` (bootstrap failure)** — including when importing `PyQt6` itself fails: `_report_startup_failure(tb_text)` appends the traceback to `startup_error.log` (first writable of `~/Documents/Dersis/logs`, the app dir, or the temp dir), shows a **native Windows `MessageBox`** via `ctypes`, and echoes to stderr. This is exactly the case that used to close `pythonw.exe` silently (the reported `ModuleNotFoundError: No module named 'scheduler_app'`).
- **`QApplication` running** — appends a `CRASH` entry to `storage.crash_log_path()` (`~/Documents/Dersis/logs/crash_log.txt`; falls back to `startup_error.log` if `storage` can't be imported) and opens `CrashReportDialog` (email-based report via `mailto:`); falls back to `QMessageBox.critical` if the crash dialog itself fails.
- Both paths end at `sys.__excepthook__`.

### Step 1 — `multiprocessing.freeze_support()`
`main()` lazily imports `multiprocessing` and calls `freeze_support()` first. Mandatory on Windows for the frozen build; harmless elsewhere. The `ParallelScorerPool` uses `ProcessPoolExecutor`, which on Windows requires `freeze_support`.

### Step 2 — Create the `QApplication` and pin the light palette
`main()` lazily imports `QApplication` and `SchedulerApp, apply_light_palette`, then `app = QApplication(sys.argv); app.setStyle("Fusion")`. Fusion is the consistent cross-platform style used by every dialog. Immediately after, `apply_light_palette(app)` pins a complete light `QPalette` so the light-only stylesheet stays readable even when the OS is in **dark mode** — Qt 6.5+ otherwise follows the OS scheme and supplies light text, rendering menus and lists white-on-white. See `06_UI_MAP.md` §1.0.

### Step 3 — Language gate
`from scheduler_app.first_run import run_language_gate; run_language_gate()`.

The language gate (in `ui/first_run.py`) checks the encrypted settings file for a `language` key. If absent (first launch), it shows the `LanguageSelectorDialog` with 22 flag buttons. Selection is persisted via `storage.save_encrypted`. The chosen code is applied with `set_language(code)`. This is **local only — no network**, and **must** run before the main window is constructed because Qt widgets read translations at instantiation time.

### Step 4 — Unlock all features locally
`TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)` (from `scheduler_app/plans.py`). This sets the singleton to the `institutional` tier, so every feature flag is `True` and all entity limits are unlimited. There is no login, license check, or server lookup — the tier is fixed at startup. The gating helpers (`require_feature` / `require_entity_limit` / `gate_menu_action`) and `UpgradeDialog` in `ui/tier_enforcement.py` are retained but always allow and never display; the toolbar upgrade button/banner stay hidden.

### Step 5 — Construct and show the main window, then run the event loop
`window = SchedulerApp(); window.show()` opens directly into the main window.
- `SchedulerApp` is the `QMainWindow` subclass in `ui/app.py`.
- The constructor builds menus, toolbars, tabs (timetable views, dashboard, warnings), wires drag-drop, registers tier-gated buttons (all effectively unlocked), and triggers autosave restoration.

`sys.exit(app.exec())` then enters the Qt event loop. It only returns when the user closes the window or the app explicitly quits, and `sys.exit(...)` propagates the exit code.

## Configuration / data loading inside the window

`SchedulerApp.__init__` calls (in this rough order):
1. `storage.ensure_dirs()` — creates `~/Documents/Dersis/{settings,saves,learning,logs,exports,backups,keys}` if missing. Also migrates the legacy `~/Documents/ClassScheduler/` folder if found.
2. `storage.migrate_legacy_files()` — moves any old `.json` / `.jsonl` files into the new `.egu` containers.
3. Loads app settings from `settings/app_settings.egu`. These include UI prefs (last tab, zoom, language is already set by the language gate but is also stored here for parity).
4. Loads negotiation settings from `settings/negotiation_settings.egu`.
5. Loads the autosaved state from `saves/autosave.egu` if it exists and the user agrees (an "untitled" prompt may run).
6. Spawns a `FeedbackLogger` (writes to `logs/feedback_log.egu`).
7. Spawns a `PreferenceLearner` (reads `learning/learned_weights.egu` and feeds learned deltas into `PlacementScorer` via the workflow).
8. Builds the menu bar / toolbar / status bar.
9. Constructs the tab widgets: timetable views (per classroom / per lecturer / per branch / Show Everything matrix) plus the dashboard tab and the warnings panel.
10. Calls `apply_translations()` / `retranslate_ui()` to set every label from `tr()`.
11. Connects every Qt signal (button clicked, drop event, slider changed) to handlers that go through `SchedulingWorkflow`.

## UI initialisation specifics

- The renderer (`ui/renderer.py`) is a `QGraphicsView`/`QGraphicsScene` pair. Each cell is a `QGraphicsRectItem` subclass; lessons are `LessonItem` instances supporting mouse drag, context menu, and protection-badge painting. The empty cells are `EmptySlotItem` drop targets.
- The `dashboard` tab is a `QWidget` with painted bar charts and tables (no matplotlib).
- The first-run flow (`first_run.py`) may chain into the tutorial overlay (`tutorial.py`) using deferred `QTimer.singleShot` callbacks so the main window paints first.

## Error handling at startup

| Failure mode | Handling |
|--------------|----------|
| Missing runtime deps / `scheduler_app` not importable | **Bootstrap path** (no `QApplication` yet): the excepthook calls `_report_startup_failure`, which writes `startup_error.log` and shows a native Windows `MessageBox` (no Qt needed) — no longer a silent close under `pythonw.exe`. Deps are also validated at build time by `verify_deps.py` and the build's import smoke test. |
| Language gate raises unexpectedly | `QApplication` exists by now → **Qt path**: crash hook writes `crash_log.txt`, shows `CrashReportDialog`, exits. |
| `SchedulerApp()` construction raises | **Qt path**: crash hook writes `crash_log.txt`, shows `CrashReportDialog`, exits. |
| Unhandled exception anywhere at runtime | `_global_exception_handler` (Qt path) appends to `logs/crash_log.txt`, shows `CrashReportDialog` (offers an email-based report via `mailto:`), then re-raises via `sys.__excepthook__`. |

## Shutdown flow

1. `SchedulerApp.closeEvent` writes the current state to `saves/autosave.egu` (and flushes any other pending local persistence). There are no auth/heartbeat/updater threads to tear down.
2. `app.exec()` returns.
3. `sys.exit(app.exec())` propagates the exit code.

## Verification entry flow

There is no automated test suite in the repository — the previous `test_release_audit.py`, `test_workflow.py`, and `tests/` (including `tests/test_updater.py`) were removed alongside the offline conversion. Verification is now:
- **Manual**: launch `python scheduler_gui.py` (which opens directly into the main window — no login) and exercise the core flows (add a class, auto-place, drag, reschedule, export, save, re-open).
- **CI**: the import / version / build checks (`verify_deps.py` plus the Nuitka/embeddable-Python build) confirm the package imports cleanly and packages.

Any ad-hoc script that imports `scheduler_app.*` operates on pure state dicts (`new_state()`) and result dataclasses and does not need a running `QApplication`.
