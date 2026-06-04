"""Data model: schedule state and class structure."""

import uuid

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


def get_location_labels():
    """Return translated location labels keyed by location type."""
    return {lt: get_location_label(lt) for lt in LOCATION_TYPES}


def get_virtual_location_labels():
    """Return translated virtual resource labels in stable display order."""
    return [get_location_label(lt) for lt in VIRTUAL_LOCATION_TYPES]


def parse_location_type_label(value):
    """Parse a raw or translated location-type label into a stable key."""
    text = str(value or "").strip()
    if not text:
        return LOCATION_FACE_TO_FACE
    lowered = text.casefold()
    if lowered in {lt.casefold(): lt for lt in LOCATION_TYPES}:
        return {lt.casefold(): lt for lt in LOCATION_TYPES}[lowered]

    aliases = {}
    for lt, label_key in LOCATION_LABEL_KEYS.items():
        aliases[LOCATION_LABELS[lt].casefold()] = lt
        for lang_dict in TRANSLATIONS.values():
            aliases[str(lang_dict.get(label_key, "")).strip().casefold()] = lt
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


def should_show_physical_classroom(cls):
    """Return True when physical classroom fields should be visible/active."""
    return class_uses_physical_room(cls)


def get_special_location_resource(cls_or_location_type):
    """Return the system-defined effective resource label for a special location."""
    if isinstance(cls_or_location_type, dict):
        lt = location_type_of(cls_or_location_type)
    else:
        lt = cls_or_location_type if cls_or_location_type in LOCATION_TYPES else LOCATION_FACE_TO_FACE
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

    used_virtual_types = set()
    for cls in classes or []:
        location_type = location_type_of(cls)
        if is_virtual_location_type(location_type):
            used_virtual_types.add(location_type)

    for location_type in VIRTUAL_LOCATION_TYPES:
        label = get_location_label(location_type)
        if location_type in used_virtual_types and label not in seen:
            labels.append(label)
            seen.add(label)
    return labels


# ── Protection levels ────────────────────────────────────────────────────
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
    """Return the availability dict for a lecturer, or defaults (fully available)."""
    return state.get("lecturer_availability", {}).get(
        lecturer_name, new_lecturer_availability())


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
    """
    days = cls.get("allowed_days") or list(all_days)
    if cls.get("excluded_days"):
        excluded = set(cls["excluded_days"])
        days = [d for d in days if d not in excluded]
    return days


def filter_class_times(cls, all_times):
    """Return times filtered by a class's allowed/excluded time constraints.

    If cls has allowed_times, only those are considered; otherwise all_times.
    Then excluded_times are removed.
    """
    times = cls.get("allowed_times") or list(all_times)
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


def ensure_class_uid(cls):
    """Assign a class_uid if the class dict doesn't already have one.

    Used for migration of legacy data that predates the class_uid field.
    """
    if not cls.get("class_uid"):
        cls["class_uid"] = str(uuid.uuid4())


_EDITABLE_CLASS_FIELDS = (
    "class_code",
    "name",
    "lecturer",
    "targets",
    "duration",
    "participants",
    "location_type",
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
    return normalize_class_location_fields(cls)


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
