# Handoff — Phase 10

Phase 9 is complete on `fix/phase-9-convergence` (3 commits, cut from `main` at
`f049964`). This file is what the next session works from.

Read [`PROGRESS.md`](PROGRESS.md#phase-9--complete) first. Its "What the briefs
got wrong" section records four prescribed fixes that were wrong, and that is
more useful than the list of what worked.

---

## The one thing to take from Phase 9

**Four times a prescribed fix was wrong, and every time it was found by building
it and watching it fail — never by argument.** Twice the wrong fix would have
introduced a *second* defect; once it would have shipped green while permitting
the exact defect it was written for; once it would have created an ST-SEC blind
spot in a file where two previous phases had already found one.

So: the loop is not a formality.

> reproduce the original failure → implement the fix → re-run the *same* probe
> and show it is gone → regression-check the neighbours → mark resolved

And its two hard edges, both of which paid this phase:

* **If you cannot reproduce it, change nothing.** Four of Section C's ten items
  did not survive measurement, and acting on one of them (C10) would have been a
  regression. `NOT_REPRODUCIBLE` is a result.
* **Adversarial testing runs only after you believe the fixes are done**, and its
  job is to validate them. Phase 9 ran it that way and it found two regressions
  the phase's own fixes had introduced, plus a policy violation the main agent
  added by hand.

---

## STATE OF THE REPO

* Branch `fix/phase-9-convergence`, 3 commits ahead of `main`.
* **Fast lane: 1271 selected · 1269 passed · 2 xfailed · 0 failed · exit 0.**
  (1144 / 1142 / 2 at the start of Phase 9: **+127 tests**.)
* Slow engine lane: 52, exit 0. `ST-SCHED-013` is `strict=False` by design — it
  xfails on a loaded box and XPASSes on an idle one, and both are fine.
* `mypy` clean over 42 source files.
* **Translation backlog 2548 against a 2548 ceiling — the ratchet never moved
  in Phase 9**, because every new string shipped in all 22 locales. All four
  layering ratchets still `0`.
* `MAX_SCHEDULERAPP_METHODS` 154 with the class at **153**.
  `MAX_APP_PY_TOTAL_MCCABE` was raised 915 → 920; the per-function accounting is
  in the constant's docstring.
* The two remaining `xfail` pins are still the **ST-DATA-013** pair. They are
  documentation, not defects. Leave them.

### ⚠ `main` still has never been pushed

`origin/main` is at `b6c453b`, the **Phase 6** merge. Local `main` is 102 commits
ahead and carries Phases 7 and 8; Phase 9 adds 3 more on its own branch. A `v*`
tag pushed today would still run the *old* workflow tree.

---

## ENVIRONMENT — read every line

* Python: `.venv-audit/Scripts/python.exe` — **never a bare `python`**. It lives
  in the main tree; `scheduler_app` is **not** pip-installed and resolves from
  the current working directory, so run pytest **from the tree you mean to test**.
* `PYTHONIOENCODING=utf-8` on every command. The machine default is **cp1254**
  and `print()` raises on a Turkish letter or a `→`.
* `QT_QPA_PLATFORM=offscreen` for Qt tests; **never for a geometry measurement**.
  Offscreen has no Segoe UI, its fallback is fixed-pitch, and it wraps about
  twice as often: the same 500-warning dialog measures **24 110 px** on the real
  Windows platform and **56 100 px** offscreen. Neither is "the" number.
* Any standalone probe needs `if __name__ == "__main__":` — the optimizer uses
  multiprocessing and will otherwise fork-bomb Windows.
* Use the `make_app` fixture for anything that builds a `SchedulerApp`.

### Corrected: the line-ending rule

`HANDOFF-PHASE9.md` said "every tracked file is CRLF". **Measured, that is not
true.** The working tree is **124 CRLF files and 11 bare-LF** (`git ls-files
--eol` shows the index is uniformly `i/lf`; six of the eleven predate Phase 9).

The *practical* rule survives unchanged and is more important than the reason
for it: **use the Write/Edit tools for every source change.** A `\n`-based
`str.replace` on a CRLF file is a **silent no-op**. That cost Phase 8 two no-op
patches, and it cost Phase 9 one — a `'@classmethod\n'` replace that left the
decorator in place at module scope and produced 27 failures with
`TypeError: 'classmethod' object is not callable`. You cannot tell which kind of
file you have by looking, so do not hand-roll the edit.

### Four traps that cost real time

1. **`pytest`'s final summary line is swallowed in this environment.** The words
   "passed"/"failed" never appear in captured output. **Gate on the exit code**
   and count outcomes from the progress characters (`.`, `F`, `x`, `X`, `E`, `s`)
   on the lines ending `[ NN%]`.
2. **Never background a test run and poll for it.** Foreground, with an explicit
   `timeout`. Three Phase 8 agents hung permanently in `until grep -q "passed"`
   loops and had to be killed.
3. **The full fast lane now takes ~11 minutes and EXCEEDS the 600 s tool cap.**
   Run it in the background, or run single modules (5–20 s), which is almost
   always what you want.
4. **Worktrees are provisioned from a stale base.** Every worktree agent in
   Phase 9 — as in Phases 6, 7 and 8 — found its worktree cut from the *Phase 6*
   merge. Make `git log --oneline -3` and `git merge <the phase branch>` the
   first action of any worktree agent, and have it verify
   `scheduler_app.__file__` resolves **inside its own worktree** before trusting
   a single measurement.

### The rule Phase 8 passed on, which held for a whole phase

**Translate into all 22 locales instead of raising the translation ratchet.**
Phase 9 added six user-facing strings across B and C and moved
`MAX_MISSING_LOCALE_KEY_PAIRS` **zero** times. It costs one scripted insert.

And the measurement rule that goes with it: **count the backlog the way the test
counts it**, i.e. with `import scheduler_app.i18n.tier_translations` first.
Without that import you get ~1700 and a false sense of headroom; the real figure
is 2548 against 2548. Three phases in a row fell into this; Phase 9 did not,
because it measured before writing anything.

---

## OPEN WORK, in the order it deserves

### 1. B4's exposure on the load paths — needs a DECISION, not a patch

`open_file` and `_auto_load` both load a state that can carry dangling room
names, and **neither reconciles**. A file saved by any build before Phase 9 still
has them, and `AddClassDialog` rebuilds both room fields from the live room list
— so opening such a class and pressing OK **deletes** the constraint.

Phase 9 tried the obvious fix and it was wrong. `core/models.py`
(`normalize_class_data`'s neighbourhood) states the policy in writing:

> Deliberately NOT called from `normalize_state_classes` (and so not from the
> .egu load path): unplacing orphans at load time would silently discard the
> user's own placements with no way to see or undo it… Callers decide what to do
> — **warn, list, or offer to reconcile**.

`_reconcile_after_setup()` was added to `open_file` and chose none of the three.
Measured on a real `.egu` from an older build: **6 of 6 lessons unplaced, undo
depth 0**, and `mark_current_state_as_baseline()` then recorded the wrecked state
as having no unsaved changes — one save and the term's placements are gone from
disk. It was reverted, with the policy quoted at the site.

So the work is to build one of the three the policy names. `_auto_load` is the
harder half: it runs from `__init__` before `_build_main()`, so there is no
widget for a report, and `_pending_settings_report` is a single slot that is
already contended (see `_flush_startup_settings_report`, which puts the damaged-
log report first for exactly this reason).

### 2. B1/B2 a third time, in `_bulk_schedule` — found, not reproduced

`_bulk_schedule` (`ui/app.py`, the `actions.bulk_schedule` push) has the same
pre-emptive shape `_place_classes_batch` just lost: `_push_undo` fires before
`_schedule_new_classes`, and that callee can end in
`self._workflow.rollback_schedule(...)` when the user presses Cancel in
`BulkResultsDialog`. The rollback restores the placements. It does **not** restore
the redo stack `_push_undo` cleared, nor the `_undo_stack[0]` it evicted at the
50-entry cap.

Not reproduced — it needs the BulkAdd + BulkResults dialog pair driven end to
end — and not fixed, because the fix changes `_schedule_new_classes`'s contract:
the rollback would have to become the thing that decides, the way the placement
comparison now decides in `_place_classes_batch`. **Reproduce it first.**

Related, same area, cheap: `_execute_drop_anywhere` sets `_drag_success = True`
unconditionally after `_place_classes_batch`, even when the batch placed nothing.
It causes no false toast today *only* because `_start_drag_unplaced` shows its
success toast solely for `len(drag_classes) == 1` while `_execute_drop_anywhere`
only fires for `len > 1`. Two lines apart, in different methods, with nothing
pinning the coincidence.

### 3. `AddClassDialog` lies about its own caption

After B3's "No", the form is re-shown seeded through the only channel the dialog
has, `edit_cls=`, and the title is derived from it — so a user **adding** a class
who answers No gets all their data back under an **"Edit Class"** caption. Every
field is correct; only the caption lies, and only on the add path's second
showing. The fix is a keyword-only `title=None` on `AddClassDialog.__init__`; it
was not done because `dialogs.py` was owned by another agent and because
choosing the title caller-side costs a branch `ui/app.py` did not have room for
at the time.

### 4. Parity gap, not a risk: the excluded-rooms dialog bound

`tests/test_phase9_b6.py` bounds the 500-row dialog for the room-type warning and
its `allowed_rooms` sibling. Phase 9 added two more per-row keys
(`warnings.unknown_excluded_rooms`, `warnings.allowed_rooms_too_small`) that can
each fire 500 times, and no test pins them. **Checked: this is a pinning gap, not
a risk** — `ui/validation_report.py` routes on `_fits_a_plain_box(detail)`, so
the bound is structural and independent of which key produced the lines. Worth
adding for parity.

### 5. The probe files are named after a phase, not a behaviour

Twelve permanent regression tests are called `tests/test_phase9_*.py`. Nothing
outside them references the names (checked), so renaming is safe and mechanical.
`test_phase9_b7.py` tells a future reader nothing; `test_feedback_log_health.py`
would. Low value, zero risk, do it when touching them anyway.

---

## What Phase 9 deliberately did NOT do, and why

Do not re-open these without new evidence; each was measured.

| Item | Why it stands |
|---|---|
| **C2** — "exact equality re-opens ST-FUNC-010" | The claim inverts the defect. Exact equality is the **guard**: tightening a drop-predicate can only ever drop FEWER rows. Measured by shouting and lower-casing every cell of row 2 of every sheet — nothing is lost; a row is *gained* and the workbook is refused loudly. |
| **C4** — "the fallback must subtract `excluded_rooms`" | The outcome happens; the cause is wrong. Where a lab class reaches a lecture hall the fallback is already `[]`, so subtracting changes nothing — and the rescue sentence fires, so it is not silent. Where the fallback is non-empty the subtraction is a placement no-op. Building it turns an existing test red and re-opens the ST-FUNC-009 inversion. |
| **C8** — "the batch early-return leaks `_drag_undo_pushed`" | Dead for grid drags: `_start_drag_gfx` filters to PLACED classes and unplaces only the primary, so with `len>1` at least one member is still placed. Measured over 11 cases. Live only for sidebar drags, which hold no snapshot. |
| **C10** — "the regex needs a `$` anchor" | **The proposed fix IS the defect.** Proven by mutation: move `exit /b 1` into `build_embed.bat`'s else branch and the current pattern catches it RED, while the anchored version runs past `) else (`, finds the wrong `exit /b 1`, and goes GREEN. The sibling that *does* use `^\)$` is correct because that block ends in a bare `)`. |
| **C7** — `InfeasibilityAnalyzer(state, None, None)` | The crash is real; `ConstraintNegotiator` is the only construction site in the package and it cannot pass `None`. No menu action, import or solve reaches it, so the PyQt6 slot-death multiplier never engages. Closed with one docstring sentence instead of a guard for a caller that does not exist. |
| **Release rehearsal** (`v1.0.1` on a scratch fork) | **The user declined it.** The script is in `HANDOFF-PHASE8.md` §6 if it is ever wanted. |
| **PDF shaping for ar/fa/hi** | `_resolve_pdf_fonts` **short-circuits on shaped scripts**, so no substitute face is ever tried. Needs a real shaping engine. reportlab 5.0.1 ships `rlbidi`/`uharfbuzz` extras — but `requirements-lock.txt` pins **4.4.10** for the shipped build, so verify against that version. |
| **ST-ARCH-005** (`ui/app.py` god object) | Six candidate seams were built in Phase 7; **all six leave the MI at exactly 0.00**, because the complexity term alone (−205) exceeds the formula's 171 constant. Extraction is not the cure — the ratchets are. |
| **`requirements-lock.txt` pins** | 13 of 26 have drifted and 3 name packages the audit venv does not install. **The test CI does not read this file** — only the build paths do — so regenerating it changes what *ships* and cannot be verified without running the build. |
| **The two ST-DATA-013 pins** | Library-level properties with no production producer. Documentation. |

---

## The rule Phase 9 would most like to pass on

**A test that plants the state it is meant to observe is measuring nothing.**

Phase 8 recorded this once: deleting `self._drag_undo_pushed = True` from
production left the whole suite green, because the test helpers set the flag
themselves. Phase 9 hit the identical pattern again, one layer over — two
production lines could be deleted **together** with the suite green, and removing
them let a sidebar drag plus one Ctrl+Z resurrect an abandoned gesture's
placement under the wrong label. The test that set that state up **hand-assigned
`_drag_undo_entry = None`** instead of letting `_start_drag_unplaced` clear it,
so it could not notice production had stopped clearing it.

The check is cheap and it is the only one that works: **mutate the production
line, confirm the mutation actually landed with `git diff --stat`, and run the
suite.** A green suite under a real mutation is a finding about your test, not a
fact about the code. Phase 9 ran ten mutations on the drag path alone; seven were
killed and three survived, and the three were the finding.
