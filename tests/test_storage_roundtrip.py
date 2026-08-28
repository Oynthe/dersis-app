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
# _is_fernet_token() returns True for any blob whose first 80 bytes decode as
# ASCII, so a plain-JSON file made of ASCII is routed to the Fernet branch and
# dies there, while the *same* file with a Turkish letter near the front takes
# the plain-JSON branch and loads. The two tests below pin both halves.

@pytest.mark.xfail(
    strict=True,
    reason="ST-FUNC-007 — _is_fernet_token() treats any ASCII blob as a Fernet "
           "token, so ASCII plain-JSON legacy saves are misrouted and fail; "
           "unscheduled in 14-implementation-roadmap.md")
def test_legacy_plain_ascii_json_save_loads(dersis_home):
    """ST-FUNC-007: a legacy unencrypted JSON save written in ASCII must load.

    A failure (today) means a user upgrading from an old DERSİS build cannot
    open their own pre-encryption save file — it is rejected as undecryptable.
    """
    path = _save_path(dersis_home, "legacy_ascii.json")
    payload = {"days": ["monday"], "slots": ["09:00"], "note": "plain ascii"}
    with open(path, "wb") as f:
        f.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    assert storage.load_encrypted(path) == payload


def test_legacy_plain_json_with_turkish_text_loads(dersis_home):
    """ST-FUNC-007 (inverse half): non-ASCII plain JSON loads fine today.

    Kept as a guard so the ST-FUNC-007 fix does not "solve" the asymmetry by
    breaking the half that currently works. A failure means legacy Turkish saves
    stopped opening.
    """
    path = _save_path(dersis_home, "legacy_turkish.json")
    # The Turkish letters must land inside the first 80 bytes — that is the
    # window _is_fernet_token() inspects.
    payload = {"ders": "Türkçe Öğretmenliği ışİĞŞ", "note": "legacy"}
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        blob[:80].decode("ascii")  # documents *why* this half works
    with open(path, "wb") as f:
        f.write(blob)

    assert storage.load_encrypted(path) == payload
    assert open(path, "rb").read() == blob, (
        "loading a legacy plain-JSON file rewrote it in place")


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

    This is the companion to the ST-DATA-002 pin below: the fix has to make a
    *corrupt* log loud without making a *missing* one loud. A failure means a
    first run — where no feedback log exists — starts throwing at the user.
    """
    path = os.path.join(str(dersis_home), storage.LOGS_DIR, "never_written.egu")
    assert not os.path.exists(path)
    assert storage.load_encrypted_lines(path) == []
    assert not os.path.exists(path), "reading a missing log created one"


@pytest.mark.xfail(
    strict=True,
    reason="ST-DATA-002 — load_encrypted_lines catches every exception and "
           "returns [], so a corrupt log is indistinguishable from an empty "
           "one; unscheduled by ID in 14-implementation-roadmap.md, listed as "
           "Related on the Phase 1 ST-DATA-001 row")
def test_load_encrypted_lines_does_not_swallow_corruption(dersis_home):
    """ST-DATA-002: a damaged log must raise, not masquerade as an empty log.

    A failure (today) means DERSİS reports "no feedback history" when the
    history is actually there but unreadable — and the user never learns that
    anything went wrong.
    """
    path = os.path.join(str(dersis_home), storage.LOGS_DIR, "feedback.egu")
    storage.save_encrypted_lines([{"a": 1}, {"b": 2}], path)
    blob = open(path, "rb").read()
    with open(path, "wb") as f:
        f.write(_corrupt(blob, "truncated"))

    with pytest.raises(EguFileError):
        storage.load_encrypted_lines(path)


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

    The *other* half of ST-DATA-002 is still open and still pinned, one test
    up: the damaged log reads back as ``[]`` rather than raising, so nothing
    tells the user their history stopped being readable.
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
    assert storage.load_encrypted_lines(path) == [], (
        "the damaged log became readable again — the premise of this test "
        "(an append onto a log nothing can parse) no longer holds")

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
