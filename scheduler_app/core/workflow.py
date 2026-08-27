"""SchedulingWorkflow: UI-free business logic for class scheduling operations.

Extracts placement, scheduling, editing, rescheduling, and drop-validation
logic from the UI layer so that it can be tested and reused independently.
All methods operate on plain state dicts and return result objects — no Qt
imports, no dialog references, no widget manipulation.
"""

from __future__ import annotations

import copy
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


# Distinct from None on purpose: None is a legitimate cached value, meaning
# "nothing was unplaced, so there is nothing to report".
_UNSET = object()


@dataclass
class RescheduleResult:
    """Result of a full reschedule operation.

    ST-PERF-007: ``negotiation_result`` is a lazily computed, memoised property
    rather than a field. The negotiation pass costs roughly as much as the solve
    itself (measured at ~10 s of wrapper overhead on a 25-class instance) and
    ran unconditionally whenever anything was unplaced, whether or not anyone
    ever looked at it.

    NOTE the field is *deleted*, not shadowed. Leaving the annotation in the
    dataclass body while adding the property makes the generated ``__init__``
    run ``self.negotiation_result = None``, which goes through the setter and
    permanently poisons the cache with None — every later read returns None and
    the negotiation tab silently disappears.
    """
    placed: list = field(default_factory=list)
    unplaced: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    summary: Optional[dict] = None
    analytics: Optional[dict] = None
    explanation: Optional[dict] = None
    _negotiation_factory: Optional[Callable] = field(default=None, repr=False)
    _negotiation_cache: Any = field(default=_UNSET, repr=False)

    @property
    def negotiation_result(self):
        """The negotiation report, computed on first read and then cached."""
        if self._negotiation_cache is _UNSET:
            self._negotiation_cache = (
                self._negotiation_factory() if self._negotiation_factory
                else None)
        return self._negotiation_cache

    @negotiation_result.setter
    def negotiation_result(self, value):
        # Kept so the attribute still behaves like a plain one for callers
        # that assign to it.
        self._negotiation_cache = value


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

        # ST-DATA-011: the add is all-or-nothing. Without this, an optimizer,
        # negotiator or feedback-logger failure left a half-added class in
        # state["classes"] — sometimes already marked placed — that the user was
        # never told about and could not see a cause for.
        try:
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
        except BaseException:
            # ORDER IS LOAD-BEARING, and the opposite of the public
            # rollback_schedule(): restore FIRST, while the new classes are
            # still in state["classes"], so restore_placements() can see them
            # and clear the placed=True the fast path may already have written.
            # Removing them first would leave exactly that orphan behind.
            # BaseException, not Exception: the optimizer is wall-clock bound
            # and multiprocess, so KeyboardInterrupt here is realistic.
            restore_placements(self.state, existing)
            for _cls in new_classes:
                if _cls in self.state["classes"]:
                    self.state["classes"].remove(_cls)
            raise

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
                   seed=DEFAULT_OPTIMIZER_SEED,
                   cancel_token=None, **optimizer_kwargs) -> RescheduleResult:
        """Run full reschedule optimization. Returns proposed changes.

        `seed` defaults to a fixed value so the same timetable regenerates the
        same way (ST-SCHED-013); pass None to randomize deliberately. The seed
        actually used comes back as `result.summary['seed']`.
        """
        self._optimizing = True
        try:
            return self._reschedule_impl(weights, use_cpsat,
                                         progress_callback, seed,
                                         cancel_token, **optimizer_kwargs)
        finally:
            self._optimizing = False

    def _reschedule_impl(self, weights, use_cpsat, progress_callback,
                         seed=DEFAULT_OPTIMIZER_SEED, cancel_token=None,
                         **optimizer_kwargs):
        placed, unplaced, changes, summary = optimized_reschedule_all(
            self.state, weights=weights,
            progress_callback=progress_callback,
            use_cpsat=use_cpsat, seed=seed,
            cancel_token=cancel_token, **optimizer_kwargs)

        # ST-PERF-001: stop before the expensive analysis passes below. A user
        # who cancelled does not want to wait out a negotiation run for a
        # result that is about to be discarded.
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

        # Build analytics
        from scheduler_app.explanation_engine import ExplanationEngine
        engine = ExplanationEngine()
        explanation = (engine.explain_reschedule_improvements(summary)
                       if summary else None)
        analytics = analyze_schedule(self.state, placed) if placed else None

        # ST-PERF-007: deferred, and pinned to the state as of NOW. ui/app.py
        # reads this on both sides of apply_reschedule(); analysing the live
        # state at read time would give the results dialog and the warning log
        # different answers for the same reschedule. The snapshot is also what
        # keeps the negotiator's mutate-and-restore estimators (ST-DATA-011)
        # away from the live timetable during a UI repaint.
        # Measured deepcopy cost: 0.49 ms at 25 classes, 3.06 ms at 250 —
        # against the 727 ms / 5.8 s pass it defers.
        negotiation_factory = None
        if unplaced:
            frozen_state = copy.deepcopy(self.state)
            by_uid = {cls_key(c): c for c in frozen_state["classes"]}
            frozen_unplaced = [(by_uid.get(cls_key(c), c), r)
                               for c, r in unplaced]

            def negotiation_factory():
                return negotiate_after_optimization(
                    frozen_state, [], frozen_unplaced)

        return RescheduleResult(
            placed=placed,
            unplaced=unplaced,
            changes=changes,
            summary=summary,
            analytics=analytics,
            explanation=explanation,
            _negotiation_factory=negotiation_factory,
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

        # Build validator excluding EVERY class that will be re-placed —
        # pinned ones included. ST-SCHED-002: pinned classes used to be excluded
        # from this exclusion set and then skipped by the commit loop entirely,
        # so two classes pinned to the same room/day/hour were both committed
        # and reported to nobody, while the quality panel called the timetable
        # clean.
        placed_keys = {cls_key(c) for c, _, _, _ in result.placed}
        validator = ConstraintValidator(
            self.state, exclude_ids=placed_keys)

        rejected = []

        # Pins first: they are fixed points the flexible classes must work
        # around, so they have to be in the occupancy map before anything else
        # is checked against it.
        for cls_item, _day, _slot, _room in result.placed:
            if not cls_item["pinned"]:
                continue
            day = cls_item.get("pinned_day")
            slot = cls_item.get("pinned_time")
            room = (cls_item.get("pinned_classroom")
                    if needs_physical_room(cls_item) else None)
            ok = (day in valid_days and slot in valid_slots
                  and validator.check_placement(cls_item, day, slot, room))
            if not ok:
                # The pin is the user's explicit instruction, so it is NOT
                # silently cleared — that would destroy the intent they typed
                # in. It is reported instead, so the UI can show the clash and
                # let them decide which pin to move.
                rejected.append(cls_item.get("name", "?"))
            # Register it either way: an infeasible pin still occupies the cell
            # once committed, and flexible classes must not be steered into it.
            if day in valid_days and slot in valid_slots:
                validator.add_placement(cls_item, day, slot, room)

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

    @staticmethod
    def reconcile_placements(state) -> list:
        """Clear every placement or pin that points at an axis value the state
        no longer has. Returns the affected class dicts.

        ST-DATA-004. Removing a day, hour, room or lecturer in Setup used to
        leave the classes already placed there pointing at something that no
        longer exists — orphans reachable through completely ordinary UI use,
        which then crashed analytics, export and reschedule (ST-DATA-003).

        It lives in core rather than in ``SetupDialog`` so that import, undo and
        any future entry point get the same repair; dialogs writing live state
        is ST-ARCH-007. It only ever *clears* fields, never invents a placement,
        so it cannot corrupt a good file and needs no schema bump.

        A blank lecturer is deliberately treated as "unassigned", not "deleted":
        ``new_class()`` ships ``"lecturer": ""``, ``SetupDialog`` never puts ""
        into ``state["lecturers"]``, and the core reads blank as "no lecturer
        constraint". Treating it as an orphan would unplace every not-yet-staffed
        lesson on the first Setup OK.
        """
        days = set(state.get("days") or [])
        slots = set(state.get("slots") or [])
        rooms = set(state.get("classrooms") or [])
        lecturers = set(state.get("lecturers") or [])
        affected = []
        for cls in state.get("classes", []):
            physical = needs_physical_room(cls)
            name = (cls.get("lecturer") or "").strip()
            lecturer_ok = (not name) or name in lecturers
            touched = False
            if cls.get("pinned"):
                day_bad = cls.get("pinned_day") not in days
                time_bad = cls.get("pinned_time") not in slots
                room_bad = physical and cls.get("pinned_classroom") not in rooms
                if day_bad or time_bad or room_bad or not lecturer_ok:
                    cls["pinned"] = False
                    if day_bad:
                        cls["pinned_day"] = None
                    if time_bad:
                        cls["pinned_time"] = None
                    if room_bad:
                        cls["pinned_classroom"] = None
                    touched = True
            if cls.get("placed") and (
                    cls.get("placed_day") not in days
                    or cls.get("placed_time") not in slots
                    or (physical and cls.get("placed_classroom") not in rooms)
                    or not lecturer_ok):
                mark_unplaced(cls)
                touched = True
            if touched:
                affected.append(cls)
        return affected

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
