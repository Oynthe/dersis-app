# File: `scheduler_app/data_io/__init__.py`

## 1. File Role
Public API re-exports for the data_io package.

## 2. Why this file matters
Supporting. Makes imports stable.

## 3. Imports and Dependencies
- Internal: `data_io.importer`, `data_io.exporter`, `data_io.template`.

## 4. Main Symbols
Re-exports `load_scheduler_data_from_excel`, `DataValidationReport`, `SchedulerDataset`, `export_schedule`, `generate_excel_template`. `__all__` declared.

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1 | docstring | |
| 3–7 | imports | |
| 9 | `__all__` | List of public names. |

## 6. Runtime Behavior
Pure re-export.

## 7. Data Flow
None.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
None.

## 10. Integration Points
Consumers: `ui/app.py` import/export menu actions.

## 11. Risks and Maintenance Notes
Keep `__all__` in sync when adding new public symbols.

## 12. Mini Summary
Public re-export module for import/export/template helpers.
