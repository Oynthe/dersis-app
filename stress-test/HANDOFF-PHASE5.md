# Handoff — Phase 5 (UI consistency & accessibility)

Phase 4 is complete on `fix/phase-4-workflow-ux`. This file is the ready-to-paste
prompt for the next session, plus what Phase 4 left behind.

---

## Ready-to-paste prompt

> You are continuing the DERSİS remediation (C:\dev\dersis-app), an offline
> PyQt6 school-timetabling desktop app (~49k lines Python, v1.0.0). A full
> stress-test audit lives in `stress-test/`; you are working the phased plan in
> `stress-test/14-implementation-roadmap.md`. Phases 0–4 are done. Your job is
> Phase 5 — UI consistency & accessibility.
>
> READ FIRST, in this order:
> 1. `stress-test/PROGRESS.md` — what Phases 0–4 changed, and WHY the register's
>    recommendations were often not sufficient. Highest-value file. Note the
>    Phase 4 section's "A correction to the Phase 2 record".
> 2. `stress-test/14-implementation-roadmap.md` §"Phase 5"
> 3. `stress-test/09-ui-ux-audit.md` — proposals P3, P4, P5, which Phase 5 is
> 4. `stress-test/12-findings-register.md` — canonical findings
> 5. `tests/README.md` — the suite's conventions; follow them
>
> STATE OF THE REPO
> - Branch for your work: `fix/phase-5-consistency`, cut from
>   `fix/phase-4-workflow-ux` (or from `main` if that has merged).
> - Suite: **539 tests — 515 pass, 24 known-defect pins, 0 failures.** Both lanes
>   exit 0.
>
> ENVIRONMENT
> - Python: `.venv-audit/Scripts/python.exe` — never a bare `python`.
> - Run tests from the repo root. CI runs `pytest -m "not slow"`.
> - `tests/conftest.py` sandboxes HOME at conftest-import time — mandatory.
>   Never import `scheduler_app` from a conftest at module scope.
> - Set `PYTHONIOENCODING=utf-8`. The app is Turkish-first.
> - **Use the `make_app` fixture** for any test that builds a `SchedulerApp`.
>   Constructing one bare leaks a `FirstRunController` QTimer that fires into a
>   *later* test's rebound `storage._ROOT_DIR`. Phase 4 hit this; see below.
> - Any standalone probe script needs an `if __name__ == "__main__":` guard —
>   the optimizer uses multiprocessing and will otherwise fork-bomb on Windows.
> - **Beware the shell heredoc**: writing Python via `<<'EOF'` silently eats one
>   level of backslash escaping, so `"\n"` in a patch script becomes a real
>   newline and breaks the file. Use the Edit tool for anything containing
>   escapes or non-ASCII.

---

## What Phase 4 changed that Phase 5 will touch

Phase 5 is P3 (contrast + legend + redundant encodings), P4 (keyboard grid
navigation + accessible names) and P5 (responsive shell). All three land on the
renderer and the shell, which Phase 4 modified:

- **`ui/renderer.py` grew lanes.** `RendererAdapter._default_filtered_blocks`
  and `everything_blocks` no longer emit one block per cell — a contested run is
  split into `lane`/`lane_count` inside the same column. **P4's cell cursor must
  navigate lanes, not just cells**, or a conflicted cell will be partly
  unreachable by keyboard — reintroducing ST-UI-001 for keyboard users only.
- **Conflicted lessons carry `block["conflict"]`, `["conflict_partners"]` and
  `["conflict_labels"]`.** P3's "redundant encodings" work should use them: the
  conflict is currently signalled by a red border plus a ÇAKIŞMA pill, which is
  already text+colour, but the pill drops to a bare `!` when the lane is narrow.
- **`_paint_conflict_pill` is bottom-right by measurement**, because both paint
  methods draw the class code at the top and `QPainter.drawText` does not clip.
  Any P3 re-layout of in-cell text must re-check that.
- **The status bar gained two segments** (`incl. N pinned`, `N not on the
  timetable`). P5's responsive work has to decide what it drops first at
  1000 px.

## Known gaps left behind (pick these up if they touch what you change)

1. **`multi_start_time_limit` is 120 s in production, not the 3600 s PROGRESS.md
   recorded for Phase 2.** The raise moved `ScheduleOptimizer`'s default; the
   live path goes through `optimized_reschedule_all`, which passes 120.0
   explicitly. Verified by spying on the real constructor. Needs its own
   measurement pass — the Phase 4 task-6 spec that proposed a global deadline was
   calibrated on the wrong number and its reviewer returned *materially-wrong*.
2. **`targets.index(t)` is unchanged in all three everything-matrix builders**
   (`ui/renderer.py`, `data_io/exporter.py`, `ui/app.py::_write_excel`). `.index`
   compares dicts by `==`, so a non-joint class with two identical target dicts
   resolves both to offset 0. Switching one to `enumerate` is correct in
   isolation and would create a new screen-vs-PDF-vs-XLSX divergence. Fix all
   three together or none.
3. **A legacy `.egu` carrying a duplicate time slot has no in-app repair path.**
   `SetupDialog` is the only writer of `state["slots"]`, so the user must delete
   the line by hand. A "remove duplicate lines" button is the obvious affordance
   and was deliberately not added (it is a rewrite of user text).
4. **New user-facing strings are `en` + `tr` only** — roughly 30 keys across
   Phases 0–4. The other 20 locales fall back to English via `tr()`, never to a
   raw key, so ST-UI-011 is not reopened. **Phase 5 owns the coverage check**,
   and this is now the largest it has been.
5. **`_get_current_slots` de-duplicates defensively** so mid-edit readers are
   safe. If Phase 5 adds another reader of the live Setup text, it gets that for
   free — but a reader of `state["slots"]` is a different thing and is already
   guaranteed unique by the OK gate.
6. **The conflict sweep runs on every repaint, unmemoised, by measurement**
   (2.1 ms at 250 classes, 9.0 ms at 600, against the 306–563 ms repaint
   ST-UI-009 was about). If P5's responsive work adds repaints, re-measure
   before assuming it still does not matter.
7. **`Claude Code Review` CI fails** on every PR and did so before this work
   started. Unrelated; needs whoever owns that workflow's configuration.

## The single most useful thing Phase 4 learned

**Run the adversarial verification, and mutation-test every fix it produces.**
43 verifiers found **34 confirmed defects** in six commits that had a green
suite — including a Critical-adjacent one (the Online tab silently dropping
every conflict mark) that no test could have caught, because no test in the
repository built a `TimetableScene`.

Then, while fixing them, three more traps:

* **A mutation test that cannot see its own mutation manufactures confidence.**
  Stale `__pycache__` made a working fix report *GREEN — PINS NOTHING*. Clear
  the cache between runs.
* **A threshold can sit exactly on the broken value.** A regression test
  asserted `count >= 2`; the measurements are 4 when working and 2 when broken.
  It would have certified the bug. Measure both sides before choosing a bound.
* **A new feature can mask the defect its own test was written for.** The
  conflict appendix kept a needle alive no matter what the grid cell did.

## Lessons from Phase 4 worth carrying

- **The register's recommendation is a starting point.** Phase 4's clearest
  case: ST-UI-002's own "clamp/assert non-negative" is the *worst* available fix
  — it turns −1 into 0 when the truth is 3, contradicting the sidebar on the
  same screen. Mutation-test against the recommendation you were given.
- **Verify the finding's evidence, not just its claim.** ST-UI-002's headline
  screenshot came from the audit's own harness. The finding was still real; the
  evidence was not reproducible as described.
- **Ask what the engine actually requires before enforcing anything.** "Validate
  time slots" sounds obviously right until `grep` shows nothing parses a slot as
  a time — at which point HH:MM validation becomes a bug that rejects real
  schools.
- **Detect defects at the level the engine defines them.** Comparing *covered
  cells* catches reorder, mid-list substitution and removal uniformly; an
  edit-shaped detector catches only the cases someone thought of.
- **Never let a timer outlive the context that armed it.** Phase 2 hit this as a
  lambda firing into a destroyed window; Phase 4 hit it as a `FirstRunController`
  timer firing into a rebound `storage._ROOT_DIR`, corrupting an unrelated
  test's settings file. Use `make_app`.
- **An "agreement" test that calls the helper the fix installs is `f(x) == f(x)`.**
  Read what the widget *rendered*.
- **`isVisible()` is uniformly False in this suite** — widgets are never shown.
  Assert on `isHidden()` and on text.
- **The suite is pinned to Turkish.** Call the accessor; never write the English
  string.
