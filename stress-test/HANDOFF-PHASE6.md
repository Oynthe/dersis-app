# Handoff — Phase 6 (Architecture & maintainability)

Phase 5 is **mostly** complete on `fix/phase-5-consistency`. This file is the
ready-to-paste prompt for the next session, plus what Phase 5 left behind.

Read [`PROGRESS.md`](PROGRESS.md#phase-5--mostly-complete) first — it records
what the register got wrong, which is more useful than what it got right.

---

## Ready-to-paste prompt

> You are continuing the DERSİS remediation (C:\dev\dersis-app), an offline
> PyQt6 school-timetabling desktop app (~49k lines Python, v1.0.0). A full
> stress-test audit lives in `stress-test/`; you are working the phased plan in
> `stress-test/14-implementation-roadmap.md`. Phases 0–4 are done and Phase 5 is
> mostly done. Your job is Phase 6 — architecture & maintainability — **plus the
> three Phase 5 rows listed under "What Phase 5 did not finish" below.**
>
> READ FIRST, in this order:
> 1. `stress-test/PROGRESS.md` — what Phases 0–5 changed, and WHY the register's
>    recommendations were often not sufficient. Highest-value file.
> 2. `stress-test/HANDOFF-PHASE6.md` (this file)
> 3. `stress-test/14-implementation-roadmap.md` §"Phase 6"
> 4. `stress-test/12-findings-register.md` — canonical findings
> 5. `tests/README.md` — the suite's conventions; follow them, especially the
>    two Phase 5 added about mutation testing and pixel assertions.
>
> STATE OF THE REPO
> - Branch for your work: `fix/phase-6-architecture`, cut from
>   `fix/phase-5-consistency` (or from `main` if that has merged).
> - Suite: **671 tests — 647 pass, 24 known-defect pins, 0 failures.** Both
>   lanes exit 0.
>
> ENVIRONMENT
> - Python: `.venv-audit/Scripts/python.exe` — never a bare `python`.
> - Run tests from the repo root. CI runs `pytest -m "not slow"`.
> - `tests/conftest.py` sandboxes HOME at conftest-import time — mandatory.
>   Never import `scheduler_app` from a conftest at module scope.
> - Set `PYTHONIOENCODING=utf-8`. The app is Turkish-first.
> - **Use the `make_app` fixture** for any test that builds a `SchedulerApp`.
> - Any standalone probe script needs an `if __name__ == "__main__":` guard —
>   the optimizer uses multiprocessing and will otherwise fork-bomb on Windows.
> - **Beware the shell heredoc**: writing Python via `<<'EOF'` eats one level of
>   backslash escaping. Use the Edit tool for anything with escapes or
>   non-ASCII. Phase 5 hit this on a patch containing line continuations.

---

## What Phase 5 did not finish

Three rows, in the order they are worth doing.

### 1. ST-UI-013 — the responsive shell. **Re-measure before working it.**

Not implemented on purpose. The finding's numbers do not survive measurement on
the native platform:

| register says | measured |
|---|---|
| sidebar "~430 px (43%)" | flat **350 px** at every width; never 430 |
| "below ~1400 px tabs truncate" | `sum(tabSizeHint)` 789 px → truncates at **W < 1159** |
| dashboard tabs collapse to an icon at 1000 px | never collapses; 4 of 5 fit |
| sidebar starves the grid to 2.5 columns | 3.53 in the room view; the 2.5 is the **online** filter's sub-column layout |
| fix: "sidebar 25%, max 360px" | buys **zero** extra columns — Qt clamps a splitter section to the sidebar's own `minimumSizeHint` of 301 px |

Two counter-claims were also checked and are also wrong: the "`_expand_panel`
leaves a 0 px splitter handle" defect does **not** reproduce (0 → 5 px,
draggable, three cycles on the real window — the reading was taken before the
event loop re-laid it out), and "truncation at 1400×860" is an **offscreen**
artifact (1148 px vs 947 available offscreen; 789 vs ~1030 natively).

What is genuinely left: the sidebar does not shrink on a narrow window, and the
tab bar truncates below ~1159. When you build it:

* the lever is `setMaximumWidth`, not `setSizes` — a `setSizes` call is clamped
  by `minimumSizeHint`;
* `_expand_panel` unconditionally runs `setMinimumWidth(0)` /
  `setMaximumWidth(16777215)`, so any cap must be re-applied there;
* auto-collapse needs a **user-intent state machine**. A plain `resizeEvent`
  breakpoint was built and measured: at 1000 px the user clicks Expand, then a
  1 px window nudge re-collapses it and they can never keep it open;
* **do not calibrate any breakpoint from CI.** Offscreen inflates text metrics
  1.5–2× and changes which rows get dropped, not just their size.

### 2. ST-UI-006 — the year legend and redundant encodings

Not built. The finding's real content is stronger than it states:
`get_year_color` is `YEAR_COLORS[years.index(name) % 8]`, so a school with 9+
years paints **two different years the same colour** — Year-01 and Year-09 are
both `#3B82F6`. That is reachable on every tier above Starter
(`professional` allows 15 years, `max` 40, `institutional` unlimited) and is the
norm for a Turkish K–12 school running grades 1–12, which gets *three* colliding
pairs.

So a legend mapping swatch → year would be **actively wrong** above 8 years; it
has to be built from the year list and group years that share a colour. And
above 8 years the colour encoding is not merely inaccessible, it is **ambiguous
for sighted users**, which makes the redundant text encoding load-bearing rather
than a nice-to-have.

The cell has almost no room for it: measured, a populated row is grown to
exactly `_needed_height_for_class`, leaving **5–7 free pixels** regardless of
lane count, and the drop order as the cell shrinks is badge → room → lecturer
(the name has no guard at all). A legend strip costs 23 px of grid height.

### 3. ST-UI-016…020 — the form-UX row. Triaged, mostly false.

Verify before working: the tutorial does **not** fire over a modal, the language
switch is **not** a flag-only entry (it is a titled menubar entry), and half of
ST-UI-019 was closed by Phase 2's sticky/derived split. The live items are:

* `AddClassDialog` shows only `errors[0]` — the validator already returns a list;
* the toolbar's `QToolButton::menu-indicator { image: none }` makes a dropdown
  button pixel-identical to an action button;
* a lecturer name typed into the editable combo never joins `state['lecturers']`,
  so availability never applies to it and `reconcile_placements` treats it as an
  orphan.

---

## What Phase 5 changed that Phase 6 will touch

- **`core/constants.py` now owns the in-cell palette** (`CELL_FG_*`), consumed by
  `ui/renderer.py`, `data_io/exporter.py` (both the XLSX and PDF paths) and
  `ui/app.py`. `tests/test_cell_contrast.py` fails if any of them re-introduces a
  literal in *code* (comments are exempt). ST-ARCH-003's "unify the two export
  engines" must keep that single source.
- **`core/text_safety.py` and `data_io/spreadsheet_safety.py` are new.** Three
  contexts, three incompatible neutralisations; the docstrings explain why they
  cannot be merged. If ST-ARCH-003 unifies the exporters, the XLSX formula sweep
  currently runs at four separate save points and should end up at one.
- **`ui/renderer.py` grew a cursor coordinate system.** `TimetableScene` publishes
  `_cursor_index` / `_cursor_uid_index` and three accessors; `TimetableView`
  overrides `setScene` to re-anchor. Any Phase 6 refactor of the scene builders
  must keep populating the index in **both** filtered builders, keyed on the day.
- **`_needed_height_for_class` gained a `conflict` parameter** that reserves the
  ÇAKIŞMA pill's strip. It has six call sites.
- **`tests/test_translation_coverage.py` holds two ratchets** —
  `MAX_MISSING_LOCALE_KEY_PAIRS = 2508` and `MAX_PLACEHOLDER_SUBSETS = 1`. Adding
  an English string moves the first by 20 and *should*; bump it deliberately and
  say why. It fired on both Phase 5 commits that added keys, which is the design.

## The single most useful thing Phase 5 learned

**Mutation-test every test, and distrust every measurement — including the ones
in the review that corrects you.**

Four Phase 5 tests pinned nothing and were only caught this way:

* an ST-UI-011 test that stayed green with the finding **fully restored**, because
  `labels.targets` lives in a data table and an AST scan for `tr("literal")`
  cannot see it — the same blind spot the finding itself is in;
* an ST-UI-008 test whose payload went into the class name, which `CellRichText`
  never types as a formula (0 cells) — the injectable field is the slot label
  (4 cells);
* a pill-overlap assertion that reduced to `f(x) == f(x)`, and whose rendering
  rewrite then matched every row because a conflicted cell's *border* is the same
  red as the pill;
* a double-scroll test that could not fail, because offscreen the grid fits the
  viewport and nothing can scroll.

And three measurements that did not survive re-running:

* "the splitter handle is 0 px after `_expand_panel`" — read before the event
  loop re-laid it out;
* "tab truncation reproduces at 1400×860" — true offscreen, false natively;
* "`stability` is a live raw-key exposure in all 22 languages" — real gap, but
  unreachable in production, because the only scorer built with
  `previous_placements` never feeds `explain_placement`.

The house rule from Phases 1–4 still holds and got stronger: **verify the
evidence, not just the claim** — and that applies to your own evidence, and to
the adversarial reviewer's.
