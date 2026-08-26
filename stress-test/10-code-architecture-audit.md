# 10 — Code, Architecture & Maintainability Audit

Part of the [DERSİS stress-test audit](00-README.md). Covers architecture,
maintainability, duplication, coupling, state management, god objects, typing,
and the (absent) test infrastructure. Findings are registered as `ST-ARCH-*`
in the [findings register](12-findings-register.md); the remediation sequence is
in the [roadmap](14-implementation-roadmap.md) (Phase 6 & 7).

All metrics below are **OBSERVED** — measured with `radon` (installed into the
audit venv) and AST scripts over `scheduler_app/` at commit `365b24b`. Raw
complexity data: [`evidence/arch_complexity_top50.csv`](evidence/arch_complexity_top50.csv).

**Headline:** the working code is ~28 000 LOC (excluding the 20 785-line
translation table). Two files hold a third of it and both score the **floor** of
the maintainability scale. There are **zero automated tests** and CI is wired to
a branch that does not exist, so nothing is verified on any push. The engine is
clean enough to test headlessly today; the UI is not, until a handful of
mechanical extraction seams land. None of the recommendations below is a rewrite.

## Code metrics: size, complexity, maintainability

**Size.** 48,838 LOC in `scheduler_app/`; 20,785 of that is the flat translation dict (`ui/translations.py`), leaving ~28,053 LOC of working code. Distribution: ui 14,514 (excl. translations), core 10,184, data_io 1,723, storage 555, learning 506. Two files — `ui/app.py` (4,961) and `ui/dialogs.py` (4,451) — hold **33.5% of all working code**.

**Maintainability Index (radon, worst first):** `app.py` 0.00, `dialogs.py` 0.00, `renderer.py` 0.00 (the floor of the scale), `core/logic.py` 0.51, `core/constraint_negotiator.py` 0.62, `core/schedule_optimizer.py` 8.80, `core/workflow.py` 16.52. Everything else ranks A. The three 0.00 modules are the ones every feature change must touch.

**Cyclomatic complexity.** 941 functions/methods: A 667, B 154, C 84, D 28, E 2, F 6. Worst 10 (full top-50 in `../evidence/arch_complexity_top50.csv`):

| CC | Function | Location | Lines |
|----|----------|----------|-------|
| 105 | CPSATScheduler.solve | core/cpsat_scheduler.py:91 | 510 |
| 84 | ScheduleOptimizer.optimize | core/schedule_optimizer.py:203 | 326 |
| 53 | ScheduleOptimizer._lns_improve | core/schedule_optimizer.py:671 | 176 |
| 46 | AddClassDialog.__init__ | ui/dialogs.py:2064 | 337 |
| 44 | NegotiationReportBuilder.build_diagnostic_summary | core/constraint_negotiator.py:922 | 155 |
| 43 | SchedulerApp._write_excel | ui/app.py:3947 | 496 |
| 33 | ConstraintValidator.check_placement_explained | core/constraint_validator.py:101 | 83 |
| 32 | InfeasibilityAnalyzer.analyze_class | core/constraint_negotiator.py:70 | 167 |
| 30 | InfeasibilityAnalyzer._analyze_occupancy_blocking | core/constraint_negotiator.py:239 | 119 |
| 29 | BulkAddDialog._ok | ui/dialogs.py:3680 | 97 |

Note the pattern: the CC≥50 functions are exactly where the correctness audits found shipped bugs (CP-SAT/validator divergence lives inside `solve`'s 510 lines; the latent NameError paths live inside `optimize`'s 326).

**Exception hygiene.** 55 broad handlers (`except:`/`except Exception`); 22 are single-statement silent swallows. Concentration: app.py 16, dialogs.py 16, storage.py 9. The critical-path offenders are `_auto_load` (app.py:1832 — any load error → fresh state), `_auto_save` (app.py:1842-1843 read-failure→`{}`, 1850-1851 outer `except: pass` — the silent-wipe chain), and storage's migration/JSON fallbacks (storage.py:392, 423, 473, 494). These are not defensive coding; they are error deletion on the data-safety path.

## Coupling: import graph, layering, cycles

An AST-based, shim-aware import graph (the `scheduler_app/__init__.py:21-56` meta_path shim maps 26 flat legacy names onto subpackages, so `scheduler_app.translations` is really `scheduler_app.ui.translations`) yields:

**19 upward layer violations** — lower layers importing `ui/`:
- **core→ui (11):** logic, models, constraint_validator, constraint_negotiator, candidate_generator, cpsat_scheduler, explanation_engine, schedule_analytics import `ui.translations` and/or `ui.day_keys`.
- **data_io→ui (6):** exporter additionally imports `ui.badge_formatter` and `ui.cell_formatter` — presentation logic feeding the export layer.
- **storage→ui (1):** storage.py:47 imports translations (a 20.8k-line module) at load.
- **learning→ui (1):** preference_learner.

All target modules are currently Qt-free (probe-verified by the lifecycle pass), so headless use works — but the engine is not extractable, engine return values carry translated strings (solver output depends on display language), and one added Qt import in `ui/translations.py` breaks every headless consumer including the CP-SAT subprocess worker.

**11 module-level cycles, all through `core.logic`:** logic↔constraint_validator, logic↔placement_scorer, logic↔constraint_propagator, logic↔schedule_optimizer (plus longer variants via lns_strategies, timetable_scorer, conflict_graph, cpsat_scheduler, parallel_scorer/candidate_generator), logic↔schedule_analytics, logic↔constraint_negotiator. The cycles do not crash at import only because `logic.py` defers its side of every edge into **20 function-level imports** (logic.py:1129-1455) while the partners import logic at module top (e.g. constraint_validator.py:10). Promoting any one deferred import to top level — a natural cleanup — produces a startup ImportError. 

**Seam:** split `logic.py` into primitives (occupancy/slots/conflicts/layout — what the stack needs) and a `core/facade.py` holding the `optimized_*` bridges (logic.py:1121-1456) with normal imports; move `translations`/`day_keys`/`badge_formatter`/`cell_formatter` into a leaf `i18n`/`common` package (the shim makes this a zero-call-site change). Then enforce with an import-linter contract in CI.

## Duplication: exports, validation, day keys

**Parallel export engines (confirmed).** The Excel menu path is `_export_to_excel` → `SchedulerApp._write_excel` (app.py:3882→3947; 496 lines, CC 43) — a complete export implementation inside the main window. `data_io/exporter.py`'s `_export_excel` (:130) and `_export_csv` (:362) have **zero production callers**: repo-wide grep shows `export_schedule` is called from the UI only with `format='pdf'` (app.py:3941); every xlsx/csv call is in stress-test probes. CSV is likewise duplicated inline at app.py:2290-2321. Behavior already diverges: `_write_excel` supports classroom/group/lecturer/everything modes, localized deduplicated sheet names, and virtual-classroom lane sheets; `exporter._export_excel(schedule, filepath)` has **no mode parameter** despite `export_schedule`'s docstring claiming mode applies to Excel (exporter.py:899), and emits `T_`/`R_`-prefixed unlocalized sheets. Styling/badge/color rendering is maintained twice (app.py:3981+ vs exporter.py:92-127).

**Four hard-constraint validators.** (1) `ConstraintValidator` (constraint_validator.py:45,77) — authoritative; (2) deprecated `logic.respects_constraints` (logic.py:291) — no lecturer-availability check, still live in the drag-drop path (workflow.py:532,690); (3) legacy `_check_placement_fast` (logic.py:522) — availability hole, dead path; (4) inline day/time checks in `workflow.validate_drop`/`check_drop_valid` (workflow.py:479-489,677-684). Plus four duplicated helper pairs, all grep-verified: occupancy build (logic.py:492 vs constraint_validator.py:40), compactness gap (logic.py:591 vs placement_scorer.py:456), constraint tightness (logic.py:672 vs constraint_validator.py:280), unplaced-reason (logic.py:756 vs candidate_generator.py:154). The CP-SAT/validator divergence bugs are the observed cost of this duplication style.

**Day-key normalization.** A single good implementation exists (`ui/day_keys.py:39-96`) but lives in the ui layer while core imports it (logic.py:14, constraint_validator.py:22). `dialogs._normalize_days` (dialogs.py:456) is a thin wrapper (acceptable). The real hazard is placement, not copies: `data_io/importer.py:162-164` stores lecturer-availability day names **raw**, and canonicalization happens only when `_auto_save` runs `normalize_state_day_keys` as a persistence side effect (app.py:1844) — correctness of imported data depends on autosave firing. Field validation is centralized (`models.validate_class_fields`, used at dialogs.py:2526,3736 — good), though AddClassDialog validates before the constraint checkboxes are read (dialogs.py:2526 vs 2531-2540), so contradictory allow/exclude sets are never checked.

## State management: the shared dict, mutation topology, undo

**Architecture.** All application state is one plain dict created by `new_state()` and bound to `self.state_data` (app.py:818). The same object is aliased into `SchedulingWorkflow` (app.py:856-859; re-bound after new/open at 869, 2212, 2230) and passed **by reference into 54 call sites** in app.py — dialogs, renderer scenes, exporters, negotiator, dashboard, first-run controller. There is no ownership boundary, change notification, or dirty tracking.

**Mutation topology (counted):** app.py performs ~15 direct mutations (6 index assignments, 3 list ops incl. `classes.pop/append/remove` at 2609/3665/3672, 5 via local alias `s` in the import-merge at 4514-4523, 1 class-dict write); `dialogs.py` writes the live state directly in 8 places — `SetupDialog._ok` assigns all 7 setup keys (dialogs.py:1813-1819) and `EditClassesDialog` removes classes (:4430); `first_run.py:377` reads `app.state_data` for the setup-needed check; `workflow.py` mutates placements via `mark_placed/mark_unplaced`; `constraint_negotiator.py:767-854` temporarily rewrites live class constraint lists with **no try/finally**; and `models.cls_key` mutates class dicts on read (:460-471). Normalization (`normalize_state_day_keys`/`normalize_state_classes`) runs inside `_auto_load`/`_auto_save` (app.py:1820-1821, 1844-1845) — i.e., data hygiene is a side effect of persistence, and `refresh_grid` (25 call sites) triggers a full read-decrypt-modify-encrypt-write of the settings container per user action.

**Undo model.** Snapshot-based: `_push_undo` deep-copies **only `state['classes']`** (app.py:1767-1774), 18 call sites, 50-entry cap, redo cleared on any action. Setup edits (days/slots/rooms/availability/years) push no undo entry and are irreversible; undoing class actions across a setup change can restore placements referencing removed days/slots — feeding the stale-slot ValueError and ghost-day failure modes the core audit probe-confirmed. Cost: O(classes) deepcopy per action stacked on the per-refresh encryption write.

**Risk summary.** The design is workable for a single-window app, but three properties make it fragile: mutation is distributed across layers (a dialog can corrupt state with no mediation), invariants are restored by side effects rather than at mutation points, and the only rollback tools (workflow snapshots, classes-only undo) cover different, partial slices of state. The recommended guardrails (dialog-returns-result convention, normalization at mutation points, try/finally in the estimators, full-state undo snapshots) preserve the architecture while closing the sharpest edges.

## God objects: responsibility breakdown and extraction seams

**ui/app.py (4,961 LOC, MI 0.00).** `SchedulerApp` has ~135 methods across at least ten responsibilities (banner comments confirm the internal awareness): stylesheet + palette (module level, ~530-798); window/menu/toolbar/status construction (908-1650); undo/redo (1765-1806); encrypted persistence (1808-1851); grid-rendering coordination (1960-2050); unplaced panel (2051-2202); file lifecycle (2203-2323); edit + scheduling orchestration incl. reschedule pipeline and per-refresh auto-negotiation (2324-3076); selection & drag-drop (3077-3825); a full Excel export engine (3826-4460); Excel import merge (4460-4554); tutorial/language/misc (4555-4961).

**ui/dialogs.py (4,451 LOC, MI 0.00).** 14 unrelated dialog classes (SetupDialog alone is 1,145 lines) plus a module-level Excel row→class parsing pipeline (:440-491) and template generation, coupled only by living in one file.

**Extraction seams (each mechanical, independently shippable, no rewrite):**
1. **Export engine → data_io/exporter.** `_write_excel` reads only `self.state_data` + openpyxl; move it (it is the more featureful implementation), give `export_schedule` its documented mode parameter, delete the exporter's stale `_export_excel` and app.py's inline CSV. Unifies the drifting duplicates and makes exports golden-file-testable.
2. **Persistence + undo → Qt-free `SessionStore`.** `_auto_save/_auto_load/_push_undo/undo/redo` + normalization calls have no Qt dependency beyond `set_language`/layout direction (which stay in app). This makes the silent-wipe path unit-testable and is the precondition for fixing it safely.
3. **Scheduling orchestration → thin controller.** `reschedule/_do_reschedule/_schedule_new_classes/_run_auto_negotiation` are already 90% delegation to `SchedulingWorkflow`; extracting them isolates the processEvents pumping and the ignored `apply_reschedule` return (app.py:2713).
4. **dialogs.py → package split** with a re-exporting `__init__` (zero call-site churn); row-parsing helpers join `data_io/importer.py`.
5. **Optimizer megafunctions** split along phase comments (see findings) once the seeded regression harness exists.

Order matters: land the characterization tests (next section) before seams 1-3; seams are then diff-reviewable as pure moves.

## Typing

AST scan over 972 functions: **10.8% carry any annotation** (parameter or return). By package: storage 93%, data_io 37%, core **7%**, ui **5%**, learning 0%. The gradient is inverted relative to risk: the untyped 93% includes the entire domain model — 8-key state dicts and 23-field class dicts with stringly-typed keys (`models.new_state`/`new_class`, models.py:281/431) flowing through every solver, where a typo'd key or wrong-shaped value fails only at runtime. The audits already surfaced this failure class in the wild: `lecturer_available_at` KeyErrors on malformed availability dicts (models.py:304-331) and an unimported `tr()` NameError in `schedule_optimizer.py:497` that any type/name checker would have flagged.

Proportionate remedy (not full annotation): define `TypedDict`s for `StateDict` and `ClassDict` in models.py; annotate the ~30 public seam functions (workflow API, validator API, `optimized_*` bridges, storage API — storage is already done); run mypy on `core/` only in CI with a permissive baseline. Roughly two days of work for static coverage of the highest-risk shapes.

## Test infrastructure: zero tests, dead CI, and a risk-ranked plan

**Current state (all OBSERVED).** No `tests/` directory, no test files, no pytest/coverage/lint/type-check configuration anywhere. `requirements-dev.txt` pins `pytest>=7.0` under the heading 'Tools for testing' — nothing uses it. CI (`.github/workflows/ci.yml`) runs **no tests** (version checks, `pip check`, build-file existence, and an import smoke of 5 modules) — and does not run at all: its triggers filter on `branches: [master]` (ci.yml:12,14) while the repository's only branch is `main` (`git branch -a` confirms no master anywhere). **Conceptual coverage: 0%**, on a codebase where prior passes probe-confirmed multiple correctness holes.

**Testability split.** *Easy today (headless, no Qt):* the entire `core/` (state construction is 6 lines; `SchedulingWorkflow`, `ConstraintValidator`, `CandidateGenerator`, `PlacementScorer`, `ScheduleOptimizer` all take plain dicts; determinism achievable via `random.seed` + `parallel_workers=-1` + `use_cpsat=False`), `storage/` (sandboxed HOME/USERPROFILE — mandatory since `_ROOT_DIR` binds at import, storage.py:55), `data_io/` (file round-trips), `learning/` (explicit dirs). *Hard:* `ui/app.py`/`dialogs.py`/`renderer.py` — Qt offscreen works (`stress-test/tests/smoke_offscreen_launch.py` exists as a template) but modal dialogs, 100/400/500ms QTimers, and the god-object structure resist unit testing until the seams land.

**Top 10 highest-risk untested behaviors** (consolidated from verified briefing risks, ranked by user impact × likelihood):
1. Settings-container corruption → silent full-schedule wipe (app.py:1832-1851; storage key regeneration storage.py:189-193).
2. Optimizer/validator parity: every committed placement passes `ConstraintValidator.check_placement`; `apply_reschedule` rejected list empty (CP-SAT availability/protection divergence; app.py:2713 ignores rejects).
3. Ghost-day placements (models.py:356 not intersecting `state['days']`; unguarded commit paths workflow.py:173-192, 274-282).
4. Stale `allowed_times`/`placed_time` → uncaught ValueError aborting optimization (candidate_generator.py:41; logic.py:17-18).
5. Protection semantics (`locked`/`same_day`/`improve_only`/`soft`) across greedy, LNS repair, and CP-SAT.
6. First-run/legacy migration ordering (language gate creates dirs before `ensure_dirs`, defeating migration — scheduler_gui.py:171-178; storage.py:92, 464-466).
7. Sequential/joint class semantics (`_active_targets` vs raw targets — validator vs LNS/TimetableScorer divergence).
8. Negotiation impact estimators corrupting live constraints on exception (constraint_negotiator.py:767-854, no try/finally).
9. Day-key normalization on import/open (raw availability days from importer.py:162-164; pinned/placed day filtering day_keys.py:71-96).
10. Undo/redo consistency across setup changes (classes-only snapshots, app.py:1767-1806).

**Minimal regression suite (first wave, ~30-40 tests, all headless):** storage roundtrip + corruption/backup semantics; validator invariant suite (encode current bugs as xfail to pin them); workflow E2E with seeded RNG asserting `apply_reschedule` rejected==[]; exporter golden files (also the safety net for unifying the duplicate export engines); day-normalization property tests; legacy-solver characterization tests *if* retained, else delete the family. **Second wave:** offscreen smoke launch in CI, dialog `_ok` result-shape tests. **Infrastructure:** fix ci.yml branch filter (one line), add `pytest -q`, ruff (E722/BLE001 freeze), and mypy-on-core. Without the first wave, every refactor recommended elsewhere in this audit is unverifiable.
---

## Cross-references

- The **four divergent hard-constraint validators** (this doc, "Duplication")
  are the structural root of the scheduler-correctness findings
  [ST-SCHED-001](12-findings-register.md#st-sched-001)…005 and
  [ST-SCHED-007](12-findings-register.md#st-sched-007).
- The **parallel export engines** (`app.py._write_excel` vs `data_io/exporter.py`)
  are [ST-ARCH-003](12-findings-register.md); their drift produced
  [ST-FUNC-004](12-findings-register.md#st-func-004)/005/006.
- The **silent-swallow persistence path** (`_auto_save`/`_auto_load`) is
  [ST-ARCH-006](12-findings-register.md) and directly enables
  [ST-DATA-005](12-findings-register.md) and [ST-DATA-014](12-findings-register.md).
- The **zero-tests / dead-CI** pair is [ST-ARCH-001](12-findings-register.md#st-arch-001)
  and [ST-ARCH-002](12-findings-register.md); the risk-ranked first test wave
  is echoed in [roadmap Phase 7](14-implementation-roadmap.md).
- Dead/unreachable symbols ([ST-ARCH-011](12-findings-register.md)) include the
  legacy solver family carrying [ST-SCHED-007](12-findings-register.md#st-sched-007).
