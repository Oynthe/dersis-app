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
