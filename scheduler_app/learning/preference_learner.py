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

        self._load_weights()

    def get_weights(self):
        """Return the current effective weights (defaults + learned deltas)."""
        weights = dict(DEFAULT_WEIGHTS)
        for key, delta in self.weight_deltas.items():
            if key in weights:
                # Ensure weights don't go negative
                weights[key] = max(0.01, weights[key] + delta)
        return weights

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
        # The cheapest possible gate first: if the log has not grown by a
        # single byte since the last pass, there is provably nothing new, and
        # this costs one stat call rather than a walk over the whole history.
        size = self.feedback_logger.log_size()
        if size and size == self._learned_size:
            return 0

        total = self.feedback_logger.entry_count()
        if total < self.MIN_ENTRIES_TO_LEARN:
            # Guarded on the TOTAL, not on the tail: a long log must keep
            # learning from single new entries.
            return 0
        if self._learned_through >= total:
            return 0  # nothing new; costs one stat call, no decryption

        entries, lost = self.feedback_logger.get_entries_since_report(
            self._learned_through)
        # Reset on every pass that actually reads, so a repaired log stops
        # reporting. The size fast-path above returns before this and leaves
        # the previous value standing, which is what the UI wants: it reads
        # this once at startup, after the first real read.
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
                # Open, and deliberately not closed by re-reading from 0, which
                # is the O(n) cost ST-PERF-005 removed. Detecting a restore
                # needs a fingerprint of the skipped BYTES (a hash of the span
                # -- cheap, since it needs no AES-GCM decrypt). A fingerprint
                # over their LENGTH does not work: the measurement above is the
                # counterexample.
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
