# Roadmap progress

Living tracker for the [implementation roadmap](14-implementation-roadmap.md).
The audit documents (01–15) are the frozen 2026-08-26 baseline; this file records
what has changed since. Per-finding state also lives in the
[findings register](12-findings-register.md).

| Phase | State | Branch |
|---|---|---|
| **0 — Critical stabilisation & test scaffold** | ✅ Complete | `fix/phase-0-test-scaffold` |
| 1 — Data & correctness | Not started | — |
| 2 — Performance foundations | Not started | — |
| 3 — Scheduling engine hardening | Not started | — |
| 4–7 | Not started | — |

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

- **GitHub Actions is not executing anything in this repository**, so the CI fix
  is unverified end to end. Diagnosed on PR #7: the push and the PR created **no
  workflow run at all** — not even `Claude Code Review`, which subscribes to
  `pull_request: [opened, synchronize, ready_for_review, reopened]` with no branch
  filter and ran fine on PR #6. Closing and reopening the PR to re-fire the event
  also produced nothing. `GET /actions/permissions` reports
  `enabled: true, allowed_actions: all`; the repo is public, not archived, not
  disabled. The last workflow run of any kind was **2026-06-19, 68 days before
  this branch** — consistent with GitHub's inactivity shutoff, which is cleared by
  the repo owner from the Actions tab ("I understand my workflows, go ahead and
  enable them"). **Action required from the maintainer**: re-enable Actions, then
  confirm the Ubuntu path (offscreen Qt, apt Qt libs, reportlab fonts), which has
  only ever been observed green locally on Windows.

  Side effect worth knowing: `ci.yml` and `claude.yml` are absent from
  `GET /actions/workflows` even though both have been on `main` since the initial
  commit. That index appears to list only workflows that have run at least once —
  `ci.yml` never could (it triggered on `master`, a branch this repo has never
  had) and `claude.yml` needs an `@claude` mention. It is a symptom, not a cause.
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
