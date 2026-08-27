"""Schedule quality analytics — pure computation, no UI.

Computes metrics from the current timetable state:
- Lecturer gap distribution (idle periods between classes)
- Student idle-time distribution per branch/group
- Room utilization rates
- Busiest days and time slots
- Underused classrooms
- Overloaded lecturers (dense schedules)
"""

from scheduler_app.logic import (
    find_slot_index,
    get_placed_classes, occupied_slots_of, classroom_of, slot_index,
    total_duration,
)
from scheduler_app.models import effective_day, effective_time


def _build_day_slot_map(state, placed, key_fn):
    """Group placed classes by a key (e.g. lecturer) into {key: {day: [slot_indices]}}."""
    result = {}
    for cls in placed:
        k = key_fn(cls)
        if not k:
            continue
        day = effective_day(cls)
        start = effective_time(cls)
        td = total_duration(cls)
        si = find_slot_index(state, start)
        if si is None or day not in state["days"]:
            continue  # placement orphaned by a removed slot/day (ST-DATA-003)
        indices = list(range(si, si + td))
        result.setdefault(k, {}).setdefault(day, []).extend(indices)
    # Sort each day's indices
    for k in result:
        for day in result[k]:
            result[k][day] = sorted(set(result[k][day]))
    return result


def _count_gaps(sorted_indices):
    """Count idle-period slots between occupied indices on a single day."""
    if len(sorted_indices) < 2:
        return 0
    gaps = 0
    for i in range(1, len(sorted_indices)):
        diff = sorted_indices[i] - sorted_indices[i - 1]
        if diff > 1:
            gaps += diff - 1
    return gaps


# ── Lecturer gaps ─────────────────────────────────────────────────────

def lecturer_gap_distribution(state):
    """Return {lecturer: {day: gap_count}} and summary {lecturer: total_gaps}."""
    placed = get_placed_classes(state)
    lec_map = _build_day_slot_map(state, placed, lambda c: c.get("lecturer", ""))
    per_day = {}
    totals = {}
    for lec, days in lec_map.items():
        per_day[lec] = {}
        total = 0
        for day, indices in days.items():
            g = _count_gaps(indices)
            per_day[lec][day] = g
            total += g
        totals[lec] = total
    return per_day, totals


# ── Student idle-time ─────────────────────────────────────────────────

def _group_key(target):
    return f"{target['year']} / {target['branch']}"


def student_idle_distribution(state):
    """Return {group: {day: gap_count}} and summary {group: total_gaps}."""
    placed = get_placed_classes(state)
    group_map = {}  # group -> {day -> [slot indices]}
    for cls in placed:
        day = effective_day(cls)
        start = effective_time(cls)
        td = total_duration(cls)
        si = find_slot_index(state, start)
        if si is None or day not in state["days"]:
            continue  # placement orphaned by a removed slot/day (ST-DATA-003)
        indices = list(range(si, si + td))
        for t in cls.get("targets", []):
            gk = _group_key(t)
            group_map.setdefault(gk, {}).setdefault(day, []).extend(indices)
    for gk in group_map:
        for day in group_map[gk]:
            group_map[gk][day] = sorted(set(group_map[gk][day]))
    per_day = {}
    totals = {}
    for gk, days in group_map.items():
        per_day[gk] = {}
        total = 0
        for day, indices in days.items():
            g = _count_gaps(indices)
            per_day[gk][day] = g
            total += g
        totals[gk] = total
    return per_day, totals


# ── Room utilization ──────────────────────────────────────────────────

def room_utilization(state):
    """Return {room: utilization_pct} where 100% means every slot on every day is used."""
    placed = get_placed_classes(state)
    total_capacity = len(state["days"]) * len(state["slots"])
    if total_capacity == 0:
        return {}
    room_slots = {}  # room -> set of (day, slot_index)
    for cls in placed:
        room = classroom_of(cls)
        day = effective_day(cls)
        start = effective_time(cls)
        td = total_duration(cls)
        si = find_slot_index(state, start)
        if si is None or day not in state["days"]:
            continue  # placement orphaned by a removed slot/day (ST-DATA-003)
        # Same duration overrun as busiest_slots. Here it does not raise — a set
        # accepts any index — it silently claims hours the grid does not have,
        # so one 2-hour lesson on a one-hour day reported 200% utilization.
        for i in range(min(td, len(state["slots"]) - si)):
            room_slots.setdefault(room, set()).add((day, si + i))
    result = {}
    for room in state["classrooms"]:
        used = len(room_slots.get(room, set()))
        result[room] = round(100 * used / total_capacity, 1)
    return result


# ── Busiest days and time slots ───────────────────────────────────────

def busiest_days(state):
    """Return {day: class_count} sorted descending."""
    placed = get_placed_classes(state)
    counts = {}
    for cls in placed:
        day = effective_day(cls)
        counts[day] = counts.get(day, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def busiest_slots(state):
    """Return {slot: class_count} sorted descending."""
    placed = get_placed_classes(state)
    counts = {}
    for cls in placed:
        start = effective_time(cls)
        td = total_duration(cls)
        si = find_slot_index(state, start)
        if si is None:
            continue  # placement orphaned by a removed slot (ST-DATA-003)
        # ST-DATA-003, duration half. The `si is None` guard above covers a
        # placement whose START slot was deleted. It does not cover a block
        # whose start still exists but whose DURATION now overruns the end of
        # the day — place a 2-hour lesson at 15:00, then delete the 16:00 hour
        # in Setup. `reconcile_placements` does not catch that either (it only
        # checks membership of `placed_time`), so the class stays placed and
        # this used to index past the end and raise IndexError, out of
        # `compute_all_metrics`, out of `DashboardWidget.refresh`, and into
        # `ui/app.py`, which calls it with no `try`. The hours that do not
        # exist are simply not counted.
        for i in range(min(td, len(state["slots"]) - si)):
            s = state["slots"][si + i]
            counts[s] = counts.get(s, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


# ── Underused classrooms ─────────────────────────────────────────────

def underused_classrooms(state, threshold=15.0):
    """Return list of (room, utilization_pct) where util < threshold."""
    utils = room_utilization(state)
    return [(r, u) for r, u in sorted(utils.items(), key=lambda x: x[1]) if u < threshold]


# ── Overloaded lecturers ─────────────────────────────────────────────

def lecturer_load(state):
    """Return {lecturer: {total_slots, days_active, avg_per_day, max_day, gaps}}."""
    placed = get_placed_classes(state)
    lec_map = _build_day_slot_map(state, placed, lambda c: c.get("lecturer", ""))
    _, gap_totals = lecturer_gap_distribution(state)
    result = {}
    for lec, days in lec_map.items():
        total = sum(len(indices) for indices in days.values())
        days_active = len(days)
        max_day_slots = max(len(indices) for indices in days.values()) if days else 0
        avg = round(total / days_active, 1) if days_active else 0
        result[lec] = {
            "total_slots": total,
            "days_active": days_active,
            "avg_per_day": avg,
            "max_day": max_day_slots,
            "gaps": gap_totals.get(lec, 0),
        }
    return result


def overloaded_lecturers(state, threshold_slots=6):
    """Return lecturers whose max daily slots exceed threshold."""
    loads = lecturer_load(state)
    return [(lec, info) for lec, info in sorted(loads.items(), key=lambda x: -x[1]["max_day"])
            if info["max_day"] >= threshold_slots]


# ── Summary snapshot ──────────────────────────────────────────────────

def compute_all_metrics(state):
    """Compute all metrics at once and return a dict of results."""
    placed = get_placed_classes(state)
    lec_gaps_per_day, lec_gaps_total = lecturer_gap_distribution(state)
    stu_gaps_per_day, stu_gaps_total = student_idle_distribution(state)
    return {
        "placed_count": len(placed),
        "unplaced_count": len([c for c in state["classes"] if not c["placed"] and not c["pinned"]]),
        "total_classes": len(state["classes"]),
        "lec_gaps_per_day": lec_gaps_per_day,
        "lec_gaps_total": lec_gaps_total,
        "student_gaps_per_day": stu_gaps_per_day,
        "student_gaps_total": stu_gaps_total,
        "room_utilization": room_utilization(state),
        "busiest_days": busiest_days(state),
        "busiest_slots": busiest_slots(state),
        "underused_rooms": underused_classrooms(state),
        "lecturer_load": lecturer_load(state),
        "overloaded_lecturers": overloaded_lecturers(state),
    }
