# Handoff — Phase 9

Phase 8 is complete on `fix/phase-8-remaining`. This file is what the next
session works from, **in the order the user set**.

Read [`PROGRESS.md`](PROGRESS.md#phase-8--complete) first — its "Where the
handoff was wrong" section records eight things Phase 8's own brief got wrong,
and that is more useful than what it got right.

---

## The order, and why it is this order

The user set it explicitly at the end of Phase 8, after asking for **convergence
rather than another expanding audit**:

> "Sorunlar B'yi diğer seansın başında düzeltelim. Sorunlar C'yi de diğer seansın
> sonunda kovalayalım gerçekten var mı? Varsa, çözümlerini üretelim."

1. **Section B first.** Nine items, already reproduced and verified by an
   adversarial round. They are real; they need fixing, not investigating.
2. **Section C last.** Ten items found *incidentally* while fixing something
   else. They were never verified. For each, the first question is **"is this
   real?"** — and only then "how do we fix it?". Several may evaporate on
   re-measurement, which is exactly why they are last.

**The method is the loop, not a one-off.** Every item, without exception:

> reproduce the original failure → implement the fix → re-run the *same* probe
> and show it is gone → regression-check the neighbouring modules → mark resolved

Nothing is marked resolved on inspection, on an intended change, or on a
plausible explanation. If you cannot reproduce a failure, mark it
`NOT_REPRODUCIBLE` and change nothing — do not "fix" a defect you never
observed. Two candidates in Phase 8 died that way and it saved real work.

**Adversarial testing comes only after you believe the fixes are done**, and its
job is to *validate completed fixes*, not to discover that they were incomplete.
Phase 8 ran it that way and it earned its keep: it caught a HIGH regression in
Phase 8's own headline storage fix, which had already passed review and a green
suite.

---

## STATE OF THE REPO

* Branch: `fix/phase-8-remaining`, cut from `main` (`82f558e`, the Phase 7 merge).
* **Fast lane: 1144 selected · 1142 passed · 2 xfailed · 0 failed · exit 0.**
  (954 / 946 / 8 at the start of Phase 8: **+190 tests, 6 of 8 pins closed**.)
* Slow engine lane: 52 · 51 passed · 1 XPASS (`ST-SCHED-013`, `strict=False` by
  design — it xfails on a loaded box and XPASSes on an idle one; both are fine).
* `mypy` clean over 42 source files.
* **Translation backlog 2548 against a 2548 ceiling — the ratchet was never
  raised in Phase 8.** All four layering ratchets still `0`.
* The two remaining `xfail` pins are the **ST-DATA-013** pair. They are
  documentation, not defects: true at the library level, no production producer,
  measured 2026-08-28. Leave them.

### ⚠ `main` has never been pushed

`origin/main` is at `b6c453b`, the **Phase 6** merge. Local `main` is 81 commits
ahead and carries Phases 7 and 8. Nothing in Phase 8 was pushed. This matters
for two reasons: a `v*` tag pushed today would run the *old* workflow tree, and
`main` still carries `build-release.yml` on the remote.

---

## ENVIRONMENT — read every line, all of these cost time in Phase 8

* Python: `.venv-audit/Scripts/python.exe` — never a bare `python`. It lives in
  the main tree; `scheduler_app` is **not** pip-installed and resolves from the
  current working directory, so run pytest **from the tree you mean to test**.
* `PYTHONIOENCODING=utf-8` on every command. The machine default is **cp1254**
  and `print()` raises on a Turkish letter or a `→`.
* `QT_QPA_PLATFORM=offscreen` for Qt tests; **never** for a geometry measurement.
* Every tracked file is **CRLF**, and a shell heredoc `<<'EOF'` eats one level of
  backslash escaping. **Use the Edit/Write tools for every source change.** This
  bit the main agent twice in Phase 8 and silently produced a no-op patch both
  times.
* Any standalone probe needs `if __name__ == "__main__":` — the optimizer uses
  multiprocessing and will otherwise fork-bomb Windows.
* Use the `make_app` fixture for anything that builds a `SchedulerApp`.

### Two traps that cost Phase 8 three agents

1. **`pytest`'s final summary line is swallowed in this environment.** The words
   "passed"/"failed" never appear in captured output — it ends at the "slowest
   durations" block. **Gate on the exit code**, and count outcomes from the
   progress characters (`.`, `F`, `x`, `X`, `E`, `s`) on the lines ending
   `[ NN%]`.
2. **Never background a test run and poll for it.** Two agents hung permanently
   in `until grep -q "passed"` loops and had to be killed; a third burned twenty
   minutes on a CPU busy-wait. One was polling a task that had *already exited*,
   whose output was 185 bytes of `grep` results. Run in the foreground with an
   explicit `timeout`.
3. **The full fast lane now takes ~11 minutes and EXCEEDS the 600 s tool cap.**
   Run it in the background, or run single modules (5–20 s), which is almost
   always what you want.
4. **Worktrees are provisioned from a stale base.** Every single agent in Phase 8
   — nine of them — found its worktree cut from the *Phase 6* merge while the
   brief said Phase 7. Make `git log --oneline -2` and
   `git merge fix/phase-8-remaining` the first action of any worktree agent, and
   have it verify `scheduler_app.__file__` resolves inside its own worktree.

---

## SECTION B — do these first (9 items, all reproduced and verified)

Each was found by an adversarial attacker and independently reproduced by a
second agent that defaulted to REFUTED. They are real.

| # | Sev | Item |
|---|---|---|
| B1 | 🟡 Medium | A cancelled or refused drag **destroys the redo stack** |
| B2 | 🟢 Low | Undo-cap eviction: a drag that commits nothing evicts the oldest entry |
| B3 | 🟢 Low | The two halves of the shared fold apply **opposite** policies |
| B4 | 🟢 Low | Resolved room names are a snapshot nothing reconciles |
| B5 | 🟢 Low | ST-FUNC-009 is still open through **joint sessions** |
| B6 | 🟢 Low | 500 identical warnings in one 49 096-pixel-tall dialog |
| B7 | 🟢 Low | Damaged log below `MIN_ENTRIES_TO_LEARN` is never reported |
| B8 | 🟢 Low | An in-place bit flip on a caught-up log is never reported |
| B9 | 🟢 Low | The damaged-log report only fires for EGL1 **and** ≥5 records |

### B1 — a cancelled or refused drag destroys the redo stack (Medium)

`_start_drag_gfx` (`ui/app.py:4478`) pushes its pre-emptive snapshot through
`_push_undo`, which ends with `self._redo_stack.clear()` (`:1918`). If the drag
is then cancelled or refused, the tail at `:4530-4539` restores `_drag_backup`
and pops the undo entry — **but nothing puts the redo entries back**.

The gesture is a complete no-op on the timetable and a total loss of the user's
redo history. Reproduce: unplace a lesson, `Ctrl+Z`, then start dragging another
lesson and abandon it (Esc, or drop it where the app refuses). `Ctrl+Y` is now
dead.

**B1 and B2 share one root cause** — the snapshot is pushed *before* the gesture
is known to commit — and Phase 8 recorded the structural cure without taking it:
hold the snapshot in a field (`self._drag_undo_snapshot`) and push it only at the
commit points. That also touches `DraggableUnplacedList.dropEvent`
(`ui/app.py:678`) and would rewrite the contract two currently-green tests
encode, which is why Phase 8 left it. **Do B1 and B2 together, as that one
change.** Note this is verbatim the Phase 4 Setup bug that `ui/app.py:3696-3699`
records as fixed for `edit_setup` — the same mistake, still live in the drag
starter.

### B2 — undo-cap eviction (Low)

`_push_undo` evicts at the 50-entry cap with `self._undo_stack.pop(0)`
(`:1915-1916`), and that happens at drag **start**, before anyone knows whether
the gesture commits. Phase 8's relabel branch keeps depth on the success path, so
a committed drag is fine — but a cancelled one leaves the stack one short and the
oldest entry is gone for good. Measured: it fires on a **refused drop** too, not
only on Esc, which is broader than first reported.

### B3 — the fold's two halves disagree about what to do (Low)

`register_lecturer` (`core/workflow.py:962-967`) returns the *existing* spelling
on a fold match and never appends the typed one. `ui/app.py:3154`, `:3634` and
`:4903` then store that returned name on the class. So the **class form silently
reassigns the lesson to a different teacher**, while the **importer refuses the
whole workbook** for the same collision (`errors.teacher_names_fold_together`,
added in Phase 8).

Two real Turkish given-name pairs differ only by the dotted/dotless I — `Ilgın`
/ `İlgin`, `Sıla` / `Sila`. Decide one policy for both surfaces. The importer's
loud refusal is defensible; the form's silent reassignment is not.

### B4 — resolved room names are a snapshot nothing reconciles (Low)

`importer.py:480` stores literal room **names** in `required_classrooms`.
`reconcile_placements` — the one repair that runs after a Setup change
(`app.py:3745`) and after an import (`app.py:5227`) — only clears `placed_*` and
`pinned_*`. It never looks at `required_classrooms` or `excluded_classrooms`.

So renaming a room in Setup makes any class requiring it unplaceable, and
nothing says so: the reconcile reports 0 affected and the Edit Class dialog shows
the blocking constraint as *absent*, because it rebuilds the checkbox list from
the live room list. Pre-existing for `allowed_rooms`; Phase 8 widened who is
exposed by starting to populate the field from `required_room_type`.

### B5 — ST-FUNC-009 is still open through joint sessions (Low)

`_process_classes` resolves `required_room_type` per row
(`importer.py:465-491`), but `_resolve_joint_groups` (`:630-653`) merges a joint
group by keeping `classes[0]` as primary, copying only `targets` off the others,
and deleting them. **`required_classrooms` is never merged.** If the row that
declares the lab is not first, a joint lab session can still be scheduled into a
lecture hall.

Not a regression — every class had this before Phase 8 — but it is the fix's own
headline case surviving, and the user has been told the column works now.

### B6 — 500 identical warnings in one un-scrollable dialog (Low)

`room_type` is optional on the Rooms sheet and `required_room_type` is optional
on the Classes sheet. A school that fills the class column but omits the room
column gets **one warning per class row** (`importer.py:469-478`). At 500 classes
that is a `QMessageBox` roughly **49 096 pixels tall** with its OK button at
y≈49 065 and no scroll area. Still dismissible from the keyboard; unreadable
past the first few dozen lines.

Fix shape: one warning per distinct unmatched type, or a capped-and-counted
summary. Note the per-row form was a deliberate choice for parity with the
sibling `allowed_rooms` column — changing it means changing both, or explaining
why they differ.

### B7 · B8 · B9 — the damaged-log report has three blind spots (all Low)

All three are the same shape: **the storage layer knows the log is damaged, and
the one consumer of that knowledge is gated behind a learning-throughput check.**

* **B7** — `preference_learner.py:92`, `if total < MIN_ENTRIES_TO_LEARN: return 0`
  (value 5) returns *before* any read, so `last_read_lost` stays 0 and
  `_report_damaged_feedback_log` (`app.py:2087`) short-circuits. A user with
  fewer than five recorded corrections loses their history silently.
* **B8** — `learn()` opens with a size fast-path (`:88`,
  `if size and size == self._learned_size: return 0`). **A flipped bit does not
  change the file's size**, so on a log the learner has already consumed, every
  later `learn()` returns at line 88 and the rot is never reported. This is the
  ordinary failure mode for a file written once and read forever.
* **B9** — the report fires only for a log that is both `EGL1` **and** ≥5
  records. Both of Phase 8's new tests are built to clear that gate and neither
  asks what happens below it.

**Treat these as one fix**: the integrity report must not be a passenger on the
learning path. The honest shape is to surface `LogRead.lost` from a read that
happens regardless of whether learning proceeds.

---

## SECTION C — do these last, and verify before fixing (10 items)

**These were never verified.** They were noticed in passing while fixing
something else, by agents who were explicitly told not to chase them. Some will
be real, some will not, and a few may already be fixed. For each: **reproduce it
first**. If it does not reproduce, record that and move on — that is a result,
not a failure.

| # | Where | Claim to verify |
|---|---|---|
| C1 | `importer.py` | The Excel import path **never normalizes imported lecturer availability to day keys**, so a solve run before the first save silently drops every `allowed_days` / `excluded_days` constraint the workbook set. *Probably the most serious item in this section — verify it first.* |
| C2 | `importer.py:686` | The template's row-2 help text is matched by **exact string equality**, so upper/lower-casing it re-opens ST-FUNC-010 (rows silently dropped). |
| C3 | `importer.py:476-480` | `excluded_rooms` is never validated against the workbook's room list, while `allowed_rooms` is. Asymmetry, not necessarily a defect. |
| C4 | `importer.py` | The excluded-rooms rescue's fallback does not subtract `excluded_rooms`, so one row can land a lab class in a lecture hall. **Left deliberately in Phase 8** — widening it is the inversion an existing test exists to catch. Decide, don't drift. |
| C5 | `importer.py:495-528` | The other two room-type warnings still fire for online / lecturer-office classes. Noise, not a false claim (unlike the head-count one, which Phase 8 fixed). |
| C6 | `core/models.py` | `allowed_rooms` capacity blind spot: a class whose hand-typed rooms are all too small is never warned. Pre-existing; Phase 8 fixed only the type-resolved half. |
| C7 | `core/constraint_negotiator.py` | `InfeasibilityAnalyzer.analyze_class` raises `AttributeError` when constructed as `InfeasibilityAnalyzer(state, None, None)`. Check whether any production path does that. |
| C8 | `ui/app.py` | `_execute_drop`'s batch early-return skips the relabel and leaves `_drag_undo_pushed` True. Check what the next gesture then does. |
| C9 | `tests/test_validator_unification.py:180` | `_drop_verdict`'s docstring still cites stale `ui/app.py` line numbers — the same class of defect Phase 8 fixed in `test_drag_and_drop.py`. |
| C10 | `tests/test_packaging_manifest.py:585` | The `if %ERRORS% GTR 0 \((.*?)^\)` regex has no `$` anchor, so it stops at the first line beginning with `)`. |

Two more, recorded as **not defects** but worth not re-discovering:

* `fold_text` is non-idempotent on exactly 8 inputs (an i-family letter or an
  fi/ffi ligature followed by a stray U+0307). **No caller produces one**;
  documented in the module.
* A header-only `EGL1` file reads as `LogRead([], 0)` with count 0 — bit-for-bit
  indistinguishable from a brand-new empty log. Documented residue, not fixable
  by any reader of this format.

---

## What Phase 8 deliberately did NOT do, and why

Do not re-open these without new evidence; each was measured.

| Item | Why it stands |
|---|---|
| **Release rehearsal** (`v1.0.1` on a scratch fork) | **The user declined it.** No fork, no tag, no push. The defects it found *by reading* were fixed, including a Phase 7 regression that would have shipped an installer whose every shortcut pointed at a missing `Dersis.exe`. The rehearsal script is in `HANDOFF-PHASE8.md` §6 if it is ever wanted. |
| **PDF shaping for ar/fa/hi** | A bundled font cannot help: `_resolve_pdf_fonts` **short-circuits on shaped scripts**, so no substitute face is ever tried for them. It needs a real shaping engine. reportlab 5.0.1 *does* ship `bidi`/`shaping` extras (`rlbidi`, `uharfbuzz`) — but `requirements-lock.txt` pins **4.4.10** for the shipped build, so verify against that version, not the audit venv's. |
| **ST-ARCH-005** (`ui/app.py` god object) | Six candidate seams were built; **all six leave the Maintainability Index at exactly 0.00**, because the complexity term alone (−205.4) exceeds the formula's 171 constant. Extraction is not the cure. |
| **`requirements-lock.txt` pins** | 13 of 26 pins have drifted from the audit venv and 3 name packages it does not install. **The test CI does not read this file** — only the build paths do — so regenerating it changes what *ships* and cannot be verified without running the build. A consistency test was added instead. |
| **The two ST-DATA-013 pins** | Library-level properties with no production producer. Documentation. |
| **`os.chmod(key.bin, 0o600)`** | A measured no-op on Windows (`st_mode` 0o100666 before and after, identical DACLs) and load-bearing on the POSIX CI runner and macOS. Kept, with the measurement in the comment. A Windows ACL would add a new permanent failure mode for no measured gain. |

---

## The rule Phase 8 would most like to pass on

**Translate into all 22 locales instead of raising the translation ratchet.**

`HANDOFF-PHASE8.md` advised "the next English string must move the ratchet in the
same commit". That is the *second*-best option. Phase 8 added **six** new
user-facing strings and shipped every one in all 22 locales, which adds zero
missing pairs — so `MAX_MISSING_LOCALE_KEY_PAIRS` never moved, and 22 schools'
worth of users get a native message instead of an English fallback. It costs one
scripted insert.

And the measurement rule that goes with it: **count the backlog the way the test
counts it**, i.e. with `import scheduler_app.i18n.tier_translations` first. Two
independent agents in Phase 8 reported "1700 against 2548, plenty of headroom"
from a count taken without that import. The real figure was **2548 against 2548,
zero headroom**. This is the third phase in a row to fall into it.
