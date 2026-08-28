"""Explainable optimization helpers for placement and reschedule feedback."""

import re

from scheduler_app.translations import TRANSLATIONS, tr


_COMPONENT_INFO = {
    "lecturer_gap": {
        "label_key": "explanation.component.lecturer_gap.label",
        "positive_key": "explanation.component.lecturer_gap.positive",
    },
    "lecturer_cluster": {
        "label_key": "explanation.component.lecturer_cluster.label",
        "negative_key": "explanation.component.lecturer_cluster.negative",
    },
    "student_gap": {
        "label_key": "explanation.component.student_gap.label",
        "positive_key": "explanation.component.student_gap.positive",
    },
    "student_cluster": {
        "label_key": "explanation.component.student_cluster.label",
        "negative_key": "explanation.component.student_cluster.negative",
    },
    "fragmentation": {
        "label_key": "explanation.component.fragmentation.label",
        "positive_key": "explanation.component.fragmentation.positive",
    },
    "day_overload": {
        "label_key": "explanation.component.day_overload.label",
        "positive_key": "explanation.component.day_overload.positive",
    },
    "time_quality": {
        "label_key": "explanation.component.time_quality.label",
        "positive_key": "explanation.component.time_quality.positive",
        "negative_key": "explanation.component.time_quality.negative",
    },
    "room_switch": {
        "label_key": "explanation.component.room_switch.label",
        "positive_key": "explanation.component.room_switch.positive",
    },
    "tidiness": {
        "label_key": "explanation.component.tidiness.label",
    },
    # ST-UI-011. `PlacementScorer` writes a "stability" component whenever a
    # candidate moves a class off its previous placement, and this table had no
    # entry for it — so `tr(info.get("label_key", key))` fell through to
    # `tr("stability")`, which resolves to nothing and renders the bare Python
    # identifier `stability` as a user-facing label, in all 22 languages.
    #
    # Latent rather than live today: the only scorer constructed with
    # `previous_placements` is the optimizer's (schedule_optimizer.py), and the
    # only reachable caller of `explain_placement` is
    # `logic.score_placement_explained`, which builds its own scorer without
    # them. So the branch cannot currently fire through the UI. It is filled in
    # anyway because the gap is one wiring change away from being visible, and
    # because a missing entry here degrades to a raw key rather than to
    # anything a reader would notice.
    "stability": {
        "label_key": "explanation.component.stability.label",
        "positive_key": "explanation.component.stability.positive",
    },
}

_REASON_CATEGORY_KEYS = {
    "room_conflicts": ("validation.room_occupied",),
    "lecturer_conflicts": (
        "validation.lecturer_busy",
        "validation.lecturer_unavailable",
    ),
    "group_conflicts": ("validation.group_busy",),
    "capacity_violations": ("validation.room_capacity",),
    "constraint_violations": (
        "validation.duration_overflow",
        "validation.day_not_allowed",
        "validation.day_not_allowed_simple",
        "validation.day_excluded",
        "validation.day_excluded_simple",
        "validation.time_not_allowed",
        "validation.time_excluded",
        "validation.time_excluded_simple",
        "validation.room_not_required",
        "validation.room_not_required_simple",
        "validation.room_excluded",
        "validation.room_excluded_simple",
    ),
}


def _translation_template_matches(text, key):
    for lang_dict in TRANSLATIONS.values():
        template = lang_dict.get(key)
        if not template:
            continue
        pattern = re.escape(template)
        pattern = re.sub(r"\\\{[^}]*\\\}", r".+?", pattern)
        if re.fullmatch(pattern, text):
            return True
    return False


def _reason_in_category(reason, category):
    return any(_translation_template_matches(reason, key) for key in _REASON_CATEGORY_KEYS[category])


class ExplanationEngine:
    """Converts scheduling scores and conflicts into localized explanations."""

    def explain_placement(self, cls, day, slot, room, breakdown):
        pros = []
        cons = []
        components = []
        total = sum(breakdown.values())

        for key, value in sorted(breakdown.items(), key=lambda item: item[1]):
            info = _COMPONENT_INFO.get(key, {})
            label = tr(info.get("label_key", key))

            if value < -0.01:
                desc_key = info.get("negative_key")
                desc = (
                    tr(desc_key).format(v=abs(value))
                    if desc_key
                    else tr("explanation.bonus_of").format(v=abs(value))
                )
                pros.append(f"{label}: {desc}")
            elif value > 0.01:
                desc_key = info.get("positive_key")
                desc = (
                    tr(desc_key).format(v=value)
                    if desc_key
                    else tr("explanation.penalty_of").format(v=value)
                )
                cons.append(f"{label}: {desc}")

            component_desc_key = info.get("positive_key" if value > 0 else "negative_key")
            component_desc = (
                tr(component_desc_key).format(v=abs(value))
                if component_desc_key
                else ""
            )
            components.append((label, value, component_desc))

        if total < -1.0:
            quality = tr("quality.excellent_placement")
        elif total < 0:
            quality = tr("quality.good_placement")
        elif total < 2.0:
            quality = tr("quality.acceptable_placement")
        elif total < 5.0:
            quality = tr("quality.below_average_placement")
        else:
            quality = tr("quality.poor_placement")

        summary = f"{quality} ({total:+.2f})"
        if pros and not cons:
            summary += f" — {pros[0]}"
        elif cons and not pros:
            summary += f" — {cons[0]}"

        return {
            "summary": summary,
            "pros": pros,
            "cons": cons,
            "score": total,
            "components": components,
        }

    def explain_rejection(self, cls, day, slot, room, reasons):
        categorized = {
            "room_conflicts": [],
            "lecturer_conflicts": [],
            "group_conflicts": [],
            "capacity_violations": [],
            "constraint_violations": [],
        }

        for reason in reasons:
            if _reason_in_category(reason, "capacity_violations"):
                categorized["capacity_violations"].append(reason)
            elif _reason_in_category(reason, "room_conflicts"):
                categorized["room_conflicts"].append(reason)
            elif _reason_in_category(reason, "lecturer_conflicts"):
                categorized["lecturer_conflicts"].append(reason)
            elif _reason_in_category(reason, "group_conflicts"):
                categorized["group_conflicts"].append(reason)
            else:
                categorized["constraint_violations"].append(reason)

        parts = []
        if categorized["room_conflicts"]:
            parts.append(tr("conflicts.room"))
        if categorized["lecturer_conflicts"]:
            parts.append(tr("conflicts.lecturer"))
        if categorized["group_conflicts"]:
            parts.append(tr("conflicts.student_group"))
        if categorized["capacity_violations"]:
            parts.append(tr("negotiation.room_capacity_insufficient"))
        if categorized["constraint_violations"]:
            parts.append(tr("conflicts.constraint_violation"))

        summary = tr("errors.cannot_place_detailed").format(
            name=cls["name"],
            day=day,
            slot=slot,
            room=room,
            reasons=", ".join(parts) if parts else tr("labels.unknown"),
        )

        return {
            "summary": summary,
            "categorized_reasons": categorized,
            "reasons": reasons,
        }

    def explain_auto_placement(
        self,
        cls,
        chosen_day,
        chosen_slot,
        chosen_room,
        chosen_breakdown,
        alternatives=None,
    ):
        chosen_explained = self.explain_placement(
            cls, chosen_day, chosen_slot, chosen_room, chosen_breakdown
        )

        result = {
            "summary": tr("status.placed_detailed").format(
                name=cls["name"],
                day=chosen_day,
                slot=chosen_slot,
                room=chosen_room,
            ),
            "quality": chosen_explained["summary"],
            "pros": chosen_explained["pros"],
            "cons": chosen_explained["cons"],
            "score": chosen_explained["score"],
        }

        if alternatives:
            alt_summaries = []
            for day, slot, room, score, _ in alternatives[:3]:
                diff = score - chosen_explained["score"]
                alt_summaries.append(
                    tr("explanation.alternative_summary").format(
                        day=day,
                        slot=slot,
                        room=room,
                        score=score,
                        diff=diff,
                        worse=tr("quality.worse"),
                    )
                )
            result["alternatives_considered"] = alt_summaries

        return result

    def explain_reschedule_improvements(self, summary):
        imp = summary.get("improvement", {})
        before = summary.get("before", {})
        after = summary.get("after", {})

        improvements = []
        degradations = []

        metric_labels = {
            "lecturer_gaps": tr("analytics.lecturer_gaps"),
            "student_gaps": tr("analytics.student_idle_periods"),
            "fragmentation": tr("analytics.schedule_fragmentation"),
            "day_balance": tr("analytics.day_load_balance"),
            "room_switching": tr("analytics.room_switching"),
            "time_quality": tr("analytics.time_slot_quality"),
        }

        for key, label in metric_labels.items():
            delta = imp.get(key, 0)
            if delta > 0.1:
                before_val = before.get(key, 0)
                after_val = after.get(key, 0)
                improvements.append(
                    {
                        "metric": label,
                        "before": before_val,
                        "after": after_val,
                        "improvement": delta,
                        "description": tr("explanation.metric_delta").format(
                            label=label,
                            before=before_val,
                            after=after_val,
                            delta=delta,
                        ),
                    }
                )
            elif delta < -0.1:
                degradations.append(
                    {
                        "metric": label,
                        "change": delta,
                        "description": tr("explanation.metric_increased").format(
                            label=label,
                            delta=abs(delta),
                        ),
                    }
                )

        total_imp = imp.get("total", 0)
        before_total = before.get("total", 0)
        after_total = after.get("total", 0)

        if total_imp > 1.0:
            quality_verdict = tr("analytics.significant_improvement")
        elif total_imp > 0.1:
            quality_verdict = tr("analytics.moderate_improvement")
        elif total_imp > -0.1:
            quality_verdict = tr("analytics.no_significant_change")
        else:
            quality_verdict = tr("analytics.quality_decreased")

        # Surface budget exhaustion warning
        greedy_stats = summary.get("greedy_stats", {})
        budget_exhausted = greedy_stats.get("budget_exhausted", False)
        budget_note = None
        if budget_exhausted:
            used = greedy_stats.get("iterations_used", 0)
            limit = greedy_stats.get("max_iterations", 0)
            budget_note = (
                f"Optimization budget exhausted ({used}/{limit} iterations). "
                "Results may improve with more iterations."
            )

        # Surface LNS strategy performance
        lns_stats = summary.get("lns_strategy_stats", [])
        strategy_notes = []
        for st in lns_stats:
            name = st.get("name", "")
            total = st.get("uses", 0)
            successes = st.get("successes", 0)
            if total > 0:
                pct = round(100 * successes / total)
                strategy_notes.append(
                    f"{name}: {successes}/{total} successful ({pct}%)")

        return {
            "verdict": quality_verdict,
            "total_before": before_total,
            "total_after": after_total,
            "total_improvement": total_imp,
            "improvements": improvements,
            "degradations": degradations,
            "classes_moved": summary.get("classes_moved", 0),
            "classes_placed": summary.get("classes_placed", 0),
            "classes_unplaced": summary.get("classes_unplaced", 0),
            "engine": self._engine_description(summary),
            "budget_exhausted": budget_exhausted,
            "budget_note": budget_note,
            "lns_strategy_notes": strategy_notes,
        }

    def _engine_description(self, summary):
        runs = summary.get("runs_completed", 1)
        elapsed = summary.get("total_time", 0)
        parts = [tr("explanation.engine_runs").format(n=runs)]
        if summary.get("cpsat_used"):
            status = summary.get("cpsat_status_label") or summary.get("cpsat_status", "")
            parts.append(tr("explanation.engine_cpsat").format(status=status))
        parts.append(tr("explanation.elapsed_seconds").format(seconds=elapsed))
        return " + ".join(parts)

    def explain_change(self, cls, old_day, old_slot, old_room,
                       new_day, new_slot, new_room,
                       old_breakdown=None, new_breakdown=None):
        """Explain why a class was moved during reschedule.

        Args:
            old_breakdown: Score breakdown at old placement.
            new_breakdown: Score breakdown at new placement.

        Returns:
            dict with primary_reason, all_improvements, old_score, new_score.
        """
        if not old_breakdown or not new_breakdown:
            return {"primary_reason": None, "all_improvements": []}

        old_score = sum(old_breakdown.values())
        new_score = sum(new_breakdown.values())
        improvements = []
        for key in old_breakdown:
            old_val = old_breakdown.get(key, 0)
            new_val = new_breakdown.get(key, 0)
            delta = old_val - new_val
            if delta > 0.3:
                info = _COMPONENT_INFO.get(key, {})
                label = info.get("label_key", key)
                improvements.append({
                    "component": key,
                    "label": label,
                    "improvement": round(delta, 2),
                })
        improvements.sort(key=lambda x: x["improvement"], reverse=True)
        return {
            "old_score": round(old_score, 2),
            "new_score": round(new_score, 2),
            "primary_reason": improvements[0] if improvements else None,
            "all_improvements": improvements,
        }
