"""Persistence round-trip, encryption, and corruption behavior for ``storage.py``.

Every save the user makes goes through ``storage.save_encrypted`` into a binary
``.egu`` container (``EGU1`` magic + salt + IV + AES-256-GCM payload + SHA-256
checksum) and comes back through ``storage.load_encrypted``.  This module is the
safety net for that pipeline.  It covers three separate concerns:

* **fidelity** — everything the user typed (Turkish characters, nested structures,
  empty containers, placements) must survive a save/load cycle byte-for-byte
  equal at the Python-object level;
* **opacity + integrity** — the bytes on disk must not contain the plaintext,
  and any damage to them must be *detected* (including damage that carries a
  valid checksum, which only the AES-GCM tag can catch), reported as the right
  kind of problem, and never guessed around.  Note the word: *opacity*, not
  *confidentiality*.  The master key is written to ``keys/key.bin`` beside the
  saves, so these tests say nothing about whether a person with access to the
  folder can read a timetable — measured, they can (ST-SEC-002);
* **failure honesty** — a failed load must not mutate, truncate, relocate, or
  delete the very file (or key) the user is trying to recover, and a failed
  *save* must leave the copy it was replacing untouched.

Guards (no xfail) protect behavior that is correct today.  Tests decorated with
``xfail(strict=True)`` are **known-defect pins**: they assert the behavior the app
*should* have, so the suite turns red the moment the corresponding fix lands and
the pin needs flipping.

Findings guarded here: ST-DATA-001, ST-DATA-002, ST-DATA-013, ST-FUNC-007.
"""
import copy
import hashlib
import json
import math
import os
import struct

import pytest

from scheduler_app.core.models import (
    LOCATION_FACE_TO_FACE,
    normalize_state_classes,
)
from scheduler_app.storage import storage
from scheduler_app.storage.storage import EguFileError
from scheduler_app.translations import tr


# ── Helpers ──────────────────────────────────────────────────────────────────

# A string that is (a) unmistakable in a hex dump and (b) full of the Turkish
# letters that break naive encoding assumptions.  If this ever shows up in the
# raw file, the "encrypted" container is not encrypting.
MARKER = "ÇOK-GİZLİ-DERS-ADI-XYZZY · ğĞşŞıİöÖüÜçÇ"

TURKISH_ALPHABET = "abcçdefgğhıijklmnoöprsştuüvyzABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ"


def _save_path(dersis_home, name):
    return os.path.join(str(dersis_home), storage.SAVES_DIR, name)


def _realistic_state(make_preset):
    """A tiny but fully-shaped scheduler state, decorated with nasty payloads.

    ``load_encrypted`` runs ``normalize_state_classes`` on anything that looks
    like a state dict, so the fixture normalizes *before* the snapshot is taken.
    Normalization is idempotent (asserted in the round-trip test), which makes an
    exact deep-equality comparison the honest expectation rather than a fuzzy one.
    """
    state = make_preset("tiny")

    # Place a couple of classes so placements are part of the payload. They must
    # be face-to-face: normalization deliberately blanks the room fields of
    # online / office classes, which would make an exact comparison meaningless.
    state["classes"][0]["location_type"] = LOCATION_FACE_TO_FACE
    state["classes"][1]["location_type"] = LOCATION_FACE_TO_FACE
    state["classes"][0]["placed"] = True
    state["classes"][0]["placed_day"] = state["days"][0]
    state["classes"][0]["placed_time"] = state["slots"][0]
    state["classes"][0]["placed_classroom"] = state["classrooms"][0]
    state["classes"][1]["placed"] = True
    state["classes"][1]["placed_day"] = state["days"][2]
    state["classes"][1]["placed_time"] = state["slots"][3]
    state["classes"][1]["placed_classroom"] = state["classrooms"][1]

    # Turkish text in the places a user would actually put it.
    state["classes"][0]["name"] = MARKER
    state["classes"][1]["name"] = "İleri Türkçe Öğretim Yöntemleri II"
    state["classes"][2]["name"] = TURKISH_ALPHABET
    state["lecturers"].append("Öğr. Gör. Dr. Şükrü Işıkgüç")

    # Nested dicts, empty containers, and the JSON scalar edge cases.
    state["_audit_payload"] = {
        "nested": {"deeper": {"deepest": ["ığ", {"k": "ş"}, [[["Ş"]]]]}},
        "empty_list": [],
        "empty_dict": {},
        "empty_string": "",
        "none_value": None,
        "bools": [True, False],
        "ints": [0, -1, 2 ** 53 - 1, -(2 ** 53 - 1)],
        "floats": [0.0, -0.5, 1e-9, 1.7976931348623157e308],
        "unicode_edges": [" ", "​", "emoji-free ✓", "tab\tnewline\n"],
    }
    # Large-ish payload so the test exercises a realistic container size
    # (~250 KB of JSON) rather than a toy one.
    state["_audit_payload"]["bulk"] = [
        f"satır-{i:04d} ĞŞİÖÇÜ ğşiöçü" for i in range(3000)
    ]

    normalize_state_classes(state)
    return state


# Read from the module under test rather than hardcoded, so a deliberate format
# bump changes what "a file from the future" means instead of silently rotting.
_FORMAT_VERSION = storage._FORMAT_VERSION


def _reseal(body):
    """Re-attach a *valid* SHA-256 checksum to a mutated container body.

    Damage that leaves the checksum consistent is the interesting case: the
    cheap integrity check passes, so detection has to come from AES-GCM
    authentication further down. Without this the GCM tag is never exercised,
    because the checksum always fires first.
    """
    return body + hashlib.sha256(body).digest()


def _corrupt(blob, mode):
    """Return a damaged / unreadable copy of a valid .egu container.

    Container layout: magic(4) version(2) salt(16) iv(12) payload_len(4)
    ciphertext(N) checksum(32) — so byte 43 is inside the ciphertext and the
    last byte is inside the SHA-256 checksum.
    """
    if mode == "truncated":
        return blob[:-10]
    if mode == "flipped_ciphertext_byte":
        b = bytearray(blob)
        b[43] ^= 0x01
        return bytes(b)
    if mode == "flipped_checksum_byte":
        b = bytearray(blob)
        b[-1] ^= 0x01
        return bytes(b)
    if mode == "tampered_ciphertext_resealed":
        # Same ciphertext edit as above, but with the checksum recomputed so it
        # matches. Only the AES-GCM auth tag can catch this one.
        b = bytearray(blob[:-32])
        b[43] ^= 0x01
        return _reseal(bytes(b))
    if mode == "future_version":
        # A well-formed container written by a *newer* DERSİS. Not corruption —
        # the user must be told the version is unsupported, not that their file
        # is broken.
        b = bytearray(blob[:-32])
        b[4:6] = (_FORMAT_VERSION + 1).to_bytes(2, "big")
        return _reseal(bytes(b))
    if mode == "empty":
        return b""
    if mode == "garbage_magic":
        return b"NOPE" + blob[4:]
    raise AssertionError(f"unknown corruption mode {mode!r}")


# mode -> the translation *key* the user-facing message must come from. Keys
# rather than literal strings, so a reworded message does not fail the test but
# reporting the *wrong kind* of problem does.
UNREADABLE_MODES = {
    # payload_len no longer agrees with the file size
    "truncated": "errors.egu_corrupted_unexpected_size",
    "flipped_ciphertext_byte": "errors.egu_checksum_mismatch",
    "flipped_checksum_byte": "errors.egu_checksum_mismatch",
    # checksum is valid; only the GCM tag can reject this
    "tampered_ciphertext_resealed": "errors.egu_decryption_failed",
    "future_version": "errors.unsupported_egu_version",
    "empty": "errors.file_empty",
    # no EGU1/UVA1 magic -> not a container at all; falls through to plain JSON
    "garbage_magic": "errors.unrecognized_file_format",
}

CORRUPTION_MODES = list(UNREADABLE_MODES)


def _expected_message(mode):
    key = UNREADABLE_MODES[mode]
    if mode == "future_version":
        return tr(key).format(version=_FORMAT_VERSION + 1,
                              supported=_FORMAT_VERSION)
    return tr(key)


# ── 1. Round-trip fidelity ───────────────────────────────────────────────────

def test_roundtrip_preserves_a_realistic_state_exactly(dersis_home, make_preset):
    """Guard on save/load fidelity (the contract ST-DATA-013 chips away at).

    A failure means a user saves a timetable and gets back something different —
    a renamed lesson, a lost placement, or mangled Turkish characters.
    """
    state = _realistic_state(make_preset)
    expected = copy.deepcopy(state)

    # The load path re-runs normalization; prove it is idempotent so that exact
    # deep equality below is a fair expectation and not an accident.
    normalize_state_classes(state)
    assert state == expected, "normalize_state_classes is not idempotent"

    path = _save_path(dersis_home, "roundtrip.egu")
    storage.save_encrypted(state, path)

    assert state == expected, "save_encrypted mutated the caller's data"

    loaded = storage.load_encrypted(path)
    assert loaded == expected

    # Spot-check the things most likely to be silently dropped, so a failure
    # message points at the cause instead of dumping a 250 KB diff.
    assert loaded["classes"][0]["name"] == MARKER
    assert loaded["classes"][2]["name"] == TURKISH_ALPHABET
    assert loaded["classes"][0]["placed"] is True
    assert loaded["classes"][0]["placed_classroom"] == state["classrooms"][0]
    assert loaded["_audit_payload"]["empty_list"] == []
    assert loaded["_audit_payload"]["empty_dict"] == {}
    assert loaded["_audit_payload"]["none_value"] is None
    assert loaded["_audit_payload"]["bools"] == [True, False]
    assert len(loaded["_audit_payload"]["bulk"]) == 3000


def test_roundtrip_preserves_list_payloads(dersis_home):
    """Guard: ``save_encrypted_lines``/``load_encrypted_lines`` keep entry order.

    A failure means the feedback/learning log silently reorders or drops
    entries, which corrupts everything the preference learner infers from it.
    """
    path = os.path.join(str(dersis_home), storage.LOGS_DIR, "entries.egu")
    entries = [{"i": i, "note": f"kayıt-{i} ğşİ"} for i in range(50)]

    storage.save_encrypted_lines(entries, path)
    assert storage.load_encrypted_lines(path) == entries

    storage.append_encrypted_entry({"i": 50, "note": "son kayıt Ç"}, path)
    round_tripped = storage.load_encrypted_lines(path)
    assert len(round_tripped) == 51
    assert round_tripped[:50] == entries
    assert round_tripped[50] == {"i": 50, "note": "son kayıt Ç"}


# ── 2. The file on disk is actually encrypted ────────────────────────────────

def test_saved_file_is_encrypted_and_carries_the_egu1_magic(dersis_home, make_preset):
    """Guard: the .egu container must be an EGU1 blob with no readable plaintext.

    A failure means the "encrypted" save is storing student and staff names in
    the clear, so `strings`, a text editor, or a backup indexer would show them.

    Green does **not** mean the file is confidential.  ``keys/key.bin`` sits in
    the same ``Documents/Dersis/`` tree as the ciphertext, and a from-scratch
    container parser using only files under that root recovers the plaintext in
    about ten lines.  What this test pins is opacity and the container shape —
    integrity is pinned by the corruption tests below.  See ST-SEC-002 and
    ``tests/test_readme_claims.py``, which keeps the READMEs from promising more
    than that.
    """
    state = _realistic_state(make_preset)
    path = _save_path(dersis_home, "encrypted.egu")
    storage.save_encrypted(state, path)

    raw = open(path, "rb").read()

    assert raw[:4] == b"EGU1"
    assert int.from_bytes(raw[4:6], "big") == 1
    # magic(4)+version(2)+salt(16)+iv(12)+len(4)+tag(16)+checksum(32)
    assert len(raw) > 86

    # Neither the Turkish nor the ASCII half of the marker may appear, under any
    # of the encodings a leaky implementation would plausibly have used.
    for encoding in ("utf-8", "utf-16-le", "utf-16-be", "cp1254"):
        assert MARKER.encode(encoding, errors="ignore") not in raw, (
            f"class name found verbatim in the .egu file ({encoding})")
    for token in (b"XYZZY", b"classes", b"lecturers", b"placed_classroom"):
        assert token not in raw, f"{token!r} found verbatim in the .egu file"

    # The payload length field must agree with the actual file length.
    payload_len = int.from_bytes(raw[34:38], "big")
    assert 38 + payload_len + 32 == len(raw)


# ── 3. Corruption is detected, not swallowed ─────────────────────────────────

@pytest.mark.parametrize("mode", CORRUPTION_MODES)
def test_corrupt_container_raises_egufileerror(dersis_home, mode):
    """Guard: every flavour of damage must surface as the *right* EguFileError.

    A failure means DERSİS either silently accepts a damaged timetable file as
    if it were intact, blows up with an unhandled exception the UI cannot turn
    into a message, or tells the teacher the wrong story about their file (e.g.
    "corrupted" for a save written by a newer version).

    ``tampered_ciphertext_resealed`` is the load-bearing case: its SHA-256
    checksum is valid, so only AES-GCM authentication can reject it. Without it
    a build that ignored decryption failures would pass every other test here.
    """
    good = _save_path(dersis_home, "good.egu")
    storage.save_encrypted({"ders": "Türkçe", "n": 1}, good)
    blob = open(good, "rb").read()

    path = _save_path(dersis_home, f"corrupt_{mode}.egu")
    with open(path, "wb") as f:
        f.write(_corrupt(blob, mode))

    with pytest.raises(EguFileError) as excinfo:
        storage.load_encrypted(path)
    assert str(excinfo.value).strip(), "EguFileError carried no message for the UI"
    # Containment, not equality: a fix is free to *append* recovery guidance
    # ("a copy was saved to backups/…") but must not swap in a message that
    # tells the user the wrong story about what went wrong.
    assert _expected_message(mode) in str(excinfo.value), (
        f"{mode!r} reported the wrong kind of failure to the user: "
        f"{str(excinfo.value)!r}")


@pytest.mark.parametrize("mode", CORRUPTION_MODES)
def test_failed_load_does_not_touch_the_corrupt_file(dersis_home, mode):
    """Guard: a failed load must leave the damaged file byte-identical on disk.

    A failure means DERSİS destroys the user's only copy of a damaged save while
    reporting the error — removing any chance of manual or forensic recovery.
    """
    good = _save_path(dersis_home, "good.egu")
    storage.save_encrypted({"ders": "Türkçe", "n": 1}, good)
    blob = open(good, "rb").read()

    path = _save_path(dersis_home, f"untouched_{mode}.egu")
    damaged = _corrupt(blob, mode)
    with open(path, "wb") as f:
        f.write(damaged)

    before = open(path, "rb").read()
    assert before == damaged

    with pytest.raises(EguFileError):
        storage.load_encrypted(path)

    assert os.path.exists(path), "failed load deleted the corrupt file"
    assert open(path, "rb").read() == before, (
        "failed load rewrote the corrupt file")
    assert not os.path.exists(path + ".tmp"), (
        "failed load left a .tmp shadow next to the corrupt file")


def test_intact_file_still_loads_after_a_sibling_fails(dersis_home):
    """Guard: one corrupt save must not poison the others in saves/.

    A failure means a single damaged file makes the rest of the user's archive
    unreadable too.
    """
    good = _save_path(dersis_home, "intact.egu")
    payload = {"ders": "Beden Eğitimi", "sınıf": ["9-A", "9-B"]}
    storage.save_encrypted(payload, good)
    blob = open(good, "rb").read()

    bad = _save_path(dersis_home, "broken.egu")
    with open(bad, "wb") as f:
        f.write(_corrupt(blob, "flipped_checksum_byte"))

    with pytest.raises(EguFileError):
        storage.load_encrypted(bad)

    assert storage.load_encrypted(good) == payload


def test_saved_state_survives_a_simulated_app_restart(dersis_home, make_preset):
    """Guard: a save must still open after the process (and its key cache) dies.

    Every other test in this module runs with the master key warm in
    ``storage._cached_key``, which would hide a build that encrypts with one key
    but persists a different one to ``keys/key.bin``. A failure here means the
    user's timetables open today and are unreadable after they close the app.
    """
    state = _realistic_state(make_preset)
    path = _save_path(dersis_home, "restart.egu")
    storage.save_encrypted(state, path)

    key_on_disk = open(
        os.path.join(str(dersis_home), storage.KEYS_DIR, "key.bin"), "rb").read()
    storage._cached_key = None  # the app is closed and reopened

    assert storage.load_encrypted(path) == state
    # And re-reading must not have rotated the key behind the user's back.
    assert open(
        os.path.join(str(dersis_home), storage.KEYS_DIR, "key.bin"),
        "rb").read() == key_on_disk


# ── 4. ST-DATA-001 — a damaged key.bin must never be silently regenerated ────

def test_missing_key_bin_is_created_on_first_use(dersis_home):
    """Guard: an *absent* key file is legitimately created (the "first run" case).

    This is the behavior the ST-DATA-001 fix must keep: absent means "make one",
    while malformed must mean "stop". A failure means first-run setup is broken.
    """
    key_path = os.path.join(str(dersis_home), storage.KEYS_DIR, "key.bin")
    assert not os.path.exists(key_path), "fixture handed over a dirty keys/ dir"

    key = storage._load_or_create_key()

    assert len(key) == 32
    assert os.path.exists(key_path)
    assert open(key_path, "rb").read() == key


# ST-DATA-001 fixed in Phase 1: _load_or_create_key() now distinguishes an
# absent key file (first run — mint one) from a damaged one (raise, and leave
# the bytes untouched so they can still be recovered).
def test_damaged_key_bin_is_not_silently_regenerated_on_load(dersis_home):
    """ST-DATA-001: a partially-written key.bin must not be overwritten by a load.

    A failure (today) means one bad sector in a 32-byte file silently and
    permanently locks the teacher out of every timetable they have ever saved,
    with no prompt and no way back.
    """
    path = _save_path(dersis_home, "before_damage.egu")
    storage.save_encrypted({"ders": "Matematik", "yıl": 2026}, path)
    assert storage.load_encrypted(path)["ders"] == "Matematik"

    key_path = os.path.join(str(dersis_home), storage.KEYS_DIR, "key.bin")
    with open(key_path, "r+b") as f:
        f.truncate(10)
    damaged_key = open(key_path, "rb").read()
    assert len(damaged_key) == 10

    storage._cached_key = None  # simulate a fresh process start

    with pytest.raises(EguFileError):
        storage.load_encrypted(path)

    assert os.path.exists(key_path), "the damaged key.bin was moved away"
    assert open(key_path, "rb").read() == damaged_key, (
        "key.bin was silently replaced with a freshly generated key; every "
        "prior .egu in saves/ is now permanently undecryptable")


# ST-DATA-001 fixed in Phase 1: _load_or_create_key() now distinguishes an
# absent key file (first run — mint one) from a damaged one (raise, and leave
# the bytes untouched so they can still be recovered).
def test_malformed_key_bin_fails_loudly_instead_of_minting_a_new_key(dersis_home):
    """ST-DATA-001 at the API level: malformed key material must raise.

    A failure (today) means every caller of the storage layer gets a silently
    wrong key instead of an error it could show the user or act on.
    """
    key_path = os.path.join(str(dersis_home), storage.KEYS_DIR, "key.bin")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(b"\x01" * 10)
    storage._cached_key = None

    with pytest.raises(EguFileError):
        storage._load_or_create_key()

    assert open(key_path, "rb").read() == b"\x01" * 10


# ── 5. ST-FUNC-007 — legacy plain-JSON saves ─────────────────────────────────
#
# FIXED Phase 8. ``_is_fernet_token()`` used to return True for any blob whose
# first 80 bytes decode as ASCII, so a plain-JSON file made of ASCII was routed
# to the Fernet branch and died there, while the *same* file with a Turkish
# letter near the front took the plain-JSON branch and loaded. The predicate now
# asks the positive question — ``token.startswith(b"gAAAAA")``, which is what
# urlsafe-base64 of Fernet's fixed 0x80 version byte plus a pre-2106 timestamp
# always produces.
#
# The four tests below BRACKET that discriminator, and that is the point: a
# constant-True ``_is_fernet_token`` fails the three negative ones, a
# constant-False one fails the positive one. The old heuristic was one-sided and
# nothing caught it.

def test_legacy_plain_ascii_json_save_loads(dersis_home):
    """ST-FUNC-007: a legacy unencrypted JSON save written in ASCII must load.

    A failure means a user upgrading from an old DERSİS build cannot open their
    own pre-encryption save file — it is rejected as undecryptable.
    """
    path = _save_path(dersis_home, "legacy_ascii.json")
    payload = {"days": ["monday"], "slots": ["09:00"], "note": "plain ascii"}
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as f:
        f.write(blob)

    assert storage.load_encrypted(path) == payload
    # The same claim its Turkish twin below has always made. It is what catches
    # a "fix" that routes ASCII JSON through the Fernet branch's auto-upgrade
    # (which calls save_encrypted and rewrites the file) instead of through the
    # plain-JSON branch, which never touches the bytes.
    assert open(path, "rb").read() == blob, (
        "loading a legacy plain-JSON file rewrote it in place")


def test_legacy_plain_json_with_turkish_text_loads(dersis_home):
    """ST-FUNC-007 (the half that always worked): non-ASCII plain JSON loads.

    Kept as a guard so the ST-FUNC-007 fix does not "solve" the asymmetry by
    breaking the half that already worked. A failure means legacy Turkish saves
    stopped opening.
    """
    path = _save_path(dersis_home, "legacy_turkish.json")
    payload = {"ders": "Türkçe Öğretmenliği ışİĞŞ", "note": "legacy"}
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # Kept as documentation of *why this half worked before the fix*, not as a
    # statement about how the file is routed now: the first-80-bytes ASCII
    # window decided everything until Phase 8 and decides nothing since. The
    # same precedent as ST-DATA-013's rewritten reason strings in this module —
    # a stale reason is rewritten, not deleted, because deleting it is how the
    # next reader loses the story. It is also this pair's anti-vacuity check:
    # the payload has to keep carrying non-ASCII for the pair to be a pair.
    with pytest.raises(UnicodeDecodeError):
        blob[:80].decode("ascii")
    with open(path, "wb") as f:
        f.write(blob)

    assert storage.load_encrypted(path) == payload
    assert open(path, "rb").read() == blob, (
        "loading a legacy plain-JSON file rewrote it in place")


def test_a_legacy_ascii_json_array_loads_too(dersis_home):
    """ST-FUNC-007: a legacy feedback log is an ASCII JSON *array*, not a dict.

    A second production path, not just File > Open: ``load_encrypted_lines``
    falls back to ``load_encrypted`` for any file without the ``EGL1`` magic, so
    before the fix an old array-shaped feedback log was misrouted to the Fernet
    branch exactly like a save was. A failure means an upgrading user's whole
    learned history reads back empty.
    """
    path = _save_path(dersis_home, "legacy_log.json")
    payload = [{"event": "manual_move", "n": 1},
               {"event": "correction", "n": 2}]
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as f:
        f.write(blob)

    assert storage.load_encrypted(path) == payload
    assert storage.load_encrypted_lines_report(path) == (payload, 0), (
        "a readable legacy array log reported a loss, or came back empty")


def test_a_real_fernet_token_is_still_routed_to_the_fernet_branch(dersis_home):
    """ST-FUNC-007: the fix narrows the heuristic; it must not remove it.

    A failure means the fix degenerated into "delete the legacy Fernet branch",
    and a user whose pre-DERSİS build wrote Fernet-encrypted files can no longer
    open them at all.
    """
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    keys = os.path.join(str(dersis_home), storage.KEYS_DIR)
    os.makedirs(keys, exist_ok=True)
    with open(os.path.join(keys, "scheduler.key"), "wb") as f:
        f.write(key)

    payload = {"ders": "Türkçe", "n": 1}
    token = Fernet(key).encrypt(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert token.startswith(b"gAAAAA"), (
        "anti-vacuity: the cryptography package stopped producing the token "
        "shape the predicate keys on, so this test would pass for the wrong "
        "reason (got %r)" % (token[:8],))

    path = _save_path(dersis_home, "legacy_fernet.egu")
    with open(path, "wb") as f:
        f.write(token)

    assert storage.load_encrypted(path) == payload
    assert open(path, "rb").read()[:4] == storage._MAGIC, (
        "the Fernet branch's auto-upgrade did not fire: the file was read but "
        "not re-saved in the current format")


# ── 6. ST-DATA-013 — JSON type fidelity ──────────────────────────────────────
#
# READ THIS BEFORE SPENDING A DAY ON THE TWO PINS BELOW.
#
# Both fail, and both fail for the reason they state — but they are properties
# of ``json.dumps``, not defects a user can reach. **No production code path
# produces either payload. Measured Phase 7, 2026-08-28** (probe
# ``scratchpad/probe_data013.py``), across every producer that reaches
# ``storage.save_encrypted`` / ``append_encrypted_entry``:
#
#   learning/feedback_logger.py:66      the feedback log
#   learning/preference_learner.py:304  the learned weights
#   ui/app.py:2150                      the settings container
#   ui/app.py:2934                      File > Save
#   ui/first_run.py:80                  the first-run starter file
#
# * ``new_state()`` + ``new_class()`` + ``mark_placed()`` carry no non-string
#   key and no non-finite float; ``years``, ``classroom_capacities`` and
#   ``lecturer_availability`` are all str-keyed, and ``years`` keys come from
#   ``tr("status.default_year_name")``;
# * the learned weights are keyed by ``placement_scorer.DEFAULT_WEIGHTS``, all
#   string literals, and every gradient is ``lr * <constant>`` — finite after
#   100 steps of momentum, measured;
# * the importer coerces every name through ``_cell_text``, so an Excel room
#   literally named ``42`` arrives as ``"42"`` (and a ``NaN`` cell as ``""``);
# * the package's only non-finite float is ``schedule_optimizer.py:385``
#   ``global_best_quality = float("inf")``, a loop sentinel that is compared
#   and reassigned and never leaves its scope;
# * the only numeric dict keys anywhere in the package are local lookup tables
#   (``ui/app.py:4963``/``:5015`` tab-index to export mode) and splitter size
#   lists — nothing that is persisted.
#
# The pins stay ``strict``: they are the contract on the *format*, so the day
# somebody starts persisting an int-keyed map, flipping them is part of that
# change rather than an archaeology exercise. What stops the claim above from
# rotting silently is the guard at the end of this section, which walks the
# payloads production actually writes; if that one goes red, ST-DATA-013 has
# acquired a producer and stops being theoretical.

@pytest.mark.xfail(
    strict=True,
    reason="ST-DATA-013 — json.dumps silently coerces non-string dict keys "
           "(42 -> '42') and the loader never converts them back. Library-level "
           "property: no production producer, measured Phase 7 (2026-08-28) — "
           "see the section comment above and "
           "test_no_persisted_payload_needs_the_two_pins_above")
def test_non_string_dict_keys_survive_the_roundtrip(dersis_home):
    """ST-DATA-013: an int-keyed mapping must not come back string-keyed.

    This documents a property of the persistence format, not a live defect: no
    production code path builds such a mapping (measured Phase 7 — see the
    section comment). A failure means any *future* code that persists a dict
    keyed by year/slot index would get a KeyError-shaped surprise on reload:
    ``d[42]`` is gone and ``d['42']`` took its place. Refusing to persist such
    a dict is an equally acceptable fix, so this test passes on either.
    """
    path = _save_path(dersis_home, "int_keys.egu")
    payload = {42: "ikinci ders", 7: "yedinci ders"}

    try:
        storage.save_encrypted(payload, path)
    except (TypeError, ValueError):
        return  # refusing to silently coerce is an acceptable fix

    loaded = storage.load_encrypted(path)
    assert loaded == payload
    assert 42 in loaded and "42" not in loaded


@pytest.mark.xfail(
    strict=True,
    reason="ST-DATA-013 — NaN/Infinity are written as bare JSON5-only tokens, "
           "producing a payload no spec-compliant parser can read. Library-level "
           "property: no production producer, measured Phase 7 (2026-08-28) — "
           "see the section comment above and "
           "test_no_persisted_payload_needs_the_two_pins_above")
def test_persisted_payload_is_spec_valid_json(dersis_home):
    """ST-DATA-013: the encrypted payload must be RFC 8259-valid JSON.

    Like the pin above, this documents a property of the format rather than a
    live defect: nothing in the app persists a non-finite float (measured
    Phase 7 — see the section comment). A failure means the .egu payload of
    such a *future* save would contain bare ``NaN``/``Infinity`` literals, so
    no external tool, recovery script, or non-Python reader could parse it.
    Refusing to persist non-finite floats is an equally acceptable fix, so this
    test passes on either.
    """
    path = _save_path(dersis_home, "non_finite.egu")
    payload = {"score": float("nan"), "budget": float("inf"),
               "floor": float("-inf")}

    try:
        storage.save_encrypted(payload, path)
    except (TypeError, ValueError):
        return  # refusing to persist non-finite floats is an acceptable fix

    plaintext = storage._parse_container(open(path, "rb").read()).decode("utf-8")

    def _reject(token):
        raise AssertionError(
            f"persisted payload contains the non-standard JSON token {token!r}")

    json.loads(plaintext, parse_constant=_reject)


def _json_hazards(obj, where="<root>"):
    """Every non-string dict key and non-finite float reachable in *obj*.

    Returns a list of human-readable locations, so a failure names the field
    rather than just the count.
    """
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                found.append(f"{where}: dict key {k!r} is {type(k).__name__}, "
                             f"not str")
            found.extend(_json_hazards(v, f"{where}[{k!r}]"))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found.extend(_json_hazards(v, f"{where}[{i}]"))
    elif isinstance(obj, float) and not math.isfinite(obj):
        found.append(f"{where}: non-finite float {obj!r}")
    return found


def test_no_persisted_payload_needs_the_two_pins_above(dersis_home, make_preset):
    """ST-DATA-013's justification, made executable rather than remembered.

    The two pins above are held open on one claim: *nothing the app persists
    contains a non-string dict key or a non-finite float*. That claim was
    measured by hand in Phase 6 and again in Phase 7, and a measurement that
    lives in a comment is a measurement that rots — the register carried a
    stale ST-DATA-002 reason for six phases the same way.

    So this walks what the three real producers actually hand to
    ``storage.save_encrypted`` / ``append_encrypted_entry``: a fully-shaped
    saved state (``ui/app.py:2934``, and the ``"state"`` half of the settings
    container at ``ui/app.py:2150``), the learned-weights payload
    (``learning/preference_learner.py:304``), and a feedback entry read back
    off the log (``learning/feedback_logger.py:66``).

    A failure does not mean this test is wrong. It means ST-DATA-013 has
    acquired a production producer and has stopped being a library curiosity —
    fix ``storage``, then flip the two pins.
    """
    from scheduler_app.core.models import mark_placed, new_state
    from scheduler_app.learning.feedback_logger import FeedbackLogger
    from scheduler_app.learning.preference_learner import PreferenceLearner

    # 1. the payload File > Save writes, and the state inside the settings
    #    file. Built with the app's own constructors and NOT with
    #    `_realistic_state`, whose `_audit_payload` decoration is this suite's
    #    invention rather than anything production produces.
    assert _json_hazards(new_state(), "new_state()") == []
    state = make_preset("tiny")
    mark_placed(state["classes"][0], state["days"][0], state["slots"][0],
                state["classrooms"][0])
    normalize_state_classes(state)
    assert _json_hazards(state, "state") == []

    # 2. the learned weights, after enough gradient steps for momentum to have
    #    somewhere to diverge to if it ever could
    learner = PreferenceLearner()
    for _ in range(50):
        learner._update_delta("lecturer_gap", 0.9)
        learner._update_delta("student_gap", -0.9)
    learner._save_weights()
    assert _json_hazards(storage.load_encrypted(learner.weights_path),
                         "learned_weights") == []

    # 3. a feedback entry, through the public logger and back off the disk
    logger = FeedbackLogger()
    logger.log_manual_move(state["classes"][0], "monday", "09:00", "R001",
                           "tuesday", "10:00", "R002")
    entries = storage.load_encrypted_lines(logger.log_file)
    assert entries, "the feedback log came back empty; this guard tested nothing"
    assert _json_hazards(entries, "feedback_log") == []


def test_the_json_hazard_walker_can_actually_see_a_hazard():
    """Anti-vacuity for the guard above.

    ``_json_hazards`` returning ``[]`` unconditionally would make the guard —
    and with it the entire justification for leaving the two ST-DATA-013 pins
    open — pass while looking at nothing.
    """
    assert _json_hazards({2024: ["A"]}) == [
        "<root>: dict key 2024 is int, not str"]
    assert _json_hazards({"scores": [1.0, float("nan")]}) == [
        "<root>['scores'][1]: non-finite float nan"]
    assert _json_hazards({"ok": {"nested": 1, "text": "42"}}) == []


# ── 7. ST-DATA-002 — corrupt logs must not be swallowed then overwritten ─────

def test_load_encrypted_lines_returns_empty_for_a_missing_file(dersis_home):
    """Guard: "no log yet" must stay a silent, empty result.

    This is the companion to the ST-DATA-002 guards below: the fix had to make
    a *corrupt* log loud without making a *missing* one loud. A failure means a
    first run — where no feedback log exists — starts reporting damage that is
    not there, and the user is told their history is broken on day one.
    """
    path = os.path.join(str(dersis_home), storage.LOGS_DIR, "never_written.egu")
    assert not os.path.exists(path)
    assert storage.load_encrypted_lines(path) == []
    assert not os.path.exists(path), "reading a missing log created one"


def _damaged_log(dersis_home, entries, damage):
    """Write an EGL1 log, apply *damage* to a bytearray of it, return the path.

    Guards its own fixture: the file must actually come back as ``EGL1``, or
    every assertion downstream is about some other format.
    """
    path = os.path.join(str(dersis_home), storage.LOGS_DIR, "feedback.egu")
    storage.save_encrypted_lines(entries, path)
    blob = bytearray(open(path, "rb").read())
    assert bytes(blob[:4]) == storage._LOG_MAGIC, (
        "the fixture did not produce an EGL1 log (%r); the record-framing "
        "assertions below would be vacuous" % (bytes(blob[:4]),))
    damage(blob)
    with open(path, "wb") as f:
        f.write(bytes(blob))
    return path


def _frames(blob):
    """``(length_prefix_offset, rec_len)`` for every frame in a healthy log."""
    off = struct.calcsize(storage._LOG_HEADER_FMT)
    out = []
    while off + 4 <= len(blob):
        (rec_len,) = struct.unpack_from(storage._LOG_RECLEN_FMT, blob, off)
        out.append((off, rec_len))
        off += 4 + rec_len
    return out


# The three tests below REPLACE a strict pin, `test_load_encrypted_lines_does_
# not_swallow_corruption`, which asserted `pytest.raises(EguFileError)`. Three
# measured reasons, none of them a weakening:
#
#   (i)   The pin did not test what its reason string said. It built its damage
#         with `_corrupt(blob, "truncated")` — the one shape the reader already
#         handled — and that log read back as [{'a': 1}], never []. The pin
#         failed on "DID NOT RAISE", not on "indistinguishable from an empty
#         one".
#   (ii)  With a correct fix in place it STAYED QUIETLY XFAILED. A strict pin
#         that cannot signal its own fix is the ST-FUNC-005 failure mode this
#         module's header warns about, so "land the fix and flip the pin" was
#         never an available workflow here.
#   (iii) `raises` is the wrong contract, falsified by measurement:
#         `append_encrypted_entry` calls this reader on its conversion branch
#         and needs a *value*. With a raising variant substituted the append
#         raised, `FeedbackLogger._write_entry` swallowed it, and `backups/`
#         stayed EMPTY — the quarantine that
#         `test_appending_to_an_unreadable_legacy_log_quarantines_its_bytes`
#         exists to guard never ran.
#
# Where the pin asserted ONE bit ("something was wrong"), these assert three
# facts: WHICH records survived, HOW MANY were lost, and that the two public
# readers agree about the same bytes.

def test_a_damaged_record_costs_only_itself(dersis_home):
    """ST-DATA-002: damage inside one record must not cost the others.

    A failure means one flipped bit erases the user's whole feedback history
    from the learner's point of view — measured before the fix, 0 of 3 records
    recovered from a log where 2 were intact and independently framed.
    """
    # 6-byte header, then a 4-byte length prefix, then the record's own EGU1
    # container — whose byte 43 is inside the ciphertext, not the length prefix
    # and not the checksum (same offset _corrupt uses on a standalone save).
    path = _damaged_log(
        dersis_home, [{"a": 1}, {"b": 2}, {"c": 3}],
        lambda b: b.__setitem__(6 + 4 + 43, b[6 + 4 + 43] ^ 0x01))

    entries, lost = storage.load_encrypted_lines_report(path)
    assert entries == [{"b": 2}, {"c": 3}], (
        "the intact records around the damage did not survive")
    assert lost == 1, f"expected exactly one lost record, reported {lost}"
    assert storage.load_encrypted_lines(path) == entries, (
        "the two readers disagree about the same file")


def test_a_log_nothing_can_read_is_distinguishable_from_an_empty_one(dersis_home):
    """ST-DATA-002 proper: [] and "all of it was unreadable" must differ.

    A failure means DERSİS reports "no feedback history" when the history is
    there but unreadable, and the user is never told — which is the finding.
    A reader that recovers records but stays quiet about the loss passes every
    other test in this section and fails this one.
    """
    def _wreck_every_record(blob):
        for (prefix_off, _rec_len) in _frames(bytes(blob)):
            blob[prefix_off + 4 + 43] ^= 0x01

    path = _damaged_log(dersis_home, [{"a": 1}, {"b": 2}, {"c": 3}],
                        _wreck_every_record)

    entries, lost = storage.load_encrypted_lines_report(path)
    assert entries == [], "a fully damaged log yielded records"
    assert lost == 3, (
        "a log whose three records are all unreadable reported lost=%r, so "
        "nothing distinguishes it from a log that is genuinely empty" % (lost,))

    # The companion guard, in the same test so the two can never drift: a log
    # that is genuinely empty must stay silent.
    missing = os.path.join(str(dersis_home), storage.LOGS_DIR, "absent.egu")
    assert storage.load_encrypted_lines_report(missing) == ([], 0)


def test_a_torn_tail_is_reported_as_a_loss_not_as_a_clean_end(dersis_home):
    """ST-DATA-002: the shape the old pin actually built, asserted correctly.

    A half-written final record is the ordinary outcome of a power cut mid-
    append. A failure means either the records before it are thrown away, or —
    the subtler one — the truncated tail is reported as a clean end of file, so
    the user is never told a record went missing.
    """
    path = _damaged_log(dersis_home, [{"a": 1}, {"b": 2}, {"c": 3}],
                        lambda b: b.__delitem__(slice(-10, None)))

    entries, lost = storage.load_encrypted_lines_report(path)
    assert entries == [{"a": 1}, {"b": 2}], (
        "the complete records before the torn tail were not kept")
    assert lost == 1, (
        "a torn tail was reported as a clean end of file (lost=%r). `lost` is "
        "a floor, not a count — the framing that would have counted the "
        "swallowed records is exactly what is missing — but it must not be 0"
        % (lost,))


def test_a_damaged_length_prefix_cannot_fabricate_a_record(dersis_home):
    """ST-DATA-002: a reader that skips a bad record must not invent one.

    A failure means the fix that recovers records also hands the learner
    garbage it decoded out of the middle of another record — DERSİS would then
    learn from data the user never produced. Resyncing is only safe because
    every candidate window has to carry ``EGU1`` magic, a matching SHA-256 and
    a valid AES-GCM tag, and arbitrary bytes carry none of the three.

    This also proves the resync loop terminates: a length prefix that failed to
    advance ``off`` would hang the run instead of failing it.

    The assertion is deliberately "nothing outside the written set, and the
    damaged record specifically gone", NOT a fixed entry list. It used to pin
    ``entries == []``, which was the *pre-resync* reader's behaviour — that
    reader followed the shrunk length, desynchronised, and lost records 1 and 2
    as collateral. Recovering two records the user really wrote is the point of
    the resync; pinning the collateral damage would have made this test forbid
    the fix. What must never happen is an entry that was never written.
    """
    healthy_frames = []
    written = [{"a": 1}, {"b": 2}, {"c": 3}]

    def _shrink_first_prefix(blob):
        healthy_frames.extend(_frames(bytes(blob)))
        struct.pack_into(storage._LOG_RECLEN_FMT, blob, healthy_frames[0][0], 40)

    path = _damaged_log(dersis_home, written, _shrink_first_prefix)
    assert healthy_frames[0][1] != 40, "the fixture did not change anything"

    entries, lost = storage.load_encrypted_lines_report(path)
    invented = [e for e in entries if e not in written]
    assert invented == [], (
        "the reader resynced onto garbage and FABRICATED %r — no window of "
        "arbitrary bytes should ever survive magic + checksum + GCM tag"
        % (invented,))
    assert {"a": 1} not in entries, (
        "record 0's own frame was destroyed; decoding it anyway would mean the "
        "container checks are not gating what the resync hands back")
    assert len(entries) <= len(written) - 1, (
        "more records came back than survived the damage: %r" % (entries,))
    assert lost >= 1, (
        "a destroyed length prefix was reported as a clean read (lost=%r)"
        % (lost,))


def _six_record_log(dersis_home):
    """A real 6-record EGL1 log plus its healthy bytes and frame table."""
    path = os.path.join(str(dersis_home), storage.LOGS_DIR, "feedback.egu")
    for i in range(6):
        storage.append_encrypted_entry({"i": i}, path)
    healthy = open(path, "rb").read()
    frames = _frames(healthy)
    assert len(frames) == 6, "the fixture did not produce six frames"
    return path, healthy, frames


def test_a_flipped_length_prefix_does_not_freeze_the_entry_count(dersis_home):
    """ST-DATA-002: one bad bit in a LENGTH PREFIX must not stop the count.

    ``PreferenceLearner.learn()`` gates on ``_learned_through >= entry_count()``
    and returns *before any read*, so a count that cannot grow is a permanent,
    silent learning outage: every later correction the user makes is written to
    disk and never read back, and ``last_read_lost`` keeps its constructor 0 so
    ``_report_damaged_feedback_log`` says nothing.

    Measured on this fixture before the fix, flipping ONE bit in record 2's
    prefix (117 -> 119): ``log_entry_count`` fell 6 -> 3 and stayed at 3 after
    three further real appends grew the file from 732 to 1101 bytes. Every
    ``EGU1`` record start was still in the file the whole time.

    The prefix is the one field a prefix-walking reader cannot check, which is
    why the walk anchors on the container magic instead.
    """
    path, healthy, frames = _six_record_log(dersis_home)
    assert storage.log_entry_count(path) == 6

    off, rec_len = frames[2]
    damaged = bytearray(healthy)
    struct.pack_into(storage._LOG_RECLEN_FMT, damaged, off, rec_len ^ 0x2)
    with open(path, "wb") as handle:
        handle.write(bytes(damaged))
    assert bytes(damaged).count(storage._MAGIC) == 6, (
        "the fixture destroyed a record start; this test is about the PREFIX")

    frozen_at = storage.log_entry_count(path)
    assert frozen_at >= 5, (
        "one flipped prefix bit dropped the count from 6 to %r: every record "
        "after it is unreachable to the walk, so the learner's gate can never "
        "open again" % (frozen_at,))

    for i in range(3):
        storage.append_encrypted_entry({"later": i}, path)
    assert storage.log_entry_count(path) == frozen_at + 3, (
        "the count did not grow by the three records that were really "
        "appended (%r -> %r): learning is frozen for good"
        % (frozen_at, storage.log_entry_count(path)))

    report = storage.load_encrypted_lines_report(path)
    assert report.lost >= 1, (
        "a destroyed frame was read as a clean log (lost=%r)" % (report.lost,))
    assert {"later": 0} in report.entries, (
        "records appended after the damage are still not readable")


def test_the_two_log_walks_agree_on_frame_count_after_prefix_damage(dersis_home):
    """The learner's cursor is a frame index one walk makes and another spends.

    ``log_entry_count`` cannot decrypt, so it counts frames; the learner passes
    that number back as ``skip`` to ``load_encrypted_lines_since_report``. If
    the two disagree about what a frame is on a damaged file, the cursor lands
    in the wrong place and the learner re-learns or skips entries — the drift
    ``storage.py`` warns about in as many words.

    Measured before the fix on a 6-record log with one bit flipped in record
    2's prefix: ``log_entry_count`` said 3 while the since-walk saw 4 frames.
    """
    path, healthy, frames = _six_record_log(dersis_home)

    for idx in range(6):
        off, rec_len = frames[idx]
        damaged = bytearray(healthy)
        struct.pack_into(storage._LOG_RECLEN_FMT, damaged, off, rec_len ^ 0x2)
        with open(path, "wb") as handle:
            handle.write(bytes(damaged))

        total = storage.log_entry_count(path)
        whole = storage.load_encrypted_lines_report(path).entries
        assert storage.load_encrypted_lines_since_report(path, total).entries == [], (
            "the since-walk still had records left at the cursor "
            "log_entry_count reported (%r) with record %r's prefix damaged: "
            "the learner would never read them" % (total, idx))
        for skip in range(total + 1):
            tail = storage.load_encrypted_lines_since_report(path, skip).entries
            assert tail == whole[len(whole) - len(tail):], (
                "since(%r) is not a suffix of the whole read with record %r's "
                "prefix damaged: whole=%r tail=%r"
                % (skip, idx, whole, tail))


def test_a_tail_torn_just_past_a_record_boundary_is_not_a_clean_end(dersis_home):
    """ST-DATA-002: records gone, ``lost`` reporting 0.

    ``app.py``'s ``_report_damaged_feedback_log`` opens with ``if not lost:
    return``, so ``lost == 0`` is the difference between the user being told
    their history was damaged and being told nothing at all. The frame loop
    ended on ``off + 4 <= len(blob)`` and had no loss accounting on that exit,
    so a truncation landing in the 4-byte window after any record boundary
    swallowed every later record and reported a clean read.

    Measured over every truncation length of this 6-record log before the fix:
    24 lengths lost records while reporting ``lost == 0``, 0 false alarms.
    After: 6 — exactly the cuts that land ON a record boundary, which no reader
    of this format can tell from a shorter log — and still 0 false alarms.
    """
    path, healthy, frames = _six_record_log(dersis_home)
    boundary = frames[3][0]  # first byte of record 3's length prefix

    for extra in (1, 2, 3):
        with open(path, "wb") as handle:
            handle.write(healthy[:boundary + extra])
        entries, lost = storage.load_encrypted_lines_report(path)
        assert len(entries) == 3, (
            "the complete records before the tear were not kept (%r)" % (entries,))
        assert lost >= 1, (
            "%d byte(s) past a record boundary: three records are gone and the "
            "read reported a clean end (lost=%r), so the user is told nothing"
            % (extra, lost))

    # The property that must NOT regress: a healthy log, and a log cut exactly
    # at a boundary, are clean reads. Over-reporting would warn every user on
    # every launch and make the signal worthless.
    with open(path, "wb") as handle:
        handle.write(healthy)
    assert storage.load_encrypted_lines_report(path).lost == 0, (
        "a healthy log reported a loss")
    silent, false_alarms = [], []
    for cut in range(struct.calcsize(storage._LOG_HEADER_FMT), len(healthy)):
        with open(path, "wb") as handle:
            handle.write(healthy[:cut])
        entries, lost = storage.load_encrypted_lines_report(path)
        if len(entries) < 6 and lost == 0:
            silent.append(cut)
        if len(entries) == 6 and lost != 0:
            false_alarms.append(cut)
    assert false_alarms == [], (
        "truncations that lost nothing reported a loss: %r" % (false_alarms,))
    assert silent == [f[0] for f in frames], (
        "the only truncations allowed to read as a clean end are the ones that "
        "land exactly on a record boundary; got %r" % (silent,))


def test_both_readers_agree_about_damage_after_the_cursor(dersis_home):
    """ST-DATA-002: ``load_encrypted_lines_since`` had the identical swallow.

    Measured before the fix on one 3-record log: damage in record 0 gave
    ``since(path, 1) == [{'b': 2}, {'c': 3}]`` (record 0 is skipped, so it was
    never decrypted and never noticed) while ``load_encrypted_lines`` returned
    ``[]``. A failure means ``PreferenceLearner.learn()`` — which calls this
    whenever its cursor is non-zero, i.e. on every launch after the first —
    silently loses a history the other reader can read.
    """
    prefixes = []

    def _wreck_last_record(blob):
        prefixes.extend(_frames(bytes(blob)))
        last = prefixes[-1][0]
        blob[last + 4 + 43] ^= 0x01

    path = _damaged_log(dersis_home, [{"a": 1}, {"b": 2}, {"c": 3}],
                        _wreck_last_record)
    assert len(prefixes) == 3, "the fixture did not produce three frames"

    since_entries, since_lost = storage.load_encrypted_lines_since_report(path, 1)
    assert since_entries == [{"b": 2}], (
        "the tail read past the cursor did not stop at the damaged record")
    assert since_lost == 1, (
        "damage after the cursor was not reported (lost=%r)" % (since_lost,))

    whole_entries, _whole_lost = storage.load_encrypted_lines_report(path)
    assert since_entries == whole_entries[1:], (
        "the two public readers disagree about the same bytes: whole=%r "
        "since(1)=%r" % (whole_entries, since_entries))
    assert storage.load_encrypted_lines_since(path, 1) == since_entries


def test_append_does_not_overwrite_a_corrupt_log(dersis_home):
    """ST-DATA-002 guard: an append leaves a damaged log's bytes where they are.

    **This was a strict pin whose stated reason described deleted code.** It
    read "append_encrypted_entry rebuilds from the swallowed empty list and
    overwrites the corrupt log, destroying history". ST-PERF-005 replaced that
    function with an O(1) append: on the ``EGL1`` hot path (``storage.py``
    :532-539) it opens the file ``"ab"`` and writes one record, so there is no
    rebuild and no rewrite. Measured on this tree (2026-08-28): 300 damaged
    bytes in, 398 bytes out, damaged prefix byte-identical.

    It went on failing only on its narrower final clause — the file is no
    longer *equal* to ``damaged``, and no copy of exactly those bytes reached
    ``backups/`` — which asked for a quarantine the hot path cannot perform
    without giving back the O(n) that ST-PERF-005 removed. A strict pin whose
    reason describes code with no callers is how ST-FUNC-005 survived six
    phases, so this now asserts the behaviour that is true and desirable.

    A failure means somebody put the read-modify-write back: the damaged
    prefix is gone, and with it every record written before the corruption —
    the one copy of the user's feedback history that a future recovery path
    could still read.

    The *read* half of ST-DATA-002 was fixed in Phase 8 and is guarded by the
    five tests above: a damaged log now returns the records that still decrypt
    and reports how many it could not, instead of returning ``[]`` and letting
    "unreadable" masquerade as "empty".
    """
    path = os.path.join(str(dersis_home), storage.LOGS_DIR, "feedback.egu")
    storage.save_encrypted_lines([{"a": 1}, {"b": 2}, {"c": 3}], path)
    blob = open(path, "rb").read()
    assert blob[:4] == storage._LOG_MAGIC, (
        "this test is about the EGL1 append path; the log came back in some "
        "other format (%r) and the assertions below would be vacuous"
        % (blob[:4],))

    damaged = _corrupt(blob, "flipped_checksum_byte")
    with open(path, "wb") as f:
        f.write(damaged)
    # The premise, expressed as "there is still damage". It used to read
    # `load_encrypted_lines(path) == []`, which was only ever a proxy for that
    # — and a proxy that stopped holding the moment the reader learned to keep
    # the records around the damage (measured: [{'a': 1}, {'b': 2}]).
    entries, lost = storage.load_encrypted_lines_report(path)
    assert lost, (
        "the damaged log became fully readable again — the premise of this "
        "test (an append onto a log carrying damage) no longer holds")
    assert entries == [{"a": 1}, {"b": 2}], (
        "flipped_checksum_byte damages the LAST record; the two records before "
        "it must still come back, got %r" % (entries,))

    storage.append_encrypted_entry({"d": 4}, path)

    assert os.path.exists(path), "the append moved or deleted the damaged log"
    after = open(path, "rb").read()
    assert after[:len(damaged)] == damaged, (
        "the append rewrote the log instead of extending it: %d damaged bytes "
        "went in and the first %d bytes that came back are different, so the "
        "records written before the corruption are unrecoverable"
        % (len(damaged), len(damaged)))
    assert len(after) > len(damaged), (
        "the new entry was not appended (%d bytes in, %d out)"
        % (len(damaged), len(after)))


def test_appending_to_an_unreadable_legacy_log_quarantines_its_bytes(dersis_home):
    """ST-DATA-002 / ST-DATA-014 guard: the other branch of the same promise.

    ``append_encrypted_entry`` only appends in place when it finds the ``EGL1``
    magic. Anything else — a legacy single-array log, or a file too damaged to
    identify — takes the conversion branch (``storage.py``:544-549), which
    *does* replace the file. That branch is allowed to, but only because it
    moves the original into ``backups/`` first, under a ``_corrupt_<ts>`` name
    so a quarantined file is distinguishable from a healthy backup.

    A failure means one unreadable byte at the head of a log destroys it: the
    file is replaced by a one-entry log and the original bytes are gone.
    """
    path = os.path.join(str(dersis_home), storage.LOGS_DIR, "legacy.egu")
    junk = b"NOT-A-CONTAINER-AT-ALL " * 5
    with open(path, "wb") as f:
        f.write(junk)
    assert storage.load_encrypted_lines(path) == []

    storage.append_encrypted_entry({"d": 4}, path)

    assert storage.load_encrypted_lines(path) == [{"d": 4}], (
        "the log did not become a usable EGL1 log again")
    backups = os.path.join(str(dersis_home), storage.BACKUPS_DIR)
    preserved = [os.path.join(backups, n) for n in os.listdir(backups)
                 if os.path.isfile(os.path.join(backups, n))]
    assert any(open(p, "rb").read() == junk for p in preserved), (
        "the unreadable log was replaced and no copy of its bytes survives in "
        "backups/ (found: %r)" % ([os.path.basename(p) for p in preserved],))


# ── 8. Atomicity ─────────────────────────────────────────────────────────────

def test_successful_save_leaves_no_tmp_residue(dersis_home, make_preset):
    """Guard: ``save_encrypted`` must not leave its ``.tmp`` staging file behind.

    A failure means the saves/ folder fills up with half-written shadow files
    that look like real saves to the user browsing the folder.
    """
    state = _realistic_state(make_preset)
    saves = os.path.join(str(dersis_home), storage.SAVES_DIR)
    path = os.path.join(saves, "atomic.egu")

    storage.save_encrypted(state, path)
    assert not os.path.exists(path + ".tmp")
    assert sorted(os.listdir(saves)) == ["atomic.egu"]

    # Overwriting an existing save must be equally clean, and must land the new
    # content (os.replace, not a merge).
    storage.save_encrypted({"ders": "yeni"}, path)
    assert not os.path.exists(path + ".tmp")
    assert sorted(os.listdir(saves)) == ["atomic.egu"]
    assert storage.load_encrypted(path) == {"ders": "yeni"}


def test_failed_save_leaves_the_previous_file_intact(dersis_home, monkeypatch):
    """Guard: a save that blows up must not damage the save it was replacing.

    A failure means the user hits Ctrl+S on a full disk (or on state the
    serializer chokes on) and loses the timetable that was already safely on
    disk — the classic "the crash ate the last good copy" data loss.
    """
    saves = os.path.join(str(dersis_home), storage.SAVES_DIR)
    path = os.path.join(saves, "keepme.egu")
    storage.save_encrypted({"ders": "iyi kopya", "n": 1}, path)
    original = open(path, "rb").read()

    # (a) serialization fails before anything is written.
    with pytest.raises(TypeError):
        storage.save_encrypted({"ders": {"a", "b"}}, path)
    assert open(path, "rb").read() == original, "a failed save clobbered the good file"
    assert sorted(os.listdir(saves)) == ["keepme.egu"], "a failed save left residue"

    # (b) the container is built and staged, but the final rename fails.
    def _boom(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage.os, "replace", _boom)
    try:
        with pytest.raises(OSError):
            storage.save_encrypted({"ders": "yarım yazım"}, path)
    finally:
        monkeypatch.undo()

    assert open(path, "rb").read() == original, "a failed rename clobbered the good file"
    assert not os.path.exists(path + ".tmp"), "the staging .tmp file was left behind"
    assert storage.load_encrypted(path) == {"ders": "iyi kopya", "n": 1}
