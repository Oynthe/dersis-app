# File: `scheduler_app/learning/feedback_logger.py`

## 1. File Role
Persistent logging of user interactions for the preference learner: manual moves, accepted/rejected auto-placements, corrections, reschedule accept/reject, batch results.

## 2. Why this file matters
Supporting. Without the log there's nothing for the learner to consume.

## 3. Imports and Dependencies
- stdlib: `json`, `os`, `time`, `datetime`.
- Internal: `scheduler_app.storage`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `FeedbackLogger(log_dir=None)` | Default dir = `logs/`. Appends entries to `logs/feedback_log.egu`. |
| `_class_context(cls)` | Extracts loggable context (name, lecturer, targets, duration, joint, pinned, constraint presence). |
| `_placement_info(day, slot, room)` | `{"day", "slot", "room"}` dict. |
| `_write_entry(entry)` | Adds timestamp + epoch; appends to encrypted log; failures swallowed silently. |
| `log_manual_move(cls, old_..., new_..., score_old=None, score_new=None)` | "Prefer new" signal. |
| `log_accepted_placement(cls, day, slot, room, score=None, was_best=True, candidate_count=0)` | Positive signal. |
| `log_rejected_placement(cls, day, slot, room, score=None, reason="")` | Negative signal. |
| `log_correction(cls, auto_..., final_..., score_auto=None, score_final=None)` | "Prefer final" signal. |
| `log_reschedule_accepted(changes, quality_before=None, quality_after=None)` | |
| `log_reschedule_rejected(...)` | |
| `log_batch_result(placed_count, unplaced_count, rescheduled, accepted)` | |
| `get_entries(event_type=None, limit=None)` | Read entries (optionally filtered). |
| `entry_count()`, `clear()` | Diagnostics + reset. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–22 | docstring | |
| 25–37 | `__init__` | Path setup. |
| 40–69 | private helpers | Extract context + write. |
| 70–166 | event-specific `log_*` methods | Each calls `_write_entry`. |
| 167–186 | `get_entries`, `entry_count`, `clear` | |

## 6. Runtime Behavior
Called by `core/workflow.SchedulingWorkflow` on each event. Synchronous file I/O — but errors are swallowed so it cannot crash the app.

## 7. Data Flow
- In: classes + placements + scores.
- Out: encrypted array of dicts on disk.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- All writes wrapped in `try/except Exception: pass` — silent on failure.
- Clearing tries to delete the file; ignores OSError/IOError.

## 10. Integration Points
Consumed by `learning/preference_learner.PreferenceLearner`.

## 11. Risks and Maintenance Notes
- Silent failures could hide a corrupt log; consider surfacing a status-bar warning periodically.
- Each entry includes `epoch` for chronological sorting.

## 12. Mini Summary
Append-only encrypted log of user feedback events. Read by the preference learner.
