# Phase 7 — paused mid-flight, how to resume

Session paused 2026-08-28 ~19:05 at the user's request. Nothing is lost: every
piece of work is committed to a branch. This file is the resume prompt.

## Where the branch is

`fix/phase-7-release`, at merge commit `571cc0f`, is **green and verified**:

```
859 passed · 20 deselected · 9 xfailed · exit 0      (baseline was 712 / 13)
mypy: Success — 41 source files
50 files changed, +6490 / −842, across 26 commits
```

That is a safe place to leave the repository. Waves 1 and 2 are merged and
verified. **Wave 3 is committed but NOT merged** — deliberately, so the branch
stays in a known-green state rather than a merged-but-unverified one.

## Step 1 — merge wave 3, then verify

Two branches, both committed, both already containing a merge of
`fix/phase-7-release` (so they are not stale):

| branch | commits | what |
|---|---|---|
| `worktree-wf_f114c3bf-744-1` | `92267c4` | ST-ARCH-011 — the three `NameError` calls |
| `worktree-wf_f114c3bf-744-2` | `574db20`, `e8a8de5`, `a206a9a` | pin hygiene · god-object ratchets · drag-and-drop tests (**WIP**) |

```bash
git merge --no-ff --no-edit worktree-wf_f114c3bf-744-1
git merge --no-ff --no-edit worktree-wf_f114c3bf-744-2
QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 \
  .venv-audit/Scripts/python.exe -m pytest -m "not slow"
.venv-audit/Scripts/python.exe -m mypy
```

**`a206a9a` is unverified.** `tests/test_drag_and_drop.py` (419 lines) was
written but its mutation verdicts were never run — the session stopped during
exactly that step, with `return  # MUTATION M1` still in `_execute_drop` (since
reverted). Before trusting it:

```bash
# gut the production method, run the module, expect RED
#   scheduler_app/ui/app.py :4546  ->  insert `return` as the first statement
.venv-audit/Scripts/python.exe -m pytest tests/test_drag_and_drop.py
# then REVERT the mutation in the same step
```

If it stays green under that stub it pins nothing and must be rewritten. That is
not hypothetical here: the whole reason the module exists is that the stub
leaves all 859 tests green today.

## Step 2 — what wave 3 did not finish

* **ST-ARCH-011 is partly done.** `92267c4` closes the `NameError` cluster
  (`find_conflicting_classes` ×2, `_get_valid_slots` — called, defined nowhere,
  reachable only from `cascade_relocate` / `_unplaced_reason`, which have no
  callers). Still outstanding, all measured but not executed:
  - **wire `escape_qt_rich`** (`core/text_safety.py:48`, zero callers) at
    `ui/dialogs.py:3048` and `:3102`. Its sibling `qt_tooltip` *is* wired.
    Phase 6's lesson applies: this is a fix someone forgot to call, not dead
    code. Do not delete it.
  - ~95 removable unused imports (28 of `storage/__init__`'s are re-exports and
    must stay; `from __future__ import annotations` must stay).
  - the remaining mechanical dead symbols.
* **Do not expect the layering ratchets to move.** All four are already `0` —
  the `logic.py` → `facade.py` split closed ST-ARCH-010 outright. The
  measurement's claim that removing unused imports would take the SCC 15→14 is
  spent.

## Step 3 — the adversarial verification round (not started)

The Phase 4 pattern: independent agents attack the landed work, each candidate
defect reproduced or refuted by a second agent that defaults to REFUTED. That
round found **30 confirmed defects in work that was already green**, including
a whole render mode silently dropping every conflict mark.

Phase 7 is a much bigger surface: 147 new tests, 26+ commits, and a release /
packaging cluster where **nothing was executed** (see "unverified" below).

## Step 4 — the docs pass (not started)

* `stress-test/PROGRESS.md` — a Phase 7 section in the house shape: findings
  closed, **"Where the register was not enough"**, behaviour changes, known gaps.
* `stress-test/12-findings-register.md` — per-finding status, and the
  corrections listed below.
* `stress-test/14-implementation-roadmap.md` — mark Phase 7 rows.
* `stress-test/HANDOFF-PHASE8.md` (or a closing note — Phase 7 is the last row).
* `tests/README.md` — partly updated already by two wave agents; merge, do not
  replace.
* Delete this file when the phase is written up.

## Register corrections owed (all measured this phase)

1. **ST-FUNC-004** breaks **six** letters (`ğĞşŞıİ`), not "every Turkish
   letter" — `öüçÖÜÇ` are WinAnsi and render fine. The failure is not a tofu
   box: reportlab switches to ZapfDingbats and draws `n`, so the text layer is
   falsified (Ctrl-F for "Öğretmen" finds nothing). The fix needs **no bundled
   font** — reportlab already ships Vera and the build already collects it;
   installer delta 0 bytes.
2. **ST-FUNC-006** blames `exporter.py`, which **has no production caller** for
   CSV. The live writer is `ui/app.py::export_csv`, and it had *both* halves —
   including no `encoding=` at all, so it wrote cp1254 and raised
   `UnicodeEncodeError` on a cp1252 machine. ST-FUNC-005 repeating one format
   later.
3. **ST-SEC-005**'s "it can't work in the frozen build" is **false**.
   `build_embed.bat` installs pip and gates the build on it; measured exit 0
   from a windowless pythonw. It is true under Nuitka/PyInstaller for a reason
   the finding never states: `sys.executable` is the app binary, so "install"
   silently relaunches DERSİS.
4. **ST-SEC-002** is 12 lines in 4 Markdown files, not Effort L across 22
   locales. The defect is a *heading*: every factual string is true; the
   encryption bullet just sits under "privacy". `translations.py` needs zero
   edits.
5. **ST-SEC-006** — the token *is* forwarded across the CDN redirect (proven
   3/3 in a two-server harness); but the "never looks for the `.sha256`" half is
   wrong — `digest` is populated and verification runs. The real defect was that
   a missing digest printed a note and **exited 0 with an unverified file**.
6. **ST-SEC-003**'s escalation needs a case it does not state.
   `PrivilegesRequired=lowest` means `{autopf}` is *never* Program Files. The
   default-install exposure is real but different, and the `users-modify` grant
   had **no** valid justification — all three candidates measured false.
7. **ST-SEC-007**'s implied fix is itself the bug: a new AppId orphans 105+
   existing installs into a double-install.
8. **ST-UI-013** — Phase 6's own headline is wrong. The sidebar
   `minimumSizeHint` is **tr = 301 exactly**, the register's original number;
   Phase 6 summed regular-weight advances instead of the real bold, padded,
   emoji-prefixed buttons. The locale-dependent number that bites is the **tab
   bar** (ko 913 … tr 1148 … id 1232). And the finding is not about small
   screens: the app's own default 1150×720 window draws an 841×607 grid into a
   769×457 viewport — **both scrollbars, every launch, every machine**.
9. **The translation ratchet has 848 pairs of slack** (1660 missing vs a 2508
   ceiling ≈ 42 free `en`+`tr` keys), not zero. Two independent measurements
   agree. Several phases have contorted fixes to avoid a key on a false premise.
10. **ST-ARCH-011**'s "name collision" caution is stale — Phase 6 already
    deleted the `logic` copies of `respects_constraints` /
    `check_placement_explained`.

## Unverified, and it must not read as shipped

**Nothing in the release / packaging cluster was executed.** No workflow ran,
`iscc` is not installed, PyInstaller was not run, no `.app` was launched.

What *is* evidence: the Inno Setup digest was fetched twice and compared
(`9c73c3ba…97b732`, 10,592,232 bytes, `MZ`); PyInstaller's own
`collect_submodules("scheduler_app")` returns **58 names, exactly the 58 `.py`
files on disk** (up from the measured 13 collected / 45 dropped); and
`collect_data_files("reportlab")` returns 32 files including `Vera.ttf`.

Unproven: that `release.yml` completes at all (20 runs, **zero jobs ever**);
that Inno 6.7.3 compiles `installer.iss` as the June build did (compare against
the 118,902,541-byte baseline); the `[UninstallDelete]` and ACL behaviour; that
`build_nuitka.bat` still builds; that a `v*` tag now triggers exactly `ci.yml`
plus `release.yml`. **Rehearse a `v1.0.1` tag on a scratch fork before trusting
any of it.**

## Environment reminders

* `.venv-audit/Scripts/python.exe` — never a bare `python`.
* `PYTHONIOENCODING=utf-8`; this machine's default is **cp1254** and an unset
  value makes `print()` raise on `→` or a Turkish letter.
* `QT_QPA_PLATFORM=offscreen` for Qt tests; **never** for a geometry measurement.
* Worktree agents are cut from `main`, **not** from the current branch. Make
  them `git merge fix/phase-7-release` as their first action — wave 2 was
  written against a 10-commit-stale tree and merged cleanly only by luck.
* Do not ask a worktree agent to re-measure the baseline; it costs ~10 minutes
  of contended wall clock to re-derive a number you already know. Run the suite
  at the **end**, as the gate.
