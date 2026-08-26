"""PROBE 2 + 4: ExplanationEngine methods and ScheduleAnalytics.compare /
format_report — mapped as UNREACHABLE. Confirm reachability by grep-derived
facts (printed) and, crucially, test whether the functions actually WORK when
called: do they crash, or produce sensible text?

Reachability (from static grep, see report):
  explain_placement            -> REACHABLE (logic.score_placement_explained -> workflow.auto_place -> app.py)
  explain_reschedule_improvements -> REACHABLE (workflow.reschedule -> app.py)
  explain_rejection            -> DEAD (no callers)
  explain_auto_placement       -> DEAD (no callers)
  explain_change               -> DEAD (no callers)
  ScheduleAnalytics.compare    -> DEAD (no callers)
  ScheduleAnalytics.format_report -> DEAD (no callers)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "tests")))
import _fixtures.sandbox  # noqa: F401

import traceback
from scheduler_app.translations import set_language
from scheduler_app.core.explanation_engine import ExplanationEngine
from scheduler_app.core.schedule_analytics import ScheduleAnalytics
from scheduler_app.core.models import new_state, new_class, mark_placed, effective_room
from scheduler_app.core.models import effective_day, effective_time
from scheduler_app.core.constraint_validator import ConstraintValidator
from scheduler_app.core.placement_scorer import PlacementScorer
from scheduler_app.core.models import cls_key


def small_state():
    st = new_state()
    st["days"] = ["monday", "tuesday", "wednesday"]
    st["slots"] = ["09:00", "10:00", "11:00", "12:00"]
    st["classrooms"] = ["R1", "R2"]
    st["classroom_capacities"] = {"R1": 0, "R2": 0}
    st["lecturers"] = ["Prof-A"]
    st["years"] = {"Year-1": ["A"]}
    for i, (d, s, r) in enumerate([("monday","09:00","R1"),("monday","11:00","R2"),
                                    ("tuesday","09:00","R1")]):
        c = new_class(); c["name"] = f"C{i+1}"; c["lecturer"] = "Prof-A"
        c["targets"] = [{"year":"Year-1","branch":"A"}]; c["duration"] = 1
        mark_placed(c, d, s, r); st["classes"].append(c)
    return st


def try_call(label, fn):
    try:
        out = fn()
        print(f"[OK]   {label}")
        return out
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def main():
    set_language("tr")
    st = small_state()
    eng = ExplanationEngine()
    target = st["classes"][2]  # C3
    # Build a real breakdown via PlacementScorer.score_explained
    val = ConstraintValidator(st, exclude_ids={cls_key(target)})
    scorer = PlacementScorer(st, val)
    score, breakdown = scorer.score_explained(target, "tuesday", "10:00", "R1")
    print(f"score_explained -> score={score:.3f} breakdown={breakdown}\n")

    print("=== ExplanationEngine (does each method run + produce text?) ===")
    r1 = try_call("explain_placement",
                  lambda: eng.explain_placement(target, "tuesday", "10:00", "R1", breakdown))
    if r1:
        print("   summary:", r1["summary"])
        print("   pros:", r1["pros"], "cons:", r1["cons"])

    r2 = try_call("explain_rejection (DEAD)",
                  lambda: eng.explain_rejection(target, "monday", "09:00", "R1",
                                                ["Prof-A is busy", "Room R1 occupied"]))
    if r2:
        print("   summary:", r2["summary"])

    r3 = try_call("explain_auto_placement (DEAD)",
                  lambda: eng.explain_auto_placement(
                      target, "tuesday", "10:00", "R1", breakdown,
                      alternatives=[("monday","09:00","R2", 3.2, {}),
                                    ("wednesday","12:00","R1", 5.5, {})]))
    if r3:
        print("   summary:", r3["summary"])
        print("   alternatives_considered:", r3.get("alternatives_considered"))

    # explain_reschedule_improvements needs a summary dict
    summary = {
        "improvement": {"lecturer_gaps": 2.0, "student_gaps": -0.5, "total": 1.5},
        "before": {"lecturer_gaps": 5.0, "student_gaps": 2.0, "total": 10.0},
        "after": {"lecturer_gaps": 3.0, "student_gaps": 2.5, "total": 8.5},
        "classes_moved": 3, "classes_placed": 10, "classes_unplaced": 0,
        "runs_completed": 5, "total_time": 1.23,
        "greedy_stats": {"budget_exhausted": True, "iterations_used": 5000,
                         "max_iterations": 5000},
        "lns_strategy_stats": [{"name": "random", "uses": 10, "successes": 4}],
    }
    r4 = try_call("explain_reschedule_improvements",
                  lambda: eng.explain_reschedule_improvements(summary))
    if r4:
        print("   verdict:", r4["verdict"])
        print("   improvements:", [i["metric"] for i in r4["improvements"]])
        print("   degradations:", [d["metric"] for d in r4["degradations"]])
        print("   budget_note:", r4["budget_note"])

    old_bd = {"lecturer_gap": 5.0, "student_gap": 2.0}
    new_bd = {"lecturer_gap": 1.0, "student_gap": 2.5}
    r5 = try_call("explain_change (DEAD)",
                  lambda: eng.explain_change(target, "monday","09:00","R1",
                                             "tuesday","10:00","R1", old_bd, new_bd))
    if r5:
        print("   primary_reason:", r5["primary_reason"])

    # ── PROBE 4: ScheduleAnalytics compare / format_report ──
    print("\n=== ScheduleAnalytics.compare / format_report (DEAD) ===")
    sa = ScheduleAnalytics(st)
    before_p = [(c, effective_day(c), effective_time(c), effective_room(c))
                for c in st["classes"]]
    # after: move C1 to be adjacent (reduce gap)
    after_st = small_state()
    after_st["classes"][1]["placed_time"] = "10:00"  # move C2 monday 11->10
    after_p = [(c, effective_day(c), effective_time(c), effective_room(c))
               for c in after_st["classes"]]

    cmp = try_call("compare", lambda: sa.compare(before_p, after_p))
    if cmp:
        print(f"   score_improvement={cmp['score_improvement']:.3f} "
              f"grade {cmp['grade_before']}->{cmp['grade_after']}")
        print("   metric_deltas:", cmp["metric_deltas"])

    rep = try_call("format_report", lambda: sa.format_report(sa.analyze(before_p)))
    if rep:
        print("   --- report text (first 12 lines) ---")
        for line in rep.split("\n")[:12]:
            print("   |", line)


if __name__ == "__main__":
    main()
