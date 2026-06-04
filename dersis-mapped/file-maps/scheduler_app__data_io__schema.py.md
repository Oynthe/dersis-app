# File: `scheduler_app/data_io/schema.py`

## 1. File Role
Localised workbook schema helpers. Defines the structure (sheets + columns) for Excel import/export, and provides reverse-aliasing so a workbook in any language can be parsed.

## 2. Why this file matters
Supporting. Editing here changes the Excel template across all languages.

## 3. Imports and Dependencies
- Internal: `translations.{TRANSLATIONS, tr}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `WORKBOOK_SHEETS` | dict: sheet_id → `{title_key, legacy_title, columns: [(field, label_key, desc_key)]}`. Sheets: `teachers`, `rooms`, `branches`, `classes`. |
| `_normalize_label(value)` | Strips trailing colon/whitespace, returns plain string. |
| `get_workbook_sheet_title(sheet_id)`, `get_workbook_sheet_headers(sheet_id)`, `get_workbook_sheet_header_map(sheet_id)`, `get_workbook_sheet_description_map(sheet_id)` | Localised header generation. |
| `get_workbook_sheet_reverse_header_map(sheet_id)` | English + localised label → field key. |
| `get_workbook_sheet_alias_map()` | Sheet name in any language → sheet_id. |
| `lookup_workbook_sheet_id(sheet_name)` | Wrapper. |
| `canonicalize_workbook_columns(sheet_id, columns)` | Renames a DataFrame's columns to canonical field keys. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1 | docstring | |
| 3 | imports | |
| 6–55 | `WORKBOOK_SHEETS` | The schema source of truth. |
| 58–59 | `_normalize_label` | |
| 61–80 | header helpers | localised. |
| 83–99 | reverse maps | every language's label → field key. |
| 101–115 | sheet alias map | |
| 117–119 | `lookup_workbook_sheet_id` | |
| 121–128 | `canonicalize_workbook_columns` | for the importer. |

## 6. Runtime Behavior
Stateless; called on demand by the importer and template generator.

## 7. Data Flow
- In: sheet_id, column lists.
- Out: header/description dicts, reverse alias maps.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- Unknown sheet_id raises KeyError — caller is expected to validate via `lookup_workbook_sheet_id` first.
- Multilingual aliases include English labels + every translation's variant.

## 10. Integration Points
Imported by `data_io/template.py` and `data_io/importer.py`.

## 11. Risks and Maintenance Notes
- Adding a new sheet or column → add to `WORKBOOK_SHEETS` + supply translation keys.
- The reverse map is rebuilt on each call (no caching). Hot path for large imports — consider caching if perf matters.

## 12. Mini Summary
Schema for Excel template + the alias maps that let the importer accept any-language workbooks.
