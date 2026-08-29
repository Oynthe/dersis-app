"""Scaling and durability of the feedback log — ST-PERF-005.

Why this module exists
----------------------
``FeedbackLogger`` is written to on ordinary UI actions: every drag-drop, every
accepted or rejected suggestion, every reschedule. It is the one file in DERSİS
whose size is a function of *how long the user has been using the app*. Today
each append rewrites the entire encrypted log, so the cost of using the app
grows with the history of using the app, and ``PreferenceLearner.learn()``
re-reads and re-trains on the whole file on every call.

The hot path is doubled: ``core/workflow.py::log_manual_move`` logs a
``manual_move`` and then immediately calls ``learn()``. One drag-drop pays a
whole-log rewrite *and* a whole-log read-and-retrain. ``ui/app.py::__init__``
pays the read-and-retrain again on every launch.

Why every assertion here is a count or a ratio, never a duration
---------------------------------------------------------------
An earlier draft of this module asserted that a single append onto an 800-entry
log takes at most 3x what it takes on an empty one. On this machine that ratio
measures **2.27 / 2.40 / 2.97** across three runs — it *passed* on an unfixed
codebase in 3/3 solo runs and 3/3 full-module runs, and only failed when the box
was loaded with eight busy processes. The reason is structural, not incidental:
on Windows the fixed per-append cost (temp file + ``os.replace``, ~11.4 ms
measured) is the same order as the entire size-dependent cost at n=800
(~15 ms), so the fixed floor sits in the denominator and crushes the ratio
toward 1. No threshold separates signal from noise there, and a "fail-now" test
that passes today pins nothing. It was removed.

Bytes have no such floor, and they are what the defect is actually made of. The
byte counter below therefore carries the whole load, on both axes: an append
must not *write* the whole log, and it must not *read* it either.

What was measured on ``.venv-audit`` at ``fix/phase-2-performance``
------------------------------------------------------------------
1. **Bytes written per append grow linearly with the log.** One append writes
   803 B on an empty log, 23 672 B when the log already holds 30 entries and
   84 424 B at 120 — **3.57x** for a 4x bigger log and **105x** the first
   append. Cumulative: 334 992 B for 30 appends against 5 199 317 B for 120
   (**15.52x**, where linear would be 4.0). Those 120 appends wrote 5.2 MB to
   persist 89 KB of feedback.

2. **Bytes read per append grow the same way**, because the append path loads
   and decrypts the entire file to find the end of it: 0 B for the first append,
   22 957 B at n=30, 83 709 B at n=120 (**3.65x**), 5 113 464 B cumulative for
   120 appends against 313 461 B for 30 (**16.31x**).

3. **``learn()`` re-does all of its work on every call.** With 40 entries on
   disk: 40 signals, then 40 *again* with no new feedback, then 45 after five
   more entries. A brand-new ``PreferenceLearner`` — what every app launch
   builds — re-applies all 40 a third time. Three passes over one unchanged
   40-entry log moved ``lecturer_gap`` from 6.56 to 10.55, ``student_gap`` from
   3.28 to 5.28, and ``train_count`` from 1 to 3: the log is not merely
   re-read, it is re-*learned*, so the user's learned preferences are a function
   of how often a pass happened to run. On a 100-entry log a repeat pass
   processes 100 signals and re-reads 70 288 B; on an 800-entry log, 800 signals
   and 561 688 B (**7.99x** for an 8x log).

4. **Appending to a damaged log destroys it.** A three-entry log of 2 232 B,
   truncated by 8 bytes: the append path swallowed the corruption and wrote a
   fresh 790 B one-entry file over it, leaving ``backups/`` empty. The user's
   feedback history was gone with no error anywhere.

Reading the assertions
----------------------
Every threshold is a ratio or a count. Each is stated so that the ideal
implementation lands far from the limit in one direction and today's code lands
far from it in the other, and the "linear" / "quadratic" / "ideal" reference
values are written into the failure message so a future reader can tell a
regression from noise.

Findings guarded here: ST-PERF-005, plus the ST-DATA-002 cursor. The storage
layer's half of ST-DATA-002 (a damaged log must return the records that still
decrypt, and must report the ones it lost instead of letting "unreadable" pass
for "empty") is guarded in ``tests/test_storage_roundtrip.py`` and is
deliberately not restated. What is asserted here is what that costs the
*learner*: the append path must not overwrite the damaged bytes, and the
learning cursor must not sit at 0 re-reading them forever.
"""
import builtins
import contextlib
import copy
import io
import os
import statistics
import struct

from scheduler_app.learning.feedback_logger import FeedbackLogger
from scheduler_app.learning.preference_learner import PreferenceLearner
from scheduler_app.storage import storage


# ── Instrumentation ─────────────────────────────────────────────────────────

@contextlib.contextmanager
def _counted_io(watch_dir):
    """Count bytes read from / written to any file under *watch_dir*.

    Patches ``builtins.open`` **and** ``io.open`` (the same function object, but
    ``pathlib`` looks it up through the ``io`` module) so the counter follows the
    implementation wherever the ST-PERF-005 fix decides to put it — a counter
    bound to one module's namespace would silently read zero if the append path
    moved to a new file, and zero passes every "is this small?" assertion. Every
    test using this helper therefore also asserts that the *write* counter is
    non-zero and that the appends actually landed, so a blind counter fails
    loudly instead of passing vacuously. (The *read* counter is deliberately not
    guarded that way: a correct append-only log may legitimately read nothing.)

    Yields a dict with ``written`` / ``read`` byte totals and ``opens_w`` /
    ``opens_r`` handle counts.
    """
    real_open = builtins.open
    watch = os.path.abspath(watch_dir)
    counts = {"written": 0, "read": 0, "opens_w": 0, "opens_r": 0}

    class _Counting:
        def __init__(self, fh):
            self._fh = fh

        def write(self, data):
            counts["written"] += len(data)
            return self._fh.write(data)

        def read(self, *args):
            data = self._fh.read(*args)
            counts["read"] += len(data)
            return data

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def __iter__(self):
            return iter(self._fh)

        def __getattr__(self, name):
            return getattr(self._fh, name)

    def _fake_open(file, mode="r", *args, **kwargs):
        try:
            path = os.path.abspath(os.fspath(file))
        except TypeError:          # an already-open file descriptor
            return real_open(file, mode, *args, **kwargs)
        handle = real_open(file, mode, *args, **kwargs)
        if path.startswith(watch):
            if any(ch in mode for ch in "wa+"):
                counts["opens_w"] += 1
            else:
                counts["opens_r"] += 1
            return _Counting(handle)
        return handle

    builtins.open = _fake_open
    io.open = _fake_open
    try:
        yield counts
    finally:
        builtins.open = real_open
        io.open = real_open


def _ratio(numerator, denominator):
    """Ratio that never raises inside an assertion message."""
    return numerator / denominator if denominator else float("inf")


# ── Fixtures / builders ─────────────────────────────────────────────────────

def _cls(i):
    """A realistically shaped class dict, uniquely identifiable by name."""
    return {
        "name": f"DERS-{i:05d}",
        "lecturer": "Ogr. Gor. Ayse Yilmaz",
        "targets": ["9-A", "9-B"],
        "duration": 2,
        "joint_session": True,
        "pinned": False,
    }


def _log_move(logger, i):
    """Append one ``manual_move`` entry through the public logger API.

    ``manual_move`` with both scores present is worth exactly one training
    signal to ``PreferenceLearner``, which makes ``learn()``'s return value a
    direct count of how many entries it processed.
    """
    logger.log_manual_move(
        _cls(i),
        "Pazartesi", "09:00", "D101",
        "Sali", "10:00", "D102",
        score_old=5.0, score_new=7.0,
    )


def _raw_entry(i):
    """The on-disk shape a ``manual_move`` append produces, built by hand.

    Used to seed a log in a single write for the ``learn()`` tests, where the
    cost of *building* the log is not what is under test.
    """
    return {
        "event": "manual_move",
        "class": {
            "name": f"DERS-{i:05d}",
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
        "timestamp": f"2026-08-26T12:00:{i % 60:02d}",
        "epoch": 1787000000.0 + i,
    }


def _seed_log(n_entries):
    """Write an *n_entries* feedback log in one shot, in the shipped format.

    ``storage.save_encrypted(list, path)`` is byte-for-byte what
    ``save_encrypted_lines`` does today (``storage.py``), so this is the
    on-disk file a real user already has. Seeding this way rather than by
    appending keeps the ``learn()`` tests measuring ``learn()`` — the cost of
    *building* a log is ST-PERF-005's other half and is measured separately.
    """
    storage.save_encrypted([_raw_entry(i) for i in range(n_entries)],
                           storage.feedback_log_path())


def _logs_dir():
    return storage.sub_dir(storage.LOGS_DIR)


def _names(entries):
    return [e.get("class", {}).get("name") for e in entries]


# How far the byte-growth test grows the log. A 4x size difference is all the
# separation the thresholds need (today's per-append cost grows ~3.8x for it,
# against a 1.5x limit), and the absolute size is kept small on purpose: the
# wall time of an append loop on this platform is dominated by per-append file
# operations, not by bytes, and it is wildly variable — the same 400-append loop
# measured 6.8 s and 24.4 s on different runs, and 200 appends once cost 22.6 s.
# Cost therefore scales with the *number* of appends, so the loop is run once
# and both byte counters are read from it.
SMALL_N = 30
LARGE_N = 120

# Enough entries for the learner's MIN_ENTRIES_TO_LEARN=5 guard to be irrelevant
# and for repeated passes to move the weights unmistakably, while staying well
# inside the ±2x-of-default delta clamp in ``_update_delta``.
LEARN_N = 40


# ── 1. Append must be O(1) in bytes written ─────────────────────────────────

def test_one_append_neither_rewrites_nor_rereads_the_whole_log(dersis_home):
    """ST-PERF-005: one feedback append must not touch the whole log.

    A failure means the app gets slower the longer someone uses it — by the
    120th drag-drop of a school year a single move already moves ~168 KB of
    encrypted I/O instead of the ~0.7 KB the entry is worth, and the UI stalls
    proportionally. Both halves of the O(n²) are asserted here, from one append
    loop, in bytes, so that no machine speed enters the result:

    * **writes** — today ``append_encrypted_entry`` re-encrypts and rewrites
      every entry ever logged. Measured: 803 B for the first append, 23 672 B at
      n=30, 84 424 B at n=120 (3.57x for a 4x bigger log, 105x the first
      append); cumulative 334 992 B for 30 appends against 5 199 317 B for 120
      (15.52x, where linear would be 4.0).
    * **reads** — and it gets that list by loading and decrypting the entire
      file first, so an append reads the history as well as rewriting it.
      Measured: 0 B for the first append, 22 957 B at n=30, 83 709 B at n=120
      (3.65x), 5 113 464 B cumulative against 313 461 B (16.31x).

    The read half is a genuinely separate defect, not a restatement: an
    implementation could write only the new record while still loading the whole
    file to find where the end is. A correct append-only log reads nothing at
    all, which the one-sided ratios below permit.

    The 20x allowance on the first-append comparison leaves room for per-record
    framing, an index, or a checkpoint.
    """
    logger = FeedbackLogger()
    written, read = [], []

    with _counted_io(_logs_dir()) as counts:
        for i in range(LARGE_N):
            w0, r0 = counts["written"], counts["read"]
            _log_move(logger, i)
            written.append(counts["written"] - w0)
            read.append(counts["read"] - r0)

    assert counts["written"] > 0, (
        "instrumentation wrote nothing — the byte counter is not seeing the "
        "append path, so every threshold below would pass vacuously")
    assert logger.entry_count() == LARGE_N, (
        "appends did not actually land; the byte measurements are meaningless")

    def _sample(series):
        return (series[0],
                statistics.median(series[SMALL_N:SMALL_N + 5]),
                statistics.median(series[LARGE_N - 5:LARGE_N]),
                sum(series[:SMALL_N]),
                sum(series))

    w_first, w_early, w_late, w_cum_small, w_cum_large = _sample(written)
    _r_first, r_early, r_late, r_cum_small, r_cum_large = _sample(read)

    assert w_first > 0, "the first append wrote nothing"

    assert w_late <= w_early * 1.5, (
        f"per-append bytes WRITTEN grow with log size: {w_early} B at "
        f"n={SMALL_N} -> {w_late} B at n={LARGE_N} "
        f"({_ratio(w_late, w_early):.2f}x for a 4x bigger log; O(1) append "
        f"~1.0, full rewrite ~4.0)")

    assert w_cum_large <= w_cum_small * 6.0, (
        f"total bytes WRITTEN are super-linear in the number of appends: "
        f"{w_cum_small} B for {SMALL_N} appends -> {w_cum_large} B for "
        f"{LARGE_N} ({_ratio(w_cum_large, w_cum_small):.2f}x; linear 4.0, "
        f"quadratic 16.0)")

    assert w_late <= w_first * 20, (
        f"an append onto a {LARGE_N}-entry log writes {w_late} B where the very "
        f"first append onto an empty log wrote {w_first} B "
        f"({_ratio(w_late, w_first):.1f}x); an append-only log writes ~1x")

    assert r_late <= max(r_early, 1) * 1.5, (
        f"per-append bytes READ grow with log size: {r_early} B at n={SMALL_N} "
        f"-> {r_late} B at n={LARGE_N} ({_ratio(r_late, r_early):.2f}x for a 4x "
        f"bigger log; an append-only log reads ~0)")

    assert r_cum_large <= max(r_cum_small, 1) * 6.0, (
        f"total bytes READ are super-linear in the number of appends: "
        f"{r_cum_small} B for {SMALL_N} appends -> {r_cum_large} B for "
        f"{LARGE_N} ({_ratio(r_cum_large, r_cum_small):.2f}x; linear 4.0, "
        f"quadratic 16.0)")


# ── 2. The log survives the format change ───────────────────────────────────

def test_all_entries_stay_readable_and_ordered_across_appends(dersis_home):
    """ST-PERF-005: changing the log format must not lose or reorder history.

    A failure means the fix that makes appends cheap also silently drops or
    shuffles the user's feedback history — the learner would then train on a
    different past than the one the user lived.
    """
    logger = FeedbackLogger()
    for i in range(18):
        _log_move(logger, i)

    first_batch = logger.get_entries()
    assert _names(first_batch) == [f"DERS-{i:05d}" for i in range(18)]

    for i in range(18, 40):
        _log_move(logger, i)

    entries = logger.get_entries()
    assert len(entries) == 40
    assert _names(entries) == [f"DERS-{i:05d}" for i in range(40)], (
        "appended entries are not in write order, or earlier entries were lost")
    assert logger.entry_count() == 40

    # The first 18 must come back byte-for-byte identical, not merely present.
    assert entries[:18] == first_batch, (
        "existing entries were rewritten by later appends")

    # Every entry keeps the fields the learner and the UI read.
    for entry in entries:
        assert entry["event"] == "manual_move"
        assert entry["signal"] == "prefer_new"
        assert entry["old_placement"] == {
            "day": "Pazartesi", "slot": "09:00", "room": "D101"}
        assert isinstance(entry.get("epoch"), float)
        assert isinstance(entry.get("timestamp"), str) and entry["timestamp"]

    # The read helpers keep their contract on top of the new format.
    assert logger.get_entries(event_type="correction") == []
    assert _names(logger.get_entries(limit=3)) == [
        f"DERS-{i:05d}" for i in (37, 38, 39)], "limit must return the newest"


def test_each_append_is_durable_before_the_next_one(dersis_home):
    """ST-PERF-005: a cheap append must still be a *committed* append.

    A failure means the speed-up was bought with an in-memory buffer that a
    crash — or simply closing the app, which never calls any logger flush —
    throws away, so a user's most recent feedback silently never happened.
    Each append is read back by a *freshly constructed* logger, which is what
    the next launch of DERSİS does.
    """
    writer = FeedbackLogger()
    for i in range(5):
        _log_move(writer, i)
        reader = FeedbackLogger()
        assert _names(reader.get_entries()) == [
            f"DERS-{k:05d}" for k in range(i + 1)], (
            f"entry {i} was not on disk immediately after it was logged")


# ── 3. Back-compat: an already-shipped log must keep loading ────────────────

def test_a_log_written_by_todays_code_still_loads_and_appends(dersis_home):
    """ST-PERF-005: existing users' feedback logs must survive the format change.

    DERSİS is a shipped desktop app; ``logs/feedback_log.egu`` already exists on
    real machines as a single encrypted JSON array. A failure means upgrading
    DERSİS quietly discards everything the app has learned about that user — or
    worse, the first post-upgrade append overwrites it.

    The "old" file is built with ``storage.save_encrypted`` — which is exactly
    what ``save_encrypted_lines`` does today (``storage.py``) and is the
    generic single-object container writer used for settings, saves and learned
    weights. Building it that way rather than through ``save_encrypted_lines``
    means this test keeps testing *back-compat* even if the fix repoints the
    ``…_lines`` helpers at a new format.
    """
    path = storage.feedback_log_path()
    legacy = [_raw_entry(i) for i in range(30)]
    storage.save_encrypted(copy.deepcopy(legacy), path)

    assert storage.load_encrypted_lines(path) == legacy, (
        "the current on-disk array format no longer round-trips")

    logger = FeedbackLogger()
    assert logger.entry_count() == 30
    assert _names(logger.get_entries()) == [f"DERS-{i:05d}" for i in range(30)]

    _log_move(logger, 999)

    after = logger.get_entries()
    assert len(after) == 31, "appending to a pre-existing log lost entries"
    assert after[:30] == legacy, (
        "the 30 pre-existing entries were altered or reordered by one append")
    assert after[30]["class"]["name"] == "DERS-00999"

    # And the learner must still be able to read that history.
    assert PreferenceLearner().learn() > 0


def test_a_legacy_uva_feedback_log_still_loads_and_appends(dersis_home):
    """ST-PERF-005: the pre-rename ``.uva`` log is still a live on-disk case.

    ``storage.feedback_log_path()`` falls back to ``logs/feedback_log.uva`` when
    no ``.egu`` exists (``storage.py::feedback_log_path`` via
    ``storage.py::_with_legacy_fallback``), so a user who has not logged
    feedback since the rename still
    has their history in a ``.uva``. A failure means that history is dropped —
    or destroyed by the first append — on upgrade.
    """
    uva = os.path.join(_logs_dir(), "feedback_log.uva")
    legacy = [_raw_entry(i) for i in range(12)]
    storage.save_encrypted(copy.deepcopy(legacy), uva)   # see the test above

    logger = FeedbackLogger()
    assert _names(logger.get_entries()) == [f"DERS-{i:05d}" for i in range(12)], (
        "the .uva fallback in feedback_log_path() stopped being honoured, or "
        "the legacy file no longer parses")

    _log_move(logger, 777)

    after = FeedbackLogger().get_entries()
    assert len(after) == 13, "appending to a legacy .uva log lost entries"
    assert after[:12] == legacy
    assert after[12]["class"]["name"] == "DERS-00777"


# ── 4. learn() must not redo the whole history on every call ────────────────

def test_learn_does_not_reprocess_entries_it_has_already_learned_from(
        dersis_home):
    """ST-PERF-005: ``learn()`` must consume new feedback, not the whole log.

    A failure means every learning pass costs the full history — and, because
    ``workflow.py::log_manual_move`` fires a pass after *every* drag-drop, a user who moves
    ten classes re-processes the first move ten times. Measured today: 40
    entries -> 40 signals, then 40 signals *again* with no new feedback, then 45
    after five more entries.

    ``manual_move`` entries are worth exactly one signal each, so ``learn()``'s
    return value is a direct, machine-independent count of the work it did.
    The lower bound on the incremental pass matters as much as the upper one: a
    cursor that is advanced past everything and never moved again would make the
    work go to zero by simply never learning anything again.
    """
    _seed_log(LEARN_N)

    learner = PreferenceLearner()
    first = learner.learn()
    assert first == LEARN_N, (
        f"a {LEARN_N}-entry manual_move log should be worth exactly {LEARN_N} "
        f"signals on a first pass, got {first}")

    repeat = learner.learn()
    assert repeat == 0, (
        f"learn() reprocessed {repeat} signals with no new feedback on disk "
        f"(the first pass processed {first})")

    logger = FeedbackLogger()
    for i in range(LEARN_N, LEARN_N + 5):
        _log_move(logger, i)

    incremental = learner.learn()
    assert incremental <= 5, (
        f"learn() processed {incremental} signals after only 5 new entries "
        f"were appended; it is re-reading the whole {logger.entry_count()}"
        f"-entry log")
    assert incremental >= 1, (
        "learn() ignored 5 brand-new feedback entries entirely — the learner "
        "stopped learning instead of learning incrementally")


def test_repeat_learn_does_not_move_weights_it_already_applied(dersis_home):
    """ST-PERF-005: re-reading the log is not free — it corrupts the weights.

    A failure means DERSİS's idea of the user's preferences depends on how many
    times a learning pass happened to run rather than on what the user did.
    Measured today: three passes over an unchanged 40-entry log pushed
    ``train_count`` to 3, ``lecturer_gap`` from 6.56 to 10.55 and ``student_gap``
    from 3.28 to 5.28.

    This is the same root cause as the test above, stated as its consequence
    rather than as its cost. It is kept separate on purpose: a fix that merely
    *caches* the parsed log makes the cost assertion pass while leaving the
    weights corrupted, and that would not be a fix. (Verified: a cache-only
    implementation passes the cost assertions and fails this one.)

    The final clause is the restart case, and it is the one that decides whether
    the fix's high-water mark has to be **persisted**. ``ui/app.py::__init__``
    constructs a ``PreferenceLearner`` and calls ``learn()`` on every launch, so
    a cursor that lives only in memory still re-applies the entire history once
    per app start — the weights drift a little every time the user opens DERSİS.
    """
    _seed_log(LEARN_N)

    learner = PreferenceLearner()
    learner.learn()
    weights_after_first = dict(learner.get_weights())
    train_count_after_first = learner.train_count

    learner.learn()
    learner.learn()

    assert learner.get_weights() == weights_after_first, (
        "two extra learning passes over an unchanged log moved the weights")
    assert learner.train_count == train_count_after_first, (
        f"train_count grew from {train_count_after_first} to "
        f"{learner.train_count} without any new feedback")

    # A new process — i.e. the next launch of DERSİS — must not re-apply it all.
    restarted = PreferenceLearner()
    reprocessed = restarted.learn()
    assert reprocessed == 0, (
        f"a freshly constructed PreferenceLearner reprocessed {reprocessed} "
        f"signals from a log it had already learned from; every app launch "
        f"re-trains on the user's whole history")
    assert restarted.get_weights() == weights_after_first, (
        "restarting DERSİS moved the learned weights without any new feedback")
    assert restarted.train_count == train_count_after_first, (
        f"train_count grew from {train_count_after_first} to "
        f"{restarted.train_count} across a restart with no new feedback")


def test_repeat_learn_cost_does_not_grow_with_log_size(dersis_home):
    """ST-PERF-005: a learning pass with nothing new must cost the same at any size.

    A failure means the learner's overhead is a function of how long the user
    has owned DERSİS. Both halves are machine-independent: the number of signals
    the pass reports, and the bytes it reads off disk. Measured today, a repeat
    pass reports 100 signals and re-reads 70 288 B on a 100-entry log, and 800
    signals / 561 688 B on an 800-entry one — an 8x log costs 8x on both axes.
    The assertions allow 2x for a checkpoint or index that legitimately grows a
    little with the log.

    The bytes-read half is the one that decides how much of the fix is needed: a
    cursor alone is *not* enough, because ``learn()`` reaches the tail through
    ``feedback_logger.get_entries()``, which decrypts the whole file to slice it.
    (Verified against a simulated cursor-only implementation: the signal
    assertion passes, this one fails at 7.99x.) A pass with nothing new must be
    able to decide that from a header or a record count, not from the history.

    Each size starts from a clean ``learned_weights.egu`` so the measurement
    cannot depend on which size was measured first.
    """
    def _repeat_pass(n_entries):
        """Seed the real log with *n_entries*, do one full pass, measure the next."""
        weights = storage.learned_weights_path()
        if os.path.exists(weights):
            os.remove(weights)
        _seed_log(n_entries)
        learner = PreferenceLearner()
        assert learner.learn() > 0, "seeded log produced no training signals"
        with _counted_io(_logs_dir()) as counts:
            signals = learner.learn()
        return signals, counts["read"]

    small_signals, small_read = _repeat_pass(100)
    large_signals, large_read = _repeat_pass(800)

    # A correct implementation may read nothing at all on a repeat pass; the
    # ``max(..., 1)`` floors keep that case from dividing by zero rather than
    # relaxing anything.
    assert large_signals <= max(small_signals, 1), (
        f"a repeat learning pass processed {small_signals} signals on a "
        f"100-entry log and {large_signals} on an 800-entry one; the work "
        f"scales with the history")
    assert large_read <= max(small_read, 1) * 2.0, (
        f"a repeat learning pass read {small_read} B on a 100-entry log and "
        f"{large_read} B on an 800-entry one "
        f"({_ratio(large_read, small_read):.2f}x for an 8x log)")


def test_launch_cost_does_not_grow_with_log_size(dersis_home):
    """R1: the LAUNCH pass must not decrypt the history either.

    The test above deliberately primes with one ``learn()`` and measures the
    SECOND, idle one — so nothing in this repository measured a first-of-process
    pass at all, and that is the one ``SchedulerApp.__init__`` pays,
    synchronously, before the window exists. ``_check_log_health`` sits above
    every gate and, with ``_checked_span`` None on construction, read and
    decrypted the whole log on it.

    Measured 2026-08-29 on .venv-audit, min of 5, fresh learner per rep over a
    caught-up log: 17.6 ms at 2 000 records, 44.5 ms at 5 000, 94.1 ms at
    10 000, 199.8 ms at 20 000 (11.7 MB), 538.2 ms at 50 000 — linear, ~10 us
    per record, and the log is append-only with no rotation, cap or pruning
    anywhere in ``learning/`` or ``storage/``. A whole ``SchedulerApp.__init__``
    costs 90 ms with no feedback log at all, so at 20 000 records this one read
    was more than the entire rest of the launch. With the clean-span anchor:
    10.7 ms at 20 000, 26.0 ms at 50 000.

    Counted in AES-GCM decrypts, not milliseconds, for the reason at the top of
    this module: a duration threshold on this box is noise, and the decrypt IS
    the cost. The one or two decrypts a correct implementation still does are
    ``learned_weights.egu``, not the log.
    """
    def _launch_decrypts(n_entries):
        """Seed *n_entries*, catch the learner up, then count a fresh launch."""
        weights = storage.learned_weights_path()
        if os.path.exists(weights):
            os.remove(weights)
        # save_encrypted_lines, not _seed_log: this test is about the EGL1
        # append-only log the app actually writes, whose records are decrypted
        # one at a time. _seed_log writes the LEGACY single-array container.
        storage.save_encrypted_lines([_raw_entry(i) for i in range(n_entries)],
                                     storage.feedback_log_path())
        assert PreferenceLearner().learn() == n_entries

        calls = []
        real = storage._parse_container

        def counted(blob):
            calls.append(len(blob))
            return real(blob)

        storage._parse_container = counted
        try:
            relaunched = PreferenceLearner()
            relaunched.learn()
        finally:
            storage._parse_container = real
        assert relaunched.last_read_lost == 0, (
            "a healthy %r-entry log reported a loss at launch (%r)"
            % (n_entries, relaunched.last_read_lost))
        return len(calls)

    small = _launch_decrypts(100)
    large = _launch_decrypts(800)

    assert large <= max(small, 1) * 2.0, (
        f"the first learning pass of a process decrypted {small} records on a "
        f"100-entry log and {large} on an 800-entry one "
        f"({_ratio(large, small):.2f}x for an 8x log). That pass runs inside "
        f"SchedulerApp.__init__ on every launch, so the wait before the window "
        f"appears is a function of how long the user has owned DERSİS: 199.8 ms "
        f"at 20 000 records, and nothing caps the log")


# ── 5. A damaged log must not be destroyed by the next append ───────────────

def _all_files_under(root):
    for base, _dirs, files in os.walk(root):
        for name in files:
            yield os.path.join(base, name)


def _bytes_survive(root, blob):
    """True if *blob* is still on disk anywhere under *root*.

    Containment, not equality: an append-only log that appends onto the damaged
    file leaves the damaged bytes as a *prefix* of a longer file, and those
    bytes have plainly not been destroyed. Requiring exact equality would fail
    the very design ST-PERF-005 is being fixed with.
    """
    for path in _all_files_under(root):
        try:
            with open(path, "rb") as handle:
                if blob in handle.read():
                    return True
        except OSError:
            continue
    return False


def _make_damaged_log(n_entries, truncate_by):
    """Build a real log by appending, truncate it, and return the damaged bytes.

    Built by *appending* rather than by writing a container directly, so the
    file is always in whatever format the code under test actually produces.
    """
    path = storage.feedback_log_path()
    seed = FeedbackLogger()
    for i in range(n_entries):
        _log_move(seed, i)
    assert seed.entry_count() == n_entries
    with open(path, "rb") as handle:
        healthy = handle.read()
    damaged = healthy[:-truncate_by]
    with open(path, "wb") as handle:
        handle.write(damaged)
    assert damaged != healthy and len(damaged) > 0
    return path, damaged


def test_storage_append_does_not_destroy_a_corrupt_log(dersis_home):
    """ST-PERF-005: a damaged feedback log must survive the next append.

    ST-DATA-002 (pinned in ``tests/test_storage_roundtrip.py``) covers the
    *read* half — a corrupt log must raise instead of reading as empty. This is
    the destructive half, and it belongs to the append path being rewritten
    here: because the read returns ``[]``, the very next append writes a fresh
    one-entry file over the damaged one. Measured today: a 2 232 B three-entry
    log truncated by 8 bytes became a 790 B one-entry file, and ``backups/`` was
    empty — the history was unrecoverable.

    A failure means one bad sector plus one drag-drop permanently erases
    everything DERSİS has learned about the user. Every non-destructive outcome
    is accepted: refuse the append and leave the file alone, quarantine it the
    way ``storage.quarantine_corrupt`` (``storage.py``) already does for
    saves and settings, or append past the damage. What is not accepted is the
    bytes ceasing to exist — ST-DATA-014 settled that principle: nothing is ever
    deleted.
    """
    path, damaged = _make_damaged_log(3, truncate_by=8)

    with contextlib.suppress(Exception):
        storage.append_encrypted_entry(_raw_entry(99), path)

    assert _bytes_survive(str(dersis_home), damaged), (
        "storage.append_encrypted_entry overwrote a damaged feedback log; the "
        "user's history is gone and nothing was quarantined")


def test_logger_append_does_not_destroy_a_corrupt_log(dersis_home):
    """ST-PERF-005: the same, through the logger the UI actually calls.

    A failure means the destruction happens on an ordinary drag-drop and the
    user is never told, because ``FeedbackLogger._write_entry``
    (``feedback_logger.py``) swallows every exception on the way out.

    Deliberately a separate test with its own ``dersis_home`` rather than a
    second half of the one above: if both ran in one sandbox and the storage
    path quarantined its copy, that quarantined file alone would satisfy this
    assertion no matter what the logger did to the second log.
    """
    _path, damaged = _make_damaged_log(5, truncate_by=16)

    _log_move(FeedbackLogger(), 1)

    assert _bytes_survive(str(dersis_home), damaged), (
        "FeedbackLogger overwrote a damaged feedback log; because _write_entry "
        "swallows exceptions the user is never told")


# ── 6. ST-DATA-002 — the learning cursor must step past unreadable bytes ────

def _wreck_every_record(path):
    """Flip one ciphertext bit inside every framed record of an EGL1 log.

    Damages the payloads and leaves the framing intact, which is what a bad
    sector looks like: ``log_entry_count`` still walks the length prefixes and
    still says 12, so the ``MIN_ENTRIES_TO_LEARN`` gate is still cleared and
    ``learn()`` really does reach the code under test.
    """
    blob = bytearray(open(path, "rb").read())
    assert bytes(blob[:4]) == storage._LOG_MAGIC, (
        "not an EGL1 log (%r); this helper's frame arithmetic would be "
        "meaningless" % (bytes(blob[:4]),))
    off = struct.calcsize(storage._LOG_HEADER_FMT)
    wrecked = 0
    while off + 4 <= len(blob):
        (rec_len,) = struct.unpack_from(storage._LOG_RECLEN_FMT, blob, off)
        if rec_len <= 0 or off + 4 + rec_len > len(blob):
            break
        blob[off + 4 + 43] ^= 0x01   # inside the record's ciphertext
        wrecked += 1
        off += 4 + rec_len
    with open(path, "wb") as handle:
        handle.write(bytes(blob))
    return wrecked


def test_the_learner_does_not_re_read_an_unreadable_log_forever(dersis_home):
    """ST-DATA-002: an unreadable prefix must be stepped over, not re-read.

    Measured before the fix: the cursor stayed at 0 across four consecutive
    ``learn()`` calls on a damaged 12-entry log, and a 2 000-record log with one
    flipped bit burned 27.8 ms of decryption on *every* call — and ``learn()``
    runs after every manual move, so the cost never ends and the learning
    outage is permanent.

    A failure means DERSİS quietly stops learning for good the first time a
    single byte of the feedback log goes bad, while paying full price to
    re-decrypt and re-discard it forever. Asserted on the cursor, never on the
    clock (``tests/README.md``).
    """
    path = storage.feedback_log_path()
    logger = FeedbackLogger()
    for i in range(12):
        _log_move(logger, i)
    assert logger.entry_count() == 12

    assert _wreck_every_record(path) == 12, "the fixture damaged the wrong count"
    assert storage.load_encrypted_lines(path) == [], (
        "the fixture left readable records, so the empty-read branch under "
        "test is never reached")

    learner = PreferenceLearner()
    assert learner._learned_through == 0

    assert learner.learn() == 0, "unreadable records produced training signals"
    assert learner._learned_through == learner.feedback_logger.entry_count(), (
        "the cursor stayed at %r on a %r-frame log: every future learning pass "
        "will decrypt and discard the same unreadable bytes again"
        % (learner._learned_through, learner.feedback_logger.entry_count()))
    assert learner.last_read_lost == 12, (
        "the learner did not record the loss, so the UI has nothing to report "
        "to the user (last_read_lost=%r)" % (learner.last_read_lost,))

    # And a healthy log must not report a loss — otherwise the UI warns every
    # user on every launch and the signal is worthless.
    fresh_home_path = os.path.join(_logs_dir(), "healthy.egu")
    storage.save_encrypted_lines([_raw_entry(i) for i in range(6)],
                                 fresh_home_path)
    assert storage.load_encrypted_lines_report(fresh_home_path).lost == 0


def _flip_one_prefix_bit(path, index):
    """Flip a single bit in record *index*'s 4-byte LENGTH PREFIX.

    Deliberately NOT what ``_wreck_every_record`` does. That helper's own
    docstring says it "leaves the framing intact ... so the MIN_ENTRIES_TO_LEARN
    gate is still cleared and learn() really does reach the code under test" —
    every other ST-DATA-002 test in this suite damages payloads only, and the
    prefix is the one field a prefix-walking reader has no way to check.
    Returns the number of ``EGU1`` record starts still present, so the caller
    can assert the records themselves were not touched.
    """
    blob = bytearray(open(path, "rb").read())
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
            struct.pack_into(storage._LOG_RECLEN_FMT, blob, off, rec_len ^ 0x2)
            with open(path, "wb") as handle:
                handle.write(bytes(blob))
            return bytes(blob).count(storage._MAGIC)
        seen += 1
        off += 4 + rec_len
    raise AssertionError("the log has no record %r" % (index,))


def test_a_flipped_length_prefix_does_not_end_learning_for_good(dersis_home):
    """ST-DATA-002: the damage shape that produces the WORST outcome.

    ``learn()`` gates on ``self._learned_through >= total`` and returns before
    reading anything, so a frozen ``entry_count()`` is a learning outage that
    can never end: the cursor is never overtaken, ``last_read_lost`` keeps its
    constructor value of 0, and ``_report_damaged_feedback_log``
    (``ui/app.py``, ``if not lost: return``) therefore says nothing. The user
    goes on correcting the timetable, every correction is written to disk, and
    none of it is ever read again.

    The damage is placed AFTER the cursor on purpose. That is the shape where
    the learner really loses something it had not read yet, so it is the shape
    that must reach ``last_read_lost``; and it is also the shape that used to
    freeze the count at a value the cursor then caught up to and never passed
    again.

    Measured before the fix on this exact fixture: one flipped bit in record
    14's length prefix took ``entry_count()`` from 18 to 15, the restarted
    learner drained it to a cursor of 15, and four further real ``manual_move``
    appends (file 5766 bytes and growing) left the count at 15 and ``learn()``
    at 0 for ever after, with ``last_read_lost`` back at 0. Every ``EGU1``
    record start was still in the file the whole time.

    A failure means learning is dead and silent, which is ST-DATA-002 itself.
    """
    path = storage.feedback_log_path()
    logger = FeedbackLogger()
    for i in range(12):
        _log_move(logger, i)
    assert logger.entry_count() == 12

    learner = PreferenceLearner()
    assert learner.learn() > 0, "the healthy log produced no signals"
    assert learner._learned_through == 12, (
        "the fixture did not leave the cursor at the end of the healthy log "
        "(%r); the gate under test would not be the one that fires"
        % (learner._learned_through,))

    for i in range(100, 106):
        _log_move(logger, i)
    assert logger.entry_count() == 18

    starts = _flip_one_prefix_bit(path, 14)
    assert starts == 18, (
        "the fixture destroyed a record start (%r left); this test is about "
        "the PREFIX, not the record" % (starts,))

    restarted = PreferenceLearner()
    assert restarted._learned_through == 12, "the cursor did not survive the restart"
    assert restarted.learn() > 0, (
        "one flipped bit in a length prefix stopped learning: entry_count() "
        "is %r against a cursor of 12"
        % (restarted.feedback_logger.entry_count(),))
    assert restarted.last_read_lost >= 1, (
        "a record past the cursor was destroyed and the learner reported no "
        "loss (last_read_lost=%r), so _report_damaged_feedback_log returns at "
        "its `if not lost` and the user is told nothing"
        % (restarted.last_read_lost,))
    drained = restarted._learned_through

    # The permanence half: four more real corrections must still be learned
    # from. A count frozen by the damage can never be overtaken again, so this
    # is what separates "one damaged record" from "learning is over".
    for i in range(200, 204):
        _log_move(logger, i)
    relaunched = PreferenceLearner()
    assert relaunched.feedback_logger.entry_count() >= drained + 4, (
        "the entry count did not grow by the four records that were really "
        "appended (%r against a cursor of %r): the learner's gate can never "
        "open again" % (relaunched.feedback_logger.entry_count(), drained))
    assert relaunched.learn() > 0, (
        "the four corrections the user made after the damage produced no "
        "training signals; they are on disk and will never be read")
