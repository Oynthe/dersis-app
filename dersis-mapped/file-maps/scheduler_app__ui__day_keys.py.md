# File: `scheduler_app/ui/day_keys.py`

## 1. File Role
Weekday key helpers + normalisation. Day keys are lowercase English strings; display labels come from translation.

## 2. Why this file matters
Supporting. Without normalisation, day strings from imports / old saves would drift.

## 3. Imports and Dependencies
- Internal: `translations.{TRANSLATIONS, tr}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `DAY_KEYS` | `["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]`. |
| `day_label(key)` | Returns `tr(f"weekdays.{key}")`. |
| `display_day(value)` | Coerce key/translated/raw value → translated display. |
| `format_day_time(day, slot=None)` | "Monday 09:00". |
| `normalize_day_value(value)` | Translated label or canonical key → key. Walks all language `weekdays.*` for matching. |
| `normalize_day_list(values)` | Apply to a list. |
| `normalize_state_day_keys(state)` | Recursively normalise `state["days"]` + every class's `allowed/excluded_days` + lecturer availability + classes' `pinned_day`/`placed_day`. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1 | docstring | |
| 3 | imports | |
| 5–14 | `DAY_KEYS` | |
| 17–19 | `day_label` | |
| 22–27 | `display_day` | |
| 30–37 | `format_day_time` | |
| 40–60 | `normalize_day_value` | multilingual lookup. |
| 60–~95 | normalisation helpers | list + state walkers. |

## 6. Runtime Behavior
Stateless. Hot on import (single class load may normalise dozens of day strings).

## 7. Data Flow
- In: arbitrary day strings.
- Out: stable key strings or display labels.

## 8. UI Flow
Used by every dialog showing a day, by the renderer, by the importer.

## 9. Error Handling and Edge Cases
- Empty / unknown values → None from `normalize_day_value`.
- `display_day` falls back to `str(value)` if normalisation fails.

## 10. Integration Points
Universal — `ui/app.py`, `ui/dialogs.py`, `ui/renderer.py`, `data_io/importer.py`, etc.

## 11. Risks and Maintenance Notes
- Adding a new translated weekday → automatic (any `weekdays.X` key in any language is matched).
- The eight-key list assumes a Western Monday-start week; the locale UI doesn't change this.

## 12. Mini Summary
Stable day keys + multilingual reverse-lookup. Tolerant to translated/legacy day strings.
