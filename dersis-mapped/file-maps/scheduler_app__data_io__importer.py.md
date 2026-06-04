# File: `scheduler_app/data_io/importer.py`

## 1. File Role
Excel import pipeline: read .xlsx → validate schema → resolve FK references → produce a `SchedulerDataset` containing a populated state dict + a `DataValidationReport`.

## 2. Why this file matters
Critical. The primary onboarding path for non-trivial data.

## 3. Imports and Dependencies
- stdlib: `dataclasses`, `typing.Any`.
- Third-party (lazy, guarded): `pandas`.
- Internal: `models.{new_class, new_state, new_lecturer_availability, normalize_class_data, normalize_state_classes, parse_location_type_label}`, `translations.tr`, `data_io.schema.{canonicalize_workbook_columns, lookup_workbook_sheet_id}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| Schema sets (`TEACHER_REQUIRED/OPTIONAL/ALL`, etc.) | Per-sheet required and optional column names. |
| `DataValidationReport` (dataclass) | `errors`, `warnings`, `is_valid`, `add_error`, `add_warning`, `summary`. |
| `SchedulerDataset` (dataclass) | `state`, `report`, `raw_teachers`, `raw_rooms`, `raw_branches`, `raw_classes`. |
| `_parse_comma_list(value)` | Splits "Mon, Tue, Wed" → ["Mon","Tue","Wed"]. |
| `_validate_schema(df, sheet_name, required, all_cols, report)` | True/False. Adds errors/warnings. |
| `_check_duplicates(df, id_col, sheet_name, report)` | Adds errors. |
| `_process_teachers`, `_process_rooms`, `_process_branches`, `_process_classes` | Per-sheet pipelines. |
| `load_scheduler_data_from_excel(filepath)` → `SchedulerDataset` | Top entry point. |
| `_resolve_joint_groups(dataset)` | Post-processing to merge `joint_class_group` clusters. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–8 | docstring | |
| 10–24 | imports + flag | |
| 27–45 | schema sets | |
| 50–98 | dataclasses | |
| 102–137 | helpers | parse + validate + duplicate check. |
| 141–225 | per-sheet processors | populate state lists. |
| 227–305 | `_process_classes` | most complex; validates FK, builds class dicts. |
| 308–409 | `load_scheduler_data_from_excel` | main loop. Reads sheets, calls processors in dependency order, normalises. |
| 412–437 | `_resolve_joint_groups` | merges classes sharing `joint_class_group`. |

## 6. Runtime Behavior
Synchronous file I/O. Called from the import menu.

## 7. Data Flow
- In: .xlsx file.
- Out: `SchedulerDataset(state, report, raw_…)`.

## 8. UI Flow
Triggered by `ui/app.py::on_import`; preview surfaced via `ImportPreviewDialog` (in `dialogs.py`).

## 9. Error Handling and Edge Cases
- Pandas missing → error in report.
- Excel open failure → error in report; empty dataset.
- Missing optional sheets → warning per sheet.
- Description row heuristic: long text or whitespace in the ID column → skip the row.
- Unknown rooms in `allowed_rooms` → warning, dropped.
- FK resolution failures → row-level error, class skipped.
- Joint groups of < 2 → no merge.

## 10. Integration Points
Used by `ui/app.py`. Output state dict is then merged into the live state.

## 11. Risks and Maintenance Notes
- Schema validation is done before processing; expanding `_OPTIONAL` keys requires updating both schema.py and the processor.
- The default-year naming uses `tr("status.default_year_name").format(n=1)` — translations must support `{n}`.
- The description-row heuristic could false-positive on long real names (rare).

## 12. Mini Summary
Excel → state dict pipeline with strong validation. Returns a `SchedulerDataset`; UI handles user-side errors.
