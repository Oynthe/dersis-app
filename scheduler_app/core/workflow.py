"""SchedulingWorkflow: UI-free business logic for class scheduling operations.

Extracts placement, scheduling, editing, rescheduling, and drop-validation
logic from the UI layer so that it can be tested and reused independently.
All methods operate on plain state dicts and return result objects — no Qt
imports, no dialog references, no widget manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from scheduler_app.models import (
    DEFAULT_OPTIMIZER_SEED,
    split_non_joint, needs_physical_room, room_fits_class,
    copy_editable_class_fields,
    mark_placed, mark_unplaced,
    cls_key,
)
from scheduler_app.logic import (
    find_slot_index,
    slots_fit, total_duration, find_conflicts,
    respects_constraints, find_valid_options,
    optimized_auto_place, optimized_batch_schedule,
    optimized_reschedule_all,
    score_placement, score_placement_explained,
    analyze_schedule, negotiate_after_optimization,
    get_placed_classes,
)


# ── Result dataclasses ───────────────────────────────────────────────────────

@dataclass
class AutoPlaceResult:
    """Result of auto-placing a single class."""
    success: bool
    relocated: list = field(default_factory=list)
    placed_info: Optional[tuple] = None       # (day, slot, room) or None
    explanation: Optional[dict] = None        # placement explanation dict
    score: float = 0.0


@dataclass
class ScheduleNewResult:
    """Result of scheduling one or more new classes."""
    placed: list = field(default_factory=list)        # [(cls, day, slot, room), ...]
    unplaced: list = field(default_factory=list)       # [(cls, reason), ...]
    rescheduled: bool = False
    single_success: bool = False   # single class placed without dialog needed
    single_failed: bool = False    # single class could not be placed
    negotiation_report: Optional[dict] = None  # constraint negotiation report


@dataclass
class PlaceBatchResult:
    """Result of batch-placing multiple classes."""
    placed_count: int = 0
    unresolved_count: int = 0
    rescheduled: bool = False
    placed: list = field(default_factory=list)
    unplaced: list = field(default_factory=list)


@dataclass
class RescheduleResult:
    """Result of a full reschedule operation."""
    placed: list = field(default_factory=list)
    unplaced: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    summary: Optional[dict] = None
    analytics: Optional[dict] = None
    explanation: Optional[dict] = None
    negotiation_result: Optional[dict] = None


@dataclass
class DropValidation:
    """Result of validating a drop at (day, slot)."""
    valid: bool = True
    reasons: list = field(default_factory=list)
    room: Optional[str] = None
    conflicts: list = field(default_factory=list)


@dataclass
class EditClassResult:
    """Result of editing a class — what state changes occurred."""
    placement_cleared: bool = False


# ── Snapshot helpers ─────────────────────────────────────────────────────────

def snapshot_placements(state):
    """Return {cls_key(cls): (day, time, room)} for all placed non-pinned classes."""
    return {
        cls_key(c): (c["placed_day"], c["placed_time"], c["placed_classroom"])
        for c in state["classes"]
        if c["placed"] and not c["pinned"]
    }


def restore_placements(state, snapshots):
    """Restore placements from a snapshot dict."""
    for cls in state["classes"]:
        snap = snapshots.get(cls_key(cls))
        if snap:
            mark_placed(cls, snap[0], snap[1], snap[2])
        elif cls["placed"] and not cls["pinned"]:
            # Class was placed during the operation but wasn't in the
            # original snapshot — unplace it to restore original state.
            mark_unplaced(cls)


# ── SchedulingWorkflow ───────────────────────────────────────────────────────

class SchedulingWorkflow:
    """UI-free orchestrator for all scheduling business logic.

    Parameters
    ----------
    state : dict
        The application state dict (classes, days, slots, classrooms, …).
    get_weights : callable
        Returns the current learned scoring weights dict.
    feedback_logger : object, optional
        FeedbackLogger instance for logging placement decisions.
    preference_learner : object, optional
        PreferenceLearner instance for learning from feedback.
    """

    def __init__(self, state, get_weights, feedback_logger=None,
                 preference_learner=None):
        self.state = state
        self.get_weights = get_weights
        self._feedback = feedback_logger
        self._learner = preference_learner
        self._optimizing = False

    # ── Auto-place single class ──────────────────────────────────────────

    @property
    def is_optimizing(self):
        """True while an optimization operation is running."""
        return self._optimizing

    def auto_place(self, cls) -> AutoPlaceResult:
        """Automatically place *cls* using AI-assisted optimization.

        Returns an AutoPlaceResult with the outcome.  Does NOT mutate state
        on failure; on success the caller should apply placements.
        """
        self._optimizing = True
        try:
            return self._auto_place_impl(cls)
        finally:
            self._optimizing = False

    def _auto_place_impl(self, cls) -> AutoPlaceResult:
        existing = snapshot_placements(self.state)
        # Exclude the target class from snapshots
        existing.pop(cls_key(cls), None)

        weights = self.get_weights()
        success, placements, rescheduled = optimized_auto_place(
            self.state, cls, weights=weights)

        if not success:
            return AutoPlaceResult(success=False)

        relocated = []
        placed_info = None

        for c in self.state["classes"]:
            p = placements.get(cls_key(c))
            if p is None:
                continue
            day, slot, room = p
            if c is cls:
                effective_room = room if needs_physical_room(cls) else None
                if not cls["pinned"]:
                    mark_placed(cls, day, slot, effective_room)
                placed_info = (day, slot, effective_room)
            else:
                old = existing.get(cls_key(c))
                if old:
                    relocated.append({
                        "name": c["name"],
                        "old_day": old[0], "old_time": old[1], "old_room": old[2],
                        "new_day": day, "new_time": slot, "new_room": room,
                    })
                effective_room = room if needs_physical_room(c) else None
                mark_placed(c, day, slot, effective_room)

        # Score & explain the placement
        explanation = None
        score = 0.0
        if placed_info:
            s, breakdown, explanation = score_placement_explained(
                self.state, cls,
                placed_info[0], placed_info[1], placed_info[2],
                weights=weights)
            score = s
            if self._feedback:
                self._feedback.log_accepted_placement(
                    cls, placed_info[0], placed_info[1], placed_info[2],
                    score=s)

        return AutoPlaceResult(
            success=True,
            relocated=relocated,
            placed_info=placed_info,
            explanation=explanation,
            score=score,
        )

    # ── Schedule new classes ─────────────────────────────────────────────

    def schedule_new_classes(self, new_classes) -> ScheduleNewResult:
        """Add *new_classes* to state and run batch scheduling.

        Returns a ScheduleNewResult describing what happened.
        The caller decides whether to commit (apply placements) or rollback.
        """
        if not new_classes:
            return ScheduleNewResult()

        existing = snapshot_placements(self.state)

        # Add new classes to state
        for cls in new_classes:
            self.state["classes"].append(cls)

        weights = self.get_weights()
        placed, unplaced, rescheduled = optimized_batch_schedule(
            self.state, new_classes, weights=weights)

        result = ScheduleNewResult(
            placed=placed,
            unplaced=unplaced,
            rescheduled=rescheduled,
        )

        # Fast path: all new classes placed without rescheduling existing ones
        new_ids = {cls_key(c) for c in new_classes}
        new_placed = [p for p in placed if cls_key(p[0]) in new_ids]
        if (len(new_placed) == len(new_classes) and not unplaced
                and not rescheduled):
            for cls, day, slot, room in new_placed:
                effective_room = room if needs_physical_room(cls) else None
                if not cls["pinned"]:
                    mark_placed(cls, day, slot, effective_room)
            if self._feedback:
                self._feedback.log_batch_result(
                    len(new_placed), 0, False, True)
            result.single_success = True
            return result

        # Single-class that could not be placed
        if len(new_classes) == 1 and not placed:
            cls = new_classes[0]
            from scheduler_app.constraint_negotiator import ConstraintNegotiator
            neg = ConstraintNegotiator(self.state)
            report = neg.negotiate_class(cls)
            result.single_failed = True
            result.negotiation_report = report

            # Remove the failed class from state regardless of pinned status
            if cls in self.state["classes"]:
                self.state["classes"].remove(cls)
            return result

        return result

    def apply_schedule_result(self, result: ScheduleNewResult):
        """Commit the placements from a ScheduleNewResult."""
        for cls, day, slot, room in result.placed:
            if not cls["pinned"]:
                effective_room = room if needs_physical_room(cls) else None
                mark_placed(cls, day, slot, effective_room)
        for cls, _ in result.unplaced:
            if not cls["pinned"]:
                mark_unplaced(cls)
        if self._feedback:
            self._feedback.log_batch_result(
                len(result.placed), len(result.unplaced),
                result.rescheduled, True)

    def rollback_schedule(self, new_classes, existing_snapshots):
        """Remove new_classes from state and restore old placements."""
        for cls in new_classes:
            if cls in self.state["classes"]:
                self.state["classes"].remove(cls)
        restore_placements(self.state, existing_snapshots)

    # ── Batch placement ──────────────────────────────────────────────────

    def place_batch(self, candidates) -> PlaceBatchResult:
        """Auto-place a batch of candidate classes."""
        uniq = []
        seen = set()
        for cls in candidates:
            if cls is None or cls not in self.state["classes"]:
                continue
            cid = cls_key(cls)
            if cid in seen:
                continue
            seen.add(cid)
            uniq.append(cls)

        if not uniq:
            return PlaceBatchResult()

        weights = self.get_weights()
        placed, unplaced, rescheduled = optimized_batch_schedule(
            self.state, uniq, weights=weights)

        placed_map = {}
        for cls, day, slot, room in placed:
            placed_map[cls_key(cls)] = (day, slot, room)

        # Validate placements against live state before committing
        from scheduler_app.core.constraint_validator import ConstraintValidator
        batch_validator = ConstraintValidator(
            self.state,
            exclude_ids=set(placed_map.keys()))
        valid_days = set(self.state.get("days", []))
        valid_slots = set(self.state.get("slots", []))

        for cls in self.state["classes"]:
            p = placed_map.get(cls_key(cls))
            if p is not None:
                day, slot, room = p
                effective_room = room if needs_physical_room(cls) else None
                if (day in valid_days and slot in valid_slots
                        and batch_validator.check_placement(
                            cls, day, slot, effective_room)):
                    mark_placed(cls, day, slot, effective_room)
                    batch_validator.add_placement(cls, day, slot, effective_room)
                else:
                    # Placement invalid against live state — treat as unplaced
                    placed_map.pop(cls_key(cls))
                    if not cls.get("pinned"):
                        mark_unplaced(cls)

        unresolved_ids = set()
        for cls, _reason in unplaced:
            unresolved_ids.add(cls_key(cls))
            if not cls.get("pinned"):
                mark_unplaced(cls)

        placed_count = sum(1 for cls in uniq if cls_key(cls) in placed_map)
        unresolved_count = sum(1 for cls in uniq if cls_key(cls) in unresolved_ids)

        if self._feedback:
            self._feedback.log_batch_result(
                len(placed), len(unplaced), rescheduled, True)

        return PlaceBatchResult(
            placed_count=placed_count,
            unresolved_count=unresolved_count,
            rescheduled=rescheduled,
            placed=placed,
            unplaced=unplaced,
        )

    # ── Reschedule ───────────────────────────────────────────────────────

    def reschedule(self, weights, use_cpsat=False,
                   progress_callback=None,
                   seed=DEFAULT_OPTIMIZER_SEED) -> RescheduleResult:
        """Run full reschedule optimization. Returns proposed changes.

        `seed` defaults to a fixed value so the same timetable regenerates the
        same way (ST-SCHED-013); pass None to randomize deliberately. The seed
        actually used comes back as `result.summary['seed']`.
        """
        self._optimizing = True
        try:
            return self._reschedule_impl(weights, use_cpsat,
                                         progress_callback, seed)
        finally:
            self._optimizing = False

    def _reschedule_impl(self, weights, use_cpsat, progress_callback,
                         seed=DEFAULT_OPTIMIZER_SEED):
        placed, unplaced, changes, summary = optimized_reschedule_all(
            self.state, weights=weights,
            progress_callback=progress_callback,
            use_cpsat=use_cpsat, seed=seed)

        # Build analytics
        from scheduler_app.explanation_engine import ExplanationEngine
        engine = ExplanationEngine()
        explanation = (engine.explain_reschedule_improvements(summary)
                       if summary else None)
        analytics = analyze_schedule(self.state, placed) if placed else None

        negotiation_result = None
        if unplaced:
            negotiation_result = negotiate_after_optimization(
                self.state, placed, unplaced)

        return RescheduleResult(
            placed=placed,
            unplaced=unplaced,
            changes=changes,
            summary=summary,
            analytics=analytics,
            explanation=explanation,
            negotiation_result=negotiation_result,
        )

    def apply_reschedule(self, result: RescheduleResult):
        """Commit reschedule placements, validating against current state.

        Builds a fresh ConstraintValidator and checks each placement
        before committing, so any state changes between optimization
        and apply are caught. Returns list of rejected class names.
        """
        from scheduler_app.core.constraint_validator import ConstraintValidator
        valid_days = set(self.state.get("days", []))
        valid_slots = set(self.state.get("slots", []))

        # Build validator excluding all classes that will be re-placed
        placed_keys = {cls_key(c) for c, _, _, _ in result.placed
                       if not c["pinned"]}
        validator = ConstraintValidator(
            self.state, exclude_ids=placed_keys)

        rejected = []
        for cls_item, day, slot, room in result.placed:
            if cls_item["pinned"]:
                continue
            if day not in valid_days or slot not in valid_slots:
                mark_unplaced(cls_item)
                rejected.append(cls_item.get("name", "?"))
                continue
            effective_room = room if needs_physical_room(cls_item) else None
            if not validator.check_placement(cls_item, day, slot,
                                             effective_room):
                mark_unplaced(cls_item)
                rejected.append(cls_item.get("name", "?"))
                continue
            validator.add_placement(cls_item, day, slot, effective_room)
            mark_placed(cls_item, day, slot, effective_room)

        for cls_item, _ in result.unplaced:
            if not cls_item["pinned"]:
                mark_unplaced(cls_item)

        if self._feedback:
            self._feedback.log_reschedule_accepted(result.changes)
        if self._learner:
            self._learner.learn()

        return rejected

    def reject_reschedule(self, snapshots, changes=None):
        """Rollback a rejected reschedule."""
        restore_placements(self.state, snapshots)
        if self._feedback:
            self._feedback.log_reschedule_rejected(changes or [])

    # ── Drop validation ──────────────────────────────────────────────────

    @staticmethod
    def validate_drop(state, cls, day, slot, drag_backup=None) -> DropValidation:
        """Validate whether *cls* can be dropped at (day, slot).

        Returns a DropValidation with reasons if invalid.
        This is pure validation — no state mutation.
        """
        td = total_duration(cls)
        reasons = []

        # Same-day protection
        if cls.get("protection") == "same_day" and drag_backup:
            original_day = drag_backup.get("placed_day")
            if original_day and day != original_day:
                reasons.append(("restricted_to_day", original_day))

        # ST-DATA-003: `slots_fit` now returns False for a slot that is not on
        # the grid at all, which would otherwise expose the bare `.index(slot)`
        # below. Distinguish the two: "this hour does not exist" and "this hour
        # exists but the class does not fit after it" need different wording.
        slot_idx = find_slot_index(state, slot)
        if slot_idx is None:
            reasons.append(("slot_not_in_grid", slot))
        elif day not in state["days"]:
            reasons.append(("day_not_in_grid", day))
        elif not slots_fit(state, slot, td):
            slots_available = len(state["slots"]) - slot_idx
            reasons.append(("not_enough_slots", td, slots_available, slot))

        if cls["allowed_days"] and day not in cls["allowed_days"]:
            reasons.append(("day_not_allowed", day, cls["allowed_days"]))

        if cls.get("excluded_days") and day in cls["excluded_days"]:
            reasons.append(("day_excluded", day, cls["excluded_days"]))

        if cls["allowed_times"] and slot not in cls["allowed_times"]:
            reasons.append(("time_not_allowed", slot, cls["allowed_times"]))

        if cls.get("excluded_times") and slot in cls["excluded_times"]:
            reasons.append(("time_excluded", slot, cls["excluded_times"]))

        if reasons:
            return DropValidation(valid=False, reasons=reasons)

        return DropValidation(valid=True)

    @staticmethod
    def find_drop_classroom(state, cls, day, slot, preferred_rooms=None):
        """Find the best classroom for a drop at (day, slot).

        Parameters
        ----------
        preferred_rooms : list, optional
            Ordered list of rooms to prefer (e.g. current filter, original room).

        Returns (room, conflicts) — room is None if no compatible room exists.
        """
        rooms = list(state["classrooms"])
        if cls["required_classrooms"]:
            rooms = [r for r in rooms if r in cls["required_classrooms"]]
        if cls["excluded_classrooms"]:
            rooms = [r for r in rooms if r not in cls["excluded_classrooms"]]
        rooms = [r for r in rooms if room_fits_class(state, r, cls)]

        # Apply preference ordering
        if preferred_rooms:
            for pref in reversed(preferred_rooms):
                if pref in rooms:
                    rooms = [pref] + [r for r in rooms if r != pref]

        for room in rooms:
            conflicts = find_conflicts(state, cls, day, slot, room)
            if not conflicts:
                return room, []

        if rooms:
            return rooms[0], find_conflicts(state, cls, day, slot, rooms[0])
        return None, ["no_compatible_classrooms"]

    @staticmethod
    def validate_drop_constraints(state, cls, day, slot, room) -> DropValidation:
        """Check classroom-level constraints after room selection."""
        if not respects_constraints(cls, day, slot, room, state=state):
            reasons = []
            if cls["required_classrooms"] and room not in cls["required_classrooms"]:
                reasons.append(("classroom_not_required", room,
                                cls["required_classrooms"]))
            if cls["excluded_classrooms"] and room in cls["excluded_classrooms"]:
                reasons.append(("classroom_excluded", room))
            if not room_fits_class(state, room, cls):
                from scheduler_app.models import get_room_capacity
                cap = get_room_capacity(state, room)
                reasons.append(("classroom_capacity", room, cap,
                                cls.get("participants", 0)))
            return DropValidation(valid=False, reasons=reasons, room=room)
        return DropValidation(valid=True, room=room)

    def log_manual_move(self, cls, old_day, old_slot, old_room,
                        new_day, new_slot, new_room):
        """Log a manual drag-drop move for preference learning."""
        weights = self.get_weights()
        score_old = None
        score_new = None
        if old_day and old_slot and old_room:
            score_old = score_placement(
                self.state, cls, old_day, old_slot, old_room, weights=weights)
        score_new = score_placement(
            self.state, cls, new_day, new_slot, new_room, weights=weights)
        if self._feedback:
            self._feedback.log_manual_move(
                cls, old_day, old_slot, old_room,
                new_day, new_slot, new_room,
                score_old=score_old, score_new=score_new)
        if self._learner:
            self._learner.learn()

    # ── Edit class ───────────────────────────────────────────────────────

    @staticmethod
    def apply_class_edit(state, cls, updated) -> EditClassResult:
        """Apply edits from *updated* dict to *cls*, validating placement.

        Returns an EditClassResult describing what happened.
        """
        was_placed = cls.get("placed", False)
        old_day = cls.get("placed_day")
        old_slot = cls.get("placed_time")
        old_room = cls.get("placed_classroom")

        copy_editable_class_fields(cls, updated)

        if cls["pinned"]:
            mark_unplaced(cls)
        elif was_placed:
            mark_placed(cls, old_day, old_slot,
                        old_room if needs_physical_room(cls) else None)
        else:
            mark_unplaced(cls)

        # Check if placement is still valid after edit
        placement_cleared = False
        if cls["placed"]:
            td = total_duration(cls)
            day = cls["placed_day"]
            slot = cls["placed_time"]
            room = cls["placed_classroom"] if needs_physical_room(cls) else None
            if (not day or not slot
                    or not slots_fit(state, slot, td)
                    or find_conflicts(state, cls, day, slot, room)):
                mark_unplaced(cls)
                placement_cleared = True

        return EditClassResult(placement_cleared=placement_cleared)

    @staticmethod
    def validate_placements_after_edit(state) -> list:
        """Check all placed classes — return list of names whose placement
        became invalid (and unplace them)."""
        invalidated = []
        for cls in state["classes"]:
            if not cls.get("placed") or cls.get("pinned"):
                continue
            day = cls.get("placed_day")
            slot = cls.get("placed_time")
            room = (cls.get("placed_classroom")
                    if needs_physical_room(cls) else None)
            td = total_duration(cls)
            if (not day or not slot
                    or not slots_fit(state, slot, td)
                    or find_conflicts(state, cls, day, slot, room)):
                invalidated.append(cls["name"])
                mark_unplaced(cls)
        return invalidated

    # ── Remove classes ───────────────────────────────────────────────────

    @staticmethod
    def remove_classes(state, classes) -> int:
        """Remove *classes* from state. Returns count of actually removed."""
        uniq = []
        seen = set()
        for cls in classes:
            if cls is None:
                continue
            cid = cls_key(cls)
            if cid in seen:
                continue
            seen.add(cid)
            if cls in state["classes"]:
                uniq.append(cls)
        for cls in uniq:
            if cls in state["classes"]:
                state["classes"].remove(cls)
        return len(uniq)

    # ── Unplace classes ──────────────────────────────────────────────────

    @staticmethod
    def unplace_classes(classes) -> int:
        """Mark classes as unplaced. Returns count."""
        count = 0
        for cls in classes:
            mark_unplaced(cls)
            count += 1
        return count

    # ── Split non-joint convenience ──────────────────────────────────────

    @staticmethod
    def split_non_joint(cls):
        """Split a non-joint class into per-target classes."""
        return split_non_joint(cls)

    # ── Quick-check for drop (no UI, fast) ───────────────────────────────

    @staticmethod
    def check_drop_valid(state, cls, day, slot, drag_backup=None,
                         preferred_rooms=None):
        """Fast boolean check: can cls be dropped at (day, slot)?"""
        if cls.get("protection") == "same_day" and drag_backup:
            original_day = drag_backup.get("placed_day")
            if original_day and day != original_day:
                return False

        td = total_duration(cls)
        if not slots_fit(state, slot, td):
            return False
        if cls["allowed_days"] and day not in cls["allowed_days"]:
            return False
        if cls.get("excluded_days") and day in cls["excluded_days"]:
            return False
        if cls["allowed_times"] and slot not in cls["allowed_times"]:
            return False
        if cls.get("excluded_times") and slot in cls["excluded_times"]:
            return False

        room, conflicts = SchedulingWorkflow.find_drop_classroom(
            state, cls, day, slot, preferred_rooms=preferred_rooms)
        if room is None or conflicts:
            return False
        if not respects_constraints(cls, day, slot, room, state=state):
            return False
        return True
