# File: `scheduler_app/core/workflow.py`

## 1. File Role
The UI-free orchestration layer. Provides `SchedulingWorkflow` plus result dataclasses (`AutoPlaceResult`, `ScheduleNewResult`, `PlaceBatchResult`, `DropValidation`, `EditClassResult`) and pure-Python placement snapshot helpers. UI calls into this layer instead of touching `core/logic.py` and the optimizer directly.

## 2. Why this file matters
**Critical.** It is the contract between UI and engine. Adding a new menu action almost always involves adding a method here.

## 3. Imports and Dependencies
- stdlib: `dataclasses`, `typing`.
- Internal: `scheduler_app.models` (state/cls helpers, normalisation), `scheduler_app.logic` (every solver/analytics function used).
- **No Qt imports.** This invariant is critical for testability.

## 4. Main Symbols
| Symbol | Lines (approx.) | Purpose |
|--------|-----------------|---------|
| `AutoPlaceResult` | 994–1002 | dataclass returned by `auto_place_class`. |
| `ScheduleNewResult` | 1004–1013 | dataclass returned by `schedule_new_classes`. |
| `PlaceBatchResult` | 1015–1024 | dataclass returned by `place_batch`. |
| `DropValidation` | (similar block) | dataclass returned by `validate_drop`. |
| `EditClassResult` | (similar block) | dataclass returned by `edit_class`. |
| `snapshot_placements(state)`, `restore_placements(state, snap)` | (helpers) | Used by undo/redo and rollback. |
| `SchedulingWorkflow(state, weights=None, learner=None, feedback_logger=None, …)` | (rest of file) | Main class. |
| `.auto_place_class(cls)` → `AutoPlaceResult` | | Delegates to `optimized_auto_place`. |
| `.schedule_new_classes(new_classes)` → `ScheduleNewResult` | | Delegates to `optimized_batch_schedule`. |
| `.place_batch(classes)` → `PlaceBatchResult` | | Batched manual placement. |
| `.reschedule_all(progress_callback=…)` → tuple | | Delegates to `optimized_reschedule_all`. |
| `.validate_drop(cls, day, slot, room)` → `DropValidation` | | Used by renderer drag-drop. |
| `.edit_class(cls, updated)` → `EditClassResult` | | Mutates the class via `copy_editable_class_fields`, then revalidates. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–10 | docstring | Explains the UI-free contract. |
| 11–12 | future import + dataclasses | `__future__`, `typing`. |
| 14–22 | imports from models | structural helpers. |
| 23–32 | imports from logic | optimizer bridge, scoring, analyse, negotiation. |
| 35–~150 | result dataclasses | each with `field(default_factory=…)` for safe defaults. |
| ~150–~250 | snapshot helpers | `snapshot_placements`, `restore_placements`. |
| ~250–end | `SchedulingWorkflow` class | constructor stores state + injected dependencies; each public method delegates to logic or implements the orchestration. |

## 6. Runtime Behavior
Instantiated once per main window. Each call is synchronous (heavy ones may be run inside a QThread driven by the UI). No event loop, no Qt.

## 7. Data Flow
- Inputs: state dict + the user's chosen class/placement.
- Outputs: result dataclasses; state dict is mutated in place via `mark_placed` etc.
- Side effects: optional `feedback_logger.log_*` calls so the preference learner can train.

## 8. UI Flow
Not applicable directly; consumers convert result dataclasses into widget updates.

## 9. Error Handling and Edge Cases
- `validate_drop` returns `DropValidation(valid=False, reasons=[...])` instead of raising.
- `auto_place_class` returns `AutoPlaceResult(success=False)` on failure rather than raising.
- `edit_class` validates after applying changes; if invalid, the class is rolled back and `EditClassResult.success = False`.
- Locked / pinned / protected classes are filtered by the `optimized_*` functions; the workflow does not re-check.

## 10. Integration Points
- Consumed by `ui/app.py` (every interactive action).
- Calls `core/logic.optimized_*`, `score_placement_explained`, `analyze_schedule`, `negotiate_after_optimization`.

## 11. Risks and Maintenance Notes
- Keep this file UI-free. Even a single `from PyQt6 …` import would defeat the testability win.
- New methods should always return a dataclass (not raw tuples) so UI consumption is type-safe.
- The `state` dict is mutated in place — make sure callers don't share state with another thread.

## 12. Mini Summary
The single bridge between UI and engine. UI calls a method, gets a dataclass back, updates widgets. No Qt imports here.
