# File: `scheduler_app/storage/__init__.py`

## 1. File Role
Re-export shim for the storage package. Allows `from scheduler_app import storage; storage.save_encrypted(...)` to continue working after the move of the code into `storage/storage.py`.

## 2. Why this file matters
Critical for back-compat.

## 3. Imports and Dependencies
- Internal: re-exports everything from `scheduler_app.storage.storage`.

## 4. Main Symbols
Re-exports: `UvaFileError`, `root_dir`, `sub_dir`, `ensure_dirs`, `settings_path`, `negotiation_settings_path`, `learned_weights_path`, `feedback_log_path`, `crash_log_path`, `autosave_path`, `new_save_path`, `save_encrypted`, `load_encrypted`, `save_encrypted_lines`, `load_encrypted_lines`, `append_encrypted_entry`, `migrate_legacy_files`, plus directory constants.

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–6 | docstring | |
| 7 | wildcard import | back-compat. |
| 8–20 | explicit imports | for type checkers + Nuitka. |

## 6. Runtime Behavior
Pure re-export. No side effects.

## 7. Data Flow
None at this layer.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
None.

## 10. Integration Points
Imported by `scheduler_gui.py`, `ui/app.py`, `auth/*`, `learning/*`, `ui/first_run.py`, ...

## 11. Risks and Maintenance Notes
Keep the explicit re-export list in sync with `storage.py` so Nuitka discovers everything at compile time.

## 12. Mini Summary
Back-compat re-export of `storage/storage.py`. No business logic.
