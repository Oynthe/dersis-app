# File: `scheduler_app/data_io/exporter.py`

## 1. File Role
Final-timetable export pipeline. Supports Excel (.xlsx with rich text, colour-coded), CSV (flat grid), and PDF (reportlab). Multiple views per workbook (per-classroom, per-lecturer, per-branch, Show Everything).

## 2. Why this file matters
Critical. The deliverable end-users hand to colleagues.

## 3. Imports and Dependencies
- stdlib: `csv`, `os`, `typing.Any`.
- Third-party (lazy, guarded): `openpyxl`. PDF uses `reportlab` (imported lazily when format=pdf).
- Internal: `logic.{get_placed_classes, occupied_slots_of, classroom_of, total_duration, get_year_color, lighten_color, build_virtual_classroom_day_layout}`, `models.{get_classroom_export_labels, get_protection_label, effective_day, effective_time, slot_offset_for_target}`, `translations.tr`, `ui.badge_formatter.get_badge`, `ui.cell_formatter.plain_cell_text`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `HAS_OPENPYXL` | Flag. |
| `FinalSchedule(state)` | Wrapper. Properties: `days`, `slots`, `classrooms`, `lecturers`, `years`. `placed_classes()`, `build_grid()` returns `(day, slot) → list[entry]`. |
| `_strip_hash(color)`, `_cell_text(entry)`, `_rich_cell(entry)` | Cell content helpers. |
| `export_schedule(state, path, format=…)` | Public dispatch — Excel/CSV/PDF. |
| Internal: per-view writers (`_write_excel_per_classroom`, `_write_excel_per_lecturer`, `_write_excel_per_branch`, `_write_excel_matrix`, `_write_csv`, `_write_pdf`). |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–10 | docstring + import guard | |
| 11–35 | imports + flag | |
| 36–80 | `FinalSchedule` wrapper | Provides view-friendly accessors. |
| 82–100 | helpers | strip hash, plain text, rich text. |
| (rest of file) | per-view writers + dispatch | Excel writes with CellRichText + PatternFill + Border + Alignment; CSV writes flat grid; PDF uses reportlab Table styles. |

## 6. Runtime Behavior
Synchronous file write. Triggered from the export menu.

## 7. Data Flow
- In: state dict + path + format.
- Out: file on disk.

## 8. UI Flow
Triggered by `ui/app.py::on_export_*`.

## 9. Error Handling and Edge Cases
- Missing openpyxl → translated error.
- Missing reportlab (PDF) → translated error.
- Empty schedule → still produces a file with empty grid; no error.
- Non-physical (virtual) classes export through `build_virtual_classroom_day_layout` for proper sub-column layout.

## 10. Integration Points
- Called by `ui/app.py` and possibly batch scripts.
- Uses cell formatters from `ui/cell_formatter.py` and badges from `ui/badge_formatter.py`.

## 11. Risks and Maintenance Notes
- Tier-gated by `FEATURE_EXPORT_PDF/EXCEL/CSV` in `plans.py`. The exporter itself does not gate; UI does.
- Adding a new view → add a writer + dispatch in `export_schedule`.

## 12. Mini Summary
Multi-format final-schedule export with color-coded rich-text Excel, flat CSV, and styled PDF. Uses the same cell formatters as the UI for visual consistency.
