"""B9 — the damaged-feedback-log report is a passenger on the learning path.

ST-DATA-002 gave the storage layer a way to say "part of this log is
unreadable": ``LogRead.lost``. ``SchedulerApp._report_damaged_feedback_log``
(``ui/app.py``) is the only consumer of that knowledge, and it does not read
the storage layer at all — it reads ``PreferenceLearner.last_read_lost``, which
is written in exactly one place: ``learn()``, *after* three gates that exist
for performance reasons and have nothing to do with damage.

::

    def learn(self):
        size = self.feedback_logger.log_size()
        if size and size == self._learned_size:     # G1  size fast-path
            return 0
        total = self.feedback_logger.entry_count()
        if total < self.MIN_ENTRIES_TO_LEARN:       # G2  fewer than 5 frames
            return 0
        if self._learned_through >= total:          # G3  cursor at the end
            return 0
        entries, lost = self.feedback_logger.get_entries_since_report(...)
        self.last_read_lost = lost                  # the only writer

So the user is told their history is damaged only when the damage happens to
sit in a log that is *also* worth a learning pass. Measured against the code on
2026-08-29 (see the table below): of the six shapes where the storage layer
reports damage, one reaches the user and five do not — including the two shapes
where ``LogRead.lost`` is ``-1``, which per ``LogRead``'s own docstring is the
*strongest* statement the format can make ("the file could not be identified as
a log at all"). Those two are unreachable by construction: an unidentifiable
container makes ``log_entry_count`` return 0, and 0 < 5 is G2.

This module is the STRUCTURAL probe: a table over the whole condition matrix,
so a fix has to satisfy all of it and not just the row someone wrote down.
Phase 8's two tests (``tests/test_settings_recovery.py::
test_a_damaged_feedback_log_reaches_the_user`` and
``tests/test_feedback_log_scaling.py::
test_the_learner_does_not_re_read_an_unreadable_log_forever``) both establish
the same single row — a fresh profile, an EGL1 log of 6 or 12 records with
every payload damaged and the framing left deliberately intact, cursor at 0 —
and ``_damage_feedback_log``'s docstring says so outright ("the
MIN_ENTRIES_TO_LEARN gate is still cleared — otherwise learn() would return
before reaching the code under test"). Neither asks what happens below the gate.

The contract pinned here
------------------------
**Whether the user is told is a property of the log, not of the learner.** The
oracle is the storage layer's own verdict on the same file: if
``storage.load_encrypted_lines_report(feedback_log_path()).lost`` is non-zero,
DERSIS knows the history is damaged, and a person must be told at startup. If
it is zero, nothing may be said. Every row below asserts that oracle against
the shape it built *before* the window is constructed, so the table cannot
drift away from what storage actually reports.

Not pinned, deliberately: a header-only EGL1 file reads back as
``LogRead([], 0)`` and is bit-for-bit indistinguishable from a brand-new empty
log. That is documented residue in the format, not a defect, and the
``header_only_log`` row demands silence for it exactly as for a new profile.

Also deliberately not pinned: *which* channel carries the message, or that any
particular method was called. The assertion is on the string a user is shown.
"""
import os
import re
import struct

import pytest


# ── The message, identified by its own text ──────────────────────────────────

def _distinctive_text(key):
    """The longest placeholder-free run of a translation's own text.

    Same technique as ``tests/test_settings_recovery.py``, and for the same
    reason: the suite runs pinned to Turkish, whose
    ``errors.feedback_log_damaged`` OPENS with ``{path}``. "Everything before
    the first brace" is the empty string there, and ``"" in anything`` is True
    — a matcher that passes with the report deleted from the app entirely.
    """
    from scheduler_app.translations import tr

    parts = [p.strip() for p in re.split(r"\{[^}]*\}", tr(key))]
    longest = max(parts, key=len) if parts else ""
    assert len(longest) >= 20, (
        "no placeholder-free run of %r is long enough to identify it (%r); "
        "matching on it would pass for the wrong reason" % (key, longest))
    return longest


# ── Log shapes ───────────────────────────────────────────────────────────────

def _entries(n):
    return [{"event": "manual_move", "n": i} for i in range(n)]


def _write_log(storage, n):
    path = storage.feedback_log_path()
    storage.save_encrypted_lines(_entries(n), path)
    assert open(path, "rb").read(4) == storage.storage._LOG_MAGIC, (
        "the fixture did not produce an EGL1 log")
    return path


def _wreck_every_payload(storage, path):
    """Flip one ciphertext bit inside every record, leaving the framing intact.

    Byte-for-byte the damage Phase 8's fixtures use, so the rows here differ
    from the row Phase 8 pinned in exactly one variable at a time. The length
    prefixes are untouched, so ``log_entry_count`` still counts every frame and
    the file keeps its size — which is what makes the G1 and G2 rows below
    isolate their gate instead of tripping several at once.
    """
    blob = bytearray(open(path, "rb").read())
    off = struct.calcsize(storage.storage._LOG_HEADER_FMT)
    wrecked = 0
    while off + 4 <= len(blob):
        (rec_len,) = struct.unpack_from(
            storage.storage._LOG_RECLEN_FMT, blob, off)
        if rec_len <= 0 or off + 4 + rec_len > len(blob):
            break
        blob[off + 4 + 43] ^= 0x01
        wrecked += 1
        off += 4 + rec_len
    with open(path, "wb") as handle:
        handle.write(bytes(blob))
    return wrecked


def _build_healthy(storage):
    _write_log(storage, 8)


def _build_absent(storage):
    assert not os.path.exists(storage.feedback_log_path())


def _build_header_only(storage):
    storage.save_encrypted_lines([], storage.feedback_log_path())


def _build_damaged_eight(storage):
    path = _write_log(storage, 8)
    assert _wreck_every_payload(storage, path) == 8
    assert storage.log_entry_count(path) == 8, (
        "the damage broke the framing; this row is meant to clear the "
        "entry-count gate, which is what makes it the control")


def _build_damaged_four(storage):
    """Four damaged records: below ``MIN_ENTRIES_TO_LEARN``.

    A user who has corrected the timetable four times has a history worth
    exactly as much to them as one who has corrected it five times.
    """
    path = _write_log(storage, 4)
    assert _wreck_every_payload(storage, path) == 4
    assert storage.log_entry_count(path) == 4, (
        "the fixture must leave 4 countable frames; the point of this row is "
        "that 4 < PreferenceLearner.MIN_ENTRIES_TO_LEARN")


def _build_damaged_after_a_full_pass(storage):
    """A log learned to the end, then damaged in place without changing size.

    The commonest real shape there is: the corruption arrives *after* DERSIS
    has already read the log — a bad sector, a half-flushed write, a sync
    conflict. ``learn()`` persists ``learned_size`` next to the weights, so the
    next launch stats the file, sees the same byte count, and returns without
    opening it.
    """
    from scheduler_app.learning.preference_learner import PreferenceLearner

    path = _write_log(storage, 8)
    learner = PreferenceLearner()
    learner.learn()
    assert learner._learned_through == 8, (
        "the fixture did not drain the healthy log (cursor=%r); the gates "
        "under test would not be the ones that fire"
        % (learner._learned_through,))
    size_before = storage.log_size(path)
    assert _wreck_every_payload(storage, path) == 8
    assert storage.log_size(path) == size_before, (
        "the damage changed the file size, so the size fast-path would not "
        "fire and this row would not test what it says it tests")


def _build_flipped_version_byte(storage):
    """An EGL1 log whose VERSION byte is damaged: ``LogRead.lost == -1``.

    ``log_entry_count`` checks both halves of the header and falls back to
    ``len(load_encrypted_lines(path))`` — which is 0 for a version the reader
    refuses. Eight records' worth of history, counted as none.
    """
    path = _write_log(storage, 8)
    blob = bytearray(open(path, "rb").read())
    blob[5] ^= 0x01
    with open(path, "wb") as handle:
        handle.write(bytes(blob))


def _build_unidentifiable_container(storage):
    """The legacy (non-``EGL1``) container path, unreadable.

    The handoff's claim is that the report fires only for ``EGL1``. It holds,
    but not because anything checks the format: a legacy container that will
    not decrypt yields ``LogRead([], -1)`` from storage AND 0 from
    ``log_entry_count`` (which falls back to ``len(load_encrypted_lines(...))``
    on any non-``EGL1`` head), and 0 < 5 is the same G2 that swallows the
    four-record row. A legacy container that DOES decrypt reports ``lost == 0``
    and has nothing to say — see ``legacy_readable_array``.
    """
    with open(storage.feedback_log_path(), "wb") as handle:
        handle.write(b"EGU1" + bytes(range(256)) + bytes(144))


def _build_legacy_readable_array(storage):
    """A pre-EGL1 single-array container that reads back fine: stay silent."""
    storage.save_encrypted(_entries(8), storage.feedback_log_path())


# ── The condition matrix ─────────────────────────────────────────────────────
#
# ``must_report`` is what a person must be told. It is checked against the
# storage layer's own ``LogRead.lost`` for the file the row just built, so the
# table cannot claim damage storage does not see, or silence for a file storage
# calls damaged.

CASES = [
    ("healthy_log", _build_healthy, False,
     "an intact log must never warn anybody"),
    ("absent_log", _build_absent, False,
     "a first launch has no history and nothing to report"),
    ("header_only_log", _build_header_only, False,
     "a header-only log reads as LogRead([], 0) and is indistinguishable "
     "from a new one; silence is the documented residue"),
    ("legacy_readable_array", _build_legacy_readable_array, False,
     "a pre-EGL1 container that still decrypts has lost nothing"),
    ("damaged_eight_records", _build_damaged_eight, True,
     "the one row Phase 8 already pins: EGL1, 8 frames, fresh cursor. If "
     "THIS row fails the probe itself is broken, not the app"),
    ("damaged_four_records", _build_damaged_four, True,
     "G2: total < MIN_ENTRIES_TO_LEARN returns before the read, so a short "
     "history can be destroyed in silence"),
    ("damaged_after_a_full_pass", _build_damaged_after_a_full_pass, True,
     "G1/G3: the log was already learned to the end and the damage did not "
     "change its size, so learn() never opens the file again"),
    ("flipped_version_byte", _build_flipped_version_byte, True,
     "LogRead.lost == -1, the strongest damage signal the format has, and "
     "log_entry_count collapses to 0 so G2 swallows it"),
    ("unidentifiable_container", _build_unidentifiable_container, True,
     "the legacy-container path: lost == -1 with 0 countable frames, "
     "unreportable by construction"),
]


# ── User-visible channels ────────────────────────────────────────────────────

class _Recorder:
    def __init__(self, ret):
        self._ret = ret
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._ret

    def texts(self):
        out = []
        for args, kwargs in self.calls:
            out.extend(a for a in args if isinstance(a, str))
            out.extend(v for v in kwargs.values() if isinstance(v, str))
        return out


class _Probe:
    """Every channel the app could plausibly use, as a union.

    The finding is "the user is never told", so the test must not dictate how
    they are told. Modals are neutralized rather than merely observed: an
    unpatched one blocks the whole suite under the offscreen platform.
    """

    def __init__(self):
        self.modals = {}
        self.toasts = []
        self.log_entries = []

    def texts(self, caplog):
        out = [m for m, _kind in self.toasts]
        out += [m for m, _kind in self.log_entries]
        out += [t for rec in self.modals.values() for t in rec.texts()]
        out += [r.getMessage() for r in caplog.records
                if r.levelno >= 30
                and "scheduler_app" in (r.pathname or "").replace("\\", "/")]
        return out

    def channels(self, caplog):
        hit = [name for name, rec in self.modals.items() if rec.calls]
        if self.toasts:
            hit.append("toast")
        if self.log_entries:
            hit.append("warning_log")
        return hit


@pytest.fixture
def probe(monkeypatch, caplog):
    from PyQt6.QtWidgets import QMessageBox

    from scheduler_app.ui.app import SchedulerApp
    from scheduler_app.ui.widgets import WarningLogPanel

    p = _Probe()
    for name, ret in (("information", QMessageBox.StandardButton.Ok),
                      ("warning", QMessageBox.StandardButton.Ok),
                      ("critical", QMessageBox.StandardButton.Ok),
                      ("question", QMessageBox.StandardButton.Yes)):
        rec = _Recorder(ret)
        p.modals[name] = rec
        monkeypatch.setattr(QMessageBox, name, staticmethod(rec))
    exec_rec = _Recorder(QMessageBox.StandardButton.Ok)
    p.modals["exec"] = exec_rec
    monkeypatch.setattr(QMessageBox, "exec", exec_rec)

    # Recorded, not constructed: a real Toast arms a 3 s QTimer that would
    # outlive the window this test tears down.
    monkeypatch.setattr(
        SchedulerApp, "_show_toast",
        lambda self, message, kind="info": p.toasts.append((message, kind)))
    real_log = WarningLogPanel.log

    def spy_log(self, message, kind="info"):
        p.log_entries.append((message, kind))
        return real_log(self, message, kind)

    monkeypatch.setattr(WarningLogPanel, "log", spy_log)
    caplog.set_level(0)
    return p


# ── The probe ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case_id,build,must_report,why",
                         CASES, ids=[c[0] for c in CASES])
def test_a_damaged_feedback_log_reaches_the_user_whatever_shape_it_takes(
        case_id, build, must_report, why,
        dersis_home, make_app, probe, qapp, caplog):
    """ST-DATA-002 / B9: the report must not depend on the learner's gates.

    A failure on a ``must_report`` row means DERSIS knew, at the storage layer,
    that part of the user's feedback history had stopped being readable, and
    said nothing — while the file sits there still repairable from a backup,
    which is precisely what ``errors.feedback_log_damaged`` ("The file has NOT
    been changed") invites the user to do.

    A failure on a silent row means every launch nags a user whose log is fine,
    which is how a real warning stops being read.
    """
    from scheduler_app import storage

    build(storage)
    path = storage.feedback_log_path()

    # The oracle: what the storage layer itself says about this exact file.
    # Asserted against the table so a row cannot lie about the shape it built.
    lost = storage.load_encrypted_lines_report(path).lost
    assert bool(lost) == must_report, (
        "the %s fixture no longer builds the shape it claims: storage reports "
        "LogRead.lost=%r for it, so this row is testing something else "
        "(%s)" % (case_id, lost, why))

    win = make_app()
    qapp.processEvents()   # drain the deferred modal onto the recorder

    wanted = _distinctive_text("errors.feedback_log_damaged")
    texts = probe.texts(caplog)
    told = any(wanted in t for t in texts)

    diagnosis = (
        "storage LogRead.lost=%r, log_entry_count=%r, log_size=%r, "
        "learner.last_read_lost=%r, learner cursor=%r, channels=%r"
        % (lost, storage.log_entry_count(path), storage.log_size(path),
           getattr(win._preference_learner, "last_read_lost", None),
           getattr(win._preference_learner, "_learned_through", None),
           probe.channels(caplog)))

    if must_report:
        assert told, (
            "[%s] the feedback log is damaged and the user was never told.\n"
            "  why this row exists: %s\n"
            "  %s\n"
            "  user-visible text was: %r"
            % (case_id, why, diagnosis, texts))
    else:
        assert not told, (
            "[%s] an undamaged feedback log was reported as damaged.\n"
            "  why this row exists: %s\n"
            "  %s"
            % (case_id, why, diagnosis))
