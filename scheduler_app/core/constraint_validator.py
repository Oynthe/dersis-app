"""Hard constraint validation — deterministic and authoritative.

Wraps all hard-constraint checks (lecturer conflicts, classroom conflicts,
branch conflicts, pinned classes, allowed/excluded days & times,
required/excluded classrooms, duration rules, joint vs non-joint logic)
into a single ConstraintValidator class with pre-built occupancy maps
for O(1) conflict lookups.
"""

from scheduler_app.logic import (
    find_slot_index, slot_index, total_duration, _active_targets,
    build_occupancy, occ_claim, occ_release, student_targets_conflict,
)
from scheduler_app.translations import tr
from scheduler_app.models import (
    cls_key,
    room_fits_class, lecturer_available_at, needs_physical_room,
    get_room_candidates, effective_day, effective_time,
    filter_class_days, filter_class_times,
    apply_lecturer_availability_filters,
    classroom_series_key, same_classroom_series_required,
)
from scheduler_app.i18n.day_keys import display_day


class ConstraintValidator:
    """Deterministic hard-constraint checker for class scheduling.

    Maintains occupancy maps for fast conflict detection.
    All methods return deterministic pass/fail — no scoring or ranking.
    """

    def __init__(self, state, exclude_ids=None):
        self.state = state
        self.exclude_ids = exclude_ids or set()
        # (day, slot) -> {entity: refcount}. Ref-counted rather than a set so
        # that removing one of two classes claiming the same cell does not
        # erase the other's claim (ST-SCHED-010). Reads are unaffected: every
        # consumer does `entity in occ.get(key, set())`, `set(cell)` or
        # `bool(cell)`, which behave identically on a dict.
        self.room_occ = {}    # (day, slot) -> {room name: refcount}
        self.lect_occ = {}    # (day, slot) -> {lecturer name: refcount}
        self.group_occ = {}   # (day, slot) -> {(year, branch): refcount}
        self.group_class_occ = {}  # cell -> target -> class-id -> class/count
        # lecturer/course series -> class-id -> {class, room}.  This spans
        # different times: its purpose is to stop room-hopping between parallel
        # sections, not to detect simultaneous room use.
        self.room_series_occ = {}
        self._build_occupancy()

    def _build_occupancy(self):
        """Build occupancy maps from currently placed/pinned classes."""
        (self.room_occ, self.lect_occ, self.group_occ,
         self.group_class_occ) = build_occupancy(
             self.state, self.exclude_ids)
        self.room_series_occ = {}
        for existing in self.state.get("classes", []):
            eid = cls_key(existing)
            if eid in self.exclude_ids:
                continue
            if not (existing.get("placed") or existing.get("pinned")):
                continue
            key = classroom_series_key(existing)
            room = (existing.get("pinned_classroom") if existing.get("pinned")
                    else existing.get("placed_classroom"))
            if key is not None and room:
                self.room_series_occ.setdefault(key, {})[eid] = {
                    "class": existing, "room": room,
                }

    def room_series_mismatches(self, cls, room):
        """Return rooms used by linked sections that disagree with *room*."""
        key = classroom_series_key(cls)
        if key is None or not room:
            return []
        cid = cls_key(cls)
        mismatches = []
        for other_id, record in self.room_series_occ.get(key, {}).items():
            if other_id == cid:
                continue
            if (same_classroom_series_required(cls, record["class"])
                    and record["room"] != room):
                mismatches.append(record["room"])
        return sorted(set(mismatches))

    def group_target_blocked(self, cls, key, target):
        """Whether *target* at *key* is held by a conflicting class.

        ``group_occ`` intentionally remains aggregate for the scorer.  Hard
        validation uses this identity-preserving companion index so two
        classes in one explicitly configured overlap group can share a cell
        without also becoming invisible to ordinary compulsory lessons.
        """
        target_key = (target["year"], target["branch"])
        class_index = getattr(self, "group_class_occ", None)
        if class_index is None:
            # Compatibility for hand-built validators in older tests/plugins.
            return target_key in self.group_occ.get(key, set())
        occupants = class_index.get(key, {}).get(target_key, {})
        candidate_id = cls_key(cls)
        for occupant_id, record in occupants.items():
            if occupant_id == candidate_id:
                continue
            existing = record["class"]
            if student_targets_conflict(cls, [target], existing, [target]):
                return True
        return False

    def _claim_group_class(self, key, target, cls):
        target_key = (target["year"], target["branch"])
        by_class = self.group_class_occ.setdefault(key, {}).setdefault(
            target_key, {})
        cid = cls_key(cls)
        record = by_class.get(cid)
        if record is None:
            by_class[cid] = {"class": cls, "count": 1}
        else:
            record["count"] += 1

    def _release_group_class(self, key, target, cls):
        target_key = (target["year"], target["branch"])
        by_target = self.group_class_occ.get(key)
        if not by_target:
            return
        by_class = by_target.get(target_key)
        if not by_class:
            return
        cid = cls_key(cls)
        record = by_class.get(cid)
        if record is None:
            return
        if record["count"] <= 1:
            del by_class[cid]
            if not by_class:
                del by_target[target_key]
            if not by_target:
                del self.group_class_occ[key]
        else:
            record["count"] -= 1

    def respects_constraints(self, cls, day, slot, room):
        """Check if (day, slot, room) satisfies the class's own constraints."""
        # ST-SCHED-003/004: a placement can only be valid on a cell that
        # exists. `respects_constraints` reads cls["allowed_days"] directly and
        # never goes through filter_class_days, so intersecting the allow-lists
        # with the grid is NOT enough to stop the validator blessing a ghost
        # day — this guard is what does it.
        if day not in self.state["days"] or slot not in self.state["slots"]:
            return False
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
            if self.room_series_mismatches(cls, room):
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
        if day not in self.state["days"]:
            return False
        si = find_slot_index(self.state, start_slot)
        if si is None or si + td > len(self.state["slots"]):
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
                if self.group_target_blocked(cls, key, t):
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
        display_day_value = display_day(day)
        if day not in self.state["days"]:
            reasons.append(tr("validation.day_not_in_grid").format(
                display_day_value))
            return False, reasons
        si = find_slot_index(self.state, start_slot)
        if si is None:
            reasons.append(tr("validation.slot_not_in_grid").format(start_slot))
            return False, reasons
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
            mismatches = self.room_series_mismatches(cls, room)
            if mismatches:
                reasons.append(tr("validation.same_classroom_series").format(
                    room=room, expected=", ".join(mismatches)))

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
        #
        # Only when it is actually IN those maps. A class in `exclude_ids` was
        # deliberately left out of `build_occupancy`, so the remove below finds
        # nothing to release — but the restore in the `finally` would then add a
        # claim that never existed, permanently marking a free cell occupied for
        # every later check. Screening a proposed schedule (`screen_placements`)
        # excludes every class it is about to test, so this path is ordinary.
        cur_day = effective_day(cls)
        cur_time = effective_time(cls)
        already_placed = (cur_day is not None and cur_time is not None
                          and cls_key(cls) not in self.exclude_ids)
        if already_placed:
            cur_room = cls.get("pinned_classroom") if cls.get("pinned") else cls.get("placed_classroom")
            self.remove_placement(cls, cur_day, cur_time, cur_room)

        # ST-DATA-011: the class's own placement was lifted out of the occupancy
        # maps above so it cannot conflict with itself. If anything below raises,
        # putting it back is not optional — the validator would otherwise go on
        # believing the cell is free and bless a real double-booking.
        try:
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
                    if self.group_target_blocked(cls, key, t):
                        reasons.append(
                            tr("validation.group_busy").format(
                                t['year'], t['branch'], display_day_value, s))
        finally:
            # Restore the class's placement in occupancy maps
            if already_placed:
                self.add_placement(cls, cur_day, cur_time, cur_room)

        return len(reasons) == 0, reasons

    def find_conflicts(self, cls, day, start_slot, room):
        """Return a list of human-readable conflict descriptions.

        ST-SCHED-009: this list is guaranteed non-empty whenever
        ``check_placement`` rejects the same placement. It used to be possible
        for the two to disagree — a duration-2 class whose lecturer was free at
        09:00 but not at 10:00 was rejected by ``check_placement`` while
        ``find_conflicts`` returned ``[]``, because it only tested availability
        at the *start* slot. The drag-and-drop UI then refused the drop and had
        nothing to tell the user about why.
        """
        conflicts = []
        td = total_duration(cls)
        display_day_value = display_day(day)
        if day not in self.state["days"]:
            conflicts.append(tr("validation.day_not_in_grid").format(
                display_day_value))
            return conflicts
        si = find_slot_index(self.state, start_slot)
        if si is None:
            conflicts.append(tr("validation.slot_not_in_grid").format(start_slot))
            return conflicts
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
                mismatches = self.room_series_mismatches(cls, room)
                if mismatches:
                    conflicts.append(
                        tr("validation.same_classroom_series").format(
                            room=room, expected=", ".join(mismatches)))
        check_room = needs_physical_room(cls) and room is not None
        slots_list = self.state["slots"][si:si + td]
        lecturer = cls.get("lecturer", "")
        for off, s in enumerate(slots_list):
            key = (day, s)
            # Availability over the WHOLE block, not just the start slot —
            # this is the ST-SCHED-009 gap, and it matches what
            # respects_constraints() has always enforced.
            if lecturer and not lecturer_available_at(self.state, lecturer, day, s):
                conflicts.append(
                    tr("validation.lecturer_unavailable").format(
                        lecturer, display_day_value, s))
            if check_room and room in self.room_occ.get(key, set()):
                conflicts.append(tr("validation.room_occupied").format(room, display_day_value, s))
            if cls["lecturer"] in self.lect_occ.get(key, set()):
                conflicts.append(
                    tr("validation.lecturer_busy").format(
                        cls['lecturer'], display_day_value, s))
            for t in _active_targets(cls, off):
                if self.group_target_blocked(cls, key, t):
                    conflicts.append(
                        tr("validation.group_busy").format(
                            t['year'], t['branch'], display_day_value, s))

        # Backstop. Everything above enumerates a *known* reason; if some future
        # rule makes check_placement stricter than this enumeration, an empty
        # list would silently reopen ST-SCHED-009 — a rejection the UI cannot
        # explain. Better a generic sentence than no sentence.
        if not conflicts and not self.check_placement(cls, day, start_slot, room):
            conflicts.append(tr("validation.placement_invalid"))
        return conflicts

    def add_placement(self, cls, day, start_slot, room):
        """Register a placement in the occupancy maps.

        ST-SCHED-010: claims are ref-counted, so registering the same cell
        twice takes two removals to free it. See ``logic.occ_claim``.
        """
        td = total_duration(cls)
        series_key = classroom_series_key(cls)
        if series_key is not None and room:
            self.room_series_occ.setdefault(series_key, {})[cls_key(cls)] = {
                "class": cls, "room": room,
            }
        si = find_slot_index(self.state, start_slot)
        if si is None or day not in self.state["days"]:
            return  # orphaned placement — occupies no cell on this grid
        slots_list = self.state["slots"][si:si + td]
        track_room = needs_physical_room(cls) and room is not None
        for off, s in enumerate(slots_list):
            key = (day, s)
            if track_room:
                occ_claim(self.room_occ, key, room)
            occ_claim(self.lect_occ, key, cls["lecturer"])
            for t in _active_targets(cls, off):
                occ_claim(self.group_occ, key, (t["year"], t["branch"]))
                self._claim_group_class(key, t, cls)

    def remove_placement(self, cls, day, start_slot, room):
        """Remove a placement from the occupancy maps.

        Releases one ref-count per cell; a cell stays occupied while another
        class still claims it (ST-SCHED-010).
        """
        td = total_duration(cls)
        series_key = classroom_series_key(cls)
        if series_key is not None:
            by_class = self.room_series_occ.get(series_key)
            if by_class is not None:
                by_class.pop(cls_key(cls), None)
                if not by_class:
                    self.room_series_occ.pop(series_key, None)
        si = find_slot_index(self.state, start_slot)
        if si is None or day not in self.state["days"]:
            return  # orphaned placement — occupies no cell on this grid
        slots_list = self.state["slots"][si:si + td]
        track_room = needs_physical_room(cls) and room is not None
        for off, s in enumerate(slots_list):
            key = (day, s)
            if track_room:
                occ_release(self.room_occ, key, room)
            occ_release(self.lect_occ, key, cls["lecturer"])
            for t in _active_targets(cls, off):
                occ_release(self.group_occ, key, (t["year"], t["branch"]))
                self._release_group_class(key, t, cls)

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


# ══════════════════════════════════════════════════════════════════════════
#  THE COMMIT RULE  (ST-ARCH-004, ST-SCHED-001, ST-SCHED-002)
# ══════════════════════════════════════════════════════════════════════════

def screen_placements(state, placements, immovable_ids=None):
    """Decide which of *placements* may be committed **together**.

    Validating placements one at a time answers the wrong question: each may be
    individually legal while the set of them double-books a room. This walks the
    proposal in precedence order, registering each accepted placement in the
    occupancy maps so the next one is judged against it.

    Precedence, highest first:

    1. **Pinned.** Screened at their pin position, never dropped. A pin is an
       instruction the user typed, so an infeasible one is *reported* and still
       registered — clearing it would destroy their intent, and pretending the
       cell is free would steer flexible classes straight into it (ST-SCHED-002).
    2. **Immovable** (``protection="locked"``, plus anything the caller names).
       Same treatment: reported, never dropped. Unplacing a locked class is
       itself a violation of the guarantee its protection level makes.
    3. **Everything else**, in the order given. A placement that cannot stand
       alongside what has already been accepted is rejected.

    Both the optimizer's self-check and ``SchedulingWorkflow.apply_reschedule``
    go through here, so "which schedules are legal" has exactly one answer
    (ST-ARCH-004). This function is pure — it never touches the class dicts.

    Parameters
    ----------
    state : dict
    placements : iterable of ``(cls, day, slot, room)``
    immovable_ids : set of ``cls_key`` values to treat as rank 2.

    Returns
    -------
    (accepted, rejected)
        ``accepted`` -- ``[(cls, day, slot, room)]``, room normalised to None
        for classes that need no physical room.
        ``rejected`` -- ``[(cls, day, slot, room, reasons)]``. ``reasons`` is
        never empty (ST-SCHED-009). A *reported* pin or locked class appears in
        ``rejected`` **and** in ``accepted``: the caller must commit it, and
        must also tell the user it clashes.
    """
    immovable_ids = immovable_ids or set()
    entries = [(c, d, s, r) for c, d, s, r in placements]
    placed_keys = {cls_key(c) for c, _, _, _ in entries}
    validator = ConstraintValidator(state, exclude_ids=placed_keys)

    def rank(cls):
        if cls.get("pinned"):
            return 0
        if cls_key(cls) in immovable_ids:
            return 1
        return 2

    accepted = []
    rejected = []
    # Stable: `sorted` keeps the caller's order within each rank, so a
    # deterministic proposal screens deterministically.
    for cls, day, slot, room in sorted(entries, key=lambda e: rank(e[0])):
        if cls.get("pinned"):
            # Read the pin off the class, not off the proposal: the pin is the
            # authority on where a pinned class goes.
            day = cls.get("pinned_day")
            slot = cls.get("pinned_time")
            room = cls.get("pinned_classroom")
        room = room if needs_physical_room(cls) else None
        if validator.check_placement(cls, day, slot, room):
            validator.add_placement(cls, day, slot, room)
            accepted.append((cls, day, slot, room))
            continue

        reasons = validator.find_conflicts(cls, day, slot, room)
        if rank(cls) < 2:
            # Reported, not dropped — and still registered, so nothing else is
            # steered into the cell it occupies. add_placement is a no-op for an
            # off-grid day/slot, which is exactly right: such a placement
            # occupies no real cell and blocks nothing.
            validator.add_placement(cls, day, slot, room)
            accepted.append((cls, day, slot, room))
        rejected.append((cls, day, slot, room, reasons))

    return accepted, rejected
