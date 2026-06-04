"""Candidate placement generation.

Generates all valid (day, slot, room) tuples for a given class by
iterating the constraint-filtered search space and checking each
candidate against the ConstraintValidator. Returns candidates for
scoring — never picks a winner itself.
"""

from scheduler_app.logic import slot_index, slots_fit, total_duration
from scheduler_app.models import (
    get_room_candidates, get_physical_room_candidates,
    needs_physical_room,
    filter_class_days, filter_class_times,
    apply_lecturer_availability_filters,
)
from scheduler_app.constraint_validator import ConstraintValidator
from scheduler_app.translations import tr


class CandidateGenerator:
    """Generate all valid candidate placements for a class.

    Uses ConstraintValidator for hard-constraint filtering.
    Returns raw candidate tuples for the PlacementScorer to rank.
    """

    def __init__(self, state, validator=None, exclude_ids=None):
        self.state = state
        self.validator = validator or ConstraintValidator(
            state, exclude_ids=exclude_ids)

    def get_search_space(self, cls):
        """Return the (days, times, rooms) search space for a class,
        pre-filtered by the class's own constraints."""
        days = filter_class_days(cls, self.state["days"])
        times = filter_class_times(cls, self.state["slots"])
        rooms = get_room_candidates(self.state, cls)
        # Filter times that can't fit the duration
        td = total_duration(cls)
        times = [t for t in times
                 if slot_index(self.state, t) + td <= len(self.state["slots"])]
        # Filter by lecturer availability
        days, times = apply_lecturer_availability_filters(
            self.state, cls.get("lecturer", ""), days, times)
        return days, times, rooms

    def generate(self, cls):
        """Generate all valid (day, slot, room) candidates for *cls*.

        Returns a list of tuples that pass all hard constraints.
        """
        days, times, rooms = self.get_search_space(cls)
        candidates = []
        for day in days:
            for slot in times:
                for room in rooms:
                    if self.validator.check_placement(cls, day, slot, room):
                        candidates.append((day, slot, room))
        return candidates

    def generate_with_conflicts(self, cls, all_placed):
        """Generate candidates including those that displace flexible classes.

        Returns list of (day, slot, room, displaced_classes) where
        displaced_classes is a list of non-pinned classes that would
        need to be relocated. Conflict-free candidates have empty list.
        """
        days, times, rooms = self.get_search_space(cls)
        candidates = []
        for day in days:
            for slot in times:
                for room in rooms:
                    if self.validator.check_placement(cls, day, slot, room):
                        candidates.append((day, slot, room, []))
                    else:
                        # Only occupancy conflicts are negotiable. If the slot
                        # violates the class's own constraints (or overflows the
                        # day), skip it immediately.
                        if not self.validator.respects_constraints(cls, day, slot, room):
                            continue
                        if not slots_fit(self.state, slot, total_duration(cls)):
                            continue
                        # Find which placed classes are blocking
                        displaced = self._find_displaced(
                            cls, day, slot, room, all_placed)
                        if displaced is not None:
                            candidates.append(
                                (day, slot, room, displaced))
        # Sort: conflict-free first, then by number of displacements
        candidates.sort(key=lambda c: len(c[3]))
        return candidates

    def _find_displaced(self, cls, day, slot, room, all_placed):
        """Find which placed classes would be displaced.

        Returns None if any pinned class would be displaced (impossible).
        """
        from scheduler_app.logic import (
            occupied_slots_of, _active_targets, targets_overlap,
            classroom_of, slot_index as si_fn,
        )
        td = total_duration(cls)
        start_idx = si_fn(self.state, slot)
        needed_slots = self.state["slots"][start_idx:start_idx + td]
        displaced = []
        for existing in all_placed:
            if existing is cls:
                continue
            ex_room = classroom_of(existing)
            ex_occ = set(occupied_slots_of(self.state, existing))
            ex_start = (existing["pinned_time"] if existing["pinned"]
                        else existing["placed_time"])
            ex_start_idx = si_fn(self.state, ex_start)
            dominated = False
            for i, ns in enumerate(needed_slots):
                if (day, ns) not in ex_occ:
                    continue
                ns_idx = si_fn(self.state, ns)
                # Room conflict
                if (room is not None and ex_room == room
                        and needs_physical_room(cls)
                        and needs_physical_room(existing)):
                    dominated = True
                    break
                # Lecturer conflict
                if existing["lecturer"] == cls["lecturer"]:
                    dominated = True
                    break
                # Target overlap
                cand_targets = _active_targets(cls, i)
                ex_offset = ns_idx - ex_start_idx
                ex_targets = _active_targets(existing, ex_offset)
                if targets_overlap(ex_targets, cand_targets):
                    dominated = True
                    break
            if dominated:
                if existing["pinned"]:
                    return None  # Can't displace pinned
                if existing.get("protection") == "locked":
                    return None  # Can't displace locked
                displaced.append(existing)
        return displaced

    def has_any_valid(self, cls):
        """Quick check: does at least one valid placement exist?"""
        days, times, rooms = self.get_search_space(cls)
        for day in days:
            for slot in times:
                for room in rooms:
                    if self.validator.check_placement(cls, day, slot, room):
                        return True
        return False

    def unplaced_reason(self, cls):
        """Determine why a class can't be placed."""
        days, times, rooms = self.get_search_space(cls)
        if needs_physical_room(cls) and not rooms:
            # Distinguish capacity issues from other room constraint issues
            all_rooms = get_physical_room_candidates(
                self.state, cls, apply_capacity=False)
            if all_rooms:
                return tr("negotiation.no_room_capacity")
            return tr("negotiation.no_matching_classrooms")
        if not days:
            return tr("negotiation.no_allowed_days_configured")
        if not times:
            return tr("negotiation.no_allowed_times_configured")
        if self.has_any_valid(cls):
            return tr("negotiation.batch_displacement_required")
        return tr("negotiation.all_slots_occupied")
