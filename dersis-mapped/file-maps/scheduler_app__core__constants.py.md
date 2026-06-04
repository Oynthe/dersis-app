# File: `scheduler_app/core/constants.py`

## 1. File Role
Visual constants — colours and cell dimensions — used by the renderer and the exporter so both produce visually consistent timetables.

## 2. Why this file matters
Supporting. Touching values here changes the look of every view and every Excel export.

## 3. Imports and Dependencies
None.

## 4. Main Symbols
| Symbol | Lines | Value | Used by |
|--------|-------|-------|---------|
| `YEAR_COLORS` | 3–12 | 8-colour palette (blue / green / amber / red / purple / pink / cyan / lime). | `logic.get_year_color`, renderer, exporter. |
| `MIN_CELL_W` | 14 | 150 | Renderer/exporter. |
| `MIN_CELL_H` | 15 | 70 | Renderer/exporter. |
| `EMPTY_BG` | 16 | `#F8FAFC` | Renderer. |
| `HEADER_BG_DARK` | 17 | `#334155` | Renderer/exporter. |
| `TIME_BG` | 18 | `#475569` | Renderer/exporter. |
| `CORNER_BG` | 19 | `#94A3B8` | Renderer/exporter. |
| `MATRIX_*` (BORDER / DAY_BG / DAY_FG / BRANCH_BG / BRANCH_FG / SESSION_BG / TIME_BG / CELL_FG / CORNER_BG) | 22–30 | Matrix-view palette. | Renderer (Show Everything), exporter. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1 | docstring | Banner. |
| 3–12 | year palette | 8 colours cycled by `get_year_color`. |
| 14–19 | grid colours/sizes | Default cell dimensions and timetable colours. |
| 22–30 | matrix palette | Distinct palette for the Show Everything view. |

## 6. Runtime Behavior
Loaded once on import.

## 7. Data Flow
Read-only constants consumed elsewhere.

## 8. UI Flow
Not applicable; consumed by UI/exporter modules.

## 9. Error Handling and Edge Cases
None.

## 10. Integration Points
Renderer, exporter, dashboard.

## 11. Risks and Maintenance Notes
Changing `MIN_CELL_W/H` ripples into export column widths.

## 12. Mini Summary
Theme constants for the timetable grid. One central palette.
