# 01 — System Map

Part of the [DERSİS stress-test audit](00-README.md). A verified map of the
application as a running system — traced from actual code at commit `365b24b`,
not from `README`/`docs/`. Where the prior `dersis-mapped/` docs disagreed with
the code, the code wins (discrepancies noted inline).

Companion documents: the feature-level breakdown is in
[02-functional-inventory.md](02-functional-inventory.md); every subsystem's
test status is in the [coverage matrix](#coverage-matrix) at the end of this file.

---

## 1. What DERSİS is

A **fully offline, single-user PyQt6 desktop application** for preparing weekly
school/university timetables. No server, accounts, login, license server, or
network API. Turkish-first with 22 language dictionaries. Version 1.0.0.
~48 800 LOC of Python under `scheduler_app/` (20 785 of that is the flat
translation table). Runs on Windows (primary) and macOS (PyInstaller).

The user builds a **setup** (days, time slots, classrooms + capacities,
lecturers + availability, year/branch groups), enters **classes** (each with a
lecturer, one or more year/branch *targets*, a duration, a location type, and
optional pins/protection/allow-exclude constraints), then lets an **optimizer**
place them into a conflict-free grid, adjusting by drag-and-drop, and finally
exports to Excel / CSV / PDF.

## 2. Process & runtime shape

- **Single process, single GUI thread.** No `QThread` anywhere. Long optimizations
  run **synchronously on the UI thread**, kept alive only by
  `QApplication.processEvents(ExcludeUserInputEvents)` inside progress callbacks
  ([ST-PERF-001](12-findings-register.md#st-perf-001)).
- **CPU parallelism uses processes, not threads:** a `ProcessPoolExecutor`
  (≤ min(cpu,4)) for candidate scoring, and a dedicated `multiprocessing.Process`
  wrapping the CP-SAT solver so a native OR-Tools crash can't take down the app
  (`schedule_optimizer.py:884-905`, killed after `time_limit+30s`).
- **Entry point** `scheduler_gui.py` installs a two-mode `sys.excepthook` *before*
  any heavy import (startup failures → native MessageBox + `logs/startup_error.log`;
  runtime crashes → `logs/crash_log.txt` + `CrashReportDialog`). Because a custom
  excepthook is installed, the app **keeps running after unhandled exceptions**.
- **No single-instance guard** (verified: 0 `QLockFile`/`QSharedMemory`/mutex).
  Two instances clobber the shared settings file, last-writer-wins
  ([ST-DATA-012](12-findings-register.md)).
- **Import shim:** `scheduler_app/__init__.py` installs a `sys.meta_path` finder
  aliasing 26 flat legacy module names onto the real subpackages, so
  `scheduler_app.translations` *is* `scheduler_app.ui.translations`,
  `scheduler_app.app` *is* `scheduler_app.ui.app`, etc.

## 3. Layered structure (and its violations)

```
scheduler_gui.py            entry point, excepthook, QApplication bootstrap
scheduler_app/
├── ui/            ← Qt layer (app.py 4961, dialogs.py 4451, renderer.py, dashboard, widgets,
│                    translations.py 20785, first_run, tutorial, bug_report, icons, tier_*)
├── core/          ← engine (models, logic, constraint_validator/propagator, conflict_graph,
│                    candidate_generator, placement_scorer, schedule_optimizer, lns_strategies,
│                    cpsat_scheduler, parallel_scorer, timetable_scorer, optimization_goals,
│                    constraint_negotiator, explanation_engine, schedule_*analytics/impact, workflow)
├── data_io/       ← importer, exporter, schema, template  (Excel/CSV/PDF)
├── storage/       ← storage.py  (.egu AES-GCM container, ~/Documents/Dersis)
├── learning/      ← feedback_logger, preference_learner
└── plans.py       ← tier/plan config (dormant — pinned to institutional)
```

The intended dependency direction is `ui → core → {data_io, storage, learning}`.
**It is violated:** there are **19 upward imports** where `core`, `data_io`,
`storage`, and `learning` import the `ui` package (chiefly `ui.translations`,
`ui.day_keys`, `ui.badge_formatter`, `ui.cell_formatter`) — so the engine is not
cleanly extractable and solver output carries display-language strings
([ST-ARCH-009](12-findings-register.md)). There are also **11 module-level import
cycles**, all through `core.logic`, held together only by 20 function-level
deferred imports ([ST-ARCH-010](12-findings-register.md)). Full analysis in
[10-code-architecture-audit.md](10-code-architecture-audit.md).

## 4. The domain model (shared mutable state dict)

There are **no domain classes** — all persistent data is one plain dict from
`models.new_state()` shared by reference across the whole app (54 call sites):

| Key | Shape | Notes |
|---|---|---|
| `days` | `list[str]` | lowercase English keys (`"monday"`…); **order is the timeline** |
| `slots` | `list[str]` | time labels in chronological order; **index = position** (`slots.index()` is load-bearing) |
| `classrooms` | `list[str]` | |
| `classroom_capacities` | `dict[str,int]` | 0 = unlimited |
| `lecturers` | `list[str]` | |
| `lecturer_availability` | `dict[name, {allowed_days,allowed_hours,excluded_days,excluded_hours}]` | excluded wins; empty = unrestricted |
| `years` | `dict[year, list[branch]]` | |
| `classes` | `list[dict]` | 23-field class dicts (below) |

A **class dict** (`models.new_class()`) has 23 fields: identity `class_uid`
(uuid4, auto-assigned on read by `cls_key` — a mutation-on-read), `targets`
(`list[{year,branch}]`), `duration`, `participants`, `location_type`
(`face_to_face` / `online` / `lecturer_office` — only face-to-face uses rooms),
`joint_session` (False + N>1 targets ⇒ a *sequential* class occupying
`duration×N` consecutive slots), `pinned` + `pinned_day/time/classroom`,
`protection` (`none`/`soft`/`same_day`/`improve_only`/`locked`),
`allowed/excluded_days/times`, `required/excluded_classrooms`, and solver output
`placed` + `placed_day/time/classroom`. **Effective placement** = `pinned_*` if
pinned else `placed_*`. **Occupancy is derived, never stored** — three
`(day,slot)→set` maps are rebuilt on demand (this set-based design has no
ref-counting: [ST-SCHED-010](12-findings-register.md)).

> Invariants the code assumes but does not enforce: `allowed_days ⊆ days`,
> `allowed_times ⊆ slots`, `placed_day ∈ days`, `placed_time ∈ slots`, pins
> feasible. Every one of these is violable and several crash downstream code
> ([ST-SCHED-003](12-findings-register.md#st-sched-003),
> [ST-SCHED-004](12-findings-register.md#st-sched-004),
> [ST-DATA-003](12-findings-register.md#st-data-003),
> [ST-DATA-006](12-findings-register.md)).

## 5. The scheduling pipeline

```
User action (Add / Reschedule / Auto-place / Drag)
   │
   ▼
SchedulingWorkflow  (core/workflow.py — UI-free orchestration over the state dict)
   │   schedule_new_classes / reschedule / auto_place / place_batch / validate_drop
   ▼
logic.optimized_*  bridges  ──►  ScheduleOptimizer.optimize()  (core/schedule_optimizer.py)
   │                                  │
   │   partition: pinned / locked / protected(soft) / flexible
   │   build ConflictGraph
   │   multi-start loop (default 5 runs, 120 s total budget, checked BETWEEN runs):
   │      Phase 1  recursive GREEDY construction  (cap 100 000 calls, NO time limit, NO progress)
   │      Phase 2  LNS  (200 iters, 7 destroy strategies, simulated-annealing accept)
   │   best run by (placed count, then TimetableScorer quality)
   │   Phase 4 (deep mode) CPSATScheduler in a subprocess — adopted only if better
   ▼
workflow.apply_reschedule  ──►  re-validates every placement with ConstraintValidator,
                                 returns a `rejected` list  (⚠ the UI discards it: app.py:2713)
```

Key facts the audit established about this pipeline:

- The **raw optimizer output can contain hard-constraint violations** between
  distinct classes; `apply_reschedule` silently drops the losers
  ([ST-SCHED-001](12-findings-register.md#st-sched-001)).
- **Pinned classes bypass validation on commit** entirely
  ([ST-SCHED-002](12-findings-register.md#st-sched-002)).
- The optimizer is **non-deterministic** (unseeded global RNG,
  [ST-SCHED-013](12-findings-register.md#st-sched-013)) and **super-linear**
  (~O(n^1.77), [ST-PERF-001](12-findings-register.md#st-perf-001)).
- There is a **complete legacy solver family** (`logic.reschedule_all` /
  `batch_schedule` / `auto_place_class`) that is **dead code** but still importable
  and carries known constraint holes ([ST-SCHED-007](12-findings-register.md#st-sched-007),
  [ST-ARCH-011](12-findings-register.md)).

Detailed testing in
[05-scheduling-engine-stress-test.md](05-scheduling-engine-stress-test.md).

## 6. Persistence

All data lives under a hardcoded `~/Documents/Dersis/` tree
(`settings/`, `saves/`, `learning/`, `logs/`, `exports/`, `backups/`, `keys/`),
bound at import time with **no environment override**. The save format is a
custom `.egu` binary container: JSON → AES-256-GCM (per-file key
`sha256(master+salt)`) wrapped as `EGU1` magic + version + salt + nonce + length
+ ciphertext(+tag) + SHA-256 trailer. The 32-byte master key is stored in
**plaintext** at `keys/key.bin` beside the data — so this is *tamper/corruption
detection, not confidentiality* ([ST-SEC-002](12-findings-register.md#st-sec-002)).

The de-facto "autosave" is **not** `saves/autosave.egu` (that path has zero
callers) — the entire timetable is embedded under key `"state"` inside
`settings/app_settings.egu`, rewritten in full on **every** `refresh_grid`
([ST-PERF-002](12-findings-register.md#st-perf-002)), with all exceptions
swallowed ([ST-DATA-005](12-findings-register.md)). Corrupt key or container →
silent data loss ([ST-DATA-001](12-findings-register.md#st-data-001),
[ST-DATA-014](12-findings-register.md)). Detail in
[07-data-state-reliability.md](07-data-state-reliability.md).

## 7. Import / export

Excel import reads a 4-sheet localized workbook (schema maps translated
sheet/column names in all 22 languages back to canonical keys); the UI path is
**100% broken** on success ([ST-FUNC-001](12-findings-register.md#st-func-001))
and destructively merges blank joint groups
([ST-FUNC-002](12-findings-register.md#st-func-002)). Export exists **twice** —
`data_io/exporter.py` (used only for PDF) and a parallel `app.py._write_excel`
(Excel/CSV) — which have drifted ([ST-ARCH-003](12-findings-register.md)). Detail
in [08-error-edge-case-audit.md](08-error-edge-case-audit.md) and the functional
tests ([04](04-functional-stress-test.md)).

## 8. Tiers / licensing (dormant)

`plans.py` defines 5 tiers × 6 entity limits × 14 feature flags, but the app
pins the tier to `institutional` at startup, so every gate passes and all
upgrade UI is unreachable. Enforcement is UI-layer-only and trivially bypassable
(it's a local app) — noted for completeness, low relevance.
`docs`/`dersis-mapped` describe this as a licensing system; in this build it is
inert scaffolding.

---

## Coverage matrix

Every subsystem identified above, with its audit status. `PARTIALLY TESTED` and
`NOT TESTABLE` carry a reason (per [brief §24](00-README.md)).

| Subsystem | Status | Where | Notes |
|---|---|---|---|
| App lifecycle / startup / excepthook | **TESTED** | [04](04-functional-stress-test.md), [07](07-data-state-reliability.md) | Headless launch (1.1 s), crash-hook, migration ordering |
| Core domain model & invariants | **TESTED** | [05](05-scheduling-engine-stress-test.md) | Oracle + malformed-input probes |
| Constraint validation | **TESTED** | [05](05-scheduling-engine-stress-test.md) | 4 divergent impls; find_conflicts gap |
| Scheduling: heuristic (greedy+LNS) | **TESTED** | [05](05-scheduling-engine-stress-test.md), [06](06-performance-audit.md) | Scaling benchmark, determinism, correctness oracle |
| Scheduling: CP-SAT deep mode | **TESTED** | [05](05-scheduling-engine-stress-test.md) | In-process solve; protection/availability holes |
| Constraint negotiation / explanation | **PARTIALLY TESTED** | [05](05-scheduling-engine-stress-test.md), [08](08-error-edge-case-audit.md) | Move-conflict dead path confirmed; full diagnostic UI not driven |
| Analytics / dashboard metrics | **TESTED** | [08](08-error-edge-case-audit.md), [09](09-ui-ux-audit.md) | Room-metric zero bug; degenerate-state safety |
| Impact analyzer (deepdiff) | **TESTED** | [08](08-error-edge-case-audit.md) | With & without deepdiff |
| Persistence / `.egu` container | **TESTED** | [07](07-data-state-reliability.md) | Roundtrip + corruption fuzzing |
| Key management | **TESTED** | [07](07-data-state-reliability.md), [11](11-security-resilience-notes.md) | Silent regeneration reproduced |
| Excel/CSV import | **TESTED** | [04](04-functional-stress-test.md) | Edge cases + template round-trip |
| Excel/CSV/PDF export | **TESTED** | [04](04-functional-stress-test.md) | Turkish fonts, crash chars, formula injection |
| Learning / preference model | **TESTED** | [06](06-performance-audit.md) | O(n²) append cost measured |
| UI shell / main window | **TESTED** | [09](09-ui-ux-audit.md) | Driven headless, 38 screenshots |
| Timetable renderer | **TESTED** | [09](09-ui-ux-audit.md) | Silent-conflict-hiding confirmed |
| Dialogs (14) | **TESTED** | [09](09-ui-ux-audit.md) | Constructed + graded; validation gaps |
| Drag-and-drop / selection / undo | **PARTIALLY TESTED** | [08](08-error-edge-case-audit.md), [09](09-ui-ux-audit.md) | Undo corruption reproduced via handlers; real mouse DnD not simulated |
| Localization (22 langs) | **PARTIALLY TESTED** | [09](09-ui-ux-audit.md) | Key-coverage counted; not visually proofed in every language |
| Tiers / licensing | **TESTED** | [01](#8-tiers--licensing-dormant) | Confirmed dormant / bypassable |
| Build / packaging / installer | **NOT TESTABLE** | [10](10-code-architecture-audit.md), [11](11-security-resilience-notes.md) | Needs network + long Windows/macOS builds; reviewed statically |
| CI workflows | **TESTED (static)** | [11](11-security-resilience-notes.md) | Dead trigger + auto-release confirmed by reading + live API |
| `download_release.py` updater | **TESTED** | [11](11-security-resilience-notes.md) | Run read-only against GitHub API |
| macOS runtime | **NOT APPLICABLE** | — | Audit ran on Windows; macOS packaging reviewed statically |
| Real multi-user concurrency | **NOT APPLICABLE** | [11](11-security-resilience-notes.md) | Single-user app; tested via 2 local processes |

Nothing in this map is left unreviewed. Detailed test IDs and scenarios are in
documents [04](04-functional-stress-test.md)–[08](08-error-edge-case-audit.md).
