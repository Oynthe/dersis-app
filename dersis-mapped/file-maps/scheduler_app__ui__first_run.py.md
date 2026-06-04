# File: `scheduler_app/ui/first_run.py`

## 1. File Role
First-run state machine: language gate (always runs at startup) → tutorial trigger (first launch only) → optional setup prompt. Persistent flags stored in `settings/app_settings.egu`.

## 2. Why this file matters
Critical. Without the language gate, the first launch shows English regardless of locale.

## 3. Imports and Dependencies
- stdlib: `os`.
- Third-party: PyQt6 widgets/core/gui.
- Internal: `translations.{tr, set_language, get_language, TRANSLATIONS}`, `icons.flag_*` (22 helpers), `storage`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `_read_config(path)`, `_write_flag(path, key, value)` | Helpers using `storage.load_encrypted` / `save_encrypted`. |
| `run_language_gate()` | If no language is stored, opens `LanguageSelectorDialog`; persists choice. |
| `LanguageSelectorDialog(QDialog)` | First-time language picker with flag buttons; 22 options. |
| `FirstRunController` | After main window shows, decides whether to start the tutorial overlay, etc. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–12 | docstring | Architecture summary. |
| 14–22 | imports | |
| 24–~50 | persistent flag helpers | `_read_config` / `_write_flag`. |
| ~50–~200 | `LanguageSelectorDialog` | grid of flag buttons; double-click confirms. |
| ~200–~300 | `run_language_gate` | called from `scheduler_gui.main`. |
| ~300–390 | `FirstRunController` | post-window-show orchestration. |

## 6. Runtime Behavior
`run_language_gate()` is synchronous and modal — runs before `SchedulerApp` is constructed. `FirstRunController` uses `QTimer.singleShot` so its actions run after the main window paints.

## 7. Data Flow
- In: cached language flag.
- Out: `set_language(code)` + persisted flag.

## 8. UI Flow
First launch only: shows language dialog → sets language → continues startup. Subsequent launches: silent.

## 9. Error Handling and Edge Cases
- Settings file missing → treated as first run.
- Settings file corrupt → treated as first run.
- Cancel on language dialog → falls back to English silently.

## 10. Integration Points
- `scheduler_gui.main` calls `run_language_gate()`.
- After main window construction, `FirstRunController.start_if_needed(window)` may launch the tutorial.

## 11. Risks and Maintenance Notes
- Add a new language: register a flag helper in `icons.py` and a button row here.
- Persistent flag key names should not clash with `update_prefs` etc.

## 12. Mini Summary
First-run language gate + tutorial trigger. Runs before/around main window construction.
