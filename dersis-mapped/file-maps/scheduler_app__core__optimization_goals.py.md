# File: `scheduler_app/core/optimization_goals.py`

## 1. File Role
Translates 6 user-facing optimization-goal sliders into the 14-weight `PlacementScorer` profile. Provides preset profiles ("balanced", "lecturer_priority", "student_priority", etc.).

## 2. Why this file matters
Supporting. Optional layer; if absent, `DEFAULT_WEIGHTS` are used unchanged.

## 3. Imports and Dependencies
- Internal: `placement_scorer.DEFAULT_WEIGHTS`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `GOAL_KEYS` | The 6 keys: `lecturer_compactness`, `student_compactness`, `room_utilization`, `fairness`, `minimal_disruption`, `early_hour_preference`. |
| `DEFAULT_GOALS` | Slider values that reproduce `DEFAULT_WEIGHTS` exactly. |
| `PRESETS` | dict of named profiles (`balanced`, `lecturer_priority`, `student_priority`, etc.). |
| `compute_weights(goals)` | Converts goal dict (0-100 values) into a weight override dict mergeable with `DEFAULT_WEIGHTS`. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–14 | docstring | |
| 17 | import DEFAULT_WEIGHTS | |
| 20–34 | GOAL_KEYS + DEFAULT_GOALS | |
| 37–~120 | `PRESETS` | hand-tuned named slider configurations. |
| ~120–187 | `compute_weights(goals)` | Linear/affine mapping from goal → weight; bounded. |

## 6. Runtime Behavior
Stateless. Called when the user changes a slider or picks a preset.

## 7. Data Flow
- In: dict of goal → int(0..100).
- Out: weight override dict for `PlacementScorer`/`TimetableScorer`.

## 8. UI Flow
Driven by `OptimizationGoalsDialog` in `ui/dialogs.py`. Slider state persisted in app settings.

## 9. Error Handling and Edge Cases
- Unknown goal keys are silently dropped.
- Values outside 0-100 are clamped.

## 10. Integration Points
Called from `ui/dialogs.OptimizationGoalsDialog.apply()`.

## 11. Risks and Maintenance Notes
- The slider-to-weight mapping is hand-tuned; changing it changes the user-facing behaviour of every preset.
- Adding a new weight to `DEFAULT_WEIGHTS` doesn't automatically wire it here.

## 12. Mini Summary
Six sliders → fourteen weights, with named presets. Optional layer over `DEFAULT_WEIGHTS`.
