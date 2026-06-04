"""Comprehensive timetable quality evaluation.

Evaluates the overall quality of a complete or partial timetable.
Used by the LNS optimizer to compare solutions and by the UI to
display quality metrics. Lower score = better timetable.

Scoring priorities:
  1. Lecturer compactness (highest weight)
  2. Student group compactness (second priority)
  3. Fragmentation (isolated single-class days)
  4. Gap penalties (unnecessary idle periods)
  5. Day load balance
  6. Room switching
  7. Time-of-day quality
"""

from scheduler_app.logic import slot_index, total_duration, _active_targets
from scheduler_app.models import get_effective_room_resource_for_class
from scheduler_app.placement_scorer import DEFAULT_WEIGHTS


class TimetableScorer:
    """Evaluate complete timetable quality from a list of placements.

    Independent of ConstraintValidator — builds its own occupancy
    snapshot from the placements list so it can score any proposed
    schedule without side effects.
    """

    def __init__(self, state, weights=None):
        self.state = state
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self._total_slots = len(state.get("slots", []))
        self._total_days = len(state.get("days", []))

    def score(self, placements):
        """Compute overall timetable quality score. Lower is better.

        Args:
            placements: list of (cls, day, slot, room) tuples for all
                        placed classes (including pinned).
        Returns:
            float: quality score.
        """
        if not placements:
            return 0.0

        # Build aggregated data structures
        lect_days = {}      # lecturer -> {day -> sorted list of slot indices}
        group_days = {}     # (year, branch) -> {day -> sorted list of slot indices}
        lect_rooms = {}     # (lecturer, day) -> set of rooms
        lect_totals = {}    # lecturer -> total slot count
        group_totals = {}   # (year, branch) -> total slot count
        day_loads = {}      # day -> total slot count

        for cls, day, slot, room in placements:
            si = slot_index(self.state, slot)
            td = total_duration(cls)
            lect = cls["lecturer"]
            slots_used = list(range(si, si + td))
            effective_room = get_effective_room_resource_for_class(
                cls, room_override=room)

            if lect:
                lect_days.setdefault(lect, {}).setdefault(day, []).extend(slots_used)
                if effective_room:
                    lect_rooms.setdefault((lect, day), set()).add(effective_room)
                lect_totals[lect] = lect_totals.get(lect, 0) + td

            for t in cls["targets"]:
                gk = (t["year"], t["branch"])
                group_days.setdefault(gk, {}).setdefault(day, []).extend(slots_used)
                group_totals[gk] = group_totals.get(gk, 0) + td

            day_loads[day] = day_loads.get(day, 0) + td

        w = self.weights
        total = 0.0

        # ── 1. Lecturer compactness (highest priority) ──
        for lect, days_map in lect_days.items():
            for day, indices in days_map.items():
                total += self._gap_penalty(indices) * w["lecturer_gap"]

        # ── 2. Student group compactness ──
        for gk, days_map in group_days.items():
            for day, indices in days_map.items():
                total += self._gap_penalty(indices) * w["student_gap"]

        # ── 3. Fragmentation: penalize lecturers with isolated single-slot days ──
        for lect, days_map in lect_days.items():
            if len(days_map) <= 1:
                continue
            for day, indices in days_map.items():
                unique = set(indices)
                if len(unique) <= 1:
                    total += w["fragmentation"]

        # ── 3b. Student fragmentation ──
        for gk, days_map in group_days.items():
            if len(days_map) <= 1:
                continue
            for day, indices in days_map.items():
                unique = set(indices)
                if len(unique) <= 1:
                    total += w["fragmentation"] * 0.3

        # ── 4. Day load balance ──
        if day_loads and self._total_days > 1:
            loads = list(day_loads.values())
            avg_load = sum(loads) / len(loads)
            for load in loads:
                deviation = abs(load - avg_load)
                if deviation > avg_load * 0.5:
                    total += deviation * w["day_overload"] * 0.3

        # ── 5. Room switching ──
        for (lect, day), rooms in lect_rooms.items():
            if len(rooms) > 1:
                total += (len(rooms) - 1) * w["room_switch_penalty"]

        # ── 6. Time-of-day quality ──
        for cls, day, slot, room in placements:
            si = slot_index(self.state, slot)
            td = total_duration(cls)
            # Penalize end-of-day
            if si + td >= self._total_slots:
                total += w["end_of_day_penalty"] * 0.3
            # Reward mid-morning
            if 1 <= si <= 3:
                total -= w["midday_bonus"] * 0.1

        return total

    def score_detailed(self, placements):
        """Return a breakdown of the quality score by category.

        Returns:
            dict with category -> score contribution.
        """
        if not placements:
            return {"total": 0.0}

        lect_days = {}
        group_days = {}
        lect_rooms = {}
        day_loads = {}

        for cls, day, slot, room in placements:
            si = slot_index(self.state, slot)
            td = total_duration(cls)
            lect = cls["lecturer"]
            slots_used = list(range(si, si + td))
            effective_room = get_effective_room_resource_for_class(
                cls, room_override=room)
            if lect:
                lect_days.setdefault(lect, {}).setdefault(day, []).extend(slots_used)
                if effective_room:
                    lect_rooms.setdefault((lect, day), set()).add(effective_room)
            for t in cls["targets"]:
                gk = (t["year"], t["branch"])
                group_days.setdefault(gk, {}).setdefault(day, []).extend(slots_used)
            day_loads[day] = day_loads.get(day, 0) + td

        w = self.weights
        breakdown = {
            "lecturer_gaps": 0.0,
            "student_gaps": 0.0,
            "fragmentation": 0.0,
            "day_balance": 0.0,
            "room_switching": 0.0,
            "time_quality": 0.0,
        }

        for lect, days_map in lect_days.items():
            for day, indices in days_map.items():
                breakdown["lecturer_gaps"] += (
                    self._gap_penalty(indices) * w["lecturer_gap"])

        for gk, days_map in group_days.items():
            for day, indices in days_map.items():
                breakdown["student_gaps"] += (
                    self._gap_penalty(indices) * w["student_gap"])

        for lect, days_map in lect_days.items():
            if len(days_map) <= 1:
                continue
            for day, indices in days_map.items():
                if len(set(indices)) <= 1:
                    breakdown["fragmentation"] += w["fragmentation"]

        if day_loads and self._total_days > 1:
            loads = list(day_loads.values())
            avg_load = sum(loads) / len(loads)
            for load in loads:
                deviation = abs(load - avg_load)
                if deviation > avg_load * 0.5:
                    breakdown["day_balance"] += deviation * w["day_overload"] * 0.3

        for (lect, day), rooms in lect_rooms.items():
            if len(rooms) > 1:
                breakdown["room_switching"] += (
                    (len(rooms) - 1) * w["room_switch_penalty"])

        for cls, day, slot, room in placements:
            si = slot_index(self.state, slot)
            td = total_duration(cls)
            if si + td >= self._total_slots:
                breakdown["time_quality"] += w["end_of_day_penalty"] * 0.3
            if 1 <= si <= 3:
                breakdown["time_quality"] -= w["midday_bonus"] * 0.1

        breakdown["total"] = sum(breakdown.values())
        return breakdown

    def placement_score(self, cls, day, slot, room):
        """Score an individual placement's contribution to timetable quality.

        This is a simpler per-placement score for identifying weak placements
        in the current timetable (used by destroy strategies).
        """
        si = slot_index(self.state, slot)
        td = total_duration(cls)
        w = self.weights
        score = 0.0

        # End-of-day penalty
        if si + td >= self._total_slots:
            score += w["end_of_day_penalty"]

        # Mid-morning bonus
        if 1 <= si <= 3:
            score -= w["midday_bonus"]

        return score

    def _gap_penalty(self, slot_indices):
        """Calculate total gap slots in a list of occupied slot indices."""
        if len(slot_indices) <= 1:
            return 0
        unique = sorted(set(slot_indices))
        if len(unique) <= 1:
            return 0
        span = unique[-1] - unique[0] + 1
        occupied = len(unique)
        return max(0, span - occupied)
