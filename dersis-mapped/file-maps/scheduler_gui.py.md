# File: `scheduler_gui.py`

> **Captured 2026-06-04.** _Updated 2026-06-08: reflects the startup-crash and dark-mode-theme fixes — the file was rewritten (~95 → ~190 lines) so top-level imports are stdlib-only, the heavy imports are lazy, the excepthook is installed in `__main__` before `main()`, and a bootstrap-failure path now logs `startup_error.log` + shows a native message box. `_crash_log_path` was removed; `_startup_log_path` and `_report_startup_failure` were added._

## 1. File Role

The single end-user entry point for the **fully offline** DERSİS desktop app (~190 lines). It installs a top-level exception hook, then bootstraps the Qt application, pins a light palette, runs the first-run language gate, sets the local "institutional" tier so every feature is unlocked, instantiates `SchedulerApp` (the main window), and enters the Qt event loop. It performs **no** login, license check, version check, heartbeat, update check, or any network call.

Only the standard library is imported at module load; the heavy imports (PyQt6, `scheduler_app`) happen **lazily** inside `main()` and inside the exception hook. The point is robustness: a packaging/import failure (e.g. `scheduler_app` not on `sys.path`) is caught by the excepthook and surfaced as a logged, visible error instead of silently closing the window under `pythonw.exe`.

## 2. Why this file matters

**Critical.** Without it there is no app. It composes the few startup steps in a specific order (excepthook installed in `__main__` *before* `main()`, language before any widget, tier before the window) and is the crash-safe bootstrap layer: it converts otherwise-silent `pythonw.exe` deaths (including import-time failures) into a written log plus a visible dialog.

## 3. Imports and Dependencies

- **stdlib (module top-level, the only eager imports)**: `os`, `sys`, `tempfile`, `traceback`, `datetime.datetime`.
- **stdlib (lazy)**: `multiprocessing` (inside `main()`), `ctypes` (inside `_report_startup_failure`, for the native Windows `MessageBoxW`).
- **Third-party (lazy)**: `PyQt6.QtWidgets.QApplication` (inside `main()` and inside the excepthook's "is Qt up?" probe); `PyQt6.QtWidgets.QMessageBox` (inside the excepthook's Qt-running path). No PyQt6 import happens at module load.
- **Internal (lazy, inside `main()`)**: `scheduler_app.app::{SchedulerApp, apply_light_palette}`, `scheduler_app.first_run::run_language_gate`, `scheduler_app.ui.tier_enforcement::TierEnforcement`, `scheduler_app.plans::TIER_INSTITUTIONAL`.
- **Internal (lazy, inside the excepthook's Qt path)**: `scheduler_app.storage` (for `crash_log_path()`), `scheduler_app.translations::tr`, `scheduler_app.ui.bug_report::CrashReportDialog`.

No `requests`/`packaging` auto-install block, no `auth.*` imports, no circular dependencies, and — unlike the pre-2026-06-08 version — **no eager `PyQt6`/`scheduler_app` imports at module top**.

## 4. Main Symbols

| Symbol | Lines | Purpose |
|--------|-------|---------|
| module docstring | 1–9 | States this is a fully offline app (no login/license/update/network). |
| import block + comment | 11–19 | Comment explaining the stdlib-only top level + lazy heavy imports; imports `os`, `sys`, `tempfile`, `traceback`, `datetime`. |
| `_APP_DIR` | 21 | `os.path.dirname(os.path.abspath(__file__))` — the dist/app directory. |
| `_startup_log_path()` | 24–41 | Returns the first writable of `~/Documents/Dersis/logs`, `_APP_DIR`, then the system temp dir, as `.../startup_error.log` (or `None`). stdlib-only so it works even when `scheduler_app` cannot be imported. |
| `_report_startup_failure(tb_text)` | 44–82 | Persists + surfaces a fatal **startup** error *before the Qt app exists*: appends to `startup_error.log`, shows a native Windows `MessageBoxW` (via `ctypes`), and echoes to stderr. All stdlib — works even when PyQt6/`scheduler_app` fail to import. |
| `_global_exception_handler(exc_type, exc_value, exc_tb)` | 85–153 | Top-level hook for any unhandled exception. Branches on whether a `QApplication` exists (see §9). Skips `KeyboardInterrupt`; always ends at `sys.__excepthook__`. |
| `main()` | 156–183 | The full (short) startup sequence — see Runtime Behavior. |
| `if __name__ == "__main__":` | 186–191 | Installs `sys.excepthook = _global_exception_handler` **before** calling `main()` so import-time failures are caught. |

## 5. Block-by-block code map

| Lines | Block | What it does |
|-------|-------|--------------|
| 1–19 | docstring + imports | Identify the offline entry point; comment that only stdlib is imported at load and heavy imports are deferred; pull in `os`, `sys`, `tempfile`, `traceback`, `datetime`. |
| 21 | `_APP_DIR` | Module constant: absolute path of the app directory. |
| 24–41 | `_startup_log_path` | Best-effort writable path for `startup_error.log` (Documents/Dersis/logs → app dir → temp). stdlib-only. |
| 44–82 | `_report_startup_failure` | Append traceback to `startup_error.log` → build a one-line summary from the last traceback line → native `ctypes` `MessageBoxW` → stderr echo. Used when there is no Qt app yet. |
| 85–153 | `_global_exception_handler` | `KeyboardInterrupt` passthrough → format traceback → probe for a live `QApplication` (lazy `PyQt6` import; failure ⇒ treat as bootstrap). **No app** ⇒ `_report_startup_failure` and return. **App up** ⇒ lazily resolve `storage.crash_log_path()` (fallback `_startup_log_path()`), append a `CRASH` entry, then lazily import `QMessageBox`/`tr` and show `CrashReportDialog` (or `QMessageBox.critical` fallback); finally `sys.__excepthook__`. |
| 156–158 | `main()` head | Lazy `import multiprocessing`; `multiprocessing.freeze_support()`. |
| 160–168 | Qt bootstrap | Lazy import `QApplication` and `SchedulerApp, apply_light_palette`; `QApplication(sys.argv)`; `app.setStyle("Fusion")`; `apply_light_palette(app)` (light palette so the light-only stylesheet stays readable under OS dark mode). |
| 170–172 | language gate | `run_language_gate()` — first-run language picker (local only). |
| 174–178 | unlock tier | `TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)` so every feature is unlocked with no server lookup. |
| 180–183 | window + loop | `SchedulerApp()` → `window.show()` → `sys.exit(app.exec())`. |
| 186–191 | guard | Installs the excepthook first, then `main()`. Allows import-without-launch. |

## 6. Runtime Behavior

Executed once per launch:

1. At `__main__`, **before anything heavy is imported or run**, `sys.excepthook = _global_exception_handler` is installed, so even an import-time failure (e.g. `scheduler_app` not on `sys.path`) is reported rather than vanishing under `pythonw.exe`. `main()` is then called **without** a surrounding `try/except`.
2. `main()` lazily imports `multiprocessing` and calls `multiprocessing.freeze_support()` first so `ParallelScorerPool` works on Windows frozen builds.
3. PyQt6 and `scheduler_app.app` are imported lazily; `QApplication` is created with the Fusion style, then `apply_light_palette(app)` pins a deterministic light `QPalette`.
4. The **language gate** runs before the main window is built, so all widgets pick up the right strings on first paint.
5. The local **institutional** tier is set, unlocking all features (no entity limits).
6. `SchedulerApp` is constructed and shown; control passes to it.
7. `app.exec()` drives the event loop until the window closes.

There is no auth gate, version block, heartbeat thread, or update check.

## 7. Data Flow

- **In**: command-line `sys.argv`; persisted UI settings/saves under `~/Documents/Dersis/` (read by `SchedulerApp`/`storage`, not by this file).
- **Out**:
  - On a **bootstrap failure** (before Qt is up): `startup_error.log` in the first writable of `~/Documents/Dersis/logs`, the app dir, or the temp dir.
  - On a **runtime crash** (Qt already up): `~/Documents/Dersis/logs/crash_log.txt` via `storage.crash_log_path()` (falling back to `startup_error.log` if `storage` itself cannot be imported).
- No session/device files are written.

## 8. UI Flow

Triggers at most these dialogs:

1. `LanguageSelectorDialog` (first run only, via `run_language_gate`).
2. On a runtime crash with Qt running: `CrashReportDialog` (`QMessageBox.critical` as a last-ditch fallback).
3. On a bootstrap failure with no Qt app: a **native Windows `MessageBox`** (via `ctypes`), since Qt may not be importable at that point.

After `window.show()`, control passes entirely to `SchedulerApp` in `ui/app.py`.

## 9. Error Handling and Edge Cases

`_global_exception_handler` has **two paths**, chosen by whether the Qt application is already running:

- **No `QApplication` (bootstrap failure)** — including the case where importing `PyQt6` itself fails: calls `_report_startup_failure(tb_text)`, which appends to `startup_error.log`, shows a native `MessageBoxW`, and echoes to stderr, then returns. This is exactly the formerly-silent `pythonw.exe` death (e.g. `ModuleNotFoundError: No module named 'scheduler_app'`).
- **`QApplication` running (rich crash reporting)** — writes the `CRASH` entry to `crash_log.txt` and shows `CrashReportDialog` (offers an email-based report via `mailto:`); if the crash dialog itself fails to construct, falls back to a plain `QMessageBox.critical`; if even that fails it silently proceeds.
- Both paths end at `sys.__excepthook__` (the bootstrap path returns early; the Qt path calls it explicitly at the end).
- `KeyboardInterrupt` bypasses the custom handler entirely (delegated to the default hook).

## 10. Integration Points

- Calls into: `scheduler_app.app.SchedulerApp`, `scheduler_app.app.apply_light_palette`, `scheduler_app.first_run.run_language_gate`, `scheduler_app.ui.tier_enforcement.TierEnforcement`, `scheduler_app.plans.TIER_INSTITUTIONAL`, `scheduler_app.storage.crash_log_path`, `scheduler_app.translations.tr`, `scheduler_app.ui.bug_report.CrashReportDialog`.
- Called by: the OS / shell / Windows installer shortcut (`Dersis.exe` → `pythonw.exe scheduler_gui.py`). No internal caller.

## 11. Risks and Maintenance Notes

- The startup order is **specific**: the excepthook is installed in `__main__` before `main()`; `run_language_gate()` runs before any widget; the tier is set before `SchedulerApp()`; `apply_light_palette(app)` runs right after `app.setStyle("Fusion")`. Reordering could break first-paint translations, feature gating, dark-mode readability, or crash visibility.
- `_global_exception_handler` and the two startup helpers (`_startup_log_path`, `_report_startup_failure`) must remain robust against partial initialisation and stdlib-only — they may run before PyQt6/`scheduler_app` are importable.
- Keep the heavy imports **lazy**. Re-adding an eager `import PyQt6` / `from scheduler_app...` at module top would defeat the bootstrap-failure reporting (an import error would happen before the excepthook can present it).
- To run the app from source: `python scheduler_gui.py`.

## 12. Mini Summary for Future Claude Instances

`scheduler_gui.py` is the crash-safe offline launcher. `__main__` installs the excepthook first, then `main()` does `freeze_support` → (lazy) `QApplication`(Fusion) → `apply_light_palette` → language gate → institutional tier → `SchedulerApp` → `exec()`. Top-level imports are stdlib-only; PyQt6/`scheduler_app` load lazily so import failures are logged (`startup_error.log` + native MessageBox) instead of closing silently under `pythonw.exe`. Runtime crashes (Qt up) still go to `crash_log.txt` + `CrashReportDialog`. No auth, no network, no business logic — it just composes startup. If you change ordering, step through `main()` and both excepthook paths to verify.
