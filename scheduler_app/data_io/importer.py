"""Excel import pipeline for the scheduler.

Reads structured .xlsx files and converts them into internal scheduler objects.
Does not modify scheduling logic or constraints.
"""

from dataclasses import dataclass, field

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

from scheduler_app.models import (
    class_uses_physical_room, get_location_label, location_type_of, new_class,
    new_state, new_lecturer_availability, normalize_class_data,
    normalize_state_classes, parse_location_type_label,
)
from scheduler_app.i18n.day_keys import DAY_KEYS, day_label, normalize_day_value
from scheduler_app.i18n.text_fold import fold_text
from scheduler_app.translations import tr
from scheduler_app.data_io.schema import (
    canonicalize_workbook_columns,
    get_workbook_sheet_description_texts,
    get_workbook_sheet_header_map,
    resolve_workbook_sheet_ids,
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

def _is_blank(value) -> bool:
    """True when a spreadsheet cell holds nothing the user actually typed.

    pandas represents an empty cell as float ``NaN``, and ``str(NaN)`` is the
    *truthy* string ``'nan'``. Reading cells with a bare ``str(...)`` is what
    made blank ``joint_class_group`` cells collapse unrelated classes into a
    single joint session (ST-FUNC-002), and reading them with ``int(...)`` is
    what aborted entire imports (ST-FUNC-003). Every cell read goes through
    here or one of the two helpers below.
    """
    if value is None:
        return True
    if HAS_PANDAS:
        try:
            if pd.isna(value):
                return True
        except (TypeError, ValueError):
            pass  # not a scalar (list/array) — fall through to the text test
    return str(value).strip() == ""


def _cell_text(value) -> str:
    """Return a cell as a stripped string; every spelling of blank gives ''."""
    return "" if _is_blank(value) else str(value).strip()


def _cell_int(value, default):
    """Parse a numeric cell without ever aborting the import.

    Returns *default* for a blank cell — the fallback the original
    ``int(row.get(col, default) or default)`` always intended, which pandas'
    ``NaN`` silently defeated — and ``None`` for text that is not a number, so
    the caller can report that one row and carry on (ST-FUNC-003).
    """
    if _is_blank(value):
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _column_label(sheet_id: str, field: str) -> str:
    """The column header as the user sees it in their own workbook."""
    try:
        return get_workbook_sheet_header_map(sheet_id).get(field, field)
    except Exception:
        return field


def _parse_comma_list(value) -> list:
    """Parse a comma-separated string into a de-duplicated list of stripped strings.

    First occurrence wins, so the user's own order is preserved. Repeats were
    kept verbatim until 2026-08-29: `allowed_rooms='Oda 1, Oda 1'` reached
    `required_classrooms=['Oda 1', 'Oda 1']` and, once `_resolve_joint_groups`
    started rendering that list into a sentence, the import report read
    "...the joint session keeps Oda 1, Oda 1 and ignores...". No consumer ever
    wanted the repeat. This helper has exactly six call sites -- the class
    row's `allowed_rooms` and `excluded_rooms`, and the lecturer's four
    availability lists -- and every reader of all six filters some other
    sequence by membership rather than iterating the parsed list:
    `get_physical_room_candidates` walks `state["classrooms"]` testing
    `r in required` / `r not in excluded` (core/models.py:552-562), and
    `apply_lecturer_availability_filters` walks the day and time grids testing
    against `set(avail[...])` (core/models.py:529-548). So removing duplicates
    cannot change any candidate set or its order; it only stops them reaching
    state and the report. (`filter_class_days` DOES iterate its allow-list and
    would repeat a day, but a class's `allowed_days` has no workbook column and
    never comes from here.)
    """
    if _is_blank(value):
        return []
    items: list = []
    for x in str(value).split(","):
        x = x.strip()
        if x and x not in items:
            items.append(x)
    return items


def _parse_day_key_list(value, field: str, row_num: int,
                        report: DataValidationReport) -> list:
    """Parse a workbook day cell into stable day KEYS, warning on what fails.

    C1. This used to be `_parse_comma_list`, which stored the cell verbatim, so
    `allowed_days` reached state as `['Pazartesi', 'Çarşamba', 'Cuma']` while
    every engine reader -- `apply_lecturer_availability_filters` and
    `lecturer_available_at` (core/models.py:529-548) -- compares against
    `state["days"]`, which holds keys. `"Pazartesi" != "monday"`, so a
    non-empty `allowed_days` intersected NOTHING and the lecturer had zero
    available days (none of their classes placeable by drag, greedy pass or
    CP-SAT), and a non-empty `excluded_days` subtracted nothing and left the
    lecturer bookable on the day their own row ruled out. Not a hypothetical
    spelling: the app's own `generate_excel_template` writes
    `tr("weekdays.<key>")` into these two cells (data_io/template.py:48-61), so
    the file the user is told to fill in carries exactly the failing values, in
    whatever language they generated it in. Locale-independent -- "Monday"
    fails against "monday" just as "Pazartesi" does.

    Normalizing HERE rather than leaving it to `normalize_state_day_keys` on
    the autosave path is what makes the value survive an import into a
    schedule whose week is still empty. The workbook has no days sheet
    (`schema.WORKBOOK_SHEETS`), so the week can only come from Setup and
    nothing forces Setup to run first; measured on a fresh schedule, that pass
    pruned every day out of every availability record and `_auto_save` wrote
    the emptied roster to disk. The empty-week guard in
    `i18n/day_keys.normalize_state_day_keys` is the other half of this fix and
    both are needed: measured by disabling one at a time against
    `tests/test_phase9_c1.py`, this half alone leaves the fresh-schedule import
    failing (the keys are pruned against an empty grid) and the guard alone
    leaves the immediate import failing (raw labels bind nothing until the
    1500 ms autosave debounce converts them).

    `allowed_hours`/`excluded_hours` keep `_parse_comma_list`: they are
    compared as the literal slot strings the Setup grid holds, and have no
    canonical form to convert to.

    A cell that does not resolve is WARNED, not dropped in silence, because
    dropping inverts the row's meaning: "Pazrtesi" would leave `allowed_days`
    empty, and an empty allow-list reads downstream as "no day restriction at
    all". Nothing else in the app would say so -- the import report, the
    success dialog and the Setup availability table all look correct.
    """
    keys: list = []
    for text in _parse_comma_list(value):
        day_key = normalize_day_value(text)
        if day_key is None:
            report.add_warning(
                tr("labels.teachers"), row_num,
                tr("warnings.unknown_day").format(
                    value=text,
                    field=_column_label("teachers", field),
                    known=", ".join(day_label(k) for k in DAY_KEYS)))
        elif day_key not in keys:
            # Two spellings of one day ("Pazartesi", "pazartesi", "MONDAY")
            # fold to a single key; `_parse_comma_list` de-duplicates the raw
            # text and cannot see that they are the same day.
            keys.append(day_key)
    return keys


def _rooms_left(room_names: list, required: list, excluded: list) -> list:
    """The live rooms a class carrying these two lists could still be placed in.

    A deliberate mirror of `get_physical_room_candidates`
    (core/models.py:552-562) minus its capacity filter: `required` intersects,
    `excluded` subtracts, and an EMPTY `required` means "any room" rather than
    "no room" -- which is the whole reason a caller cannot just ask
    `if required and not excluded`.

    Capacity is left out on purpose. `_process_classes` already reports a row
    whose resolved room list holds nothing big enough for its head count --
    `warnings.room_type_too_small` when the type chose that list,
    `warnings.allowed_rooms_too_small` when Allowed Rooms did (C6) -- so
    folding capacity in here would make `_resolve_joint_groups` re-report that
    row's problem as a group problem and name the wrong remedy. It also keeps
    this answer stable under the
    participants bug noted in the Phase 9 review -- a joint group's
    `participants` is still `classes[0]`'s head count, not the sum -- so the
    number this check would divide by is one we already know is wrong.
    """
    rooms = [r for r in room_names if r in required] if required else list(room_names)
    return [r for r in rooms if r not in excluded]


def _room_names_by_type(dataset) -> dict[str, list[str]]:
    """``{folded room type: [room names]}`` for the rooms in *this* workbook.

    Built from ``dataset.raw_rooms`` -- the Rooms sheet of the file being read
    -- and never from the translation catalogues, because the type is free
    text: the template only *suggests* "Lecture or Lab", and a school that
    writes "Atolye" must match too, which is only possible when both sides of
    the comparison come from the same file. Matching the room's *name* instead
    would appear to work in Turkish, where the lab room is called
    "Laboratuvar A" and its type is "Laboratuvar"; it fails in Dutch, where the
    lab room is called "Lab A" and its type is "Practicum", and in az, where
    the room is still "Lab A" and the type is "Laboratoriya". Measured
    2026-08-29 over all 22 shipped locales, those two are the ONLY ones a
    name-match loses: in the other 20 it would happen to work. af, da, id and
    pl were previously listed here with nl and az and do not belong -- their
    room is "Lab A" *and* their type is "Lab", so a name-match succeeds. That
    those 20 work is precisely why a name-match is the wrong rule: it is right
    by coincidence of the template fixtures, and the type column is free text
    a school fills in itself.

    Both sides go through ``fold_text``, the one case rule this app compares
    user-typed text with, so a shouted "LABORATUVAR" still finds the room typed
    "Laboratuvar". Nothing folded here is ever stored: the fold is applied at
    comparison time only.
    """
    index: dict[str, list[str]] = {}
    for room in dataset.raw_rooms:
        folded = fold_text(room.get("room_type"))
        if folded:
            index.setdefault(folded, []).append(room["name"])
    return index


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
    # ST-FUNC-012: the roster that leaves this function is keyed by display
    # name, not by teacher_id — `state["lecturers"]` is a list of names,
    # `state["lecturer_availability"]` is keyed by name, and `cls["lecturer"]`
    # holds a name. The id is dropped at the door, so two teacher rows sharing
    # a name are one lecturer everywhere downstream. Measured on two rows both
    # named "Ada Lovelace": the second row's availability replaced the first's
    # (T001's excluded day vanished), the name appeared twice in the lecturer
    # list, and both teachers' classes came back carrying the same string, so
    # the core reads them as one person and refuses to schedule them in
    # parallel. Names are folded with `scheduler_app.i18n.text_fold.fold_text`,
    # the same rule `SchedulingWorkflow.register_lecturer` uses (ST-UI-020), so
    # the importer and the class form agree on what counts as a second teacher.
    # Both sides must move together or they disagree; that is why the fold
    # lives in a leaf module both layers can import rather than in either one.
    first_spelling: dict[str, str] = {}
    duplicate_names: list[tuple[int, str, str]] = []
    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row (1-indexed header + 1-indexed data)
        tid = _cell_text(row["teacher_id"])
        name = _cell_text(row["name"])
        if not tid or not name:
            report.add_error(tr("labels.teachers"), row_num, tr("errors.teacher_id_required"))
            continue

        folded = fold_text(name)
        if folded in first_spelling:
            duplicate_names.append((row_num, first_spelling[folded], name))
        else:
            first_spelling[folded] = name

        lecturers.append(name)
        dataset.raw_teachers.append({"teacher_id": tid, "name": name})

        avail = new_lecturer_availability()
        # C1 -- days become keys at the door, hours stay verbatim. See
        # `_parse_day_key_list` for why the conversion cannot be left to the
        # autosave path's `normalize_state_day_keys`.
        avail["allowed_days"] = _parse_day_key_list(
            row.get("allowed_days"), "allowed_days", row_num, report)
        avail["allowed_hours"] = _parse_comma_list(row.get("allowed_hours"))
        avail["excluded_days"] = _parse_day_key_list(
            row.get("excluded_days"), "excluded_days", row_num, report)
        avail["excluded_hours"] = _parse_comma_list(row.get("excluded_hours"))
        if any(avail[k] for k in avail):
            availability[name] = avail

    # Reported, not silently deduplicated: which of the two teachers a class
    # meant is written in the workbook's teacher_id, and the state has nowhere
    # to keep it, so merging them would throw away a distinction only the user
    # can restore. An error rather than a warning because the import is refused
    # whole (`_import_from_excel` returns on `not report.is_valid`) — a roster
    # imported with one teacher's hours silently overwritten is worse than a
    # roster the user is asked to give two distinct names.
    #
    # It gets its own key rather than reusing `errors.duplicate_values`, which
    # `_check_duplicates` uses for genuinely identical teacher_id cells. There
    # the two printed values are the same string and the word "duplicate"
    # explains itself; here they are visibly different — "Sıla Kaya, Sila Kaya"
    # — and the generic message asserts an equality the user can see is false,
    # which reads as the app being broken rather than as something to fix. The
    # message therefore names both spellings, states the rule that merged them,
    # and asks for the remedy the comment above only ever stated to other
    # programmers. The row number is the colliding row, not None: every other
    # error in this function carries one (see `errors.teacher_id_required`
    # above), and without it a 200-row roster gives the user nowhere to look.
    for row_num, first, second in duplicate_names:
        report.add_error(tr("labels.teachers"), row_num,
                         tr("errors.teacher_names_fold_together").format(
                             field=_column_label("teachers", "name"),
                             first=first, second=second))

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
        rid = _cell_text(row["room_id"])
        name = _cell_text(row["name"])
        if not rid or not name:
            report.add_error(tr("tabs.rooms"), row_num, tr("errors.room_id_required"))
            continue

        cap = _cell_int(row.get("capacity"), 0)
        if cap is None:
            report.add_warning(
                tr("tabs.rooms"), row_num,
                tr("warnings.invalid_number_defaulted").format(
                    value=_cell_text(row.get("capacity")),
                    field=_column_label("rooms", "capacity"),
                    default=0))
            cap = 0

        classrooms.append(name)
        dataset.raw_rooms.append({
            "room_id": rid, "name": name,
            "capacity": cap,
            "room_type": _cell_text(row.get("room_type")),
        })
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
        bid = _cell_text(row["branch_id"])
        name = _cell_text(row["name"])
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
    # `_process_rooms` runs before `_process_classes`, so `raw_rooms` is
    # populated by the time this reads it. `known_types` keeps the user's own
    # spelling, unfolded, because it goes into a message they read.
    rooms_by_type = _room_names_by_type(dataset)
    known_types = sorted({_cell_text(r.get("room_type")) for r in dataset.raw_rooms
                          if _cell_text(r.get("room_type"))})

    for idx, row in df.iterrows():
        row_num = idx + 2
        cid = _cell_text(row["class_id"])
        course = _cell_text(row["course_name"])
        tid = _cell_text(row["teacher_id"])
        bid = _cell_text(row["branch_id"])

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

        # ST-FUNC-003: one malformed number must cost the user one row, not the
        # whole import. `duration` is required, so unreadable text skips the row;
        # `student_count` is optional, so it degrades to 0 with a warning. A
        # blank cell in either is not malformed — it takes the documented default.
        duration = _cell_int(row.get("duration"), None)
        if duration is None and not _is_blank(row.get("duration")):
            report.add_error(
                tr("labels.classes"), row_num,
                tr("errors.invalid_number").format(
                    value=_cell_text(row.get("duration")),
                    field=_column_label("classes", "duration")))
            continue
        if duration is None:
            duration = 1
            report.add_warning(
                tr("labels.classes"), row_num,
                tr("warnings.blank_number_defaulted").format(
                    field=_column_label("classes", "duration"), default=1))

        student_count = _cell_int(row.get("student_count"), 0)
        if student_count is None:
            report.add_warning(
                tr("labels.classes"), row_num,
                tr("warnings.invalid_number_defaulted").format(
                    value=_cell_text(row.get("student_count")),
                    field=_column_label("classes", "student_count"),
                    default=0))
            student_count = 0

        cls = new_class()
        cls["class_code"] = _cell_text(row.get("class_code"))
        cls["name"] = course
        cls["lecturer"] = teacher_map[tid]
        cls["duration"] = max(1, duration)
        cls["participants"] = student_count

        # Location type (optional column)
        lt_raw = _cell_text(row.get("location_type")).lower()
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

        # Room constraints.
        #
        # ST-FUNC-009: `required_room_type` was declared in the schema, written
        # into the shipped template ("put Laboratuvar here") and then thrown
        # away, so the template's own C001 -- a lab class -- imported with an
        # empty `required_classrooms`, which `get_physical_room_candidates`
        # reads as "any room", and the physics lab was free to land in a
        # lecture hall. It is resolved to room *names* here rather than carried
        # into state as a new field: `required_classrooms` is the one room
        # constraint the solver, the conflict graph, the negotiator and the
        # class dialog all already read, and a second field would have to be
        # taught to every one of them, and to save/load, before it meant
        # anything.
        #
        # The type may only *narrow*. Every case where it cannot narrow leaves
        # the list exactly as `allowed_rooms` left it and says so in the report
        # -- it never writes an empty list, because empty means "any room",
        # which is the opposite of what the column says.
        allowed_rooms = _parse_comma_list(row.get("allowed_rooms"))
        excluded_rooms = _parse_comma_list(row.get("excluded_rooms"))
        if allowed_rooms:
            invalid = [r for r in allowed_rooms if r not in room_names]
            if invalid:
                report.add_warning(tr("labels.classes"), row_num,
                                   tr("errors.unknown_rooms").format(
                                       rooms=", ".join(invalid)))
            cls["required_classrooms"] = [r for r in allowed_rooms if r in room_names]

        required_type = _cell_text(row.get("required_room_type"))
        # C5 / ST-ARCH-004: every sentence this block writes is about a
        # classroom, and an online or lecturer-office lesson occupies none.
        # `get_room_candidates` answers such a class with the `[None]` virtual
        # sentinel and `normalize_class_data` (below) blanks both
        # `required_classrooms` and `excluded_classrooms` outright, so the gate
        # is message-only -- exactly as Phase 8's gate on
        # `warnings.room_type_too_small` was, on the same predicate
        # `room_fits_class` and `get_physical_room_candidates` short-circuit on.
        #
        # Measured on 42e1943, all four of the remaining sentences fired for
        # BOTH virtual location types, and two of them stated something the
        # user can check against their own sheet and find false:
        # `room_type_excludes_allowed_rooms` said "only Allowed Rooms was
        # applied" while the imported class holds `required_classrooms == []`,
        # and `room_type_all_excluded` / `room_type_allowed_all_excluded` said
        # the class "could never be placed anywhere" while
        # `get_room_candidates` hands it `[None]` and it places normally.
        # 935c84b's rule -- a room-type warning must be true of the row it is
        # attached to -- is the whole reason this gate exists.
        #
        # `errors.unknown_rooms` above stays ungated on purpose: "these names
        # are not in your Rooms sheet" is true of the row whatever its location
        # type is, and it names a typo the user still wants to know about.
        # Both hoisted out of the block below: C6 moved the head-count check
        # out from under `if required_type:`, so it now runs on a row that
        # never entered this block at all and still has to read them.
        #
        # `fell_back` is set by the excluded-rooms fallback. Once it fires,
        # `required_classrooms` is no longer the list the report just described,
        # so the head-count check must not speak about it.
        #
        # `matching` is the type's rooms. It stays `[]` when there is no type
        # -- and that is exactly what makes `type_decides_the_list` False on
        # that path, because the check only runs on a NON-empty
        # `required_classrooms`, which can never equal `[]`.
        fell_back = False
        matching: list = []
        if required_type and class_uses_physical_room(cls):
            # True only once the type has actually *decided*
            # `required_classrooms`. Both later blocks speak about "the room
            # type" and about the list the type produced; when the type could
            # not be applied -- no room has it, or `allowed_rooms` and the type
            # do not intersect -- `required_classrooms` is still the
            # `allowed_rooms` list, and a sentence naming the type over that
            # list names the wrong column, the wrong rooms and the wrong
            # remedy. `fell_back` already guarded the capacity check against
            # one of these two paths; this covers the other one, and the
            # excluded-rooms rescue as well.
            type_applied = False
            matching = [r for r in rooms_by_type.get(fold_text(required_type), [])
                        if r in room_names]
            if not matching:
                report.add_warning(
                    tr("labels.classes"), row_num,
                    tr("warnings.unknown_room_type").format(
                        type=required_type,
                        field=_column_label("classes", "required_room_type"),
                        # A workbook can type no room at all, and then the list
                        # is empty; an em dash keeps the sentence from ending on
                        # a bare colon in all 22 locales without a 23rd key.
                        known=", ".join(known_types) or "—"))
            elif not cls["required_classrooms"]:
                cls["required_classrooms"] = matching
                type_applied = True
            else:
                narrowed = [r for r in cls["required_classrooms"] if r in matching]
                if narrowed:
                    cls["required_classrooms"] = narrowed
                    type_applied = True
                else:
                    report.add_warning(
                        tr("labels.classes"), row_num,
                        tr("warnings.room_type_excludes_allowed_rooms").format(
                            type=required_type,
                            type_field=_column_label("classes", "required_room_type"),
                            rooms_field=_column_label("classes", "allowed_rooms")))

            # ST-FUNC-009, second contradiction. The block above enforces "the
            # type may only narrow" against `allowed_rooms`; `excluded_rooms`
            # is written independently below and can empty the candidate set
            # just as completely. `get_physical_room_candidates` intersects
            # required with the live rooms and THEN subtracts excluded, so a
            # row whose type matches only rooms the same row excludes ends up
            # with nowhere to go.
            #
            # Measured: required_room_type='Laboratuvar' with
            # excluded_rooms='Lab 1, Lab 2' gave candidates [] where 82f558e --
            # which discarded the column entirely -- gave ['Oda 1']. So Phase 8
            # turned a schedulable class into a permanently unplaceable one,
            # and the import report was empty. Falling back to the pre-type
            # list restores exactly the old behaviour and says why.
            #
            # Gated on `type_applied`, and that gate is provably message-only:
            # when the type was not applied `required_classrooms` is already
            # the `allowed_rooms` list (or empty, and then the guard below is
            # false), so the fallback assigned it the value it already held and
            # only the sentence reached the user. Measured before the gate, on
            # required_room_type='Derslik' + allowed_rooms='Lab 1' +
            # excluded_rooms='Lab 1': the row was told "every room of type
            # Derslik is also in Excluded Rooms" when the one Derslik room,
            # Oda 1, was not excluded at all, and told the type "was not
            # applied -- otherwise this class could never be placed", when the
            # preceding line had already said the type was not applied and the
            # class was still unplaceable afterwards. Both clauses false.
            if type_applied and cls["required_classrooms"] and excluded_rooms:
                survivors = [r for r in cls["required_classrooms"]
                             if r not in excluded_rooms]
                if not survivors:
                    fallback = ([r for r in allowed_rooms if r in room_names]
                                if allowed_rooms else [])
                    # Both sentences below end "...so the room type was not
                    # applied -- otherwise this class could never be placed
                    # anywhere". That promise is only true if the fallback
                    # actually leaves somewhere to go. When every fallback room
                    # is ALSO excluded the fallback is a no-op and the class is
                    # unplaceable either way, so the sentence claims a rescue
                    # that did not happen. Measured on
                    # `type=Laboratuvar, allowed_rooms='Lab 1',
                    # excluded_rooms='Lab 1'`: the row's only line said the
                    # type had been dropped so the class could still be placed,
                    # while `get_physical_room_candidates` was `[]`.
                    #
                    # Staying silent leaves that row unwarned, which is a real
                    # gap -- but it is the same gap the importer already has for
                    # any class made unplaceable by `allowed_rooms` alone, it is
                    # recorded in HANDOFF-PHASE9 §C, and a sentence that is
                    # false is worse than one that is missing.
                    # An EMPTY fallback is not "nowhere" — it is "any room", so
                    # it rescues the class whenever any room at all survives the
                    # exclusion. Reading it as nowhere silences the no-allowed_rooms
                    # case, which is the one the rescue was written for.
                    effective = fallback or sorted(room_names)
                    rescued = any(r not in excluded_rooms for r in effective)
                    cls["required_classrooms"] = fallback
                    # Which sentence is *true* here depends on whether
                    # `allowed_rooms` did any of the narrowing. With no
                    # `allowed_rooms`, the resolved list IS `matching`, so
                    # every room of the type really is excluded. With
                    # `allowed_rooms`, the empty set is the intersection of
                    # three columns and rooms of the type can be sitting
                    # unexcluded outside `allowed_rooms` -- measured, type
                    # 'Laboratuvar' + allowed 'Oda 1, Lab 1' + excluded 'Lab 1'
                    # claimed every lab was excluded while Lab 2 was neither.
                    all_of_type_excluded = all(r in excluded_rooms
                                               for r in matching)
                    if all_of_type_excluded:
                        message = tr("warnings.room_type_all_excluded").format(
                            type=required_type,
                            type_field=_column_label("classes", "required_room_type"),
                            rooms_field=_column_label("classes", "excluded_rooms"))
                    else:
                        message = tr("warnings.room_type_allowed_all_excluded").format(
                            type=required_type,
                            type_field=_column_label("classes", "required_room_type"),
                            allowed_field=_column_label("classes", "allowed_rooms"),
                            excluded_field=_column_label("classes", "excluded_rooms"))
                    if rescued:
                        report.add_warning(tr("labels.classes"), row_num, message)
                    fell_back = True

        # ST-FUNC-009, third contradiction: the resolved list versus the
        # head count in the very same row. `get_physical_room_candidates`
        # filters by `required_classrooms` FIRST and by `room_fits_class`
        # second, so a room list that resolves only to rooms too small for
        # `participants` collapses the candidate set to [] just as
        # completely as the two cases above.
        #
        # The app's own shipped template was such a row: C001 asked for the
        # lab type with 25 students while the only lab seats 20, and
        # `generate_excel_template -> load_scheduler_data_from_excel` gave
        # `is_valid=True`, `warnings: []`, `required ['Laboratuvar A']`,
        # `candidates []`. The template is repaired at its source
        # (template.py C001 now says 18), and this catches the user's own
        # workbook saying the same thing.
        #
        # Deliberately a WARNING that changes nothing, not a relaxation:
        # widening back to "any room" is the exact ST-FUNC-009 inversion
        # `test_a_room_type_no_room_has_is_reported_and_never_widens_the_class`
        # exists to catch, and the user may genuinely intend to buy chairs.
        #
        # Deliberately NOT left to the solver, even though the solver's own
        # reason is accurate and localized -- measured on the template, the
        # unplaced list said "İzin verilen hiçbir dersliğin yeterli
        # kapasitesi yok". That sentence only arrives after the user has
        # built a full Setup (days and times) and run a solve, and it
        # arrives as one line among every other class that failed for
        # ordinary reasons. This contradiction is between two cells of one
        # row, both of which the importer is holding at this moment, and
        # File ▸ Import Excel is where the app promises to "report any
        # problems before adding the classes".
        #
        # C6 widened it from the type-resolved list to ANY resolved list.
        # It was scoped to the type on purpose in Phase 8 and the comment
        # here said so ("...is not this change's to fix"), but the two
        # columns express the same physical fact from the user's seat, so
        # which one they happened to type was deciding whether the app
        # spoke. Measured on 42e1943 against one Rooms sheet (Oda 1/30,
        # Lab 1/20, Lab 2/20), three rows, one dead end
        # (`get_physical_room_candidates` == [] for all three), one warning:
        #   allowed_rooms='Lab 1, Lab 2', 25 students        -> silent
        #   required_room_type='Laboratuvar', 25 students    -> warned
        #   type='Laboratuvar' + allowed_rooms='Lab 1', 25   -> silent
        # The third is the sharper one: the user DID fill in the type and
        # the type-scoped check still said nothing, because
        # `type_decides_the_list` is False whenever `allowed_rooms` is a
        # subset of the type's rooms.
        #
        # Widening the GATE was not an option -- it re-opens exactly the
        # false sentence the adversarial pass caught in 42e1943 (see the
        # paragraph below). The gate stays; it demotes to a choice of
        # WORDING, and the column-neutral branch is its own key.
        #
        # `class_uses_physical_room` is the same predicate `room_fits_class`
        # and `get_physical_room_candidates` short-circuit on, and this
        # check is the importer's copy of their arithmetic, so it has to
        # short-circuit in the same place. An online or lecturer-office
        # class gets `[None]` from `get_room_candidates` -- the virtual
        # sentinel, ST-ARCH-004's correct answer -- and places normally;
        # `normalize_class_data` then discards `required_classrooms`
        # entirely. Measured before this clause: an online class with
        # required_room_type='Laboratuvar' and 25 students was told it
        # "cannot be placed until the room is enlarged, the head count
        # lowered, or the type changed", about a room list the same
        # function was about to throw away.
        #
        # The wording branch is `required_classrooms == matching`, i.e. "the
        # type IS what determines this list", NOT `type_applied`, i.e. "the
        # type touched this list". Those differ whenever `allowed_rooms` is a
        # SUBSET of the type's rooms: the intersection narrows to the
        # allowed list, the flag goes True, and the sentence then quantifies
        # over the type while the seats it counted came from Allowed Rooms.
        # Measured with a fourth room Lab 3 / Laboratuvar / 50 and a row of
        # `required_room_type=Laboratuvar, allowed_rooms=Lab 1,
        # student_count=25`: "No room of type Laboratuvar seats 25 - the
        # largest is Lab 1 with 20", every clause of which is false of the
        # user's own Rooms sheet, and all three remedies it offers are the
        # wrong cell to edit.
        #
        # Which is why that expression survives C6 unchanged. It no longer
        # decides WHETHER the row is reported, only which sentence reports
        # it: with Lab 3 present, "no room of type Laboratuvar seats 25" is
        # false while "no room listed in Allowed Rooms seats 25" is true of
        # the very same row.
        type_decides_the_list = cls["required_classrooms"] == matching
        if (cls["required_classrooms"] and not fell_back
                and class_uses_physical_room(cls)):
            capacities = dataset.state.get("classroom_capacities", {})
            # Capacity 0 means unlimited, matching `room_fits_class`; a
            # class of 0 participants fits anywhere, so it never warns.
            seats = {r: capacities.get(r, 0) for r in cls["required_classrooms"]}
            if student_count > 0 and all(
                    0 < cap < student_count for cap in seats.values()):
                biggest = max(seats, key=lambda r: seats[r])
                if type_decides_the_list:
                    message = tr("warnings.room_type_too_small").format(
                        type=required_type,
                        type_field=_column_label("classes", "required_room_type"),
                        participants=student_count,
                        count_field=_column_label("classes", "student_count"),
                        room=biggest,
                        capacity=seats[biggest])
                else:
                    # Column-neutral on purpose: it must not interpolate
                    # `required_type`, because this branch is precisely the
                    # case where the type is not what chose these rooms.
                    message = tr("warnings.allowed_rooms_too_small").format(
                        rooms_field=_column_label("classes", "allowed_rooms"),
                        participants=student_count,
                        count_field=_column_label("classes", "student_count"),
                        room=biggest,
                        capacity=seats[biggest])
                report.add_warning(tr("labels.classes"), row_num, message)

        if excluded_rooms:
            # C3. `allowed_rooms`, one column to the left, checks its names
            # against the workbook's own Rooms sheet and reports the ones that
            # match nothing. This column did not, and the silence has a
            # consequence: `get_physical_room_candidates` subtracts exclusions
            # by exact name, so `Lab1` written for `Lab 1` forbids nothing
            # while the user believes it forbids something. Measured on
            # 42e1943 against a workbook holding 'Oda 1' and 'Lab 1':
            # excluded_rooms='Lab1' left candidates ['Oda 1', 'Lab 1'] and an
            # EMPTY report; excluded_rooms='Lab 1' gave ['Oda 1']. Same intent,
            # one character apart, opposite outcomes, nothing said.
            #
            # Nothing downstream recovers it. `reconcile_placements` strips the
            # dangling name but deliberately reports only a lost *required*
            # room (core/workflow.py), so the toast reads "1 class repaired"
            # with no room and no column; and `AddClassDialog` builds its room
            # checkboxes from the live room list, so a name no room answers to
            # has no checkbox to notice. This report is the only surface the
            # user has.
            #
            # WARNING ONLY. The list is deliberately NOT filtered the way its
            # twin filters: `room_names` is the Rooms sheet of THIS workbook,
            # and `load_scheduler_data_from_excel` supports a workbook without
            # one ("a school may keep its rooms in a separate file"), so
            # filtering would delete every exclusion in a classes-only workbook
            # -- exclusions naming rooms the state being merged into holds
            # perfectly well. Measured: on such a workbook the allowed_rooms
            # path already empties its own list and warns about a sheet the
            # file never claimed to carry. That is a live defect to avoid
            # copying, not a precedent. The same reason gates this warning on
            # `room_names`: with no Rooms sheet every exclusion would look
            # unknown. (`room_names` is empty exactly when `raw_rooms` is --
            # `_process_rooms` appends to both in the same step.)
            #
            # Its own key, not `errors.unknown_rooms`: all 22 locales hard-code
            # the literal column name `allowed_rooms` inside that string, so
            # reusing it here would name the wrong cell to edit -- the defect
            # 935c84b closed for the room-type warning one column over.
            #
            # Gated on `class_uses_physical_room` for the C5 reason: the
            # sentence's point is that the lesson can still land in the room
            # the sheet meant to forbid, and a class that occupies no classroom
            # cannot. `normalize_class_data` blanks `excluded_classrooms` for
            # such a row a few lines below, so there is no harm left to report.
            unknown_excluded = [r for r in excluded_rooms if r not in room_names]
            if unknown_excluded and room_names and class_uses_physical_room(cls):
                report.add_warning(
                    tr("labels.classes"), row_num,
                    tr("warnings.unknown_excluded_rooms").format(
                        field=_column_label("classes", "excluded_rooms"),
                        rooms=", ".join(unknown_excluded)))
            cls["excluded_classrooms"] = excluded_rooms

        # Joint class group. ST-FUNC-002: a blank cell arrives as NaN, and
        # `str(NaN)` is the truthy string 'nan', so every blank-group class used
        # to share one joint key and all but the first were deleted from state.
        jcg = _cell_text(row.get("joint_class_group"))
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

    # Resolved against the whole workbook at once, not sheet by sheet: one
    # title can name two different sheets in two different languages (Spanish
    # *Aulas* is a classroom, Portuguese *Aulas* is a class), and only the
    # company a title keeps can say which one this workbook means.
    sheet_lookup = resolve_workbook_sheet_ids(xls.sheet_names)

    # ST-FUNC-011: a workbook in which *no* sheet is recognized is not a
    # half-filled roster, it is the wrong file. Measured on an unrelated
    # workbook: errors=[], four warnings naming the four absent sheets, empty
    # state, `is_valid=True` — and `_import_from_excel` shows exactly that as
    # "import successful", so pointing the app at a budget spreadsheet ended in
    # a success dialog over nothing. One recognized sheet is still enough to
    # import (a school may keep its rooms in a separate file), so the error
    # fires only on zero; the per-sheet warnings below still say which sheets
    # were looked for.
    if not sheet_lookup:
        report.add_error(tr("menus.file"), None,
                         tr("errors.unrecognized_file_format"))

    # Read available sheets
    teachers_df = None
    rooms_df = None
    branches_df = None
    classes_df = None

    def _read_sheet(sheet_id):
        """Read a sheet, dropping the template's row-2 help text if it is there."""
        actual_name = sheet_lookup[sheet_id]
        df = pd.read_excel(xls, actual_name)
        df = df.rename(columns=canonicalize_workbook_columns(sheet_id, df.columns))
        if df.empty:
            return df
        # The generated template puts one row of help text under the headers.
        # It is recognized by *being* one of the strings the template writes,
        # in any of the shipped languages — not by being long or containing a
        # space, which is a description in Turkish and a class id in Chinese.
        # Getting that wrong cost data in both directions: the zh and ja
        # templates imported their own help text as a lecturer, a classroom
        # and a branch, and a class id written "C 001" was silently dropped
        # (ST-FUNC-010).
        id_cols = [c for c in df.columns if c.endswith("_id")]
        if id_cols:
            val = _cell_text(df.iloc[0].get(id_cols[0], ""))
            if val and val in get_workbook_sheet_description_texts(sheet_id):
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
    groups: dict = {}
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
        # B5 / ST-FUNC-009 — the room constraints have to be merged too.
        #
        # `_process_classes` resolves `required_room_type` into
        # `required_classrooms` per ROW, and this loop used to keep row 1 and
        # delete the rest, so every room constraint declared by any other row
        # was thrown away. Measured on a two-row group whose SECOND row said
        # `Laboratuvar`: the merged session imported with
        # `required_classrooms=[]`, and `[]` is precisely what
        # `get_physical_room_candidates` reads as "any room" — candidates came
        # back `['Oda 1', 'Lab 1', 'Lab 2']`, i.e. the lecture hall was a legal
        # placement for a lab. Row order carries no meaning in a spreadsheet
        # and nothing tells a user the typed row must come first, so a room
        # type on any row has to reach the merged session.
        #
        # That is the invariant this block actually establishes, and it is
        # narrower than the one written here until 2026-08-29 ("the same two
        # rows swapped must produce the same session"). Swapping the rows does
        # NOT in general produce the same session and this loop never made it
        # so: `location_type` is still whatever `classes[0]` says, and two rows
        # declaring disjoint room types resolve by "the earlier row wins".
        # Both of those are announced below rather than merged — see the two
        # warnings at the end — because a joint session is one session and two
        # rows that disagree about it are a data error only the user can
        # settle. What is order-independent is that no constraint is silently
        # dropped and that the merge never widens the session.
        room_names = list(dataset.state.get("classrooms", []))
        # `normalize_class_location_fields` blanks `required_classrooms` and
        # `excluded_classrooms` for every non-physical class — per row on the
        # way in, and again over the whole state right after this function
        # returns — so for an online or lecturer-office primary every list the
        # room merge could compute is wiped before anything reads it. Skipping
        # it is therefore a measured no-op on state (b1: `required=[]` either
        # way), and it is a guard rather than a tidy-up because the
        # unplaceability check below would otherwise fire on a session that
        # needs no room at all and is perfectly placeable.
        merge_rooms = class_uses_physical_room(primary)
        required = list(primary.get("required_classrooms") or [])
        excluded = list(primary.get("excluded_classrooms") or [])
        dropped_rooms: list = []
        unplaceable_rooms: list = []
        other_locations: list = []
        primary_location = location_type_of(primary)
        for other in classes[1:]:
            for t in other.get("targets", []):
                key = (t["year"], t["branch"])
                if key not in seen_targets:
                    primary["targets"].append(t)
                    seen_targets.add(key)
            # A row that disagrees about *where* the session happens. Blank
            # reads as face-to-face, which is what `_process_classes` already
            # did with the cell, so this compares the values the importer
            # actually holds rather than guessing at intent.
            other_location = location_type_of(other)
            if (other_location != primary_location
                    and other_location not in other_locations):
                other_locations.append(other_location)
            if not merge_rooms:
                if other in dataset.state["classes"]:
                    dataset.state["classes"].remove(other)
                continue
            # This row's contribution is computed first and applied only if the
            # group survives it (see the check below), so nothing here writes
            # `required`/`excluded` directly.
            new_required = required
            new_dropped: list = []
            other_required = other.get("required_classrooms") or []
            if other_required:
                if not required:
                    new_required = list(other_required)
                else:
                    narrowed = [r for r in required if r in other_required]
                    if narrowed:
                        new_required = narrowed
                    else:
                        # Disjoint lists. NEVER write the empty intersection:
                        # `[]` means "any room", so intersecting `Derslik`
                        # (`['Oda 1']`) with `Laboratuvar` (`['Lab 1','Lab 2']`)
                        # would turn the one session TWO rows constrained into
                        # the least constrained class in the file. That is the
                        # ST-FUNC-009 inversion itself, which Phase 8 shipped
                        # once inside `_process_classes`; it is guarded by
                        # `test_a_joint_merge_never_widens_a_group_whose_rows_declare_room_types`.
                        #
                        # The EARLIER row wins and the user is told. A joint
                        # session is one physical session in one room, so two
                        # disjoint room constraints are a data error only the
                        # user can settle — silently choosing either one puts
                        # the session where their own sheet says it must not
                        # go, so the choice has to be announced rather than
                        # made well. "Most constrained wins" was rejected: the
                        # shortest list is an artefact of how many rooms happen
                        # to carry each type, so adding one room to the Rooms
                        # sheet would silently flip which of two unrelated rows
                        # decides this group. Row order is visible in the file
                        # and stable under edits elsewhere in it.
                        new_dropped = [r for r in other_required
                                       if r not in dropped_rooms]
            # `excluded_classrooms` has the same hole — checked, not assumed:
            # measured with `excluded_rooms='Lab 1'` on row 2 of a joint pair,
            # the merged session came out `excluded_classrooms=[]` with
            # candidates `['Oda 1', 'Lab 1', 'Lab 2']`; it is now `['Lab 1']`
            # and `['Oda 1', 'Lab 2']`. The rule here is the opposite one,
            # because an exclusion is a prohibition: the UNION is the only
            # merge that can neither widen the session nor depend on row order,
            # and unlike the intersection above it can never contradict itself.
            other_excluded = other.get("excluded_classrooms") or []
            new_excluded = list(excluded)
            for room in other_excluded:
                if room not in new_excluded:
                    new_excluded.append(room)
            # The union CAN, however, leave the session nowhere to go, and the
            # first version of this merge shipped that silently. Measured
            # against the pre-merge importer (`git show f049964:` ...), on a
            # two-row group whose first row said `required_room_type=Derslik`
            # and whose second said `excluded_rooms='Oda 1'`:
            #   before  req=['Oda 1']  exc=[]         candidates=['Oda 1']
            #   after   req=['Oda 1']  exc=['Oda 1']  candidates=[]  warnings=0
            # Three more shapes did the same (a type against an exclusion of
            # every room of that type, in both row orders; and three rows each
            # excluding one different room, which no single row could do). A
            # group the school had been timetabling for years became impossible
            # to place, and File > Import Excel reported zero errors and zero
            # warnings — the merge was widening nothing and quietly emptying
            # everything.
            #
            # The comment that stood here called that "the pre-existing gap of
            # HANDOFF-PHASE9 §C, the same one a single row's `allowed_rooms`
            # already has". Both halves were false: the union arrived with the
            # merge in a1e1d13 and these workbooks imported placeable before
            # it, and the single-row twin is NOT unhandled —
            # `_process_classes` rescues and reports the identical
            # contradiction ~300 lines above (`required_room_type=Laboratuvar`
            # + `excluded_rooms='Lab 1, Lab 2'` on ONE row falls back to
            # `required_classrooms=[]`, candidates `['Oda 1']`, and says so).
            # So the group-level merge now does what its own file already did.
            #
            # The rule: a row's room restriction is applied only if the group
            # still has somewhere to meet afterwards; otherwise the whole of
            # that row's contribution is skipped and the user is told. Gated on
            # the group being placeable BEFORE this row — when the primary's
            # own two columns already left it nowhere, the merge is not what
            # emptied it, and inventing a rescue there would claim credit for
            # fixing a row-level problem this loop cannot see. Coarse on
            # purpose: a row carrying both a narrowing type and a fatal
            # exclusion loses both rather than being unpicked column by column,
            # so the rule stays one sentence the report can state.
            if (_rooms_left(room_names, required, excluded)
                    and not _rooms_left(room_names, new_required, new_excluded)):
                unplaceable_rooms.extend(
                    r for r in list(other_required) + list(other_excluded)
                    if r not in unplaceable_rooms)
            else:
                required = new_required
                excluded = new_excluded
                dropped_rooms.extend(new_dropped)
            # Remove the duplicate class from state
            if other in dataset.state["classes"]:
                dataset.state["classes"].remove(other)
        primary["required_classrooms"] = required
        primary["excluded_classrooms"] = excluded
        if unplaceable_rooms:
            # `kept` is resolved HERE, after the loop, not where the rejection
            # happened: a later row can still narrow the group legitimately,
            # and a sentence naming rooms the session no longer has would be
            # false of the thing it is attached to (935c84b). It can never
            # render empty — a rejection only happens when the group was
            # placeable before that row, the rejected row changes nothing, and
            # every accepted row is accepted precisely because it left
            # something.
            dataset.report.add_warning(
                tr("labels.classes"), None,
                tr("warnings.joint_group_room_unplaceable").format(
                    group_field=_column_label("classes", "joint_class_group"),
                    group=group_name,
                    dropped=", ".join(unplaceable_rooms),
                    kept=", ".join(_rooms_left(room_names, required, excluded)),
                    type_field=_column_label("classes", "required_room_type"),
                    rooms_field=_column_label("classes", "allowed_rooms"),
                    excluded_field=_column_label("classes", "excluded_rooms")))
        if other_locations:
            # `location_type` is NOT merged, and deliberately so: there is no
            # defensible winner between "online" and "face-to-face" for one
            # session, and picking either would flip whole sessions in existing
            # workbooks on the strength of a single stray cell. What was wrong
            # was the silence. Measured: row 1 online + row 2 face-to-face with
            # `required_room_type=Laboratuvar` imported as an online session
            # with `required_classrooms=[]` and an empty report — the school's
            # lab requirement simply gone. The two orderings still produce
            # different sessions, but both now say so, which is the same
            # resolution the disjoint-room-type case above already uses.
            dataset.report.add_warning(
                tr("labels.classes"), None,
                tr("warnings.joint_group_location_conflict").format(
                    group_field=_column_label("classes", "joint_class_group"),
                    group=group_name,
                    location_field=_column_label("classes", "location_type"),
                    kept=get_location_label(primary_location),
                    dropped=", ".join(get_location_label(lt)
                                      for lt in other_locations)))
        if dropped_rooms:
            # Reported against the GROUP, not against a row (`row=None`). The
            # row that declared the losing constraint has just been deleted
            # from state, so a per-row line would describe a class the user
            # will never see in the app — and a warning has to be true of the
            # thing it is attached to (935c84b). It names rooms rather than
            # room types on purpose: the same disjointness arises from two rows
            # whose `allowed_rooms` simply do not overlap, and a sentence about
            # "room types" would be false of that workbook while the room names
            # are true of both.
            dataset.report.add_warning(
                tr("labels.classes"), None,
                tr("warnings.joint_group_room_conflict").format(
                    group_field=_column_label("classes", "joint_class_group"),
                    group=group_name,
                    kept=", ".join(required),
                    dropped=", ".join(dropped_rooms),
                    type_field=_column_label("classes", "required_room_type"),
                    rooms_field=_column_label("classes", "allowed_rooms")))
