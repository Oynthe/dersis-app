# DERSİS regression suite

The safety net demanded by [ST-ARCH-001](../stress-test/12-findings-register.md#st-arch-001)
("zero automated tests"). Every fix in the
[implementation roadmap](../stress-test/14-implementation-roadmap.md) lands with a
test here that failed before it and passes after.

## Running

```bash
.venv-audit/Scripts/python.exe -m pytest              # everything
.venv-audit/Scripts/python.exe -m pytest -m "not slow"  # what CI runs
.venv-audit/Scripts/python.exe -m pytest tests/test_scheduler_invariants.py -q
```

CI runs `pytest -m "not slow"` on Ubuntu with `QT_QPA_PLATFORM=offscreen`.

## The one rule you cannot break

`scheduler_app.storage` binds `~/Documents/Dersis` into a module-level constant at
**import** time. `tests/conftest.py` therefore redirects `HOME`/`USERPROFILE` to a
throwaway temp directory at conftest-import time, before pytest collects anything.

- Never import `scheduler_app` from a `conftest.py` at module scope.
- Never add a pytest plugin or `-p` flag that imports `scheduler_app` earlier.
- Use the `dersis_home` fixture in any test that touches persistence; it gives each
  test its own `Documents/Dersis` tree and clears the process-wide cached master key
  on both sides.

## Modules

| Module | Guards |
|---|---|
| `test_scheduler_invariants.py` | the correctness spine — the independent oracle against the production optimizer |
| `test_optimizer_occupancy.py` | ST-SCHED-001/010 — the greedy/LNS seam, ref-counted occupancy |
| `test_validator_unification.py` | ST-ARCH-004/007/009 — every path reaches one verdict |
| `test_cpsat_semantics.py` | ST-SCHED-005/006 — availability across duration, all protection levels |
| `test_greedy_bounds.py` | ST-PERF-004/008, ST-SCHED-012 — bounds, convergence, no recursion |
| `test_unplaced_diagnostics.py` | ST-SCHED-014/015 — dropped classes, global infeasibility |
| `test_storage_roundtrip.py` · `test_import_roundtrip.py` · `test_export_smoke.py` | persistence and I/O |
| `test_grid_integrity.py` · `test_setup_reconcile.py` · `test_state_transactions.py` | ST-DATA family |
| `test_solver_worker.py` · `test_refresh_cost.py` · `test_reschedule_overhead.py` · `test_warning_log_growth.py` · `test_feedback_log_scaling.py` | ST-PERF family |
| `test_cell_contrast.py` | ST-UI-005 — every painted colour clears WCAG AA on all 24 cell backgrounds, from one source |
| `test_cell_layout.py` | the conflict pill must not paint over the protection badge |
| `test_grid_keyboard.py` | ST-UI-004 — the lane-aware cursor, key handling, and what a screen reader is told |
| `test_input_escaping.py` | ST-UI-007/008 — user text survives reportlab, Qt and a spreadsheet |
| `test_translation_coverage.py` | ST-UI-011 — no raw key reaches a user; the locale backlog is a ratchet |
| `test_ui_affordances.py` | the app must not lie about where things are (toast position, honoured cells) |
| `test_smoke_environment.py` | the harness itself — proves HOME is sandboxed |

## Fixtures

| Fixture | Scope | What it gives you |
|---|---|---|
| `dersis_home` | function | fresh `Documents/Dersis` root, storage rebound to it |
| `qapp` | session | offscreen `QApplication` (Qt permits exactly one per process) |
| `make_state` / `make_preset` | session | deterministic dataset builders |
| `_pinned_language` | session, autouse | UI language pinned to Turkish |

Language is pinned because the Excel importer looks sheets up by their **translated**
title — an unpinned locale makes template round-trip tests irreproducible.

## Support modules

- `_support/dataset_gen.py` — `make_state(...)`, `make_preset(name, seed=42)`.
  Presets: `tiny` 5 · `small` 25 · `normal` 80 · `large` 250 · `very_large` 600 ·
  `pathological` 1200 classes.
- `_support/schedule_oracle.py` — the independent hard-constraint oracle.
  `check_schedule()` re-derives occupancy from scratch and deliberately does **not**
  reuse the production `ConstraintValidator`, so a bug in that code cannot hide
  itself. Both were promoted from the audit's `stress-test/tests/` tree.

## Markers

`slow` (>10 s, excluded in CI) · `ui` (needs PyQt6 + a platform plugin) ·
`engine` (runs the optimizer) · `excel` (pandas + openpyxl) · `pdf` (reportlab).
`--strict-markers` is on, so an unregistered marker is an error.

## Conventions

- **Every test docstring names the finding ID it guards** and says, in one sentence,
  what a failure means for a user. Those docstrings are the documentation.
- A defect scheduled for a later phase is pinned with
  `@pytest.mark.xfail(strict=True, reason="ST-XXX-NNN — …; fixed in Phase N")`.
  `strict=True` means the suite goes **red** when the fix lands — that is the signal
  to delete the marker. Use `strict=False` only when the failure is genuinely flaky,
  and say why in a comment.
- Never weaken an assertion to make a test pass. A test that passes for the wrong
  reason is worse than no test.
- **Mutation-test every new test against the defect it names.** Phase 5 wrote four
  that pinned nothing and only found out this way: an ST-UI-011 test that stayed
  green with the finding fully restored (the key lives in a data table, invisible
  to an AST scan for `tr("literal")`); an ST-UI-008 test whose payload went into
  the class name, which `CellRichText` never makes a formula; a pill-overlap
  assertion that reduced to `f(x) == f(x)`; and a double-scroll test that could
  not fail because the grid fits the viewport offscreen and nothing can scroll.
- **Never assert an absolute pixel measurement.** `QT_QPA_PLATFORM=offscreen` has
  no Segoe UI at all — `QFontInfo(QFont("Segoe UI", 9)).family()` is `""` — and
  advances run 1.5–2x native. That changes which cell rows get *dropped*, not
  only their size, so a layout constant measured in CI is wrong on a desktop.
  Assert relations between two quantities measured in the same process.
