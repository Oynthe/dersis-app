"""B8 — an in-place bit flip on a caught-up feedback log must not be silent.

The defect
----------
``PreferenceLearner.learn()`` opens with a size fast-path
(``scheduler_app/learning/preference_learner.py:87-89``)::

    size = self.feedback_logger.log_size()
    if size and size == self._learned_size:
        return 0

A flipped bit does not change a file's length. So once the learner has consumed
the log — which it has after the first pass, and which is the steady state of
every installation older than five feedback entries — the only thing it ever
looks at again is a number that in-place rot cannot move. Every later
``learn()`` returns at that line: ``last_read_lost`` keeps its constructor 0,
``SchedulerApp._report_damaged_feedback_log`` (``ui/app.py:2087-2092``) returns
at its ``if not lost``, and the user is never told that their feedback history
stopped being readable.

This is the ORDINARY failure mode for a file written once and read forever: a
bad sector, a half-synced cloud copy, bit rot. It is not a torn tail and it is
not a truncation — those change the length and the fast-path lets them through.

What is measured here, on ``.venv-audit`` at ``main`` (f049964)
--------------------------------------------------------------
Eight ``manual_move`` records, 4820 bytes. ``learn()`` -> 8 signals, cursor 8,
``_learned_size`` 4820. One bit flipped inside record 3's ciphertext: the file
is **4820 bytes before and after**. ``entry_count()`` still says 8, and
``storage.load_encrypted_lines_report`` says ``lost=1, entries=7`` — the storage
layer sees the damage perfectly well. ``learn()`` then returns 0 without ever
calling ``entry_count()`` (instrumented: 0 calls), which is the proof that the
SIZE gate is what stopped it and not the ``MIN_ENTRIES_TO_LEARN`` gate below it.
``last_read_lost`` stays 0. A freshly constructed learner — an app relaunch,
reading the cursor and size back off disk — behaves identically, so the silence
is permanent.

Why the entry count is 8 and not 4
----------------------------------
Deliberately clear of ``MIN_ENTRIES_TO_LEARN`` (5). B7 is the count gate; this
is the size gate, and a log too short to learn from would fail these tests for
B7's reason instead of B8's. Every test below asserts the count first.

What the fix must not do
------------------------
Not a full re-read on every call: the size gate is ST-PERF-005's removal of an
O(n) cost paid after every drag-drop. The module's own ST-DATA-002 comment
(``preference_learner.py:130-136``) already names the shape of the answer — a
fingerprint of the skipped BYTES, "cheap, since it needs no AES-GCM decrypt" —
and explicitly rules out a fingerprint over their LENGTH, which is exactly what
the size gate is.
"""
import os
import struct

import pytest

from scheduler_app.learning import preference_learner as _pl_module
from scheduler_app.learning.feedback_logger import FeedbackLogger
from scheduler_app.learning.preference_learner import PreferenceLearner
from scheduler_app.storage import storage
from scheduler_app.translations import tr


# Enough to clear MIN_ENTRIES_TO_LEARN (5) with room to spare, so no assertion
# in this module can be satisfied or defeated by B7's gate.
N_ENTRIES = 8
assert N_ENTRIES > PreferenceLearner.MIN_ENTRIES_TO_LEARN


def _cls(i):
    return {
        "name": "DERS-%05d" % i,
        "lecturer": "Ogr. Gor. Ayse Yilmaz",
        "targets": ["9-A", "9-B"],
        "duration": 2,
        "joint_session": True,
        "pinned": False,
    }


def _log_move(logger, i):
    """One ``manual_move`` with both scores present — worth exactly one signal."""
    logger.log_manual_move(
        _cls(i),
        "Pazartesi", "09:00", "D101",
        "Sali", "10:00", "D102",
        score_old=5.0, score_new=7.0,
    )


# Offset of the first ciphertext byte inside an EGU1 container:
# magic(4) + version(2) + salt(16) + IV(12) + payload length(4).
_CIPHERTEXT_OFFSET = (struct.calcsize(storage._HEADER_FMT)
                      + storage._SALT_LEN + storage._IV_LEN
                      + struct.calcsize(storage._PAYLOAD_LEN_FMT))


def _flip_one_payload_bit(path, index):
    """Flip a single bit inside record *index*'s ciphertext, IN PLACE.

    The framing is not touched: the EGL1 header, every length prefix and every
    ``EGU1`` container magic survive, so ``log_entry_count`` still counts the
    whole log and the damage shows up only when a record is actually decrypted.
    That is what a bad sector or a half-synced cloud copy looks like, and it is
    the shape the size fast-path cannot see.

    Returns ``(size_before, size_after)`` so the caller can assert the file's
    length did not move — which is the entire point of this test module.
    """
    with open(path, "rb") as handle:
        blob = bytearray(handle.read())
    size_before = len(blob)
    assert bytes(blob[:4]) == storage._LOG_MAGIC, (
        "not an EGL1 log (%r); this helper's frame arithmetic would be "
        "meaningless" % (bytes(blob[:4]),))

    off = struct.calcsize(storage._LOG_HEADER_FMT)
    seen = 0
    while off + 4 <= len(blob):
        (rec_len,) = struct.unpack_from(storage._LOG_RECLEN_FMT, blob, off)
        if rec_len <= 0 or off + 4 + rec_len > len(blob):
            break
        if seen == index:
            blob[off + 4 + _CIPHERTEXT_OFFSET] ^= 0x01
            with open(path, "wb") as handle:
                handle.write(bytes(blob))
            return size_before, os.path.getsize(path)
        seen += 1
        off += 4 + rec_len
    raise AssertionError("the log has no record %r" % (index,))


def _seed_caught_up_log():
    """A healthy N_ENTRIES log with the learner fully caught up on it.

    Returns ``(path, logger, learner)``. The learner's cursor and size have been
    persisted, so a *new* ``PreferenceLearner`` — what every app launch builds —
    starts from the same caught-up state.
    """
    path = storage.feedback_log_path()
    logger = FeedbackLogger()
    for i in range(N_ENTRIES):
        _log_move(logger, i)
    assert logger.entry_count() == N_ENTRIES, (
        "the fixture wrote %r records, not %r"
        % (logger.entry_count(), N_ENTRIES))

    learner = PreferenceLearner()
    signals = learner.learn()
    assert signals == N_ENTRIES, (
        "the healthy log produced %r signals, not %r; the learner is not "
        "caught up and the gate under test would not be the one that fires"
        % (signals, N_ENTRIES))
    assert learner._learned_through == N_ENTRIES
    assert learner._learned_size == os.path.getsize(path), (
        "the size fast-path was not primed (_learned_size=%r, file=%r)"
        % (learner._learned_size, os.path.getsize(path)))
    return path, logger, learner


def test_an_in_place_bit_flip_on_a_caught_up_log_is_noticed(dersis_home):
    """B8: rot that does not change the file's length must still be reported.

    A failure means DERSİS reads a corrupt feedback history forever and says
    nothing, because the only thing it checks is a byte count that in-place
    damage cannot move.
    """
    path, logger, learner = _seed_caught_up_log()

    # Control, before any damage: a healthy caught-up log must report NO loss,
    # on the first pass and on every idle pass after it. Without this, a "fix"
    # that simply reports damage unconditionally would satisfy the assertions
    # below while warning every user on every launch.
    assert learner.last_read_lost == 0, (
        "a healthy log reported a loss (last_read_lost=%r)"
        % (learner.last_read_lost,))
    assert learner.learn() == 0, "an unchanged log produced training signals"
    assert learner.last_read_lost == 0, (
        "an idle pass over a healthy log reported a loss (last_read_lost=%r)"
        % (learner.last_read_lost,))

    # ── The damage: one bit, in place, in a record's payload. ────────────────
    size_before, size_after = _flip_one_payload_bit(path, index=3)
    assert size_after == size_before, (
        "the fixture changed the file's length (%r -> %r); a size change is "
        "exactly what the fast-path CAN see, so this would no longer be B8"
        % (size_before, size_after))

    # The framing survived, so B7's count gate is provably not what stops
    # learn(): the log still holds N_ENTRIES >= MIN_ENTRIES_TO_LEARN records.
    assert logger.entry_count() == N_ENTRIES, (
        "the fixture damaged the framing (count %r != %r); this test is about "
        "the PAYLOAD" % (logger.entry_count(), N_ENTRIES))

    # And the storage layer sees the damage perfectly well when asked. The
    # information exists; only the learner's fast-path hides it.
    report = storage.load_encrypted_lines_report(path)
    assert report.lost >= 1, (
        "the fixture did not actually damage a record (storage reports "
        "lost=%r over %r readable entries)" % (report.lost, len(report.entries)))

    # ── The behaviour under test. ────────────────────────────────────────────
    # Instrumented only to make the failure message name the right line: a
    # learn() that never reaches entry_count() returned at the size fast-path
    # (preference_learner.py:87-89), above every other gate in the method.
    reached_count_gate = []
    real_entry_count = learner.feedback_logger.entry_count
    learner.feedback_logger.entry_count = (
        lambda: (reached_count_gate.append(1), real_entry_count())[1])
    try:
        learner.learn()
    finally:
        learner.feedback_logger.entry_count = real_entry_count

    assert learner.last_read_lost >= 1, (
        "a record of the user's feedback history rotted in place and the "
        "learner reported no loss (last_read_lost=%r). The file is %r bytes "
        "before AND after the flip, so the size fast-path cannot see it; "
        "storage.load_encrypted_lines_report says lost=%r over %r readable "
        "records, and entry_count() is %r, well past MIN_ENTRIES_TO_LEARN=%r. "
        "learn() %s reach the MIN_ENTRIES gate, so it returned at the size "
        "fast-path (preference_learner.py:87-89). "
        "SchedulerApp._report_damaged_feedback_log therefore returns at its "
        "`if not lost` and the user is told nothing, forever."
        % (learner.last_read_lost, size_after, report.lost,
           len(report.entries), logger.entry_count(),
           PreferenceLearner.MIN_ENTRIES_TO_LEARN,
           "DID" if reached_count_gate else "did NOT"))


def test_the_rot_is_still_noticed_after_an_app_relaunch(dersis_home):
    """B8, the permanent half: a restart does not clear the silence.

    The cursor and ``_learned_size`` are persisted alongside the weights, so a
    brand-new ``PreferenceLearner`` — what ``SchedulerApp.__init__`` builds on
    every launch — comes up already caught up and returns at the same gate.
    Restarting DERSİS is the one thing a user would try, and it changes nothing.
    """
    path, logger, _learner = _seed_caught_up_log()
    size_before, size_after = _flip_one_payload_bit(path, index=3)
    assert size_after == size_before, (
        "the fixture changed the file's length (%r -> %r)"
        % (size_before, size_after))
    assert logger.entry_count() == N_ENTRIES

    relaunched = PreferenceLearner()
    assert relaunched._learned_through == N_ENTRIES, (
        "the cursor did not survive the restart (%r)"
        % (relaunched._learned_through,))
    assert relaunched._learned_size == size_after, (
        "the persisted size did not survive the restart (%r against a %r-byte "
        "file); the size gate under test would not fire"
        % (relaunched._learned_size, size_after))

    relaunched.learn()
    assert relaunched.last_read_lost >= 1, (
        "a relaunched DERSİS read a damaged %r-byte feedback log and reported "
        "no loss (last_read_lost=%r). The persisted _learned_size still "
        "matches the file byte for byte, so every future launch will return at "
        "the same fast-path: the corruption is permanent and permanently silent."
        % (size_after, relaunched.last_read_lost))


def test_the_user_is_told_when_a_caught_up_feedback_log_rots_in_place(
        dersis_home, make_app):
    """B8 as the user experiences it: the window must carry the warning.

    ``SchedulerApp.__init__`` builds a ``PreferenceLearner``, calls ``learn()``,
    and later calls ``_flush_startup_settings_report`` ->
    ``_report_damaged_feedback_log``, which reads ``last_read_lost`` and shows
    ``errors.feedback_log_damaged``. Asserted on the string the user is shown,
    not on the flag that produces it.
    """
    path, logger, _learner = _seed_caught_up_log()
    size_before, size_after = _flip_one_payload_bit(path, index=3)
    assert size_after == size_before, (
        "the fixture changed the file's length (%r -> %r)"
        % (size_before, size_after))
    assert logger.entry_count() == N_ENTRIES
    assert storage.load_encrypted_lines_report(path).lost >= 1

    win = make_app()

    expected = tr("errors.feedback_log_damaged").format(path=logger.log_file)
    shown = [text for _title, text in win._deferred_warnings]
    assert expected in shown, (
        "DERSİS opened over a feedback log with a rotted record and showed the "
        "user nothing about it. The file is %r bytes, unchanged in length by "
        "the damage, so PreferenceLearner.learn() returned at its size "
        "fast-path and left last_read_lost at %r. Warnings actually shown: %r"
        % (size_after, win._preference_learner.last_read_lost, shown))


# ════════════════════════════════════════════════════════════════════════════
# A LOG BIGGER THAN THE THREE FINGERPRINT WINDOWS
#
# Everything above runs on an 8-record, ~5 KB log — BELOW one 8192-byte
# ``_FINGERPRINT_WINDOW``, where ``_span_fingerprint``'s three offsets collapse
# to ``{0}`` and the whole file is hashed. Measured: every fingerprint assertion
# in this repository lived there, so the three-window code path — the fix's most
# distinctive design decision — was pinned by nothing. Deleting two of the three
# offsets left the whole B7/B8/B9 suite green.
#
# These tests run above ``3 * _FINGERPRINT_WINDOW``, where the three windows are
# genuinely disjoint and there are gaps between them, and they assert both
# halves of the documented trade: damage INSIDE a window is caught in-session,
# damage in a GAP is not — and reaches the user at the next launch anyway.
# ════════════════════════════════════════════════════════════════════════════

# 120 records is ~75 KB, comfortably past 3 * 8192 = 24 576 B, with gaps of
# ~25 KB either side of the middle window. Seeded in ONE write rather than by
# 120 real appends: the bytes are identical (``_write_log`` frames each record
# with the same ``_log_record`` the append path uses) and 120 fsync'd appends
# measured 2.1-5.6 s on this box against ~5 ms for the single write.
N_LONG = 120


def _long_entry(i):
    """The on-disk shape ``log_manual_move`` produces, built by hand."""
    return {
        "event": "manual_move",
        "class": {
            "name": "DERS-%05d" % i,
            "lecturer": "Ogr. Gor. Ayse Yilmaz",
            "targets": ["9-A", "9-B"],
            "duration": 2,
            "joint_session": True,
            "pinned": False,
            "has_day_constraints": False,
            "has_time_constraints": False,
            "has_room_constraints": False,
        },
        "old_placement": {"day": "Pazartesi", "slot": "09:00", "room": "D101"},
        "new_placement": {"day": "Sali", "slot": "10:00", "room": "D102"},
        "score_old": 5.0,
        "score_new": 7.0,
        "signal": "prefer_new",
        "timestamp": "2026-08-29T10:00:0%d" % (i % 10),
        "epoch": 1787000000.0 + i,
    }


def _fingerprint_windows(size):
    """The three byte ranges ``_span_fingerprint`` hashes, at *size* bytes.

    Written out here rather than imported from the production expression ON
    PURPOSE. A test that recomputes the offsets the way the implementation
    happens to compute them today moves with it, so an implementation that
    dropped two of the three windows would drop them from the test too and stay
    green — which is exactly the hole these tests exist to close. Only
    ``_FINGERPRINT_WINDOW`` is taken from the module, because that one IS the
    parameter under test.
    """
    window = PreferenceLearner._FINGERPRINT_WINDOW
    assert size > 3 * window, (
        "a %r-byte log is not bigger than 3 * _FINGERPRINT_WINDOW (%r); the "
        "three windows collapse and every assertion below would be vacuous"
        % (size, 3 * window))
    return [(0, window),
            ((size - window) // 2, (size - window) // 2 + window),
            (size - window, size)]


def _ciphertext_starts(path):
    """``(record index, absolute file offset of its first ciphertext byte)``."""
    with open(path, "rb") as handle:
        blob = handle.read()
    out = []
    for index, frame in enumerate(storage._walk_log_frames(blob)):
        assert frame is not None, "the fixture log has a torn frame at %r" % index
        start, _rec_len = frame
        out.append((index, start + _CIPHERTEXT_OFFSET))
    return out


def _flip_payload_bit_at(path, byte_offset):
    """Flip one bit at *byte_offset*, in place. Returns ``(before, after)``."""
    with open(path, "rb") as handle:
        blob = bytearray(handle.read())
    before = len(blob)
    blob[byte_offset] ^= 0x01
    with open(path, "wb") as handle:
        handle.write(bytes(blob))
    return before, os.path.getsize(path)


def _seed_long_caught_up_log():
    """A healthy N_LONG-record log with the learner fully caught up and saved.

    Returns ``(path, logger, learner, size, windows)``.
    """
    path = storage.feedback_log_path()
    storage.save_encrypted_lines([_long_entry(i) for i in range(N_LONG)], path)
    logger = FeedbackLogger()
    assert logger.entry_count() == N_LONG, (
        "the fixture wrote %r records, not %r" % (logger.entry_count(), N_LONG))

    learner = PreferenceLearner()
    assert learner.learn() == N_LONG
    assert learner.last_read_lost == 0
    assert learner._learned_through == N_LONG
    size = os.path.getsize(path)
    assert learner._learned_size == size
    return path, logger, learner, size, _fingerprint_windows(size)


def _pick_offset(path, inside):
    """A record's ciphertext byte satisfying *inside*, or a skip if there is none."""
    for _index, offset in _ciphertext_starts(path):
        if inside(offset):
            return offset
    raise AssertionError("no record's ciphertext starts where the test needs it")


@pytest.mark.parametrize("which", ["middle", "end"])
def test_a_flip_inside_a_later_fingerprint_window_is_noticed_in_session(
        dersis_home, which):
    """The middle and end windows must actually be hashed, not just documented.

    ``_span_fingerprint`` samples three windows so the in-session check costs
    the same on a long log as on a short one. Only the FIRST of the three is
    exercised by a log small enough to fit in one window — which every other
    fingerprint assertion in this repository is. A failure here means the log is
    identified by its first 8 KB alone: rot anywhere past that is invisible for
    the whole session, and the module's own comment about what the sampling does
    and does not cover is wrong in the direction that matters.
    """
    path, logger, learner, size, windows = _seed_long_caught_up_log()
    low, high = windows[1 if which == "middle" else 2]

    offset = _pick_offset(path, lambda b: low <= b < high)
    before, after = _flip_payload_bit_at(path, offset)
    assert after == before, (
        "the fixture changed the file's length (%r -> %r)" % (before, after))
    assert logger.entry_count() == N_LONG, "the fixture damaged the framing"
    assert storage.load_encrypted_lines_report(path).lost >= 1

    learner.learn()
    assert learner.last_read_lost >= 1, (
        "a bit flipped at byte %r of a %r-byte log — inside the %s window "
        "%r — went unnoticed by the in-session check (last_read_lost=%r). "
        "The learner is identifying the log by a prefix, so damage past that "
        "prefix cannot move the fingerprint and learn() returns at the size "
        "gate. Windows: %r"
        % (offset, size, which, (low, high), learner.last_read_lost, windows))


def test_a_flip_between_the_windows_is_silent_now_and_reported_at_next_launch(
        dersis_home, make_app):
    """The documented residue of the sampling trade, verified end to end.

    ``_span_fingerprint``'s docstring concedes that a bit flipped OUTSIDE the
    three windows of a log bigger than ``3 * _FINGERPRINT_WINDOW`` is invisible
    for the rest of the session, and claims the cost is "latency in the report,
    not silence", because "the next launch reads the whole log through
    _check_log_health()". Nobody had checked that claim; it is the sentence the
    whole trade rests on, so it is pinned here across a real relaunch AND a real
    window.

    Measured 2026-08-29 on .venv-audit: 120 records, 75 460 B, windows
    [(0, 8192), (33 634, 41 826), (67 268, 75 460)]; record 13's ciphertext
    starts at byte 8221, provably in the first gap. In-session ``lost`` 0, next
    launch 1, and ``errors.feedback_log_damaged`` in the window's warnings.

    The in-session half is asserted as ``== 0`` deliberately. It is not a wish:
    if a future change makes the in-session check catch this, the trade has
    changed and this docstring, ``_span_fingerprint``'s and the
    ``_FINGERPRINT_WINDOW`` cost argument all need rewriting. Failing here is
    how that gets noticed.
    """
    path, logger, learner, size, windows = _seed_long_caught_up_log()

    def in_a_gap(byte):
        return not any(low <= byte < high for low, high in windows)

    offset = _pick_offset(path, in_a_gap)
    before, after = _flip_payload_bit_at(path, offset)
    assert after == before, (
        "the fixture changed the file's length (%r -> %r)" % (before, after))
    assert logger.entry_count() == N_LONG
    assert storage.load_encrypted_lines_report(path).lost >= 1

    learner.learn()
    assert learner.last_read_lost == 0, (
        "the in-session check noticed a flip at byte %r, outside every window "
        "%r of a %r-byte log. That is BETTER than documented, not worse — but "
        "_span_fingerprint's docstring, this test and the _FINGERPRINT_WINDOW "
        "cost argument all describe a trade that is no longer the one being "
        "made, and they have to be rewritten together."
        % (offset, windows, size))

    relaunched = PreferenceLearner()
    relaunched.learn()
    assert relaunched.last_read_lost >= 1, (
        "a bit flipped at byte %r of a %r-byte feedback log was invisible "
        "in-session (documented, allowed) AND invisible to the next launch "
        "(last_read_lost=%r), which is not. _span_fingerprint's docstring "
        "claims 'the next launch reads the whole log through "
        "_check_log_health()'; if that is false the sampling trade costs "
        "silence, not latency, and B8 is open again. Windows: %r"
        % (offset, size, relaunched.last_read_lost, windows))

    win = make_app()
    expected = tr("errors.feedback_log_damaged").format(path=logger.log_file)
    shown = [text for _title, text in win._deferred_warnings]
    assert expected in shown, (
        "the relaunched learner saw the damage but the user was not told. "
        "last_read_lost=%r, warnings shown: %r"
        % (win._preference_learner.last_read_lost, shown))


# ════════════════════════════════════════════════════════════════════════════
# R1 — the launch integrity read must not cost O(the user's whole history)
#
# ``_check_log_health`` runs above every gate and, with ``_checked_span`` None
# on construction, did a whole-file AES-GCM + json read on the first ``learn()``
# of every process — which ``SchedulerApp.__init__`` calls synchronously before
# the window is built. Measured 2026-08-29 on .venv-audit, min of 5, fresh
# learner per rep over a caught-up log: 17.6 ms at 2 000 records, 94.1 ms at
# 10 000, 199.8 ms at 20 000 (11.7 MB), 538.2 ms at 50 000. Linear, ~10 us per
# record, and the log is append-only with no rotation, cap or pruning anywhere.
# A whole ``SchedulerApp.__init__`` costs 90 ms with no log at all.
#
# The bound is the ``_verified_*`` anchor: a SHA-256 over EVERY byte of a span
# a full read already decrypted and found clean, persisted next to the weights.
# 199.8 ms -> 10.7 ms at 20 000 records. The tests below pin the two halves that
# can go wrong in opposite directions — that the read really is skipped, and
# that it is skipped ONLY over bytes proven identical to a clean span.
# ════════════════════════════════════════════════════════════════════════════

def _counting_full_reads():
    """Count ``load_encrypted_lines_report`` calls made by ``_check_log_health``.

    Patched on ``preference_learner.storage``, which is the ``scheduler_app.
    storage`` PACKAGE — ``preference_learner`` does ``from scheduler_app import
    storage``. Patching ``scheduler_app.storage.storage`` instead would count
    zero forever: the package re-exports the name by binding it at import time,
    so the two module objects hold independent references and the learner never
    looks at the one the rest of this file imports.
    """
    calls = []
    real = _pl_module.storage.load_encrypted_lines_report

    def counted(path):
        calls.append(path)
        return real(path)

    return calls, real, counted


def test_a_relaunch_over_an_unchanged_healthy_log_does_not_re_read_the_history(
        dersis_home):
    """R1: the once-per-launch O(n) decrypt must not be paid when nothing moved.

    A failure means every launch decrypts the user's entire feedback history to
    learn what the previous launch already established — 199.8 ms at 20 000
    records, growing without bound, on a blank screen.

    Asserted as a CALL COUNT, not a duration: the module's costs are measured in
    work done, and a timing threshold on this box is noise (see
    tests/test_feedback_log_scaling.py's opening note).
    """
    path, _logger, _learner, size, _windows = _seed_long_caught_up_log()

    calls, real, counted = _counting_full_reads()
    _pl_module.storage.load_encrypted_lines_report = counted
    try:
        relaunched = PreferenceLearner()
        assert relaunched._learned_through == N_LONG, (
            "the cursor did not survive the restart (%r)"
            % (relaunched._learned_through,))
        relaunched.learn()
    finally:
        _pl_module.storage.load_encrypted_lines_report = real

    assert calls == [], (
        "a relaunch over a byte-for-byte unchanged %r-byte log decrypted the "
        "whole history again (%r full reads). The clean-span anchor persisted "
        "with the weights is meant to answer this from a SHA-256 of the same "
        "bytes: 10.7 ms against 199.8 ms at 20 000 records." % (size, calls))
    assert relaunched.last_read_lost == 0, (
        "the skipped read left a loss reported on a healthy log "
        "(last_read_lost=%r)" % (relaunched.last_read_lost,))


def test_a_relaunch_after_a_session_of_manual_moves_does_not_re_read_the_history(
        dersis_home):
    """R1: the anchor has to follow the log, or it saves exactly one launch.

    The test above starts a launch over a log nothing has touched since the
    anchor was set, which is the easy case and not the one users are in. A real
    session appends: ``core/workflow.py`` logs a ``manual_move`` and calls
    ``learn()`` immediately after each one. If the anchor is not carried forward
    over those appends it goes stale on the first drag-drop, and every launch
    from then on decrypts the whole history again — 199.8 ms at 20 000 records,
    which is the defect this was supposed to bound.

    ``_extend_verified`` is what carries it, and it hashes only the appended
    bytes: 0.06 ms per record, flat from 2 000 records to 20 000, against the
    ~44 ms that ``learn()`` pass already costs at 20 000.
    """
    path, logger, learner, size, _windows = _seed_long_caught_up_log()

    for i in range(3):
        _log_move(logger, N_LONG + i)
        assert learner.learn() == 1, "the appended record produced no signal"
    grown = os.path.getsize(path)
    assert grown > size, "the session did not grow the log"

    calls, real, counted = _counting_full_reads()
    _pl_module.storage.load_encrypted_lines_report = counted
    try:
        relaunched = PreferenceLearner()
        relaunched.learn()
    finally:
        _pl_module.storage.load_encrypted_lines_report = real

    assert calls == [], (
        "a relaunch after a session of 3 manual moves (%r -> %r bytes) "
        "decrypted the whole history again. The anchor was left behind at the "
        "size it had before the session, so it matches nothing and the launch "
        "read is paid in full every time — the R1 bound saves the one launch "
        "where the user changed nothing, and no other." % (size, grown))
    assert relaunched.last_read_lost == 0
    assert relaunched._learned_through == N_LONG + 3, (
        "the cursor did not survive the restart (%r)"
        % (relaunched._learned_through,))


def test_damage_that_arrived_while_dersis_was_closed_still_forces_a_full_read(
        dersis_home):
    """R1's other half: the anchor must never answer for bytes that changed.

    The whole point of B7-B9 is that whether a person is told their history
    stopped being readable must not depend on a throughput decision. A cheap
    launch check that misses damage has undone the phase. Here the log is
    damaged with its LENGTH unchanged — the one shape a size comparison cannot
    see — while the learner is not running.
    """
    path, logger, _learner, size, windows = _seed_long_caught_up_log()

    def in_a_gap(byte):
        return not any(low <= byte < high for low, high in windows)

    before, after = _flip_payload_bit_at(path, _pick_offset(path, in_a_gap))
    assert after == before

    calls, real, counted = _counting_full_reads()
    _pl_module.storage.load_encrypted_lines_report = counted
    try:
        first = PreferenceLearner()
        first.learn()
        second = PreferenceLearner()
        second.learn()
    finally:
        _pl_module.storage.load_encrypted_lines_report = real

    assert len(calls) == 2, (
        "%r launches over a damaged %r-byte log did %r full integrity reads. "
        "Each launch must do exactly one: the anchor is only ever set from a "
        "read that found NOTHING lost, so a damaged log has none to reuse and "
        "goes on being reported until it is repaired." % (2, size, len(calls)))
    assert first.last_read_lost >= 1 and second.last_read_lost >= 1, (
        "a %r-byte feedback log with a rotted record, unchanged in length, was "
        "reported as healthy on launch %s. The launch check answered from a "
        "stale anchor instead of from the bytes."
        % (size, "1" if first.last_read_lost < 1 else "2"))


def test_a_damaged_log_with_unlearned_records_is_reported_on_every_launch(
        dersis_home):
    """R1: a full read that found damage must leave NO anchor behind.

    The shape that makes this bite, and it is an ordinary one: the log is
    damaged AND has records the learner has not consumed yet — the app was
    closed after an auto-placement or a batch schedule, neither of which calls
    ``learn()``, or it crashed. The launch's full read finds the damage and
    reports it; then ``learn()`` goes on to read the new tail, which is
    perfectly healthy, so the incremental read reports NO loss and the anchor is
    carried forward and persisted.

    If the full read anchored regardless of its verdict, that persisted anchor
    now describes the damaged bytes exactly, every later launch matches it, and
    the warning stops after the first launch — the user restarts DERSİS once,
    which is the first thing anyone does, and the damage report disappears.
    Measured: with the ``if report.lost`` guard removed, launch 2 reports 0.
    """
    path, logger, _learner, size, _windows = _seed_long_caught_up_log()
    _flip_one_payload_bit(path, index=3)
    assert storage.load_encrypted_lines_report(path).lost >= 1

    # A record appended with no learn() after it — what an auto-placement or a
    # batch schedule leaves behind (neither calls learn(); ui/app.py and
    # core/workflow.py only call it after a manual move and a reschedule).
    _log_move(logger, N_LONG)
    assert logger.entry_count() == N_LONG + 1

    seen = []
    for _launch in range(2):
        learner = PreferenceLearner()
        learner.learn()
        seen.append(learner.last_read_lost)

    assert all(lost >= 1 for lost in seen), (
        "two launches over a damaged %r-byte log with one unlearned record "
        "reported %r. A launch whose full read found damage must not leave a "
        "clean-span anchor behind: the next launch matches it, believes the "
        "log was vouched for, and says nothing." % (size, seen))


def test_a_log_that_grew_while_dersis_was_closed_is_read_in_full(dersis_home):
    """R1: growth of ANY size sends the launch to the full read.

    The anchor is compared with ``==``, never ``>=``, and this is the shape that
    decides it. Bytes appended in a form that yields no countable frame — a
    half-written record, a torn tail from a crash mid-append — leave
    ``log_entry_count()`` unmoved, so ``learn()`` returns at ``_learned_through
    >= total`` and no incremental read ever looks at them either. The full read
    at the next launch is the ONLY thing that sees them, which is what makes
    that residue bounded silence rather than permanent silence. Accepting a
    verified PREFIX here would take the one look away and make it permanent.
    """
    path, logger, _learner, size, _windows = _seed_long_caught_up_log()

    with open(path, "ab") as handle:
        handle.write(b"\x00" * 35)  # no length prefix, no EGU1 magic: not a frame
    grown = os.path.getsize(path)
    assert grown == size + 35
    assert logger.entry_count() == N_LONG, (
        "the torn tail produced a countable frame (%r records); then learn()'s "
        "incremental read would look at it and this test would be about "
        "something else" % (logger.entry_count(),))
    assert storage.load_encrypted_lines_report(path).lost >= 1, (
        "the storage layer does not consider a 35-byte torn tail a loss, so "
        "there is nothing for the launch to report")

    calls, real, counted = _counting_full_reads()
    _pl_module.storage.load_encrypted_lines_report = counted
    try:
        relaunched = PreferenceLearner()
        relaunched.learn()
    finally:
        _pl_module.storage.load_encrypted_lines_report = real

    assert len(calls) == 1, (
        "a log that grew from %r to %r bytes while DERSİS was closed did %r "
        "full integrity reads at the next launch, not 1" % (size, grown, len(calls)))
    assert relaunched.last_read_lost >= 1, (
        "%r bytes of junk appended to a caught-up %r-byte log were never "
        "looked at (last_read_lost=%r). entry_count() is still %r, so learn() "
        "returns at its cursor gate and the incremental read never runs; if "
        "the launch check accepts a verified prefix, nothing looks at those "
        "bytes ever again." % (35, size, relaunched.last_read_lost, N_LONG))


def test_an_append_after_unseen_rot_does_not_launder_it_into_the_anchor(
        dersis_home, make_app):
    """R1: carrying the anchor over an append must not re-bless the old bytes.

    The dangerous shape, and the reason ``_extend_verified`` extends a live
    hashlib object instead of re-hashing the file: rot lands in a gap between
    the fingerprint windows (invisible in-session, documented), and then the
    user makes ONE more manual move before quitting. The append's ``learn()``
    reads only frames at or after the cursor, so it reports no loss, and the
    anchor is carried forward.

    Extending the OBJECT keeps the digest describing the prefix as it was when
    it was last really hashed, so it no longer matches the bytes on disk and the
    next launch takes the full read. Re-hashing the file here would make the
    persisted digest agree with the damaged bytes, and the single act of using
    the program would suppress the warning about the damage — permanently, since
    the anchor would be re-blessed on every later append too.
    """
    path, logger, learner, size, windows = _seed_long_caught_up_log()

    def in_a_gap(byte):
        return not any(low <= byte < high for low, high in windows)

    offset = _pick_offset(path, in_a_gap)
    before, after = _flip_payload_bit_at(path, offset)
    assert after == before
    assert storage.load_encrypted_lines_report(path).lost >= 1

    _log_move(logger, N_LONG)  # one more manual move, in front of the cursor
    assert logger.entry_count() == N_LONG + 1
    assert learner.learn() == 1, "the appended record produced no signal"
    assert learner.last_read_lost == 0, (
        "the incremental read reported a loss it cannot see; this test needs "
        "the damage to be BEHIND the cursor (last_read_lost=%r)"
        % (learner.last_read_lost,))

    relaunched = PreferenceLearner()
    relaunched.learn()
    assert relaunched.last_read_lost >= 1, (
        "a school whose feedback history rotted at byte %r and who then made "
        "one more manual move is never told (last_read_lost=%r). The anchor "
        "was carried over the append by re-reading the file, so it now vouches "
        "for the damaged bytes it was never meant to describe."
        % (offset, relaunched.last_read_lost))

    win = make_app()
    expected = tr("errors.feedback_log_damaged").format(path=logger.log_file)
    shown = [text for _title, text in win._deferred_warnings]
    assert expected in shown, (
        "the relaunched learner saw the damage but the window carried no "
        "warning. last_read_lost=%r, shown: %r"
        % (win._preference_learner.last_read_lost, shown))
