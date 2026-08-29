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
.venv-audit/Scripts/python.exe -m mypy                # the engine type gate
```

**Two things about running this suite on the audit machine.** The fast lane is
now ~11 minutes and **exceeds a 600 s command timeout** — run it in the
background, or run single modules (5–20 s), which is what you want almost every
time. And `pytest`'s final summary line (`N passed, M xfailed in Xs`) is
**swallowed** in captured output here: it ends at the "slowest durations" block
and the words "passed"/"failed" never appear. **Gate on the exit code**, and
count outcomes from the progress characters on the lines ending `[ NN%]`. Two
agents in Phase 8 hung permanently waiting for a summary line that never comes.

CI runs two jobs. **Validate** runs `pytest -m "not slow"` on Ubuntu with
`QT_QPA_PLATFORM=offscreen`, plus `mypy` over the five Qt-free packages
(`mypy.ini`), gated at **zero** errors. **Scheduling invariants** runs the
`slow` engine gates that the first job deselects:

```bash
pytest tests/test_scheduler_invariants.py tests/test_greedy_bounds.py \
       tests/test_optimizer_determinism.py tests/test_solver_work.py
```

Until Phase 7 the second job ran only `test_scheduler_invariants.py`, so **13 of
the suite's 19 `slow` tests executed in no CI job at all** — including
`test_greedy_bounds.py`'s placement floors and both slow reproducibility pins,
which had been written for exactly that purpose. Seven still do
(`test_optimizer_occupancy.py`'s two `_on_small` twins, whose fast versions run
in Validate; `test_reschedule_overhead.py` ×2; `test_warning_log_growth.py` ×2,
which need Qt; `test_cpsat_subprocess_boundary.py` ×1). Measured budget: that
command is 47 passed + 1 xfailed in **633 s** on the audit machine, and
`ubuntu-latest` runs this workload 1.87x faster — ~6 min against a 25-minute
timeout.

**If you add a `slow` test, name the job that will run it.** A `slow` marker with
no job behind it is a test that exists and never executes.

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
| `test_greedy_bounds.py` | ST-PERF-004/008, ST-SCHED-012 — bounds, convergence, no recursion; the placement floors and `repaired_conflicts == 0` at 25 and 80 classes |
| `test_solver_work.py` | ST-PERF-001 — how much work the solver does, as a ratchet on `check_placement` calls; ST-SCHED-001's `repaired_conflicts`; the open ST-SCHED-013 xfail |
| `test_protection_semantics.py` | ST-ARCH-001 item 5 — `soft` and `improve_only` on the **default** (greedy+LNS) engine, which `test_cpsat_semantics.py` never covered |
| `test_sequential_classes.py` | ST-ARCH-001 item 7 — non-joint multi-target classes: block length, per-sub-block occupancy, generator/validator agreement |
| `test_day_key_normalization.py` | ST-ARCH-001 item 9 — day labels become day keys on open/import; stale placements and pins are released |
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
| `test_unplaced_panel_identity.py` | ST-ARCH-015 — the sidebar addresses classes by identity; Ctrl+Z must not kill the app |
| `test_import_layering.py` | ST-ARCH-009/010 — the engine must not import the interface; four ratchets |
| `test_domain_shapes.py` | ST-ARCH-013 — `ClassDict`/`StateDict` match their constructors |
| `test_full_state_undo.py` | ST-ARCH-012 — undo covers the axes, and restores in place |
| `test_written_but_unwired.py` | ST-UI-007 / ST-ARCH-011 — fixes that existed and were never called |
| `test_cpsat_subprocess_boundary.py` | what survives `spawn`: the UI language, and a dead child |
| `test_form_affordances.py` | ST-UI-018/020 — a typed lecturer is registered; every error is shown |
| `test_year_legend.py` | ST-UI-006 — the colour key groups years that share a swatch |
| `test_smoke_environment.py` | the harness itself — proves HOME is sandboxed |
| `test_text_fold.py` | ST-ARCH-001 item 9 — the one case-folding rule, swept over all 22 locales. Mostly a **falsification harness**: it exists to stop the next agent building the Turkish fold the Phase 7 handoff prescribed |
| `test_drag_and_drop.py` | ST-ARCH-012 — the gesture end to end, including the **real** `_start_drag_gfx` driven through both a commit and a cancel |
| `test_pdf_locale_coverage.py` | the PDF font property: what cannot be drawn must be a subset of what needs shaping — asserted as a property, never as a host outcome |
| `test_packaging_manifest.py` · `test_release_pipeline.py` | installer/workflow files read as **data**, not grepped as text; every executable the installer's shortcuts point at must be verified by the release lane |

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
- **A ratchet is a ceiling that may only go down.** `test_import_layering.py`,
  `test_translation_coverage.py` and `test_solver_work.py` all carry measured
  maxima. Adding a violation turns the suite red; removing one means lowering the
  ceiling in the same commit, so ground gained cannot be quietly given back.
  Raising one is a deliberate act and needs a sentence in the commit saying why.
- **Gate on counts, never on the wall clock.** Measured over 11 consecutive
  `ubuntu-latest` runs of identical code: runner variance 1.36–1.49x (historical
  outlier 3.38x), and the runner is **1.87x faster** than the audit machine, so a
  locally calibrated threshold is ~1.9x wrong before variance is considered. Over
  the same runs `ConstraintValidator.check_placement` call counts were **bit-exact**
  across processes with randomised `PYTHONHASHSEED`. `test_solver_work.py` gates on
  the count; the suite's one wall-clock assertion
  (`test_reschedule_overhead.py`) is a **ratio of two times measured inside one
  call**, so machine speed divides out. Write the second shape or none.
- **A cost gate and a quality gate are not substitutes.** Measured: a
  `check_placement` stubbed to bless every cell stays inside the work ratchet at
  both scales while producing 16 (`tiny`) and 138 (`small`) hard violations;
  removing the LNS stopping condition triples the work while `placed`,
  `hard_violations` and `q_after` do not move at all. Each is blind to exactly
  what the other catches, so neither may be deleted as redundant.
- **Ask which copy of the code the user runs.** Phase 6's sharpest lesson:
  `tests/test_export_smoke.py` had 48 tests against an Excel engine with **no
  production caller**, while the writer the menu actually reached had three.
  Phase 5's contrast fix landed on the untested-by-users copy and shipped
  broken for a phase; two data-loss bugs in the live writer were invisible.
  Green is not coverage if it is coverage of the wrong module.
- **Confirm the mutation landed before believing its result.** `git diff --stat`
  after applying it. Phase 7 measured an *unmutated* tree for 13 of 24 patterns
  because every workflow file is CRLF and the patterns silently did not apply,
  and concluded "your test pins nothing" when nothing had happened. Phase 8 then
  found four tests that pinned nothing — and **every one was caught only because
  the mutation was actually run**: a redo assertion that could not fail; a
  matcher that reduced to `"" in anything` because it sliced a translated string
  opening with a placeholder (two tests passed with the production code deleted);
  a warning test whose discriminator alone already changed the wording; and a
  substring check that had to become an exact-sentence check before it could see
  its own mutation. **A green mutation is a finding about your test, not a fact
  about the code.**
- **A hand copy of production is not production.** Phase 8's headline drag fix
  passed a green suite with its production trigger deleted, because the test
  helpers set the flag themselves. If a module reproduces production's field
  assignments in a helper, at least one test must drive the **real** entry point
  — substituting a module-level name (e.g. `scheduler_app.ui.app.QDrag`) is
  usually enough and is cheaper than a Qt harness.
- **Translate a new user-facing string into all 22 locales rather than raising
  the translation ratchet.** It adds zero missing pairs, so the ceiling never
  moves, and it costs one scripted insert. And **count the backlog the way
  `test_translation_coverage.py` counts it** — with
  `import scheduler_app.i18n.tier_translations` first, which merges 52 further
  `en` keys. Three consecutive phases have taken that count without the import
  and been ~850 pairs wrong.
- **Never assert an absolute pixel measurement.** `QT_QPA_PLATFORM=offscreen` has
  no Segoe UI at all — `QFontInfo(QFont("Segoe UI", 9)).family()` is `""` — and
  advances run 1.5–2x native. That changes which cell rows get *dropped*, not
  only their size, so a layout constant measured in CI is wrong on a desktop.
  Assert relations between two quantities measured in the same process.
