# 09 — Settings, Localization, and Persistence Map

## 1. Settings management

### 1.1 What counts as "settings"

| Concern | Where stored | Module |
|---------|--------------|--------|
| UI preferences (language, last tab, zoom, recent files) | `settings/app_settings.egu` | written by `ui/app.py`, `ui/first_run.py` |
| Constraint negotiation prefs | `settings/negotiation_settings.egu` | `core/constraint_negotiator.py` |
| Learned scoring weights | `learning/learned_weights.egu` | `learning/preference_learner.py` |
| Feedback log | `logs/feedback_log.egu` | `learning/feedback_logger.py` |
| Saved timetables (user `.egu` files) | `saves/timetable_*.egu`, `saves/autosave.egu` | `ui/app.py` save/load handlers |
| Crash log (plaintext) | `logs/crash_log.txt` | `scheduler_gui.py::_global_exception_handler` |

### 1.2 Reading/writing settings

All access goes through `scheduler_app/storage/storage.py`. The public functions used directly are:

| Function | Use |
|----------|-----|
| `save_encrypted(data, path)` | Save any JSON-serialisable object. |
| `load_encrypted(path)` | Load and auto-decrypt. Returns the deserialised object. Raises `EguFileError`/`FileNotFoundError`. |
| `save_encrypted_lines(entries, path)` | Save a list as an array-of-objects. |
| `load_encrypted_lines(path)` | Load a list; returns `[]` if the file is missing or unreadable (silent). |
| `append_encrypted_entry(entry, path)` | Read existing, append, write back. Used by the feedback logger. |
| `settings_path()`, `negotiation_settings_path()`, `learned_weights_path()`, `feedback_log_path()`, `autosave_path()`, `new_save_path()`, `crash_log_path()` | Canonical path helpers. Each ensures the subdir exists. |
| `ensure_dirs()` | Create the full `~/Documents/Dersis/` tree. Called at startup. |
| `migrate_legacy_files()` | One-time migration from older `~/.class_scheduler/` and root-relative `scheduler_config.json`. |

### 1.3 Path layout under `~/Documents/Dersis/`

```
settings/
    app_settings.egu
    negotiation_settings.egu
saves/
    autosave.egu
    timetable_2026_06_04_09_30_15.egu
    ...
learning/
    learned_weights.egu
    preference_model.egu          (reserved/unused)
logs/
    feedback_log.egu              (encrypted JSON array)
    crash_log.txt                 (plain text, appended on each crash)
exports/                          (default user export location)
backups/                          (legacy files moved here during migration)
keys/
    key.bin                       (32-byte AES master key, chmod 0o600)
```

If a legacy `~/Documents/ClassScheduler/` directory exists and `~/Documents/Dersis/` does not, the entire tree is copied over once on first launch.

> **Offline build:** the former `settings/auth_session.egu` (encrypted login session) and `settings/device_identity.egu` (cached device hash) are **no longer created or read** — the auth/licensing subsystem was removed. Existing files from an earlier online install are simply ignored.

## 2. Language / localisation system

### 2.1 The translation table — `ui/translations.py`

A single Python module exposing `TRANSLATIONS: dict[str, dict[str, str]]`. ~21,790 lines, ~70 % is the per-language string blocks.

Language codes present (22 total):
`en`, `tr`, `de`, `fr`, `es`, `zh`, `ru`, `ar`, `fa`, `it`, `pt_BR`, `pt_PT`, `nl`, `sv`, `da`, `pl`, `az`, `hi`, `id`, `af`, `ja`, `ko`.

Approximate starting line numbers of each block in the file (handy for diffs):

| Lang | Start line |
|------|------------|
| `en` | 3 |
| `tr` | 1046 |
| `de` | 2090 |
| `fr` | 3072 |
| `es` | 4054 |
| `zh` | ~5036 |
| `ru` | 6018 |
| `ar` | ~7000 |
| `fa` | ~7982 |
| `it` | 8964 |
| `pt_BR` | ~9946 |
| `pt_PT` | ~10928 |
| `nl` | 11910 |
| `sv` | ~12892 |
| `da` | ~13874 |
| `pl` | 14856 |
| `az` | 15838 |
| `hi` | ~16820 |
| `id` | 17802 |
| `af` | ~18784 |
| `ja` | ~19766 |
| `ko` | ~20748 |

(The starting lines were derived by inspecting the file; some are exact, some are interpolated from adjacent confirmed blocks.)

### 2.2 Key naming convention

Dot-namespaced keys grouped by area:

| Prefix | Meaning | Examples |
|--------|---------|----------|
| `app.*` | App-wide titles + crash strings | `app.title`, `app.crash_title`, `app.crash_body` |
| `menus.*` | Menu titles | `menus.file`, `menus.edit` |
| `buttons.*` | Generic button labels | `buttons.save`, `buttons.cancel` |
| `actions.*` | Undo/redo action descriptions | `actions.place`, `actions.undo_action` |
| `labels.*` | UI labels | `labels.lecturer`, `labels.duration` |
| `weekdays.*` | Day names | `weekdays.monday` |
| `dialogs.*.*` | Dialog-specific strings | `dialogs.add_class.title` |
| `analytics.*` | Dashboard text | `analytics.balance_score` |
| `warnings.*` | Warnings & notifications | `warnings.heavy_days` |
| `errors.*` | Error messages | `errors.lecturer_required` |
| `validation.*` | Validation messages | `validation.lecturer_busy` |
| `conflicts.*` | Conflict descriptions | `conflicts.room_occupied` |
| `negotiation.*` | Negotiation report text | `negotiation.no_room_capacity` |
| `protection.*` / `badges.*` | Protection / badge labels | `protection.locked`, `badges.pinned` |
| `tutorial.*` | Tutorial step text | `tutorial.welcome_title` |
| `setup.*` | Setup dialog | `setup.capacity` |
| `upgrade.*` | Tier upgrade dialog (offline: never shown) | `upgrade.dialog.title` |
| `bug_report.*` | Bug/crash report dialogs | `bug_report.title`, `bug_report.submit` |
| `import.*` / `export.*` | Workbook headers/descriptions | `import.columns.teacher_id` |
| `template.*` | Workbook example data | `template.workbook_example.teacher_1_name` |
| `status.*` | Status-bar / banner text | `status.classes_imported_count` |

### 2.3 The `tr()` function

`tr(key, **kwargs)` — looks up the current language; falls back to English; falls back to the key itself if both miss. Optional `**kwargs` are passed to `str.format`. Never raises.

`set_language(code)` — only sets if the code exists in `TRANSLATIONS`. Modules cache `_current_lang` at module level.

`get_language()` — returns current code.

`RTL_LANGUAGES = {"ar", "he", "fa", "ur"}` and `is_rtl(lang=None)` — used by the renderer and some dialogs to reverse layouts.

### 2.4 Tier translation merge — `ui/tier_translations.py`

On import, this module mutates the global `TRANSLATIONS` dict to add `upgrade.*` keys for each supported language (`en`, etc.). Importing `tier_enforcement` triggers this side effect via the no-op `# noqa: F401` import.

### 2.5 Day-key normalisation — `ui/day_keys.py`

Because the same day can appear in user data as "Monday", "monday", "Lundi", "Pazartesi", etc., `normalize_day_value` looks up the stored value against `DAY_KEYS` and against every language's `weekdays.*` translation. `normalize_state_day_keys(state)` walks the entire state and applies this — used when loading old saves whose day strings drifted.

## 3. Theme / style handling

There is no dynamic theming system. Styles come from three places:

1. **Constants** in `core/constants.py` — used by the renderer.
2. **Per-dialog QSS** embedded as Python f-strings at the top of each dialog file (see `_DIALOG_STYLESHEET_TEMPLATE` in `ui/dialogs.py`; `_BUG_DIALOG_STYLE` in `ui/bug_report.py`).
3. **Application-wide Fusion style** set in `scheduler_gui.py::main` via `app.setStyle("Fusion")`.

The brand colour is purple `#6e4f9e` (defined in `installer/create_wizard_images.py`).

## 4. Encryption details

### 4.1 Key management
- Master key: 32 random bytes from `secrets.token_bytes(32)`. Stored at `keys/key.bin`. On POSIX, `chmod 0o600`.
- Per-file key: `sha256(master_key || salt)`. Salt is fresh random 16 bytes per file.
- Cipher: AES-256-GCM (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`). The auth tag is the last 16 bytes of the ciphertext as per AES-GCM.
- AAD: not used (passed as `None`).

### 4.2 `.egu` container layout

| Offset | Size | Field |
|--------|------|-------|
| 0 | 4 | Magic `b"EGU1"` (legacy `b"UVA1"` also accepted on load) |
| 4 | 2 | Format version, big-endian uint16, current `1` |
| 6 | 16 | Salt |
| 22 | 12 | AES-GCM IV/nonce |
| 34 | 4 | Payload length, big-endian uint32 |
| 38 | N | AES-256-GCM ciphertext (includes 16-byte tag) |
| 38+N | 32 | SHA-256 of bytes `[0 .. 38+N)` |

Total = `38 + N + 32`. Minimum valid size = 38 + 16 (empty ciphertext + tag) + 32 = 86 bytes.

### 4.3 Save pipeline (`save_encrypted`)

```
data
  → json.dumps(ensure_ascii=False, indent=2).encode("utf-8")
  → _build_container(plaintext_json)
       header = pack("!4sH", "EGU1", 1)
       salt = random 16
       iv = random 12
       derived_key = sha256(master_key || salt)
       ct = AESGCM(derived_key).encrypt(iv, plaintext, None)
       payload_len = pack("!I", len(ct))
       body = header + salt + iv + payload_len + ct
       checksum = sha256(body)
       return body + checksum
  → atomic write: open(path+".tmp", "wb") → write → os.replace(tmp, path)
```

If write fails, the temp file is best-effort removed and the exception is re-raised.

### 4.4 Load pipeline (`load_encrypted`)

```
open(path,"rb") → blob
  if blob is empty → EguFileError("file_empty")
  if blob[:4] in (EGU1, UVA1):
     _parse_container:
        validate min size
        validate magic and version
        unpack salt/iv/payload_len
        validate length consistency
        compute sha256(body) and compare to trailer
        derive key, AES-GCM decrypt → plaintext
     return json.loads(plaintext) (and normalize_state_classes if it looks like state)
  if heuristically_fernet_token(blob):
     try old Fernet decrypt with keys/scheduler.key
     on success → save_encrypted(data, path)  (auto-upgrade to EGU1)
     return data
  try plain json.loads(blob) → return
  otherwise → EguFileError("unrecognized_file_format")
```

All error strings are translated via `tr("errors.…")`.

## 5. Migration logic

`migrate_legacy_files()` runs at app startup and is idempotent:

1. `~/.class_scheduler/scheduler_config.json` → `settings/app_settings.egu`
2. `~/.class_scheduler/learned_weights.json` → `learning/learned_weights.egu`
3. `~/.class_scheduler/feedback_log.jsonl` → `logs/feedback_log.egu` (as a JSON array)
4. `~/.class_scheduler/negotiation_settings.json` → `settings/negotiation_settings.egu`
5. `~/.class_scheduler/crash_log.txt` → `logs/crash_log.txt` (plain copy)

After a successful migration, the legacy file is moved into `backups/` (with timestamp suffix if a collision occurs).

The function returns a list of human-readable migration notes; the UI may surface them in the status bar.

## 6. Backward-compatibility shims

`scheduler_app/__init__.py` installs a `_ShimFinder` on `sys.meta_path` that maps **old flat imports** (e.g. `scheduler_app.models`) to their new sub-package paths (e.g. `scheduler_app.core.models`). This is critical: legacy callers and unit tests written before the refactor still work.

The `_SHIM_MAP` covers 30+ aliases. See the file map for the full table.

## 7. Tier state (offline)

There is **no tier persistence**. The offline build does not store or load any account/license/tier data. At startup `scheduler_gui.main()` calls `TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)` (see `scheduler_app/plans.py`), so every feature is unlocked and no entity limit applies for the whole session. `gate_menu_action`/`FeatureGateWidget` still exist but always resolve to "allowed", and `UpgradeDialog` is never shown.

## 8. RTL handling

The renderer (`ui/renderer.py`) and a few dialogs query `is_rtl()` before laying out columns. The Qt application-level layout direction is *not* changed (calls to `setLayoutDirection` aren't present); RTL is handled locally by reversing column iteration where appropriate. **This is an area where bugs may hide** — see `12_RISKS_TECH_DEBT_AND_UNKNOWN.md`.

## 9. Where the user's data lives — quick reference

```
Windows:  C:\Users\<USER>\Documents\Dersis\
Linux:    /home/<user>/Documents/Dersis/
macOS:    /Users/<user>/Documents/Dersis/  (untested in this codebase)
```

The path is hardcoded as `os.path.join(os.path.expanduser("~"), "Documents", "Dersis")`. No `~/.config` or AppData convention is used.
