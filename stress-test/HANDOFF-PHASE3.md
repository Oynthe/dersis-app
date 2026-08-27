# Handoff — DERSİS, Phase 3 onward

Phases 0, 1 and 2 are complete. Paste the block below into a **fresh** Claude Code
session run from `C:\dev\dersis-app`.

Written 2026-08-27, at commit `1aaa33f` on `fix/phase-2-performance`.

---

```
You are continuing the DERSİS remediation (C:\dev\dersis-app), an offline PyQt6
school-timetabling desktop app (~49k lines Python, v1.0.0). A full stress-test
audit lives in `stress-test/`; you are working the phased plan in
`stress-test/14-implementation-roadmap.md`. Phases 0-2 are done and **all six
Criticals are closed**. Your job is Phase 3 — scheduling engine hardening.

READ FIRST, in this order:
1. stress-test/PROGRESS.md            — what Phases 0/1/2 changed, and WHY the
                                        register's recommendations were often not
                                        sufficient. This is the highest-value file.
2. stress-test/14-implementation-roadmap.md §"Phase 3"
3. stress-test/12-findings-register.md — canonical findings; each detail block has
                                        file:line, root cause, recommendation
4. tests/README.md                    — the suite's conventions; follow them

STATE OF THE REPO
- Three stacked PRs, all pushed, none merged yet:
    #7  fix/phase-0-test-scaffold      -> main    (CI green on Ubuntu)
    #8  fix/phase-1-data-correctness   -> phase-0
    #9  fix/phase-2-performance        -> phase-1
  Branch for your work: `fix/phase-3-engine-hardening`, cut from
  `fix/phase-2-performance`. If the stack has since merged, cut from `main`.
- Suite: **339 tests — 307 pass, 32 known-defect pins, 0 failures.** Both lanes
  (`-m "not slow"` and `-m slow`) exit 0.

ENVIRONMENT
- Python: `.venv-audit/Scripts/python.exe` — never a bare `python`.
- Run tests from the repo root: `.venv-audit/Scripts/python.exe -m pytest -q`.
  CI runs `pytest -m "not slow"`. The slow lane takes ~10 min; run it before you
  commit anything that touches the engine.
- `tests/conftest.py` sandboxes HOME at conftest-import time — mandatory, because
  `scheduler_app.storage` binds `~/Documents/Dersis` at import time. Never import
  scheduler_app from a conftest at module scope.
- Set `PYTHONIOENCODING=utf-8` in your shell; the app is Turkish-first and console
  encoding will otherwise mangle output and break here-doc scripts.

PHASE 3 — WHAT TO DO
Roadmap tasks, in dependency order:
  1. Unify to ONE hard-constraint validator; route every path through it.
     (ST-ARCH-004: four divergent implementations exist, and production
     drag-and-drop uses the weakest one. ST-SCHED-007.)
  2. Fix the optimizer's internal occupancy bookkeeping so RAW output contains no
     double-bookings; assert-and-repair. (ST-SCHED-001 — the last big correctness
     hole. ST-SCHED-010: occupancy maps are ref-count-free sets, so temporarily
     removing one class erases a co-located class's occupancy.)
  3. CP-SAT: model lecturer availability across a class's FULL duration, and
     honour all protection levels, not just LOCKED. (ST-SCHED-005/006.)
  4. Bound and de-recurse greedy construction (ST-PERF-004/008, ST-SCHED-012 —
     1200 classes currently raises RecursionError).
  5. Surface dropped classes and a global infeasibility reason (ST-SCHED-014).
  6. Small: remove the dead `neighbor_impact` term, fix `find_conflicts`
     returning [] for a placement `check_placement` rejects (ST-SCHED-015/009).

COMPLETION CRITERIA (from the roadmap): the oracle reports zero hard violations
in RAW optimizer output on all presets, not merely post-drop; CP-SAT respects
availability across duration and every protection level; the 1200-class
pathological preset completes without RecursionError.

THE PINS ARE YOUR SCOREBOARD. `tests/test_scheduler_invariants.py` carries
strict-xfail pins for ST-SCHED-001. When you fix it they turn RED (an xpass under
`strict=True`) — that is the signal, not a regression. Delete the marker and say
so in the commit. Enumerate every pin with:
    .venv-audit/Scripts/python.exe -m pytest -m "not slow" -rx -q

HOW THE PREVIOUS PHASES WERE RUN, AND WHY IT WORKED
Each phase used one Workflow fan-out: N agents each own ONE new test file and
also produce a concrete implementation plan; N verifier agents then try to prove
both wrong. The implementer (you) writes all production code, so tests keep their
fail-before/pass-after guarantee. This caught, among other things, three tests
that passed against an optimizer stubbed to return nothing, and a "cancel" that
merely stopped the UI listening while the solver burned CPU.

Two rules that earned their keep:
- Agents must not touch `scheduler_app/**`. One file each, no exceptions.
- Verifiers must run the code — mutation-test the fix, run the module 3-5 times,
  and once under CPU load if it asserts on timing.

LESSONS THAT WILL SAVE YOU TIME
- **The register's recommendation is a starting point, not a spec.** In Phases 1
  and 2, seven of them were necessary-but-not-sufficient or actively wrong. Build
  the naive version, watch it fail, then write down why. Examples in PROGRESS.md:
  seeding the RNG does not make a wall-clock-bounded search reproducible; "drop
  stale constraint values" silently turns "only Saturday" into "any day";
  "back up + warn on read failure" destroys good files on a transient OSError.
- **Making a reader total converts a crash into a silent drop, which is worse.**
  Whenever you guard something, ask what now happens to the data that used to
  cause the exception.
- **PyQt: never connect a lambda that captures a widget to a timer or a signal
  that can outlive it.** PyQt disconnects bound-method slots when the QObject
  dies; a lambda stays connected and fires into a destroyed window — an access
  violation, not an exception. This cost a segfault hunt. Also,
  `QTimer.singleShot(msec, context, slot)` does NOT exist in PyQt6.
- **Do not trust a plan's line numbers or one-line fixes without compiling them.**
  One handed-down "required" fix did not exist in this Qt binding at all.
- The optimizer is deterministic now (`seed=`, `deterministic_budget=True`), so
  engine assertions can be exact rather than statistical. Use it.

GUARDRAILS
- Every fix needs a regression test that fails before and passes after.
- Re-run `tests/test_scheduler_invariants.py` (both lanes) after ANY optimizer or
  validator change; it is the correctness spine.
- Do not refactor the god objects (`ui/app.py`, `ui/dialogs.py`) — that is Phase 6.
  Phase 3 is core/ work.
- Preserve behaviour users rely on; the audit asks for extraction seams, not
  rewrites.
- One phase per PR, stacked on the previous branch.

KNOWN GAPS LEFT BEHIND — pick these up if they touch what you are changing
1. **Re-entrancy guard, half-covered (from Phase 2, ST-PERF-001).**
   `SolverTask.start()` is idempotent and pinned. That `SchedulerApp` disables
   Generate / undo / Excel import while a solve is running is NOT pinned — it
   needs driving the real window through a full solve. Two solves sharing one
   state dict and one `apply_reschedule` is the most plausible way to corrupt a
   user's timetable. Verified by reading only. **Worth hardening early.**
2. **`multi_start_time_limit` is now 3600 s** (was 120 s), because the solve is
   cancellable. If Phase 3 changes solve cost, revisit it.
3. **Constraint lists on non-day axes are never pruned.** A class with
   `required_classrooms=["R003"]` after R003 is deleted has zero candidate rooms
   and is permanently unplaceable, with no message anywhere. Needs its own finding
   — pruning changes semantics, since an empty allow-list means "no restriction".
4. **Reconcile-on-open and reconcile-on-undo are deliberately NOT wired.**
   `open_file` sets `state["lecturers"] = []` for files predating the lecturers
   feature, so an unconditional reconcile there would silently unplace that user's
   entire schedule.
5. **ST-FUNC-013 for the PDF + off-grid-*day* case is still pinned.** The warning
   infrastructure now exists (`models.find_off_grid_placements`,
   `_warn_about_off_grid_placements`); this may only need the PDF to render an
   appendix listing them. Likely a quick win.
6. **New user-facing strings are `en` + `tr` only** (~15 keys across Phases 0-2).
   The other 20 locales fall back to English via `tr()` — never to a raw key, so
   ST-UI-011 is not reopened — but they need a translator. Phase 5 owns the
   coverage check.
7. **`Claude Code Review` CI fails** on every PR and did so before this work
   started. Unrelated; needs whoever owns that workflow's configuration.

Start by reading PROGRESS.md, confirming the suite is green
(`.venv-audit/Scripts/python.exe -m pytest -m "not slow" -q`), then cut
`fix/phase-3-engine-hardening` and begin with the unified validator — everything
else in Phase 3 depends on it.
```

---

## Quick reference

| Phase | Branch | PR | State |
|---|---|---|---|
| 0 — test scaffold + import Criticals | `fix/phase-0-test-scaffold` | [#7](https://github.com/Oynthe/dersis-app/pull/7) | CI green |
| 1 — data & correctness | `fix/phase-1-data-correctness` | [#8](https://github.com/Oynthe/dersis-app/pull/8) | local + Windows only |
| 2 — performance foundations | `fix/phase-2-performance` | [#9](https://github.com/Oynthe/dersis-app/pull/9) | local + Windows only |

**Merge order:** #7 first. GitHub retargets #8 to `main` automatically once #7
lands, then #9 once #8 lands.

**Findings closed so far:** 18 of 93, including all 6 Criticals —
ST-FUNC-001/002/003/013, ST-SCHED-002/003/004/013, ST-DATA-001/003/004/005/011/012/014,
ST-PERF-001/002/003/005/006/007, ST-UI-009, ST-ARCH-002, and ST-ARCH-001 in part.
