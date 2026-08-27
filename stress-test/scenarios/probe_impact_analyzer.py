"""PROBE 3: schedule_impact_analyzer capture_snapshot / analyze_impact.

Tests with deepdiff present (confirmed installed):
  - No change -> NO_RESCHEDULE_NEEDED
  - Soft change (participants) -> RESCHEDULE_RECOMMENDED
  - Hard change that breaks a placement (shrink allowed_days so a placed class
    violates its own constraint) -> RESCHEDULE_REQUIRED
  - Cosmetic change (name) -> NO_RESCHEDULE_NEEDED
Also confirms deepdiff is actually imported (not the None fallback path).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "tests")))
import _fixtures.sandbox  # noqa: F401

import copy
from scheduler_app.core import schedule_impact_analyzer as sia
from scheduler_app.core.models import new_state, new_class, mark_placed


def build_state():
    st = new_state()
    st["days"] = ["monday", "tuesday", "wednesday"]
    st["slots"] = ["09:00", "10:00", "11:00"]
    st["classrooms"] = ["R1", "R2"]
    st["classroom_capacities"] = {"R1": 0, "R2": 0}
    st["lecturers"] = ["Prof-A"]
    st["years"] = {"Year-1": ["A"]}
    c1 = new_class(); c1["name"] = "C1"; c1["lecturer"] = "Prof-A"
    c1["targets"] = [{"year": "Year-1", "branch": "A"}]; c1["duration"] = 1
    mark_placed(c1, "monday", "09:00", "R1")
    c2 = new_class(); c2["name"] = "C2"; c2["lecturer"] = "Prof-A"
    c2["targets"] = [{"year": "Year-1", "branch": "A"}]; c2["duration"] = 1
    mark_placed(c2, "tuesday", "10:00", "R2")
    st["classes"] = [c1, c2]
    return st


def run(label, mutate):
    st = build_state()
    before = sia.capture_snapshot(st)
    mutate(st)
    after = sia.capture_snapshot(st)
    result = sia.analyze_impact(before, after, st)
    print(f"--- {label} ---")
    print(f"  level = {result.level.value}")
    print(f"  changed_fields = {result.changed_fields}")
    print(f"  affected_entities = {result.affected_entities}")
    print(f"  hard_violations = {result.hard_violations}")
    print(f"  soft_impact_reasons = {result.soft_impact_reasons}")
    return result


def main():
    print("deepdiff present in analyzer:", sia.DeepDiff is not None)
    print()

    # 1. No change
    r_none = run("NO CHANGE", lambda st: None)

    # 2. Soft change: participants
    def soft(st):
        st["classes"][0]["participants"] = 40
    r_soft = run("SOFT CHANGE (participants 0->40)", soft)

    # 3. Hard change: restrict C1 allowed_days to exclude its placed day (monday)
    def hard(st):
        st["classes"][0]["allowed_days"] = ["wednesday"]  # C1 is placed monday!
    r_hard = run("HARD CHANGE (C1 allowed_days -> [wednesday], placed monday)", hard)

    # 4. Cosmetic change: name (ignored field)
    def cosmetic(st):
        st["classes"][0]["name"] = "C1-renamed"
    r_cos = run("COSMETIC CHANGE (name only)", cosmetic)

    # 5. Hard structural: remove a day entirely
    def remday(st):
        st["days"] = ["monday", "tuesday"]  # remove wednesday
    r_remday = run("STRUCTURAL (remove wednesday from days)", remday)

    print()
    print("=== VERDICT ===")
    print("no-change == NO_RESCHEDULE_NEEDED:",
          r_none.level is sia.ImpactLevel.NO_RESCHEDULE_NEEDED)
    print("soft == RESCHEDULE_RECOMMENDED:",
          r_soft.level is sia.ImpactLevel.RESCHEDULE_RECOMMENDED)
    print("hard (broken placement) == RESCHEDULE_REQUIRED:",
          r_hard.level is sia.ImpactLevel.RESCHEDULE_REQUIRED)
    print("cosmetic == NO_RESCHEDULE_NEEDED:",
          r_cos.level is sia.ImpactLevel.NO_RESCHEDULE_NEEDED)


if __name__ == "__main__":
    main()
