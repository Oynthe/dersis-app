# File: `scheduler_app/ui/cell_formatter.py`

## 1. File Role
Unified cell content assembly for timetable display (renderer) and export (CSV/Excel plain text). Provides `tooltip_text` and `plain_cell_text`.

## 2. Why this file matters
Supporting. Single source of truth for "how a class appears in a cell".

## 3. Imports and Dependencies
- Internal: `logic.classroom_of`, `translations.tr`, `ui.badge_formatter.badge_text`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `tooltip_text(cls, include_groups=True, include_duration=True)` → str | Multi-line tooltip including class code, name, lecturer, target groups, duration, room, badge. |
| `plain_cell_text(entry)` → str | Plain single-line text for CSV/clipboard: code / name / lecturer / [room]. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1 | docstring | |
| 3–5 | imports | |
| 8–30 | `tooltip_text` | Concatenates parts joined by `\n`. Empty fields skipped. |
| 33–46 | `plain_cell_text` | Same but for an export `entry` dict. |

## 6. Runtime Behavior
Hot during cell painting and tooltips.

## 7. Data Flow
- In: class dict OR export entry dict.
- Out: string.

## 8. UI Flow
- Renderer calls `tooltip_text` for hover tooltips.
- Exporter calls `plain_cell_text` for CSV cells.

## 9. Error Handling and Edge Cases
- Missing optional fields → silently skipped.
- Multi-target classes show `"year/branch, year2/branch2"`.

## 10. Integration Points
`ui/renderer.py`, `data_io/exporter.py`.

## 11. Risks and Maintenance Notes
- Changes here affect both UI tooltips and exports — visual diff against both before shipping.

## 12. Mini Summary
The single formatting helper for class cell content. Tooltip + plain-text variants.
