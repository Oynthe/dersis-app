"""Schedule Impact Analyzer — non-invasive observer module.

Detects scheduling-relevant changes, compares old vs new state,
validates current timetable (read-only), and returns a structured
impact result without modifying any core scheduling logic.

This module is fully decoupled from UI and solver internals.
"""

import copy
from enum import Enum
from typing import Any

try:
    from deepdiff import DeepDiff
except ImportError:
    # Optional dependency; the module degrades to its own comparison.
    DeepDiff: Any = None   # type: ignore[no-redef]


class ImpactLevel(Enum):
    NO_RESCHEDULE_NEEDED = "no_reschedule_needed"
    RESCHEDULE_RECOMMENDED = "reschedule_recommended"
    RESCHEDULE_REQUIRED = "reschedule_required"


class ImpactResult:
    """Structured result of a schedule impact analysis."""

    __slots__ = (
        "level", "changed_fields", "affected_entities",
        "hard_violations", "soft_impact_reasons",
    )

    def __init__(
        self,
        level: ImpactLevel = ImpactLevel.NO_RESCHEDULE_NEEDED,
        changed_fields: list | None = None,
        affected_entities: list | None = None,
        hard_violations: list | None = None,
        soft_impact_reasons: list | None = None,
    ):
        self.level = level
        self.changed_fields = changed_fields or []
        self.affected_entities = affected_entities or []
        self.hard_violations = hard_violations or []
        self.soft_impact_reasons = soft_impact_reasons or []


# ── Keys relevant to scheduling ──────────────────────────────────────────

_SCHEDULING_RELEVANT_KEYS = {
    "days", "slots", "classrooms", "classroom_capacities",
    "lecturers", "lecturer_availability", "years",
}

_HARD_CONSTRAINT_CLASS_FIELDS = {
    "lecturer", "targets", "duration", "location_type",
    "pinned", "pinned_day", "pinned_time", "pinned_classroom",
    "allowed_days", "allowed_times", "excluded_days", "excluded_times",
    "required_classrooms", "excluded_classrooms",
}

_SOFT_CLASS_FIELDS = {
    "participants", "joint_session", "protection",
}

# Fields that are cosmetic / non-scheduling
_IGNORED_CLASS_FIELDS = {
    "name", "class_code",
    "placed", "placed_day", "placed_time", "placed_classroom",
}


def capture_snapshot(state: dict) -> dict:
    """Capture a normalized scheduling-relevant snapshot of the state.

    Only includes fields that matter for impact analysis.
    Does NOT capture placement state (placed_day, etc.) — those are
    the current schedule, not the inputs.
    """
    snap = {}
    for key in _SCHEDULING_RELEVANT_KEYS:
        val = state.get(key)
        if val is not None:
            snap[key] = copy.deepcopy(val)

    # Capture class constraints (not placement state)
    classes_snap = []
    for cls in state.get("classes", []):
        c: dict[str, Any] = {}
        for field in _HARD_CONSTRAINT_CLASS_FIELDS | _SOFT_CLASS_FIELDS:
            val = cls.get(field)
            if isinstance(val, list):
                c[field] = list(val)
            elif isinstance(val, dict):
                c[field] = dict(val)
            else:
                c[field] = val
        # Use id-like key for matching classes across snapshots
        c["_name"] = cls.get("name", "")
        c["_class_code"] = cls.get("class_code", "")
        classes_snap.append(c)
    snap["_classes"] = classes_snap
    return snap


def _diff_snapshots(before: dict, after: dict) -> dict | None:
    """Compute meaningful diff between two snapshots.

    Returns None if no scheduling-relevant change detected.
    """
    if DeepDiff is not None:
        diff = DeepDiff(
            before, after,
            ignore_order=True,
            report_repetition=True,
            verbose_level=1,
        )
        if not diff:
            return None
        return diff
    # Fallback: simple equality check
    if before == after:
        return None
    return {"fallback": True}


def _extract_changed_fields(diff: dict) -> list[str]:
    """Extract human-readable field names from a DeepDiff result."""
    fields = set()
    for change_type in ("values_changed", "type_changes",
                        "dictionary_item_added", "dictionary_item_removed",
                        "iterable_item_added", "iterable_item_removed",
                        "set_item_added", "set_item_removed"):
        entries = diff.get(change_type, {})
        if isinstance(entries, dict):
            for path in entries:
                parts = str(path).replace("root", "").strip("[]'\"").split("'")
                for p in parts:
                    p = p.strip("[]")
                    if p and not p.isdigit():
                        fields.add(p)
    if diff.get("fallback"):
        fields.add("unknown")
    return sorted(fields)


def _find_affected_class_entities(before: dict, after: dict) -> list[str]:
    """Find classes whose constraints changed between snapshots."""
    affected = []
    before_classes = {(c.get("_name"), c.get("_class_code")): c
                      for c in before.get("_classes", [])}
    after_classes = {(c.get("_name"), c.get("_class_code")): c
                     for c in after.get("_classes", [])}

    # Check modified classes
    for key, after_cls in after_classes.items():
        before_cls = before_classes.get(key)
        if before_cls is None:
            affected.append(key[0] or key[1] or "new class")
            continue
        for field in _HARD_CONSTRAINT_CLASS_FIELDS | _SOFT_CLASS_FIELDS:
            if before_cls.get(field) != after_cls.get(field):
                affected.append(key[0] or key[1] or "class")
                break

    # Check removed classes
    for key in before_classes:
        if key not in after_classes:
            affected.append(key[0] or key[1] or "removed class")

    return affected


def _check_hard_violations(state: dict) -> list[str]:
    """Validate current placements against current constraints (read-only).

    Reuses the existing ConstraintValidator for consistency.
    Returns list of violation descriptions.
    """
    from scheduler_app.core.constraint_validator import ConstraintValidator
    from scheduler_app.core.logic import get_placed_classes
    from scheduler_app.core.models import (
        effective_day, effective_time, effective_room, cls_key,
    )

    placed = get_placed_classes(state)
    if not placed:
        return []

    violations = []
    for cls in placed:
        day = effective_day(cls)
        time = effective_time(cls)
        room = effective_room(cls)
        if day is None or time is None:
            continue
        # Build validator excluding this class to avoid self-conflict
        validator = ConstraintValidator(state, exclude_ids={cls_key(cls)})
        valid, reasons = validator.check_placement_explained(cls, day, time, room)
        if not valid:
            class_name = cls.get("name", "unknown")
            for reason in reasons:
                violations.append(f"{class_name}: {reason}")

    return violations


def _check_soft_impacts(before: dict, after: dict, diff: dict) -> list[str]:
    """Check for soft/optimization-relevant changes."""
    reasons = []
    changed = _extract_changed_fields(diff)

    # Structural changes that affect optimization
    if "classroom_capacities" in changed or "classroom_capacities" in str(diff):
        reasons.append("classroom capacities changed")
    if "participants" in changed:
        reasons.append("class participant counts changed")
    if "protection" in changed:
        reasons.append("class protection levels changed")
    if "joint_session" in changed:
        reasons.append("joint session settings changed")

    # Lecturer availability tightened or loosened
    before_avail = before.get("lecturer_availability", {})
    after_avail = after.get("lecturer_availability", {})
    if before_avail != after_avail:
        reasons.append("lecturer availability preferences changed")

    # Timeslot or day set changed (even if still valid)
    if before.get("days") != after.get("days"):
        reasons.append("active days changed")
    if before.get("slots") != after.get("slots"):
        reasons.append("time slots changed")

    # Classroom set changed
    if before.get("classrooms") != after.get("classrooms"):
        reasons.append("classroom list changed")

    # Years/branches changed
    if before.get("years") != after.get("years"):
        reasons.append("year/branch structure changed")

    return reasons


def analyze_impact(before_snapshot: dict, after_snapshot: dict,
                   current_state: dict) -> ImpactResult:
    """Analyze the impact of a state change on the current schedule.

    Args:
        before_snapshot: Snapshot captured before the change.
        after_snapshot: Snapshot captured after the change.
        current_state: The live state dict (for validation).

    Returns:
        ImpactResult with the appropriate level and metadata.
    """
    # Step 1: Diff
    diff = _diff_snapshots(before_snapshot, after_snapshot)
    if diff is None:
        return ImpactResult(level=ImpactLevel.NO_RESCHEDULE_NEEDED)

    changed_fields = _extract_changed_fields(diff)
    affected = _find_affected_class_entities(before_snapshot, after_snapshot)

    # Step 2: Hard validation — do current placements still satisfy constraints?
    hard_violations = _check_hard_violations(current_state)
    if hard_violations:
        return ImpactResult(
            level=ImpactLevel.RESCHEDULE_REQUIRED,
            changed_fields=changed_fields,
            affected_entities=affected,
            hard_violations=hard_violations,
        )

    # Step 3: Soft analysis
    soft_reasons = _check_soft_impacts(before_snapshot, after_snapshot, diff)
    if soft_reasons:
        return ImpactResult(
            level=ImpactLevel.RESCHEDULE_RECOMMENDED,
            changed_fields=changed_fields,
            affected_entities=affected,
            soft_impact_reasons=soft_reasons,
        )

    # Something changed but it's not scheduling-relevant
    return ImpactResult(level=ImpactLevel.NO_RESCHEDULE_NEEDED)
