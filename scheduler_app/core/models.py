"""Data model: schedule state and class structure."""

import uuid
from typing import Optional, TypedDict

from scheduler_app.i18n.text_fold import fold_text
from scheduler_app.translations import TRANSLATIONS, tr


# ── Location types ──────────────────────────────────────────────────────
LOCATION_FACE_TO_FACE = "face_to_face"
LOCATION_ONLINE = "online"
LOCATION_LECTURER_OFFICE = "lecturer_office"

LOCATION_TYPES = [LOCATION_FACE_TO_FACE, LOCATION_ONLINE, LOCATION_LECTURER_OFFICE]
VIRTUAL_LOCATION_TYPES = [LOCATION_ONLINE, LOCATION_LECTURER_OFFICE]

LOCATION_LABELS = {
    LOCATION_FACE_TO_FACE: "Face-to-Face",
    LOCATION_ONLINE: "Online",
    LOCATION_LECTURER_OFFICE: "Office (Lecturer)",
}

LOCATION_LABEL_KEYS = {
    LOCATION_FACE_TO_FACE: "location_types.face_to_face",
    LOCATION_ONLINE: "location_types.online",
    LOCATION_LECTURER_OFFICE: "location_types.lecturer_office",
}

# Virtual display names for timetable views (not real classrooms)
VIRTUAL_LOCATION_DISPLAY = {
    LOCATION_ONLINE: "Online",
    LOCATION_LECTURER_OFFICE: "Office (Lecturer)",
}

_ROOM_UNSET = object()


def get_location_label(location_type):
    """Return the translated display label for a location type."""
    key = LOCATION_LABEL_KEYS.get(location_type, LOCATION_LABEL_KEYS[LOCATION_FACE_TO_FACE])
    return tr(key)


def parse_location_type_label(value):
    """Parse a raw or translated location-type label into a stable key.

    Folded with ``fold_text``, not ``str.casefold``. Phase 8 routed four
    comparison sites through the shared fold and left this one behind, which
    made the importer inconsistent *within a single row*: two lines apart,
    ``required_room_type`` matched a shouted Turkish cell and this did not.
    Measured before the fix -- ``'ÇEVRİMİÇİ'`` -> ``face_to_face``,
    ``'OFİS (ÖĞR. ELEM.)'`` -> ``face_to_face``, while ``'Çevrimiçi'`` and
    ``'ONLINE'`` both resolved correctly.

    That miss is silent and expensive: the fallback below is
    ``LOCATION_FACE_TO_FACE``, which is indistinguishable from a blank cell, so
    an online lesson imported from a workbook whose cells are upper-cased --
    what a Turkish-locale Excel ``=UPPER()`` writes -- is marked as needing a
    physical room, and ``needs_physical_room`` then reserves a classroom for
    every remote lecture.

    The soft fallback itself is deliberately left alone: whether an
    unrecognised location label should be an import error is a product
    decision, not a folding one, and it is recorded in HANDOFF-PHASE9.md.
    """
    text = str(value or "").strip()
    if not text:
        return LOCATION_FACE_TO_FACE
    lowered = fold_text(text)
    if lowered in {fold_text(lt): lt for lt in LOCATION_TYPES}:
        return {fold_text(lt): lt for lt in LOCATION_TYPES}[lowered]

    aliases = {}
    for lt, label_key in LOCATION_LABEL_KEYS.items():
        aliases[fold_text(LOCATION_LABELS[lt])] = lt
        for lang_dict in TRANSLATIONS.values():
            aliases[fold_text(str(lang_dict.get(label_key, "")).strip())] = lt
    return aliases.get(lowered, LOCATION_FACE_TO_FACE)


def is_virtual_location_type(location_type):
    """Return True when *location_type* refers to a virtual resource."""
    return location_type in VIRTUAL_LOCATION_TYPES


def location_type_of(cls):
    """Return the normalized location_type for a class."""
    lt = cls.get("location_type", LOCATION_FACE_TO_FACE)
    return lt if lt in LOCATION_TYPES else LOCATION_FACE_TO_FACE


def class_uses_physical_room(cls):
    """Return True if the class requires a physical classroom.

    This is the single source of truth for room-allocation branching.
    Only face_to_face classes need physical rooms; online and
    lecturer_office classes bypass all classroom logic.
    """
    return location_type_of(cls) == LOCATION_FACE_TO_FACE


def needs_physical_room(cls):
    """Backward-compatible alias for class_uses_physical_room()."""
    return class_uses_physical_room(cls)


def get_special_location_resource(cls_or_location_type):
    """Return the system-defined effective resource label for a special location."""
    if isinstance(cls_or_location_type, dict):
        lt = location_type_of(cls_or_location_type)
        lecturer = str(cls_or_location_type.get("lecturer", "") or "").strip()
    else:
        lt = cls_or_location_type if cls_or_location_type in LOCATION_TYPES else LOCATION_FACE_TO_FACE
        lecturer = ""
    if lt == LOCATION_LECTURER_OFFICE and lecturer:
        # A lecturer's office is not one institution-wide virtual classroom.
        # Keep the translated location label, but qualify it with the lecturer
        # so views, exports and analytics distinguish simultaneous office
        # lessons belonging to different people.
        return f"{get_location_label(lt)} — {lecturer}"
    if is_virtual_location_type(lt):
        return get_location_label(lt)
    return None


def get_active_physical_classroom(cls, room_override=_ROOM_UNSET):
    """Return the active physical classroom for a class, or None."""
    if not class_uses_physical_room(cls):
        return None
    if room_override is not _ROOM_UNSET:
        return room_override
    if cls.get("pinned"):
        return cls.get("pinned_classroom") or None
    return cls.get("placed_classroom") or None


def get_effective_room_resource_for_class(cls, room_override=_ROOM_UNSET):
    """Return the effective room/resource label for a class."""
    if not class_uses_physical_room(cls):
        return get_special_location_resource(cls) or ""
    room = get_active_physical_classroom(cls, room_override=room_override)
    return room or ""


def get_display_location_label(cls, room_override=_ROOM_UNSET):
    """Return the display label for the class's effective location."""
    return get_effective_room_resource_for_class(cls, room_override=room_override)


def get_lecturer_office_options(lecturers):
    """Return ``(lecturer, label)`` pairs for the classroom-view filter."""
    options = []
    seen = set()
    for lecturer in lecturers or []:
        lecturer = str(lecturer or "").strip()
        if not lecturer or lecturer in seen:
            continue
        seen.add(lecturer)
        office_cls = {
            "location_type": LOCATION_LECTURER_OFFICE,
            "lecturer": lecturer,
        }
        options.append(
            (lecturer, get_effective_room_resource_for_class(office_cls)))
    return options


def display_room(cls):
    """Backward-compatible wrapper for effective room/resource display."""
    return get_display_location_label(cls)


def get_classroom_export_labels(classrooms, classes):
    """Return exportable classroom/resource labels in stable display order.

    Physical classrooms are always listed first, preserving their existing
    order. Virtual resources are appended only when at least one placed/pinned
    class actually uses that effective resource.
    """
    labels = []
    seen = set()
    for room in classrooms or []:
        if not room or room in seen:
            continue
        labels.append(room)
        seen.add(room)

    # Keep virtual types grouped in their established order, while retaining
    # one distinct office resource per lecturer. Online is intentionally still
    # a shared display category: it is not a room owned by one lecturer.
    for location_type in VIRTUAL_LOCATION_TYPES:
        for cls in classes or []:
            if location_type_of(cls) != location_type:
                continue
            label = get_effective_room_resource_for_class(cls)
            if label and label not in seen:
                labels.append(label)
                seen.add(label)
    return labels


# ── Protection levels ────────────────────────────────────────────────────
# ── Optimizer determinism ───────────────────────────────────────────────────
# ST-SCHED-013: the optimizer used the unseeded process-global `random`, so the
# same input produced a different (sometimes markedly worse) timetable every
# run. This is the default seed every entry point falls back to; pass
# `seed=None` to draw a fresh one and randomize deliberately. It lives here
# because models.py is the only constants home both core.schedule_optimizer and
# core.lns_strategies can import without creating a cycle.
DEFAULT_OPTIMIZER_SEED = 20260101


PROTECTION_NONE = "none"               # Fully movable (default)
PROTECTION_SOFT = "soft"               # Try to preserve; move if necessary
PROTECTION_SAME_DAY = "same_day"       # Movable within the same day only
PROTECTION_IMPROVE_ONLY = "improve_only"  # Move only if quality improves
PROTECTION_LOCKED = "locked"           # Cannot be moved (like pinned)

PROTECTION_LEVELS = [
    PROTECTION_NONE,
    PROTECTION_SOFT,
    PROTECTION_SAME_DAY,
    PROTECTION_IMPROVE_ONLY,
    PROTECTION_LOCKED,
]

# ── Course requirement / student-overlap policy ────────────────────────────
# ``unspecified`` is the migration-safe default for schedules saved before
# the field existed.  It behaves conservatively: no elective-only overlap is
# granted until the user explicitly classifies the lesson.
COURSE_REQUIREMENT_UNSPECIFIED = "unspecified"
COURSE_REQUIREMENT_REQUIRED = "required"
COURSE_REQUIREMENT_ELECTIVE = "elective"

COURSE_REQUIREMENTS = [
    COURSE_REQUIREMENT_UNSPECIFIED,
    COURSE_REQUIREMENT_REQUIRED,
    COURSE_REQUIREMENT_ELECTIVE,
]

COURSE_REQUIREMENT_LABEL_KEYS = {
    COURSE_REQUIREMENT_UNSPECIFIED: "course_requirement.unspecified",
    COURSE_REQUIREMENT_REQUIRED: "course_requirement.required",
    COURSE_REQUIREMENT_ELECTIVE: "course_requirement.elective",
}

STUDENT_OVERLAP_NEVER = "never"
STUDENT_OVERLAP_SAME_GROUP = "same_group"
STUDENT_OVERLAP_ELECTIVES_ONLY = "electives_only"

STUDENT_OVERLAP_POLICIES = [
    STUDENT_OVERLAP_NEVER,
    STUDENT_OVERLAP_SAME_GROUP,
    STUDENT_OVERLAP_ELECTIVES_ONLY,
]

STUDENT_OVERLAP_POLICY_LABEL_KEYS = {
    STUDENT_OVERLAP_NEVER: "student_overlap.never",
    STUDENT_OVERLAP_SAME_GROUP: "student_overlap.same_group",
    STUDENT_OVERLAP_ELECTIVES_ONLY: "student_overlap.electives_only",
}


def get_course_requirement_label(requirement):
    key = COURSE_REQUIREMENT_LABEL_KEYS.get(
        requirement, COURSE_REQUIREMENT_LABEL_KEYS[COURSE_REQUIREMENT_UNSPECIFIED])
    return tr(key)


def get_student_overlap_policy_label(policy):
    key = STUDENT_OVERLAP_POLICY_LABEL_KEYS.get(
        policy, STUDENT_OVERLAP_POLICY_LABEL_KEYS[STUDENT_OVERLAP_NEVER])
    return tr(key)

PROTECTION_LABELS = {
    PROTECTION_NONE: "Movable",
    PROTECTION_SOFT: "Softly Protected",
    PROTECTION_SAME_DAY: "Same Day Only",
    PROTECTION_IMPROVE_ONLY: "Improve Only",
    PROTECTION_LOCKED: "Fully Locked",
}

PROTECTION_LABEL_KEYS = {
    PROTECTION_NONE: "protection.movable",
    PROTECTION_SOFT: "protection.softly_protected",
    PROTECTION_SAME_DAY: "protection.same_day_only",
    PROTECTION_IMPROVE_ONLY: "protection.improve_only",
    PROTECTION_LOCKED: "protection.fully_locked",
}


def get_protection_label(level):
    """Return the translated display label for a protection level."""
    key = PROTECTION_LABEL_KEYS.get(level, level)
    return tr(key) if key in PROTECTION_LABEL_KEYS.values() else level


def is_immovable(cls):
    """Return True if the class should never be moved by the optimizer or drag."""
    return cls.get("pinned", False) or cls.get("protection") == PROTECTION_LOCKED


# ── Placement state helpers ─────────────────────────────────────────────

def effective_day(cls):
    """Return the active placement day (pinned or placed)."""
    return cls["pinned_day"] if cls.get("pinned") else cls.get("placed_day")


def effective_time(cls):
    """Return the active placement time slot (pinned or placed)."""
    return cls["pinned_time"] if cls.get("pinned") else cls.get("placed_time")


def effective_room(cls):
    """Return the active placement classroom (pinned or placed)."""
    return cls["pinned_classroom"] if cls.get("pinned") else cls.get("placed_classroom")


def mark_placed(cls, day, slot, room):
    """Set a class as placed at the given day/time/room."""
    cls["placed"] = True
    cls["placed_day"] = day
    cls["placed_time"] = slot
    cls["placed_classroom"] = room


def mark_unplaced(cls):
    """Clear placement state for a class."""
    cls["placed"] = False
    cls["placed_day"] = None
    cls["placed_time"] = None
    cls["placed_classroom"] = None


def is_sequential_class(cls):
    """Return True if class is non-joint with multiple targets (sequential display)."""
    return not cls.get("joint_session", True) and len(cls.get("targets", [])) > 1


def slot_offset_for_target(cls, target_idx):
    """Return the slot offset for a specific target in a sequential class."""
    if not is_sequential_class(cls):
        return 0
    return target_idx * cls["duration"]


def validate_class_fields(cls):
    """Return list of error translation keys for invalid class fields.

    Checks: name, lecturer, targets, duration, pinned fields.
    Does NOT check placement feasibility.
    """
    errors = []
    if not (cls.get("name") or "").strip():
        errors.append(tr("errors.class_name_required"))
    if not (cls.get("lecturer") or "").strip():
        errors.append(tr("errors.lecturer_required"))
    if not cls.get("targets"):
        errors.append(tr("errors.select_target_group"))
    if cls.get("duration", 0) < 1:
        errors.append(tr("errors.duration_required"))
    if cls.get("pinned"):
        if not cls.get("pinned_day") or not cls.get("pinned_time"):
            errors.append(tr("errors.pinned_needs_all"))
        elif needs_physical_room(cls) and not cls.get("pinned_classroom"):
            errors.append(tr("errors.pinned_needs_all"))
    return errors


class TargetDict(TypedDict):
    """One (year, branch) group a lesson is taught to."""
    year: str
    branch: str


class ClassDict(TypedDict, total=False):
    """The fields ``new_class()`` writes.

    ST-ARCH-013. Read this as **documentation with a test behind it**, not as a
    safety net, because it is worth being precise about what a TypedDict does
    and does not buy on a codebase shaped like this one:

    * It does **not** catch the failure the finding cites. A missing key raises
      ``KeyError`` at runtime at either totality -- mypy has no such check --
      so the ``lecturer_available_at`` crash on a malformed availability dict
      is untouched by this declaration. That dict is a *third* shape
      (``new_lecturer_availability``) the proposed remedy never mentions.
    * It is blind to ``.get()``, which is over half of all class-dict reads.
    * ``total=False`` because the domain genuinely is partial: the optimizer
      and the exporters attach their own keys to class dicts in flight, and
      ``models.cls_key`` writes ``class_uid`` on *read* for legacy data. A
      ``total=True`` declaration would be a claim the code does not honour.

    What it does buy is a single written-down answer to "what is a class?",
    which the audit found nowhere, and
    ``tests/test_domain_shapes.py::test_the_classdict_matches_new_class`` keeps
    it honest -- adding a field to ``new_class`` without adding it here turns
    the suite red. Before this, the 24-field shape existed only as a dict
    literal, and drift was invisible.
    """
    class_uid: str
    class_code: str
    name: str
    lecturer: str
    targets: list[TargetDict]
    duration: int
    participants: int
    location_type: str
    course_requirement: str
    student_overlap_group: str
    student_overlap_policy: str
    keep_same_classroom: bool
    joint_session: bool
    pinned: bool
    pinned_day: Optional[str]
    pinned_time: Optional[str]
    pinned_classroom: Optional[str]
    protection: str
    allowed_days: list[str]
    allowed_times: list[str]
    excluded_days: list[str]
    excluded_times: list[str]
    required_classrooms: list[str]
    excluded_classrooms: list[str]
    placed: bool
    placed_day: Optional[str]
    placed_time: Optional[str]
    placed_classroom: Optional[str]


class StateDict(TypedDict, total=False):
    """The 8 keys ``new_state()`` writes — the whole application state.

    Every dialog, exporter, scorer and solver is handed this one object by
    reference (ST-ARCH-007). ``total=False`` for the same reason as
    ``ClassDict``: ``_auto_load`` back-fills ``lecturers`` and
    ``classroom_capacities`` for files that predate them, so a loaded state is
    legitimately missing keys until it has been normalized.
    """
    days: list[str]
    slots: list[str]
    classrooms: list[str]
    classroom_capacities: dict[str, int]
    lecturers: list[str]
    lecturer_availability: dict[str, dict[str, list[str]]]
    years: dict[str, list[str]]
    classes: list[ClassDict]


def new_state():
    return {
        "days": [],
        "slots": [],
        "classrooms": [],
        "classroom_capacities": {},
        "lecturers": [],
        "lecturer_availability": {},
        "years": {},
        "classes": [],
    }


def new_lecturer_availability():
    """Default availability record for a lecturer (fully available)."""
    return {
        "allowed_days": [],
        "allowed_hours": [],
        "excluded_days": [],
        "excluded_hours": [],
    }


def get_lecturer_availability(state, lecturer_name):
    """Return the availability dict for a lecturer, or defaults (fully available).

    ST-ARCH-013 / ST-DATA-003. The fallback used to fire only when the lecturer
    key was *absent*. A key present with a partial record -- ``{}``, or one
    missing a single field -- was returned as-is, and ``lecturer_available_at``
    then did a bare ``avail["excluded_days"]`` and raised ``KeyError``. That is
    the exact crash the audit cites as ST-ARCH-013's motivating example, and it
    was still live.

    Neither in-app writer can produce a partial record: ``SetupDialog._ok`` and
    the Excel importer both build from ``new_lecturer_availability()``. The
    exposure is *stored* data -- a file written by an older build, hand-edited,
    or damaged -- which is precisely the case Phase 1 made every stored-data
    reader total for. Missing fields now fall back field by field, so an
    unspecified axis means "no restriction", exactly as an absent record does.
    """
    stored = state.get("lecturer_availability", {}).get(lecturer_name)
    if not isinstance(stored, dict):
        return new_lecturer_availability()
    record = new_lecturer_availability()
    for field, value in stored.items():
        if field in record:
            record[field] = value
    return record


def lecturer_available_at(state, lecturer_name, day, slot):
    """Check if a lecturer is available at the given day and time slot.

    Rules:
    - If no availability is defined, the lecturer is fully available.
    - excluded takes precedence over allowed.
    - If allowed_days is non-empty, the day must be in that list.
    - If excluded_days is non-empty, the day must NOT be in that list.
    - Same logic for hours (slots).
    """
    avail = get_lecturer_availability(state, lecturer_name)
    # Day checks — excluded takes precedence
    if avail["excluded_days"] and day in avail["excluded_days"]:
        return False
    if avail["allowed_days"] and day not in avail["allowed_days"]:
        return False
    # Hour checks — excluded takes precedence
    if avail["excluded_hours"] and slot in avail["excluded_hours"]:
        return False
    if avail["allowed_hours"] and slot not in avail["allowed_hours"]:
        return False
    return True


def get_room_capacity(state, room):
    """Return the capacity of a room, or 0 if not set (0 means unlimited)."""
    return state.get("classroom_capacities", {}).get(room, 0)


def room_fits_class(state, room, cls):
    """Return True if room has enough capacity for the class participants.

    A room capacity of 0 means unlimited. A class with 0 participants
    fits any room.  Non-physical classes always pass (they don't use rooms).
    """
    if not class_uses_physical_room(cls):
        return True
    if not room:
        return False
    cap = get_room_capacity(state, room)
    participants = cls.get("participants", 0)
    if cap == 0 or participants == 0:
        return True
    return cap >= participants


def filter_class_days(cls, all_days):
    """Return days filtered by a class's allowed/excluded day constraints.

    If cls has allowed_days, only those are considered; otherwise all_days.
    Then excluded_days are removed.

    ST-SCHED-003: the allow-list is INTERSECTED with the grid. Without that, a
    class restricted to Saturday was placed on Saturday even on a Mon-Fri
    timetable — a day that does not exist, so the lesson rendered off-grid or
    vanished, and downstream analytics/export crashed on it.

    Note the intersection is deliberately not a "drop stale values during
    normalization" as the register suggests: an EMPTY allowed_days means "no
    restriction" (see the ``or list(all_days)`` fallback), so emptying a
    now-impossible allow-list would silently turn "only Saturday" into "any
    day" and place the class on Monday looking like a success. An empty
    intersection must instead leave the class unplaced, with a reason.

    Order follows the allow-list, not the grid: candidate order feeds the
    optimizer's tie-breaking, and this preserves the existing behaviour.
    """
    allowed = cls.get("allowed_days")
    grid = list(all_days)
    if allowed:
        on_grid = set(grid)
        days = [d for d in allowed if d in on_grid]
    else:
        days = grid
    if cls.get("excluded_days"):
        excluded = set(cls["excluded_days"])
        days = [d for d in days if d not in excluded]
    return days


def filter_class_times(cls, all_times):
    """Return times filtered by a class's allowed/excluded time constraints.

    If cls has allowed_times, only those are considered; otherwise all_times.
    Then excluded_times are removed.

    ST-SCHED-004: the allow-list is INTERSECTED with the grid, for the same
    reason as :func:`filter_class_days`. A stale ``allowed_times`` value — one
    left behind after a slot was renamed or removed — used to reach
    ``slot_index`` and abort the whole reschedule with
    ``ValueError: '20:00' is not in list``.
    """
    allowed = cls.get("allowed_times")
    grid = list(all_times)
    if allowed:
        on_grid = set(grid)
        times = [t for t in allowed if t in on_grid]
    else:
        times = grid
    if cls.get("excluded_times"):
        excluded = set(cls["excluded_times"])
        times = [t for t in times if t not in excluded]
    return times


def apply_lecturer_availability_filters(state, lecturer, days, times):
    """Filter days and times by a lecturer's availability constraints.

    Returns (filtered_days, filtered_times).
    """
    if not lecturer:
        return days, times
    avail = get_lecturer_availability(state, lecturer)
    if avail["excluded_days"]:
        excluded = set(avail["excluded_days"])
        days = [d for d in days if d not in excluded]
    if avail["allowed_days"]:
        allowed = set(avail["allowed_days"])
        days = [d for d in days if d in allowed]
    if avail["excluded_hours"]:
        excluded = set(avail["excluded_hours"])
        times = [t for t in times if t not in excluded]
    if avail["allowed_hours"]:
        allowed = set(avail["allowed_hours"])
        times = [t for t in times if t in allowed]
    return days, times


def get_physical_room_candidates(state, cls, apply_capacity=True):
    """Return allowed physical-room candidates for a face-to-face class."""
    if not class_uses_physical_room(cls):
        return []
    rooms = list(state.get("classrooms", []))
    if cls.get("required_classrooms"):
        rooms = [r for r in rooms if r in cls["required_classrooms"]]
    if cls.get("excluded_classrooms"):
        rooms = [r for r in rooms if r not in cls["excluded_classrooms"]]
    if apply_capacity:
        rooms = [r for r in rooms if room_fits_class(state, r, cls)]
    return rooms


def get_room_candidates(state, cls, apply_capacity=True):
    """Return placement-room candidates for a class.

    Face-to-face classes return compatible physical rooms.
    Online and lecturer_office classes return a single sentinel candidate: None.
    """
    if class_uses_physical_room(cls):
        return get_physical_room_candidates(
            state, cls, apply_capacity=apply_capacity)
    return [None]


def new_class():
    return {
        "class_uid": str(uuid.uuid4()),
        "class_code": "",
        "name": "",
        "lecturer": "",
        "targets": [],
        "duration": 1,
        "participants": 0,
        "location_type": LOCATION_FACE_TO_FACE,
        "course_requirement": COURSE_REQUIREMENT_UNSPECIFIED,
        "student_overlap_group": "",
        "student_overlap_policy": STUDENT_OVERLAP_NEVER,
        "keep_same_classroom": False,
        "joint_session": True,
        "pinned": False,
        "pinned_day": None,
        "pinned_time": None,
        "pinned_classroom": None,
        "protection": PROTECTION_NONE,
        "allowed_days": [],
        "allowed_times": [],
        "excluded_days": [],
        "excluded_times": [],
        "required_classrooms": [],
        "excluded_classrooms": [],
        "placed": False,
        "placed_day": None,
        "placed_time": None,
        "placed_classroom": None,
    }


def cls_key(cls):
    """Return a stable unique identifier for a class dict.

    Uses the 'class_uid' field (UUID string) instead of id() so that
    identity survives serialization, copying, and list mutations.
    If the field is missing, one is auto-assigned (migration safety).
    """
    uid = cls.get("class_uid")
    if not uid:
        uid = str(uuid.uuid4())
        cls["class_uid"] = uid
    return uid


_EDITABLE_CLASS_FIELDS = (
    "class_code",
    "name",
    "lecturer",
    "targets",
    "duration",
    "participants",
    "location_type",
    "course_requirement",
    "student_overlap_group",
    "student_overlap_policy",
    "keep_same_classroom",
    "joint_session",
    "pinned",
    "pinned_day",
    "pinned_time",
    "pinned_classroom",
    "protection",
    "allowed_days",
    "allowed_times",
    "excluded_days",
    "excluded_times",
    "required_classrooms",
    "excluded_classrooms",
)


def normalize_class_location_fields(cls):
    """Normalize location-specific fields so inactive physical rooms stay inactive."""
    cls["location_type"] = location_type_of(cls)
    if not class_uses_physical_room(cls):
        cls["keep_same_classroom"] = False
        cls["required_classrooms"] = []
        cls["excluded_classrooms"] = []
        cls["pinned_classroom"] = None
        cls["placed_classroom"] = None
    return cls


def normalize_class_data(cls):
    """Backfill missing class keys and normalize location-specific fields."""
    defaults = new_class()
    for key, default in defaults.items():
        if key not in cls:
            if key == "class_uid":
                # Each class must get a unique UID — don't reuse the default
                cls[key] = str(uuid.uuid4())
            elif isinstance(default, list):
                cls[key] = list(default)
            elif isinstance(default, dict):
                cls[key] = dict(default)
            else:
                cls[key] = default
        elif cls[key] is None and default is not None:
            if key == "class_uid":
                cls[key] = str(uuid.uuid4())
            elif isinstance(default, list):
                cls[key] = list(default)
            elif isinstance(default, dict):
                cls[key] = dict(default)
            else:
                cls[key] = default
    if cls.get("course_requirement") not in COURSE_REQUIREMENTS:
        cls["course_requirement"] = COURSE_REQUIREMENT_UNSPECIFIED
    if cls.get("student_overlap_policy") not in STUDENT_OVERLAP_POLICIES:
        cls["student_overlap_policy"] = STUDENT_OVERLAP_NEVER
    cls["student_overlap_group"] = str(
        cls.get("student_overlap_group", "") or "").strip()
    if not cls["student_overlap_group"]:
        cls["student_overlap_policy"] = STUDENT_OVERLAP_NEVER
    cls["keep_same_classroom"] = bool(cls.get("keep_same_classroom", False))
    return normalize_class_location_fields(cls)


def classroom_series_key(cls):
    """Return the lecturer/course identity used by the same-room rule."""
    if not needs_physical_room(cls):
        return None
    lecturer = str(cls.get("lecturer", "") or "").strip()
    course = str(cls.get("class_code", "") or "").strip()
    if not course:
        course = str(cls.get("name", "") or "").strip()
    if not lecturer or not course:
        return None
    return fold_text(lecturer), fold_text(course)


def same_classroom_series_required(cls_a, cls_b):
    """Whether this pair must use one room across its parallel sections.

    Enabling the option on any one section activates the matching series, so a
    later B/C section follows an already configured A section automatically.
    """
    key_a = classroom_series_key(cls_a)
    return bool(
        key_a is not None
        and key_a == classroom_series_key(cls_b)
        and (cls_a.get("keep_same_classroom", False)
             or cls_b.get("keep_same_classroom", False))
    )


def find_off_grid_placements(state):
    """Return ``[(cls, reason)]`` for every placement that is not on the grid.

    A user who shortens the teaching day or renames an hour leaves the classes
    already placed there pointing at a cell that no longer exists (ST-DATA-003,
    created by ST-DATA-004). Those classes must never be silently dropped from
    an export or a view — the whole point of printing the timetable is to see
    what is on it.

    Pure: inspects, never mutates. Deliberately NOT called from
    ``normalize_state_classes`` (and so not from the .egu load path): unplacing
    orphans at load time would silently discard the user's own placements with
    no way to see or undo it, which is the same class of bug in a new place.
    Callers decide what to do — warn, list, or offer to reconcile.

    *reason* is one of ``"day"``, ``"slot"``, or ``"day+slot"``.
    """
    days = set(state.get("days", []))
    slots = set(state.get("slots", []))
    orphans = []
    for cls in state.get("classes", []):
        if not (cls.get("placed") or cls.get("pinned")):
            continue
        day = effective_day(cls)
        slot = effective_time(cls)
        bad_day = day not in days
        bad_slot = slot not in slots
        if bad_day and bad_slot:
            orphans.append((cls, "day+slot"))
        elif bad_day:
            orphans.append((cls, "day"))
        elif bad_slot:
            orphans.append((cls, "slot"))
    return orphans


def normalize_state_classes(state):
    """Normalize all classes in a state dict in-place."""
    for cls in state.get("classes", []):
        normalize_class_data(cls)
    return state


def copy_editable_class_fields(dst, src):
    """Copy user-editable class fields from src to dst and normalize them."""
    defaults = new_class()
    for field in _EDITABLE_CLASS_FIELDS:
        value = src.get(field, defaults[field])
        if field == "targets":
            dst[field] = [dict(t) for t in (value or [])]
        elif isinstance(defaults[field], list):
            dst[field] = list(value or [])
        else:
            dst[field] = value
    return normalize_class_location_fields(dst)


def split_non_joint(cls):
    """Split a non-joint multi-target class into separate per-target classes.

    If the class is joint or has 0-1 targets, returns [cls] unchanged.
    Otherwise returns N independent single-target classes (one per target),
    each with joint_session=True and duration equal to the original.
    """
    if cls.get("joint_session", True) or len(cls.get("targets", [])) <= 1:
        return [cls]
    result = []
    for target in cls["targets"]:
        c = dict(cls)  # shallow copy
        c["class_uid"] = str(uuid.uuid4())  # unique identity for split class
        branch_label = target["branch"]
        c["name"] = f"{cls['name']} [{branch_label}]"
        c["class_code"] = cls.get("class_code", "")
        c["targets"] = [target]
        c["joint_session"] = True  # single-target is effectively joint
        # Each split class gets its own placement state
        c["pinned"] = False
        c["pinned_day"] = None
        c["pinned_time"] = None
        c["pinned_classroom"] = None
        c["protection"] = cls.get("protection", PROTECTION_NONE)
        c["location_type"] = cls.get("location_type", LOCATION_FACE_TO_FACE)
        c["placed"] = False
        c["placed_day"] = None
        c["placed_time"] = None
        c["placed_classroom"] = None
        # Copy list fields to avoid shared references
        c["allowed_days"] = list(cls.get("allowed_days", []))
        c["allowed_times"] = list(cls.get("allowed_times", []))
        c["excluded_days"] = list(cls.get("excluded_days", []))
        c["excluded_times"] = list(cls.get("excluded_times", []))
        c["required_classrooms"] = list(cls.get("required_classrooms", []))
        c["excluded_classrooms"] = list(cls.get("excluded_classrooms", []))
        result.append(c)
    return result
