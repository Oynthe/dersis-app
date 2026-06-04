# 08 — Import, Export, and Reporting Map

All import/export code lives under `scheduler_app/data_io/`. The public API (`scheduler_app/data_io/__init__.py`) re-exports:

- `load_scheduler_data_from_excel(path) → SchedulerDataset`
- `export_schedule(state, path, format=…)` — writes Excel / CSV / PDF
- `generate_excel_template(path)` — builds a localised template workbook
- Helpers: `DataValidationReport`, `SchedulerDataset`

## 1. Supported formats

| Direction | Format | File extension | Module |
|-----------|--------|----------------|--------|
| Import | Excel workbook | `.xlsx` | `data_io/importer.py` |
| Export | Excel workbook | `.xlsx` | `data_io/exporter.py` |
| Export | CSV (flat) | `.csv` | `data_io/exporter.py` |
| Export | PDF (printable timetable) | `.pdf` | `data_io/exporter.py` |
| Template generation | Excel workbook | `.xlsx` | `data_io/template.py` |
| Persistent state | Binary `.egu` | `.egu` | `storage/storage.py` (see map 09) |

## 2. Workbook schema (`data_io/schema.py`)

The Excel template uses four sheets, defined in `WORKBOOK_SHEETS`. Sheet titles and column headers are translated, so each user gets a localised template — but on import, the canonicaliser (`canonicalize_workbook_columns`) maps any localised column name back to the stable internal key. Translation aliases for every language are consulted to do this matching.

### Sheets

| Sheet ID | Default Title (en) | Columns (canonical keys) |
|----------|--------------------|--------------------------|
| `teachers` | Teachers | `teacher_id`, `name`, `allowed_days`, `allowed_hours`, `excluded_days`, `excluded_hours` |
| `rooms` | Rooms | `room_id`, `name`, `capacity`, `room_type` |
| `branches` | Branches | `branch_id`, `name` |
| `classes` | Classes | `class_id`, `class_code`, `course_name`, `teacher_id`, `branch_id`, `duration`, `student_count`, `required_room_type`, `allowed_rooms`, `excluded_rooms`, `joint_class_group`, `location_type` |

Required vs optional columns are defined in `data_io/importer.py`:
- `TEACHER_REQUIRED = {"teacher_id", "name"}`
- `ROOM_REQUIRED = {"room_id", "name"}`
- `BRANCH_REQUIRED = {"branch_id", "name"}`
- `CLASS_REQUIRED = {"class_id", "course_name", "teacher_id", "branch_id", "duration"}`

Missing required columns raise an error; extra unknown columns raise a warning.

## 3. Import pipeline (`data_io/importer.py`)

### 3.1 Entry point
`load_scheduler_data_from_excel(filepath) → SchedulerDataset`.

If `pandas` isn't importable, the dataset is returned with an error in the report — every other path requires pandas.

### 3.2 Step-by-step
1. **Open the workbook** with `pd.ExcelFile(filepath, engine="openpyxl")`. On failure, report a "cannot open" error and return.
2. **Map sheet names to IDs** using `lookup_workbook_sheet_id` (case-folded translated alias map).
3. **Read each sheet** via `_read_sheet(sheet_id)`:
   - Rename columns to canonical IDs (`canonicalize_workbook_columns`).
   - If the first row looks like the description placeholder (long text in the ID column), skip it. This handles the template's row-2 hints.
4. **Process in dependency order**: teachers → rooms → branches → classes.
   - Teachers: validate schema → check duplicates → parse availability fields (`_parse_comma_list`) → populate `state["lecturers"]` and `state["lecturer_availability"]`.
   - Rooms: validate → parse capacity → populate `state["classrooms"]` and `state["classroom_capacities"]`.
   - Branches: validate → group under a single default year named via `tr("status.default_year_name").format(n=1)`. Users can re-organize later.
   - Classes: validate → validate FK references to teachers/branches → parse location_type, duration, student_count → resolve room references in `allowed_rooms` / `excluded_rooms` → fill `cls["targets"]` based on branch_map → record `_joint_group` marker for post-processing.
5. **Post-process joint groups** (`_resolve_joint_groups`): classes sharing a `joint_class_group` value are merged into one joint session (their `targets` lists are unioned, duplicates dropped, all extras removed from `state["classes"]`).
6. **Normalise** every class with `normalize_state_classes(dataset.state)` — backfills missing keys, enforces location-field consistency.

### 3.3 Error and warning handling
`DataValidationReport` accumulates:
- `errors`: blocking issues. `is_valid == False` if any. The UI shows them via `ImportPreviewDialog`; the user is given the choice to cancel or proceed (cancel is recommended).
- `warnings`: non-blocking issues (unknown columns, unknown room references, ...).

Translation keys used: `errors.missing_columns`, `errors.duplicate_values`, `errors.teacher_id_required`, `errors.room_id_required`, `errors.branch_id_required`, `errors.class_id_required`, `errors.teacher_not_found`, `errors.branch_not_found`, `errors.unknown_rooms`, `errors.pandas_required`, `warnings.unknown_columns`, etc.

### 3.4 Out of scope for the importer
The importer does **not**:
- Configure `state["days"]` or `state["slots"]` — those come from `SetupDialog` or are inherited from the prior state.
- Place any class. After import, all classes are unplaced.
- Validate that the resulting schedule is feasible — that's the solver's job.

## 4. Template generation (`data_io/template.py`)

`generate_excel_template(filepath)`:
1. Requires `openpyxl`; raises with a localized error otherwise.
2. Creates a workbook with one sheet per `WORKBOOK_SHEETS` entry, titled with the localised name.
3. Writes:
   - **Row 1**: column headers (`Font(bold, size 11, white)`, `PatternFill solid #4472C4`).
   - **Row 2**: descriptions for each column (italic, grey), from translation keys.
   - **Rows 3+**: example data, taken from `_sheet_examples()` which embeds localised sample names ("Mathematics", "Computer Science", etc.).
4. Auto-sizes column widths based on max(header, description, examples) lengths up to 40.
5. Freezes panes at row 3 so header + description stay visible while scrolling.

The example data includes **a joint class group** (`C004` and `C005` both with `joint_class_group=J1`) so users see how multi-target sessions are encoded.

## 5. Excel export (`data_io/exporter.py`)

The exporter takes a `state` dict (typically the live working state) and writes a multi-sheet workbook.

### 5.1 `FinalSchedule` wrapper
A thin OO façade around the state dict (lines 36–80). Exposes `days`, `slots`, `classrooms`, `lecturers`, `years`. `build_grid()` returns a `(day, slot) → list[entry]` dict where each `entry` is a normalized class display record.

### 5.2 Sheets written
- **Classroom view** — rows = slots, columns = days × classrooms (group of subcolumns per classroom). Cells contain class code (bold blue), name (bold dark), lecturer, target group lines (`year/branch`), badge text if any. Background cell colour = `lighten_color(year_color, 0.45)`.
- **Lecturer view** — rows = slots, columns = days × lecturers.
- **Branch view** — rows = slots, columns = days × branches.
- **Show Everything** — flat matrix with colour-coded cells via `MATRIX_*` colour constants.

Rich text built with `openpyxl.cell.rich_text.CellRichText` and `TextBlock` + `InlineFont`. Plain-text fallback comes from `ui/cell_formatter.plain_cell_text`.

Borders / alignment / column widths configured via `openpyxl.styles.Border/Side/Font/PatternFill/Alignment`.

### 5.3 CSV export
Single-grid CSV using Python's stdlib `csv`. One row per slot, columns per day. Cells contain `plain_cell_text(entry)` with newlines escaped according to Python's `csv` defaults.

### 5.4 PDF export
Uses `reportlab` to produce a landscape A4 page with a styled table per view. Cell colours and fonts mimic the Excel output. Each view (per-classroom, per-lecturer, per-branch) becomes a separate page.

If `reportlab` is missing, the exporter raises a translated error (`errors.reportlab_required`).

## 6. Tier gating of import/export

`plans.py` lists the relevant feature flags:

| Tier | `export_pdf` | `export_excel` | `export_csv` |
|------|--------------|----------------|--------------|
| Free | ❌ | ❌ | ❌ |
| Starter | ✅ | ❌ | ✅ |
| Professional | ✅ | ✅ | ✅ |
| Max | ✅ | ✅ | ✅ |
| Institutional | ✅ | ✅ | ✅ |

Import is not currently gated by a feature flag — any tier can import. The UI gates the **Bulk scheduling** (`bulk_scheduling`) feature instead, which determines whether the Bulk Add dialog is enabled.

When an export action is invoked on a tier that lacks the feature, the action is disabled in the menu (with upgrade tooltip via `gate_menu_action`) and a `UpgradeDialog` is shown if the user clicks anyway.

## 7. Reporting (post-optimization)

The dashboard tab and the post-reschedule dialog produce structured "reports":

- `ScheduleAnalytics.analyze(placements)` returns:
  - `global_score: float`
  - `grade: str` — `"A"`–`"F"`
  - `lecturer_metrics: dict[lecturer → {compactness, gaps, total_slots, busiest_day}]`
  - `group_metrics: dict[(year, branch) → similar]`
  - `room_metrics: dict[room → {utilization, busiest_day}]`
  - `day_balance: dict[day → utilization]`
  - `insights: list[str]` — localised actionable strings.
- `analyze_conflict_graph(state)` and `analyze_constraint_propagation(state)` return diagnostic dicts surfaced in advanced analytics.

These reports are read-only views over the state and never mutate it.

## 8. Files mapping for this section

| Concern | File |
|---------|------|
| Workbook schema + headers + aliases | `data_io/schema.py` |
| Template generation | `data_io/template.py` |
| Import pipeline + validation | `data_io/importer.py` |
| Excel / CSV / PDF export | `data_io/exporter.py` |
| Cell content formatting | `ui/cell_formatter.py` |
| Badge formatting | `ui/badge_formatter.py` |
| Day-key normalization | `ui/day_keys.py` |
| Visual constants used in export | `core/constants.py` |
