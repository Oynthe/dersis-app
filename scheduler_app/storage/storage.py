"""Centralized path management and encrypted persistence for Dersis.

All persistent data lives under  ~/Documents/Dersis/  with this layout:

    settings/       app_settings.egu, negotiation_settings.egu
    saves/          timetable_YYYY_MM_DD_HH_MM_SS.egu, autosave.egu
    learning/       learned_weights.egu, preference_model.egu
    logs/           feedback_log.egu, crash_log.txt
    exports/        (user-chosen export location default)
    backups/        old .json originals after migration
    keys/           key.bin

Binary .egu container format (v1):

    Offset  Size    Description
    ─────────────────────────────────────
    0       4       Magic header: b'EGU1'
    4       2       Format version (uint16 big-endian, currently 1)
    6       16      Salt (random per file)
    22      12      IV / nonce (random per file)
    34      4       Payload length (uint32 big-endian)
    38      N       AES-256-GCM encrypted payload (includes 16-byte auth tag)
    38+N    32      SHA-256 checksum of bytes [0..38+N)

Pipeline:
    Save: Python dict → JSON bytes → AES-256-GCM encrypt → container → disk
    Load: disk → validate header → validate checksum → decrypt → JSON parse

Backward compatibility:
    Legacy .uva files (with b'UVA1' magic header), old Fernet-encrypted files,
    and plain JSON files are all transparently loaded.  On save they are written
    in the new .egu format.

Migration:
    On startup, legacy .json/.jsonl files and old Fernet-encrypted files
    are detected, loaded, re-saved as v1 binary .egu, and originals backed up.
"""

import hashlib
import json
import os
import secrets
import shutil
import struct
import sys

from scheduler_app.core.models import normalize_state_classes
from scheduler_app.translations import tr
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── Root directory ────────────────────────────────────────────────────────

_ROOT_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "Dersis")

# Subfolder names
SETTINGS_DIR = "settings"
SAVES_DIR    = "saves"
LEARNING_DIR = "learning"
LOGS_DIR     = "logs"
EXPORTS_DIR  = "exports"
BACKUPS_DIR  = "backups"
KEYS_DIR     = "keys"

_SUBFOLDERS = [
    SETTINGS_DIR, SAVES_DIR, LEARNING_DIR, LOGS_DIR,
    EXPORTS_DIR, BACKUPS_DIR, KEYS_DIR,
]


def root_dir() -> str:
    """Return the Dersis root directory path."""
    return _ROOT_DIR


def sub_dir(name: str) -> str:
    """Return the absolute path of a named subfolder, creating it if needed."""
    path = os.path.join(_ROOT_DIR, name)
    os.makedirs(path, exist_ok=True)
    return path


def ensure_dirs():
    """Create the full directory tree at startup.

    Migrates from the legacy ~/Documents/ClassScheduler/ folder if it exists
    and the new ~/Documents/Dersis/ folder does not.
    """
    _LEGACY_ROOT = os.path.join(os.path.expanduser("~"), "Documents", "ClassScheduler")
    if os.path.isdir(_LEGACY_ROOT) and not os.path.isdir(_ROOT_DIR):
        try:
            shutil.copytree(_LEGACY_ROOT, _ROOT_DIR)
        except Exception:
            pass  # Fall through to create fresh dirs

    for sf in _SUBFOLDERS:
        os.makedirs(os.path.join(_ROOT_DIR, sf), exist_ok=True)


# ── Well-known file paths ─────────────────────────────────────────────────

def _with_legacy_fallback(directory: str, filename_egu: str) -> str:
    """Return path to .egu file, falling back to existing .uva if .egu doesn't exist yet."""
    egu_path = os.path.join(sub_dir(directory), filename_egu)
    if os.path.exists(egu_path):
        return egu_path
    uva_name = filename_egu.replace(".egu", ".uva")
    uva_path = os.path.join(sub_dir(directory), uva_name)
    if os.path.exists(uva_path):
        return uva_path
    return egu_path


def settings_path() -> str:
    """~/Documents/Dersis/settings/app_settings.egu"""
    return _with_legacy_fallback(SETTINGS_DIR, "app_settings.egu")


def negotiation_settings_path() -> str:
    """~/Documents/Dersis/settings/negotiation_settings.egu"""
    return _with_legacy_fallback(SETTINGS_DIR, "negotiation_settings.egu")


def learned_weights_path() -> str:
    """~/Documents/Dersis/learning/learned_weights.egu"""
    return _with_legacy_fallback(LEARNING_DIR, "learned_weights.egu")


def feedback_log_path() -> str:
    """~/Documents/Dersis/logs/feedback_log.egu"""
    return _with_legacy_fallback(LOGS_DIR, "feedback_log.egu")


def crash_log_path() -> str:
    """~/Documents/Dersis/logs/crash_log.txt  (plain text, not encrypted)"""
    return os.path.join(sub_dir(LOGS_DIR), "crash_log.txt")


def autosave_path() -> str:
    """~/Documents/Dersis/saves/autosave.egu"""
    return _with_legacy_fallback(SAVES_DIR, "autosave.egu")


def new_save_path() -> str:
    """Generate a timestamped save path: saves/timetable_YYYY_MM_DD_HH_MM_SS.egu"""
    ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    return os.path.join(sub_dir(SAVES_DIR), f"timetable_{ts}.egu")


# ── Encryption key management ────────────────────────────────────────────

_KEY_FILE = "key.bin"
_cached_key = None

# Binary container constants
_MAGIC = b"EGU1"
_LEGACY_MAGIC = b"UVA1"  # Recognized for backward compatibility
_FORMAT_VERSION = 1
_SALT_LEN = 16
_IV_LEN = 12   # AES-GCM nonce
_HEADER_FMT = "!4sH"  # magic(4) + version(uint16)
_PAYLOAD_LEN_FMT = "!I"  # uint32
_CHECKSUM_LEN = 32  # SHA-256

# ── Append-only log container (ST-PERF-005) ───────────────────────────────
#
# The feedback log used to be one JSON array inside a single .egu, so every
# append decrypted, re-serialised and re-encrypted the whole history: per-append
# cost grew from 2.55 ms at n=1 to 99.9 ms at n=2000, and 2000 appends took
# 108 s. EGL1 is a header followed by independently framed records:
#
#     header : b'EGL1' + uint16 version                          (6 bytes, once)
#     record : uint32 payload length + one complete EGU1 container
#
# Each record carries its own salt, IV, GCM tag and checksum, so a torn tail
# damages exactly one record and everything before it still decrypts. That
# per-record salt is also why records are NOT given a shared salt with a counter
# nonce: GCM nonce reuse across a partially rewritten file is a genuine
# key-recovery risk, and this file shares the master key with the user's saved
# timetables.
_LOG_MAGIC = b"EGL1"
_LOG_VERSION = 1
_LOG_HEADER_FMT = "!4sH"
_LOG_RECLEN_FMT = "!I"


def _key_path() -> str:
    return os.path.join(sub_dir(KEYS_DIR), _KEY_FILE)


def _load_or_create_key() -> bytes:
    """Return the 32-byte AES-256 master key, creating it on first use.

    ST-DATA-001: "the key file is absent" and "the key file is damaged" are
    completely different situations and used to be treated identically. Absent
    means first run — mint a key. Damaged means a partial write or a bad sector
    in a 32-byte file, and minting there silently and permanently orphaned every
    .egu the user had ever saved: the old key was moved to backups/, but the
    *damaged remnant* was what got backed up, so there was nothing to restore.

    A damaged key now raises EguFileError and the file is left exactly as it is,
    so a maintainer or a future recovery path still has the bytes to work with.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    kp = _key_path()

    # Also check for old Fernet key file and migrate it
    old_key_path = os.path.join(sub_dir(KEYS_DIR), "scheduler.key")

    if os.path.exists(kp):
        with open(kp, "rb") as f:
            key = f.read()
        if len(key) == 32:
            _cached_key = key
            return _cached_key
        # Present but not a valid key: never overwrite it, never mint over it.
        raise EguFileError(
            tr("errors.key_file_damaged").format(
                path=kp, size=len(key), backups=sub_dir(BACKUPS_DIR)))

    # Generate a fresh 256-bit key
    key = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(kp), exist_ok=True)
    with open(kp, "wb") as f:
        f.write(key)
    try:
        os.chmod(kp, 0o600)
    except OSError:
        pass
    _cached_key = key
    return _cached_key


# ── Binary container save / load ──────────────────────────────────────────

class EguFileError(Exception):
    """Raised when an .egu/.uva file is invalid, corrupted, or undecryptable."""
    pass


# Backward-compatible alias so existing except clauses still work
UvaFileError = EguFileError


def _build_container(plaintext_json: bytes) -> bytes:
    """Build the binary .egu container from JSON plaintext bytes.

    Returns the complete binary blob ready to write to disk.
    """
    key = _load_or_create_key()
    salt = secrets.token_bytes(_SALT_LEN)
    iv = secrets.token_bytes(_IV_LEN)

    # Derive a per-file key from master key + salt using HKDF-like construction
    derived = hashlib.sha256(key + salt).digest()
    aesgcm = AESGCM(derived)
    ciphertext = aesgcm.encrypt(iv, plaintext_json, None)  # includes 16-byte tag

    payload_len = len(ciphertext)

    # Build header + body (everything except final checksum)
    header = struct.pack(_HEADER_FMT, _MAGIC, _FORMAT_VERSION)
    payload_len_bytes = struct.pack(_PAYLOAD_LEN_FMT, payload_len)
    body = header + salt + iv + payload_len_bytes + ciphertext

    # SHA-256 checksum of everything before the checksum
    checksum = hashlib.sha256(body).digest()
    return body + checksum


def _parse_container(blob: bytes) -> bytes:
    """Parse and validate a binary .egu/.uva container, returning decrypted JSON bytes.

    Accepts both EGU1 (current) and UVA1 (legacy) magic headers.
    Raises EguFileError with a user-friendly message on any failure.
    """
    header_size = struct.calcsize(_HEADER_FMT)
    min_size = header_size + _SALT_LEN + _IV_LEN + 4 + _CHECKSUM_LEN

    if len(blob) < min_size:
        raise EguFileError(tr("errors.egu_file_too_small"))

    # Parse header — accept both current and legacy magic
    magic, version = struct.unpack_from(_HEADER_FMT, blob, 0)
    if magic not in (_MAGIC, _LEGACY_MAGIC):
        raise EguFileError(tr("errors.invalid_egu_header"))
    if version != _FORMAT_VERSION:
        raise EguFileError(
            tr("errors.unsupported_egu_version").format(
                version=version, supported=_FORMAT_VERSION))

    offset = header_size
    salt = blob[offset:offset + _SALT_LEN]
    offset += _SALT_LEN
    iv = blob[offset:offset + _IV_LEN]
    offset += _IV_LEN
    payload_len = struct.unpack_from(_PAYLOAD_LEN_FMT, blob, offset)[0]
    offset += struct.calcsize(_PAYLOAD_LEN_FMT)

    if offset + payload_len + _CHECKSUM_LEN != len(blob):
        raise EguFileError(
            tr("errors.egu_corrupted_unexpected_size"))

    ciphertext = blob[offset:offset + payload_len]
    offset += payload_len
    stored_checksum = blob[offset:offset + _CHECKSUM_LEN]

    # Validate checksum (covers everything before the checksum)
    body = blob[:offset]
    computed_checksum = hashlib.sha256(body).digest()
    if stored_checksum != computed_checksum:
        raise EguFileError(
            tr("errors.egu_checksum_mismatch"))

    # Decrypt
    key = _load_or_create_key()
    derived = hashlib.sha256(key + salt).digest()
    aesgcm = AESGCM(derived)
    try:
        plaintext = aesgcm.decrypt(iv, ciphertext, None)
    except Exception:
        raise EguFileError(
            tr("errors.egu_decryption_failed"))

    return plaintext


# ── Old Fernet format detection and loading ───────────────────────────────

def _try_load_fernet(blob: bytes) -> bytes:
    """Attempt to decrypt a blob using the old Fernet format.

    Returns decrypted plaintext bytes, or raises on failure.
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise EguFileError(tr("errors.legacy_fernet_crypto_missing"))

    old_key_path = os.path.join(sub_dir(KEYS_DIR), "scheduler.key")
    if not os.path.exists(old_key_path):
        raise EguFileError(tr("errors.legacy_fernet_key_missing"))

    with open(old_key_path, "rb") as f:
        fernet_key = f.read().strip()

    fernet = Fernet(fernet_key)
    plaintext = fernet.decrypt(blob)
    return plaintext


def _is_fernet_token(blob: bytes) -> bool:
    """Heuristic check: Fernet tokens are base64-encoded and start with version byte."""
    if len(blob) < 10:
        return False
    # Fernet tokens are URL-safe base64 — all printable ASCII
    try:
        blob[:80].decode("ascii")
        return True
    except (UnicodeDecodeError, ValueError):
        return False


# ── Public save / load API ────────────────────────────────────────────────

def save_encrypted(data, path: str) -> None:
    """Serialize *data* as JSON, encrypt into .egu binary container, write to disk."""
    plaintext = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    container = _build_container(plaintext)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Write atomically via temp file
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(container)
        # On Windows, os.replace is atomic
        os.replace(tmp, path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _normalize_if_state(data):
    """Normalize class data in-place if data looks like a scheduler state dict."""
    if isinstance(data, dict) and "classes" in data:
        normalize_state_classes(data)
    return data


def load_encrypted(path: str):
    """Read an encrypted .egu/.uva file and return the deserialized data.

    Supports the current EGU1 binary format, legacy UVA1 binary format,
    legacy Fernet format, and plain JSON.
    Raises FileNotFoundError if the file does not exist.
    Raises EguFileError with a friendly message on validation/decryption failure.
    """
    with open(path, "rb") as f:
        blob = f.read()

    if not blob:
        raise EguFileError(tr("errors.file_empty"))

    # Try binary container format (current EGU1 or legacy UVA1)
    if blob[:4] in (_MAGIC, _LEGACY_MAGIC):
        plaintext = _parse_container(blob)
        return _normalize_if_state(json.loads(plaintext.decode("utf-8")))

    # Try legacy Fernet format
    if _is_fernet_token(blob):
        try:
            plaintext = _try_load_fernet(blob)
            data = json.loads(plaintext.decode("utf-8"))
            # Auto-upgrade: re-save in new format
            try:
                save_encrypted(data, path)
            except Exception:
                pass  # Best-effort upgrade
            return _normalize_if_state(data)
        except Exception:
            raise EguFileError(
                tr("errors.egu_could_not_decrypt"))

    # Try plain JSON (legacy unencrypted file)
    try:
        return _normalize_if_state(json.loads(blob.decode("utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise EguFileError(tr("errors.unrecognized_file_format"))


def _log_record(entry) -> bytes:
    """One framed, independently encrypted log record."""
    container = _build_container(
        json.dumps(entry, ensure_ascii=False).encode("utf-8"))
    return struct.pack(_LOG_RECLEN_FMT, len(container)) + container


def _write_log(entries: list, path: str) -> None:
    """Write a whole EGL1 log atomically (creation, migration, compaction)."""
    blob = bytearray(struct.pack(_LOG_HEADER_FMT, _LOG_MAGIC, _LOG_VERSION))
    for entry in entries:
        blob += _log_record(entry)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(bytes(blob))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _read_log_records(blob: bytes) -> list:
    """Decode an EGL1 blob. A damaged tail costs only the records after it."""
    header_size = struct.calcsize(_LOG_HEADER_FMT)
    magic, version = struct.unpack_from(_LOG_HEADER_FMT, blob, 0)
    if magic != _LOG_MAGIC:
        raise EguFileError(tr("errors.invalid_egu_header"))
    if version != _LOG_VERSION:
        raise EguFileError(
            tr("errors.unsupported_egu_version").format(
                version=version, supported=_LOG_VERSION))
    entries = []
    off = header_size
    len_size = struct.calcsize(_LOG_RECLEN_FMT)
    while off + len_size <= len(blob):
        (rec_len,) = struct.unpack_from(_LOG_RECLEN_FMT, blob, off)
        off += len_size
        if rec_len <= 0 or off + rec_len > len(blob):
            break  # torn tail: keep every complete record before it
        entries.append(json.loads(
            _parse_container(blob[off:off + rec_len]).decode("utf-8")))
        off += rec_len
    return entries


def save_encrypted_lines(entries: list, path: str) -> None:
    """Write *entries* as an append-only encrypted log."""
    _write_log(list(entries), path)


def load_encrypted_lines(path: str) -> list:
    """Load an encrypted entry log, in file order.

    Reads the EGL1 append-only format, and transparently falls back to the
    legacy single-array .egu/.uva/Fernet/plain-JSON forms so a log written by an
    older build keeps loading.

    Returns an empty list if the file doesn't exist.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            blob = f.read()
        if blob[:4] == _LOG_MAGIC:
            return _read_log_records(blob)
        data = load_encrypted(path)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def append_encrypted_entry(entry: dict, path: str) -> None:
    """Append one entry. O(1) in bytes written AND bytes read.

    ST-PERF-005: this used to load the whole log, append in memory and rewrite
    it, which is what made 2000 appends cost 108 s.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except FileNotFoundError:
        _write_log([entry], path)
        return

    if head == _LOG_MAGIC:
        # The hot path: a plain append. Deliberately NOT the temp-file dance —
        # rewriting the file here would give back the O(n) this exists to remove.
        with open(path, "ab") as f:
            f.write(_log_record(entry))
            f.flush()
            os.fsync(f.fileno())
        return

    # A legacy single-array log, or an unreadable one. Convert once.
    existing = load_encrypted_lines(path)
    if not existing and os.path.getsize(path) > 0:
        # Unreadable: preserve the bytes rather than overwrite them, the same
        # way a corrupt settings container is handled (ST-DATA-014/ST-DATA-002).
        quarantine_corrupt(path)
        _write_log([entry], path)
        return
    _write_log(existing + [entry], path)


def log_size(path: str) -> int:
    """Size of the log in bytes, or 0 if absent. A stat, not a read."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def log_entry_count(path: str) -> int:
    """Number of records, without reading or decrypting their payloads.

    Seeks over each record rather than reading the file in: only the 4-byte
    length prefixes are ever read, so counting a large log stays cheap.
    """
    if not os.path.exists(path):
        return 0
    len_size = struct.calcsize(_LOG_RECLEN_FMT)
    try:
        with open(path, "rb") as f:
            if f.read(4) != _LOG_MAGIC:
                return len(load_encrypted_lines(path))
            f.seek(struct.calcsize(_LOG_HEADER_FMT))
            total = log_size(path)
            count = 0
            while True:
                raw = f.read(len_size)
                if len(raw) < len_size:
                    break
                (rec_len,) = struct.unpack(_LOG_RECLEN_FMT, raw)
                if rec_len <= 0 or f.tell() + rec_len > total:
                    break
                f.seek(rec_len, 1)
                count += 1
            return count
    except OSError:
        return 0


def load_encrypted_lines_since(path: str, skip: int) -> list:
    """Records after the first *skip* of them, decrypting only those.

    ST-PERF-005: ``PreferenceLearner.learn()`` runs after every manual move and
    at every launch. Slicing the tail out of a full read would still decrypt the
    whole history, which is the cost being removed.
    """
    if skip <= 0:
        return load_encrypted_lines(path)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return []
    if blob[:4] != _LOG_MAGIC:
        return load_encrypted_lines(path)[skip:]
    entries = []
    off = struct.calcsize(_LOG_HEADER_FMT)
    len_size = struct.calcsize(_LOG_RECLEN_FMT)
    seen = 0
    try:
        while off + len_size <= len(blob):
            (rec_len,) = struct.unpack_from(_LOG_RECLEN_FMT, blob, off)
            off += len_size
            if rec_len <= 0 or off + rec_len > len(blob):
                break
            if seen >= skip:
                entries.append(json.loads(
                    _parse_container(blob[off:off + rec_len]).decode("utf-8")))
            seen += 1
            off += rec_len
    except Exception:
        return []
    return entries


# ── Migration helpers ────────────────────────────────────────────────────

_OLD_DATA_DIR = os.path.join(os.path.expanduser("~"), ".class_scheduler")


def _app_dir() -> str:
    """The directory DERSİS was installed into — where ``scheduler_gui.py`` sits.

    ST-ARCH-001 item 6. This used to resolve to ``os.path.dirname(__file__)``,
    which is ``{app}/scheduler_app/storage`` — two levels below the "app
    directory" the caller's docstring names, and a place the pre-Dersis
    ``scheduler_config.json`` can never be. Measured: with the legacy payload
    written beside ``scheduler_gui.py``, ``migrate_legacy_files()`` returned
    ``[]`` and wrote no settings.

    ``sys.executable`` is not the anchor either on the build the installer
    ships: ``build_embed.bat`` compiles ``Dersis.exe`` as a C# wrapper that runs
    ``{app}\\python\\pythonw.exe {app}\\scheduler_gui.py``, so ``sys.frozen`` is
    never set and ``dirname(sys.executable)`` is ``{app}\\python``. The package
    root is what tracks the app directory in every layout this project ships:
    this module is always ``{app}/scheduler_app/storage/storage.py``.

    The ``sys.frozen`` branch is kept for a PyInstaller-style bundle, where the
    package is unpacked into a temporary ``_MEIPASS`` and the executable's own
    directory is the only meaningful anchor.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _old_app_config_path() -> str:
    """Legacy config location: scheduler_config.json in the app directory."""
    return os.path.join(_app_dir(), "scheduler_config.json")


def _old_app_config_candidates() -> list[str]:
    """Both documented homes of the pre-Dersis ``scheduler_config.json``.

    The app directory is what this module has always claimed;
    ``~/.class_scheduler`` is what
    ``dersis-mapped/09_SETTINGS_LOCALIZATION_AND_PERSISTENCE_MAP.md`` documents
    and where the other four legacy files live. Nobody now has a build that can
    say which one the pre-Dersis release really used, so both are checked. The
    basename is fixed, so neither candidate can pick up anything else.
    """
    return [_old_app_config_path(),
            os.path.join(_OLD_DATA_DIR, "scheduler_config.json")]


def _backup_original(src: str) -> None:
    """Move a file into the backups/ folder, preserving its name."""
    if not os.path.exists(src):
        return
    dst = os.path.join(sub_dir(BACKUPS_DIR), os.path.basename(src))
    if os.path.exists(dst):
        base, ext = os.path.splitext(os.path.basename(src))
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        dst = os.path.join(sub_dir(BACKUPS_DIR), f"{base}_{ts}{ext}")
    shutil.move(src, dst)


def _try_backup_original(src: str) -> bool:
    """``_backup_original`` that reports failure instead of raising.

    ``migrate_legacy_files()`` runs before any window exists — from
    ``scheduler_gui.main()`` and again from ``SchedulerApp.__init__`` — and both
    callers assume it returns. The one call that used to sit outside a migrator's
    ``try`` was the ``_backup_original`` in the destination-already-exists
    branch; measured with an ``msvcrt.locking`` byte-lock held on a legacy
    ``learned_weights.json``, that raised ``PermissionError [WinError 33]``
    straight out of ``main()``, so the user got a crash box and no window.

    Nothing is discarded when this returns ``False``: ``shutil.move`` either
    moves the file or leaves it exactly where it was (ST-DATA-001). The legacy
    file stays put, the next launch retries, and the app starts either way.
    """
    try:
        _backup_original(src)
        return True
    except Exception:
        return False


def quarantine_corrupt(src: str) -> str:
    """Move an unreadable container into ``backups/`` and return its new path.

    ST-DATA-014. Distinct from :func:`_backup_original`, which keeps the
    original basename: a quarantined file has to be *distinguishable* from a
    healthy backup, so it gets a ``_corrupt_<timestamp>`` infix. Nothing is ever
    deleted — the bytes are the user's schedule, and a future recovery path may
    still be able to read them.

    Raises whatever ``shutil.move`` raises; a caller that cannot move the file
    must not pretend it recovered.
    """
    base, ext = os.path.splitext(os.path.basename(src))
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    dst = os.path.join(sub_dir(BACKUPS_DIR), f"{base}_corrupt_{ts}{ext}")
    shutil.move(src, dst)
    return dst


def _migrate_json_file(src: str, dest_sav: str) -> bool:
    """Load a plain JSON file, encrypt it to *dest_sav*, and back up original."""
    if not os.path.exists(src):
        return False
    if os.path.exists(dest_sav):
        _try_backup_original(src)
        return False
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        save_encrypted(data, dest_sav)
        _backup_original(src)
        return True
    except Exception:
        return False


def _migrate_jsonl_file(src: str, dest_sav: str) -> bool:
    """Load a JSONL file, encrypt as array .egu."""
    if not os.path.exists(src):
        return False
    if os.path.exists(dest_sav):
        _try_backup_original(src)
        return False
    try:
        entries = []
        with open(src, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        save_encrypted(entries, dest_sav)
        _backup_original(src)
        return True
    except Exception:
        return False


def migrate_legacy_files() -> list:
    """Migrate all known legacy files to encrypted .egu storage.

    Call once at startup.  Returns a list of human-readable migration notes.
    """
    notes = []

    # 1. scheduler_config.json → settings/app_settings.egu
    for old_cfg in _old_app_config_candidates():
        if _migrate_json_file(old_cfg, settings_path()):
            notes.append(
                f"Migrated {os.path.basename(old_cfg)} → settings/app_settings.egu")
            break

    # 2. ~/.class_scheduler/learned_weights.json → learning/learned_weights.egu
    old_weights = os.path.join(_OLD_DATA_DIR, "learned_weights.json")
    if _migrate_json_file(old_weights, learned_weights_path()):
        notes.append("Migrated learned_weights.json → learning/learned_weights.egu")

    # 3. ~/.class_scheduler/feedback_log.jsonl → logs/feedback_log.egu
    old_log = os.path.join(_OLD_DATA_DIR, "feedback_log.jsonl")
    if _migrate_jsonl_file(old_log, feedback_log_path()):
        notes.append("Migrated feedback_log.jsonl → logs/feedback_log.egu")

    # 4. ~/.class_scheduler/negotiation_settings.json → settings/negotiation_settings.egu
    old_neg = os.path.join(_OLD_DATA_DIR, "negotiation_settings.json")
    if _migrate_json_file(old_neg, negotiation_settings_path()):
        notes.append("Migrated negotiation_settings.json → settings/negotiation_settings.egu")

    # 5. ~/.class_scheduler/crash_log.txt → logs/crash_log.txt (plain copy)
    old_crash = os.path.join(_OLD_DATA_DIR, "crash_log.txt")
    if os.path.exists(old_crash) and not os.path.exists(crash_log_path()):
        try:
            shutil.copy2(old_crash, crash_log_path())
            _backup_original(old_crash)
            notes.append("Migrated crash_log.txt → logs/crash_log.txt")
        except Exception:
            pass

    return notes
