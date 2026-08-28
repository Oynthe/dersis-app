# Handoff — Phase 7 (Testing, observability & release)

Phase 6 is **mostly** complete on `fix/phase-6-architecture`. This file is the
ready-to-paste prompt for the next session, plus what Phase 6 left behind.

Read [`PROGRESS.md`](PROGRESS.md#phase-6--mostly-complete) first — it records
what the register got wrong, which is more useful than what it got right.

---

## Ready-to-paste prompt

> You are continuing the DERSİS remediation (C:\dev\dersis-app), an offline
> PyQt6 school-timetabling desktop app (~48k lines Python, v1.0.0). A full
> stress-test audit lives in `stress-test/`; you are working the phased plan in
> `stress-test/14-implementation-roadmap.md`. Phases 0–5 are done and Phase 6 is
> mostly done. Your job is Phase 7 — testing depth, observability and release
> hardening — **plus the four Phase 6 carry-overs listed under "What Phase 6 did
> not finish" below.**
>
> READ FIRST, in this order:
> 1. `stress-test/PROGRESS.md` — what Phases 0–6 changed, and WHY the register's
>    recommendations were often not sufficient. Highest-value file.
> 2. `stress-test/HANDOFF-PHASE7.md` (this file)
> 3. `stress-test/14-implementation-roadmap.md` §"Phase 7"
> 4. `stress-test/12-findings-register.md` — canonical findings
> 5. `tests/README.md` — the suite's conventions; follow them, especially the
>    three about mutation testing, pixel assertions, and ratchets.
>
> STATE OF THE REPO
> - Branch for your work: `fix/phase-7-release`, cut from
>   `fix/phase-6-architecture` (or from `main` if that has merged).
> - Suite: **725 tests — 712 pass, 13 known-defect pins, 0 failures.** Both
>   lanes exit 0. `mypy` is clean over the five Qt-free packages.
>
> ENVIRONMENT
> - Python: `.venv-audit/Scripts/python.exe` — never a bare `python`.
> - Run tests from the repo root. CI runs `pytest -m "not slow"` and `mypy`.
> - `tests/conftest.py` sandboxes HOME at conftest-import time — mandatory.
>   Never import `scheduler_app` from a conftest at module scope.
> - Set `PYTHONIOENCODING=utf-8`. The app is Turkish-first.
> - **Use the `make_app` fixture** for any test that builds a `SchedulerApp`.
> - Any standalone probe script needs an `if __name__ == "__main__":` guard —
>   the optimizer uses multiprocessing and will otherwise fork-bomb on Windows.
> - **Beware the shell heredoc**: writing Python via `<<'EOF'` eats one level of
>   backslash escaping. Use the Edit tool for anything with escapes or
>   non-ASCII. Phase 6 hit this three times, on `\n` inside a string literal.

---

## What Phase 6 did not finish

Four carry-overs, in the order they are worth doing.

### 1. ST-ARCH-005 — the god object. **Needs a plan that moves the number.**

`ui/app.py` is 5 243 lines at MI **0.00**. Phase 6 removed its worst function
(`_write_excel`, CC 57 — the file's worst is now 27) and declined both seams the
roadmap proposed, with measurements:

| proposed seam | measured value |
|---|---|
| extract `SessionStore` (persistence + undo) | **4.7 %** of the file, **0.00** MI movement |
| split `dialogs.py` into a package | 14 of 15 modules reach radon A; `setup_dialog.py` stays at **exactly 0.00** |

Neither is wrong to do; both are worth less than the row implies, and doing them
would have been motion rather than progress. The audit's premise that the
persistence code is Qt-free is also **no longer true** — Phase 1 closed
ST-ARCH-006 by giving `_auto_save` a user-facing error channel, and that channel
is Qt.

The `dialogs.py` split is *safe* whenever someone wants it: the real hazard —
whether `scheduler_app/__init__.py`'s `_ShimLoader` survives the alias target
becoming a package — was tested with a throwaway replica of the real loader and
**all five checks pass**, including `scheduler_app.dialogs is
scheduler_app.ui.dialogs` identity.

If you work this row, decide first what number you are trying to move and how
you will know. MI 0.00 is a floor: it is driven by size, and no single
extraction escapes it.

### 2. ST-ARCH-010 — the 15-module knot. **The one seam the audit got right.**

`core` is a single strongly connected component of 15 modules once `logic.py`'s
13 remaining deferred imports are counted as the dependencies they are. The fix
is the audit's own proposal: split `logic.py` into primitives (occupancy, slots,
conflicts, layout) plus a `core/facade.py` holding the `optimized_*` bridges
with **normal** module-level imports.

`tests/test_import_layering.py` ratchets `MAX_CORE_SCC_SIZE = 15`,
`MAX_MUTUAL_IMPORT_PAIRS = 7` and `MAX_DEFERRED_IMPORTS_IN_LOGIC = 13`. Lower
them as you go.

**One measurement to save you a day.** 19 of the 21 deferrals measured at the
start of Phase 6 genuinely raise `ImportError` if promoted — but
`python -c "import scheduler_app.core.logic"` **succeeds for every one of
them**. The real entry path is `import scheduler_app.core.workflow`. Verify
promotions in a fresh subprocess against that, or you will believe you have
fixed something you have not.

### 3. ST-ARCH-011 — ~80 dead symbols remain, plus 131 unused imports

Phase 6 deleted the legacy solver family and 9 more symbols (`logic.py`
1668 → 1349). A reachability pass measures **91** unreachable symbols in total,
not the register's "~30". The remainder is mechanical, but two cautions:

* **Name collisions.** `respects_constraints` and `check_placement_explained`
  exist on both `logic` (dead) and `ConstraintValidator` (live and heavily
  used). A grep-driven deletion deletes the wrong one.
* **Some dead code is dead *and should not be*.** Phase 6 found two symbols
  with no callers that were fixes someone forgot to wire — `text_safety.qt_tooltip`
  and `_flush_before_state_swap`, the latter a latent data loss. Before deleting
  anything, ask whether its absence of callers is the bug.

Also outstanding: ~25 translation keys become orphans if the dead set goes.
`TRANSLATIONS` carries **22** locales, so that is ~525 (locale, key) pairs.
Remove them from `en` first or in lockstep, and move
`MAX_MISSING_LOCALE_KEY_PAIRS` deliberately.

### 4. ST-UI-013 — the responsive shell. **Re-measure into the register first.**

Still not built, and the reason has changed. Phase 5 deferred it because the
finding's numbers were wrong. Phase 6 measured natively again and found the one
number everyone treated as a constant is not one:

> **The sidebar's `minimumSizeHint` is the width of two translated strings.**

`12 px margin + width(tr("panels.open_slots")) + 4 + width(tr("panels.unplaced_classes"))`,
measured with real Segoe UI:

| locale | px | | locale | px |
|---|---|---|---|---|
| ja | 140 | | de | 240 |
| en | 165 | | ru | 253 |
| fr | 188 | | ar | 190 |
| **tr** | **195** | | | |

Never the 301 px on record in the register and in PROGRESS.md. So the
responsive breakpoint **differs per language**, and any threshold calibrated in
one locale is wrong in another. That is arguably a bigger finding than the row
and it is not written up anywhere yet.

Everything else Phase 5 recorded still applies: the lever is `setMaximumWidth`
(a `setSizes` call is clamped), `_expand_panel` resets both constraints so any
cap must be re-applied there, and auto-collapse needs a **user-intent state
machine** — a plain `resizeEvent` breakpoint was built and measured, and at
1000 px the user clicks Expand and a 1 px nudge re-collapses it.

**And do not trust a test that passes here without checking why.** Two tests
proposed for this row were measured to **pass before the fix** under `make_app`,
because it builds a never-shown window whose splitter is 640 px, not 1150.

---

## What Phase 6 changed that Phase 7 will touch

- **`scheduler_app/i18n/` is new and must stay a leaf.** `translations`,
  `day_keys`, `badge_formatter` and `tier_translations` live there and import
  nothing else from `scheduler_app`. `tests/test_import_layering.py` enforces
  it, `mypy.ini` covers it, and `build_nuitka.bat` now lists it — a frozen build
  would otherwise ship with no translation table.
- **There is one Excel writer**, `data_io/exporter.py::_export_excel`, and
  `export_schedule(state, "xlsx", path, mode=...)` is the way in. `mode` reaches
  Excel now, which it never did. Phase 7's "exporter golden files" idea is much
  cheaper than it was, and would now guard the engine users actually run.
- **`mypy` gates the engine at 0 errors** (`mypy.ini`, CI step "Type-check the
  engine"). `check_untyped_defs` is deliberately off at **168** errors, of which
  **zero** are `[name-defined]`; turning it on is a project, not a config flip.
- **Undo is full-state and restores in place.** Anything that holds an alias to
  `state_data` keeps working *because* of that; do not "simplify" it to a
  rebind.
- **`summary` gained `cpsat_failure`.** Nothing renders it yet — that is a
  natural Phase 7 observability item, and it is the difference between "Thorough
  mode is broken on this machine" and silence.
- **`tests/test_import_layering.py` carries four ratchets.** They are meant to
  go down. Raising one needs a sentence in the commit saying why.

## The single most useful thing Phase 6 learned

**Ask which copy of the code the user actually runs, before trusting anything
the suite tells you about it.**

ST-ARCH-003 was filed as a maintainability finding — two export engines, one
duplicated. It was not. The engine with no callers was the one the tests
exercised, the one Phase 5 fixed, and the one the audit attributed a High-severity
crash to. The consequences, all measured:

* Phase 5's WCAG fix **never reached the Excel file**, and the guard written to
  prevent that drift scanned two modules by name, neither of which was the live
  writer;
* **ST-FUNC-005's crash was never reachable** in production, and 11
  `xfail(strict=True)` pins had been guarding a bug in dead code;
* **two silent data-loss bugs in the live writer** were invisible to a suite of
  48 export tests, and surfaced as failures within seconds of the merge.

Nothing about that was discoverable from the finding text, or from the tests, or
from the fact that everything was green. It came from asking what
`_export_to_excel` actually calls.

The house rule from Phases 1–5 still holds and now has a corollary: **verify the
evidence, not just the claim — and check that the evidence is about the code
that runs.**
