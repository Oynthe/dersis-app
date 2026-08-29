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
    class_uses_physical_room, new_class, new_state, new_lecturer_availability,
    normalize_class_data, normalize_state_classes, parse_location_type_label,
)
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
    """Parse a comma-separated string into a list of stripped strings."""
    if _is_blank(value):
        return []
    return [x.strip() for x in str(value).split(",") if x.strip()]


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
        avail["allowed_days"] = _parse_comma_list(row.get("allowed_days"))
        avail["allowed_hours"] = _parse_comma_list(row.get("allowed_hours"))
        avail["excluded_days"] = _parse_comma_list(row.get("excluded_days"))
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
        if required_type:
            # Set by the excluded-rooms fallback below. Once it fires,
            # `required_classrooms` is no longer the type-resolved list, so the
            # capacity check that follows would be reporting a list the user
            # was already told was not applied.
            fell_back = False
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
                    cls["required_classrooms"] = (
                        [r for r in allowed_rooms if r in room_names]
                        if allowed_rooms else [])
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
                    report.add_warning(tr("labels.classes"), row_num, message)
                    fell_back = True

            # ST-FUNC-009, third contradiction: the resolved list versus the
            # head count in the very same row. `get_physical_room_candidates`
            # filters by `required_classrooms` FIRST and by `room_fits_class`
            # second, so a type that resolves only to rooms too small for
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
            # Scoped to the type-resolved list on purpose. A class whose
            # `allowed_rooms` are all too small reaches the same dead end and
            # is equally silent, but that path behaves exactly as it did at
            # 82f558e and is not this change's to fix.
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
            # function was about to throw away. `type_applied` replaces the
            # `matching` test for the reason given above: with `allowed_rooms`
            # present and disjoint from the type, `required_classrooms` is the
            # allowed list, and this sentence would name the type over it.
            if (type_applied and cls["required_classrooms"] and not fell_back
                    and class_uses_physical_room(cls)):
                capacities = dataset.state.get("classroom_capacities", {})
                # Capacity 0 means unlimited, matching `room_fits_class`; a
                # class of 0 participants fits anywhere, so it never warns.
                seats = {r: capacities.get(r, 0) for r in cls["required_classrooms"]}
                if student_count > 0 and all(
                        0 < cap < student_count for cap in seats.values()):
                    biggest = max(seats, key=lambda r: seats[r])
                    report.add_warning(
                        tr("labels.classes"), row_num,
                        tr("warnings.room_type_too_small").format(
                            type=required_type,
                            type_field=_column_label("classes", "required_room_type"),
                            participants=student_count,
                            count_field=_column_label("classes", "student_count"),
                            room=biggest,
                            capacity=seats[biggest]))

        if excluded_rooms:
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
        for other in classes[1:]:
            for t in other.get("targets", []):
                key = (t["year"], t["branch"])
                if key not in seen_targets:
                    primary["targets"].append(t)
                    seen_targets.add(key)
            # Remove the duplicate class from state
            if other in dataset.state["classes"]:
                dataset.state["classes"].remove(other)
