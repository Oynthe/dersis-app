# File: `scheduler_app/data_io/template.py`

## 1. File Role
Generates a localised Excel template workbook with one sheet per `WORKBOOK_SHEETS` entry, header row, description row, example rows, and frozen panes.

## 2. Why this file matters
Supporting. Quality of the template directly affects user import success rates.

## 3. Imports and Dependencies
- Third-party (lazy, guarded): `openpyxl`.
- Internal: `models.{LOCATION_FACE_TO_FACE, LOCATION_ONLINE, LOCATION_LECTURER_OFFICE, get_location_label}`, `translations.tr`, `data_io.schema.*`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `HAS_OPENPYXL` | Flag. |
| `_sheet_examples()` | Localised example data per sheet (3 teachers, 3 rooms, 3 branches, 5 classes including a joint group). |
| `generate_excel_template(filepath)` | Writes the workbook. Raises translated error if openpyxl missing. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–8 | docstring + import guard | |
| 10–22 | imports + flag | |
| 25–162 | `_sheet_examples` | Localised sample data; deliberately includes a joint-class group. |
| 165–229 | `generate_excel_template` | Build workbook: headers (bold blue fill), descriptions (italic grey), example rows, column auto-size, freeze panes at row 3. |

## 6. Runtime Behavior
Runs once per "Save Template" user action.

## 7. Data Flow
- In: filepath.
- Out: .xlsx file written.

## 8. UI Flow
Triggered from the import menu.

## 9. Error Handling and Edge Cases
- Missing openpyxl → translated RuntimeError.
- Translations for example data fall back to English.

## 10. Integration Points
Imported by `data_io/__init__.py`; called by `ui/app.py`.

## 11. Risks and Maintenance Notes
- The example data must reference real keys consistent with the importer's expectations (joint_class_group, location_type labels).
- Freeze pane at row 3 must match the importer's "skip description row" heuristic.

## 12. Mini Summary
Builds a localised Excel template with header + description + examples. Pure openpyxl, no scheduling logic.
