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
