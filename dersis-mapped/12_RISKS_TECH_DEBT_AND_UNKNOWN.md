# 12 — Risks, Tech Debt, and Unknowns

This document lists observations from reading the code. **Confirmed issues** are things directly visible in the source; **hypotheses** are reasonable suspicions that would need investigation. Nothing is being fixed — just recorded.

> **Offline-first:** DERSİS is now a fully offline desktop app. The former licensing/auth gate, heartbeat thread, auto-updater, device fingerprinting, and all remote network endpoints were removed, so the risks that used to attach to them no longer apply and have been dropped from this list.

## Resolved since capture (2026-06-08)

Two latent packaging/UI risks present in the 2026-06-04 snapshot have since been fixed (branch `claude/eloquent-rubin-7hwes6`, commits `65de83a`/`de15d22`). Recorded here for traceability:

- **Silent startup crash in the embeddable-Python build (RESOLVED).** With a `python*._pth` file the interpreter runs in safe-path mode and did not add the launched script's dir to `sys.path`, so `import scheduler_app` failed and the app closed with **no message** under `pythonw.exe`. Fixed on three fronts: `build_embed.bat` now appends `..` to `python*._pth` (and adds a bundled-interpreter import smoke test); `scheduler_gui.py` now imports the heavy deps lazily and installs `_global_exception_handler` in `__main__` *before* `main()`, so a bootstrap/import failure is written to `startup_error.log` and shown in a native `MessageBox` instead of vanishing. See `04_ENTRYPOINTS_AND_RUNTIME_FLOW.md` and `10_BUILD_PACKAGING_RELEASE_MAP.md`.
- **Dark-mode white-on-white contrast (RESOLVED).** The app shipped a light-only stylesheet but set no palette; Qt 6.5+ follows the OS scheme, so under Windows dark mode menus/lists/inputs rendered light-on-light. Fixed by `apply_light_palette(app)` in `ui/app.py` (pinned light `QPalette`, called from `scheduler_gui.main()` after `setStyle("Fusion")`) plus explicit `color: #1E293B` on the `QMenu`/`QComboBox`/`QListWidget` stylesheet rules. See `06_UI_MAP.md` §1.0.

## Confirmed issues

### 1. `scheduler_app/__init__.py` — `_ShimFinder.find_module` and `find_spec`

The `find_module` method is part of the legacy import machinery removed in Python 3.12. The class implements both `find_module` and `find_spec`, so it works on current Pythons, but `find_module` is dead code on Python 3.12+. Not urgent; flagged for cleanup eventually.

### 2. Very large translations file

`scheduler_app/ui/translations.py` is 21,790 lines. Any edit risks accidental key drift, broken format placeholders, or inconsistent translations across languages. There is no parity test. **Translating safely requires care.**

### 3. `logic.py::respects_constraints` is documented as deprecated

The function carries a deprecation note ("Use `ConstraintValidator.respects_constraints` instead"). Still called from `data_io/exporter.py` indirectly via `find_valid_options` (used by some legacy paths). Worth migrating.

### 4. Heavy bidirectional dependence between `ui/app.py` and `core/logic.py`

Although `core/workflow.py` exists precisely to avoid this, several places in `ui/app.py` still call `core/logic.py` helpers directly (notably `get_placed_classes`, `classroom_of`, `effective_*`). These are pure-function read-only helpers, so it isn't dangerous, but a future refactor that wanted to swap out `logic.py` would have to update both layers.

### 5. `cls_key()` lazily mutates the class dict

`models.py::cls_key(cls)` assigns `class_uid` if missing. This is invisible to the caller but mutates the input. Any cache keyed by `id(cls)` could become inconsistent if the dict was supposed to be treated as read-only.

### 6. Conditional import + module-level `HAS_*` flags

Several modules (`importer.py`, `exporter.py`, `cpsat_scheduler.py`, `template.py`) detect missing optional deps with try/except at import time and a `HAS_PANDAS` / `HAS_OPENPYXL` / `HAS_ORTOOLS` flag. Errors are surfaced to the user via translated messages — good. But the flags can be desynchronised across processes if `ProcessPoolExecutor` workers re-import; nothing observed to break, just worth knowing.

### 7. Embedded Python build is platform-specific

`build_embed.bat` is Windows-only. Linux/macOS users cannot build an installer from this repo. Documented; just noting.

### 8. `bug_report.py` embeds a long QSS string

Maintainability concern: changing the theme requires editing strings in multiple files (the dialog stylesheet template in `dialogs.py`, the bug-dialog style in `bug_report.py`, and the renderer constants in `core/constants.py`). No central theme module.

### 9. `feedback_logger.py` silently swallows write failures

The `_write_entry` call uses `except Exception: pass`. By design — logging must never crash the app — but it means a misconfigured filesystem produces silent data loss for the preference learner.

## Hypotheses (need investigation)

### H1. RTL handling is incomplete

`is_rtl()` exists in `ui/translations.py` but the only consumer I traced was the renderer. `QApplication.setLayoutDirection` is not called anywhere. Arabic / Hebrew / Persian users may see correct text but left-aligned dialogs.

### H2. The "Multi-department" tier feature may have no client-side implementation

`plans.py` has `FEATURE_MULTI_DEPARTMENT`, but I did not see code that branches on it. It is part of the (now always-unlocked) tier table; likely unused/future.

### H3. `optimization_goals.py` and `placement_scorer.py` weight-key consistency

The 6 goals map to subsets of the 14 weights. If a new weight is added to `DEFAULT_WEIGHTS` without updating the goals mapping, the goals UI silently has no effect on it. There is no parity assertion.

### H4. `ScheduleOptimizer.optimize()` parallel scoring might deadlock under specific Windows configurations

`ProcessPoolExecutor` on Windows requires the module to be importable; the snapshot helpers are designed for this, but there is no test that proves it on Windows. If the snapshot accidentally contains a non-picklable object (e.g. a lambda from a custom hook), workers would silently fail.

### H5. The `EguFileError` message strings come from `tr()` at raise time

If the encrypted settings file containing the language preference is itself corrupt, `tr()` falls back to English — fine. But if `translations.py` is somehow not importable (rare), the error path would crash with `ImportError` before showing a useful message.

### H6. Translation `format(**kwargs)` placeholders may differ across languages

For each translation key with `{placeholder}`, every language version must use the same placeholder names. The `tr()` function catches `KeyError/IndexError/ValueError` and returns the unformatted text, so it won't crash, but the user would see literal `{name}` strings — which is technically "silent failure". The lack of a parity test (see #2) means this can drift.

## Dead/legacy code candidates

| Item | Notes |
|------|-------|
| `logic.py::respects_constraints` | Marked deprecated. |
| `logic.py::find_conflicts` (the in-module variant, distinct from `ConstraintValidator.find_conflicts`) | Still used by `find_valid_options`; not dead but parallel with the validator. |
| `learning/preference_model.egu` filename mentioned in storage paths | I did not see code that reads/writes it. Possibly future use. |
| `_BADGE_MAP` "soft" key produces emoji 🛡️ — present in code but I did not verify rendering on every platform. |
| `plans.py` + `ui/tier_enforcement.py` | Retained but always-unlocked in the offline build (default tier `institutional`); `UpgradeDialog`/`PRICING_PAGE_URL` are effectively dead. Could be removed if tiers are never reintroduced. |

## TODO/FIXME comments

A `grep -rn "TODO\|FIXME"` was not exhaustively run as part of this map. The largest source files (`ui/app.py`, `ui/dialogs.py`, `core/schedule_optimizer.py`, `core/constraint_negotiator.py`) should be the first targets if a future cleanup pass is needed.

## Areas central to the app — handle with extra care

These should never be casually refactored:

1. **`storage/storage.py`** — the `.egu` container format. Changing the layout or checksum scheme would invalidate every existing user's data.
2. **`scheduler_app/__init__.py`** — the `_ShimFinder` shim. External scripts and old flat imports (e.g. `scheduler_app.models`) rely on it.
3. **`models.py::new_class()` schema** — any field added must also be handled by `normalize_class_data` and `copy_editable_class_fields`.
4. **`ui/translations.py`** — see #2 in *Confirmed issues* above. Use a script when refactoring keys.
5. **`scheduler_gui.py::main` startup order** — the excepthook is installed in the `__main__` guard *before* `main()`; then (after lazy heavy imports) `freeze_support` → `QApplication` (Fusion) → `apply_light_palette` → `run_language_gate()` → `TierEnforcement.set_tier(TIER_INSTITUTIONAL)` → `SchedulerApp()`. The language gate must run before any widget is built; the excepthook must be installed first; `apply_light_palette` must run right after `setStyle`. Keep the heavy imports **lazy** — re-adding an eager `import PyQt6`/`from scheduler_app …` at module top would defeat the bootstrap-failure reporting.
6. **`scheduler_gui.py::_global_exception_handler`** — wired before everything else; must remain robust against partial initialisation and **stdlib-only on the bootstrap path** (it may run before PyQt6/`scheduler_app` are importable). Its two paths — native `MessageBox` (no `QApplication`) vs `CrashReportDialog` (Qt running) — hinge on whether a `QApplication` exists.
