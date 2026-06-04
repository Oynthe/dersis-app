"""Storage package: encrypted persistence and path management.

Re-exports everything from the storage module so that existing imports
like ``from scheduler_app import storage`` / ``storage.save_encrypted(...)``
continue to work exactly as before.
"""
from scheduler_app.storage.storage import *  # noqa: F401,F403
from scheduler_app.storage.storage import (  # explicit for type checkers & Nuitka
    UvaFileError,
    root_dir, sub_dir, ensure_dirs,
    settings_path, negotiation_settings_path,
    learned_weights_path, feedback_log_path,
    crash_log_path, autosave_path, new_save_path,
    save_encrypted, load_encrypted,
    save_encrypted_lines, load_encrypted_lines,
    append_encrypted_entry,
    migrate_legacy_files,
    SETTINGS_DIR, SAVES_DIR, LEARNING_DIR, LOGS_DIR,
    EXPORTS_DIR, BACKUPS_DIR, KEYS_DIR,
)
