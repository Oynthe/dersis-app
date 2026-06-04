# File: `scheduler_app/core/explanation_engine.py`

## 1. File Role
Turns structured score breakdowns and rejection reasons into human-readable explanations (localised) for placements, optimization results, and rejected drops.

## 2. Why this file matters
Critical (product differentiator). The "explainable AI" feature relies on this.

## 3. Imports and Dependencies
- stdlib: `re`.
- Internal: `translations.{TRANSLATIONS, tr}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `_COMPONENT_INFO` | Per-weight metadata: label translation key + positive/negative phrasing keys. |
| `_REASON_CATEGORY_KEYS` | Maps validation/conflict translation keys to high-level categories (`room_conflicts`, `lecturer_conflicts`, `group_conflicts`, `capacity_violations`, `constraint_violations`). |
| `ExplanationEngine()` | Stateless. |
| `.explain_placement(cls, day, slot, room, breakdown)` → dict with `pros`, `cons`, `summary`. |
| `.explain_rejection(cls, day, slot, room, reasons)` → dict with categorised reasons. |
| `.explain_optimization(summary)` → dict with quality delta phrasing, top components changed. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–4 | docstring | |
| 6–10 | imports | |
| 12–~80 | `_COMPONENT_INFO` | static metadata. |
| ~80–~150 | `_REASON_CATEGORY_KEYS` | categorisation. |
| ~150–389 | `ExplanationEngine` methods | structured text generation. |

## 6. Runtime Behavior
Stateless; called per placement or per result.

## 7. Data Flow
- In: breakdown dict (from `PlacementScorer.score_explained`).
- Out: dict with `pros: list[str]`, `cons: list[str]`, `summary: str`.

## 8. UI Flow
Driven by `core/workflow` (via `logic.score_placement_explained`) and surfaced in tooltips and the analytics dashboard.

## 9. Error Handling and Edge Cases
- Unknown component keys fall through silently.
- Each phrase falls back to English if the language doesn't translate the key.

## 10. Integration Points
Called from `logic.score_placement_explained` and the optimization summary path.

## 11. Risks and Maintenance Notes
- Adding a new scoring component requires adding `_COMPONENT_INFO` metadata AND translation keys for the positive/negative phrasings.

## 12. Mini Summary
Generates localised pros/cons explanations from numerical score breakdowns. The "explainable AI" voice.
