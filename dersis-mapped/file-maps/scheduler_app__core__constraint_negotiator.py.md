# File: `scheduler_app/core/constraint_negotiator.py`

## 1. File Role
Event-driven constraint negotiation: diagnoses infeasibility, ranks the smallest relaxations that would unblock placement, and assembles user-readable reports. Triggered automatically when a class cannot be placed or after a global reschedule leaves unplaced classes.

## 2. Why this file matters
Critical. Differentiates the product (explainable AI). 1342 lines — by far the largest analytics module.

## 3. Imports and Dependencies
- stdlib: `collections.defaultdict`.
- Internal: many from `logic`, `models`, `constraint_validator`, `candidate_generator`, `conflict_graph`, `translations.tr`, `ui.day_keys.day_label`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `InfeasibilityAnalyzer(state)` | Categorises why specific classes cannot be placed (no_days, no_times, no_rooms, lecturer_conflict, room_conflict, group_conflict, capacity, mixed). |
| `RelaxationSuggester(state, validator, generator)` | Generates ranked suggestions: "Allow Tuesday", "Unpin X", "Increase room capacity", "Remove constraint Z from Y". |
| `NegotiationReportBuilder(state)` | Assembles analysis + suggestions into a report dict. |
| `ConstraintNegotiator(state)` | Top-level orchestrator. `.negotiate_after_optimization(placed, unplaced)`, `.diagnose_infeasibility(cls)`, `.apply_suggestion(cls, suggestion)` (static). |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–30 | docstring + imports | |
| 30–~400 | `InfeasibilityAnalyzer` | Per-class diagnosis. Walks days/times/rooms; detects the constraint type. |
| ~400–~900 | `RelaxationSuggester` | Generates suggestion dicts. Each has fields: `class_uid`, `kind` (e.g. `add_allowed_day`, `unpin`, `increase_capacity`), `parameters`, `risk_level`, `expected_unblocks`. Uses conflict graph + propagator. |
| ~900–~1100 | `NegotiationReportBuilder` | Combines analysis + suggestions. |
| ~1100–1342 | `ConstraintNegotiator` | Orchestrator + `apply_suggestion` static. Loads user prefs from `negotiation_settings.egu`. |

## 6. Runtime Behavior
Triggered after a failed placement or a reschedule with leftovers. Synchronous; can be heavy on large schedules.

## 7. Data Flow
- In: state, placed/unplaced lists.
- Out: structured report dict with `analysis[cls_id]`, `suggestions[cls_id]: list`, `summary`.

## 8. UI Flow
Result drives `NegotiationDialog` in `dialogs.py`. User selects suggestions to apply; each is fed to `apply_suggestion(cls, suggestion)`.

## 9. Error Handling and Edge Cases
- If no suggestions would unblock the class (over-constrained), report says so.
- Risk levels guide auto-apply: `low` may be auto-applied if user setting permits; `high` requires explicit confirmation.
- User preferences (auto-apply threshold, severity filter) persist in `settings/negotiation_settings.egu`.

## 10. Integration Points
- Called by `core/workflow.SchedulingWorkflow` (via `logic.negotiate_after_optimization` wrapper).
- UI: `ui/dialogs.NegotiationDialog`.

## 11. Risks and Maintenance Notes
- Suggestion shapes are not formalised in a dataclass — they're plain dicts. New suggestion types must be handled by `apply_suggestion`.
- Large file; future refactor candidate.

## 12. Mini Summary
Diagnoses why placements fail and suggests minimal relaxations. Read alongside `dialogs.NegotiationDialog`.
