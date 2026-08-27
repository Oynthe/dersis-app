"""Global capacity analysis — why a whole timetable cannot be built.

ST-SCHED-014. When an instance is oversubscribed, DERSİS used to report the
symptom once per class ("all remaining candidate slots are occupied", N times)
and never the cause. That reads as "the solver gave up"; the useful sentence is
"you are asking for 14 class-hours and the building only has 8 room-hours".

The distinction that matters is *provable* versus *searched*. Nothing here
solves anything: it counts demand against capacity per resource, which is
arithmetic. A bottleneck reported here cannot be fixed by rearranging lessons —
only by adding a room, hiring a lecturer, or dropping a course. That is exactly
why it is worth saying separately from a list of unplaced classes.

Deliberately one-sided: this reports only what it can *prove*. Passing capacity
checks does NOT mean the instance is satisfiable — pins, availability windows
and room requirements can make a comfortably-sized instance impossible, and
those need the solver. A false "you are oversubscribed" on a school that fits
would train users to ignore the warning, which costs more than saying nothing.
"""

from scheduler_app.logic import total_duration
from scheduler_app.models import (
    lecturer_available_at, needs_physical_room,
)
from scheduler_app.translations import tr

# Reported worst-first, and these are the only types callers must understand.
BOTTLENECK_GRID = "grid_capacity"
BOTTLENECK_LECTURER = "lecturer_hours"
BOTTLENECK_GROUP = "group_hours"


def _group_hours(cls):
    """Hours each of *cls*'s target groups is occupied for.

    A joint session teaches every target together for the whole block; a
    non-joint one splits the block into a sub-block per target, so each target
    only attends its own ``duration`` hours.
    """
    targets = cls.get("targets", []) or []
    if not cls.get("joint_session", True) and len(targets) > 1:
        return cls.get("duration", 1)
    return total_duration(cls)


def _lecturer_capacity(state, lecturer, grid_hours):
    """Hours *lecturer* can actually teach in a week.

    ``grid_hours`` for an unrestricted lecturer; fewer when they have blocked
    days or hours out, because those are hours the resource genuinely does not
    offer.
    """
    days = state.get("days", [])
    slots = state.get("slots", [])
    if not lecturer:
        return grid_hours
    return sum(1 for d in days for s in slots
               if lecturer_available_at(state, lecturer, d, s))


def diagnose_infeasibility(state, classes=None):
    """Name every global constraint that makes *state* provably unsatisfiable.

    Returns ``None`` when nothing is oversubscribed, otherwise::

        {"bottlenecks": [{"type", "entity", "required", "available",
                          "message"}, ...],   # non-empty, worst deficit first
         "message": str}                      # one sentence, the worst one

    ``required`` and ``available`` are the point of the whole exercise: a
    diagnosis without both numbers in it is an adjective, not a diagnosis.
    """
    classes = state.get("classes", []) if classes is None else classes
    days = state.get("days", []) or []
    slots = state.get("slots", []) or []
    rooms = state.get("classrooms", []) or []
    grid_hours = len(days) * len(slots)
    if not grid_hours:
        return None

    room_hours = grid_hours * len(rooms)
    physical_demand = 0
    per_lecturer = {}
    per_group = {}

    for cls in classes:
        td = total_duration(cls)
        if needs_physical_room(cls):
            physical_demand += td
        lecturer = cls.get("lecturer", "")
        if lecturer:
            per_lecturer[lecturer] = per_lecturer.get(lecturer, 0) + td
        gh = _group_hours(cls)
        for t in cls.get("targets", []) or []:
            key = (t.get("year"), t.get("branch"))
            per_group[key] = per_group.get(key, 0) + gh

    bottlenecks = []

    # Whole-building: face-to-face hours against day x slot x room cells.
    # Online/office classes are excluded — they consume no room.
    #
    # No `if rooms` guard on purpose. A state with face-to-face lessons and an
    # empty classroom list offers 0 room-hours, which is the most clear-cut
    # infeasibility there is; guarding on `rooms` would make the diagnosis go
    # quiet in exactly that case.
    if physical_demand > room_hours:
        bottlenecks.append({
            "type": BOTTLENECK_GRID,
            "entity": None,
            "required": int(physical_demand),
            "available": int(room_hours),
            "message": tr("infeasibility.grid_capacity").format(
                required=physical_demand, available=room_hours,
                days=len(days), slots=len(slots), rooms=len(rooms)),
        })

    for lecturer, hours in per_lecturer.items():
        capacity = _lecturer_capacity(state, lecturer, grid_hours)
        if hours > capacity:
            bottlenecks.append({
                "type": BOTTLENECK_LECTURER,
                "entity": lecturer,
                "required": int(hours),
                "available": int(capacity),
                "message": tr("infeasibility.lecturer_hours").format(
                    entity=lecturer, required=hours, available=capacity),
            })

    for (year, branch), hours in per_group.items():
        if hours > grid_hours:
            label = f"{year}/{branch}" if branch else str(year)
            bottlenecks.append({
                "type": BOTTLENECK_GROUP,
                "entity": label,
                "required": int(hours),
                "available": int(grid_hours),
                "message": tr("infeasibility.group_hours").format(
                    entity=label, required=hours, available=grid_hours),
            })

    if not bottlenecks:
        return None

    # Worst deficit first, then by type and entity so the order is stable for a
    # given instance rather than dependent on dict insertion.
    bottlenecks.sort(
        key=lambda b: (-(b["required"] - b["available"]),
                       b["type"], str(b["entity"] or "")))
    return {"bottlenecks": bottlenecks, "message": bottlenecks[0]["message"]}
