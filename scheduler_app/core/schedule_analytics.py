"""Schedule analytics — evaluates full timetable quality and reports
detailed metrics after rescheduling.

Provides a global quality score, per-entity breakdowns, and
actionable insights about the current timetable state.
"""

from scheduler_app.logic import find_slot_index, total_duration, _active_targets
from scheduler_app.models import get_effective_room_resource_for_class
from scheduler_app.translations import tr


class ScheduleAnalytics:
    """Comprehensive timetable quality analysis.

    Evaluates a complete schedule and produces structured analytics
    including per-lecturer and per-group metrics, global quality
    scores, and actionable insights.
    """

    def __init__(self, state):
        self.state = state
        self._total_slots = len(state.get("slots", []))
        self._total_days = len(state.get("days", []))

    def analyze(self, placements):
        """Perform full timetable analysis.

        Args:
            placements: list of (cls, day, slot, room) tuples.

        Returns:
            dict with keys: global_score, grade, lecturer_metrics,
            group_metrics, room_metrics, day_balance, insights.
        """
        if not placements:
            return self._empty_report()

        # Build data structures
        lect_data = {}   # lecturer -> {day -> [(slot_idx, duration, room, cls_name)]}
        group_data = {}  # (year, branch) -> {day -> [(slot_idx, duration)]}
        room_data = {}   # room -> {day -> [(slot_idx, duration)]}
        day_loads = {}   # day -> total_slots_used

        for cls, day, slot, room in placements:
            # ST-DATA-003. `slot_index` raises by design, and this loop used to
            # let it: one placement left pointing at an hour the user has since
            # deleted took the whole report down, and the dashboard's bare
            # `except` turned that into a 0/100 "Zayıf" verdict on a schedule
            # that had not changed. `TimetableScorer.score_detailed` already
            # skips such a placement; the analyser was the odd one out. Guarding
            # here rather than at one caller protects the live one too —
            # `workflow.analyze_schedule(state, placed)` passes its own list.
            si = find_slot_index(self.state, slot)
            if si is None or day not in self.state.get("days", []):
                continue  # off-grid placement scores nothing (ST-DATA-003)
            td = total_duration(cls)
            effective_room = get_effective_room_resource_for_class(
                cls, room_override=room)

            lect = cls["lecturer"]
            if lect:
                lect_data.setdefault(lect, {}).setdefault(day, []).append(
                    (si, td, effective_room, cls["name"]))

            for t in cls["targets"]:
                gk = (t["year"], t["branch"])
                group_data.setdefault(gk, {}).setdefault(day, []).append(
                    (si, td))

            if effective_room:
                room_data.setdefault(effective_room, {}).setdefault(day, []).append(
                    (si, td))

            day_loads[day] = day_loads.get(day, 0) + td

        # Compute metrics
        lecturer_metrics = self._analyze_lecturers(lect_data)
        group_metrics = self._analyze_groups(group_data)
        room_metrics = self._analyze_rooms(room_data)
        day_balance = self._analyze_day_balance(day_loads)
        global_score = self._compute_global_score(
            lecturer_metrics, group_metrics, day_balance)
        grade = self._score_to_grade(global_score)
        insights = self._generate_insights(
            lecturer_metrics, group_metrics, room_metrics, day_balance)

        return {
            "global_score": global_score,
            "grade": grade,
            "total_classes": len(placements),
            "lecturer_metrics": lecturer_metrics,
            "group_metrics": group_metrics,
            "room_metrics": room_metrics,
            "day_balance": day_balance,
            "insights": insights,
        }

    def compare(self, before_placements, after_placements):
        """Compare two schedules and report improvements.

        Returns:
            dict with before/after analytics and delta analysis.
        """
        before = self.analyze(before_placements)
        after = self.analyze(after_placements)

        delta_score = before["global_score"] - after["global_score"]

        # Per-metric deltas
        metric_deltas = {}
        for key in ["avg_gaps", "avg_compactness", "fragmented_days",
                     "max_gaps"]:
            b_lect = before["lecturer_metrics"].get("summary", {})
            a_lect = after["lecturer_metrics"].get("summary", {})
            if key in b_lect and key in a_lect:
                metric_deltas[f"lecturer_{key}"] = b_lect[key] - a_lect[key]

            b_grp = before["group_metrics"].get("summary", {})
            a_grp = after["group_metrics"].get("summary", {})
            if key in b_grp and key in a_grp:
                metric_deltas[f"group_{key}"] = b_grp[key] - a_grp[key]

        return {
            "before": before,
            "after": after,
            "score_improvement": delta_score,
            "grade_before": before["grade"],
            "grade_after": after["grade"],
            "metric_deltas": metric_deltas,
        }

    def _analyze_lecturers(self, lect_data):
        """Compute per-lecturer schedule quality metrics."""
        per_lecturer = {}
        total_gaps = 0
        total_fragmented = 0
        total_lecturers = len(lect_data)

        for lect, days_map in lect_data.items():
            gaps = 0
            teaching_days = len(days_map)
            fragmented_days = 0
            rooms_per_day = {}
            daily_load = {}

            for day, entries in days_map.items():
                # Calculate gaps
                all_slots = set()
                rooms_used = set()
                for si, td, room, _ in entries:
                    for off in range(td):
                        all_slots.add(si + off)
                    rooms_used.add(room)

                if len(all_slots) > 1:
                    sorted_slots = sorted(all_slots)
                    span = sorted_slots[-1] - sorted_slots[0] + 1
                    day_gaps = span - len(all_slots)
                    gaps += day_gaps

                # Fragmentation: single-slot isolated days
                if len(all_slots) == 1 and teaching_days > 1:
                    fragmented_days += 1

                rooms_per_day[day] = len(rooms_used)
                daily_load[day] = len(all_slots)

            compactness = 1.0 - (gaps / max(sum(daily_load.values()), 1))
            room_switches = sum(max(0, r - 1) for r in rooms_per_day.values())

            per_lecturer[lect] = {
                "teaching_days": teaching_days,
                "total_gaps": gaps,
                "fragmented_days": fragmented_days,
                "compactness": compactness,
                "room_switches": room_switches,
                "daily_load": daily_load,
            }

            total_gaps += gaps
            total_fragmented += fragmented_days

        avg_gaps = total_gaps / max(total_lecturers, 1)
        avg_compactness = (sum(m["compactness"] for m in per_lecturer.values())
                           / max(total_lecturers, 1))

        return {
            "per_lecturer": per_lecturer,
            "summary": {
                "total_lecturers": total_lecturers,
                "avg_gaps": avg_gaps,
                "max_gaps": max((m["total_gaps"] for m in per_lecturer.values()),
                                default=0),
                "avg_compactness": avg_compactness,
                "fragmented_days": total_fragmented,
            },
        }

    def _analyze_groups(self, group_data):
        """Compute per-group schedule quality metrics."""
        per_group = {}
        total_gaps = 0
        total_groups = len(group_data)

        for gk, days_map in group_data.items():
            gaps = 0
            daily_load = {}

            for day, entries in days_map.items():
                all_slots = set()
                for si, td in entries:
                    for off in range(td):
                        all_slots.add(si + off)

                if len(all_slots) > 1:
                    sorted_slots = sorted(all_slots)
                    span = sorted_slots[-1] - sorted_slots[0] + 1
                    day_gaps = span - len(all_slots)
                    gaps += day_gaps

                daily_load[day] = len(all_slots)

            compactness = 1.0 - (gaps / max(sum(daily_load.values()), 1))
            label = f"{gk[0]} / {gk[1]}"

            per_group[label] = {
                "teaching_days": len(days_map),
                "total_gaps": gaps,
                "compactness": compactness,
                "daily_load": daily_load,
            }
            total_gaps += gaps

        avg_gaps = total_gaps / max(total_groups, 1)
        avg_compactness = (sum(m["compactness"] for m in per_group.values())
                           / max(total_groups, 1))

        return {
            "per_group": per_group,
            "summary": {
                "total_groups": total_groups,
                "avg_gaps": avg_gaps,
                "max_gaps": max((m["total_gaps"] for m in per_group.values()),
                                default=0),
                "avg_compactness": avg_compactness,
            },
        }

    def _analyze_rooms(self, room_data):
        """Compute room utilization metrics."""
        per_room = {}
        capacity = self._total_slots * self._total_days

        for room, days_map in room_data.items():
            used_slots = sum(
                sum(td for _, td in entries)
                for entries in days_map.values())
            utilization = used_slots / max(capacity, 1)
            per_room[room] = {
                "used_slots": used_slots,
                "utilization": utilization,
                "active_days": len(days_map),
            }

        avg_util = (sum(m["utilization"] for m in per_room.values())
                    / max(len(per_room), 1))

        return {
            "per_room": per_room,
            "summary": {
                "total_rooms": len(per_room),
                "avg_utilization": avg_util,
                "capacity_per_room": capacity,
            },
        }

    def _analyze_day_balance(self, day_loads):
        """Analyze how evenly classes are distributed across days."""
        if not day_loads:
            return {"balance_score": 1.0, "per_day": {}, "std_dev": 0.0}

        loads = list(day_loads.values())
        avg = sum(loads) / len(loads)
        variance = sum((l - avg) ** 2 for l in loads) / len(loads)
        std_dev = variance ** 0.5
        # Balance score: 1.0 = perfect, 0.0 = terrible
        max_possible_std = avg if avg > 0 else 1.0
        balance_score = max(0.0, 1.0 - std_dev / max_possible_std)

        return {
            "balance_score": balance_score,
            "per_day": dict(day_loads),
            "avg_load": avg,
            "std_dev": std_dev,
        }

    def _compute_global_score(self, lecturer_metrics, group_metrics,
                              day_balance):
        """Compute a 0-100 global quality score (higher is better)."""
        lect_s = lecturer_metrics.get("summary", {})
        grp_s = group_metrics.get("summary", {})

        # Compactness contribution (0-40 points)
        lect_compact = lect_s.get("avg_compactness", 1.0) * 25
        grp_compact = grp_s.get("avg_compactness", 1.0) * 15

        # Gap penalty (0-20 points, fewer gaps = more points)
        max_acceptable_gaps = max(lect_s.get("total_lecturers", 1) * 2, 1)
        gap_ratio = min(1.0, lect_s.get("avg_gaps", 0) / max_acceptable_gaps)
        gap_score = (1.0 - gap_ratio) * 20

        # Balance contribution (0-10 points)
        balance = day_balance.get("balance_score", 1.0) * 10

        # Fragmentation penalty (0-10 points)
        frag_count = lect_s.get("fragmented_days", 0)
        frag_score = max(0, 10 - frag_count * 2)

        return min(100.0, lect_compact + grp_compact + gap_score +
                   balance + frag_score)

    def _score_to_grade(self, score):
        """Convert numeric score to letter grade."""
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"

    def _generate_insights(self, lecturer_metrics, group_metrics,
                           room_metrics, day_balance):
        """Generate actionable insights about the timetable."""
        insights = []
        lect_s = lecturer_metrics.get("summary", {})
        grp_s = group_metrics.get("summary", {})

        # Lecturer gap insights
        if lect_s.get("avg_gaps", 0) > 1.5:
            insights.append({
                "type": "warning",
                "category": "lecturer_gaps",
                "message": tr("analytics.high_lecturer_gaps").format(
                    v=lect_s["avg_gaps"]),
            })
        elif lect_s.get("avg_gaps", 0) < 0.5:
            insights.append({
                "type": "success",
                "category": "lecturer_gaps",
                "message": tr("analytics.compact_lecturer_schedules").format(
                    v=lect_s["avg_gaps"]),
            })

        # Student gap insights
        if grp_s.get("avg_gaps", 0) > 1.5:
            insights.append({
                "type": "warning",
                "category": "student_gaps",
                "message": tr("analytics.significant_student_idle").format(
                    v=grp_s["avg_gaps"]),
            })
        elif grp_s.get("avg_gaps", 0) < 0.5:
            insights.append({
                "type": "success",
                "category": "student_gaps",
                "message": tr("analytics.compact_student_schedules").format(
                    v=grp_s["avg_gaps"]),
            })

        # Fragmentation
        frag = lect_s.get("fragmented_days", 0)
        if frag > 3:
            insights.append({
                "type": "warning",
                "category": "fragmentation",
                "message": tr("analytics.fragmented_lecturer_days").format(
                    n=frag),
            })

        # Day balance
        if day_balance.get("balance_score", 1.0) < 0.6:
            insights.append({
                "type": "warning",
                "category": "day_balance",
                "message": tr("analytics.uneven_class_load"),
            })
        elif day_balance.get("balance_score", 1.0) > 0.85:
            insights.append({
                "type": "success",
                "category": "day_balance",
                "message": tr("analytics.balanced_class_load"),
            })

        # Room utilization
        room_s = room_metrics.get("summary", {})
        if room_s.get("avg_utilization", 0) > 0.8:
            insights.append({
                "type": "info",
                "category": "room_utilization",
                "message": tr("analytics.high_room_utilization").format(
                    v=room_s["avg_utilization"]),
            })

        # Worst lecturers
        per_lect = lecturer_metrics.get("per_lecturer", {})
        worst = sorted(per_lect.items(),
                       key=lambda x: x[1]["total_gaps"], reverse=True)
        if worst and worst[0][1]["total_gaps"] > 3:
            name, m = worst[0]
            insights.append({
                "type": "info",
                "category": "lecturer_specific",
                "message": tr("analytics.most_gaps_lecturer").format(
                    name=name, n=m["total_gaps"]),
            })

        return insights

    def _empty_report(self):
        return {
            "global_score": 0.0,
            "grade": "F",
            "total_classes": 0,
            "lecturer_metrics": {"per_lecturer": {}, "summary": {}},
            "group_metrics": {"per_group": {}, "summary": {}},
            "room_metrics": {"per_room": {}, "summary": {}},
            "day_balance": {"balance_score": 0.0, "per_day": {}},
            "insights": [],
        }

    def format_report(self, analytics):
        """Format analytics dict into a human-readable text report."""
        lines = []
        lines.append(tr("analytics.quality_report"))
        lines.append("=" * 40)
        lines.append(f"{tr('labels.global_score')}: {analytics['global_score']:.0f}/100 "
                     f"({tr('labels.grade')}: {analytics['grade']})")
        lines.append(f"{tr('analytics.total_classes')}: {analytics['total_classes']}")
        lines.append("")

        # Lecturer summary
        ls = analytics["lecturer_metrics"].get("summary", {})
        if ls:
            lines.append(tr("analytics.lecturer_schedule_quality"))
            lines.append("-" * 30)
            lines.append(f"  {tr('analytics.average_compactness')}: {ls.get('avg_compactness', 0):.0%}")
            lines.append(f"  {tr('analytics.average_gaps')}: {ls.get('avg_gaps', 0):.1f}")
            lines.append(f"  {tr('analytics.fragmented_days')}: {ls.get('fragmented_days', 0)}")
            lines.append("")

        # Group summary
        gs = analytics["group_metrics"].get("summary", {})
        if gs:
            lines.append(tr("analytics.student_schedule_quality"))
            lines.append("-" * 30)
            lines.append(f"  {tr('analytics.average_compactness')}: {gs.get('avg_compactness', 0):.0%}")
            lines.append(f"  {tr('analytics.average_gaps')}: {gs.get('avg_gaps', 0):.1f}")
            lines.append("")

        # Day balance
        db = analytics.get("day_balance", {})
        if db.get("per_day"):
            lines.append(tr("dashboard.day_balance"))
            lines.append("-" * 30)
            for day, load in db["per_day"].items():
                lines.append(f"  {day}: {load} {tr('labels.slots')}")
            lines.append(f"  {tr('analytics.balance_score')}: {db.get('balance_score', 0):.0%}")
            lines.append("")

        # Insights
        if analytics.get("insights"):
            lines.append(tr("analytics.insights"))
            lines.append("-" * 30)
            for insight in analytics["insights"]:
                icon = {"success": "+", "warning": "!", "info": "*"}.get(
                    insight["type"], "-")
                lines.append(f"  [{icon}] {insight['message']}")

        return "\n".join(lines)
