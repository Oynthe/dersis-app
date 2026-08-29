# Handoff — Phase 10

Phase 9 is complete and **merged into local `main`** (`a561a71`). This file is
what the next session works from, in the order the user set.

Read [`PROGRESS.md`](PROGRESS.md#phase-9--complete) first. Its "What the briefs
got wrong" section records four prescribed fixes that were wrong, and that is
more useful than the list of what worked.

---

## The scope, and why it is this scope

After Phase 9 merged, a four-angle read-only audit enumerated everything still
open. The user then set the split explicitly:

> "1'den 17'e kadar olan sorunların hepsini çözelim phase 10'da aynı mantıkla.
> ayrıca B, C, D, E, F, G, H'de düzeltilmeli ama phase 11'de, A şimdilik dursun."

* **Phase 10 = items 1–17.** Every open defect a *user* can reach. That is the
  whole of the application-facing backlog: 1 High, 4 Medium, 12 Low.
* **Phase 11 = items B–H.** Release, packaging and CI. Real, and none of it
  touches a running user — it hits whoever ships next.
* **Item A is parked on the user's instruction.** `origin/main` stays 108
  commits behind. Do not push. Do not tag. See "Do not do this" below.

**"Aynı mantıkla" is the operative phrase**, and it means the loop, per item,
with no exceptions:

> reproduce the original failure → implement the fix → re-run the *same* probe
> and show it is gone → regression-check the neighbours → mark resolved

Nothing is marked resolved on inspection, on an intended change, or on a
plausible explanation. **If you cannot reproduce it, mark it
`NOT_REPRODUCIBLE` and change nothing.** Phase 9 did that four times out of ten
in section C and one of those four "fixes" would have been a regression.

---

## ⚠ READ THIS BEFORE YOUR FIRST COMMIT — both god-object ceilings are full

This is the single most important operational fact for Phase 10, it is new, and
**nothing recorded it before this file**. Measured with the ratchet module's own
counter at `a561a71`:

| ceiling | actual | headroom |
|---|---|---|
| `MAX_APP_PY_TOTAL_MCCABE` | **920 / 920** | **0** |
| `MAX_DIALOGS_PY_TOTAL_MCCABE` | **885 / 885** | **0** |
| `MAX_SCHEDULERAPP_METHODS` | 153 / 154 | 1 |
| `MAX_SCHEDULERAPP_TOTAL_MCCABE` | 817 / 830 | 13 |

**Adding one `if`, `and`, `except`, ternary or comprehension `for` to
`ui/app.py` or `ui/dialogs.py` turns the fast lane red on the very next commit.**
At least ten of your seventeen items live in one of those two files.

So plan for it up front rather than discovering it item by item:

* **Prefer module scope and new modules.** Phase 9 did this four times —
  `_commit_undo_entry`, `_confirm_lecturer_reassignment`,
  `ui/validation_report.py`, and `_repair_report_message` + `_conflict_label` —
  which is why the method count came *down* to 153 while behaviour was added.
  Moving a function out of the class does not lower the FILE's McCabe; moving it
  to a different file does.
* **Several of these items are natural extractions**, not additions. Items 10,
  12 and 13 are widget behaviour that has no business in a 5 800-line window
  class; item 16 is a one-token fix in four files that *reduces* nothing but
  costs nothing either.
* **When you do raise a ceiling, the ratchet's contract demands a sentence in
  the commit message saying why**, and Phase 9's raise (915 → 920) set the bar:
  a per-function accounting of what moved and what it bought, in the constant's
  docstring. Do not raise both ceilings in one commit and do not raise either
  without the accounting.
* `BANKED_HEADROOM` is 20 and it cuts **both** ways: a ceiling sitting more than
  20 above reality must be *lowered* in the same commit that earned it.
  `MAX_SCHEDULERAPP_TOTAL_MCCABE` is 13 below its ceiling right now — Phases 8–9
  took that out without banking it. Lowering it to 817 is free and is the kind
  of thing that should be done while someone is looking.

---

## STATE OF THE REPO

* `main` at `a561a71` (the Phase 9 merge). **Nothing is pushed**; `origin/main`
  is at `b6c453b`, the Phase 6 merge, 108 commits behind. That is item A and it
  is parked deliberately.
* **Fast lane: 1271 selected · 1269 passed · 2 xfailed · 0 failed · exit 0.**
* Slow engine lane: 52 · 51 passed · 1 XPASS · exit 0. `ST-SCHED-013` is
  `strict=False` by design — it xfails on a loaded box and XPASSes on an idle
  one. Both are fine.
* `mypy` clean over 42 source files.
* **Translation backlog 2548 against a 2548 ceiling.** Zero headroom. All four
  layering ratchets still `0` (asserted with `==`, so they are floor *and*
  ceiling).
* The two remaining `xfail` pins are the **ST-DATA-013** pair. Re-verified in the
  audit: still true, still unreachable by a user, and guarded by a live test in
  the same file (`test_no_persisted_payload_needs_the_two_pins_above`) that goes
  red the day a real save path acquires the hazard. **Leave them.**
* **Zero `TODO` / `FIXME` / `XXX` / `HACK` markers under `scheduler_app/`.**
  Swept case-insensitively. That item is closed, not merely quiet.

---

## ENVIRONMENT — read every line

* Python: `.venv-audit/Scripts/python.exe` — **never a bare `python`**. It lives
  in the main tree; `scheduler_app` is **not** pip-installed and resolves from
  the current working directory, so run pytest **from the tree you mean to test**.
* `PYTHONIOENCODING=utf-8` on every command. The machine default is **cp1254**
  and `print()` raises on a Turkish letter or a `→`.
* `QT_QPA_PLATFORM=offscreen` for Qt tests; **never for a geometry measurement**.
  Offscreen has no Segoe UI, its fallback is fixed-pitch, and it wraps about
  twice as often — the same dialog measured **24 110 px** real and **56 100 px**
  offscreen. **Items 4, 11, 12 and 15 are all geometry or colour**, so this
  matters more in Phase 10 than it did in Phase 9.
* **Do not measure a widget with `sizeHint()`.** It reported 973×8106 for a
  dialog the user receives at 24 110 px, because QMessageBox re-wraps and grows
  inside its show handler. Use `setAttribute(WA_DontShowOnScreen)` then `show()`
  — real fonts, full layout, no window on screen. `tests/test_phase9_b6.py` is
  the worked example.
* Any standalone probe needs `if __name__ == "__main__":` — the optimizer uses
  multiprocessing and will otherwise fork-bomb Windows.
* Use the `make_app` fixture for anything that builds a `SchedulerApp`.

### The line-ending rule, corrected

`HANDOFF-PHASE9.md` said "every tracked file is CRLF". **Measured, that is not
true**: the working tree is **124 CRLF files and 11 bare-LF** (`git ls-files
--eol` shows the index uniformly `i/lf`). The practical rule is unchanged and
matters more than its reason: **use the Write/Edit tools for every source
change.** A `\n`-based `str.replace` on a CRLF file is a **silent no-op** — it
cost Phase 8 two no-op patches and Phase 9 one, a `'@classmethod\n'` replace
that left the decorator at module scope and produced 27 failures with
`TypeError: 'classmethod' object is not callable`.

### Four traps that have cost real time in more than one phase

1. **`pytest`'s final summary line is swallowed here.** "passed"/"failed" never
   appear; output ends at the "slowest durations" block. **Gate on the exit
   code** and count from the progress characters on lines matching
   `^([.FxXEs]+)\s+\[\s*\d+%\]`. **Counting bare `.FxXEs` characters anywhere in
   the output is wrong** — it picks them out of prose and gives a nonsense total.
2. **Never background a test run and poll for it.** Foreground, explicit
   `timeout`. Three Phase 8 agents hung permanently in `until grep -q "passed"`
   loops and had to be killed.
3. **The full fast lane takes ~11 min and exceeds the 600 s tool cap.** Run it
   with `run_in_background`, or run single modules (5–20 s).
4. **Worktrees are provisioned from a stale base.** Every worktree agent in
   Phases 6–9 found its worktree cut from the *Phase 6* merge. Make
   `git log --oneline -3` and `git merge main` the first action of any worktree
   agent, and verify `scheduler_app.__file__` resolves **inside its own
   worktree** before trusting a measurement.

### Translations: ship all 22, do not raise the ratchet

Phase 9 added six user-facing strings and moved `MAX_MISSING_LOCALE_KEY_PAIRS`
**zero** times. Backlog is 2548 against 2548 — one English-only key is a red
lane. A working inserter lives in the session scratchpad; the shape is
`{"key": {"en": ..., "tr": ..., ...}}` for **en tr de fr es zh ru ar fa it pt_BR
pt_PT nl sv da pl az hi id af ja ko**, inserted in sorted position, CRLF-safe,
idempotent. Write real translations, not English copies.

**Measure the backlog the way the test does** — `import
scheduler_app.i18n.tier_translations` FIRST. Without it you get ~1700 and a false
sense of headroom. Three phases in a row fell into this; Phase 9 did not.

---

## PHASE 10 — the seventeen items

**Verification state is marked on every item and it is not decoration.** Phase 9
proved that a handoff item written against an old tree is often wrong: four of
ten Section C items evaporated on measurement. Nine of the seventeen below come
from register entries that have sat as a bare `OBSERVED` for nine phases and have
**never been reproduced by anyone**. Reproduce first. `NOT_REPRODUCIBLE` is a
result and the user has already accepted it as one.

Key: **⬤ reproduced live** · **◐ code shape confirmed, not driven end to end** ·
**○ located in code from a register entry, never reproduced**

### The one High

**1. ⬤ Ctrl+C on the Dashboard tab makes DERSİS announce that it has crashed.**
`ui/app.py::_copy_to_clipboard`. The line is
`filter_fn = [self._filter_classroom, self._filter_group, self._filter_lecturer][tab_idx]`
— a three-element list indexed by the tab number. Tab 3 has its own branch above;
the Dashboard is tab 4, so the index is out of range. With the shipped
`scheduler_gui.py` exception hook installed, the user gets the **crash-report
dialog** saying the program has crashed, an entry in the crash log, and nothing
on the clipboard. Ctrl+C works correctly on the other four tabs.

Reproduced live twice by the audit — once by calling the method, once by a real
`QTest` key press through the actual Qt shortcut. **Registered as ST-FUNC-008,
Medium, bare `OBSERVED`, for nine phases, pinned by no test.** The audit re-rated
it High because "raises IndexError" understates what a user is shown. This is the
most user-facing thing left in the application and it is one line.

### The Mediums

**2. ⬤ A file saved by an older build opens with dangling room rules, and the
app then deletes them without saying so.** `ui/app.py::open_file` and
`::_auto_load` — neither calls `reconcile_placements`. A dangling "must be in the
physics lab" matches no room, so the lesson can never be placed again by drag,
Place All Unplaced or the solver, and nothing says why. A dangling "never room
12" silently stops applying — a *wrong* timetable rather than none. Then
`ui/dialogs.py::AddClassDialog._ok` rebuilds both fields from the live room list,
so opening the class and pressing OK **erases the rule**.

**Phase 9 tried the obvious fix and it was wrong**, which is why this is still
open. `core/models.py` states the policy in writing:

> Deliberately NOT called from `normalize_state_classes` (and so not from the
> .egu load path): unplacing orphans at load time would silently discard the
> user's own placements with no way to see or undo it… Callers decide what to do
> — **warn, list, or offer to reconcile**.

`_reconcile_after_setup()` was added to `open_file` and chose none of the three.
Measured on a real `.egu`: **6 of 6 lessons unplaced, undo depth 0**, and
`mark_current_state_as_baseline()` then recorded the wrecked state as having no
unsaved changes — one save and the term's placements are gone from disk. It was
reverted with the policy quoted at the site.

So **build one of the three the policy names.** `_auto_load` is the harder half:
it runs from `__init__` before `_build_main()`, so there is no widget for a
report, and `_pending_settings_report` is a single slot that is already contended
(see `_flush_startup_settings_report`, which puts the damaged-log report first
for exactly this reason).

**3. ○ A multi-lesson drag from the timetable moves only the lesson under the
cursor.** `ui/app.py::_start_drag_gfx` — `_dragging_classes` gets the whole
selection, but `_drag_backup` and `mark_unplaced` touch only the primary, so
`_execute_drop`'s `all(not placed)` guard is False and the single-lesson branch
runs. Registered as **ST-DATA-010** and pinned by a test in
`tests/test_drag_and_drop.py`, so the current behaviour is *asserted*: changing
it means changing that test, deliberately, with the reason in the commit message.
Note this interacts with Phase 9's drag rework — read `_start_drag_gfx`'s held
snapshot and the three commit points before touching it.

**4. ○ One long class name inflates that hour's row across the whole grid.**
`ui/renderer.py::_needed_height_for_class` and its two consumers, which do
`row_heights[b["row"]] = needed` unbounded. `grep elide` over `renderer.py`
returns nothing. **ST-UI-012**, bare `OBSERVED`, in no phase's plan.
**Geometry — do not measure it offscreen.**

**5. ◐ Opening "Edit Classes" and closing it unchanged destroys your Redo
history.** `ui/app.py::edit_classes` — an unconditional `_push_undo` above
`dlg.exec()`. The snapshot is genuinely needed, because
`EditClassesDialog._delete_selected` writes state directly; what is wrong is that
it fires whether or not anything happens. **Recorded nowhere before this file.**

**8. ○ The warnings sidebar re-runs a full negotiation pass on every repaint.**
`ui/app.py::_refresh_warnings` has no fingerprint guard, unlike
`_refresh_open_slots` right beside it, and `_run_auto_negotiation` does a
`neg.negotiate_class(cls)` per unplaced class per refresh. **ST-PERF-006.**
The fix shape already exists in the same file — copy the fingerprint guard.
**Measure before and after; a performance claim without a number is not a fix.**

### The undo family — 6 and 7, same root cause, do them together

**6. ◐ Bulk Add, then Cancel in the results dialog.** `ui/app.py::_bulk_schedule`
— `_push_undo` fires above `_schedule_new_classes`, which can end in
`SchedulingWorkflow.rollback_schedule`. The rollback restores the placements; it
does **not** restore the redo stack `_push_undo` cleared, nor the
`_undo_stack[0]` it evicted at the 50-entry cap.

**7. ◐ Add Class, then Cancel.** `ui/app.py::add_class` — identical shape above
`_schedule_new_classes(split_classes)`. **Recorded nowhere before this file.**

This is **B1/B2 for the third and fourth time**, and the cure is already in the
tree twice: `edit_setup` holds its snapshot in a local and commits only on
accept, and Phase 9's `_execute_drop` holds it in a field and commits at a commit
point. `_push_undo`'s own docstring forbids exactly this shape. **The fix changes
`_schedule_new_classes`'s contract** — the rollback has to become the thing that
decides, the way the placement comparison now decides in `_place_classes_batch`.

Two warnings from Phase 9, both paid for:

* **Do not gate on a count.** The obvious gate — "commit the undo entry only if
  something was placed" — is wrong: `placed_count` counts only the candidates,
  while Phase 2 of `optimized_batch_schedule` re-solves every already-placed
  unpinned lesson, so a batch can report `0 placed` while having *relocated*
  lessons on the timetable. `result.rescheduled` is worse: it is returned `True`
  unconditionally. Compare placements before and after.
* **Neither of these is reproduced yet.** The audit read the code shape and
  stopped there, because driving the BulkAdd + BulkResults dialog pair end to end
  is reproduction work. Do that first.

**Related, and NOT in the user's 1–17.** `_execute_drop_anywhere` sets
`_drag_success = True` unconditionally after `_place_classes_batch`, even when
the batch placed nothing — so a multi-lesson drop can report success having done
nothing. It is harmless *today* only because `_start_drag_unplaced` shows its
toast solely for `len(drag_classes) == 1` while `_execute_drop_anywhere` only
fires for `len > 1`: two lines apart, in different methods, with nothing pinning
the coincidence. It is the same family and nearly free while you are in there.
**Ask before folding it in** — the user scoped this phase at 1–17 explicitly.

### The Lows

**9. ○ The Hindi "unsupported file version" message drops its `{supported}`
placeholder.** `i18n/translations.py`, `hi` catalogue, key
`errors.unsupported_egu_version`; raised at `storage/storage.py`. A Hindi user
opening a `.egu` from a different build is told the file's version but not which
version their copy can read — every other language tells them both. It does not
crash (`str.format` ignores the extra argument); the sentence just loses half its
information. Pinned by `MAX_PLACEHOLDER_SUBSETS = 1` in
`tests/test_translation_coverage.py`, currently at **zero headroom with this as
its sole occupant** — so fixing it lets you lower that ratchet to 0 in the same
commit, which is the banked-headroom rule working as designed.

**10. ○ Open-slots rows advertise clickability and do nothing.**
`ui/app.py::_refresh_open_slots` builds each row as a bare `QWidget` with
`setCursor(PointingHandCursor)` and a `:hover` stylesheet, and **connects
nothing**. **ST-UI-017**, recorded as "Partially fixed (Phase 6)". Decide what a
click should do (jump to the slot? place the selected lesson there?) — that is a
product question, and the cheap honest alternative is to remove the affordance.

**11. ○ The crash and bug-report dialogs are dark-themed in a light-only app.**
`ui/bug_report.py` — `background: #1e293b` at eight sites; the module docstring
itself says "polished dark-themed dialog". **ST-UI-018.** Note this is the dialog
item 1 makes users see, so doing 1 and 11 together is coherent.

**12. ○ The warning log has no timestamps, a 120 px ceiling, and an unbounded
history list.** `ui/widgets.py` — `setMaximumHeight(120)`, and `log()` appends to
`self._sticky` with no bound. **ST-UI-019.** The unbounded list is the
ST-PERF-003 growth shape; `tests/test_warning_log_growth.py` already exists, so
read what it pins before changing the bound.

**13. ○ The empty state offers no guidance and terminology drifts across
screens.** No empty-state widget exists; `grep empty_state|getting_started` over
`ui/` and `i18n/` returns nothing. **ST-UI-020**, "Empty-state CTAs untouched".
Any new string ships in 22 locales.

**14. ○ When Thorough mode's solver silently falls back, nothing tells the
user.** `core/schedule_optimizer.py` sets `summary["cpsat_failure"]` at six
sites; `grep` finds **no reader anywhere in `scheduler_app/ui/`**. The user asked
for the thorough engine, got the fallback, and was not told. Note Phase 8 found a
sibling claim false — "the UI cannot tell a cancelled solve from a failed one"
turned out to be wrong because three distinct signals exist — so **check that
this write-only flag really is unread before building on it.**

**15. ○ Green and amber text in dialogs fails the contrast standard.**
`ui/dialogs.py` (nine sites) and `ui/app.py` (one), plus a documented comparison
comment in `core/constants.py`. `tests/test_cell_contrast.py` exists — read what
ratio it enforces and where, because the grid may already be held to a standard
the dialogs are not.

**16. ○ A class with two identical targets draws both sub-blocks in the same
place.** `c["targets"].index(t)` where `enumerate` is meant, in **four** places:
`ui/renderer.py`, `data_io/exporter.py` (two sites) and `ui/app.py`. `.index()`
returns the first match, so two identical targets both get index 0 and overlap.
Cheap, but **fix all four or none** — a partial fix means the screen and the
export disagree, which is worse than both being wrong the same way.

**17. ○ The Add Class form calls itself "Edit Class" while the user is adding
one.** After Phase 9's B3 fix, answering "No" to the name-collision prompt
re-shows the form seeded through the only channel the dialog has, `edit_cls=`,
and the title is derived from it. Every field is correct; only the caption lies,
and only on the add path's second showing. The fix is a keyword-only
`title=None` on `AddClassDialog.__init__`.

---

## PHASE 11 — release, packaging and CI (do NOT start these now)

The user deferred these explicitly. They are recorded here so Phase 11 does not
have to re-derive them; **B and C are the dangerous pair** because they are
silently wrong rather than obviously broken.

| | Item | Where |
|---|---|---|
| **B** | 🔴 The documented "Manual alternative" release route runs the **old, broken June workflow** and looks identical in the UI. GitHub reads `workflow_dispatch` from the **default branch only**, and `origin/main` is still Phase 6. That file has no `test` job, and `macos-13` — retired by GitHub on 2025-12-08 — makes `needs: build-macos` permanently unsatisfiable, so `publish` never runs and no release is ever created. | `origin/main:.github/workflows/release.yml`; `docs/RELEASE_CHECKLIST.md:36` |
| **C** | 🟠 `VERSION` is still `1.0.0`, one commit since "Initial public release". A release cut today produces `Dersis_Setup_v1.0.0.exe` with the same filename, About string and bug-report version as the June builds already downloaded 105+ times — after nine phases of change. | `VERSION`; `scheduler_app/_version.py` |
| **D** | 🟠 The release lane's test gate is weaker than CI's and the stronger run does not block publishing: `release.yml`'s `test` job runs `pytest -m "not slow"` only — no `mypy`, none of the four slow engine modules. A tag with a provably broken engine still publishes. | `release.yml` job `test` vs `ci.yml` job `engine` |
| **E** | 🟠 macOS artifacts are built from **unpinned** dependencies while Windows is pinned: `build_mac.sh` installs `requirements-build-mac.txt` → `requirements.txt` (floors only), so the `.dmg` ships whatever PyPI resolves that day. One version number, two dependency sets. | `build_mac.sh:83` |
| **F** | 🟡 `BUILD.md` — linked from all five READMEs — is stale in four ways, including a release recipe that no longer matches the workflow. | `BUILD.md` |
| **G** | 🟡 `docs/RELEASE_CHECKLIST.md` still tells the releaser the repository has no tests. | `:10`, `:36` |
| **H** | 🟡 The installer never carries the Visual C++ runtime; the failure mode is a silent non-start on a clean machine. | `installer.iss:123-125`, `:157` |

**Item A — pushing `main` — is parked on the user's instruction.** Do not push,
do not tag, do not open a PR. `origin/main` stays at `b6c453b`, 108 commits
behind. Note that **B cannot be fixed without A**, because the broken dispatch
route lives on the remote's default branch — so Phase 11 has to start by asking
the user to lift the park.

---

## Do not re-open these without new evidence — each was measured

| Item | Why it stands |
|---|---|
| **The two ST-DATA-013 pins** | Library-level JSON properties (int dict keys stringified; NaN/Infinity written as non-standard tokens) with no production producer. Re-verified after Phases 8–9: every settings write uses string keys and string/boolean values, and a live test in the same file goes red the day that stops being true. |
| **ST-SCHED-013's non-strict xfail** | Making it strict turns every fast correct run into a failure; deleting it turns every slow correct run into one. Measured: 96.4 s / 103.6 s idle vs 120.07 s loaded, same machine, same hour; CI is 1.87× faster. The runner-independent half of the property is asserted unconditionally by two other tests. |
| **The 2548 translation backlog** | A translator's job, not a defect. Machine translation was rejected on a measured failure mode: a translation that drops a placeholder makes the room name vanish from the sentence with **no error at all** — see item 9 for the one instance that already exists. |
| **No coverage ratchet** | A coverage floor that skips when the data file is absent is not a ratchet, it is a test that always passes. Needs `coverage` in `requirements-dev.txt` and a CI change; `requirements-dev.txt` still has no such entry, so this is a stated non-decision, not an oversight. |
| **ST-ARCH-005 extraction** | Six candidate seams were built in Phase 7; **all six** left radon's Maintainability Index at exactly 0.00, because the complexity term alone (−205) exceeds the formula's 171 constant. The ratchets exist instead of the refactor. |
| **PDF shaping for ar/fa/hi** | `_resolve_pdf_fonts` **short-circuits on shaped scripts**, so no substitute face — bundled or host — is ever tried. Needs a real shaping engine. reportlab 5.0.1 ships `rlbidi`/`uharfbuzz` extras, but `requirements-lock.txt` pins **4.4.10** for the shipped build, so verify against that version. |
| **`requirements-lock.txt` pins** | The test CI does not read this file — only the build paths do — so regenerating it changes what *ships* and cannot be verified without running the build. A consistency test guards it instead. |
| **The release rehearsal** | The user declined it in Phase 8. The script is in `HANDOFF-PHASE8.md` §6 if it is ever wanted. |

---

## Two rules Phase 9 would most like to pass on

**1. A test that plants the state it is meant to observe is measuring nothing.**

Phase 8 recorded this once: deleting `self._drag_undo_pushed = True` from
production left the whole suite green, because the test helpers set the flag
themselves. Phase 9 hit the identical pattern again — two production lines
deletable **together** with the suite green, whose removal let a sidebar drag
plus one Ctrl+Z resurrect an abandoned gesture's placement under the wrong label.
The test that set that state up **hand-assigned** `_drag_undo_entry = None`
instead of letting `_start_drag_unplaced` clear it.

The check is cheap and it is the only one that works: **mutate the production
line, confirm the mutation landed with `git diff --stat`, and run the suite.** A
green suite under a real mutation is a finding about your test. Phase 9 ran ten
mutations on the drag path alone; seven were killed, three survived, and the
three were the finding.

**2. Build the prescribed fix before believing it.**

Four times in Phase 9 a prescribed fix was wrong, and **every one was found by
building it and watching it fail** — never by argument. Twice it would have
introduced a *second* defect; once it would have shipped green while permitting
the exact defect it was written for; once ("add the `$` anchor") it would have
created an ST-SEC blind spot in a file where two earlier phases had already found
one. This handoff is written by the same fallible process. **Treat its fix
sketches as evidence, not orders**, and when one of them turns out to be wrong,
say so in the write-up — that record is the most valuable thing each phase has
handed the next.
