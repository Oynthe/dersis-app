# Handoff — Phase 8 (the remaining work)

Phase 7 is complete on `fix/phase-7-release` and was the **last row of the
roadmap**. This file is the ready-to-paste prompt for whoever picks the work up,
plus everything Phase 7 measured and did not fix.

Read [`PROGRESS.md`](PROGRESS.md#phase-7--complete) first — its "Where the
register was not enough" section records eighteen things the audit got wrong, and
that is more useful than what it got right.

---

## Ready-to-paste prompt

> You are continuing the DERSİS remediation (`C:\dev\dersis-app`), an offline
> PyQt6 school-timetabling desktop app (~48k lines Python, v1.0.0, Turkish-first).
> A full stress-test audit lives in `stress-test/`. Phases 0–7 of
> `stress-test/14-implementation-roadmap.md` are done; the roadmap has no further
> rows. Your job is the remaining open work listed in
> `stress-test/HANDOFF-PHASE8.md`, in the order given there — **two of the first
> three are live defects that produce a wrong timetable.**
>
> READ FIRST, in this order:
> 1. `stress-test/PROGRESS.md` §"Phase 7 — complete" — what changed and, more
>    importantly, what the register got wrong. Highest-value file.
> 2. `stress-test/HANDOFF-PHASE8.md` (this file)
> 3. `stress-test/12-findings-register.md` — canonical findings; every status is
>    current as of Phase 7.
> 4. `tests/README.md` — the suite's conventions. Follow them, especially the
>    ones about mutation testing, pixel assertions, and ratchets.
>
> STATE OF THE REPO
> - Branch for your work: `fix/phase-8-remaining`, cut from `fix/phase-7-release`
>   (or from `main` if that has merged).
> - Suite: **1026 tests — 1018 pass, 8 known-defect pins, 0 failures.** Both lanes
>   exit 0. `mypy` is clean over the six Qt-free packages.
> - **All four layering ratchets are `0`.** They are hard contracts now, not
>   ceilings to spend.
>
> ENVIRONMENT
> - Python: `.venv-audit/Scripts/python.exe` — never a bare `python`.
> - Run tests from the repo root. CI runs `pytest -m "not slow"` and `mypy`.
> - `tests/conftest.py` sandboxes HOME at conftest-import time — mandatory. Never
>   import `scheduler_app` from a conftest at module scope.
> - Set `PYTHONIOENCODING=utf-8`. This machine's default is **cp1254** and an
>   unset value makes `print()` raise on a Turkish letter or an arrow.
> - `QT_QPA_PLATFORM=offscreen` for Qt tests; **never** for a geometry
>   measurement — offscreen has no Segoe UI and inflates advances ~1.8x.
> - Use the `make_app` fixture for any test that builds a `SchedulerApp`.
> - Any standalone probe needs an `if __name__ == "__main__":` guard — the
>   optimizer uses multiprocessing and will otherwise fork-bomb Windows.
> - Beware the shell heredoc: `<<'EOF'` eats one level of backslash escaping, and
>   most files here are **CRLF**, so a `\n` anchor in a patch script will not
>   match. Use the Edit/Write tools for anything with escapes or non-ASCII.

---

## The two that produce a wrong timetable — do these first

### 1. One Ctrl+Z after a drag **unplaces** the lesson instead of putting it back

Pinned: `tests/test_drag_and_drop.py::test_one_undo_after_a_drag_puts_the_lesson_back_where_it_was`.

`_start_drag_gfx` pushes a correct snapshot **before** its pre-emptive
`mark_unplaced` (`ui/app.py:4435-4438`, and the comment there says exactly why).
Then `_execute_drop` throws that snapshot away and pushes a fresh one
(`ui/app.py:4676-4681`) — by which time `mark_unplaced` has already run, so the
"move" entry captures the **unplaced** state.

The comment on those lines says *"The snapshot data is identical — only the label
differs."* Measured, it is not. That sentence is the whole bug.

Do not re-push. **Relabel the entry that is already on the stack**, or rebuild
the snapshot from `self._drag_backup` (which still holds `placed_day`,
`placed_time`, `placed_classroom` at that point — it is read three lines above).
Drag-and-drop is the app's primary editing gesture and had no test at all before
Phase 7; `tests/test_drag_and_drop.py` now covers it, and gutting `_execute_drop`
to a bare `return` reddens 9 of its tests, so the module is a real net.

### 2. A Turkish day name typed in capitals is silently dropped

Pinned: `tests/test_day_key_normalization.py::test_a_turkish_day_typed_in_capitals_is_still_that_day` (two cases).

`normalize_day_value` folds case with `str.casefold()`
(`scheduler_app/i18n/day_keys.py:44`, and again at `:49` and `:54`), which is
locale-independent and does not implement the Turkish dotted/dotless I. Measured
on this tree:

```
'PAZARTESİ'.casefold() == 'pazarteṡi'   != 'pazartesi'
'SALI'.casefold()      == 'sali'        != 'salı'
```

Both return `None`, and `normalize_state_day_keys` then **filters them out
silently** (`day_keys.py:78-79` and `:93-94`, `[d for d in … if d in allowed]`).
So a school whose Excel roster writes availability days in capitals — ordinary in
Turkish — loses those constraints with no warning, and the planner places the
teacher on a day they told it to avoid.

**Fix all three sites together, and no fewer.** The same root cause lives in
`core/workflow.py:223` (`register_lecturer`, `folded = name.casefold()`) and in
`data_io/importer.py`'s duplicate-lecturer check. A Phase 7 agent measured this
and deliberately declined to fix the importer alone, because diverging from
`register_lecturer` would make the importer and the class form disagree about
whether two teachers are the same person. That reasoning was right — so either
fix the shared rule in one place all three call, or leave all three. A Turkish
casefold is `'İ'→'i'`, `'I'→'ı'` applied before the ordinary fold.

---

## The rest, in the order worth doing

### 3. `required_room_type` is advertised and never consumed — ST-FUNC-009

Pinned: `tests/test_import_roundtrip.py::test_required_room_type_constrains_the_class_to_matching_rooms`.

The generated template tells the user to write "Laboratuvar"; the importer
validates the value and discards it. A physics lab is then scheduled into a
lecture hall. For a Turkish K-12 with labs, a gym and a computer room this is an
ordinary case, not an edge one.

**~4 h, and the only open item that changes solver input.** It needs a
`room_type → required_classrooms` resolution plus a warning when no room matches;
`InfeasibilityAnalyzer.analyze_class` already produces a correct
`required_room_missing` message for the adjacent case, so wire into that rather
than writing a second answer to the same question.

### 4. A corrupt feedback log silently disables preference learning — ST-DATA-002

Pinned: `tests/test_storage_roundtrip.py::test_load_encrypted_lines_does_not_swallow_corruption`.

`_read_log_records` (`storage.py:468-489`) is *deliberately* tolerant — its own
docstring promises "a damaged tail costs only the records after it", and line 485
breaks on a torn tail keeping every complete record before it. That promise is
defeated one frame up: `load_encrypted_lines`' blanket
`except Exception: return []` (`storage.py:515-516`) throws the healthy records
away too. **Measured: 0 of 3 recovered**, and the log keeps growing while reading
as empty, permanently.

Live via `PreferenceLearner.learn()` → `get_entries_since(skip)` → with `skip==0`
straight to `load_encrypted_lines`. One flipped byte and the "learns from your
edits" feature is dead with no message.

**Do not simply make it raise.** It is called from `append_encrypted_entry:542`
and `load_encrypted_lines_since:598,607`; raising turns a silent learning outage
into a throw on the *append* path, and `_write_entry` swallows that with
`except Exception: pass`, so the user still learns nothing while the log stops
being written. The narrow fix is to let `_read_log_records` keep what it already
parsed and surface the count that was lost.

### 5. Legacy ASCII saves cannot be opened — ST-FUNC-007

Pinned: `tests/test_storage_roundtrip.py::test_legacy_plain_ascii_json_save_loads`.

`_is_fernet_token()` (`storage.py:357-366`) returns `True` for **any** blob whose
first 80 bytes decode as ASCII — that is its entire test. A plain-JSON legacy
save is therefore routed to the Fernet branch and dies, while the *same file*
with a Turkish letter in the first 80 bytes takes the plain-JSON branch and
loads. Live via `ui/app.py:2624` (File ▸ Open) and `ui/first_run.py:58`.

Try `json.loads` first, or check for a `{`/`[` first non-whitespace byte, before
the Fernet heuristic. The companion guard
`test_legacy_plain_json_with_turkish_text_loads` already protects the half that
works. **~1.5 h.** Likelihood is unknown — v1.0.0 is the first tagged release, so
the affected population may be empty.

### 6. Rehearse a release on a scratch fork — **the largest unverified surface**

Phase 7 rewrote the release, packaging and installer path and **executed none of
it**: no workflow ran, `iscc` is not installed, PyInstaller was not run, no
`.app` was launched.

What *is* evidence: the Inno Setup digest was fetched twice and compared
(`9c73c3ba…97b732`, 10,592,232 bytes, `MZ`); PyInstaller's own
`collect_submodules("scheduler_app")` returns **58 names, exactly the 58 `.py`
files on disk** (up from a measured 13 collected / 45 dropped); the retirement of
`macos-13` was confirmed live against the runner-images API.

**Unproven, and each is a way for a release to ship nothing or ship broken:**

* that `release.yml` completes at all — it has **20 runs, all startup failures,
  zero jobs ever executed**;
* that Inno **6.7.3** compiles `installer.iss` as the June build did — compare
  the output against the **118,902,541-byte** baseline;
* the `[UninstallDelete]` and ACL behaviour after `[Dirs]` was deleted;
* that `build_nuitka.bat` still builds;
* that the `.app` launches;
* that a `v*` tag now triggers exactly `ci.yml` + `release.yml` and not the
  six-job stampede that used to exist.

Push `v1.0.1` on a **scratch fork** and watch it end to end. The last defect
found here — that the first tag would publish nothing, because `publish` needed a
`build-macos` leg targeting a retired runner — was found by *reading YAML*, and
there is no reason to assume it was the only one.

### 7. Nine of twenty-two locales cannot print their weekday names

Not pinned; recorded in `PROGRESS.md`'s known gaps.

`Vera.ttf` covers Latin-1 only. Phase 7 added host-font detection
(`_resolve_pdf_fonts` picks the first system face covering the **whole**
document) and, where nothing covers it, prints the undrawable codepoints on a
final page instead of silently corrupting them. Measured coverage: `arial` →
ru/pl/az, `msgothic` → ja/zh, `malgun` → ko. **Devanagari is absent**, and
Arabic/Hebrew/Indic substitution was **deliberately declined** — reportlab has no
bidi and no shaping, so `arial.ttf` emits Arabic in logical order in isolated
forms, i.e. a word printed backwards that still reads as a word, which is worse
than a box that announces itself.

If you take this row: it needs a real shaping engine, not a font swap. Check
whether `Vera` can be replaced wholesale by a bundled DejaVu (fixes ru/pl/az and
nothing else, ~700 KB) before deciding that is worth it.

### 8. Smaller, all measured, none blocking

| Item | Where | Note |
|---|---|---|
| No signed installer | `installer.iss` | The hook is wired behind a secret and is **inert** — `gh secret list`, `gh variable list` and the environments list are all empty. A checksum is published instead. Do not claim a signed installer. |
| `requirements-lock.txt` is stale | `:17` | Pins `reportlab==4.4.10`; `.venv-audit` runs **5.0.1**. The lock does not describe the environment the suite validates against. |
| `os.chmod(key.bin, 0o600)` is a no-op on Windows | `storage.py:232` | Measured: `st_mode` 0o100666 before and after, identical DACLs. |
| `check_untyped_defs` is off | `mypy.ini` | 168 errors, **zero** of them `[name-defined]`. Turning it on is a project, not a config flip. |
| Translation backlog | `test_translation_coverage.py` | **2548 pairs against a 2548 ceiling — zero slack.** The next English string must move the ratchet in the same commit. **Measure with `scheduler_app.i18n.tier_translations` imported** — see the trap below. |
| `FeedbackLogger.log_correction` never called | `learning/` | Wiring it would change what the preference learner trains on — a behaviour change, not a repair. Decide deliberately. |
| `SchedulingWorkflow.is_optimizing` is write-only | set at 4 sites, read nowhere | The UI cannot tell a cancelled solve from a failed one. |
| ~30 further dead symbols | measured, unshipped | Each needs its live twin named before it can be deleted honestly. |
| ST-DATA-013 ×2 | pinned | **Not defects.** True at library level, **no production producer**, measured 2026-08-28. Leave them; the reason strings say why. |

---

## Traps this phase paid for — do not re-learn them

1. **`inspect.getsource` reads the file on disk, not the imported module.** Eight
   test modules use it. Running the suite while anything else mutates the working
   tree — a concurrent agent, an editor, a mutation experiment — produces a
   failure that does not reproduce in isolation. Phase 4 hit the same trap through
   stale `__pycache__`. **Run the gate on a quiet tree.**
2. **Re-measuring only helps if the instrument matches the one that matters.**
   Phase 7 settled an agent disagreement about the translation backlog by
   re-measuring, and the re-measurement was wrong: it read `TRANSLATIONS` without
   importing `scheduler_app.i18n.tier_translations`, which merges **52 further
   `en` keys**. `test_translation_coverage.py` imports it. The authority is the
   test, not a REPL — the count differs by ~850 pairs.
3. **Ask which copy of the code the user runs.** This phase hit it three times:
   ST-FUNC-006's CSV pin drove a writer with no production caller; the sanitiser
   the register said to wire pointed at two dialogs nothing constructs; and the
   Phase 6 precedent was 48 Excel tests against a dead engine. Trace from the
   menu wiring in `ui/app.py` before trusting any test about an export path.
4. **A recommendation can itself be the bug.** Four proposed fixes were rejected
   on measurement this phase alone, including one that would have reintroduced
   the defect beside it (`always() && needs.build.result == 'success'` — a
   job-level `if:` replaces the implicit `success()` for *every* need).
5. **Worktree agents are cut from `main`, not the current branch.** Make merging
   the working branch their first action, and do **not** ask them to re-measure
   the baseline — it costs ~10 minutes of contended wall clock to re-derive a
   number you can hand them.
6. **A pin whose reason string is false is how a bug survives phases.**
   ST-FUNC-005 lasted six that way. Phase 7 retired one such pin — and then wrote
   a new one, naming a module the code had already moved out of. Re-read your own
   reason strings after any refactor.

## The single most useful thing Phase 7 learned

**Green tells you almost nothing.** Every one of this phase's three worst findings
was invisible to a passing suite: a total data loss on the upgrade path with 725
tests green; drag-and-drop committing nothing with 859 green; and a release
pipeline that had been dead for two days while CI was green throughout.

The adversarial round — eight attackers, each finding independently reproduced or
refuted by a second agent defaulting to REFUTED — returned **20 CONFIRMED, 9
PARTLY, 1 REFUTED** against work that had already passed review, and caught two
defects this phase had introduced itself, including its own headline fix. It cost
about an hour. **Run one before believing any phase is done.**
