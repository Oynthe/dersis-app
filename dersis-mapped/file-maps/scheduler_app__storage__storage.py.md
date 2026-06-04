# File: `scheduler_app/storage/storage.py`

## 1. File Role
Central path management + AES-256-GCM `.egu` encrypted-container format implementation. Owns `~/Documents/Dersis/` directory layout, master key file, save/load API, legacy-format auto-migration.

## 2. Why this file matters
**Critical.** Every persistent operation in the app routes through here. Breaking the file format would invalidate every user's data.

## 3. Imports and Dependencies
- stdlib: `hashlib`, `json`, `os`, `secrets`, `shutil`, `struct`, `sys`, `datetime`.
- Third-party: `cryptography.hazmat.primitives.ciphers.aead.AESGCM`. Optional `cryptography.fernet.Fernet` (lazy, legacy load only).
- Internal: `core.models.normalize_state_classes`, `translations.tr`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `_ROOT_DIR` | `~/Documents/Dersis/`. |
| Subdir constants (`SETTINGS_DIR`, `SAVES_DIR`, `LEARNING_DIR`, `LOGS_DIR`, `EXPORTS_DIR`, `BACKUPS_DIR`, `KEYS_DIR`). | |
| `root_dir()`, `sub_dir(name)`, `ensure_dirs()` | Path helpers. |
| `_with_legacy_fallback(directory, filename_egu)` | Returns `.egu` path or falls back to existing `.uva` for backward compat. |
| `settings_path()`, `negotiation_settings_path()`, `learned_weights_path()`, `feedback_log_path()`, `crash_log_path()`, `autosave_path()`, `new_save_path()` | Well-known files. |
| Container constants: `_MAGIC=b"EGU1"`, `_LEGACY_MAGIC=b"UVA1"`, `_FORMAT_VERSION=1`, `_SALT_LEN=16`, `_IV_LEN=12`, `_HEADER_FMT="!4sH"`, `_PAYLOAD_LEN_FMT="!I"`, `_CHECKSUM_LEN=32`. |
| `_key_path()`, `_load_or_create_key()` | 32-byte master key in `keys/key.bin`, chmod 0o600. |
| `EguFileError` (alias `UvaFileError`) | Exception. |
| `_build_container(plaintext_json)` → bytes | Header + salt + iv + payload_len + ciphertext + SHA-256 trailer. |
| `_parse_container(blob)` → bytes | Validate magic, version, length, checksum, then decrypt. |
| `_try_load_fernet(blob)`, `_is_fernet_token(blob)` | Legacy Fernet decryption support. |
| `save_encrypted(data, path)` | json.dumps → encrypt → atomic write via tmp + os.replace. |
| `_normalize_if_state(data)` | If data looks like a state dict, normalises classes. |
| `load_encrypted(path)` | Try EGU1/UVA1 → Fernet → plain JSON. |
| `save_encrypted_lines(entries, path)`, `load_encrypted_lines(path)`, `append_encrypted_entry(entry, path)` | Array-of-objects helpers. |
| `_OLD_DATA_DIR`, `_old_app_config_path()`, `_backup_original(src)`, `_migrate_json_file(src, dest)`, `_migrate_jsonl_file(src, dest)`, `migrate_legacy_files()` | One-time migration from `~/.class_scheduler/` and root-relative configs. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–37 | docstring | Full file-format spec embedded. |
| 39–51 | imports | |
| 53–99 | path constants + helpers + `ensure_dirs` | Directory tree + legacy-folder copy. |
| 102–149 | well-known paths | |
| 152–202 | key management | `_load_or_create_key` with caching + chmod. |
| 207–238 | `_build_container` | Encrypt + checksum. |
| 241–296 | `_parse_container` | Validate + decrypt. |
| 299–332 | Fernet fallback | Legacy-format support. |
| 335–404 | public save/load API | atomic write; normalize on load. |
| 406–431 | array-of-objects helpers | For feedback log / future array storage. |
| 434–535 | migration helpers | `_backup_original`, `_migrate_json_file`, `_migrate_jsonl_file`, `migrate_legacy_files`. |

## 6. Runtime Behavior
- `ensure_dirs()` called at startup.
- `migrate_legacy_files()` called at startup (idempotent).
- Every save/load is atomic and synchronous.
- The master key is loaded on first use and cached for the process lifetime.

## 7. Data Flow
- Save: Python object → JSON bytes → encrypted container → `<path>.tmp` → `os.replace(tmp, path)`.
- Load: file bytes → validate header → validate checksum → decrypt → JSON parse → optional state normalisation.

## 8. UI Flow
Not applicable directly. Loading messages surface via the file dialogs and the warning panel.

## 9. Error Handling and Edge Cases
- Empty file → `EguFileError("file_empty")`.
- Magic mismatch → `EguFileError("invalid_egu_header")`.
- Wrong version → `EguFileError("unsupported_egu_version")`.
- Checksum mismatch → `EguFileError("egu_checksum_mismatch")`.
- Decrypt failure → `EguFileError("egu_decryption_failed")`.
- Atomic write tmp-cleanup on exception.
- Legacy formats (UVA1, Fernet, plain JSON) all transparently loaded; resaved as EGU1.
- chmod failure (e.g. on Windows) ignored.

## 10. Integration Points
Used by every persistence consumer.

## 11. Risks and Maintenance Notes
- **Never** change the container layout without bumping `_FORMAT_VERSION` AND keeping the old parser available.
- The master key is irreplaceable. If lost, all encrypted files are unrecoverable. Treat `keys/key.bin` like a private key.
- AAD is unused — adding it would be a breaking change.
- `_normalize_if_state` only fires when the loaded dict has a `classes` key — be careful with naming collisions in unrelated dicts.

## 12. Mini Summary
The single persistence layer. Custom binary `.egu` format with AES-256-GCM. Atomic writes. Idempotent legacy migration. Touch with maximum care.
