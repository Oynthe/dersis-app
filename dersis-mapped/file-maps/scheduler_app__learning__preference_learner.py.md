# File: `scheduler_app/learning/preference_learner.py`

## 1. File Role
Online gradient learning over PlacementScorer weights, with momentum. Reads the feedback log, extracts preference signals, updates per-key deltas, persists to `learning/learned_weights.egu`.

## 2. Why this file matters
Supporting (UX). Without it, the scoring is static.

## 3. Imports and Dependencies
- stdlib: `json`, `os`, `math`.
- Internal: `placement_scorer.DEFAULT_WEIGHTS`, `translations.tr`, `feedback_logger.FeedbackLogger`, `storage`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `PreferenceLearner(data_dir=None)` | Loads existing deltas; reads `MIN_ENTRIES_TO_LEARN`. |
| Class constants: `LEARNING_RATE=0.05`, `MOMENTUM=0.9`, `MIN_ENTRIES_TO_LEARN=5`. |
| `.get_weights()` → dict | Returns `DEFAULT_WEIGHTS + deltas`, clamped to ≥ 0.01. |
| `.learn()` → int | Process the feedback log, returns signals processed. |
| Internal: `_learn_from_move`, `_learn_from_correction`, `_learn_from_acceptance`, `_learn_from_rejection`, `_learn_from_reschedule`, `_learn_directional`, `_adjust_from_preference`, `_reinforce`, `_reinforce_all`, `_penalize_placement`, `_penalize_all`, `_update_delta(key, gradient)`, `_save_weights`, `_load_weights`. |
| `.reset()` | Clears deltas. |
| `.summary()` → str | Human-readable summary of learned adjustments. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–14 | docstring | |
| 16–25 | imports | |
| 27–53 | `__init__` | Loads weights, prepares state. |
| 54–61 | `get_weights` | Applies deltas with min-floor. |
| 63–96 | `learn` | Dispatch per event type; persist on change. |
| 98–156 | per-event helpers | Each returns 0+ "signals processed". |
| 158–192 | `_learn_directional` | When scores absent, infer from placement structure (room change, day change, slot change). |
| 194–249 | `_adjust_from_preference`, `_reinforce*`, `_penalize*` | Symmetric updates. |
| 251–263 | `_update_delta` | Momentum + clamp at ±2× default. |
| 265–287 | `_save_weights` / `_load_weights` | Encrypted persistence. |
| 289–311 | `reset`, `summary` | Diagnostics. |

## 6. Runtime Behavior
`PreferenceLearner.learn()` is typically called once per app launch (or periodically). Each pass walks the entire feedback log. The learned deltas feed the optimizer's scorer via `SchedulingWorkflow(weights=learner.get_weights())`.

## 7. Data Flow
- In: feedback log entries.
- Out: weight delta dict, persisted to disk.

## 8. UI Flow
Not applicable directly.

## 9. Error Handling and Edge Cases
- Below `MIN_ENTRIES_TO_LEARN=5` → returns 0 (no updates).
- Missing keys silently skipped in `_update_delta`.
- Persistence failures swallowed.

## 10. Integration Points
Constructed by `SchedulerApp`. Output consumed by `SchedulingWorkflow`.

## 11. Risks and Maintenance Notes
- Gradient sign and magnitude are hand-tuned heuristics, not derived from a formal loss function — improvements may require careful experimentation.
- `_update_delta`'s clamp at ±2× the default prevents catastrophic drift but is itself a magic number.

## 12. Mini Summary
Online gradient + momentum updates to per-weight deltas. Persisted encrypted. Feeds the optimizer via `get_weights()`.
