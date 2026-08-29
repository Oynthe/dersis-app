"""Feedback logging system for scheduler learning.

Records user interactions with the scheduler to build a training dataset
for the PreferenceLearner:

- manual_move: User drag-dropped a class to a different slot
- accepted_placement: User accepted an auto-placed suggestion
- rejected_placement: User rejected/undid an auto-placed suggestion
- correction: User manually corrected a final schedule position
- reschedule_accepted: User accepted a full reschedule result
- reschedule_rejected: User cancelled a reschedule

Each entry records the class context, placement details, and the
scoring state at the time of the decision.
"""

import os
import time
from datetime import datetime

from scheduler_app import storage


class FeedbackLogger:
    """Persistent feedback logger for scheduler interactions.

    Stores feedback entries as an encrypted JSON array in an .egu file.
    Each entry captures enough context for the PreferenceLearner
    to adjust scoring weights.
    """

    def __init__(self, log_dir=None):
        if log_dir is None:
            log_dir = storage.sub_dir(storage.LOGS_DIR)
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = storage.feedback_log_path()

    def _class_context(self, cls):
        """Extract loggable context from a class dict."""
        return {
            "name": cls.get("name", ""),
            "lecturer": cls.get("lecturer", ""),
            "targets": cls.get("targets", []),
            "duration": cls.get("duration", 1),
            "joint_session": cls.get("joint_session", True),
            "pinned": cls.get("pinned", False),
            "has_day_constraints": bool(cls.get("allowed_days")
                                        or cls.get("excluded_days")),
            "has_time_constraints": bool(cls.get("allowed_times")
                                         or cls.get("excluded_times")),
            "has_room_constraints": bool(cls.get("required_classrooms")
                                         or cls.get("excluded_classrooms")),
        }

    def _placement_info(self, day, slot, room):
        """Format placement as loggable dict."""
        return {"day": day, "slot": slot, "room": room}

    def _write_entry(self, entry):
        """Append a feedback entry to the encrypted log file."""
        entry["timestamp"] = datetime.now().isoformat()
        entry["epoch"] = time.time()
        try:
            storage.append_encrypted_entry(entry, self.log_file)
        except Exception:
            pass  # Logging failure should never crash the app

    def log_manual_move(self, cls, old_day, old_slot, old_room,
                        new_day, new_slot, new_room, score_old=None,
                        score_new=None):
        """Log when a user manually drags a class to a new position.

        This is the strongest learning signal: the user explicitly
        preferred new_placement over old_placement.
        """
        self._write_entry({
            "event": "manual_move",
            "class": self._class_context(cls),
            "old_placement": self._placement_info(old_day, old_slot, old_room),
            "new_placement": self._placement_info(new_day, new_slot, new_room),
            "score_old": score_old,
            "score_new": score_new,
            "signal": "prefer_new",
        })

    def log_accepted_placement(self, cls, day, slot, room,
                               score=None, was_best=True,
                               candidate_count=0):
        """Log when a user accepts an auto-placed suggestion."""
        self._write_entry({
            "event": "accepted_placement",
            "class": self._class_context(cls),
            "placement": self._placement_info(day, slot, room),
            "score": score,
            "was_best_candidate": was_best,
            "total_candidates": candidate_count,
            "signal": "positive",
        })

    def log_rejected_placement(self, cls, day, slot, room,
                               score=None, reason=""):
        """Log when a user rejects/undoes an auto-placed suggestion."""
        self._write_entry({
            "event": "rejected_placement",
            "class": self._class_context(cls),
            "placement": self._placement_info(day, slot, room),
            "score": score,
            "reason": reason,
            "signal": "negative",
        })

    def log_correction(self, cls, auto_day, auto_slot, auto_room,
                       final_day, final_slot, final_room,
                       score_auto=None, score_final=None):
        """Log when a user corrects an auto-placed class to a different slot.

        This provides a direct preference signal: final > auto.
        """
        self._write_entry({
            "event": "correction",
            "class": self._class_context(cls),
            "auto_placement": self._placement_info(
                auto_day, auto_slot, auto_room),
            "final_placement": self._placement_info(
                final_day, final_slot, final_room),
            "score_auto": score_auto,
            "score_final": score_final,
            "signal": "prefer_final",
        })

    def log_reschedule_accepted(self, changes, quality_before=None,
                                quality_after=None):
        """Log when a user accepts a full reschedule result."""
        self._write_entry({
            "event": "reschedule_accepted",
            "num_changes": len(changes),
            "quality_before": quality_before,
            "quality_after": quality_after,
            "signal": "positive",
        })

    def log_reschedule_rejected(self, changes, quality_before=None,
                                quality_after=None):
        """Log when a user cancels a reschedule."""
        self._write_entry({
            "event": "reschedule_rejected",
            "num_changes": len(changes),
            "quality_before": quality_before,
            "quality_after": quality_after,
            "signal": "negative",
        })

    def log_batch_result(self, placed_count, unplaced_count, rescheduled,
                         accepted):
        """Log the outcome of a batch scheduling operation."""
        self._write_entry({
            "event": "batch_schedule",
            "placed_count": placed_count,
            "unplaced_count": unplaced_count,
            "rescheduled": rescheduled,
            "accepted": accepted,
            "signal": "positive" if accepted else "negative",
        })

    def get_entries(self, event_type=None, limit=None):
        """Read feedback entries from the encrypted log file.

        Args:
            event_type: Filter by event type (e.g., 'manual_move').
            limit: Max number of entries to return (most recent first).

        Returns:
            List of entry dicts.
        """
        all_entries = storage.load_encrypted_lines(self.log_file)
        if event_type is not None:
            all_entries = [e for e in all_entries if e.get("event") == event_type]
        if limit:
            all_entries = all_entries[-limit:]
        return all_entries

    def log_size(self):
        """Bytes on disk. Cheap enough to call on every learning pass."""
        return storage.log_size(self.log_file)

    def entry_count(self):
        """Number of logged entries, without decrypting any of them."""
        return storage.log_entry_count(self.log_file)

    def get_entries_since(self, skip):
        """Entries after the first *skip*, decrypting only those (ST-PERF-005)."""
        return storage.load_encrypted_lines_since(self.log_file, skip)

    def clear(self):
        """Clear all feedback entries."""
        try:
            if os.path.exists(self.log_file):
                os.remove(self.log_file)
        except (IOError, OSError):
            pass
