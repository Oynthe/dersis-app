"""Phase 9 / B7 -- a damaged feedback log below MIN_ENTRIES_TO_LEARN.

``PreferenceLearner.learn()`` gates on the record COUNT before it reads
anything::

    total = self.feedback_logger.entry_count()
    if total < self.MIN_ENTRIES_TO_LEARN:      # 5
        return 0

That ``return`` happens before ``get_entries_since_report`` is ever called, so
``last_read_lost`` keeps its constructor 0, and
``SchedulerApp._report_damaged_feedback_log`` (ui/app.py:2087) short-circuits on
``if not lost: return``.  A user who has recorded fewer than five corrections
therefore loses that history in complete silence: nothing is learned from it,
and nothing is said about it.

ST-DATA-002 exists precisely to stop that silence, and the suite already pins it
for a SIX-record log (``tests/test_settings_recovery.py``
``test_a_damaged_feedback_log_reaches_the_user``).  Six clears the threshold.
Nothing pins one, three or four, and that is the whole of the defect: the user
who has used the app least is the user who is told least.

What these tests assert is the message a person actually sees -- the
``errors.feedback_log_damaged`` text arriving on a user-visible channel of a
real ``SchedulerApp`` -- not ``last_read_lost``, which is one refactor away from
meaning nothing.

The damage shape is one flipped ciphertext bit inside every record, with the
EGL1 framing left untouched.  ``_damaged_log`` asserts that shape is genuinely
reported by the reader (``load_encrypted_lines_report(...).lost == n``) before
any test relies on it, so a failure here can never be blamed on damage the
reader is entitled to ignore -- a truncation landing on a record boundary, for
instance, is byte-for-byte a shorter healthy log and is silent by design.
"""
import re
import struct

import pytest


# ---------------------------------------------------------------------------
# Fixture: a real feedback log, damaged the way the reader notices
# ---------------------------------------------------------------------------

def _write_real_records(n):
    """Append *n* records through the real FeedbackLogger. Returns the path."""
    from scheduler_app import storage
    from scheduler_app.learning.feedback_logger import FeedbackLogger

    logger = FeedbackLogger()
    cls = {
        "name": "PHYS 101",
        "lecturer": "Ada Lovelace",
        "targets": ["CS-1"],
        "duration": 1,
        "joint_session": True,
    }
    for i in range(n):
        logger.log_manual_move(
            cls, "monday", i, "A-101", "tuesday", i, "B-202",
            score_old=12.5, score_new=3.5)

    path = storage.feedback_log_path()
    assert storage.log_entry_count(path) == n, (
        "the fixture did not write %d records through FeedbackLogger "
        "(entry_count=%d)" % (n, storage.log_entry_count(path)))
    assert len(storage.load_encrypted_lines(path)) == n, (
        "the fixture's records do not read back before damage; nothing below "
        "would be measuring damage")
    return path


def _damaged_log(n):
    """*n* real records, then one flipped ciphertext bit inside each of them.

    The EGL1 record framing is deliberately left intact.  ``log_entry_count``
    walks the length prefixes without decrypting, so it still reports *n* --
    which means the entry-count gate in ``learn()`` is the ONLY thing standing
    between the reader and damage the reader can see.  That is the defect under
    test, so the tripwires below refuse to run unless the fixture really is in
    that state.
    """
    from scheduler_app import storage

    path = _write_real_records(n)

    blob = bytearray(open(path, "rb").read())
    assert bytes(blob[:4]) == storage.storage._LOG_MAGIC, (
        "the fixture did not produce an EGL1 log (%r); the frame arithmetic "
        "below would be meaningless" % (bytes(blob[:4]),))
    off = struct.calcsize(storage.storage._LOG_HEADER_FMT)
    wrecked = 0
    while off + 4 <= len(blob):
        (rec_len,) = struct.unpack_from(
            storage.storage._LOG_RECLEN_FMT, blob, off)
        if rec_len <= 0 or off + 4 + rec_len > len(blob):
            break
        # Inside the record's own EGU1 payload, past its header/salt, so the
        # container's SHA-256 and AES-GCM tag both reject it while the outer
        # length prefix and container magic stay exactly as written.
        blob[off + 4 + 43] ^= 0x01
        wrecked += 1
        off += 4 + rec_len
    with open(path, "wb") as f:
        f.write(bytes(blob))

    assert wrecked == n, (
        "the fixture damaged %d of %d records" % (wrecked, n))
    assert storage.log_entry_count(path) == n, (
        "the damage broke the EGL1 framing, so the count gate would reject "
        "the log for the wrong reason")
    report = storage.load_encrypted_lines_report(path)
    assert report.entries == [] and report.lost == n, (
        "this damage shape is not one the reader reports (entries=%r lost=%r); "
        "a test built on it would be asserting against a loss the format is "
        "entitled to stay silent about"
        % (report.entries, report.lost))
    return path


# ---------------------------------------------------------------------------
# Fixture: everything the app says to a person, without opening a modal
# ---------------------------------------------------------------------------

class _Recorder:
    """Stand-in for a blocking modal static: records, returns, never blocks."""

    def __init__(self, sink, ret):
        self._sink = sink
        self._ret = ret

    def __call__(self, *args, **kwargs):
        self._sink.extend(a for a in args if isinstance(a, str))
        self._sink.extend(v for v in kwargs.values() if isinstance(v, str))
        return self._ret


@pytest.fixture
def user_messages(qapp, monkeypatch):
    """Collect every string that reaches a user-visible channel.

    A union of channels on purpose: the finding is "the user is never told",
    so the test must not dictate *which* channel tells them.

    Nothing here constructs a real Toast (it arms a 3 s QTimer that would
    outlive the test) and nothing lets a real ``QMessageBox`` open (offscreen,
    that hangs the run).  ``_deferred_warning`` is recorded rather than executed
    for the same reason: it parents a 0 ms QTimer to the window, and draining it
    after teardown is how a modal escapes into an unrelated test.
    """
    from PyQt6.QtWidgets import QMessageBox

    from scheduler_app.ui.app import SchedulerApp
    from scheduler_app.ui.widgets import WarningLogPanel

    seen = []

    monkeypatch.setattr(
        SchedulerApp, "_show_toast",
        lambda self, message, kind="info": seen.append(message))
    monkeypatch.setattr(
        SchedulerApp, "_deferred_warning",
        lambda self, title, text: seen.extend((title, text)))

    real_log = WarningLogPanel.log

    def spy_log(self, message, kind="info"):
        seen.append(message)
        return real_log(self, message, kind)

    monkeypatch.setattr(WarningLogPanel, "log", spy_log)

    for name, ret in (("information", QMessageBox.StandardButton.Ok),
                      ("warning", QMessageBox.StandardButton.Ok),
                      ("critical", QMessageBox.StandardButton.Ok),
                      ("question", QMessageBox.StandardButton.Yes)):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(_Recorder(seen, ret)))
    # A hand-built QMessageBox().exec() would hang offscreen too.
    monkeypatch.setattr(
        QMessageBox, "exec", _Recorder(seen, QMessageBox.StandardButton.Ok))

    return seen


def _distinctive_text(key):
    """The longest placeholder-free run of a translation's own text.

    Matched against the key's own text so a reworded message does not fail
    these tests but reporting the WRONG message does.  Deliberately the longest
    run and not "everything before the first ``{``": the suite pins Turkish, and
    the Turkish ``errors.feedback_log_damaged`` opens with ``{path}``, so the
    simpler version yields the empty string and ``"" in anything`` is True.  The
    length guard below is what stops that from passing vacuously.
    """
    from scheduler_app.translations import tr

    parts = [p.strip() for p in re.split(r"\{[^}]*\}", tr(key))]
    longest = max(parts, key=len) if parts else ""
    assert len(longest) >= 20, (
        "no placeholder-free run of %r is long enough to identify it (%r)"
        % (key, longest))
    return longest


def _damage_report_reached(messages):
    stem = _distinctive_text("errors.feedback_log_damaged")
    return [m for m in messages if isinstance(m, str) and stem in m]


# ---------------------------------------------------------------------------
# The pin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_records", [1, 3, 4])
def test_a_damaged_feedback_log_below_the_learning_threshold_reaches_the_user(
        make_app, user_messages, n_records):
    """B7: a short history is still a history, and losing it must be said.

    A failure means DERSIS threw away every correction a new user had made,
    learned nothing from any of them, and said nothing -- purely because there
    were fewer than five.  The identical damage on a six-record log is already
    reported (see the control below), so the user's own diligence is what
    decides whether they are informed.
    """
    from scheduler_app.learning.preference_learner import PreferenceLearner

    assert n_records < PreferenceLearner.MIN_ENTRIES_TO_LEARN, (
        "this test only means anything below the threshold (%d records vs "
        "MIN_ENTRIES_TO_LEARN=%d)"
        % (n_records, PreferenceLearner.MIN_ENTRIES_TO_LEARN))

    path = _damaged_log(n_records)

    win = make_app()

    assert not win.state_data.get("classes"), (
        "classes in state would let ordinary scheduling warnings land on the "
        "same channels; this fixture must start empty")

    hits = _damage_report_reached(user_messages)
    assert hits, (
        "%d unreadable feedback records at %s and the user was told nothing.\n"
        "The log reads back as 0 entries / %d lost, and the EGL1 framing is "
        "intact so entry_count() still returns %d -- but learn() returns on "
        "`total < MIN_ENTRIES_TO_LEARN` (%d) BEFORE it reads, so "
        "last_read_lost stays 0 (%r) and _report_damaged_feedback_log "
        "short-circuits.\nEverything the app did say: %r"
        % (n_records, path, n_records, n_records,
           PreferenceLearner.MIN_ENTRIES_TO_LEARN,
           getattr(win._preference_learner, "last_read_lost", "<absent>"),
           [m for m in user_messages if isinstance(m, str)]))


def test_the_same_damage_above_the_threshold_still_reaches_the_user(
        make_app, user_messages):
    """Control -- the ONLY difference from the test above is the record count.

    This one passes today.  It is here so that a failure above cannot be
    dismissed as a broken fixture, a damage shape the reader ignores, or a
    channel this file forgot to watch: same helper, same damage, same capture,
    six records instead of three.

    If this one ever fails, the fault is in this file, not in the app.
    """
    from scheduler_app.learning.preference_learner import PreferenceLearner

    n_records = PreferenceLearner.MIN_ENTRIES_TO_LEARN + 1
    path = _damaged_log(n_records)

    make_app()

    assert _damage_report_reached(user_messages), (
        "the control did not fire: %d unreadable records at %s produced no "
        "damage report either, so this file's capture or damage shape is "
        "wrong and the failures above prove nothing.\nSaw: %r"
        % (n_records, path, [m for m in user_messages if isinstance(m, str)]))
