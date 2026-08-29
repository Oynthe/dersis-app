"""Preference learning from user feedback.

Analyzes feedback log entries to adjust the PlacementScorer's weights
over time. The learning process:

1. Reads feedback entries (manual moves, corrections, accepted/rejected)
2. Extracts preference signals (user preferred placement A over B)
3. Adjusts weights to better align scorer output with user preferences
4. Persists learned weights for future sessions

Uses a simple online gradient approach: for each preference signal
(user preferred placement A over placement B), nudge weights so that
score(A) < score(B) (since lower is better).
"""

import hashlib
import os

from scheduler_app.placement_scorer import DEFAULT_WEIGHTS
from scheduler_app.translations import tr
from scheduler_app.feedback_logger import FeedbackLogger
from scheduler_app import storage


class PreferenceLearner:
    """Learn scoring weight adjustments from user feedback.

    Maintains a set of weight adjustments (deltas) that modify the
    default PlacementScorer weights based on observed user preferences.
    """

    LEARNING_RATE = 0.05
    MOMENTUM = 0.9
    MIN_ENTRIES_TO_LEARN = 5
    # Bytes per window of the cheap "did the log change underneath us?" check in
    # _span_fingerprint. THREE windows — start, middle, end — so the check costs
    # the same on a 2 000-record log as on a 10-record one. Hashing the whole
    # consumed span instead was measured against
    # tests/test_feedback_log_scaling.py::
    # test_repeat_learn_cost_does_not_grow_with_log_size, which allows an idle
    # pass over an 8x log to read 2x as much: a full-prefix hash reads 8x
    # (70 288 B against 561 688 B on that test's own fixtures) and fails it
    # outright, because a whole-prefix hash IS an O(n) read of the history.
    # 24 576 B here, at either size.
    #
    # Measured 2026-08-29 on .venv-audit, best of 7: an idle learn() went from
    # 0.017 ms to 0.068 ms, and — the number that matters — it is 0.068 ms on a
    # 100-record log AND on a 2 000-record one. A full-prefix SHA-256 alone
    # costs 0.133 ms at 100 records and 0.678 ms at 2 000, i.e. it puts the
    # growth back.
    _FINGERPRINT_WINDOW = 8192

    def __init__(self, data_dir=None):
        if data_dir is None:
            data_dir = storage.sub_dir(storage.LEARNING_DIR)
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.weights_path = storage.learned_weights_path()
        self.feedback_logger = FeedbackLogger()

        # Weight deltas (adjustments to defaults)
        self.weight_deltas = {}
        # Momentum terms for smooth updates
        self._velocity = {}
        # Number of training iterations completed
        self.train_count = 0
        self._learned_through = 0
        self._learned_size = 0
        # ST-DATA-002: how many records the last read could not decode. Read by
        # SchedulerApp._flush_startup_settings_report, which is what turns it
        # into something the user sees.
        self.last_read_lost = 0
        # ``(size, fingerprint)`` of the log as of the last integrity read, or
        # None when no integrity read has happened in THIS process.
        # Deliberately NOT persisted next to the weights, unlike _learned_size:
        # None on construction is exactly what forces one full integrity read
        # per launch, and the launch pass is the only one whose verdict anybody
        # reads (SchedulerApp._flush_startup_settings_report, once, at startup).
        # Persisting it would make a corruption that arrived while DERSİS was
        # closed survive unlooked-at forever, which is the B8 defect again.
        self._checked_span = None

        self._load_weights()

    def get_weights(self):
        """Return the current effective weights (defaults + learned deltas)."""
        weights = dict(DEFAULT_WEIGHTS)
        for key, delta in self.weight_deltas.items():
            if key in weights:
                # Ensure weights don't go negative
                weights[key] = max(0.01, weights[key] + delta)
        return weights

    def _span_fingerprint(self, span):
        """A cheap identity for the first *span* bytes of the feedback log.

        SHA-256 over three bounded windows — start, middle, end — plus *span*
        itself, so that a change of length and a change of content are both
        visible and neither costs more on a long history than on a short one.
        Returns None when the bytes cannot be read, which the caller treats as
        "assume it changed" rather than as "unchanged".

        Sampling instead of hashing the whole span is a conscious trade, and the
        residue is this: a single flipped bit landing outside the three windows
        of a log bigger than 3 * _FINGERPRINT_WINDOW is invisible to THIS check
        for the rest of the session. It is not invisible to the user — the next
        launch reads the whole log through _check_log_health() — so what the
        trade costs is latency in the report, not silence. The alternative,
        hashing the consumed prefix in full, is an O(n) read on every manual
        move; see _FINGERPRINT_WINDOW for the number that rules it out.

        No AES-GCM here on purpose: this runs on the hot path and must not
        decrypt anything. It answers "did these bytes move?", not "are they
        still readable?" — that second question is _check_log_health()'s, and it
        is the expensive one.
        """
        if span <= 0:
            return "empty"
        window = self._FINGERPRINT_WINDOW
        # A set, then sorted: on a log shorter than 3 windows these collapse to
        # one offset and the whole file is hashed, which is the common case for
        # a real user's feedback log and strictly the strongest answer.
        offsets = sorted({0, max(0, (span - window) // 2), max(0, span - window)})
        digest = hashlib.sha256()
        digest.update(str(span).encode("ascii"))
        try:
            with open(self.feedback_logger.log_file, "rb") as handle:
                for off in offsets:
                    handle.seek(off)
                    digest.update(handle.read(min(window, span - off)))
        except OSError:
            return None
        return digest.hexdigest()

    def _anchor_span(self, size):
        """Remember the log's identity at *size* bytes — or forget it entirely.

        A fingerprint that could not be computed is stored as None rather than
        as a value, so the next call re-reads instead of trusting a comparison
        between two failures.
        """
        digest = self._span_fingerprint(size)
        self._checked_span = None if digest is None else (size, digest)

    def _check_log_health(self, size):
        """Set ``last_read_lost`` from the LOG, not from the learning gates.

        ST-DATA-002 / B7-B9. ``last_read_lost`` used to be written in exactly
        one place — *after* the three gates in learn() — so whether a person was
        told their feedback history had stopped being readable depended on
        whether a learning pass happened to be worth running. Measured
        2026-08-29 over the six log shapes storage reports damage for, exactly
        one reached the user:

          * fewer than MIN_ENTRIES_TO_LEARN records: died on the count gate,
            which returns before any read. The user with the shortest history is
            the one told nothing about losing it (4 records, lost=4, reported 0);
          * damaged in place after a full pass: died on the size fast-path — a
            flipped ciphertext bit changes no length, so os.path.getsize is
            byte-for-byte a fingerprint that cannot see it (8 records, lost=8,
            reported 0), permanently, across every relaunch;
          * the two shapes where LogRead.lost is -1 — the whole file
            unidentifiable as a log, the strongest statement the format can make
            — died on BOTH gates, because the same unreadability that sets -1
            makes log_entry_count() return 0 and 0 < 5.

        This reads the whole log and therefore decrypts it. Payload damage is
        only visible at decrypt time: a framing-only walk over _walk_log_frames
        reports lost == 0 for a flipped ciphertext bit, which is precisely the
        commonest shape. That decrypt is the ST-PERF-005 cost (27.8 ms on a
        2 000-record log), so it is spent only when something has actually
        moved:

          * once per process — the pass whose verdict the UI reads;
          * whenever the bounded fingerprint of the already-checked span
            changes, which is what an in-place bit flip looks like and what no
            length comparison can see;
          * NEVER on a plain append, which leaves that span byte-identical.
            learn() runs after every manual move and every manual move appends,
            so this is the case that decides whether the fix costs anything: it
            costs one 24 KB re-hash. The appended records are covered by the
            incremental read in learn(), which reports its own loss.

        Residue, stated so it is not mistaken for coverage: bytes appended in a
        shape that yields no new countable frame — a half-written record — leave
        log_entry_count() unmoved, so learn() returns at ``_learned_through >=
        total`` and no incremental read looks at them either. That damage waits
        for the next launch's full read. It is bounded silence, not permanent
        silence, which is the whole difference from what this replaced.

        What that comes to, measured 2026-08-29 on .venv-audit, best of 7:

          * manual move (append + learn) on a 2 000-record log: 17.8 ms against
            16.6 ms before — inside the noise of the append's own fsync, which
            dominates both;
          * idle learn(): 0.068 ms against 0.017 ms, and flat in log size;
          * relaunch over a caught-up log — the one case that really costs
            something, because the size fast-path used to answer it for free:
            0.27 ms at 10 records, 1.06 ms at 100, 4.51 ms at 500, 18.3 ms at
            2 000 (1.2 MB), against 0.02-0.03 ms before. ONCE PER LAUNCH. The
            cost ST-PERF-005 removed was 27.8 ms per manual move on the same
            log; this is not that cost back, and it is what buys the report.

        Returns the LogRead when it read, None when it did not.
        """
        checked = self._checked_span
        if checked is not None:
            prev_size, prev_digest = checked
            digest = self._span_fingerprint(prev_size)
            if digest is not None and size >= prev_size and digest == prev_digest:
                if size != prev_size:
                    # Grew, with everything already vouched for untouched:
                    # re-anchor on the longer span so the next call is not left
                    # comparing only the stale prefix.
                    self._anchor_span(size)
                return None
        report = storage.load_encrypted_lines_report(self.feedback_logger.log_file)
        # Assigned on every read, including a clean one, so a REPAIRED log stops
        # reporting. That is why the fingerprint gate above must not be allowed
        # to swallow a shrink: `size >= prev_size` is what sends a truncation
        # here rather than treating it as an append.
        self.last_read_lost = report.lost
        self._anchor_span(size)
        return report

    def learn(self):
        """Learn from feedback not already learned from.

        ST-PERF-005: this used to re-read the whole log and re-apply every entry
        on every call — and it is called after every manual move, after every
        accepted reschedule, and at every launch. Cost grew with the user's
        entire history (16.9 ms at 100 entries, 78 ms at 2000), and repeating
        the same signals kept nudging the weights for feedback the user gave
        once.

        The cursor is a record COUNT, not a byte offset, so the one-time
        conversion of a legacy log does not invalidate it, and it is persisted
        alongside the weights so a restart does not re-learn the history.

        Returns:
            Number of signals processed.
        """
        size = self.feedback_logger.log_size()

        # ST-DATA-002: ABOVE the three gates, and that placement is the whole
        # fix. Whether a person is told their history stopped being readable is
        # a property of the LOG; every gate below is a property of whether a
        # learning pass is worth running, and all three return before anything
        # is read. Relaxing them instead does not work: MIN_ENTRIES_TO_LEARN
        # would still leave the two `lost == -1` shapes silent (they die on
        # log_entry_count() == 0, upstream of every gate), and deleting the size
        # fast-path alone leaves `_learned_through >= total` to return first on
        # a caught-up log — measured, all three B8 tests still red.
        health = self._check_log_health(size)

        # The cheapest possible gate first: if the log has not grown by a
        # single byte since the last pass, there is provably nothing new, and
        # this costs one stat call rather than a walk over the whole history.
        # It is now a gate on LEARNING only — the integrity verdict above has
        # already been taken, so returning here is no longer silence.
        if size and size == self._learned_size:
            return 0

        total = self.feedback_logger.entry_count()
        if total < self.MIN_ENTRIES_TO_LEARN:
            # Guarded on the TOTAL, not on the tail: a long log must keep
            # learning from single new entries.
            return 0
        if self._learned_through >= total:
            return 0  # nothing new; costs one stat call, no decryption

        if health is not None and self._learned_through == 0:
            # The health read above IS this read: load_encrypted_lines_since_report
            # delegates to load_encrypted_lines_report for skip <= 0, on the same
            # file, microseconds earlier. Doing it twice would double the O(n)
            # AES-GCM cost of a first launch over a long history to buy nothing.
            entries, lost = health
        else:
            entries, lost = self.feedback_logger.get_entries_since_report(
                self._learned_through)
        # RAISES the verdict, never lowers it — the assignment here used to be
        # unconditional and that is now wrong, because this read decrypts only
        # the frames at or after the cursor (storage.py:914) and so cannot see
        # damage BEHIND it. One clean append past a damaged prefix would
        # otherwise reset a verdict _check_log_health took from a read of the
        # whole file. Clearing it is that method's job alone: it is the only
        # read that looks at everything, which is what makes it the only one
        # entitled to say a repaired log is healthy again.
        if lost:
            self.last_read_lost = lost
        if not entries:
            if lost:
                # ST-DATA-002: the records are unreadable, not absent. The old
                # code returned here without advancing, so the cursor stayed at
                # 0 forever — measured, 4 consecutive learn() calls on a
                # 12-record log with one flipped bit, cursor 0 every time — and
                # every future pass re-decrypted and re-discarded the same
                # bytes (27.8 ms burned per call on a 2 000-record log, on
                # every manual move).
                #
                # The log is append-only, so advancing costs nothing NEW. It
                # is not free, and the earlier claim here -- "damaged bytes do
                # not heal, so stepping past them loses nothing" -- is false in
                # the one case this app's own message invites. Measured
                # 2026-08-29: write 8 records, keep a copy, flip one bit in
                # each payload (4953 bytes before and after -- the damage does
                # not change the length), learn() -> cursor 8 / lost 8; then
                # restore the user's copy and relaunch -> entry_count 8,
                # 8 readable, persisted cursor 8, learn() returns 0 signals.
                # Eight intact records, learned from never. And
                # errors.feedback_log_damaged ends "The file has NOT been
                # changed", which is precisely an invitation to restore from a
                # backup, OneDrive history, or a keys/ directory that did not
                # travel with a copied Dersis folder.
                #
                # Still open, and deliberately so. The fingerprint this comment
                # asked for now exists -- _span_fingerprint, a hash of the
                # skipped bytes and not of their length -- and a restore does
                # change it, so _check_log_health() above notices and the user
                # is told the log reads clean again (last_read_lost drops back
                # to 0). What it deliberately does NOT do is rewind the cursor
                # to re-learn the restored records.
                #
                # That is a decision, not an omission. Reporting and training
                # are different jobs. Rewinding would re-apply every surviving
                # record's signal a second time and tick train_count for
                # feedback the user gave once, which is exactly the defect
                # tests/test_feedback_log_scaling.py::
                # test_repeat_learn_does_not_move_weights_it_already_applied
                # pins -- a corrupted view of the user's preferences bought in
                # exchange for a warning. Re-learning a restored log needs a
                # cursor that can distinguish "read to report" from "read to
                # learn"; until it has one, the honest behaviour is to tell the
                # user and leave the weights alone.
                self._learned_through = total
                self._learned_size = self.feedback_logger.log_size()
                self._save_weights()
            return 0

        signals_processed = 0

        for entry in entries:
            event = entry.get("event")

            if event == "manual_move":
                signals_processed += self._learn_from_move(entry)
            elif event == "correction":
                signals_processed += self._learn_from_correction(entry)
            elif event == "accepted_placement":
                signals_processed += self._learn_from_acceptance(entry)
            elif event == "rejected_placement":
                signals_processed += self._learn_from_rejection(entry)
            elif event in ("reschedule_accepted", "reschedule_rejected"):
                signals_processed += self._learn_from_reschedule(entry)

        self._learned_through = total
        self._learned_size = self.feedback_logger.log_size()
        if signals_processed > 0:
            self.train_count += 1
        # Saved even when nothing was learned, so an entry carrying no signal is
        # not re-read on every future pass.
        self._save_weights()

        return signals_processed

    def _learn_from_move(self, entry):
        """Learn from a manual move: user preferred new over old position."""
        old = entry.get("old_placement", {})
        new = entry.get("new_placement", {})
        score_old = entry.get("score_old")
        score_new = entry.get("score_new")
        cls_ctx = entry.get("class", {})

        if score_old is None or score_new is None:
            # Without scores, we can still learn directional signals
            return self._learn_directional(cls_ctx, old, new)

        if score_new >= score_old:
            # Scorer thought old was better but user disagreed
            # Increase weight of features that favor the new placement
            return self._adjust_from_preference(
                cls_ctx, old, new, score_old, score_new)
        # Scorer agreed with user — reinforce slightly
        return self._reinforce(cls_ctx, strength=0.3)

    def _learn_from_correction(self, entry):
        """Learn from a correction: user preferred final over auto."""
        auto = entry.get("auto_placement", {})
        final = entry.get("final_placement", {})
        score_auto = entry.get("score_auto")
        score_final = entry.get("score_final")
        cls_ctx = entry.get("class", {})

        if score_auto is not None and score_final is not None:
            if score_final >= score_auto:
                return self._adjust_from_preference(
                    cls_ctx, auto, final, score_auto, score_final)
        return self._learn_directional(cls_ctx, auto, final)

    def _learn_from_acceptance(self, entry):
        """Learn from an accepted placement: mildly reinforce current weights."""
        cls_ctx = entry.get("class", {})
        return self._reinforce(cls_ctx, strength=0.1)

    def _learn_from_rejection(self, entry):
        """Learn from a rejected placement: penalize current scoring."""
        placement = entry.get("placement", {})
        cls_ctx = entry.get("class", {})
        # Mild penalty to weights that led to this placement
        return self._penalize_placement(cls_ctx, placement, strength=0.2)

    def _learn_from_reschedule(self, entry):
        """Learn from reschedule accept/reject."""
        if entry.get("event") == "reschedule_accepted":
            # Good — reinforce overall approach
            q_before = entry.get("quality_before")
            q_after = entry.get("quality_after")
            if q_before is not None and q_after is not None:
                if q_after < q_before:
                    return self._reinforce_all(strength=0.1)
            return 0
        else:
            # Rejected — the user didn't like the proposed changes
            return self._penalize_all(strength=0.05)

    def _learn_directional(self, cls_ctx, old_placement, new_placement):
        """Learn from directional preference without scores.

        Analyze the structural differences between placements to
        determine which features to adjust.
        """
        signals = 0
        lr = self.LEARNING_RATE

        old_slot = old_placement.get("slot", "")
        new_slot = new_placement.get("slot", "")
        old_day = old_placement.get("day", "")
        new_day = new_placement.get("day", "")

        # If user moved to a different day, they may prefer compactness
        if old_day != new_day:
            self._update_delta("lecturer_gap", -lr * 0.5)
            self._update_delta("lecturer_cluster", lr * 0.3)
            self._update_delta("fragmentation", lr * 0.3)
            signals += 1

        # If user moved to earlier/later slot, adjust time preferences
        if old_slot != new_slot:
            self._update_delta("student_gap", -lr * 0.3)
            self._update_delta("student_cluster", lr * 0.2)
            signals += 1

        # If user moved to a different room
        old_room = old_placement.get("room", "")
        new_room = new_placement.get("room", "")
        if old_room != new_room:
            self._update_delta("room_switch_penalty", lr * 0.3)
            signals += 1

        return signals

    def _adjust_from_preference(self, cls_ctx, worse, better,
                                score_worse, score_better):
        """Adjust weights when scorer disagreed with user preference.

        The scorer rated 'worse' lower (better) than 'better', but
        the user preferred 'better'. Nudge weights to fix this.
        """
        lr = self.LEARNING_RATE
        # Increase all compactness-related weights (most common reason
        # users move things)
        self._update_delta("lecturer_gap", lr)
        self._update_delta("lecturer_cluster", lr * 0.5)
        self._update_delta("student_gap", lr * 0.5)
        self._update_delta("student_cluster", lr * 0.3)
        self._update_delta("fragmentation", lr * 0.3)

        # Decrease structural/tidiness weights (they may be overriding
        # quality concerns)
        self._update_delta("day_spread", -lr * 0.2)
        self._update_delta("slot_position", -lr * 0.1)

        return 1

    def _reinforce(self, cls_ctx, strength=0.1):
        """Mildly reinforce current weights (user agreed with placement)."""
        # Very small reinforcement — just prevents drift
        lr = self.LEARNING_RATE * strength
        for key in DEFAULT_WEIGHTS:
            self._update_delta(key, lr * 0.01)
        return 1

    def _reinforce_all(self, strength=0.1):
        """Reinforce all weights mildly."""
        lr = self.LEARNING_RATE * strength
        for key in DEFAULT_WEIGHTS:
            self._update_delta(key, lr * 0.02)
        return 1

    def _penalize_placement(self, cls_ctx, placement, strength=0.2):
        """Penalize weights that led to a rejected placement."""
        lr = self.LEARNING_RATE * strength
        # Reduce structural bias
        self._update_delta("day_spread", -lr)
        self._update_delta("slot_position", -lr)
        # Increase quality weights
        self._update_delta("lecturer_gap", lr)
        self._update_delta("student_gap", lr * 0.5)
        return 1

    def _penalize_all(self, strength=0.05):
        """Mild penalty to all weights (reschedule rejected)."""
        lr = self.LEARNING_RATE * strength
        # Reduce aggressiveness of optimization
        for key in ["fragmentation", "day_overload", "end_of_day_penalty"]:
            self._update_delta(key, -lr)
        return 1

    def _update_delta(self, key, gradient):
        """Update a weight delta with momentum."""
        if key not in DEFAULT_WEIGHTS:
            return
        old_vel = self._velocity.get(key, 0.0)
        new_vel = self.MOMENTUM * old_vel + (1 - self.MOMENTUM) * gradient
        self._velocity[key] = new_vel
        old_delta = self.weight_deltas.get(key, 0.0)
        new_delta = old_delta + new_vel
        # Clamp deltas to prevent extreme drift
        max_delta = DEFAULT_WEIGHTS[key] * 2.0
        new_delta = max(-max_delta, min(max_delta, new_delta))
        self.weight_deltas[key] = new_delta

    def _save_weights(self):
        """Persist learned weights to disk (encrypted)."""
        data = {
            "weight_deltas": self.weight_deltas,
            "velocity": self._velocity,
            "train_count": self.train_count,
            "learned_through": self._learned_through,
            "learned_size": self._learned_size,
        }
        try:
            storage.save_encrypted(data, self.weights_path)
        except Exception:
            pass

    def _load_weights(self):
        """Load previously learned weights from disk (encrypted)."""
        if not os.path.exists(self.weights_path):
            return
        try:
            data = storage.load_encrypted(self.weights_path)
            self.weight_deltas = data.get("weight_deltas", {})
            self._velocity = data.get("velocity", {})
            self.train_count = data.get("train_count", 0)
            # Absent in files written before this change: reads back as 0, so
            # the first pass after upgrading re-learns the log once and then
            # settles. That is the right back-compat behaviour, and needs no
            # migration.
            self._learned_through = data.get("learned_through", 0)
            self._learned_size = data.get("learned_size", 0)
        except Exception:
            pass

    def reset(self):
        """Reset all learned weights to defaults."""
        self.weight_deltas = {}
        self._velocity = {}
        self.train_count = 0
        self._save_weights()

    def summary(self):
        """Return a human-readable summary of learned adjustments."""
        if not self.weight_deltas:
            return tr("status.no_learned_adjustments")
        lines = [tr("status.training_iterations").format(n=self.train_count)]
        weights = self.get_weights()
        for key in sorted(DEFAULT_WEIGHTS.keys()):
            default = DEFAULT_WEIGHTS[key]
            current = weights[key]
            delta = self.weight_deltas.get(key, 0.0)
            if abs(delta) > 0.001:
                direction = "+" if delta > 0 else ""
                lines.append(
                    f"  {key}: {default:.2f} -> {current:.2f} "
                    f"({direction}{delta:.3f})")
        return "\n".join(lines)
