# Handoff — DERSİS, Phase 4 onward

Phases 0–3 are complete. Paste the block below into a **fresh** Claude Code
session run from `C:\dev\dersis-app`.

Written 2026-08-27, on `fix/phase-3-engine-hardening`.

---

```
You are continuing the DERSİS remediation (C:\dev\dersis-app), an offline PyQt6
school-timetabling desktop app (~49k lines Python, v1.0.0). A full stress-test
audit lives in `stress-test/`; you are working the phased plan in
`stress-test/14-implementation-roadmap.md`. Phases 0-3 are done: all six
Criticals are closed, and the scheduling engine now produces valid schedules.
Your job is Phase 4 — core workflow UX.

READ FIRST, in this order:
1. stress-test/PROGRESS.md            — what Phases 0-3 changed, and WHY the
                                        register's recommendations were often not
                                        sufficient. This is the highest-value file.
2. stress-test/14-implementation-roadmap.md §"Phase 4"
3. stress-test/09-ui-ux-audit.md      — proposals P1 and P2, which Phase 4 is
4. stress-test/12-findings-register.md — canonical findings
5. tests/README.md                    — the suite's conventions; follow them

STATE OF THE REPO
- Branch for your work: `fix/phase-4-workflow-ux`, cut from
  `fix/phase-3-engine-hardening` (or from `main` if that has merged).
- Suite: **456 tests — 428 pass, 28 known-defect pins, 0 failures.** Both lanes
  (`-m "not slow"` and `-m slow`) exit 0. The 28 pins are findings scheduled for
  later phases; none of them belong to Phase 3.

ENVIRONMENT
- Python: `.venv-audit/Scripts/python.exe` — never a bare `python`.
- Run tests from the repo root: `.venv-audit/Scripts/python.exe -m pytest -q`.
  CI runs `pytest -m "not slow"`. The slow lane takes ~10 min.
- `tests/conftest.py` sandboxes HOME at conftest-import time — mandatory, because
  `scheduler_app.storage` binds `~/Documents/Dersis` at import time. Never import
  scheduler_app from a conftest at module scope.
- Set `PYTHONIOENCODING=utf-8` in your shell; the app is Turkish-first and console
  encoding will otherwise mangle output and break here-doc scripts.
- Any standalone probe script needs an `if __name__ == "__main__":` guard — the
  optimizer uses multiprocessing and will otherwise fork-bomb on Windows.

PHASE 4 — WHAT TO DO
Roadmap tasks, in dependency order:
  1. **P1 — conflict-aware cells** (ST-UI-001). The grid silently HIDES one of two
     colliding lessons. The engine no longer creates collisions, but pinned ones
     still reach the grid by design, so this is still live.
  2. **P2 — one placement vocabulary everywhere** (ST-UI-002): the placed-count
     disagreement and the negative unplaced count.
  3. Room analytics read the right key (ST-UI-003 — dashboard room metrics are
     always zero).
  4. **"Why unplaced?" panel** (ST-SCHED-014, ST-UI-015). Phase 3 built the data
     for this; nothing consumes it yet. See "what Phase 3 left for you" below.
  5. Structured time-slot entry with validation (ST-UI-014).
  6. Reschedule dialog: plain-language modes + progress.

WHAT PHASE 3 LEFT SPECIFICALLY FOR YOU
The engine now produces diagnosis the UI throws away. Wiring it up is most of
task 4 and part of task 1:
  - `apply_reschedule()` returns a list of dicts, each with `name`, `class_uid`,
    `reason` and `reasons` — one per placement it could not commit.
    `ui/app.py:3019` still discards the return value entirely.
  - `result.summary['infeasibility']` is `None`, or a dict with `bottlenecks`
    (each carrying `type`, `entity`, `required`, `available`, `message`) and a
    one-sentence `message`. It names the global constraint that makes an
    instance impossible — "14 class-hours against 8 room-hours" — which is the
    one thing a list of unplaced classes can never say.
  - `result.summary['infeasible_fixed']` names pinned/locked classes whose fixed
    position clashes with another. These ARE committed (the pin is the user's
    instruction, ST-SCHED-002) and are exactly the collisions P1 must render.
  - `result.summary['repaired_conflicts']` is 0 on every preset. Non-zero means
    an engine defect, not a user problem — surface it as such if you surface it.
  - The negotiation report now describes the schedule being proposed rather than
    the pre-solve one, so its numbers agree with the dialog they sit next to.

GUARDRAILS
- Every fix needs a regression test that fails before and passes after.
- Re-run `tests/test_scheduler_invariants.py` (both lanes) after ANY optimizer or
  validator change; it is the correctness spine.
- Do not refactor the god objects (`ui/app.py`, `ui/dialogs.py`) — that is
  Phase 6. Phase 4 is allowed to touch them, but surgically.
- Preserve behaviour users rely on; the audit asks for extraction seams, not
  rewrites.
- One phase per PR, stacked on the previous branch.

LESSONS THAT WILL SAVE YOU TIME
- **The register's recommendation is a starting point, not a spec.** Across
  Phases 1-3, roughly a dozen were necessary-but-not-sufficient or actively
  wrong. Build the naive version, watch it fail, then write down why. Phase 3's
  worst example: "add an assertion/repair pass" for ST-SCHED-001 would have
  produced the identical committed timetable, moving a silent drop from one place
  to another.
- **Measure before you tune.** Phase 3's greedy budget was assumed to be a
  quality/time trade-off; measurement showed placements identical from 100 to
  100 000 iterations, so the "trade-off" was 82 seconds for nothing.
- **A test can pass against a stubbed-out optimizer**, and a "no violations"
  assertion is free on an empty schedule. Every such assertion needs an
  anti-vacuity floor.
- **Making a reader total converts a crash into a silent drop, which is worse.**
  Whenever you guard something, ask what now happens to the data that used to
  cause the exception.
- **PyQt: never connect a lambda that captures a widget to a timer or a signal
  that can outlive it.** PyQt disconnects bound-method slots when the QObject
  dies; a lambda stays connected and fires into a destroyed window — an access
  violation, not an exception. Also, `QTimer.singleShot(msec, context, slot)`
  does NOT exist in PyQt6.
- **Do not trust a plan's line numbers.** They go stale within the hour on a file
  anyone is editing. Verify every anchor by reading the file.

KNOWN GAPS LEFT BEHIND — pick these up if they touch what you are changing
1. **`changes[]` omits protected classes** (`schedule_optimizer.py`, the
   `cls_key(cls) in effective_protected_ids` skip). Harmless today because
   protected classes no longer move; if that ever changes, the move is invisible
   to the impact panel. Undo and rollback are snapshot-based and unaffected.
2. **`multi_start_time_limit` is not a global bound.** It is applied per phase —
   greedy uses `global_start + limit`, LNS restarts its own clock — so a full
   solve can take about twice the number the user was shown. That is a Phase 4
   UX decision (task 6) as much as an engine one.
3. **The dataset presets carry no `protection` levels and no pre-placed
   classes**, which is why an `improve_only` bug survived three phases of oracle
   runs. `tests/_support/dataset_gen.py` should grow a protection-bearing preset.
   Phase 7 owns testing depth.
4. **Constraint lists on non-day axes are never pruned.** A class with
   `required_classrooms=["R003"]` after R003 is deleted has zero candidate rooms
   and is permanently unplaceable, with no message anywhere. Needs its own
   finding — pruning changes semantics, since an empty allow-list means "no
   restriction". `summary['infeasibility']` does not catch it either: it counts
   capacity, not per-class reachability.
5. **Reconcile-on-open and reconcile-on-undo are deliberately NOT wired.**
   `open_file` sets `state["lecturers"] = []` for files predating the lecturers
   feature, so an unconditional reconcile there would silently unplace that
   user's entire schedule.
6. **ST-FUNC-013 for the PDF + off-grid-*day* case is still pinned.** The warning
   infrastructure exists (`models.find_off_grid_placements`,
   `_warn_about_off_grid_placements`); this may only need the PDF to render an
   appendix listing them. Likely a quick win.
7. **New user-facing strings are `en` + `tr` only** (~19 keys across Phases 0-3).
   The other 20 locales fall back to English via `tr()` — never to a raw key, so
   ST-UI-011 is not reopened — but they need a translator. Phase 5 owns the
   coverage check.
8. **The re-entrancy guard is half-covered** (from Phase 2, ST-PERF-001).
   `SolverTask.start()` is idempotent and pinned. That `SchedulerApp` disables
   Generate / undo / Excel import while a solve is running is NOT pinned — it
   needs driving the real window through a full solve.
9. **`test_drop_accounting_closes_on_a_real_solve` now asserts `0 == 0`** and is
   among the more expensive setups in the fast lane. A legitimate future
   regression guard that is currently paying for coverage it no longer provides.
10. **`Claude Code Review` CI fails** on every PR and did so before this work
    started. Unrelated; needs whoever owns that workflow's configuration.

Start by reading PROGRESS.md, confirming the suite is green
(`.venv-audit/Scripts/python.exe -m pytest -m "not slow" -q`), then cut
`fix/phase-4-workflow-ux` and begin with P1 — the grid hiding a colliding lesson
is the last place a user can be shown a timetable that is not the one they have.
```
