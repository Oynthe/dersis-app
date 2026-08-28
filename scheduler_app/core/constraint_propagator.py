"""Incremental constraint propagation for efficient candidate evaluation.

Maintains cached valid-slot counts per class and incrementally updates
them when placements are added or removed. Replaces the expensive
_count_valid_fast() recomputation-from-scratch pattern with O(affected)
incremental updates.

Architecture:
  ConstraintState — tracks per-class cached valid placement counts and
                    per-entity slot occupancy for fast lookups.
  ConstraintPropagator — manages reversible incremental updates when
                         placements are simulated (add/remove).

All hard constraints remain enforced by ConstraintValidator — this module
only caches and incrementally maintains valid-count metadata.
"""

from scheduler_app.models import (
    needs_physical_room, get_physical_room_candidates, cls_key,
)


class ConstraintState:
    """Cached constraint metadata for incremental propagation.

    Tracks per-class valid placement counts so that lookahead scoring
    can read cached values instead of recomputing from scratch.

    The valid_counts cache maps cls_key(cls) -> int (number of valid
    placements). Counts are lazily initialized on first access and
    incrementally updated by ConstraintPropagator.
    """

    def __init__(self, state, validator, generator, classes):
        """
        Args:
            state: Schedule state dict.
            validator: ConstraintValidator with current occupancy.
            generator: CandidateGenerator for search space computation.
            classes: List of class dicts to track.
        """
        self.state = state
        self.validator = validator
        self.generator = generator
        self.classes = classes
        self._class_set = {cls_key(c) for c in classes}

        # Cache: cls_key(cls) -> valid placement count
        self._valid_counts = {}
        # Track which classes have been initialized
        self._initialized = set()

        # Reverse index: entity -> set of class ids affected
        # entity types: ('lecturer', name), ('room', name),
        #               ('group', (year, branch))
        self._entity_to_classes = {}
        self._build_entity_index()

    def _build_entity_index(self):
        """Build reverse index from entities to affected classes."""
        for cls in self.classes:
            cls_id = cls_key(cls)
            lect = cls.get("lecturer")
            if lect:
                key = ("lecturer", lect)
                self._entity_to_classes.setdefault(key, set()).add(cls_id)
            for t in cls.get("targets", []):
                key = ("group", (t["year"], t["branch"]))
                self._entity_to_classes.setdefault(key, set()).add(cls_id)
            # Room constraints: if a class requires specific rooms,
            # it competes with anything placed in those rooms
            if not needs_physical_room(cls):
                continue
            for room in get_physical_room_candidates(
                    self.state, cls, apply_capacity=False):
                key = ("room", room)
                self._entity_to_classes.setdefault(key, set()).add(cls_id)

    def get_valid_count(self, cls):
        """Get cached valid placement count, computing lazily if needed."""
        cls_id = cls_key(cls)
        if cls_id not in self._initialized:
            self._compute_valid_count(cls)
        return self._valid_counts.get(cls_id, 0)

    def _compute_valid_count(self, cls):
        """Compute and cache valid placement count from scratch."""
        cls_id = cls_key(cls)
        days, times, rooms = self.generator.get_search_space(cls)
        count = 0
        for day in days:
            for slot in times:
                for room in rooms:
                    if self.validator.check_placement(cls, day, slot, room):
                        count += 1
        self._valid_counts[cls_id] = count
        self._initialized.add(cls_id)

    def invalidate(self, cls_id):
        """Mark a class's cached count as stale (needs recomputation)."""
        self._initialized.discard(cls_id)

    def invalidate_affected_by(self, cls):
        """Invalidate caches for all classes that share entities with cls.

        Called when cls is placed or removed — any class sharing a
        lecturer, student group, or competing room may have its valid
        count changed.
        """
        affected = set()
        lect = cls.get("lecturer")
        if lect:
            affected |= self._entity_to_classes.get(
                ("lecturer", lect), set())
        for t in cls.get("targets", []):
            affected |= self._entity_to_classes.get(
                ("group", (t["year"], t["branch"])), set())

        if needs_physical_room(cls):
            for room in get_physical_room_candidates(
                    self.state, cls, apply_capacity=False):
                affected |= self._entity_to_classes.get(
                    ("room", room), set())

        for cls_id in affected:
            self.invalidate(cls_id)

    def bulk_initialize(self, class_subset=None):
        """Pre-compute valid counts for a subset of classes.

        Args:
            class_subset: Optional list of classes. If None, initializes all.
        """
        targets = class_subset if class_subset is not None else self.classes
        for cls in targets:
            if cls_key(cls) not in self._initialized:
                self._compute_valid_count(cls)

    def reset(self):
        """Clear all cached counts (forces full recomputation)."""
        self._valid_counts.clear()
        self._initialized.clear()


class ConstraintPropagator:
    """Manages reversible incremental constraint state updates.

    Wraps ConstraintState to provide add/remove operations that
    incrementally update cached valid counts for affected classes,
    avoiding full recomputation.

    Supports temporary (simulated) placements for lookahead scoring
    via simulate_placement / revert_simulation context.
    """

    def __init__(self, constraint_state):
        """
        Args:
            constraint_state: ConstraintState instance to manage.
        """
        self.cs = constraint_state
        self._simulation_stack = []

    def add_placement(self, cls, day, slot, room):
        """Register a placement and update affected valid counts.

        Also updates the underlying validator occupancy.
        """
        self.cs.validator.add_placement(cls, day, slot, room)
        self.cs.invalidate_affected_by(cls)

    def remove_placement(self, cls, day, slot, room):
        """Remove a placement and update affected valid counts.

        Also updates the underlying validator occupancy.
        """
        self.cs.validator.remove_placement(cls, day, slot, room)
        self.cs.invalidate_affected_by(cls)

    def simulate_placement(self, cls, day, slot, room):
        """Temporarily place a class for lookahead evaluation.

        Pushes onto simulation stack for later revert.
        Returns the set of invalidated class ids.
        """
        self._simulation_stack.append((cls, day, slot, room))
        self.cs.validator.add_placement(cls, day, slot, room)
        self.cs.invalidate_affected_by(cls)

    def revert_simulation(self):
        """Revert the most recent simulated placement.

        Pops from simulation stack and restores state.
        """
        if not self._simulation_stack:
            return
        cls, day, slot, room = self._simulation_stack.pop()
        self.cs.validator.remove_placement(cls, day, slot, room)
        self.cs.invalidate_affected_by(cls)

    def revert_all_simulations(self):
        """Revert all simulated placements (LIFO order)."""
        while self._simulation_stack:
            self.revert_simulation()

    def get_valid_count(self, cls):
        """Get valid placement count for a class (cached/incremental)."""
        return self.cs.get_valid_count(cls)

    def get_valid_count_fast(self, cls, cap=20):
        """Get valid count with early exit cap.

        For lookahead, we only care whether options are scarce.
        Returns min(actual_count, cap+1).
        """
        cid = cls_key(cls)
        if cid in self.cs._initialized:
            return self.cs._valid_counts.get(cid, 0)

        # Compute with early exit
        days, times, rooms = self.cs.generator.get_search_space(cls)
        count = 0
        for day in days:
            for slot in times:
                for room in rooms:
                    if self.cs.validator.check_placement(cls, day, slot, room):
                        count += 1
                        if count > cap:
                            self.cs._valid_counts[cid] = count
                            self.cs._initialized.add(cid)
                            return count
        self.cs._valid_counts[cid] = count
        self.cs._initialized.add(cid)
        return count
