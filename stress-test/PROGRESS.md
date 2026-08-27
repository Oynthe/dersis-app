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
| 2 — Performance foundations | Not started | — |
| 3 — Scheduling engine hardening | Not started | — |
| 4–7 | Not started | — |

---

## Phase 2 — complete

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
