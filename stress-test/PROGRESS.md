# Roadmap progress

Living tracker for the [implementation roadmap](14-implementation-roadmap.md).
The audit documents (01–15) are the frozen 2026-08-26 baseline; this file records
what has changed since. Per-finding state also lives in the
[findings register](12-findings-register.md).

| Phase | State | Branch |
|---|---|---|
| **0 — Critical stabilisation & test scaffold** | ✅ Complete | `fix/phase-0-test-scaffold` |
| **1 — Data & correctness** | ✅ Complete | `fix/phase-1-data-correctness` |
| **2 — Performance foundations** | ✅ Complete | `fix/phase-2-performance` |
| **3 — Scheduling engine hardening** | ✅ Complete | `fix/phase-3-engine-hardening` |
| **4 — Core workflow UX** | ✅ Complete | `fix/phase-4-workflow-ux` |
| 5–7 | Not started | — |

---

## Phase 4 — complete

> **Starting the next session?** → [`HANDOFF-PHASE5.md`](HANDOFF-PHASE5.md)
> has a ready-to-paste prompt, what Phase 4 changed that Phase 5 will touch,
> and the gaps this work deliberately left behind.

**Suite: 539 tests — 515 pass, 24 known-defect pins, 0 failures.** Both lanes
exit 0. (526 at the end of the six feature commits; the adversarial
verification round below added 13 more.) Four pins were **deleted** because the defect they guarded is closed:
all four `ST-FUNC-013` PDF cases went `XPASS(strict)` when the export appendix
landed — the suite doing exactly the job it exists for.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-UI-001](12-findings-register.md#st-ui-001) | 🔴 Critical | Every lesson that occupies a cell now renders in it. Contested runs split into lanes *inside* the column; conflicts are marked from a validator verdict, not from geometry. |
| [ST-UI-002](12-findings-register.md#st-ui-002) | 🟠 High | One `schedule_counts()` feeds the status bar, the dashboard card and `compute_all_metrics`. Pinned is a subset annotation, not a peer segment. |
| [ST-UI-003](12-findings-register.md#st-ui-003) | 🟠 High | The dashboard reads `effective_room` — the currency the rest of the app already used. `room_switching` 0.0 → 0.8 on the audit's own fixture. |
| [ST-UI-021](12-findings-register.md#st-ui-021) | 🟠 High | **New finding.** Duplicate slot labels refused and named; slot edits that move existing lessons are reported and confirmed. |
| [ST-SCHED-011](12-findings-register.md) | 🟡 Medium | "Move conflicting class" suggestions emit for the first time: 0 → 15 of 19 on `small`. |
| [ST-UI-015](12-findings-register.md) | 🟡 Medium | `PlaceClassDialog` explains the 0-options case and disables a button that cannot succeed. |
| [ST-FUNC-013](12-findings-register.md) | 🟢 Low | PDF appendix lists every off-grid placement and every conflict by class code. Four strict pins deleted. |
| [ST-SCHED-014](12-findings-register.md) | 🟢 Low | The global bottleneck sentence reaches the results dialog; `apply_reschedule`'s rejected list reaches the warning log. |

### Where the register was not enough

As in Phases 1–3, each was proved by building the naive version and watching it
fail, or by measuring rather than assuming.

1. **ST-UI-001 is not one renderer bug but five occupancy builders that
   disagree.** Two lessons double-booked in R001 monday 09:00, before the fix:
   screen showed `ZZZ999` (dict overwrite, last wins); PDF classroom/group
   showed `AAA111` (an explicit `continue`, first wins); XLSX filtered sheets
   showed both; the XLSX everything-matrix showed one. **A user who checked on
   screen and then printed got two different, both-incomplete timetables.** The
   register's recommendation names two of the five.
2. **The collision is order-dependent, not "last wins".** A span row against a
   start row either *overdraws* or hides one, purely on `state["classes"]`
   order. The single real collision the `large` preset produces is exactly that
   shape, so a fix handling only "two starts in one cell" does not fix it.
3. **Splitting and labelling are different questions.** Two online lessons
   share an hour legitimately — a naive geometric sweep of `large` reports 14
   collisions, of which **13 are that**. And a real clash can show only one
   block (one group in two rooms puts one lesson on each room's tab). Splitting
   is geometric and per-view; the conflict mark is a validator verdict and
   view-independent.
4. **ST-UI-002's own recommendation — "clamp/assert non-negative" — is the
   worst available fix.** With 4 pins also carrying `placed=True` and 3 lessons
   genuinely unplaced, the old formula gives −1 and the clamp gives **0**, while
   the truth is **3** and those 3 are in the sidebar on the same screen. It
   replaces an impossible number with a confidently wrong one.
   `tests/test_placement_vocabulary.py` is mutation-tested against exactly that
   implementation: **8 of its 10 core tests go red under it.**
5. **ST-UI-002's `-5 yerleşmemiş` evidence is a harness artifact.**
   `stress-test/tests/_ui_boot.py::greedy_place` calls `mark_placed` on pinned
   classes; `git show 365b24b` confirms `apply_reschedule` skipped pins even at
   the audit commit. The formula is wrong regardless — it encodes a
   disjointness nothing enforces — so the counter is structurally total rather
   than trusting the invariant.
6. **ST-UI-003's cause is worse than "reads a missing key".** `cls.get("room","")`
   is passed as `room_override`, and because `""` is not the `_ROOM_UNSET`
   sentinel it *wins* — actively defeating a correct `placed_classroom`
   fallback.
7. **The roadmap's "Structured time-slot entry" is the wrong fix, and cites the
   wrong finding.** `grep` for `strptime` / `%H:%M` / `split(":")` returns
   **zero hits**: nothing parses a slot as a time, so `"1. Ders"` and
   `"Öğle Arası"` are first-class and a `QTimeEdit` per row would hard-code the
   one rule the grid must not have. Uniqueness is the only hard rule. See
   ST-UI-021.
8. **The handoff's known gap #4 is wrong.** `required_classrooms=["R003"]` after
   R003 is deleted *already* produces a correct message with suggestions
   (`InfeasibilityAnalyzer.analyze_class`, category `required_room_missing`) —
   verified against the exact input the handoff names, with `participants=0` to
   remove the capacity confound. Only the wiring was missing. Writing the
   proposed new reachability check would have created a second answer to one
   question.

### A correction to the Phase 2 record

PROGRESS.md's Phase 2 section states `multi_start_time_limit` was "raised 120 s
→ 3600 s". **On the production path that is inert.** Measured by spying on the
real constructor through the live `SchedulingWorkflow.reschedule`:

```
ScheduleOptimizer.__init__ default : 3600.0
optimized_reschedule_all default   : 120.0
what the LIVE reschedule path uses : 120.0
```

`SolverTask` is built with no optimizer kwargs, so every production solve goes
through `optimized_reschedule_all`'s own 120.0 (logic.py:1309, 1338), and
`grep multi_start_time_limit scheduler_app/ui/` returns nothing. The suite
already documented the truth — `tests/test_greedy_bounds.py:129` calls out
`normal` as "still clock-capped at 120 s by `optimized_reschedule_all`'s
`multi_start_time_limit=120.0`". Only the Phase 2 summary is misleading.

**Deliberately not changed here.** Raising it has real runtime consequences
(`test_bounding_does_not_cost_placements` runs ~123 s *because* of this cap),
and the Phase 4 task-6 implementation spec that proposed a global deadline was
calibrated on 3600.0 throughout — its adversarial reviewer returned
*materially-wrong* for that reason, and showed the proposed change would
truncate run 4 on `normal` and cost the reproducibility it was meant to
protect. It needs its own measurement pass. What Phase 4 did instead was make
the UI honest about the consequence: `summary['deterministic']` is now surfaced.

### Behaviour changes worth knowing

- **The status bar reads differently.** `80 classes │ ✅ 78 placed 📌 incl. 4
  pinned │ ⏳ 2 unplaced`. Pinned moved from a peer segment to a subset
  annotation because the three numbers otherwise summed to more than the class
  count. A new `⚠ N not on the timetable` segment appears when a placement
  points at a deleted day or hour — those are `scheduled` but drawn nowhere and
  absent from the unplaced panel, so the count used to exceed what the grid
  showed with no way to find the difference.
- **The reschedule modes are renamed** `Hızlı` / `Kapsamlı` (were `Standart` /
  `Derin (CP-SAT)`), Quick is the default, and Thorough warns that its result
  may not be reproducible — which is true, and was never said.
- **A duplicate time slot is now refused at Setup OK.** A user whose saved file
  already contains one is blocked until they fix it; the live status strip shows
  the problem the moment the dialog opens, so it is diagnosed before they click.
- **`edit_setup` now pushes an undo snapshot** (and pops it if cancelled). It
  previously pushed none, so every unplacement `_reconcile_after_setup`
  performed was irreversible.
- **`negotiate_class` no longer raises on an off-grid placement.** It read a
  stored slot through `logic.slot_index`; with one orphaned lesson, 3 of 4 calls
  died. Skipped blockers are *counted* and reported, because
  `ConstraintValidator.add_placement` returns early on the identical condition.

### The adversarial verification round

The six Phase 4 commits were then attacked by 43 verifier agents, with every
candidate defect independently reproduced or refuted by a second agent that
defaulted to REFUTED. **30 CONFIRMED, 4 PARTLY, 3 REFUTED.** All 34 are fixed
or deferred with a stated reason, across five follow-up commits.

The pass earned its keep several times over. What it found, grouped:

**Live user-visible defects Phase 4 introduced or left**

- The **Online / Lecturer-office tab discarded every conflict mark**. The
  adapter stamped the flags on both render modes; the virtual scene builder
  constructed its `LessonItem`s without passing them. One dropdown click away
  on the default tab. Nothing caught it because **no test in the repository
  built a `TimetableScene`** — every conflict test asserted on the adapter's
  blocks, one layer short of what the user sees.
- **A class name with angle brackets vanished from the PDF.** reportlab reads
  `<Vekil> Dersi` as an unknown tag and drops it. A bare `&` is tolerated,
  which is why the first version of the test pinned nothing.
- **The PDF `everything` matrix still dropped a claimant**, and stacked cells
  **overprinted the rows above and below** (`rowHeights` is fixed; reportlab
  draws over neighbours rather than growing a row).
- **The XLSX everything matrix stacked a class against itself** when it carried
  two identical target dicts — what a user gets typing `"A, B, A"` as branches.
- **The app called the user a liar about their own pin.** `apply_reschedule`
  reports two events through one list; on the project's own dataset generator
  **13 of 13 rejections** are "your pin clashes where you put it", and all were
  reported as errors reading "could not be committed where the planner put it".
- **"Move X (frees N slots)" overstated N.** Blockers were counted per cell, so
  a cell blocked by two lessons credited both — moving either frees nothing.
- **The Setup undo was worse than no undo** — see below.

**Phase 4's own tests that pinned nothing**

`make_app`'s TierEnforcement snapshot named three wrong attributes behind a
`hasattr` guard, so it restored nothing while looking like isolation. Three
other tests were vacuous — including one whose assertion
(`quick.isDefault() or not deep.isDefault()`) is TRUE in exactly the state its
failure message describes. **ST-UI-002's rendering half had no test at all**:
the status bar could be reverted wholesale with the suite still green.

**A withdrawal**

Phase 4 added an undo snapshot to `edit_setup`. `_push_undo` deep-copies
`state["classes"]` and nothing else, while Setup rewrites the axis lists — so
"Undo: setup change" restored placements onto hours the grid no longer has,
resurrecting the ST-DATA-003 orphans from a button labelled as a safety net.
It also cleared the redo stack on cancel. A half-transaction undo is not a
partial fix; it was withdrawn. ST-UI-014's second clause needs full-state
snapshots — ST-ARCH-012, Phase 6.

**A broken measuring tool, worth recording**

Stale `__pycache__` invalidated three consecutive measurements: `inspect.getsource`
reads the file while the running function came from cached bytecode, so a
mutation test reported *GREEN — PINS NOTHING* for a fix that was working. The
conclusion on offer was "drop the fix". The mutation harness now clears the
cache before every run. **A mutation test that cannot see its own mutation is
worse than none: it manufactures confidence.**

A second, subtler masking: one test could not go red because the conflict
appendix — added earlier in the same phase — listed the same names through its
own escaped path and kept the needle alive regardless of what the grid cell
did. A new feature was hiding the defect its own test was written for.

### Known gaps left behind

1. **`multi_start_time_limit` still is not a global bound**, and is 120 s in
   production rather than the 3600 s Phase 2 recorded. See the correction above.
   Needs its own measurement pass.
2. **`targets.index(t)` is unchanged in all three everything-matrix builders.**
   Switching the renderer's copy to `enumerate` is correct in isolation but
   would create a new screen-vs-PDF-vs-XLSX divergence for duplicate-target
   non-joint classes. Fix all three together or none.
3. **A legacy `.egu` carrying a duplicate slot has no in-app repair path.**
   `SetupDialog` is the only writer of `state["slots"]`, so the user must delete
   the line by hand; no "remove duplicates" affordance is offered.
4. **New strings are `en` + `tr` only** (~30 keys across Phases 0–4). Phase 5
   owns the coverage check.
5. **`Claude Code Review` CI still fails**, as it has since before this work.

---

## Phase 3 — complete

> **Starting the next session?** → [`HANDOFF-PHASE4.md`](HANDOFF-PHASE4.md)
> has a ready-to-paste prompt, what Phase 3 built that the UI does not yet
> consume, and the ten known gaps this work deliberately left behind.

**Suite: 456 tests — 428 pass, 28 known-defect pins, 0 failures.** Both lanes
exit 0 (fast 410 pass / 28 pins, slow 18 pass). The 28 pins are exactly the ones Phase 2 left behind; **every pin this
phase created was closed by this phase**, and the four ST-SCHED-001 pins the
handoff named as the scoreboard turned red and had their markers deleted.

**The Critical is closed at the root, not papered over.** `repaired_conflicts`
— the assert-and-repair pass added as a safety net — measures **0 on every
preset**, meaning nothing ever reaches it. The optimizer stopped producing
invalid schedules rather than learning to clean up after itself.

### Completion criteria

| Criterion | Result |
|---|---|
| Oracle: raw optimizer output has zero hard violations on **all** presets | ✅ zero **optimizer-caused** violations on all six (`tiny` → `pathological`); see the qualification below |
| CP-SAT respects availability across duration and all protection levels | ✅ |
| 1200-class instance completes without `RecursionError` | ✅ 853 placed, no error, stock recursion limit |

**The qualification on criterion 1, stated plainly.** On `large`, `very_large`
and `pathological` the oracle still reports hard violations — 9, 10 and 89. Every
one belongs to a **pinned** class: measured `flexible=0` at all three scales. The
preset generator emits mutually infeasible pins (93 pins on `pathological`), and
DERSİS deliberately commits an infeasible pin rather than clearing it, because
the pin is an instruction the user typed (ST-SCHED-002, Phase 1). Those cells are
now *named* in `summary['infeasible_fixed']` instead of being silent. So the
criterion is met for everything the engine is responsible for; what remains is
the input's, and it is reported.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-SCHED-001](12-findings-register.md#st-sched-001) | 🔴 Critical | The optimizer no longer proposes hard-constraint violations. `small` 18 → 0, `normal` 102 → 0, **with the placement count unchanged** (21 and 76). |
| [ST-ARCH-004](12-findings-register.md) | 🟠 High | One validator. Drag-and-drop, the class editor, the placement sweep and the legacy solvers all reach their verdict through `ConstraintValidator`; `screen_placements()` is the single commit rule. |
| [ST-SCHED-005](12-findings-register.md#st-sched-005) | 🟠 High | CP-SAT models lecturer availability across a class's whole duration, not just its start hour. |
| [ST-SCHED-006](12-findings-register.md) | 🟠 High | All four protection levels honoured — including two the register did not know were broken. |
| [ST-SCHED-007](12-findings-register.md#st-sched-007) | 🟡 Medium | The legacy solver family forwards to the optimized path; it no longer places an unavailable lecturer or moves a locked class. |
| [ST-SCHED-009](12-findings-register.md) | 🟡 Medium | `find_conflicts` is guaranteed non-empty whenever `check_placement` rejects. |
| [ST-SCHED-010](12-findings-register.md) | 🟡 Medium | Occupancy cells are ref-counted, so removing one of two classes claiming a cell no longer frees the survivor's claim. |
| [ST-SCHED-012](12-findings-register.md) | 🟡 Medium | Greedy construction is iterative. 1200 classes complete; depth is heap-bound. |
| [ST-PERF-004](12-findings-register.md) | 🟠 High | The greedy phase has a real stopping condition. It converges instead of burning its budget. |
| [ST-PERF-008](12-findings-register.md) | 🟡 Medium | The greedy phase is wall-clock bounded. 125–291 s against a 5 s budget → 6.4 s. |
| [ST-SCHED-014](12-findings-register.md) | 🟢 Low | `summary['infeasibility']` names the global bottleneck with numbers; the negotiator analyses the schedule being proposed. |
| [ST-SCHED-015](12-findings-register.md) | 🟢 Low | The dead `neighbor_impact` term is gone, from all four places it lived. |

### The one that mattered: ST-SCHED-001's actual root cause

The register attributes it to "the optimizer's internal placement bookkeeping".
That is the symptom. The defect is a **single seam**, in
`ScheduleOptimizer._greedy_construct`.

`solve()` recorded its answer as a *snapshot* (`best_solution`) taken at a leaf,
while continuing to mutate `solution` and the occupancy maps. It has two exits:

* **Full success** — every class placed. It returns `True` and each frame
  returns without running its matching `_remove`, so occupancy still describes
  the answer.
* **Anything else** — a partial best, or the iteration budget running out. Every
  frame falls through to `_remove`, the stack unwinds completely, and occupancy
  empties back to the baseline — while `best_solution` still claims a full set of
  placements.

In the second case the caller was handed a solution and a validator that
disagreed about **every cell in it**. Measured on `small`: 20 placements
returned, **0** of them known to the validator. `_lns_improve` then ran its
entire repair loop against a grid it believed was empty and stacked classes on
top of each other — exactly the 18 room/lecturer/group double-books the oracle
reported. `apply_reschedule` hid the damage by dropping the losers.

This also explains the shape of the bug that nothing had explained before: **why
`tiny` was always clean.** Five classes, 5/5 placed, so the greedy takes its
full-success exit and the desync never happens. The finding's own evidence
("reproduces at `multi_start_runs=1`, 6 distinct-class collision cells") is the
same seam seen from the other end.

The fix is a reconciliation loop of eight lines. Everything else in this phase is
consequence or defence.

### Where the register was not enough

As in Phases 1 and 2, each of these was proved by building the naive version and
watching it fail — or by measuring rather than assuming.

1. **"Add an assertion/repair pass" would have shipped the bug.** A repair pass
   that drops colliding classes produces the *same committed timetable*
   `apply_reschedule` already produced — clean, and short by however many classes
   collided. It converts a silent drop in one place into a silent drop in
   another. The repair pass is here, but as a tripwire: it measures 0 on every
   preset, and a non-zero `summary['repaired_conflicts']` is now defined as an
   engine defect rather than a normal outcome.

2. **ST-SCHED-015's "dead code — always returns 0.0" is right about the value and
   wrong about the consequence.** `_neighbor_impact` did measure 0.0 on all 3307
   calls across `small` and `normal`. But `neighbor_impact_penalty` is also half
   of the user-facing **"minimal disruption" slider**
   (`optimization_goals._GOAL_WEIGHT_MAP`), so this was not dead code — it was a
   *slider running at half strength*. And deleting the key from `DEFAULT_WEIGHTS`
   alone raises `KeyError` on every reschedule with custom goals, because
   `goals_to_weights` accumulates into `{k: 0.0 for k in DEFAULT_WEIGHTS}` with
   no membership guard. Four sites, one commit.

3. **ST-SCHED-006 is worse than "only LOCKED is respected", in two ways the
   register does not mention.**
   * `same_day` was ignored by **greedy construction** too, not just by CP-SAT.
     `RepairStrategy` filtered candidates by the original day; `_greedy_construct`
     did not. So the protection held or not depending on whether LNS happened to
     destroy and repair that class — a coin flip presented as a guarantee.
   * `improve_only` was **broken in both engines**. The gate is
     `candidate_score <= baseline`, but the candidates were scored with
     `PlacementScorer` and the baseline computed with
     `TimetableScorer.placement_score` — different functions on different scales
     (measured: −0.20…0.60 against −3.67…8.34). For **4 of 10** measured classes
     the gate kept **zero** candidates, *including the class's own current
     placement* — so the protection that promises "never worse" could force the
     class to be unplaced entirely. Both sides now use the same scorer, which
     makes "stay where you are" always admissible.

4. **A wall-clock bound sampled every N nodes is not a wall-clock bound.** The
   first version checked the deadline every 512 search nodes, on the reasoning
   that `time.time()` is measurable at 100 000 nodes. It moved a 5 s budget from
   125–291 s to 65–168 s — because the quantity that needs bounding is *seconds
   between two looks at the clock*, and one node calls `generate()` over
   days × slots × rooms and then scores every candidate against the look-ahead
   window. The interval in nodes was bounded; the interval in seconds was not.
   Checking every node costs tens of nanoseconds against a node costing
   microseconds to milliseconds.

5. **"Bound the greedy phase" (ST-PERF-004) is not about the number.** Cutting
   `max_iterations` bounds the time and silently costs placements — the classic
   trap. Measurement first: placements are **identical at every budget from 100
   to 100 000** (`small` 21, `normal` 76, `large` 231 from 500 up), while the
   full pipeline costs 257 s against 175 s on `normal` and 43.8 s against 10.3 s
   on `small`. The budget was buying nothing at all. So the fix is not a smaller
   number but a *stopping condition*: the search ends when it stops improving its
   incumbent, which is a reason rather than a timeout.

6. **Making `find_conflicts` total is the opposite of the usual trap.** Phase 1's
   lesson is that guarding a reader turns a crash into a silent drop. Here the
   silent case came first — `check_placement` said no and `find_conflicts`
   returned `[]`, so the UI refused a drop with nothing to say. Both the
   availability-across-the-block gap and a backstop for any future rule now
   guarantee a non-empty list.

### Behaviour changes worth knowing

- **`apply_reschedule` returns dicts, not names.** Each entry carries `name`,
  `class_uid`, `reason` and `reasons`. `ui/app.py` still discards the value —
  wiring it into the results dialog is Phase 4's "Why unplaced?" panel — but the
  data it needs now exists. A `str` subclass was tried first, to keep every
  existing caller working untouched; it was rejected because a consumer cannot
  tell a rich entry from a bare name without introspection, which is exactly the
  ambiguity the finding is about.
- **The negotiation report describes the proposed schedule, not the pre-solve
  one.** This is the ST-SCHED-014 fix and it changes what the negotiation tab
  says. Two Phase 2 tests pinned the old baseline and were updated; see below.
- **`improve_only` under CP-SAT is frozen in place, deliberately.** CP-SAT scores
  in a different currency from `PlacementScorer`, so "only move somewhere at
  least as good" cannot be stated in that model. Not moving always satisfies the
  promise, so CP-SAT declines to improve such a class rather than risk making it
  worse; the heuristic phase still optimizes it properly, and now correctly
  (point 3 above). The alternative — leaving it free to move, as it was — breaks
  the promise outright.
- **The legacy solver family is now a set of forwarding shims.** ~325 lines of
  divergent constraint logic became unreachable. `_solve_backtrack`,
  `_get_valid_slots` and `_check_placement_fast` are dead; deleting them is
  ST-ARCH-011's job in Phase 6.
- **`find_drop_classroom` returns `None` for a lesson that needs no room**, which
  is `get_room_candidates`' sentinel rather than a failure. `ui/app.py` was
  taught the difference in both places it checked. Before this, a drag committed
  a *physical classroom* onto an online lesson while `apply_reschedule` stored
  `None` for the same lesson — so the same lesson showed a room or not depending
  on how it was placed, and exports disagreed with the timetable.
- **`summary` gained four keys**: `repaired_conflicts`, `repaired_classes`,
  `infeasible_fixed`, `infeasibility`.

### Tests changed rather than added

Five test files were written by agents that never touched `scheduler_app/**`, so
the fail-before/pass-after guarantee holds for everything they pin. Five tests
needed the implementer to change them, and each is a case where landing the fix
made the *test* wrong rather than the code:

1. `test_auto_place_class_never_displaces_a_locked_class` was **unsatisfiable**
   once locked classes stopped being movable: it asserted both "the newcomer was
   placed" and "the locked class did not move", on a board where the only legal
   cell for the newcomer was the locked one. Rebuilt with a *displaceable* class
   in the way, so the displacement pass provably runs and the locked lesson
   provably survives it. (Both the adversarial verifier and the implementer
   reached this independently.)
2. `test_neighbor_impact_loop_body_never_executes` monkeypatched the method the
   fix deletes, so it hard-errored with `AttributeError` the moment the deletion
   landed. Replaced by `test_neighbor_impact_term_stays_deleted`, which pins that
   it stays gone; the pre-deletion measurement is recorded in its docstring.
3. `test_neighbor_impact_penalty_weight_changes_no_score` became **silently
   vacuous** after the deletion: `PlacementScorer.__init__` merges an unknown
   weight key in as an orphan, so swinging it across nine orders of magnitude
   changed nothing by construction. Deleted; `test_scoring_digest_is_unchanged`
   is the durable tripwire and its golden is unchanged across the deletion.
4. `_drop_verdict`, the drag-and-drop harness, states that it mirrors
   `ui/app.py::_execute_drop` "phase for phase" — and hard-coded the pre-fix
   `if room is None: reject`. Updated to mirror the fixed code, which is what its
   own contract requires.
5. **Two Phase 2 tests** (`test_negotiation_result_still_says_what_it_used_to`,
   `test_negotiation_result_survives_apply_unchanged`) compared the negotiation
   report against a pass over the **pre-solve** state — the baseline
   ST-SCHED-014 deliberately moves. Their ST-PERF-007 property is untouched and
   both still assert it; the second now perturbs the live state explicitly to
   test the pinning directly, because "committing changed the answer" is no
   longer a source of contrast now that the snapshot describes the proposal.

One companion test was **added** by the implementer:
`test_a_harmless_edit_leaves_the_lesson_where_it_was`. Without it, the five
`test_editing_a_class_does_not_leave_it_on_a_now_illegal_cell` cases are all
satisfied by an `apply_class_edit` that unplaces the lesson on *every* edit —
their own escape hatch accepts the unplaced branch as a pass. The pair would
have permanently certified a bulk-unplace as correct.

### Two latent bugs found in passing

- **`schedule_optimizer.py` never imported `tr`**, so the `generator is None`
  branch of the unplaced-reason fallback raised `NameError` instead of reporting.
- **`check_placement_explained` corrupted occupancy for an excluded class.** It
  lifts the class's own placement out of the maps and restores it in a `finally`
  — but for a class in `exclude_ids` the lift finds nothing to release while the
  restore really claims the cell, permanently marking a free cell occupied for
  every later check. Reachable now that `screen_placements` excludes every class
  it is about to test.

### What the adversarial verification caught after the code had landed

The five verifier agents ran against a tree that kept moving under them. Four of
their findings were things the implementer had already fixed independently
(including the two fatal ones: an unsatisfiable locked-class test and a test that
`AttributeError`s the moment the deletion it documents lands). Four more were
real and are fixed in a follow-up commit:

1. **The deadline bounded the search but not the return.** When a stop fired,
   `enter()` returned False and the driver then popped one frame at a time while
   `advance()` re-applied and re-removed every untried candidate of every frame
   still on the stack — genuine occupancy work, O(depth x candidates) of it, done
   past the deadline, counted by nothing and consulting no clock. It now unwinds
   directly. (The same shape existed in the original recursive code, so this is
   not a regression — but a clock bound that keeps working after it fires is not
   a clock bound.)
2. **A stop before the first leaf threw the whole partial descent away.**
   `best_solution` is only written at a leaf, so a run capped early returned
   `[None] * n` and the resync then dutifully stripped every placement the search
   had already made. The stop now offers the current `solution` to the incumbent
   first — mid-descent it is a complete, internally consistent partial answer.
3. **Only one of the three `_greedy_construct` call sites had a deadline.** The
   other two are `optimized_auto_place` and `optimized_batch_schedule` — the
   "add a class" and "place batch" buttons — so ST-PERF-008's user-visible
   symptom survived on exactly the interactive paths, where there is no progress
   dialog to explain the wait.
4. **The one test carrying ST-PERF-008 was vacuous.** It passed against a greedy
   phase that returns `[None] * n` and sets `_clock_capped` (measured: 1.35 s,
   PASSED) — its two stated anti-vacuity guards checked the flag, not the search.
   It now asserts the search visited nodes and placed something beyond the
   instance's 24 pinned classes; mutation-tested against that exact stub.

A fifth was a stale scoreboard: `test_bounding_does_not_cost_placements` kept
floors set from pre-fix measurements on the expectation that the fix would move
`raw_placed` DOWN. It did not — it moved `raw_clean` and `committed` UP to meet
`raw_placed`, which did not move — so `normal`'s clean floor of 39 against an
actual 76 tolerated a 49 % regression in proposal cleanliness. Re-based to 72/72
and 20/20.

The verification also found the occupancy module's headline invariant was
**count-blind** (`set(cell)` discards the refcount, and a doubly-claimed cell
still refuses `check_placement`), so the plausible wrong fix — re-adding
`best_solution` without releasing the stale claim, which is idempotent on sets
and permanent on ref-counted cells — would have passed all ten of its tests and
the whole invariants spine. `test_greedy_holds_exactly_one_claim_per_placement_it_returns`
closes that, and is mutation-proven to be the only test in the module that does.

### Known gaps left behind

1. **`changes[]` still omits protected classes** (`schedule_optimizer.py`, the
   `cls_key(cls) in effective_protected_ids` skip). It no longer *matters*,
   because protected classes no longer move — the defect is closed by
   construction rather than by fixing the builder. If a future change lets them
   move again, the move will be invisible to the impact panel. Undo and rollback
   are snapshot-based and were never affected, contrary to the finding text.
2. **`multi_start_time_limit` is not a global bound.** It is applied per phase —
   the greedy deadline is `global_start + limit` but LNS restarts its own clock —
   so a full solve can take roughly twice the number the user was shown. Bounding
   it globally is a Phase 4 UX decision, not a correctness one.
3. **The presets carry no `protection` levels and no pre-placed classes**, which
   is why the `improve_only` currency bug survived three phases of oracle runs.
   `dataset_gen` should grow a protection-bearing preset; that belongs with
   Phase 7's testing work.
4. **`test_drop_accounting_closes_on_a_real_solve` asserts `0 == 0`** now that
   nothing is dropped, and it is among the more expensive setups in the fast
   lane. It is a legitimate future regression guard, but it is paying for
   coverage it no longer provides.
5. **New user-facing strings are `en` + `tr` only** (4 keys this phase, ~19
   across Phases 0–3). The other 20 locales fall back to English via `tr()` —
   never to a raw key — but need a translator. Phase 5 owns the coverage check.
6. **The re-entrancy guard is still only half-covered** (carried from Phase 2).
   `SolverTask.start()` is idempotent and pinned; that `SchedulerApp` disables
   Generate / undo / import while a solve runs is still not, because pinning it
   means driving the real window through a complete solve.
7. **`Claude Code Review` CI fails** on every PR and did so before this work
   started. Unrelated; needs whoever owns that workflow's configuration.

---

## Phase 2 — complete

> [`HANDOFF-PHASE3.md`](HANDOFF-PHASE3.md) was the prompt for the phase above;
> it is kept as a record. Phase 3's own gaps are listed in its section.


**Suite: 339 tests — 307 pass, 32 known-defect pins, 0 failures.** The non-slow
lane was run three times to confirm stability (299 pass / 28 pins each time).

**All six Criticals from the audit are now closed.** ST-PERF-001 was the last.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-PERF-001](12-findings-register.md#st-perf-001) | 🔴 Critical | The solve runs on a worker thread with real progress and a working Cancel. |
| [ST-PERF-002](12-findings-register.md#st-perf-002) | 🟠 High | Autosave is coalesced behind a 1.5 s debounce and skipped entirely when a hash of the payload matches disk. |
| [ST-PERF-003](12-findings-register.md#st-perf-003) | 🟠 High | The warning log separates sticky history from derived findings; derived ones are replaced, not appended. |
| [ST-PERF-005](12-findings-register.md#st-perf-005) | 🟡 Medium | New EGL1 append-only log format; one append is O(1) in bytes written *and* read. Learning is incremental. |
| [ST-PERF-006](12-findings-register.md) | 🟡 Medium | The open-slots panel is skipped when nothing it displays has changed. |
| [ST-PERF-007](12-findings-register.md) | 🟡 Medium | The negotiation pass is lazy, memoised, and pinned to the reschedule-time state. |
| [ST-UI-009](12-findings-register.md) | 🟡 Medium | Re-selecting what is already selected does no work. |

### Where the plans were not enough

As in Phase 1, each of these was **proved by building the wrong version and
watching it fail**, not argued:

1. **A lazy negotiation property saves nothing on its own.** `BulkResultsDialog`
   is constructed after *every* reschedule and built its negotiation tab inside
   `__init__`, so the first read happened immediately and always. Deferring the
   computation moved ~727 ms (250 classes) a few lines later inside the same
   frozen stretch. The tab is now a placeholder populated on first selection.
2. **Cheap autosave fingerprints all pass the tests and all lose data.** Class
   names, the class count, and `state["classes"]` alone each passed the whole
   module — and each silently drops real edits: a drag mutates one class dict in
   place (same count, same names), and a Setup room change touches
   `state["classrooms"]` and nothing else. The fingerprint hashes the whole
   payload.
3. **A grid-shape-only fingerprint freezes the open-slots panel.** Days, slots
   and classrooms are stable for an entire editing session, so the panel would be
   built once and then show occupied slots as free. Occupancy and the selection
   are both in the fingerprint.
4. **Counting log records must not read them.** The incremental learner's
   early-return still read the entire log to find out how many entries there
   were, so a no-op pass cost 1.6 MB on an 800-entry log. Counting now seeks over
   the framing, and a size check gates the pass before even that.

### A crash this surfaced

The full suite began segfaulting in an unrelated module's teardown, inside a
lambda in `app.py`. The deferred settings modal was connected to its timer as
`lambda: QMessageBox.warning(self, ...)`. **PyQt disconnects a bound-method slot
when its QObject is destroyed; a lambda capturing `self` is just a callable,
stays connected, and fires into a half-destroyed window** — an access violation,
not an exception. It had been latent since Phase 1 and only became reachable
once the off-thread solve started pumping the event loop hard.

The plan's proposed fix for it, `QTimer.singleShot(0, self, lambda: ...)`, **does
not compile under PyQt6** — the context-object overload is not exposed. A real
`QTimer` parented to the window is the equivalent that works.

### Behaviour changes worth knowing

- `refresh_grid` used to normalize `state_data` synchronously as a side effect of
  autosaving. That now happens up to 1.5 s later, or on close. Every load path
  still normalizes, so the exposure is an in-session mutation read by something
  else inside the debounce window.
- **⚠ Corrected in Phase 4 — this change is inert on the production path.** The
  raise moved `ScheduleOptimizer`'s own default; every production solve goes
  through `optimized_reschedule_all`, whose signature default is still 120.0 and
  which passes it explicitly. Measured live: the reschedule path uses **120.0**.
  See the Phase 4 section, "A correction to the Phase 2 record".
- `multi_start_time_limit` raised 120 s → 3600 s. That cap existed to bound a
  freeze the user could not escape; now that the solve is cancellable, truncating
  the search is the wrong trade, and a cap that fires costs reproducibility
  outright.
- The feedback log is written in a new EGL1 format. Logs written by older builds
  are converted once, on the next append, and still load either way.

### Known gap, deliberately left

**The re-entrancy guard is only half-covered.** `SolverTask.start()` is
idempotent and that *is* pinned; that `SchedulerApp` disables Generate / undo /
import while a solve runs is **not**, because pinning it means driving the real
window through a complete solve. Two solves sharing one state dict and one
`apply_reschedule` is the most plausible way this change could corrupt a
timetable. Verified by reading, not by test — it deserves hardening.

---

## Phase 1 — complete

**Suite: 261 tests — 229 pass, 32 known-defect pins, 0 failures.** Five of Phase
0's `xfail(strict=True)` pins flipped to passing and their markers were deleted:
ST-DATA-001 (×2), ST-SCHED-002, ST-FUNC-013 (×2). That is the pins doing exactly
the job they exist for.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-SCHED-013](12-findings-register.md#st-sched-013) | 🟡 Medium | The optimizer is reproducible. Every random draw in the package now comes from one seeded stream, and the LNS phase is bounded by **iteration count instead of the wall clock**. See "the register was not enough" below. |
| [ST-SCHED-003](12-findings-register.md#st-sched-003) | 🟠 High | `filter_class_days` / `filter_class_times` intersect the class's allow-list with the actual grid, so a class allowed only on Saturday is no longer placed on Saturday on a Mon–Fri timetable. |
| [ST-SCHED-004](12-findings-register.md#st-sched-004) | 🟠 High | New total `find_slot_index()`; `slot_index` deliberately still raises. A stale `allowed_times` no longer aborts the reschedule with `ValueError: '20:00' is not in list`. |
| [ST-SCHED-002](12-findings-register.md#st-sched-002) | 🟠 High | `apply_reschedule` validates pins instead of skipping them. An infeasible pin is reported through the rejected list; the pin itself is left alone, because silently clearing it would destroy the instruction the user deliberately typed. |
| [ST-DATA-001](12-findings-register.md#st-data-001) | 🟠 High | `_load_or_create_key` distinguishes an **absent** key file (first run — mint one) from a **damaged** one (raise, leave the bytes untouched). Previously a 10-byte truncation silently minted a new key and orphaned every save the user had ever made. |
| [ST-DATA-003](12-findings-register.md#st-data-003) | 🟠 High | Every stored-placement reader is total: occupancy, conflict detection, analytics, the scorer, `validate_drop`. |
| [ST-DATA-004](12-findings-register.md#st-data-004) | 🟠 High | New core-layer `SchedulingWorkflow.reconcile_placements()`, called from both `SetupDialog` sites and from Excel import, before the repaint. |
| [ST-DATA-014](12-findings-register.md) | 🟢 Low | A corrupt settings container is quarantined to `backups/`, never rebuilt-from-`{}`-then-overwritten. |
| [ST-DATA-005](12-findings-register.md) | 🟡 Medium | `_auto_save` no longer swallows everything: it reports, returns a bool, and never writes a container it failed to read. |
| [ST-DATA-011](12-findings-register.md) | 🟡 Medium | `schedule_new_classes` is all-or-nothing; the four mutate-compute-restore sites got `try/finally`. |
| [ST-DATA-012](12-findings-register.md) | 🟢 Low | New `scheduler_app/single_instance.py`, acquired before the language gate. |
| [ST-FUNC-013](12-findings-register.md) | 🟢 Low | Exports warn about off-grid placements instead of vanishing them; the CSV writes them. |

### Where the register was not enough

Three places where following the recommendation literally would have shipped a
half-fix. Each was **proved by building the half-fix and watching it fail**, not
argued:

1. **ST-SCHED-013 — a seed is necessary but not sufficient.** A tree carrying
   only the seed change still ran 25 LNS iterations on a simulated fast machine
   and 14 on a slow one, from the same seed and the same instance, landing on
   different placements and different scores. The search was bounded by the wall
   clock, so *machine speed was an input to the answer*. The register's Effort
   "M" covers only the seed half.
2. **ST-SCHED-003 — "drop stale constraint values during normalization" is
   wrong.** An **empty** `allowed_days` means "no restriction", so emptying a
   now-impossible allow-list would silently turn "only Saturday" into "any day"
   and place the lesson on Monday looking like a success. Intersection, not
   dropping; an empty intersection leaves the class unplaced with a reason.
3. **ST-DATA-014 — "back up + warn" on *any* read failure is data loss dressed
   up as recovery.** A prototype that quarantined on every exception destroyed a
   perfectly good settings file on a transient `OSError`. Only `EguFileError`
   means "genuinely unreadable"; everything else propagates.

A fourth, smaller one: making the readers total turns a crash into a **silent
drop**, which is worse — the printout looks complete. Hence
`models.find_off_grid_placements()` and the export warnings.

### Deliberate scope calls

- **`slot_index` still raises.** Around forty call sites do `idx + duration`
  arithmetic; returning `None` would trade a loud `ValueError` for an obscure
  `TypeError`, and returning `-1` would be worse still — `-1` is a valid Python
  index, so lessons would land in the last hour of the day. Stored-data readers
  use `find_slot_index` instead.
- **An infeasible pin is reported, not cleared.** The pin is what the user
  typed.
- **`find_off_grid_placements` is not called from `normalize_state_classes`,**
  so it never runs on the `.egu` load path. Unplacing orphans at load would
  discard the user's own placements with no way to see or undo it — the same
  class of bug in a new place.
- **CP-SAT keeps a wall-clock budget.** It gets `random_seed`, but
  `summary['deterministic']` is False whenever it ran, so the app never claims a
  reproducibility it cannot deliver. A deterministic CP-SAT budget needs
  per-scale calibration; Phase 3, with ST-PERF-009.

### Follow-ups this opened

- **The 120 s `multi_start_time_limit` default needs revisiting.** 80 classes now
  reproduce exactly, but the department-scale run was measured at 77 s once and
  105 s on a busier machine — against a 120 s cap. The margin is thin and it is
  contention-sensitive, so a slower or loaded machine hits the emergency cap and
  loses reproducibility (correctly reported as such, but lost). Interacts with [ST-PERF-001](12-findings-register.md#st-perf-001) —
  Phase 2 wants the solve off the UI thread anyway.
- **Constraint lists on the non-day axes are still never pruned.** A class with
  `required_classrooms=["R003"]` after R003 is deleted has zero candidate rooms
  and is permanently unplaceable, with no message anywhere. `reconcile_placements`
  clears *placements*, not constraints, and pruning changes semantics (an empty
  allow-list means "no restriction"), so this needs its own finding and its own
  decision rather than a silent fix here.
- **Reconciling on file-open and on undo is deliberately NOT wired.** `open_file`
  sets `state["lecturers"] = []` for files predating the lecturers feature, so an
  unconditional reconcile-on-open would unplace that user's entire schedule,
  silently.
- **New strings are `en` + `tr` only** (12 keys across Phases 0–1). The other 20
  locales fall back to English via `tr()` — never to a raw key — but need a
  translator. Phase 5 owns the coverage check.

---

## Phase 0 — complete

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-FUNC-001](12-findings-register.md#st-func-001) | 🔴 Critical | `_import_from_excel` called `_on_state_changed()` / `refresh()`, neither of which exists in the MRO, so every *successful* import crashed **after** mutating state. Now calls `refresh_grid()` / `_update_status()`, and the whole merge is a transaction: any failure — including in the repaint — restores the pre-import state and reports the error. |
| [ST-FUNC-002](12-findings-register.md#st-func-002) | 🔴 Critical | Blank cells arrive from pandas as `NaN`, and `str(NaN)` is the truthy string `'nan'`, so every blank-`joint_class_group` class shared one joint key and all but the first were deleted. All cell reads now go through `_is_blank` / `_cell_text`. The app's own template round-trips **5 rows → 4 classes** (was 2). |
| [ST-FUNC-003](12-findings-register.md#st-func-003) | 🟠 High | One malformed number aborted the entire import with an uncaught `ValueError`. Numeric cells now parse per row: blank takes the documented default, unreadable text in the required `duration` skips that row with an error, unreadable text in the optional `student_count` degrades to 0 with a warning. Room `capacity` got the same treatment. |
| [ST-ARCH-002](12-findings-register.md) | 🟠 High | CI triggered on `master`, a branch this repo has never had, so it had never run. Now `main` + `workflow_dispatch`. |
| [ST-ARCH-001](12-findings-register.md#st-arch-001) | 🔴 Critical | **Partially.** 0 tests → 138 (132 of them in the fast CI job). Depth is Phase 7. |

### The safety net

`pytest.ini` + `tests/` — **138 tests: 101 pass, 37 known-defect pins, 0 failures.**

| Module | Covers |
|---|---|
| `test_scheduler_invariants.py` | the audit's independent hard-constraint oracle vs. the production optimizer |
| `test_storage_roundtrip.py` | encrypted round-trip, 7 corruption modes, key/​container damage |
| `test_import_roundtrip.py` | Excel import at library level, template round-trip |
| `test_export_smoke.py` | xlsx / csv / pdf smoke ×4 modes, parsed back through real parsers |
| `test_import_ui_flow.py` | the real `SchedulerApp` driven headlessly through an import |
| `test_smoke_environment.py` | the harness itself — proves HOME is sandboxed |

CI runs `pytest -m "not slow"` in the **Validate** job and the full oracle
(including the slow presets) in a separate **Scheduling invariants** job.

Conventions, fixtures and the one rule you cannot break are in
[`tests/README.md`](../tests/README.md).

### 37 known-defect pins

Defects scheduled for later phases are pinned with
`@pytest.mark.xfail(strict=True, …)`, so **the suite goes red the moment a fix
lands** — that is the signal to delete the marker. They cover ST-SCHED-001/002,
ST-DATA-001/002/013, ST-FUNC-004/005/006/007/009/010/011/012/013.

Two pins are deliberately **non-strict**: the `normal`-preset ST-SCHED-001 pins.
The optimizer is non-deterministic (ST-SCHED-013) *and* wall-clock-bound, so it
cannot be made reproducible by seeding alone; 1 of 13 measured 80-class runs came
out clean by luck, which would XPASS a strict marker and redden the build at
random. The `small` pins aggregate three independent trials and stay strict
(~0.2 % false-XPASS).

### What this implies for Phase 1

**Do [ST-SCHED-013](12-findings-register.md#st-sched-013) (seed the RNG) early.**
The roadmap ranks it 3/3/3/S, but it is what makes the ST-SCHED-001 pins
deterministic — and therefore what makes the Phase 3 engine work verifiable
rather than statistical. It is a prerequisite, not a nice-to-have.

### Known gaps in this phase

- **CI is green on Ubuntu.** Both jobs pass on PR #7: **Validate** (1 m 01 s,
  including `pytest -m "not slow"` under `QT_QPA_PLATFORM=offscreen` with the apt
  Qt libraries) and **Scheduling invariants** (4 m 10 s, the full oracle
  including the slow presets). ST-ARCH-002 is therefore verified end to end, and
  Phase 0's "CI runs and is green" completion criterion is met.

  *Correction to an earlier entry here.* This file previously recorded that
  "GitHub Actions is not executing anything in this repository", diagnosed from
  PR #7 showing no runs — not even `Claude Code Review`, which subscribes to
  `pull_request` with no branch filter — and from the last run of any kind being
  68 days earlier. That conclusion was **wrong**. The runs were simply late:
  they were created about fifteen minutes after the PR, well after the checks
  that concluded otherwise, and nothing needed to be re-enabled. The lesson is
  narrow and worth keeping: an empty `GET /actions/runs` shortly after a push is
  evidence of queueing, not of a disabled repository, and the two look identical
  for as long as the queue lasts.

  Still true, and still only a symptom: `ci.yml` and `claude.yml` are absent from
  `GET /actions/workflows` until their first run, because that index lists only
  workflows that have executed at least once. `ci.yml` never could — it triggered
  on `master`, a branch this repo has never had.

- **`Claude Code Review` fails**, on this branch and on PR #6 back in June. It is
  unrelated to the roadmap and predates this work; it needs whoever owns that
  workflow's configuration.
- The three new user-facing strings (`errors.invalid_number`,
  `warnings.blank_number_defaulted`, `warnings.invalid_number_defaulted`) exist
  in **en** and **tr** only. The other 20 locales fall back to English via
  `tr()` — never to a raw key, so [ST-UI-011](12-findings-register.md) is not
  reopened — but they need a translator. Phase 5 owns the coverage check.
- The import still pushes nothing onto the undo stack, so a user cannot reverse
  a bad import. Out of scope here: the undo model only covers `state['classes']`
  while an import also replaces lecturers, rooms and years, so a partial undo
  would desync the state. That is [ST-ARCH-012](12-findings-register.md) /
  full-state snapshots, Phase 6.
