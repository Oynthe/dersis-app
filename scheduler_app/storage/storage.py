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
from typing import Iterator, NamedTuple, Optional, Tuple

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

# The smallest byte count a real EGU1 container can occupy: header + salt + IV +
# payload length + checksum, with a zero-length payload. Any length prefix
# claiming less than this is damage, whatever else it looks like.
_MIN_CONTAINER = (struct.calcsize(_HEADER_FMT) + _SALT_LEN + _IV_LEN
                  + struct.calcsize(_PAYLOAD_LEN_FMT) + _CHECKSUM_LEN)


class LogRead(NamedTuple):
    """What one log read produced, and what it could not read.

    A plain tuple so ``load_encrypted_lines`` can stay a one-line wrapper and
    every existing caller keeps its list. ``lost`` is 0 for a healthy or absent
    log, the number of records that would not decode for a damaged one, and -1
    when the file could not be identified as a log at all (bad magic,
    unsupported version, an unreadable legacy container) — there is no framing
    left to count with, and reporting 0 there would be the ST-DATA-002 lie in a
    new place.

    ``lost`` is a floor, not an exact count: a torn tail is one loss however
    many records it swallowed, because the framing that would have counted them
    is exactly what is missing. Nothing user-facing may print it as a number.

    The one shape this cannot see, stated so the sentence above is not read as
    more than it is: a truncation landing EXACTLY on a record boundary leaves a
    file that is byte-for-byte a shorter healthy log, and no reader of this
    format can tell it from one — the header carries no record count. Measured
    over every truncation length of a 6-record log: 6 such lengths, one per
    boundary, against 24 silently-lost lengths before the tail accounting was
    added. Putting a count in the EGL1 header would close it, at the price of
    turning the O(1) append into a read-modify-write of the header and creating
    a window where the count and the records disagree after a crash; that trade
    was not taken.
    """
    entries: list
    lost: int


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
    # Owner-only on the platforms where that means something, and a measured
    # no-op on the platform most users are on.
    #
    # Windows: this does nothing. Measured — st_mode is 0o100666 before the
    # call and 0o100666 after, and `icacls` reports the same inherited DACL
    # (SYSTEM:(F), BUILTIN\Administrators:(F), OWNER RIGHTS:(F)) either way.
    # CPython maps os.chmod onto the FAT read-only attribute alone, so the only
    # bit it can move is write-vs-read (0o400 does turn st_mode into 0o100444);
    # the 0o077 half of the mode has no representation to land in. The key file
    # is protected here by living under the user's profile, not by this line.
    #
    # POSIX: it does exactly what it says, and both platforms that run this in
    # anger are POSIX — the ubuntu-latest CI runner and macOS — so the call is
    # load-bearing there and stays.
    #
    # Deliberately NOT replaced with a Windows ACL. Writing an explicit DACL
    # would add a permanent new way for first-run to fail (roaming profiles,
    # redirected folders, non-English SIDs) in exchange for no measured gain
    # over the profile directory's own inherited permissions.
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
    """Is this blob a legacy Fernet token?

    ST-FUNC-007. This used to ask "do the first 80 bytes decode as ASCII",
    which is true of every plain-JSON file ever written in ASCII. So a legacy
    unencrypted save was routed here and rejected as undecryptable, while the
    SAME file with a Turkish letter in its first 80 bytes fell through to the
    plain-JSON branch and loaded. The docstring already claimed the check was
    about the version byte; it never was.

    A Fernet token is urlsafe-base64 of ``0x80`` + an 8-byte big-endian
    timestamp + IV + ciphertext + HMAC. The version byte is fixed at ``0x80``
    by the spec and the top four timestamp bytes stay zero until 2106, so the
    first six characters are literally ``gAAAAA``. Measured against tokens
    generated with this venv's ``cryptography``: version byte 0x80, and 20 of
    20 tokens began ``gAAAAABq``.

    Anything this now rejects falls through to the plain-JSON branch below,
    which either parses it or reports ``unrecognized_file_format`` — a more
    accurate story than ``egu_could_not_decrypt`` for a file that was never
    encrypted.
    """
    token = blob.strip()
    return len(token) >= 10 and token.startswith(b"gAAAAA")


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


def _walk_log_frames(blob: bytes) -> Iterator[Optional[Tuple[int, int]]]:
    """Walk the record frames of an EGL1 blob, resyncing on the EGU1 magic.

    THE one framing rule for this format. Every walk over an EGL1 log goes
    through here — ``_read_log_records_report``, ``log_entry_count`` and
    ``load_encrypted_lines_since_report`` — because the learner's cursor is a
    *frame index* produced by one of them and consumed by another, so any
    disagreement about what counts as a frame makes it re-learn or skip
    entries.

    Yields ``(payload_start, payload_len)`` for every structurally intact
    frame, in file order, and ``None`` once for each span that had to be
    skipped. A ``None`` is NOT a frame: it never advances the cursor unit.

    Resyncing on the magic instead of trusting the length prefix is what makes
    a damaged prefix survivable. The old walk advanced by the *claimed* length,
    so one flipped bit in a prefix desynchronised it permanently and every
    later record became unreachable — measured on a 6-record log, a single bit
    flipped in record 2's prefix took the count from 6 to 3 and froze it there
    across every subsequent append. Scanning forward to the next ``EGU1`` start
    recovers the records after the damage instead.

    A resync cannot INVENT a record: a frame is only yielded when its payload
    begins with a container magic and is at least ``_MIN_CONTAINER`` bytes, and
    the caller still has to get past that container's SHA-256 and AES-GCM tag.
    Measured with an exhaustive single-bit sweep over a healthy log: zero
    fabricated entries.

    The forward scan runs ONLY after a frame fails to check out. A healthy log
    is still walked by seeking from one prefix to the next — eight bytes read
    per record instead of four, no scanning — so ``log_entry_count`` keeps the
    cost ST-PERF-005 exists for.

    Termination: a yielded frame advances ``off`` by at least
    ``len_size + _MIN_CONTAINER``, and a resync advances it by at least 1
    because the search starts past the current frame's own payload magic.
    """
    len_size = struct.calcsize(_LOG_RECLEN_FMT)
    magic_size = len(_MAGIC)
    off = struct.calcsize(_LOG_HEADER_FMT)
    end = len(blob)
    while off + len_size <= end:
        (rec_len,) = struct.unpack_from(_LOG_RECLEN_FMT, blob, off)
        start = off + len_size
        if (rec_len >= _MIN_CONTAINER and start + rec_len <= end
                and blob[start:start + magic_size] in (_MAGIC, _LEGACY_MAGIC)):
            # A frame: the payload is a container start and it fits. Whether the
            # container itself decrypts is the caller's business, not framing's.
            yield (start, rec_len)
            after = start + rec_len
            if (after == end
                    or blob[after + len_size:after + len_size + magic_size]
                    in (_MAGIC, _LEGACY_MAGIC)):
                off = after  # the claimed length lands on the next record
                continue
            # It does not, and there are two ways that happens. Deciding
            # between them is the difference between reporting a lost record
            # and hiding one.
            #
            # (a) THIS record's length prefix is damaged, so `after` points
            #     nowhere in particular. Resyncing on the next magic skips only
            #     garbage, and nothing is lost that was not already counted.
            # (b) The NEXT record's container magic is damaged. Then `after` is
            #     the true next prefix and the length there is intact — so the
            #     resync below would jump clean over a whole real record and
            #     report nothing. That is ST-DATA-002's silence, and the first
            #     version of this walk had it: measured on a 6-record log, a
            #     single bit flipped in the magic of records 1-4 returned 5
            #     records with `lost == 0`, where the reader this replaced
            #     reported `lost == 1`.
            #
            # Tell them apart by asking whether `after` still parses as a
            # well-formed prefix whose record ends on a record start (or on the
            # end of the file). If it does, we are in case (b): count the
            # record whose magic is gone and carry on from the one after it.
            # Where both readings are possible, this picks the one that REPORTS
            # a loss -- for a finding about silence, the conservative direction
            # is to speak up.
            if after + len_size <= end:
                (nxt_len,) = struct.unpack_from(_LOG_RECLEN_FMT, blob, after)
                nxt_after = after + len_size + nxt_len
                if (nxt_len >= _MIN_CONTAINER and nxt_after <= end
                        and (nxt_after == end
                             or blob[nxt_after + len_size:
                                     nxt_after + len_size + magic_size]
                             in (_MAGIC, _LEGACY_MAGIC))):
                    yield None
                    off = nxt_after
                    continue
            already_lost = False
        else:
            # This prefix cannot be trusted — it may itself be the damaged
            # bytes. Whatever it framed is unreadable: count one loss.
            yield None
            already_lost = True
        nxt = blob.find(_MAGIC, start + 1)
        if nxt < len_size:  # not found, or no room for a length prefix ahead of it
            if not already_lost:
                # Bytes follow this record but no record start does: the rest of
                # the file is a torn or damaged tail, not a clean end.
                yield None
            return
        off = nxt - len_size
    if off < end:
        # 1-3 bytes left over: a record was torn away mid-prefix. Falling out of
        # the loop here used to report a clean end, so a truncation landing in
        # that 4-byte window swallowed whole records and reported lost == 0 —
        # the ST-DATA-002 lie in a new place, and a direct contradiction of the
        # ``LogRead`` contract ("a torn tail is one loss however many records it
        # swallowed"). Measured over every truncation of a 6-record log: 24
        # silent-loss lengths before, 6 after (the 6 that land exactly on a
        # record boundary, which no reader of this format can detect), with
        # false alarms staying at 0.
        yield None


def _read_log_records(blob: bytes) -> list:
    """Decode an EGL1 blob, keeping every record that still decrypts."""
    return _read_log_records_report(blob).entries


def _read_log_records_report(blob: bytes) -> LogRead:
    """Decode an EGL1 blob. Returns ``(entries, lost)``.

    ST-DATA-002. The old reader kept a torn *tail* but let damage anywhere
    else out as an exception, and the caller one frame up turned that into
    ``[]`` — so one flipped bit in record 0 cost all 12 records. Measured on a
    3-record log: 0 recovered, where 2 were intact and framed.

    Skipping a bad record is safe because of the framing, not in spite of it:
    every record carries its own length prefix, EGU1 magic, SHA-256 and
    AES-GCM tag, so a reader that resyncs can lose records but cannot invent
    one — a window of arbitrary bytes fails all three checks. Measured with an
    exhaustive single-bit sweep over a healthy 6-record log (5,856 flips): zero
    fabricated entries.

    Framing is delegated to :func:`_walk_log_frames`, which is shared with
    ``log_entry_count`` and ``load_encrypted_lines_since_report`` so all three
    agree on what a frame is.

    ``lost`` is a floor, not a count: a torn tail is one loss however many
    records it swallowed, because the framing that would have counted them is
    exactly what is missing.
    """
    magic, version = struct.unpack_from(_LOG_HEADER_FMT, blob, 0)
    if magic != _LOG_MAGIC:
        raise EguFileError(tr("errors.invalid_egu_header"))
    if version != _LOG_VERSION:
        raise EguFileError(
            tr("errors.unsupported_egu_version").format(
                version=version, supported=_LOG_VERSION))
    entries = []
    lost = 0
    for frame in _walk_log_frames(blob):
        if frame is None:
            lost += 1  # a skipped span: a torn tail, or a frame we resynced past
            continue
        start, rec_len = frame
        try:
            entries.append(json.loads(
                _parse_container(blob[start:start + rec_len]).decode("utf-8")))
        except Exception:
            lost += 1  # one unreadable record; the next frame is still framed
    return LogRead(entries, lost)


def save_encrypted_lines(entries: list, path: str) -> None:
    """Write *entries* as an append-only encrypted log."""
    _write_log(list(entries), path)


def load_encrypted_lines(path: str) -> list:
    """Load an encrypted entry log, in file order.

    Reads the EGL1 append-only format, and transparently falls back to the
    legacy single-array .egu/.uva/Fernet/plain-JSON forms so a log written by an
    older build keeps loading.

    Returns an empty list if the file doesn't exist. A DAMAGED log returns the
    records that still decrypt; use :func:`load_encrypted_lines_report` when the
    caller needs to know that anything was lost.
    """
    return load_encrypted_lines_report(path).entries


def load_encrypted_lines_report(path: str) -> LogRead:
    """``load_encrypted_lines`` that also reports what it could not read.

    ST-DATA-002. Deliberately still does not raise, and this is the whole
    reason the fix is shaped this way: ``append_encrypted_entry`` calls this on
    its conversion branch and depends on getting a *value*, because an empty
    value is what routes an unreadable log to ``quarantine_corrupt`` instead of
    letting it be overwritten. Measured with a raising variant substituted: the
    append raised ``EguFileError``, ``FeedbackLogger._write_entry``
    (``learning/feedback_logger.py``) swallowed it with ``except Exception:
    pass``, and ``backups/`` stayed empty — a silent learning outage traded for
    a silent write outage with the quarantine lost too. The loss is reported
    through the return value instead, and ``ui/app.py`` turns it into the same
    user-facing message a corrupt settings container gets.
    """
    if not os.path.exists(path):
        return LogRead([], 0)
    try:
        with open(path, "rb") as f:
            blob = f.read()
        if blob[:4] == _LOG_MAGIC:
            return _read_log_records_report(blob)
        data = load_encrypted(path)
        return LogRead(data, 0) if isinstance(data, list) else LogRead([], -1)
    except Exception:
        return LogRead([], -1)


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
    """Number of record frames, without reading or decrypting their payloads.

    Seeks over each record rather than reading the file in: on a healthy log
    only the 4-byte length prefix and the 4-byte container magic of each record
    are ever read, so counting a large log stays cheap.

    The magic check is not decoration. Without it this walk trusted the length
    prefix, which is exactly the byte that may be damaged: one flipped bit in a
    prefix made the count collapse (6 -> 3 on a measured 6-record log) and stay
    collapsed through every later append, because the learner's gate is
    ``learned_through >= log_entry_count(...)`` and the count could never grow
    past the damage again. Learning died silently and permanently.

    When a frame does not check out, counting past it needs the resync walk,
    and it MUST be the SAME walk ``load_encrypted_lines_since_report`` uses —
    the number returned here is the unit that function's ``skip`` is expressed
    in. So the damaged path reads the file in and defers to
    :func:`_walk_log_frames` rather than reimplementing the resync.
    """
    if not os.path.exists(path):
        return 0
    len_size = struct.calcsize(_LOG_RECLEN_FMT)
    magic_size = len(_MAGIC)
    try:
        header_size = struct.calcsize(_LOG_HEADER_FMT)
        with open(path, "rb") as f:
            head = f.read(header_size)
            # BOTH halves of the header, not just the magic. Checking only the
            # magic let this function count records in a file the reader
            # refuses: `_read_log_records_report` raises on an unsupported
            # version and `load_encrypted_lines_report` turns that into
            # `LogRead([], -1)`, so a single flipped bit in the VERSION byte
            # gave count 6 against 0 records readable. Measured over all 48
            # single-bit flips of the 6-byte header: the 32 magic flips already
            # agreed (0/0), the 16 version flips all disagreed (6/0).
            if (len(head) < header_size
                    or struct.unpack_from(_LOG_HEADER_FMT, head, 0)
                    != (_LOG_MAGIC, _LOG_VERSION)):
                return len(load_encrypted_lines(path))
            total = log_size(path)
            count = 0
            while True:
                raw = f.read(len_size)
                if not raw:
                    return count  # exact EOF: the seek walk consumed the file
                if len(raw) < len_size:
                    # 1-3 bytes left. That is a torn tail on a healthy log, but
                    # it is ALSO what an inflated length prefix looks like: a
                    # rec_len that still clears _MIN_CONTAINER and still fits
                    # inside the file seeks past every later record and leaves a
                    # short read at the end. Measured on a 6-record log
                    # (pad=98): one flipped bit took record 0's length from 201
                    # to 1225, which lands 1 byte from EOF, and this path
                    # returned 1 where the shared walk recovers all 6. Returning
                    # here is only safe at an exact EOF, so anything else defers.
                    break
                (rec_len,) = struct.unpack(_LOG_RECLEN_FMT, raw)
                start = f.tell()
                if rec_len < _MIN_CONTAINER or start + rec_len > total:
                    break
                if f.read(magic_size) not in (_MAGIC, _LEGACY_MAGIC):
                    break
                f.seek(start + rec_len)
                count += 1
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return 0
    return sum(1 for frame in _walk_log_frames(blob) if frame is not None)


def load_encrypted_lines_since(path: str, skip: int) -> list:
    """Records after the first *skip* of them, decrypting only those.

    ST-PERF-005: ``PreferenceLearner.learn()`` runs after every manual move and
    at every launch. Slicing the tail out of a full read would still decrypt the
    whole history, which is the cost being removed.

    A DAMAGED log returns the records after *skip* that still decrypt; use
    :func:`load_encrypted_lines_since_report` when the caller needs to know
    that anything was lost.
    """
    return load_encrypted_lines_since_report(path, skip).entries


def load_encrypted_lines_since_report(path: str, skip: int) -> LogRead:
    """``load_encrypted_lines_since`` that reports what it could not read.

    ST-DATA-002, second half. This function had the identical blanket
    ``except Exception: return []`` around its whole loop, and it disagreed
    with ``load_encrypted_lines`` about the same file: measured on a 3-record
    log with one bit flipped inside record 0, ``since(path, 1)`` returned
    ``[{'b': 2}, {'c': 3}]`` (record 0 is skipped, so it is never decrypted and
    never noticed) while ``load_encrypted_lines`` returned ``[]``.
    """
    if skip <= 0:
        return load_encrypted_lines_report(path)
    if not os.path.exists(path):
        return LogRead([], 0)
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return LogRead([], -1)
    if blob[:4] != _LOG_MAGIC:
        whole = load_encrypted_lines_report(path)
        return LogRead(whole.entries[skip:], whole.lost)
    entries = []
    lost = 0
    seen = 0
    # Counts FRAMES, not decoded records, so the learner's cursor stays in the
    # same unit as log_entry_count() — which walks the same _walk_log_frames()
    # and cannot decrypt — even when a record in the middle is unreadable. The
    # two used to be separate loops that could disagree on a damaged file; they
    # are now one walk, so they cannot drift and make the learner re-learn or
    # skip entries.
    for frame in _walk_log_frames(blob):
        if frame is None:
            lost += 1  # a skipped span is a loss, never a frame
            continue
        start, rec_len = frame
        if seen >= skip:
            try:
                entries.append(json.loads(
                    _parse_container(blob[start:start + rec_len]).decode("utf-8")))
            except Exception:
                lost += 1
        seen += 1
    return LogRead(entries, lost)


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
