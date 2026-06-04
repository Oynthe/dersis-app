"""Hard constraint validation — deterministic and authoritative.

Wraps all hard-constraint checks (lecturer conflicts, classroom conflicts,
branch conflicts, pinned classes, allowed/excluded days & times,
required/excluded classrooms, duration rules, joint vs non-joint logic)
into a single ConstraintValidator class with pre-built occupancy maps
for O(1) conflict lookups.
"""

from scheduler_app.logic import (
    slot_index, slots_fit, total_duration, get_consecutive_slots,
    get_placed_classes, classroom_of, _active_targets, targets_overlap,
    build_occupancy,
)
from scheduler_app.translations import tr
from scheduler_app.models import (
    room_fits_class, lecturer_available_at, needs_physical_room,
    get_room_candidates, effective_day, effective_time,
    filter_class_days, filter_class_times,
    apply_lecturer_availability_filters,
)
from scheduler_app.ui.day_keys import display_day


class ConstraintValidator:
    """Deterministic hard-constraint checker for class scheduling.

    Maintains occupancy maps for fast conflict detection.
    All methods return deterministic pass/fail — no scoring or ranking.
    """

    def __init__(self, state, exclude_ids=None):
        self.state = state
        self.exclude_ids = exclude_ids or set()
        self.room_occ = {}    # (day, slot) -> set of room names
        self.lect_occ = {}    # (day, slot) -> set of lecturer names
        self.group_occ = {}   # (day, slot) -> set of (year, branch) tuples
        self._build_occupancy()

    def _build_occupancy(self):
        """Build occupancy maps from currently placed/pinned classes."""
        self.room_occ, self.lect_occ, self.group_occ = build_occupancy(
            self.state, self.exclude_ids)

    def respects_constraints(self, cls, day, slot, room):
        """Check if (day, slot, room) satisfies the class's own constraints."""
        if cls["allowed_days"] and day not in cls["allowed_days"]:
            return False
        if cls.get("excluded_days") and day in cls["excluded_days"]:
            return False
        if cls["allowed_times"] and slot not in cls["allowed_times"]:
            return False
        if cls.get("excluded_times") and slot in cls["excluded_times"]:
            return False
        # Classroom constraints only for face-to-face
        if needs_physical_room(cls):
            if cls["required_classrooms"] and room not in cls["required_classrooms"]:
                return False
            if cls["excluded_classrooms"] and room in cls["excluded_classrooms"]:
                return False
            if not room_fits_class(self.state, room, cls):
                return False
        # Lecturer availability — check ALL slots for multi-duration classes
        lecturer = cls.get("lecturer", "")
        if lecturer:
            td = total_duration(cls)
            si = slot_index(self.state, slot)
            slots_list = self.state["slots"]
            for d in range(td):
                idx = si + d
                if idx >= len(slots_list):
                    return False
                if not lecturer_available_at(self.state, lecturer, day, slots_list[idx]):
                    return False
        return True

    def check_placement(self, cls, day, start_slot, room):
        """Fast conflict check using occupancy maps.

        Returns True if the placement is valid (no hard constraint violated).
        """
        td = total_duration(cls)
        si = slot_index(self.state, start_slot)
        if si + td > len(self.state["slots"]):
            return False
        if not self.respects_constraints(cls, day, start_slot, room):
            return False
        check_room = needs_physical_room(cls) and room is not None
        slots_list = self.state["slots"][si:si + td]
        for off, s in enumerate(slots_list):
            key = (day, s)
            if check_room and room in self.room_occ.get(key, set()):
                return False
            if cls["lecturer"] in self.lect_occ.get(key, set()):
                return False
            for t in _active_targets(cls, off):
                if (t["year"], t["branch"]) in self.group_occ.get(key, set()):
                    return False
        return True

    def check_placement_explained(self, cls, day, start_slot, room):
        """Check placement validity and return (valid, reasons).

        Returns:
            (True, []) if placement is valid.
            (False, [reason_strings]) if constraints are violated.
        """
        reasons = []
        td = total_duration(cls)
        si = slot_index(self.state, start_slot)
        display_day_value = display_day(day)
        allowed_days = ", ".join(display_day(d) for d in cls.get("allowed_days", []))
        excluded_days = ", ".join(display_day(d) for d in cls.get("excluded_days", []))
        if si + td > len(self.state["slots"]):
            reasons.append(tr("validation.duration_overflow"))
            return False, reasons

        # Own constraints
        if cls["allowed_days"] and day not in cls["allowed_days"]:
            reasons.append(tr("validation.day_not_allowed").format(display_day_value, allowed_days))
        if cls.get("excluded_days") and day in cls["excluded_days"]:
            reasons.append(tr("validation.day_excluded").format(display_day_value))
        if cls["allowed_times"] and start_slot not in cls["allowed_times"]:
            reasons.append(tr("validation.time_not_allowed").format(start_slot))
        if cls.get("excluded_times") and start_slot in cls["excluded_times"]:
            reasons.append(tr("validation.time_excluded").format(start_slot))
        if needs_physical_room(cls):
            if cls["required_classrooms"] and room not in cls["required_classrooms"]:
                reasons.append(tr("validation.room_not_required").format(room, cls['required_classrooms']))
            if cls["excluded_classrooms"] and room in cls["excluded_classrooms"]:
                reasons.append(tr("validation.room_excluded").format(room))
            if not room_fits_class(self.state, room, cls):
                from scheduler_app.models import get_room_capacity
                cap = get_room_capacity(self.state, room)
                reasons.append(
                    tr("validation.room_capacity").format(
                        room, cap, cls.get('participants', 0)))

        # Lecturer availability — check ALL slots for multi-duration classes
        lecturer = cls.get("lecturer", "")
        if lecturer:
            slots_list = self.state["slots"]
            for d in range(td):
                s = slots_list[si + d]
                if not lecturer_available_at(self.state, lecturer, day, s):
                    reasons.append(
                        tr("validation.lecturer_unavailable").format(
                            lecturer, display_day_value, s))

        if reasons:
            return False, reasons

        # Temporarily remove class's current placement from occupancy maps
        # to avoid self-conflicts when re-validating an already-placed class.
        cur_day = effective_day(cls)
        cur_time = effective_time(cls)
        already_placed = cur_day is not None and cur_time is not None
        if already_placed:
            cur_room = cls.get("pinned_classroom") if cls.get("pinned") else cls.get("placed_classroom")
            self.remove_placement(cls, cur_day, cur_time, cur_room)

        # Occupancy conflicts
        check_room = needs_physical_room(cls) and room is not None
        slots_list = self.state["slots"][si:si + td]
        for off, s in enumerate(slots_list):
            key = (day, s)
            if check_room and room in self.room_occ.get(key, set()):
                reasons.append(tr("validation.room_occupied").format(room, display_day_value, s))
            if cls["lecturer"] in self.lect_occ.get(key, set()):
                reasons.append(
                    tr("validation.lecturer_busy").format(
                        cls['lecturer'], display_day_value, s))
            for t in _active_targets(cls, off):
                if (t["year"], t["branch"]) in self.group_occ.get(key, set()):
                    reasons.append(
                        tr("validation.group_busy").format(
                            t['year'], t['branch'], display_day_value, s))

        # Restore the class's placement in occupancy maps
        if already_placed:
            self.add_placement(cls, cur_day, cur_time, cur_room)

        return len(reasons) == 0, reasons

    def find_conflicts(self, cls, day, start_slot, room):
        """Return a list of human-readable conflict descriptions."""
        conflicts = []
        td = total_duration(cls)
        si = slot_index(self.state, start_slot)
        display_day_value = display_day(day)
        if si + td > len(self.state["slots"]):
            conflicts.append(tr("validation.duration_overflow"))
            return conflicts
        if not self.respects_constraints(cls, day, start_slot, room):
            if cls["allowed_days"] and day not in cls["allowed_days"]:
                conflicts.append(tr("validation.day_not_allowed_simple").format(display_day_value))
            if cls.get("excluded_days") and day in cls["excluded_days"]:
                conflicts.append(tr("validation.day_excluded_simple").format(display_day_value))
            if cls["allowed_times"] and start_slot not in cls["allowed_times"]:
                conflicts.append(tr("validation.time_not_allowed").format(start_slot))
            if cls.get("excluded_times") and start_slot in cls["excluded_times"]:
                conflicts.append(tr("validation.time_excluded_simple").format(start_slot))
            if needs_physical_room(cls):
                if cls["required_classrooms"] and room not in cls["required_classrooms"]:
                    conflicts.append(tr("validation.room_not_required_simple").format(room))
                if cls["excluded_classrooms"] and room in cls["excluded_classrooms"]:
                    conflicts.append(tr("validation.room_excluded_simple").format(room))
                if not room_fits_class(self.state, room, cls):
                    from scheduler_app.models import get_room_capacity
                    cap = get_room_capacity(self.state, room)
                    conflicts.append(
                        tr("validation.room_capacity").format(
                            room, cap, cls.get('participants', 0)))
        # Lecturer availability
        lecturer = cls.get("lecturer", "")
        if lecturer and not lecturer_available_at(self.state, lecturer, day, start_slot):
            conflicts.append(
                tr("validation.lecturer_unavailable").format(
                    lecturer, display_day_value, start_slot))
        check_room = needs_physical_room(cls) and room is not None
        slots_list = self.state["slots"][si:si + td]
        for off, s in enumerate(slots_list):
            key = (day, s)
            if check_room and room in self.room_occ.get(key, set()):
                conflicts.append(tr("validation.room_occupied").format(room, display_day_value, s))
            if cls["lecturer"] in self.lect_occ.get(key, set()):
                conflicts.append(
                    tr("validation.lecturer_busy").format(
                        cls['lecturer'], display_day_value, s))
            for t in _active_targets(cls, off):
                if (t["year"], t["branch"]) in self.group_occ.get(key, set()):
                    conflicts.append(
                        tr("validation.group_busy").format(
                            t['year'], t['branch'], display_day_value, s))
        return conflicts

    def add_placement(self, cls, day, start_slot, room):
        """Register a placement in the occupancy maps."""
        td = total_duration(cls)
        si = slot_index(self.state, start_slot)
        slots_list = self.state["slots"][si:si + td]
        track_room = needs_physical_room(cls) and room is not None
        for off, s in enumerate(slots_list):
            key = (day, s)
            if track_room:
                self.room_occ.setdefault(key, set()).add(room)
            self.lect_occ.setdefault(key, set()).add(cls["lecturer"])
            for t in _active_targets(cls, off):
                self.group_occ.setdefault(key, set()).add(
                    (t["year"], t["branch"]))

    def remove_placement(self, cls, day, start_slot, room):
        """Remove a placement from the occupancy maps."""
        td = total_duration(cls)
        si = slot_index(self.state, start_slot)
        slots_list = self.state["slots"][si:si + td]
        track_room = needs_physical_room(cls) and room is not None
        for off, s in enumerate(slots_list):
            key = (day, s)
            if track_room:
                self.room_occ.get(key, set()).discard(room)
            self.lect_occ.get(key, set()).discard(cls["lecturer"])
            for t in _active_targets(cls, off):
                self.group_occ.get(key, set()).discard(
                    (t["year"], t["branch"]))

    def _get_constrained_search_space(self, cls):
        """Return (days, times, rooms) filtered by class + lecturer constraints.

        Single source of truth for constraint-filtered search space within
        the validator. Used by constraint_tightness() and scheduling_difficulty().
        """
        days = filter_class_days(cls, self.state["days"])
        times = filter_class_times(cls, self.state["slots"])
        days, times = apply_lecturer_availability_filters(
            self.state, cls.get("lecturer", ""), days, times)
        rooms = get_room_candidates(self.state, cls)
        return days, times, rooms

    def constraint_tightness(self, cls):
        """Estimate how constrained a class is (lower = tighter)."""
        days, times, rooms = self._get_constrained_search_space(cls)
        td = total_duration(cls)
        valid_time_count = sum(
            1 for s in times
            if slot_index(self.state, s) + td <= len(self.state["slots"]))
        return len(days) * valid_time_count * len(rooms)

    def scheduling_difficulty(self, cls):
        """Compute a comprehensive difficulty score for ordering.

        Combines multiple factors into a single score where
        *lower* = harder to place (should be scheduled first).
        Factors:
          - Number of valid candidate slots (most important)
          - Constraint density (how many constraint types are active)
          - Duration (longer classes are harder)
          - Number of targets (more targets = more conflicts)
        """
        # Count actual valid placements against current occupancy
        valid_count = 0
        days, times, rooms = self._get_constrained_search_space(cls)
        td = total_duration(cls)
        for day in days:
            for slot in times:
                si = slot_index(self.state, slot)
                if si + td > len(self.state["slots"]):
                    continue
                for room in rooms:
                    if self.check_placement(cls, day, slot, room):
                        valid_count += 1

        # Constraint density: count active constraint types
        density = 0
        if cls.get("allowed_days"):
            density += 1
        if cls.get("excluded_days"):
            density += 1
        if cls.get("allowed_times"):
            density += 1
        if cls.get("excluded_times"):
            density += 1
        if cls.get("required_classrooms"):
            density += 1
        if cls.get("excluded_classrooms"):
            density += 1

        # Duration factor: longer classes are harder
        duration_factor = td

        # Target factor: more targets = more potential conflicts
        target_count = len(cls.get("targets", []))

        # Combine: primary sort by valid_count, break ties with density/duration
        # Lower score = harder = schedule first
        return (valid_count * 1000
                - density * 50
                - duration_factor * 30
                - target_count * 20)

    def sort_by_difficulty(self, classes):
        """Sort classes so hardest-to-place come first.

        Returns a new list sorted by scheduling difficulty (ascending).
        """
        return sorted(classes, key=lambda c: self.scheduling_difficulty(c))
