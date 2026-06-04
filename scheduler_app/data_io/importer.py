"""Excel import pipeline for the scheduler.

Reads structured .xlsx files and converts them into internal scheduler objects.
Does not modify scheduling logic or constraints.
"""

from dataclasses import dataclass, field
from typing import Any

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

from scheduler_app.models import (
    new_class, new_state, new_lecturer_availability,
    normalize_class_data, normalize_state_classes, parse_location_type_label,
)
from scheduler_app.translations import tr
from scheduler_app.data_io.schema import (
    canonicalize_workbook_columns,
    lookup_workbook_sheet_id,
)


# ── Schemas ─────────────────────────────────────────────────────────────────

TEACHER_REQUIRED = {"teacher_id", "name"}
TEACHER_OPTIONAL = {"allowed_days", "allowed_hours", "excluded_days", "excluded_hours"}
TEACHER_ALL = TEACHER_REQUIRED | TEACHER_OPTIONAL

ROOM_REQUIRED = {"room_id", "name"}
ROOM_OPTIONAL = {"capacity", "room_type"}
ROOM_ALL = ROOM_REQUIRED | ROOM_OPTIONAL

BRANCH_REQUIRED = {"branch_id", "name"}
BRANCH_ALL = BRANCH_REQUIRED

CLASS_REQUIRED = {"class_id", "course_name", "teacher_id", "branch_id", "duration"}
CLASS_OPTIONAL = {
    "class_code", "student_count", "required_room_type", "allowed_rooms",
    "excluded_rooms", "joint_class_group", "location_type",
}
CLASS_ALL = CLASS_REQUIRED | CLASS_OPTIONAL


# ── Data containers ─────────────────────────────────────────────────────────

@dataclass
class DataValidationReport:
    """Summarizes import validation issues."""
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def is_valid(self):
        return len(self.errors) == 0

    def add_error(self, sheet: str, row: int | None, message: str):
        prefix = (
            f"[{sheet}]"
            if row is None
            else tr("status.import_row_prefix").format(sheet=sheet, row=row)
        )
        self.errors.append(f"{prefix} {message}")

    def add_warning(self, sheet: str, row: int | None, message: str):
        prefix = (
            f"[{sheet}]"
            if row is None
            else tr("status.import_row_prefix").format(sheet=sheet, row=row)
        )
        self.warnings.append(f"{prefix} {message}")

    def summary(self) -> str:
        lines = []
        if self.errors:
            lines.append(tr("status.import_errors_count").format(n=len(self.errors)))
            lines.extend(f"  - {e}" for e in self.errors)
        if self.warnings:
            lines.append(tr("status.import_warnings_count").format(n=len(self.warnings)))
            lines.extend(f"  - {w}" for w in self.warnings)
        if not lines:
            lines.append(tr("status.import_validation_passed"))
        return "\n".join(lines)


@dataclass
class SchedulerDataset:
    """Container for imported scheduler data."""
    state: dict = field(default_factory=new_state)
    report: DataValidationReport = field(default_factory=DataValidationReport)
    raw_teachers: list = field(default_factory=list)
    raw_rooms: list = field(default_factory=list)
    raw_branches: list = field(default_factory=list)
    raw_classes: list = field(default_factory=list)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_comma_list(value) -> list:
    """Parse a comma-separated string into a list of stripped strings."""
    if pd.isna(value) or value is None:
        return []
    return [x.strip() for x in str(value).split(",") if x.strip()]


def _validate_schema(df, sheet_name: str, required: set, all_cols: set,
                     report: DataValidationReport) -> bool:
    """Validate that a DataFrame has the required columns."""
    actual = set(df.columns)
    missing = required - actual
    if missing:
        report.add_error(sheet_name, None,
                         tr("errors.missing_columns").format(
                             cols=", ".join(sorted(missing))))
        return False
    extra = actual - all_cols
    if extra:
        report.add_warning(sheet_name, None,
                           tr("warnings.unknown_columns").format(
                               cols=", ".join(sorted(extra))))
    return True


def _check_duplicates(df, id_col: str, sheet_name: str,
                      report: DataValidationReport):
    """Check for duplicate IDs."""
    dupes = df[df[id_col].duplicated(keep=False)]
    if not dupes.empty:
        dupe_vals = dupes[id_col].unique().tolist()
        report.add_error(sheet_name, None,
                         tr("errors.duplicate_values").format(
                             id_col=id_col,
                             values=", ".join(str(v) for v in dupe_vals)))


# ── Sheet processors ────────────────────────────────────────────────────────

def _process_teachers(df, report: DataValidationReport, dataset: SchedulerDataset):
    """Process Teachers sheet into lecturers and lecturer_availability."""
    if not _validate_schema(df, tr("labels.teachers"), TEACHER_REQUIRED, TEACHER_ALL, report):
        return

    _check_duplicates(df, "teacher_id", tr("labels.teachers"), report)

    lecturers = []
    availability = {}
    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row (1-indexed header + 1-indexed data)
        tid = str(row["teacher_id"]).strip()
        name = str(row["name"]).strip()
        if not tid or not name:
            report.add_error(tr("labels.teachers"), row_num, tr("errors.teacher_id_required"))
            continue

        lecturers.append(name)
        dataset.raw_teachers.append({"teacher_id": tid, "name": name})

        avail = new_lecturer_availability()
        avail["allowed_days"] = _parse_comma_list(row.get("allowed_days"))
        avail["allowed_hours"] = _parse_comma_list(row.get("allowed_hours"))
        avail["excluded_days"] = _parse_comma_list(row.get("excluded_days"))
        avail["excluded_hours"] = _parse_comma_list(row.get("excluded_hours"))
        if any(avail[k] for k in avail):
            availability[name] = avail

    dataset.state["lecturers"] = lecturers
    dataset.state["lecturer_availability"] = availability


def _process_rooms(df, report: DataValidationReport, dataset: SchedulerDataset):
    """Process Rooms sheet into classrooms and capacities."""
    if not _validate_schema(df, tr("tabs.rooms"), ROOM_REQUIRED, ROOM_ALL, report):
        return

    _check_duplicates(df, "room_id", tr("tabs.rooms"), report)

    classrooms = []
    capacities = {}
    for idx, row in df.iterrows():
        row_num = idx + 2
        rid = str(row["room_id"]).strip()
        name = str(row["name"]).strip()
        if not rid or not name:
            report.add_error(tr("tabs.rooms"), row_num, tr("errors.room_id_required"))
            continue

        classrooms.append(name)
        dataset.raw_rooms.append({
            "room_id": rid, "name": name,
            "capacity": int(row.get("capacity", 0) or 0),
            "room_type": str(row.get("room_type", "")).strip(),
        })
        cap = int(row.get("capacity", 0) or 0)
        if cap > 0:
            capacities[name] = cap

    dataset.state["classrooms"] = classrooms
    dataset.state["classroom_capacities"] = capacities


def _process_branches(df, report: DataValidationReport, dataset: SchedulerDataset):
    """Process Branches sheet into years structure."""
    if not _validate_schema(df, tr("setup.branches"), BRANCH_REQUIRED, BRANCH_ALL, report):
        return

    _check_duplicates(df, "branch_id", tr("setup.branches"), report)

    for idx, row in df.iterrows():
        row_num = idx + 2
        bid = str(row["branch_id"]).strip()
        name = str(row["name"]).strip()
        if not bid or not name:
            report.add_error(tr("setup.branches"), row_num, tr("errors.branch_id_required"))
            continue
        dataset.raw_branches.append({"branch_id": bid, "name": name})

    # Group branches as a flat structure under a default year
    # Users can re-organize later in the UI
    if dataset.raw_branches:
        branch_names = [b["name"] for b in dataset.raw_branches]
        dataset.state["years"] = {tr("status.default_year_name").format(n=1): branch_names}


def _process_classes(df, report: DataValidationReport, dataset: SchedulerDataset,
                     teacher_map: dict, branch_map: dict):
    """Process Classes sheet into scheduler class objects."""
    if not _validate_schema(df, tr("labels.classes"), CLASS_REQUIRED, CLASS_ALL, report):
        return

    _check_duplicates(df, "class_id", tr("labels.classes"), report)

    room_names = set(dataset.state.get("classrooms", []))

    for idx, row in df.iterrows():
        row_num = idx + 2
        cid = str(row["class_id"]).strip()
        course = str(row["course_name"]).strip()
        tid = str(row["teacher_id"]).strip()
        bid = str(row["branch_id"]).strip()

        if not cid or not course:
            report.add_error(tr("labels.classes"), row_num, tr("errors.class_id_required"))
            continue

        # Validate references
        if tid not in teacher_map:
            report.add_error(tr("labels.classes"), row_num,
                             tr("errors.teacher_not_found").format(tid=tid))
            continue
        if bid not in branch_map:
            report.add_error(tr("labels.classes"), row_num,
                             tr("errors.branch_not_found").format(bid=bid))
            continue

        duration = int(row.get("duration", 1) or 1)
        student_count = int(row.get("student_count", 0) or 0)

        cls = new_class()
        cls["class_code"] = str(row.get("class_code", "")).strip() if not pd.isna(row.get("class_code")) else ""
        cls["name"] = course
        cls["lecturer"] = teacher_map[tid]
        cls["duration"] = max(1, duration)
        cls["participants"] = student_count

        # Location type (optional column)
        lt_raw = str(row.get("location_type", "")).strip().lower()
        if lt_raw:
            cls["location_type"] = parse_location_type_label(lt_raw)

        # Branch target — use the first year that contains this branch
        branch_name = branch_map[bid]
        target_year = None
        for yr, branches in dataset.state.get("years", {}).items():
            if branch_name in branches:
                target_year = yr
                break
        if target_year:
            cls["targets"] = [{"year": target_year, "branch": branch_name}]

        # Room constraints
        allowed_rooms = _parse_comma_list(row.get("allowed_rooms"))
        excluded_rooms = _parse_comma_list(row.get("excluded_rooms"))
        if allowed_rooms:
            invalid = [r for r in allowed_rooms if r not in room_names]
            if invalid:
                report.add_warning(tr("labels.classes"), row_num,
                                   tr("errors.unknown_rooms").format(
                                       rooms=", ".join(invalid)))
            cls["required_classrooms"] = [r for r in allowed_rooms if r in room_names]
        if excluded_rooms:
            cls["excluded_classrooms"] = excluded_rooms

        # Joint class group
        jcg = str(row.get("joint_class_group", "")).strip()
        if jcg:
            cls["_joint_group"] = jcg  # Used for post-processing grouping

        normalize_class_data(cls)
        dataset.raw_classes.append({"class_id": cid, "cls": cls, "row": row_num})
        dataset.state["classes"].append(cls)


# ── Main entry point ────────────────────────────────────────────────────────

def load_scheduler_data_from_excel(filepath: str) -> SchedulerDataset:
    """Load scheduler data from a structured Excel workbook.

    Reads Teachers, Rooms, Branches, and Classes sheets. Validates schema
    and references. Returns a SchedulerDataset with populated state and report.

    Args:
        filepath: Path to the .xlsx file.

    Returns:
        SchedulerDataset with state dict and validation report.
    """
    if not HAS_PANDAS:
        ds = SchedulerDataset()
        ds.report.add_error(tr("labels.system"), None,
                            tr("errors.pandas_required"))
        return ds

    dataset = SchedulerDataset()
    report = dataset.report

    try:
        xls = pd.ExcelFile(filepath, engine="openpyxl")
    except Exception as e:
        report.add_error(tr("menus.file"), None, tr("errors.cannot_open_excel").format(err=e))
        return dataset

    sheet_names = xls.sheet_names
    sheet_lookup = {}
    for actual_name in sheet_names:
        sheet_id = lookup_workbook_sheet_id(actual_name)
        if sheet_id and sheet_id not in sheet_lookup:
            sheet_lookup[sheet_id] = actual_name

    # Read available sheets
    teachers_df = None
    rooms_df = None
    branches_df = None
    classes_df = None

    def _read_sheet(sheet_id):
        """Read a sheet, auto-detecting and skipping description rows."""
        actual_name = sheet_lookup[sheet_id]
        df = pd.read_excel(xls, actual_name)
        df = df.rename(columns=canonicalize_workbook_columns(sheet_id, df.columns))
        if df.empty:
            return df
        # If the first data row looks like descriptions (all strings, no
        # valid IDs), skip it — this handles the template's row-2 descriptions.
        first = df.iloc[0]
        id_col = [c for c in df.columns if c.endswith("_id")]
        if id_col:
            val = str(first.get(id_col[0], ""))
            # Heuristic: description rows have long text with spaces
            if len(val) > 20 or " " in val:
                df = df.iloc[1:].reset_index(drop=True)
        return df

    if "teachers" in sheet_lookup:
        teachers_df = _read_sheet("teachers")
    else:
        report.add_warning(tr("menus.file"), None, tr("errors.no_teachers_sheet"))

    if "rooms" in sheet_lookup:
        rooms_df = _read_sheet("rooms")
    else:
        report.add_warning(tr("menus.file"), None, tr("errors.no_rooms_sheet"))

    if "branches" in sheet_lookup:
        branches_df = _read_sheet("branches")
    else:
        report.add_warning(tr("menus.file"), None, tr("errors.no_branches_sheet"))

    if "classes" in sheet_lookup:
        classes_df = _read_sheet("classes")
    else:
        report.add_warning(tr("menus.file"), None, tr("errors.no_classes_sheet"))

    # Process sheets in dependency order
    if teachers_df is not None:
        _process_teachers(teachers_df, report, dataset)

    if rooms_df is not None:
        _process_rooms(rooms_df, report, dataset)

    if branches_df is not None:
        _process_branches(branches_df, report, dataset)

    # Build lookup maps for class processing
    teacher_map = {t["teacher_id"]: t["name"] for t in dataset.raw_teachers}
    branch_map = {b["branch_id"]: b["name"] for b in dataset.raw_branches}

    if classes_df is not None:
        _process_classes(classes_df, report, dataset, teacher_map, branch_map)

    # Post-process joint class groups
    _resolve_joint_groups(dataset)

    # Normalize all class data to ensure consistent fields
    normalize_state_classes(dataset.state)

    return dataset


def _resolve_joint_groups(dataset: SchedulerDataset):
    """Merge classes sharing the same joint_class_group into joint sessions."""
    groups = {}
    for entry in dataset.raw_classes:
        cls = entry["cls"]
        jg = cls.pop("_joint_group", "")
        if jg:
            groups.setdefault(jg, []).append(cls)

    for group_name, classes in groups.items():
        if len(classes) < 2:
            continue
        # Merge targets into the first class and remove duplicates
        primary = classes[0]
        primary["joint_session"] = True
        seen_targets = {(t["year"], t["branch"]) for t in primary.get("targets", [])}
        for other in classes[1:]:
            for t in other.get("targets", []):
                key = (t["year"], t["branch"])
                if key not in seen_targets:
                    primary["targets"].append(t)
                    seen_targets.add(key)
            # Remove the duplicate class from state
            if other in dataset.state["classes"]:
                dataset.state["classes"].remove(other)
