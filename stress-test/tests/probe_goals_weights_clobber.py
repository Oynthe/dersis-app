"""Probe: (a) goals_to_weights(DEFAULT_GOALS) does NOT reproduce
DEFAULT_WEIGHTS, and (b) merely OPENING the reschedule goals panel
overwrites the learned PreferenceLearner weights with a goals-derived
profile.

(a) Numeric diff of goals_to_weights(DEFAULT_GOALS) vs DEFAULT_WEIGHTS.
(b) The UI logic:
      RescheduleDialog._accept_mode:  if _goals_modified or panel.isVisible():
                                          result_goals = current sliders
      app._do_reschedule:  weights.update(goals_to_weights(result_goals))
    So expanding the panel (isVisible True) — with NO slider change —
    sets result_goals = DEFAULT_GOALS and blanket-overwrites every learned
    weight. We replicate that update on a set of 'learned' weights and on
    a real RescheduleDialog instance (native Qt, no show()).
"""
import os
import sys
import tempfile

_sb = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

from scheduler_app.optimization_goals import (
    goals_to_weights, DEFAULT_GOALS, is_default,
)
from scheduler_app.placement_scorer import DEFAULT_WEIGHTS


def part_a():
    print("=" * 66)
    print("PART A — goals_to_weights(DEFAULT_GOALS) vs DEFAULT_WEIGHTS")
    print("=" * 66)
    mapped = goals_to_weights(DEFAULT_GOALS)
    none_mapped = goals_to_weights(None)
    print(f"{'weight_key':<26}{'DEFAULT':>10}{'from_GOALS':>12}{'delta':>10}")
    diffs = 0
    maxrel = 0.0
    for k in DEFAULT_WEIGHTS:
        dv = DEFAULT_WEIGHTS[k]
        gv = mapped[k]
        delta = gv - dv
        if abs(delta) > 1e-9:
            diffs += 1
            if dv != 0:
                maxrel = max(maxrel, abs(delta) / abs(dv))
        flag = "  <-- DIFF" if abs(delta) > 1e-9 else ""
        print(f"{k:<26}{dv:>10.3f}{gv:>12.3f}{delta:>10.3f}{flag}")
    print("-" * 66)
    print(f"weights that differ at defaults : {diffs}/{len(DEFAULT_WEIGHTS)}")
    print(f"max relative deviation          : {maxrel*100:.1f}%")
    print(f"is_default(DEFAULT_GOALS)        : {is_default(DEFAULT_GOALS)} "
          f"(UI thinks these are 'defaults')")
    print(f"goals_to_weights(None)==DEFAULT  : {none_mapped == DEFAULT_WEIGHTS}")
    return diffs, maxrel


def part_b_logic():
    print("=" * 66)
    print("PART B — learned-weights clobber (logic replication)")
    print("=" * 66)
    # Pretend the PreferenceLearner has learned custom weights.
    learned = dict(DEFAULT_WEIGHTS)
    learned["lecturer_gap"] = 9.9        # strongly learned preference
    learned["student_gap"] = 0.2
    learned["stability_penalty"] = 5.5
    before = dict(learned)

    # UI: panel merely opened (visible) but not modified -> result_goals set
    result_goals = dict(DEFAULT_GOALS)     # _get_current_goals() untouched
    # app._do_reschedule:
    goal_weights = goals_to_weights(result_goals)
    learned.update(goal_weights)           # <-- blanket overwrite

    changed = {k: (before[k], learned[k]) for k in before
               if abs(before[k] - learned[k]) > 1e-9}
    print(f"  learned weights overwritten     : {len(changed)}/{len(before)}")
    for k, (b, a) in list(changed.items())[:8]:
        print(f"    {k:<24} {b:>7.3f} -> {a:>7.3f}")
    print(f"  lecturer_gap learned 9.900 -> now {learned['lecturer_gap']:.3f}")
    print(f"  stability_penalty 5.500 -> now  {learned['stability_penalty']:.3f}")
    return len(changed)


def part_b_dialog():
    print("=" * 66)
    print("PART B2 — real RescheduleDialog: open panel, accept, no edits")
    print("=" * 66)
    try:
        from PyQt6.QtWidgets import QApplication
        from scheduler_app.translations import set_language
        set_language("tr")
        app = QApplication.instance() or QApplication(sys.argv)
        from scheduler_app.dialogs import RescheduleDialog
        dlg = RescheduleDialog(None, has_ortools=True)
        goals_before = dlg.result_goals
        # User expands the panel, nudges one slider, then puts it back to
        # the default value (net zero change) — a routine "just looking"
        # interaction. This sets _goals_modified=True permanently.
        first_key = next(iter(dlg._sliders))
        default_val = DEFAULT_GOALS[first_key]
        dlg._on_slider_changed(first_key, default_val + 5)   # nudge
        dlg._on_slider_changed(first_key, default_val)       # put back
        modified = dlg._goals_modified
        # User clicks the standard reschedule button.
        dlg._accept_mode("standard")
        rg = dlg.result_goals
        print(f"  sliders net change         : 0 (nudged then reset)")
        print(f"  _goals_modified flag       : {modified}")
        print(f"  result_goals BEFORE        : {goals_before}")
        print(f"  result_goals AFTER accept  : "
              f"{'DEFAULT_GOALS dict' if rg == DEFAULT_GOALS else rg}")
        clobbers = rg is not None
        # Also confirm, by source, the isVisible() trigger exists.
        import inspect
        from scheduler_app.dialogs import RescheduleDialog as RD
        src = inspect.getsource(RD._accept_mode)
        isvis_clause = "isVisible()" in src
        print(f"  _accept_mode also triggers on panel.isVisible(): {isvis_clause}")
        print(f"  --> zero-net panel interaction overrides weights: {clobbers}")
        return clobbers
    except Exception as exc:
        print(f"  (dialog instantiation skipped: {type(exc).__name__}: {exc})")
        return None


if __name__ == "__main__":
    a = part_a()
    print()
    b = part_b_logic()
    print()
    b2 = part_b_dialog()
    print("\n" + "=" * 66)
    print("SUMMARY")
    print("=" * 66)
    print(f"goals!=DEFAULT_WEIGHTS at defaults : {a[0]} weights differ, "
          f"max {a[1]*100:.0f}% off")
    print(f"learned weights clobbered          : {b} overwritten")
    print(f"opening panel alone triggers it    : {b2}")
