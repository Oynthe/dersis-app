# File: `scheduler_app/core/schedule_impact_analyzer.py`

## 1. File Role
Non-invasive observer: detects scheduling-relevant changes between old and new states, validates the current timetable read-only, and returns an `ImpactResult` indicating whether a full reschedule is needed.

## 2. Why this file matters
Supporting. Helps the UI surface "you've edited X, this may invalidate Y placements".

## 3. Imports and Dependencies
- stdlib: `copy`, `enum.Enum`, `typing`.
- Third-party (lazy): `deepdiff.DeepDiff` — used when available; falls back gracefully.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `ImpactLevel(Enum)` | `NO_RESCHEDULE_NEEDED`, `RESCHEDULE_RECOMMENDED`, `RESCHEDULE_REQUIRED`. |
| `ImpactResult` | `level`, `changed_fields`, `affected_entities`, `hard_violations`, `soft_impact_reasons`. Uses `__slots__`. |
| `_SCHEDULING_RELEVANT_KEYS` | Top-level state keys that affect scheduling. |
| `_HARD_CONSTRAINT_CLASS_FIELDS` | Class-level fields that, when changed, can invalidate placements. |
| `ScheduleImpactAnalyzer` (or top-level functions) | Compare old vs new, return `ImpactResult`. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–9 | docstring | |
| 11–13 | imports | optional deepdiff. |
| 15–30 | Enum + result | structured output. |
| 30–60 | constants | fields lists. |
| 60–288 | analyser functions | Walks state diff, classifies each change. |

## 6. Runtime Behavior
Called after a settings edit (Setup dialog) or after a bulk import — before redrawing.

## 7. Data Flow
- In: previous state snapshot, current state.
- Out: `ImpactResult`.

## 8. UI Flow
Result text can be surfaced as a banner or toast.

## 9. Error Handling and Edge Cases
- `deepdiff` missing → falls back to manual key comparison.
- Unknown changes → conservative `RESCHEDULE_RECOMMENDED`.

## 10. Integration Points
Used by `ui/app.py` after Setup edits.

## 11. Risks and Maintenance Notes
- Adding a new top-level state key relevant to scheduling → add to `_SCHEDULING_RELEVANT_KEYS`.
- Adding a new class-level constraint field → add to `_HARD_CONSTRAINT_CLASS_FIELDS`.

## 12. Mini Summary
Non-invasive change detector. Tells the UI whether a reschedule is recommended.
