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

import json
import os
import math

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
        """Run a learning pass over all available feedback.

        Reads the feedback log, extracts preference signals, and
        updates weight deltas accordingly.

        Returns:
            Number of signals processed.
        """
        entries = self.feedback_logger.get_entries()
        if len(entries) < self.MIN_ENTRIES_TO_LEARN:
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

        if signals_processed > 0:
            self.train_count += 1
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
