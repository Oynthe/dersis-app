# 14 — Implementation Roadmap

Part of the [DERSİS stress-test audit](00-README.md). Turns the
[findings](12-findings-register.md) and
[opportunities](13-improvement-opportunities.md) into a sequenced plan. Phases are
ordered by dependency and risk, not by finding number.

**Prioritisation model.** Each task carries Impact / Urgency / Risk-reduced /
Effort on a 1–5 scale (5 = highest / largest). These rank tasks *within* a phase;
they deliberately do **not** let a low-effort cosmetic fix outrank a high-impact
structural one. Effort letters: S ≤ ½day · M ≤ 2days · L ≤ 1wk · XL > 1wk.

> **Sequencing principle.** The audit found that DERSİS has *no test safety net*
> ([ST-ARCH-001](12-findings-register.md#st-arch-001)) and *silently corrupts or
> loses data*. So Phase 0 stands up just enough testing to make every later change
> verifiable, and Phase 1 stops the bleeding on data/correctness before any
> performance or UX polish. Doing UX first would be polishing a tool that produces
> wrong schedules.

---

## Phase 0 — Critical stabilisation & test scaffold

*Goal: make the app safe to change and stop the two workflows that are 100% broken.*

| Task | Findings | Files | I/U/R/E | Effort |
|---|---|---|---|---|
| Fix CI trigger `master`→`main`; add `pytest -q` step | [ST-ARCH-002](12-findings-register.md) | `.github/workflows/ci.yml` | 4/5/4/1 | S |
| Stand up pytest + first regression wave (storage roundtrip+corruption, import round-trip of generated template, export smoke ×3, **scheduler invariant oracle** promoted from `tests/schedule_oracle.py`) | [ST-ARCH-001](12-findings-register.md#st-arch-001) | new `tests/` | 5/5/5/4 | L |
| Repair Excel-import success path + try/except rollback | [ST-FUNC-001](12-findings-register.md#st-func-001) | `ui/app.py:4512-4531` | 5/5/4/1 | S |
| Guard blank joint-group cells | [ST-FUNC-002](12-findings-register.md#st-func-002) | `data_io/importer.py:297` | 5/5/4/1 | S |
| Per-row numeric parsing with error report | [ST-FUNC-003](12-findings-register.md) | `data_io/importer.py:258` | 4/4/3/1 | S |

**Completion criteria.** CI runs and is green on `main`; importing the generated
template yields the original class count; the invariant oracle runs in CI and
xfail-pins the known scheduler violations. **Regression tests required:** the
first-wave suite above. **Risk:** low — these are localized fixes + new test code,
no production behavior change beyond the two bug fixes.

## Phase 1 — Data & correctness

*Goal: DERSİS never silently loses data or commits an invalid schedule.*

| Task | Findings | I/U/R/E | Effort |
|---|---|---|---|
| `key.bin`: distinguish absent vs malformed; fail loudly + restore-from-backup; never auto-regenerate | [ST-DATA-001](12-findings-register.md#st-data-001) | 5/5/5/M | M |
| Corrupt settings container: back up + warn, never rebuild-from-`{}`-then-overwrite; surface `_auto_save` errors | [ST-DATA-014](12-findings-register.md), [ST-DATA-005](12-findings-register.md) | 5/5/5/M | M |
| Intersect `allowed_days`/`allowed_times` with the grid; guard `slot_index`; normalize away stale constraints | [ST-SCHED-003](12-findings-register.md#st-sched-003), [ST-SCHED-004](12-findings-register.md#st-sched-004), [ST-DATA-003](12-findings-register.md#st-data-003) | 5/5/5/M | M |
| Validate pins on commit; flag infeasible pins as conflicts | [ST-SCHED-002](12-findings-register.md#st-sched-002) | 5/4/5/M | M |
| Reconcile placements on `SetupDialog` OK (unplace + warn) | [ST-DATA-004](12-findings-register.md#st-data-004) | 4/4/4/M | M |
| Seed the RNG (reproducibility) | [ST-SCHED-013](12-findings-register.md#st-sched-013) | 3/3/3/S | S |
| Single-instance guard | [ST-DATA-012](12-findings-register.md) | 3/3/4/S | S |
| Internal rollback in `schedule_new_classes`; try/finally in estimators | [ST-DATA-011](12-findings-register.md) | 3/3/3/S | S |

**Completion criteria.** The invariant oracle passes with **zero committed hard
violations** across all presets (the xfail pins from Phase 0 flip to pass); a
truncated `key.bin` and a corrupt settings file both preserve data or fail
loudly; removing a slot from setup no longer crashes. **Regression tests:**
oracle assertions un-xfailed; storage corruption tests; a setup-removal test.

## Phase 2 — Performance foundations

*Goal: the UI stays responsive at realistic scale.*

| Task | Findings | I/U/R/E | Effort |
|---|---|---|---|
| Move solving to a worker with progress + cancel | [ST-PERF-001](12-findings-register.md#st-perf-001) | 5/4/4/L | L |
| Debounce/delta autosave (stop full rewrite per refresh) | [ST-PERF-002](12-findings-register.md#st-perf-002) | 4/4/3/M | M |
| Rebuild warnings from current state (fix +480 MB leak + O(n²) HTML) | [ST-PERF-003](12-findings-register.md#st-perf-003), [ST-PERF-006](12-findings-register.md) | 4/4/3/M | M |
| Skip side-panel/negotiation recompute on selection-only changes | [ST-UI-009](12-findings-register.md), [ST-PERF-007](12-findings-register.md) | 3/3/2/M | M |
| Append-only feedback log (kill O(n²)) | [ST-PERF-005](12-findings-register.md#st-perf-005) | 3/2/2/M | M |

**Completion criteria.** A reschedule on 250 classes runs off-thread and is
cancellable; a single refresh on 250 classes is < 300 ms; 100 refreshes show flat
RSS. **Regression tests:** a perf smoke test asserting refresh latency and no
unbounded `warning_log._messages` growth.

## Phase 3 — Scheduling engine hardening ✅ COMPLETE

*Goal: correct, scalable, diagnosable solving.*

> **Done** on `fix/phase-3-engine-hardening`. All six tasks landed; see
> [`PROGRESS.md`](PROGRESS.md#phase-3--complete) for what the register got wrong
> and what was left behind. Completion criteria met, with one qualification
> recorded there: the residual oracle violations on the three largest presets
> are all attributable to the preset generator's own infeasible **pins**
> (measured `flexible=0`), which DERSİS reports rather than clears by design
> (ST-SCHED-002).

| Task | Findings | I/U/R/E | Effort |
|---|---|---|---|
| Unify to one hard-constraint validator; route all paths through it | [ST-ARCH-004](12-findings-register.md), [ST-SCHED-007](12-findings-register.md#st-sched-007) | 5/4/5/L | L |
| Fix internal occupancy bookkeeping so raw output has no double-bookings; assert-and-repair | [ST-SCHED-001](12-findings-register.md#st-sched-001), [ST-SCHED-010](12-findings-register.md) | 5/4/5/L | L |
| CP-SAT: model availability across full duration; complete protection semantics | [ST-SCHED-005](12-findings-register.md#st-sched-005), [ST-SCHED-006](12-findings-register.md) | 4/3/4/M | M |
| Bound + iterative greedy (kill 100k-iter blow-up and RecursionError) | [ST-PERF-004](12-findings-register.md), [ST-PERF-008](12-findings-register.md), [ST-SCHED-012](12-findings-register.md) | 4/3/4/M | M |
| Surface dropped classes + global infeasibility reason | [ST-SCHED-001](12-findings-register.md#st-sched-001), [ST-SCHED-014](12-findings-register.md) | 4/3/3/M | M |
| Remove/repair dead `neighbor_impact` term; fix `find_conflicts` empty-reason gap | [ST-SCHED-015](12-findings-register.md), [ST-SCHED-009](12-findings-register.md) | 2/2/2/S | S |

**Completion criteria.** Oracle: raw optimizer output has zero hard violations on
all presets (not just post-drop); CP-SAT respects availability across duration and
all protection levels; 1200-class instance completes without RecursionError.

## Phase 4 — Core workflow UX

*Goal: the primary scheduling loop is clear and trustworthy.*

Proposals P1/P2 from [09](09-ui-ux-audit.md):

| Task | Findings | I/U/R/E | Effort |
|---|---|---|---|
| **P1** Conflict-aware cells (split + chip + warning + export annotate) | [ST-UI-001](12-findings-register.md#st-ui-001) | 5/4/4/M | M |
| **P2** One placement vocabulary everywhere | [ST-UI-002](12-findings-register.md#st-ui-002) | 4/3/2/S | S |
| Room analytics read the right key | [ST-UI-003](12-findings-register.md#st-ui-003) | 3/3/2/S | S |
| "Why unplaced?" panel (reuse negotiator) | [ST-SCHED-014](12-findings-register.md), [ST-UI-015](12-findings-register.md) | 4/3/3/M | M |
| Structured time-slot entry with validation | [ST-UI-014](12-findings-register.md) | 3/3/3/M | M |
| Reschedule dialog: plain-language modes + progress | [ST-PERF-001](12-findings-register.md#st-perf-001), UX | 3/3/2/S | S |

## Phase 5 — UI consistency & accessibility 🟡 MOSTLY COMPLETE

*Five of the six rows are done on `fix/phase-5-consistency`; see
[`PROGRESS.md`](PROGRESS.md#phase-5--mostly-complete) for what the register got
wrong and [`HANDOFF-PHASE6.md`](HANDOFF-PHASE6.md) for what is left.*

> **P5 (responsive shell) is deliberately not built.** Its numbers do not
> survive measurement on the native platform — the sidebar is a flat 350 px and
> never 430, truncation begins at W < 1159 rather than 1400, the dashboard's
> inner tabs never collapse, and the proposal's own fix buys zero extra columns
> because Qt clamps a splitter section to the sidebar's `minimumSizeHint`. The
> residual defect is real; it needs re-measuring into the register first.

Proposals P3/P4/P5:

| Task | Findings | I/U/R/E | Effort |
|---|---|---|---|
| **P4** Keyboard grid navigation + accessible names ✅ | [ST-UI-004](12-findings-register.md) | 4/3/3/L | L |
| **P3** Contrast fixes ✅ · year legend + redundant encodings ⬜ | [ST-UI-005](12-findings-register.md) ✅, [ST-UI-006](12-findings-register.md) ⬜ | 4/3/2/M | M |
| **P5** Responsive shell ⬜ *(numbers corrected, not built)* | [ST-UI-013](12-findings-register.md) | 3/3/2/M | M |
| Translation key-coverage CI check + fill gaps ✅ | [ST-UI-011](12-findings-register.md) | 3/3/2/S | S |
| Escape HTML/CSV/PDF inputs (injection) ✅ | [ST-UI-007](12-findings-register.md), [ST-UI-008](12-findings-register.md) | 3/3/3/S | S |
| Form UX (all errors, inline markers, QFormLayout); light-theme dialogs; warning-log polish; empty-state CTAs ⬜ *(triaged; much of it is already false — see HANDOFF-PHASE6)* | [ST-UI-015](12-findings-register.md) ✅ (Phase 4)…020 | 3/2/2/M | M |

## Phase 6 — Architecture & maintainability 🟡 MOSTLY COMPLETE

*Done on `fix/phase-6-architecture`; see [`PROGRESS.md`](PROGRESS.md#phase-6--mostly-complete)
for what the register got wrong and [`HANDOFF-PHASE7.md`](HANDOFF-PHASE7.md) for
what is left.*

> **The headline was not an architecture finding.** Scouting ST-ARCH-012 turned
> up **ST-ARCH-015**: Ctrl+Z, or deleting a class, could kill DERSİS silently at
> `0xC0000409` with no dialog and no traceback, and a second, quieter half that
> returned the *wrong class* and never raised at all. Fixed first.

| Task | Findings | I/U/R/E | Effort |
|---|---|---|---|
| Unify export engines; delete dead one ✅ | [ST-ARCH-003](12-findings-register.md) | 3/2/3/M | M |
| Move i18n/formatters to leaf package ✅ · break core→ui cycles 🟡 | [ST-ARCH-009](12-findings-register.md) ✅ (22→0), [ST-ARCH-010](12-findings-register.md) 🟡 (ratcheted) | 3/2/3/M | M |
| Delete dead legacy solver family ✅ · remaining unreachable symbols ⬜ | [ST-ARCH-011](12-findings-register.md) | 3/2/3/M | M |
| `TypedDict` state/class + mypy-on-core ✅ | [ST-ARCH-013](12-findings-register.md) | 3/2/3/M | M |
| Full-state undo snapshots ✅ | [ST-ARCH-012](12-findings-register.md) | 3/2/3/M | M |
| Extract `SessionStore` (persistence+undo) from `app.py` ⬜ *(descoped — see below)* | [ST-ARCH-005](12-findings-register.md), [ST-ARCH-006](12-findings-register.md) ✅ | 4/2/4/L | L |
| Split `dialogs.py` into a package ⬜ *(descoped — see below)* | [ST-ARCH-005](12-findings-register.md) | 2/1/2/M | M |

**Two rows were descoped to the defects they surfaced, with measurements.** The
`SessionStore` seam is worth 4.7 % of `app.py` and moves its Maintainability
Index by **exactly zero**; the audit's "no Qt dependency" premise is also no
longer true, because Phase 1 closed ST-ARCH-006 by giving `_auto_save` a Qt
error channel. Splitting `dialogs.py` leaves `setup_dialog.py` at **exactly
0.00** — the same floor the finding is about, relocated rather than fixed. Both
surfaced real defects (a latent data loss in `_flush_before_state_swap`, an
unwired Phase 5 escaping helper) and those are fixed. ST-ARCH-005 stays open and
needs a plan that moves the number.

**Also closed here:** [ST-FUNC-005](12-findings-register.md) (by deletion — the
crash only ever existed in the engine with no callers; 11 strict pins removed),
[ST-UI-005](12-findings-register.md) (reopened: Phase 5's fix never reached the
Excel file), [ST-UI-006](12-findings-register.md),
[ST-UI-007](12-findings-register.md) (its Qt half was written and never wired),
and the live items of [ST-UI-017/018/020](12-findings-register.md).

## Phase 7 — Testing, observability & release

| Task | Findings | I/U/R/E | Effort |
|---|---|---|---|
| Expand suite to the [top-10 untested behaviors](10-code-architecture-audit.md#test-infrastructure-zero-tests-dead-ci-and-a-risk-ranked-plan); offscreen smoke launch in CI | [ST-ARCH-001](12-findings-register.md#st-arch-001) | 5/3/5/L | L |
| Gate releases behind version tags; mark auto-builds prerelease; publish+verify checksums; sign installer | [ST-SEC-001](12-findings-register.md#st-sec-001), [ST-SEC-004](12-findings-register.md), [ST-SEC-006](12-findings-register.md) | 4/3/4/M | M |
| Fix installer ACL (no users-modify); real AppId GUID | [ST-SEC-003](12-findings-register.md), [ST-SEC-007](12-findings-register.md) | 3/2/3/S | S |
| Align crypto claims OR derive key from a user secret / OS keystore | [ST-SEC-002](12-findings-register.md#st-sec-002) | 3/2/3/L | L |
| Redact username paths in crash log / bug report | [ST-SEC-008](12-findings-register.md) | 2/2/2/S | S |
| Remove runtime `pip install` fallbacks | [ST-SEC-005](12-findings-register.md) | 2/2/3/S | S |
| Perf benchmark + solver-quality regression in CI (reuse `tests/scheduler_benchmark.py`) | [ST-PERF-001](12-findings-register.md#st-perf-001) | 3/2/3/M | M |

---

## Dependency graph (condensed)

```
Phase 0 (tests + 2 crash fixes)
   └─► Phase 1 (data & correctness)      ← needs the oracle from P0
          ├─► Phase 2 (performance)      ← worker-thread move is safe once state is safe
          └─► Phase 3 (engine hardening) ← needs one unified validator + oracle
                 └─► Phase 4 (workflow UX) ← conflict cells need engine to expose conflicts
                        └─► Phase 5 (consistency & a11y)
Phase 6 (architecture)  ← after P0 tests exist; enables safe refactor of P2/P3 internals
Phase 7 (test depth + release/security) ← continuous; release hardening can start anytime
```

Phases 0–1 are the non-negotiable core: they convert DERSİS from "produces
incorrect schedules with no safety net" to "produces correct schedules, verifiably".
Everything after is quality and scale. The five highest-leverage individual
changes are called out in [15-final-assessment.md](15-final-assessment.md).
