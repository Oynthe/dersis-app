# Roadmap progress

Living tracker for the [implementation roadmap](14-implementation-roadmap.md).
The audit documents (01–15) are the frozen 2026-08-26 baseline; this file records
what has changed since. Per-finding state also lives in the
[findings register](12-findings-register.md).

| Phase | State | Branch |
|---|---|---|
| **0 — Critical stabilisation & test scaffold** | ✅ Complete | `fix/phase-0-test-scaffold` |
| **1 — Data & correctness** | ✅ Complete | `fix/phase-1-data-correctness` |
| **2 — Performance foundations** | ✅ Complete | `fix/phase-2-performance` |
| **3 — Scheduling engine hardening** | ✅ Complete | `fix/phase-3-engine-hardening` |
| **4 — Core workflow UX** | ✅ Complete | `fix/phase-4-workflow-ux` |
| **5 — UI consistency & accessibility** | 🟡 Mostly complete | `fix/phase-5-consistency` |
| **6 — Architecture & maintainability** | 🟡 Mostly complete | `fix/phase-6-architecture` |
| **7 — Testing, observability & release** | 🟢 Complete | `fix/phase-7-release` |
| **8 — Closing the remaining work** | 🟢 Complete | `fix/phase-8-remaining` |

---

## Phase 8 — complete

> **Picking the work up?** → [`HANDOFF-PHASE9.md`](HANDOFF-PHASE9.md). It lists
> what is still open **in the order the user set**: section B (nine verified
> defects) first, section C (ten unverified incidentals) last, and for C the
> first question is "is this real?" rather than "how do we fix it?".

**Suite: 1140 tests — 1138 pass, 2 known-defect pins, 0 failures.** Both lanes
exit 0. (954 / 946 / 8 at the start of the phase: **+186 tests, and 6 of the 8
pins closed by fixing the defect**.) `mypy` clean over 42 source files. **All
four layering ratchets are still `0`, and the translation ratchet was never
raised** — every one of the six new user-facing strings ships in all 22 locales.

The two surviving pins are the **ST-DATA-013** pair, which are documentation
rather than defects: true at the library level, no production producer.

### The phase changed shape halfway through, on the user's instruction

It began as "close the remaining work" and, once the backlog was enumerated,
the user redirected it:

> "The goal should no longer be to keep discovering new issues indefinitely…
> I need convergence, not another expanding audit cycle… I want the adversarial
> stage to validate completed fixes, not to discover that the fixes were
> incomplete in the first place."

Everything after that point ran one loop, per item, with no exceptions:
**reproduce the original failure → fix → re-run the same probe → regression-check
→ mark resolved.** Nothing was marked resolved on inspection. Two candidates were
marked `NOT_REPRODUCIBLE` and left alone, which is the loop working.

### What the handoff got wrong

Phase 8's own brief was wrong or incomplete on **six of its eight items**. As in
every previous phase, each was established by building the proposed version and
watching it fail.

1. **Item 2's prescribed fix is the bug.** The handoff specified a *Turkish*
   fold — `'İ'→'i'`, `'I'→'ı'` before the ordinary fold. Built and measured
   across 22 locales × 7 days × 4 casings, it **breaks 42 locale/weekday pairs**,
   including plain ASCII `FRIDAY`, `DIENSTAG`, `LUNDI`, `DOMINGO` and every
   Portuguese `-FEIRA` form — and `PAZARTESI`/`CUMARTESI`, which the suite
   already pins **green**. The correct rule is neither Turkish nor
   locale-dependent: fold every dotted and dotless I (`I`, `i`, `İ`, `ı`, and the
   two-codepoint `i`+U+0307 that `casefold()` actually emits) onto plain ASCII
   `i`. Locale-free, idempotent, and a strict superset of what `casefold` already
   merged, so nothing that is one thing today becomes two.
   The defect was also **larger** than recorded: three Turkish weekdays break,
   not two, plus two Azerbaijani ones that break under plain ASCII `.upper()`
   with no Turkish keyboard involved. And it is **destructive**, not merely a
   dropped constraint — `normalize_state_day_keys` runs from `_auto_save` on a
   debounce timer, so a week written in capitals is read back short and the
   shortened week is written to disk.
2. **Item 1 hid a second defect nobody had pinned.** The recorded defect was
   "one Ctrl+Z after a drag unplaces the lesson". Measured, the same
   unconditional `pop()` also meant a drag **from the sidebar** destroyed an
   unrelated action's only undo snapshot — depth unchanged, which is exactly why
   nobody noticed. **Both fixes the handoff itself proposed were built and both
   fail that case.**
3. **Item 3's prescribed wiring says nothing, ever.** "Wire into
   `InfeasibilityAnalyzer`'s existing `required_room_missing` message" — that
   branch is unreachable when `required_classrooms` is empty, which is precisely
   the state the finding is about. Measured: 8 valid slots, `bottleneck: None`,
   zero blocking categories. An import-time warning was used instead.
4. **Item 4's pin was wrong about its own subject.** It built its damage with
   `_corrupt(blob, "truncated")` — the one shape the reader already handled — so
   it failed on "DID NOT RAISE", not on the defect it named, and a *correct* fix
   left it quietly xfailed rather than turning it red. It was rewritten into
   three guards that assert which records survived, how many were lost, and that
   the two readers agree. The handoff also missed `load_encrypted_lines_since`,
   which carried the identical swallow and is the function the learner actually
   calls.
5. **Item 7's blocking premise is false on the installed reportlab.** "reportlab
   has no bidi and no shaping" — 5.0.1 declares both as optional extras
   (`rlbidi`, `uharfbuzz`) with live code paths. And the shipped figure is
   **3 of 22 locales, not 9**: `_resolve_pdf_fonts` recovers 6 of the 9 from host
   faces. The decision not to act still stands, for a *structural* reason the
   handoff did not give — the resolver **short-circuits on shaped scripts**, so
   no substitute face, bundled or host, is ever tried for ar/fa/hi.
6. **Item 6 was incomplete in the direction it warned about.** It said of the
   last read-found defect, "there is no reason to assume it was the only one."
   It was not: Phase 7's `release.yml` dropped `"$dist\Dersis.exe"` from the
   verify list the deleted `build-release.yml` had carried, while
   `installer.iss` still points the Start Menu shortcut, the Desktop shortcut
   and the post-install launch at that file. `build_embed.bat` compiles it with
   `Add-Type … 2>$null`, never checks it, and had no `exit /b 1`. A failed
   launcher compile would have shipped an installer that installs cleanly and
   whose every shortcut points at nothing.
7. **Item 8f's stated consequence is false.** "The UI cannot tell a cancelled
   solve from a failed one" — `solver_task.py` declares finished/failed/cancelled
   as three separate signals and `app.py` connects three distinct handlers. The
   write-only flag is real; the consequence is not.
8. **The translation ratchet had zero headroom, and two agents measured it
   wrong in the same hour.** Both reported "1700 against 2548" — the count taken
   *without* `import scheduler_app.i18n.tier_translations`. The real figure was
   **2548 against 2548**. This is the third consecutive phase to fall into a trap
   that is written down by name in the handoff.

### The fix for the ratchet, which is better than the handoff's advice

The handoff prescribed "the next English string must move the ratchet in the same
commit". Phase 8 added **six** new user-facing strings and moved the ratchet
**zero** times, by shipping each key in all 22 locales. That adds no missing
pairs at all, and 21 locales get a native message instead of an English fallback.
It costs one scripted insert. Raising the ceiling is the second-best option.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| **ST-ARCH-012** | 🟡 Medium | One Ctrl+Z after a drag puts the lesson back where it was — and a sidebar drag no longer destroys an unrelated action's snapshot. |
| **ST-ARCH-001** item 9 | 🔴 Critical | One case-folding rule (`i18n/text_fold.py`) shared by day keys, the class form, the importer and the workbook schema. A Turkish week typed in capitals no longer loses half its days on the next autosave. |
| [ST-FUNC-009](12-findings-register.md) | 🟡 Medium | `required_room_type` is consumed: resolved to real rooms at import, intersecting with `allowed_rooms`, never widening, never emptying, and warned when it cannot narrow. |
| [ST-FUNC-007](12-findings-register.md) | 🟡 Medium | Legacy plain-ASCII JSON saves load. `_is_fernet_token` asks a positive question (`startswith(b"gAAAAA")`) instead of "do the first 80 bytes decode as ASCII". |
| [ST-DATA-002](12-findings-register.md) | 🟠 High | A damaged feedback log costs the damaged records only, and the user is told. The count, the reader and the learner's cursor now share one framing rule. |
| [ST-FUNC-012](12-findings-register.md) | 🟢 Low | Two teacher names that fold together are reported with a message that names both spellings and says why. |
| **ST-SEC** (packaging) | 🟠 High | The release lane verifies the launcher every installer shortcut points at; `build_embed.bat` fails where the failure happens. |

### Defects this phase introduced, and caught

Three, all found by the adversarial stage attacking work that had already passed
review with a green suite. This is the reason the stage exists.

* **The ST-DATA-002 fix reintroduced ST-DATA-002.** When a record's container
  magic was the damaged byte, the new resync stepped over a whole record and
  reported `lost == 0` — where the reader it replaced reported `lost == 1`. It
  made the common case recoverable and the uncommon case **silent**.
* **`log_entry_count` and the shared walk disagreed.** An inflated length prefix
  (201 → 1225, still passing both guards) left the seek 1 byte from EOF and
  returned 1 where the walk recovered 6. That count is the unit the learner's
  cursor is expressed in.
* **`required_room_type` + `excluded_rooms` stranded a class.** Candidates went
  from `['Oda 1']` at `82f558e` to `[]` — a class the school had been
  timetabling became impossible to place, with an empty import report.

And one the phase caught in its own *testing*: deleting
`self._drag_undo_pushed = True` from production left the entire suite **green**,
because the test helpers set the flag themselves. The headline fix worked and its
wiring was pinned by nothing — verbatim the Phase 7 pattern, one layer down,
inside `tests/`.

### Tests that pinned nothing, caught by mutation

Four, each found only because the mutation was actually run:

* a redo assertion that **could not fail** — every real action clears the redo
  stack, so it was green under both the fix and the wrong fix;
* a matcher that reduced to `"" in anything`, because it sliced a translated
  string that opens with a placeholder — **two tests passed with the production
  report deleted entirely**;
* a room-type warning test that stayed green under its own mutation, because the
  discriminator alone already changed the wording;
* a substring assertion that had to become an exact-sentence assertion before it
  could see its mutation.

The rule this phase would restate: **confirm the mutation actually landed
(`git diff --stat`) before believing any result**, and treat a green mutation as
a finding about your test, not a fact about the code.

### Known gaps left behind

1. **Nine verified defects** are recorded in
   [`HANDOFF-PHASE9.md`](HANDOFF-PHASE9.md) §B, to be fixed first in Phase 9.
   The two drag residues (B1, B2) share one root cause and one cure.
2. **Ten unverified incidentals** are in §C, to be *checked* before being fixed.
3. **Nothing in the release/packaging cluster was executed** — the user declined
   the rehearsal. `main` has never been pushed: `origin/main` is still at the
   Phase 6 merge, 81 commits behind.

---

## Phase 7 — complete

> Superseded by Phase 8. The handoff this section points at
> ([`HANDOFF-PHASE8.md`](HANDOFF-PHASE8.md)) is retained because its §6 carries
> the release-rehearsal script, which is still unrun — but **six of its eight
> items were wrong or incomplete**, and the corrections are recorded in the
> Phase 8 section above. Read that first.

**Suite: 1026 tests — 1018 pass, 8 known-defect pins, 0 failures.** Both lanes
exit 0. (725 at the start of the phase: **+301 tests, and 7 pins deleted**; two
were added that document newly-measured open defects.) `mypy` is clean over the
six Qt-free packages. **All four layering ratchets are now `0`.**

All seven roadmap rows are done, all four Phase 6 carry-overs are resolved, and
**seven of the thirteen `xfail` pins the roadmap never scheduled are closed by
fixing the defect** (ST-FUNC-004, 006, 010, 011, 012, and both ST-FUNC-013
cases). One more was **retired** because its reason string described code
ST-PERF-005 had already deleted. Three new pins were added, each documenting a
defect this phase measured and did not fix: 13 → 8.

**An adversarial round then attacked everything this phase landed**, and every
candidate was independently reproduced or refuted by a second agent that
defaulted to REFUTED: **20 CONFIRMED, 9 PARTLY, 1 REFUTED**. All 29 are fixed or
narrowed. Two of the worst were introduced by this phase's own work, including
its headline fix — see "What the adversarial round caught" below.

### The headline is a data loss on the upgrade path

**Opening the new build for the first time destroyed the user's entire
schedule.** Not in the register, not in the roadmap, found by asking which of
the "top-10 untested behaviours" were genuinely untested.

`run_language_gate()` writes `settings/app_settings.egu` to record the chosen
language. `storage.migrate_legacy_files()` was called only from
`SchedulerApp.__init__`, i.e. *after* it. And `_migrate_json_file`
(`storage.py:677-679`) is:

```python
if os.path.exists(dest_sav):
    _backup_original(src)   # move the user's file out of the way
    return False            # ...and do not migrate it
```

Measured on a simulated frozen install:

| order | classes recovered | language |
|---|---|---|
| language gate first (**shipped**) | `[]` | `tr` → `en` |
| migration first (counterfactual) | `['LEGACY-LESSON']` | `tr` |

So a user upgrading from the pre-DERSİS build picked a language and landed on an
empty timetable, with their whole schedule sitting in
`backups/scheduler_config.json` and nothing in the UI saying so. Their saved
language was lost too. `grep migrate_legacy_files tests/` returned **zero**
matches: `scheduler_gui.py` is imported by no test, which is why seven phases
never saw it.

Half of this had already been fixed by accident — ST-DATA-012 moved the
single-instance lock ahead of the gate, and the lock calls `ensure_dirs()`, so
the *folder* copy was carried over. Only the *file* migration was left behind.
The register's stated reason for the finding ("the language gate creates dirs
before `ensure_dirs`") describes the half that was already closed.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| **ST-ARCH-001** | 🔴 Critical | **Closed.** The upgrade data loss above; the three genuinely-untested top-10 behaviours now pinned; drag-and-drop tested for the first time. |
| [ST-FUNC-004](12-findings-register.md) | 🟠 High | The printed timetable spells Turkish names. Six letters, not "every Turkish letter" — and the failure was a falsified text layer, not a box. |
| [ST-FUNC-006](12-findings-register.md) | 🟡 Medium | The **live** CSV writer is UTF-8 and localizes its day column. The pinned one had no production caller. |
| [ST-FUNC-011](12-findings-register.md) | 🟡 Medium | An unrecognized workbook is no longer announced as a successful import. |
| [ST-FUNC-012](12-findings-register.md) | 🟡 Medium | Duplicate lecturer names are reported, case-insensitively. |
| [ST-FUNC-013](12-findings-register.md) | 🟢 Low | The PDF names the lessons it could not draw instead of dropping them. |
| [ST-SEC-001](12-findings-register.md) | 🟠 High | No workflow publishes a release from a branch push. `build-release.yml` deleted. |
| [ST-SEC-002](12-findings-register.md) | 🟡 Medium | The README says what the storage actually protects. |
| [ST-SEC-003](12-findings-register.md) | 🟡 Medium | The installer no longer makes the program directory world-writable. |
| [ST-SEC-004](12-findings-register.md) | 🟡 Medium | Every build-time download is pinned and hash-gated. **This one had already fired.** |
| [ST-SEC-005](12-findings-register.md) | 🟡 Medium | A missing dependency is reported, not installed over the network. |
| [ST-SEC-006](12-findings-register.md) | 🟢 Low | No credential crosses the CDN redirect; an unverifiable download is a hard failure. |
| [ST-SEC-007](12-findings-register.md) | 🟢 Low | The AppId is pinned with the reason it must never change. |
| [ST-SEC-008](12-findings-register.md) | 🟢 Low | The Windows account name stays on the machine when a bug report is sent. |
| [ST-ARCH-009](12-findings-register.md) | 🟡 Medium | **Reopened and re-closed.** The macOS bundle collected 13 of 58 modules. |
| [ST-ARCH-010](12-findings-register.md) | 🟡 Medium | **Closed outright.** The 15-module knot is gone; every layering ratchet is `0`. |
| [ST-ARCH-011](12-findings-register.md) | 🟡 Medium | Three calls to functions that exist nowhere, deleted and guarded. |
| [ST-PERF-001](12-findings-register.md) | 🔴 Critical | Regression-guarded: a work-count ratchet, and CI now runs the solver gates written for it. |
| [ST-UI-013](12-findings-register.md) | 🟡 Medium | **Closed.** The window opens the size of the screen; the sidebar yields to the grid. |
| [ST-DATA-002](12-findings-register.md) | 🟡 Medium | Partly — the append half was **already fixed**; the pin was describing deleted code. |

### Where the register was not enough

As in Phases 1–6, each was proved by building the naive version and watching it
fail, or by measuring rather than assuming.

1. **ST-SEC-004 was not hypothetical — it had already fired, and it had been
   breaking every release build for two days.**
   `https://jrsoftware.org/download.php/is.exe` now serves an HTML page
   (302 → `/isdl.php`, `text/html`, 10 478 bytes).
   `Invoke-WebRequest -OutFile` writes it into `innosetup.exe` and **exits 0**;
   `Start-Process` then dies *"The file or directory is corrupted and
   unreadable."* — verbatim the failure of build-release runs 11 and 13–16. Had
   the served bytes been a working `.exe` it would have been installed with
   `/VERYSILENT` and used to compile the installer users download, with no hash,
   no signature and no log line. An unverified download had already been
   silently substituted; it was benign only by luck.
2. **"Switch to `release.yml`, it already does the right thing" ships nothing.**
   `release.yml` has **20 runs, every one a startup failure, zero jobs ever
   executed** — and it carries the identical broken Inno step. The YAML parse
   error was fixed in June, but it still cannot be reached: `build-release.yml`
   creates its tag with `secrets.GITHUB_TOKEN`, and GitHub suppresses workflow
   triggering from GITHUB_TOKEN events. Proof: three `v*` tags exist and
   `build-installer.yml` (`on.push.tags: v*`, present before all three) has **0
   runs, ever**.
3. **A tag gate would have gated on nothing.** `ci.yml` did not trigger on tags,
   so a tag push ran **zero tests** — and `ci.yml`'s own "Verify tag matches
   VERSION (on tag push)" step, guarded by `startsWith(github.ref,
   'refs/tags/v')`, was unreachable dead code.
4. **The macOS bundle could never open its main window.** `scheduler_gui.py`
   imports through the `sys.meta_path` shim (`scheduler_app.app`, `.first_run`,
   `.translations` are aliases with no file on disk), and `Dersis-mac.spec`
   declared no hiddenimports. Measured with PyInstaller's own modulegraph:
   **13 of 58 modules collected, 45 dropped, 0 warnings emitted.** The READMEs
   advertised those `.dmg` downloads. (No release has ever carried one, which is
   the only reason nobody hit it.)
5. **ST-FUNC-006 is ST-FUNC-005 repeating one format later.** The pinned test
   drives `exporter.py::_export_csv`, which **has no production caller** — the
   CSV a user gets comes from `ui/app.py::export_csv`. The dead copy already
   wrote `encoding="utf-8"`; the live one had no `encoding=` at all, so it wrote
   cp1254 and raised `UnicodeEncodeError` on a cp1252 machine. The suite's
   *passing* "CSV is UTF-8" guard was also guarding the dead writer.
6. **ST-FUNC-004 is six letters, not "every Turkish letter", and the failure is
   worse than a box.** `öüçÖÜÇ` are WinAnsi and render correctly; only
   `ğĞşŞıİ` break. reportlab does not draw tofu — it splits the paragraph at
   each unmappable codepoint and switches to **ZapfDingbats**, drawing the ASCII
   letter `n`, which paints as a solid block. So the page looks redacted rather
   than broken, and the **text layer is falsified**: Ctrl-F for "Öğretmen" finds
   nothing and copy-paste yields `Önretmen`.
7. **And the recommended fix was unnecessary work.** "Bundle a Unicode TTF (e.g.
   DejaVu)" would add ~700 KB, a new asset directory, an `installer.iss` entry
   and a `build_nuitka.bat` line — to duplicate `Vera.ttf`, which **reportlab
   already ships** (283 glyphs, missing none of the twelve Turkish letters) and
   which `--include-package-data=reportlab` already collects. Installer delta:
   **0 bytes**.
8. **ST-SEC-005's stated premise is false.** "It can't work in the frozen build"
   — `build_embed.bat` installs pip via `get-pip.py`, uncomments `import site`
   in `python._pth`, and *gates the build* on `python.exe -m pip install -r
   requirements-lock.txt`. Measured from a windowless pythonw with NULL std
   handles: `check_call([exe,"-m","pip","install","--no-index","reportlab"])` →
   **exit 0**. Where it *is* true is Nuitka/PyInstaller, for a reason the finding
   never states: `sys.executable` is the app binary, so "install" silently
   **relaunches DERSİS** and blocks until the user closes the second window.
9. **ST-SEC-002's defect is a heading, not a sentence.** A 22-locale scan found
   **zero** strings promising confidentiality. Every factual claim is true —
   AES-256-GCM, unique salt and nonce per file, SHA-256 integrity. The encryption
   bullet simply sits under a section titled *privacy*, beside "no network
   calls". Cost: **12 lines in 4 Markdown files**, not the register's Effort L
   across 22 locales; `translations.py` needed **zero** edits. And the suite had
   been asserting the false version for six phases — a docstring told the reader
   a green test meant the data was not "readable by anyone with access to the
   Documents folder", which a ten-line probe disproves while that test passes.
10. **DPAPI was rejected on measurement, not preference.** It buys nothing on a
    shared Windows login (one profile = one principal) and near-nothing on
    separate logins, while creating a **new permanent data-loss mode**: the naive
    wrapper broke 3 of 42 storage tests because a 282-byte DPAPI blob trips
    `storage.py:218`'s `len(key) == 32` and reports ST-DATA-001's "your saved
    timetables cannot be opened". A user resetting their Windows profile would
    lose every save.
11. **ST-SEC-003's escalation needs a case the finding does not state.**
    `PrivilegesRequired=lowest` means Setup never runs elevated "even if it was
    started by a member of the Administrators group", so `{autopf}` is **never**
    Program Files. The real default-install exposure is different and still real:
    `%LOCALAPPDATA%\Programs\Dersis` inherits SYSTEM/Administrators/owner and
    **no** `BUILTIN\Users`, and the Inno grant adds it. All three candidate
    justifications for `users-modify` measured **false** — `build_embed.bat`
    deletes every `.py` and ships sourceless `.pyc`, and a write-denied directory
    imports fine (`rc=0`, zero files written; CPython swallows the bytecode
    `OSError`).
12. **ST-SEC-007's implied fix is itself the bug.** Inno keys upgrade *and*
    uninstall detection on AppId. `Dersis_Setup_v1.0.0.exe` has 106 downloads; a
    new GUID makes every existing install invisible to the new setup — two
    Add/Remove entries, and uninstalling either deletes the other's files.
13. **Phase 6's own ST-UI-013 headline is wrong.** It recorded the sidebar's
    `minimumSizeHint` as locale-dependent, 140–253 px, "never the 301 on record".
    Read off the live widget it is **tr = 301 exactly** — the register's original
    number. Phase 6 summed regular-weight text advances; the real hint is
    `12 + minSizeHint(button) + 4 + minSizeHint(button)` and both buttons are
    bold, padded and emoji-prefixed. The locale-dependent number that actually
    bites is the **tab bar** (ko 913 … tr 1148 … ru 1214 … id 1232).
14. **ST-UI-013 is not about small screens.** The app's own default window —
    1150×720, never saved and never restored — draws an 841×607 timetable into a
    769×457 viewport. **Both scrollbars, every launch, every machine.** It clears
    the Turkish tab bar by exactly 0 px and fails `id`, `pl` and `ru` outright.
15. **The translation ratchet had zero slack, and the measurement that said
    otherwise was taken with a broken instrument — this one.** Mid-phase, two
    agents disagreed: one reported the backlog at its 2508 ceiling, another at
    1660. The disagreement was settled by re-measuring, and the re-measurement
    was wrong: it read `TRANSLATIONS` **without importing
    `scheduler_app.i18n.tier_translations`**, which merges **52 further `en`
    keys** into the catalogue on import. `test_translation_coverage.py` imports
    it; a count that does not is taken against a half-built catalogue.

    | counted | `en` keys | missing pairs |
    |---|---|---|
    | without the tier import | 1022 | 1700 |
    | **as the test counts it** | **1074** | **2548** |

    So the ceiling was exact and the "848 pairs of headroom" figure — which then
    propagated into two agent briefings — never existed. Two independent fix
    agents caught it within the same hour, each while adding a key. The house
    rule is *verify the evidence, not just the claim*; the corollary this phase
    adds is that **re-measuring only helps if the instrument matches the one
    that matters.** Here the authority is the test, not a REPL.
16. **The "reuse `tests/scheduler_benchmark.py`" row does not survive contact.**
    It has **0 asserts**, hard-codes `REPO = r"C:\dev\dersis-app"`, and appends
    to a repo-tracked CSV; 28 of its 432 lines port. And a wall-clock gate is the
    wrong instrument: 11 real `ubuntu-latest` runs spread 1.36–1.49×, and the
    runner is **1.87× faster** than the audit box, so a locally-calibrated
    threshold is ~1.9× wrong before variance is counted.
17. **CI was not running 13 of the suite's 19 `slow` tests.** The engine job ran
    only `test_scheduler_invariants.py`, so `test_greedy_bounds.py`'s placement
    floors and both slow reproducibility pins — written for exactly this purpose
    — executed in **no CI job at all**.
18. **The god object cannot be fixed by extraction, and the number that matters
    had never been measured.** Six plausible seams were built; **all six leave
    the Maintainability Index at exactly 0.00**, because the complexity term
    alone (−205.4) exceeds the formula's 171 constant. Meanwhile `ui/app.py` is
    **47.7 %** covered, **79 of its 145 `SchedulerApp` methods never execute a
    single statement**, and `SessionStore` — the seam the roadmap wanted
    extracted — is the *best*-covered part at 86 %. Six phases of remediation
    **raised** the file's complexity from 838 to 893.

### Defects found in passing, none in the register

- **Drag-and-drop had no test at all.** Replacing `_execute_drop`'s body with a
  bare `return` left the entire suite — 859 passed — green. The one helper that
  claimed to mirror it "phase for phase" cited a line range hundreds of lines
  stale and omitted two of production's inputs, so it answered `valid=True`
  where production says `valid=False`.
- **Three calls to functions that exist nowhere.** `find_conflicting_classes`
  (×2) and `_get_valid_slots` are called in `core/logic.py` and defined in no
  file. Reachable only from `cascade_relocate` and `_unplaced_reason`, which
  have no callers — so wiring any of it raises `NameError` on the first call.
  This is the inverse of Phase 6's "dead code that should not be dead".
- **A strict pin was describing deleted code.** `test_append_does_not_overwrite_a_corrupt_log`'s
  reason string says `append_encrypted_entry` "rebuilds from the swallowed empty
  list and overwrites the corrupt log, destroying history". ST-PERF-005 replaced
  that with an O(1) append; measured, the damaged bytes survive verbatim as a
  prefix. A strict pin whose reason is false is exactly how ST-FUNC-005 survived
  six phases guarding a bug in code with no callers.
- **Two ST-DATA-013 pins have no production producer.** `new_state()`,
  `new_class()`, the default learned weights and every state dict are string-keyed
  and finite, and the importer coerces every name through `_cell_text` — so an
  Excel room literally named `42` arrives as `"42"`. True at the library level,
  unobservable to any user.
- **21 of 22 language switches sized the sidebar against the previous
  language.** `setText` only *posts* a layout request; without `layout().activate()`
  the new hint is not readable yet.
- **`build_nuitka.bat` never shipped `VERSION`**, so `iscc` emitted
  `Dersis_Setup_v0.0.0.exe`, and its `ERRORS` list printed "BUILD SUCCESSFUL!"
  over a missing-asset report.
- **A mutation harness reported a false negative.** Every workflow file is CRLF,
  so 13 of 24 multi-line mutation patterns silently did not apply and the harness
  measured an *unmutated* tree — concluding "your test pins nothing" when nothing
  had happened. Phase 4 recorded the same class of failure with stale
  `__pycache__`. **A mutation test that cannot see its own mutation manufactures
  confidence.**
- **`os.chmod(key.bin, 0o600)` is a no-op on Windows** — `st_mode` 0o100666
  before and after, identical DACLs. Recorded, not fixed.
- **`requirements-lock.txt` does not describe the environment the suite runs
  in** — it pins `reportlab==4.4.10`; `.venv-audit` has 5.0.1.

### What the adversarial round caught

Eight agents attacked the landed work; every candidate was independently
reproduced or refuted by a second agent that **defaulted to REFUTED**. 20
CONFIRMED, 9 PARTLY, 1 REFUTED. The pattern from Phase 4 held: nothing here was
visible from the suite being green.

**Two of the three worst were introduced by this phase, and its own tests could
not see either.**

1. **The headline data-loss fix did not fire on the shipped build.** The
   ordering fix was right; the *path* was not. `_old_app_config_path()` resolved
   to `scheduler_app/storage/scheduler_config.json` — two directories below the
   app directory its own docstring names. Reproduced unstubbed: `notes []`, no
   settings written, legacy file still in place. The frozen branch was dead too:
   `build_embed.bat` ships a C# wrapper launching `pythonw.exe`, so `sys.frozen`
   is never set. **And the test passed because it stubbed the path** — a second
   finding showed its language assertion also passed with the fix removed. Both
   halves fixed; one assertion now calls the real unstubbed resolver.
2. **A new `xfail(strict=True)` would have reddened the engine job on every
   run.** It pinned `deterministic is True` as *failing*, on the premise that an
   80-class solve cannot finish 5 restarts in 120 s. Measured: it XPASSes at
   96.4 s with 20 % headroom, and CI is 1.87x faster. Worse, the verdict flips
   on one machine within an hour — 96.4/103.6 s idle, 120.07/120.24 s loaded —
   so it was non-deterministic on the author's own box. Its reason string also
   named `core/logic.py` (the code had moved to `core/facade.py`) and cited
   "11/11 CI runs" that could not have included it, since no CI job ran that
   module and the test is `slow`. **This phase spent effort retiring a pin whose
   reason described deleted code, and then wrote a new one.**
3. **The first `v*` tag would have published nothing.** `publish` needs
   `build-macos`, whose x64 leg targeted **`macos-13`, retired by GitHub**
   (verified live against the runner-images API). And the Windows-only fallback
   written for exactly this case was unreachable — the `::warning::` line lives
   *inside* the job that gets skipped. The release row was this phase's largest
   effort and would have shipped nothing on first use.

**Defects the round found in work that was not new**

- **The live CSV reports the wrong hour for every group after the first** in a
  non-joint lesson. The grid, the PDF and the XLSX matrix all add
  `slot_offset_for_target`; `SchedulerApp.export_csv` was the only one of the
  four surfaces that did not — and it is the default shape, since the class
  dialog writes `joint_session=False` whenever there is more than one target.
  The new `test_export_csv_live.py` could not see it: every state it builds is
  single-target.
- **The Spanish template cannot be re-imported** — Spanish *Aulas* is claimed by
  Portuguese *Classes* in the flat alias map, so a Spanish user re-importing the
  app's own generated template gets classrooms read as classes.
- **The zh/ja template imports phantom data.** The description-row heuristic
  never fires for CJK, so the template imports as **valid** with a phantom
  lecturer, classroom and branch made of instruction text.
- **ST-FUNC-004 was closed on twelve Turkish letters while the product ships 22
  languages.** Vera covers Latin-1 only, so **9 of 22 locales** printed their
  weekday names as boxes. Not a regression — before the fix the same locales drew
  ZapfDingbats blobs — but the fix's own three tests ran only in the default
  locale.
- **A `%` followed by two hex digits was eaten out of the bug-report body.**
  Turkish writes every percentage that way, and that field is the one place a
  user writes free prose.

**Where a proposed fix was itself wrong** — the round's verifiers proposed
remedies, and four were rejected on measurement:

- `always() && needs.build.result == 'success'` **reintroduces the defect beside
  it**: a job-level `if:` replaces the implicit `success()` for *every* need, so
  a `publish` that also needs the new `test` job would publish a red tag.
- Resolving the app directory via `__main__.__file__ or sys.argv[0]` answers a
  *different* directory under pytest than under `pythonw.exe`, and makes the very
  assertion the finding demands unwritable.
- Substituting a host font for Arabic/Hebrew/Indic: reportlab has no bidi and no
  shaping, so `arial.ttf` emits Arabic in **logical order in isolated forms** — a
  word printed backwards still reads as a word, which is worse than a box that
  announces itself. Undrawable codepoints are named on a final page instead.
- Excluding the space from the redaction's segment boundary turns
  `C:\Users\Ayşe Yılmaz\Documents` into `C:\Users\<user> Yılmaz\Documents` —
  trading a cosmetic over-redaction for a real leak. Measured and declined; the
  refusal is pinned by its own test.

### Behaviour changes worth knowing

- **The printed PDF spells Turkish names**, and its text layer is searchable.
  Every PDF grows ~40 KB (one embedded font subset per document).
- **The exported CSV is UTF-8 with a BOM and localized day names.** A colleague
  on a non-Turkish Windows can open it; previously it was written in the OS
  codepage and raised on a cp1252 machine.
- **An import that recognizes no sheet is now an error, not a success.** A
  workbook with duplicate lecturer names is reported rather than silently
  merged — including case variants, which used to import as two teachers whose
  classes carried different strings while the class form treated them as one.
- **The app never runs `pip`.** A missing dependency is reported. This makes the
  README's "no network calls of any kind" true for the first time.
- **A bug report no longer carries your Windows account name.** The on-disk
  crash log still does, on purpose — it never leaves the machine and a local
  support person needs the real path.
- **The window opens at the size of your screen and remembers where you left
  it.** First run is maximized. The sidebar yields 314 px to the grid when the
  grid needs it, and an explicit expand survives later resizes.
- **`Ctrl+B` toggles the sidebar.** It had no shortcut.
- **Releases are tag-gated.** A push to `main` no longer publishes anything;
  `build-release.yml` is deleted. Every build-time download is pinned and
  hash-checked before it is executed.
- **The installer no longer makes its own program directory writable by every
  local account**, and the uninstaller now removes the bundled Python tree it
  was leaving behind.
- **`ci.yml` runs on tags**, and the engine job runs the solver-quality gates
  that were written for it and had never executed anywhere.

### Known gaps left behind

1. **Nothing in the release/packaging cluster was executed.** No workflow ran,
   `iscc` is not installed, PyInstaller was not run, no `.app` was launched.
   What *is* evidence: the Inno digest was fetched twice and compared, and
   PyInstaller's own `collect_submodules("scheduler_app")` returns 58 names —
   exactly the 58 files on disk, up from the measured 13. **Unproven:** that
   `release.yml` completes at all (20 runs, zero jobs ever); that Inno 6.7.3
   compiles `installer.iss` as the June build did; that a `v*` tag triggers
   exactly `ci.yml` + `release.yml`. **Rehearse a `v1.0.1` tag on a scratch fork
   before trusting any of it.**
2. **Eight pins remain. Five are deferred defects, three are new.**
   *Deferred:* ST-FUNC-009 (`required_room_type` is advertised in the template
   and never consumed — the only one that adds solver input, ~4 h), ST-FUNC-007
   (legacy ASCII saves misroute to the Fernet branch), the swallow half of
   ST-DATA-002 (one flipped byte silently disables preference learning forever),
   and the two ST-DATA-013 pins, which are **documentation rather than defects** —
   true at the library level with no production producer, measured, and their
   reason strings now say so with the date.
   *New this phase, each pinning something measured and not fixed:* a Turkish
   day name typed in capitals (`PAZARTESİ`, `SALI`) is not recognised, because
   `casefold()` is not Turkish-correct on the dotted/dotless I — the same root
   cause the importer's duplicate-lecturer check declined to fix unilaterally,
   since `register_lecturer` shares it and the two must not diverge; and one
   undo after a drag does not put the lesson back where it was.
4. **No installer is signed.** The hook is wired behind a secret and is inert:
   `gh secret list`, `gh variable list` and the environments list are all empty.
   A checksum is published instead, and that is what the README should say.
5. **The README still advertises a macOS build that has never been released.**
   The download promise was corrected, but `release.yml` attaches mac artifacts
   with `fail_on_unmatched_files: false`, so it can silently publish
   Windows-only again.
6. **`os.chmod(key.bin, 0o600)` is a no-op on Windows.** Measured; not fixed.
7. **`requirements-lock.txt` is stale** — it pins `reportlab==4.4.10` while the
   audit venv runs 5.0.1, so the lock does not describe the environment the
   suite validates against.
8. **`check_untyped_defs` is still off** at 168 errors, unchanged from Phase 6.
9. **The translation backlog is 2548 (locale, key) pairs** against a ceiling
   this phase moved deliberately from 2508, for the two `en`+`tr` keys it added
   (`export.unprintable_note`, `bug_report.no_mail_client`). There is **no
   slack**: the ceiling is exact, so the next English string anyone adds must
   move this number in the same commit. Measure it **with
   `scheduler_app.i18n.tier_translations` imported** — without it the count
   reads ~850 pairs low, which is a mistake this phase made and had to correct.
   It still needs a translator.
10. **The work-count ratchet's anti-vacuity floor does not catch a broken
    validator.** A `check_placement` stubbed to `True` lands *inside* the band.
    The gate gets cheaper regressions, not correctness; the oracle is what
    guards correctness.

---

## Phase 6 — mostly complete

> Phase 7's handoff was [`HANDOFF-PHASE7.md`](HANDOFF-PHASE7.md); it is kept as a
> record.

**Suite: 725 tests — 712 pass, 13 known-defect pins, 0 failures.** Both lanes
exit 0. (671 at the start of the phase: +54 new tests, and **11 pins deleted**,
none added.) `mypy` runs in CI over the five Qt-free packages at **0 errors**.

Six of the roadmap's seven rows are done, plus two of the three Phase 5
leftovers. **ST-UI-013 (the responsive shell) is still not built** — see below;
its remaining premise turned out to be language-dependent, which nobody knew.
The `SessionStore` extraction and the `dialogs.py` split were **deliberately
descoped** to the defects they surfaced; the reasoning is under "The two rows
that were not built as written".

### The headline is not an architecture finding

**Ctrl+Z, or deleting a class, could kill DERSİS silently.** New finding,
ST-ARCH-015, found while scouting ST-ARCH-012 and not in the register.

`refresh_grid` → `_render_current_tab` → `_clear_class_selection` →
`_refresh_open_slots` → `_open_slots_fingerprint` → `selected_classes` reads the
unplaced sidebar's stored *positions* into `state_data["classes"]` **before**
`_update_side_panels` rebuilds them. After any shrink of the classes list, with
a row selected in that sidebar, the reader indexed the new short list with an
old long-list position. Two live failures:

1. **The process died.** `IndexError` inside a Qt slot, and PyQt6 answers that
   with `qFatal()`. Measured: exit **`0xC0000409`**, empty stdout, empty
   stderr — no dialog, no traceback, no crash report, and any edit still inside
   the 1.5 s autosave debounce unwritten.
2. **Silently the wrong class.** When the shrink was at the *front*, the stale
   position still resolved — to a different class than the one highlighted.
   That half never raised, so no crash could ever have revealed it.

Fixed by addressing classes through `class_uid`, whose docstring already said it
exists so identity "survives serialization, copying, and list mutations".
Bounds-checking the position instead would have fixed the crash and left
failure 2 alive.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| **ST-ARCH-015** | 🔴 Critical | **New.** The unplaced sidebar addresses classes by identity, not position. Ctrl+Z and Delete no longer kill the app. |
| [ST-ARCH-003](12-findings-register.md) | 🟠 High | One Excel engine, and it is the one the menu reaches. `mode` reaches Excel for the first time. |
| [ST-ARCH-009](12-findings-register.md) | 🟡 Medium | **22 upward layering violations → 0.** New `scheduler_app/i18n/` leaf package; the ceiling is now a hard contract. |
| [ST-ARCH-010](12-findings-register.md) | 🟡 Medium | Deferred imports in `logic.py` 21 → 13; mutually importing pairs 9 → 7. Enforced as a ratchet. |
| [ST-ARCH-011](12-findings-register.md) | 🟡 Medium | The legacy solver family and 9 more dead symbols deleted; `logic.py` 1668 → 1349 lines. |
| [ST-ARCH-012](12-findings-register.md) | 🟡 Medium | Undo covers the whole state. **Setup is undoable** — the fix Phase 4 had to withdraw. |
| [ST-ARCH-013](12-findings-register.md) | 🟡 Medium | `mypy` gate at 0 errors over the engine; `ClassDict`/`StateDict` declared; the KeyError the finding names is fixed. |
| [ST-FUNC-005](12-findings-register.md) | 🟠 High | **Closed by deletion.** The crash only ever existed in the unused engine. 11 strict pins deleted. |
| [ST-UI-005](12-findings-register.md) | 🟠 High | **Reopened and re-closed.** Phase 5's fix never reached the Excel file (below). |
| [ST-UI-006](12-findings-register.md) | 🟠 High | The grid has a colour key that groups years sharing a swatch instead of claiming a mapping the palette cannot make. |
| [ST-UI-007](12-findings-register.md) | 🟡 Medium | Phase 5's Qt-side escaping was written and never called. Wired. |
| [ST-UI-017/018/020](12-findings-register.md) | 🟢 Low | Dropdown caret restored; all validation errors shown; **a typed lecturer is registered**. |

### Where the register was not enough

As in Phases 1–5, each was proved by building the naive version and watching it
fail, or by measuring rather than assuming.

1. **ST-ARCH-003 had already cost a shipped fix, silently, one phase after it
   landed.** `export_schedule` is called from the app only with `format="pdf"`;
   the XLSX a school prints comes from `ui/app.py`'s own writer. Phase 5 fixed
   the *other* copy, so the workbook kept the pre-Phase-5 palette. Measured by
   exporting a file and reading the colours back out of it: room `#16A34A` at
   **1.55:1**, class code 3.15:1, lecturer 3.56:1, branch 3.34:1, badge 1.50:1,
   against AA's 4.5:1 — while the screen and the PDF were correct. The guard
   written to prevent exactly this drift scans `renderer` and `exporter` by
   name, and the live writer is in neither.
2. **The suite was testing the wrong engine, and it hid two data-loss bugs.**
   Unifying them turned two tests red immediately: a placed lesson with **no
   target groups**, and one whose **target year was deleted**, are both absent
   from the everything-matrix. The printout looked complete. Neither could fire
   before, because the tests exercised the copy with no users.
3. **ST-FUNC-005 was never reachable.** Registered High, pinned by 11
   `xfail(strict=True)` cases, and the crash existed only in the dead writer —
   the live one has sanitised sheet titles since before the audit. All 11 went
   XPASS the moment the engines merged. The finding is closed by deleting code,
   not by fixing it.
4. **ST-ARCH-010's "11 module-level import cycles" is wrong in both halves.**
   Measured with a shim-aware AST grapher and Tarjan: module-level edges alone
   form **zero** cycles and zero mutually-importing pairs — nothing cyclic runs
   at import time, today or at the audit commit. The cycles appear only once
   `logic.py`'s deferred imports are counted, and then it is not 11 discrete
   cycles but **one 15-module strongly connected component** covering nearly
   all of `core`. You cannot fix a 15-node SCC one cycle at a time, which is
   why the remedy is the `logic` split and not a list.
5. **ST-ARCH-009 is 22, not 19 — and 16 of them never mention `ui`.** They go
   through the flat shim name `scheduler_app.translations`. A grep-driven fix
   finds six of twenty-two. (19 was right at the audit commit; three were added
   by this remediation.)
6. **"The shim makes the leaf move a zero-call-site change" is false.**
   `_SHIM_MAP` carries only the flat `translations` name; `day_keys`,
   `badge_formatter` and `cell_formatter` are imported by real path at 31
   statements and are not in the map at all. Doing what the audit says breaks
   26 of 58 modules — the app does not start.
7. **`cell_formatter` must not move, though the finding lists it.** Its
   `tooltip_text` needs `core.logic.classroom_of`, so relocating it converts a
   `core → ui` violation into an `i18n → core` one and puts the leaf package
   inside a cycle — strictly worse than the defect. Its one dependency-free
   function moved to its single caller instead.
8. **ST-ARCH-011 undercounts by 3×.** "~30 dead/unreachable symbols" measures
   at **91**; the Phase 3 comment's three named orphan helpers are three of
   eleven.
9. **ST-ARCH-013's remedy does not address the failure it cites.** A TypedDict
   catches a missing key at *neither* totality, is blind to `.get()` (over half
   of all class-dict reads), and the cited KeyError lives in a **third** dict
   shape the remedy never mentions. `[name-defined]` needs
   `check_untyped_defs`, which costs 168 errors here and finds **zero**
   name-defined problems. So the crash was chased directly instead — and it was
   still live: `get_lecturer_availability` fell back to defaults only when the
   lecturer key was *absent*, so a present-but-partial record raised `KeyError`
   in `lecturer_available_at`. Exactly the audit's example, three phases on.
10. **ST-ARCH-012's cost premise is stale.** Full-state snapshots measure
    **+1.6–2.9 % time and +2.6–3.8 % memory** over classes-only, across the
    whole 50-entry stack, because the classes list is ~97 % of the bytes either
    way. The audit's "stacked on the per-refresh encryption write" was removed
    by Phase 2.
11. **ST-UI-006's collision pairs are not the ones predicted.** The handoff
    said Year-01 and Year-09, from the modulo. Year names are free text and
    `sorted()` is lexicographic, so for a real Turkish school `"10. Sınıf"`
    sorts between `"1."` and `"2."`, and 12 years give **four** pairs —
    `1./6.`, `7./10.`, `8./11.`, `9./12.` Which years collide depends on how
    the school names them, which is why the legend is built from the live list.
12. **"No legend fits" was an offscreen artifact.** Measured under
    `QT_QPA_PLATFORM=offscreen` a 12-chip legend is 1449 px and fits no
    supported window; measured natively with real Segoe UI it is **738 px** and
    fits comfortably. The offscreen fallback is fixed-pitch, so every advance
    roughly doubles. The analysis that recommended dropping the row had walked
    into the exact trap `tests/README.md` warns about.
13. **ST-UI-020's item (a) fix is a no-op as written.** "AddClassDialog
    validates before the constraint checkboxes are read, so contradictory
    allow/exclude sets are never checked" — the ordering is real, but
    `validate_class_fields` has no contradiction check at all, so moving the
    reads changes nothing. Contradictions *are* detected, by
    `ConstraintNegotiator`, after commit, in a collapsed log line.

### Defects found in passing, none in the register

- **A typed lecturer name was a delayed, silent unplacement.** The combo is
  editable; nothing registered the name; `reconcile_placements` treats a
  lecturer absent from `state["lecturers"]` exactly as it treats a **deleted**
  one. So the next Setup OK unplaced the lesson and blamed whatever the user
  had just changed in Setup. Availability never applied to that teacher either,
  because no UI can create a record for an unlisted name.
- **The CP-SAT subprocess answered in English.** `translations._current_lang`
  is a module global and Windows multiprocessing uses **spawn**, so the child
  re-imports and resets it. Measured: a Turkish parent got `'Optimum'`, the
  child produced `'Optimal'`. Those are the *unplaced reasons* the user reads,
  so a Turkish school running Thorough got an English list.
- **A dead CP-SAT child silently downgraded the run.** Every failure path
  returned `None` and the caller fell back to the heuristic with no message and
  no summary key. The user asked for Thorough, got Quick, same completion
  dialog.
- **`_flush_before_state_swap` had zero callers** and its own docstring
  describes the loss: an edit inside the 1.5 s debounce followed by File ▸ New
  or File ▸ Open never reaches disk.
- **`text_safety.escape_qt_rich` / `qt_tooltip` were written in Phase 5 and
  never called.** `setToolTip` sniffs its argument with `Qt.mightBeRichText`,
  so the *format* of a grid tooltip was decided by the user's own class name.
- **`build_nuitka.bat` enumerates subpackages one by one**, so the new `i18n`
  package would have shipped a Windows build with no translation table at all.

### The two rows that were not built as written

**`SessionStore` (ST-ARCH-005/006) was descoped to its defects.** Measured, the
extraction is worth **4.7 % of `app.py`** and moves its Maintainability Index by
**exactly zero** — `app.py` is at the 0.00 floor because of its size, and one
seam does not change that. The audit's premise that the code is Qt-free is also
no longer true: Phase 1 closed ST-ARCH-006 by giving `_auto_save` a user-facing
error channel, and that channel is Qt. What the row was really worth is the
defects it surfaced, and those are fixed. The god-object finding is real and
still open; it needs a plan that moves the number, not one seam.

**The `dialogs.py` split was descoped for the same reason.** Splitting by class
moves 14 of 15 modules into radon's A band and leaves `setup_dialog.py` at
**exactly 0.00** — the same floor the finding is about. The MI is relocated, not
fixed. The shim hazard was checked empirically and is *absent* (a throwaway
replica of the real `_ShimLoader` passes all five checks with the target as a
package, including `scheduler_app.dialogs is scheduler_app.ui.dialogs`), so the
move is safe whenever someone wants it — it is the *value* that is unproven.

**ST-UI-013 (responsive shell) is still not built**, and the reason has changed.
Phase 5 deferred it because the numbers were wrong. Measured natively now, the
one number everyone treated as a constant is not one: the sidebar's
`minimumSizeHint` is **the width of two translated strings**, and it ranges from
**140 px (ja)** through **195 px (tr)** to **253 px (ru)** — never the 301 px on
record. So the responsive breakpoint differs per language, and any threshold
calibrated in one locale is wrong in another. That is a bigger finding than the
row, and it needs its own pass.

### Behaviour changes worth knowing

- **The Excel export is one engine.** Sheets are named after the year / room /
  group / lecturer, localized and deduplicated — the `T_`/`R_`/`B_` prefixed
  sheets are gone with the writer that made them. A lesson the matrix has no
  column for now gets its own sheet instead of vanishing.
- **Undo covers everything, and Setup is undoable.** The entry is recorded only
  when the dialog is accepted, so a cancelled Setup no longer destroys redo.
- **A lecturer typed into the class form joins the school's lecturer list**,
  matched case-insensitively so `"ayşe yılmaz"` does not become a second
  teacher beside `"Ayşe Yılmaz"`.
- **The By-Group tab has a colour key**, in the filter row, costing no grid
  height. Above eight years it shows one swatch per *colour*, listing the years
  that share it.
- **Toolbar dropdown buttons look like dropdowns again.**
- **The class form reports every mistake at once.**
- **`summary` gained `cpsat_failure`**, naming why deep mode did not run.

### Known gaps left behind

1. **ST-UI-013 is not implemented**, and its premise is language-dependent —
   see above. Re-measure into the register before working it.
2. **ST-ARCH-005 (god object) is open.** `app.py` is 5 796 → 5 243 lines and
   still MI 0.00. The `_write_excel` extraction removed its worst function
   (CC 57 → the file's worst is now 27).
3. **The `core` SCC is still 15 modules.** Breaking it needs the `logic.py`
   primitives/facade split, which is the one seam the audit got exactly right.
   `MAX_CORE_SCC_SIZE` in `tests/test_import_layering.py` is the ratchet.
4. **19 of `logic.py`'s remaining deferred imports are load-bearing.** Recorded
   for whoever promotes them: `python -c "import scheduler_app.core.logic"` is
   **not** the check — it succeeds for every one of them while
   `import scheduler_app.core.workflow`, the real entry path, still raises.
5. **~80 dead symbols remain** of the 91 measured. This phase deleted the
   family the finding names; the rest is a mechanical follow-up, along with 131
   unused imports.
6. **`check_untyped_defs` is off**, at 168 errors. Turning it on is a project,
   not a config flip; the measurement is in `mypy.ini`.
7. **The rest of ST-UI-019/018 is untouched** — bug-dialog theming, warning-log
   timestamps and de-duplication, empty-state CTAs. All Low, all better done
   together.
8. **CSV is still two writers, deliberately.** `exporter._export_csv` emits the
   timetable; `app.export_csv` emits a class list. Different columns, different
   granularity — two products, not a duplicate. Merging them would silently
   change the file a user has been getting.
9. **The translation backlog is unchanged** at 2508 (locale, key) pairs. This
   phase added no new keys on purpose, reusing `export.appendix_offgrid` and
   `actions.setup` where new ones were tempting.

---

## Phase 5 — mostly complete

**Suite: 671 tests — 647 pass, 24 known-defect pins, 0 failures.** Both lanes
exit 0. (515 pass at the start of the phase; +132 new tests, no pins added and
none removed.)

Five of the roadmap's six rows are done. **ST-UI-013 (the responsive shell) is
deliberately not implemented** — see "The row that was not built" below; its
headline numbers do not survive measurement, and what remains of it needs its
own pass. The form-UX row (ST-UI-016…020) is triaged but only partly built.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-UI-004](12-findings-register.md#st-ui-004) | 🟠 High | The grid has a lane-aware keyboard cursor, a focus ring, and an accessible description. Both halves of a contested cell are reachable; the cursor follows its lesson across a scene rebuild. |
| [ST-UI-005](12-findings-register.md#st-ui-005) | 🟠 High | Every in-cell text colour clears WCAG AA against all 24 cell backgrounds, from one source consumed by the screen, the XLSX and the PDF. |
| [ST-UI-007](12-findings-register.md) | 🟡 Medium | The PDF no longer dies or drops text on a school's own labels; Qt labels carrying user text are `PlainText`. |
| [ST-UI-008](12-findings-register.md) | 🟡 Medium | No exported cell is a formula, and neutralising one does not rename it. |
| [ST-UI-010](12-findings-register.md) | 🟡 Medium | The toast follows its window instead of a fixed screen point. |
| [ST-UI-011](12-findings-register.md) | 🟡 Medium | No raw key reaches a user; a coverage check now guards three separate properties. |
| [ST-ARCH-004](12-findings-register.md) | 🟠 High | **New surface.** The open-slots panel and `_add_class_at` now honour the cell they name. |

Two defects **not in the register** were found while measuring and fixed: the
ÇAKIŞMA pill painting over the pinned badge, and three pairs of same-colour
text in one cell.

### Where the register was not enough

As in Phases 1–4, each was proved by building the naive version and watching it
fail, or by measuring rather than assuming.

1. **ST-UI-005 is 13 failing elements, not 5, and two of them straddle the
   threshold.** The audit computed against "the four common cell backgrounds";
   there are **24** — eight `YEAR_COLORS` at each of the three lighten factors
   the code uses. The register reports the class code as "3.40:1" and the
   lecturer as "3.84:1"; the real quantities are ranges of 3.15–4.56 and
   3.56–5.16, which **cross 4.5**. The same element was compliant or not
   depending on which year a class belonged to, and a fix validated against one
   background would have passed while half the palette still failed.
2. **ST-UI-005's own recommendation ships two new AA failures.** "Render badges
   as filled pills with white text" is 3.30:1 on the current green and 3.19:1 on
   the current amber. It is also geometrically impossible where the badge lives,
   because the bottom-right band is already the conflict pill's.
3. **The right instrument matters more than the right question.** "Are the
   darkened colours still distinguishable?" was first answered with contrast
   ratio, which is luminance-only and scores two different hues of equal
   lightness at 1.00:1 — it produced a confident, wrong "the palette collapses".
   Redone in CIE Lab: darkening costs mean pairwise ΔE76 95.7 → 55.7, hue
   survives, and the register's "darken the text" is right after all.
4. **ST-UI-007 and ST-UI-008 both blame the class name, and the class name is
   the field that is already safe.** In the PDF, the injectable fields are the
   slot label, branch, year, room and lecturer — **10 of 24** (field × mode)
   combinations raised `ValueError` and wrote **no file at all**, while the
   class name was 0 of 4 because Phase 4 escaped it. In the XLSX the same
   asymmetry: a class name goes through `CellRichText` and is never a formula;
   the **slot label** is, in column A of every sheet.
5. **ST-UI-008's own recommendation is a data-corruption bug where it points.**
   Prefixing the value with an apostrophe renamed **5 of 8** round-tripped
   values, including `-9A Matematik`. DERSİS re-imports its own workbooks.
   `quotePrefix` renames 0 of 8.
6. **ST-UI-011 names a key that has never existed.** `labels.protection` is in
   no catalogue and no Python file. And the fallback the code already had was
   dead: `tr(k) or fallback` can never select the fallback, because `tr` returns
   the *key* on a miss and a key is truthy — which is precisely why
   `labels.targets` reached the user.
7. **The i18n gap is 130 keys, not the ~30 the handoff recorded**, and what is
   missing is the error channel: `errors.settings_write_failed`,
   `errors.key_file_damaged`, `conflicts.cell_pair`.
8. **ST-UI-004's inference is backwards.** `TimetableView` already has
   `StrongFocus` and is already in the tab chain. The problem is the opposite —
   the arrows are *already consumed* to scroll, so a naive handler moves the
   cursor **and** scrolls.
9. **The audit's per-cell accessible names cannot be built at any effort.**
   `QGraphicsItem` is not a `QObject`, and PyQt6 exposes **no** `QAccessible`
   bindings anywhere. What shipped is the view describing its cursor cell, and
   the phase record says so rather than claiming the proposal was met.

### The row that was not built

**ST-UI-013 (responsive shell) is not implemented, on purpose.** Measured
natively, its headline numbers are wrong: the sidebar is a flat **350 px** (never
430), tab truncation begins at **W < 1159** (not 1400), the dashboard's inner tabs
**never** collapse to an icon, and the "2.5 day columns" figure comes from the
online filter's sub-column layout rather than from the sidebar. The proposal's
own fix — a 25 % proportional sidebar — buys **zero** extra columns, because Qt
clamps a splitter section to the sidebar's `minimumSizeHint` of 301 px.

Two claims made *against* those measurements were also checked and are also
wrong: the "`_expand_panel` leaves a 0 px splitter handle" defect does not
reproduce on the real window (0 → 5 px, draggable, three cycles — the reading was
taken before the event loop re-laid it out), and "truncation at 1400×860" is an
**offscreen artifact** (1148 px against 947 available offscreen; 789 against
~1030 natively).

What is left of the finding is real — the sidebar does not shrink, and the tab
bar does truncate below ~1159 — but the fix needs a user-intent state machine
for auto-collapse (a plain `resizeEvent` breakpoint re-collapses the sidebar on
the next 1 px nudge after the user opens it), `setMaximumWidth` rather than
`setSizes` as the lever, and re-application of the cap inside `_expand_panel`,
which resets both constraints. None of it can be calibrated from CI. It wants
its own pass rather than a half-build on numbers that do not hold.

### Behaviour changes worth knowing

- **The timetable is keyboard-operable.** Arrows move a cursor, `Alt+←/→` walks
  the lanes of a contested cell, `Space` selects, `Enter`/`F2` edits,
  `Menu`/`Shift+F10` opens the cell's context menu. Arrows deliberately do not
  select: `_select_class_gfx` rebuilds the whole open-slots sidebar.
- **Enter activates; it does not open the context menu**, contrary to the
  audit's proposal, because left-click selects and right-click menus — binding
  the keyboard's primary activation to the secondary mouse action inverts the
  mapping Qt's own item views use.
- **In-cell text is darker**, and the same values now reach the XLSX and the PDF.
  The ARDIŞIK marker is slate rather than violet: it is structural information
  like the branch letter, not a statement about what the scheduler may do.
- **A conflicted cell is ~14 px taller**, reserving the pill's strip so the
  pinned badge survives.
- **The open-slots panel lists online lessons.** It previously told every one of
  them it had nowhere to go.
- **A CSV cell beginning with `=` is prefixed; an XLSX cell is not** — it gets
  Excel's `quotePrefix` attribute instead, so re-import reads the name back
  unchanged.

### Known gaps left behind

1. **ST-UI-013 is not implemented.** See above; the finding needs re-measuring
   into the register before it is worked.
2. **The form-UX row (ST-UI-016…020) is triaged, not built.** Much of it is
   already false: the tutorial does not fire over a modal, the language switch is
   not flag-only, and half of ST-UI-019 was closed by Phase 2. The live items
   are the first-error-only validation in `AddClassDialog`, the suppressed
   toolbar menu caret, and a typed lecturer name that never joins
   `state['lecturers']`.
3. **ST-UI-006's legend is not built.** `get_year_color` wraps at 8, so a school
   with 9+ years — reachable on every tier above Starter, and the norm for a
   Turkish K–12 — paints two different years the same colour. A legend mapping
   swatch → year would be *wrong* there, which is the finding's real content and
   is stronger than "there is no legend".
4. **P4 defers Ctrl+X/Ctrl+V lift-and-place and a cursor for the Show Everything
   matrix**, both with reasons; the matrix emits one block per matching target,
   so a two-target class has no single address.
5. **`cell_at` has 1-px dead bands** and a span-2 lesson's centre lands in one.
   Harmless to the cursor (which never consults it) and latent for drag/drop.
6. **Dialog-context contrast still fails** — `#16A34A` at 3.30:1 and `#D97706`
   at 3.19:1 on white, ~20 sites. Outside ST-UI-005's scope, and several of the
   same literals are background fills, so it needs its own inventory.
7. **The translation backlog is 2508 (locale, key) pairs** and needs a
   translator. Machine translation is not available as a shortcut: 174
   `tr(key).format(...)` sites are unguarded, 26 of them in
   `ConstraintValidator`, and a drifted placeholder raises rather than degrades.
8. **`Claude Code Review` CI no longer fails — but it does not run either.**
   Carried as an unexplained gap since Phase 3; the cause turned out to be
   trivial and worth writing down. `gh secret list` returns **nothing** — the
   repository has no secrets at all — so
   `secrets.CLAUDE_CODE_OAUTH_TOKEN` interpolates to an empty string, the
   action falls back to direct-Anthropic-API mode, and it aborts on credential
   validation after 21 s. It never had the credentials to run.

   Both Claude workflows now gate their steps on the token being present, so
   the job **skips** with a `::notice::` naming the missing secret instead of
   reporting failure. A check that is red for every change, regardless of the
   change, teaches everyone to ignore the checks. Adding the token under
   *Settings → Secrets and variables → Actions* switches the review on with no
   further edit.

   The two checks that actually exercise this repository both pass on Linux /
   Python 3.11 — `Validate` 647 passed / 24 xfailed, `Scheduling invariants`
   16 passed — identical to the local Windows / 3.12 result.

---

## Phase 4 — complete

> **Starting the next session?** → [`HANDOFF-PHASE5.md`](HANDOFF-PHASE5.md)
> has a ready-to-paste prompt, what Phase 4 changed that Phase 5 will touch,
> and the gaps this work deliberately left behind.

**Suite: 539 tests — 515 pass, 24 known-defect pins, 0 failures.** Both lanes
exit 0. (526 at the end of the six feature commits; the adversarial
verification round below added 13 more.) Four pins were **deleted** because the defect they guarded is closed:
all four `ST-FUNC-013` PDF cases went `XPASS(strict)` when the export appendix
landed — the suite doing exactly the job it exists for.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-UI-001](12-findings-register.md#st-ui-001) | 🔴 Critical | Every lesson that occupies a cell now renders in it. Contested runs split into lanes *inside* the column; conflicts are marked from a validator verdict, not from geometry. |
| [ST-UI-002](12-findings-register.md#st-ui-002) | 🟠 High | One `schedule_counts()` feeds the status bar, the dashboard card and `compute_all_metrics`. Pinned is a subset annotation, not a peer segment. |
| [ST-UI-003](12-findings-register.md#st-ui-003) | 🟠 High | The dashboard reads `effective_room` — the currency the rest of the app already used. `room_switching` 0.0 → 0.8 on the audit's own fixture. |
| [ST-UI-021](12-findings-register.md#st-ui-021) | 🟠 High | **New finding.** Duplicate slot labels refused and named; slot edits that move existing lessons are reported and confirmed. |
| [ST-SCHED-011](12-findings-register.md) | 🟡 Medium | "Move conflicting class" suggestions emit for the first time: 0 → 15 of 19 on `small`. |
| [ST-UI-015](12-findings-register.md) | 🟡 Medium | `PlaceClassDialog` explains the 0-options case and disables a button that cannot succeed. |
| [ST-FUNC-013](12-findings-register.md) | 🟢 Low | PDF appendix lists every off-grid placement and every conflict by class code. Four strict pins deleted. |
| [ST-SCHED-014](12-findings-register.md) | 🟢 Low | The global bottleneck sentence reaches the results dialog; `apply_reschedule`'s rejected list reaches the warning log. |

### Where the register was not enough

As in Phases 1–3, each was proved by building the naive version and watching it
fail, or by measuring rather than assuming.

1. **ST-UI-001 is not one renderer bug but five occupancy builders that
   disagree.** Two lessons double-booked in R001 monday 09:00, before the fix:
   screen showed `ZZZ999` (dict overwrite, last wins); PDF classroom/group
   showed `AAA111` (an explicit `continue`, first wins); XLSX filtered sheets
   showed both; the XLSX everything-matrix showed one. **A user who checked on
   screen and then printed got two different, both-incomplete timetables.** The
   register's recommendation names two of the five.
2. **The collision is order-dependent, not "last wins".** A span row against a
   start row either *overdraws* or hides one, purely on `state["classes"]`
   order. The single real collision the `large` preset produces is exactly that
   shape, so a fix handling only "two starts in one cell" does not fix it.
3. **Splitting and labelling are different questions.** Two online lessons
   share an hour legitimately — a naive geometric sweep of `large` reports 14
   collisions, of which **13 are that**. And a real clash can show only one
   block (one group in two rooms puts one lesson on each room's tab). Splitting
   is geometric and per-view; the conflict mark is a validator verdict and
   view-independent.
4. **ST-UI-002's own recommendation — "clamp/assert non-negative" — is the
   worst available fix.** With 4 pins also carrying `placed=True` and 3 lessons
   genuinely unplaced, the old formula gives −1 and the clamp gives **0**, while
   the truth is **3** and those 3 are in the sidebar on the same screen. It
   replaces an impossible number with a confidently wrong one.
   `tests/test_placement_vocabulary.py` is mutation-tested against exactly that
   implementation: **8 of its 10 core tests go red under it.**
5. **ST-UI-002's `-5 yerleşmemiş` evidence is a harness artifact.**
   `stress-test/tests/_ui_boot.py::greedy_place` calls `mark_placed` on pinned
   classes; `git show 365b24b` confirms `apply_reschedule` skipped pins even at
   the audit commit. The formula is wrong regardless — it encodes a
   disjointness nothing enforces — so the counter is structurally total rather
   than trusting the invariant.
6. **ST-UI-003's cause is worse than "reads a missing key".** `cls.get("room","")`
   is passed as `room_override`, and because `""` is not the `_ROOM_UNSET`
   sentinel it *wins* — actively defeating a correct `placed_classroom`
   fallback.
7. **The roadmap's "Structured time-slot entry" is the wrong fix, and cites the
   wrong finding.** `grep` for `strptime` / `%H:%M` / `split(":")` returns
   **zero hits**: nothing parses a slot as a time, so `"1. Ders"` and
   `"Öğle Arası"` are first-class and a `QTimeEdit` per row would hard-code the
   one rule the grid must not have. Uniqueness is the only hard rule. See
   ST-UI-021.
8. **The handoff's known gap #4 is wrong.** `required_classrooms=["R003"]` after
   R003 is deleted *already* produces a correct message with suggestions
   (`InfeasibilityAnalyzer.analyze_class`, category `required_room_missing`) —
   verified against the exact input the handoff names, with `participants=0` to
   remove the capacity confound. Only the wiring was missing. Writing the
   proposed new reachability check would have created a second answer to one
   question.

### A correction to the Phase 2 record

PROGRESS.md's Phase 2 section states `multi_start_time_limit` was "raised 120 s
→ 3600 s". **On the production path that is inert.** Measured by spying on the
real constructor through the live `SchedulingWorkflow.reschedule`:

```
ScheduleOptimizer.__init__ default : 3600.0
optimized_reschedule_all default   : 120.0
what the LIVE reschedule path uses : 120.0
```

`SolverTask` is built with no optimizer kwargs, so every production solve goes
through `optimized_reschedule_all`'s own 120.0 (logic.py:1309, 1338), and
`grep multi_start_time_limit scheduler_app/ui/` returns nothing. The suite
already documented the truth — `tests/test_greedy_bounds.py:129` calls out
`normal` as "still clock-capped at 120 s by `optimized_reschedule_all`'s
`multi_start_time_limit=120.0`". Only the Phase 2 summary is misleading.

**Deliberately not changed here.** Raising it has real runtime consequences
(`test_bounding_does_not_cost_placements` runs ~123 s *because* of this cap),
and the Phase 4 task-6 implementation spec that proposed a global deadline was
calibrated on 3600.0 throughout — its adversarial reviewer returned
*materially-wrong* for that reason, and showed the proposed change would
truncate run 4 on `normal` and cost the reproducibility it was meant to
protect. It needs its own measurement pass. What Phase 4 did instead was make
the UI honest about the consequence: `summary['deterministic']` is now surfaced.

### Behaviour changes worth knowing

- **The status bar reads differently.** `80 classes │ ✅ 78 placed 📌 incl. 4
  pinned │ ⏳ 2 unplaced`. Pinned moved from a peer segment to a subset
  annotation because the three numbers otherwise summed to more than the class
  count. A new `⚠ N not on the timetable` segment appears when a placement
  points at a deleted day or hour — those are `scheduled` but drawn nowhere and
  absent from the unplaced panel, so the count used to exceed what the grid
  showed with no way to find the difference.
- **The reschedule modes are renamed** `Hızlı` / `Kapsamlı` (were `Standart` /
  `Derin (CP-SAT)`), Quick is the default, and Thorough warns that its result
  may not be reproducible — which is true, and was never said.
- **A duplicate time slot is now refused at Setup OK.** A user whose saved file
  already contains one is blocked until they fix it; the live status strip shows
  the problem the moment the dialog opens, so it is diagnosed before they click.
- **`edit_setup` now pushes an undo snapshot** (and pops it if cancelled). It
  previously pushed none, so every unplacement `_reconcile_after_setup`
  performed was irreversible.
- **`negotiate_class` no longer raises on an off-grid placement.** It read a
  stored slot through `logic.slot_index`; with one orphaned lesson, 3 of 4 calls
  died. Skipped blockers are *counted* and reported, because
  `ConstraintValidator.add_placement` returns early on the identical condition.

### The adversarial verification round

The six Phase 4 commits were then attacked by 43 verifier agents, with every
candidate defect independently reproduced or refuted by a second agent that
defaulted to REFUTED. **30 CONFIRMED, 4 PARTLY, 3 REFUTED.** All 34 are fixed
or deferred with a stated reason, across five follow-up commits.

The pass earned its keep several times over. What it found, grouped:

**Live user-visible defects Phase 4 introduced or left**

- The **Online / Lecturer-office tab discarded every conflict mark**. The
  adapter stamped the flags on both render modes; the virtual scene builder
  constructed its `LessonItem`s without passing them. One dropdown click away
  on the default tab. Nothing caught it because **no test in the repository
  built a `TimetableScene`** — every conflict test asserted on the adapter's
  blocks, one layer short of what the user sees.
- **A class name with angle brackets vanished from the PDF.** reportlab reads
  `<Vekil> Dersi` as an unknown tag and drops it. A bare `&` is tolerated,
  which is why the first version of the test pinned nothing.
- **The PDF `everything` matrix still dropped a claimant**, and stacked cells
  **overprinted the rows above and below** (`rowHeights` is fixed; reportlab
  draws over neighbours rather than growing a row).
- **The XLSX everything matrix stacked a class against itself** when it carried
  two identical target dicts — what a user gets typing `"A, B, A"` as branches.
- **The app called the user a liar about their own pin.** `apply_reschedule`
  reports two events through one list; on the project's own dataset generator
  **13 of 13 rejections** are "your pin clashes where you put it", and all were
  reported as errors reading "could not be committed where the planner put it".
- **"Move X (frees N slots)" overstated N.** Blockers were counted per cell, so
  a cell blocked by two lessons credited both — moving either frees nothing.
- **The Setup undo was worse than no undo** — see below.

**Phase 4's own tests that pinned nothing**

`make_app`'s TierEnforcement snapshot named three wrong attributes behind a
`hasattr` guard, so it restored nothing while looking like isolation. Three
other tests were vacuous — including one whose assertion
(`quick.isDefault() or not deep.isDefault()`) is TRUE in exactly the state its
failure message describes. **ST-UI-002's rendering half had no test at all**:
the status bar could be reverted wholesale with the suite still green.

**A withdrawal**

Phase 4 added an undo snapshot to `edit_setup`. `_push_undo` deep-copies
`state["classes"]` and nothing else, while Setup rewrites the axis lists — so
"Undo: setup change" restored placements onto hours the grid no longer has,
resurrecting the ST-DATA-003 orphans from a button labelled as a safety net.
It also cleared the redo stack on cancel. A half-transaction undo is not a
partial fix; it was withdrawn. ST-UI-014's second clause needs full-state
snapshots — ST-ARCH-012, Phase 6.

**A broken measuring tool, worth recording**

Stale `__pycache__` invalidated three consecutive measurements: `inspect.getsource`
reads the file while the running function came from cached bytecode, so a
mutation test reported *GREEN — PINS NOTHING* for a fix that was working. The
conclusion on offer was "drop the fix". The mutation harness now clears the
cache before every run. **A mutation test that cannot see its own mutation is
worse than none: it manufactures confidence.**

A second, subtler masking: one test could not go red because the conflict
appendix — added earlier in the same phase — listed the same names through its
own escaped path and kept the needle alive regardless of what the grid cell
did. A new feature was hiding the defect its own test was written for.

### Known gaps left behind

1. **`multi_start_time_limit` still is not a global bound**, and is 120 s in
   production rather than the 3600 s Phase 2 recorded. See the correction above.
   Needs its own measurement pass.
2. **`targets.index(t)` is unchanged in all three everything-matrix builders.**
   Switching the renderer's copy to `enumerate` is correct in isolation but
   would create a new screen-vs-PDF-vs-XLSX divergence for duplicate-target
   non-joint classes. Fix all three together or none.
3. **A legacy `.egu` carrying a duplicate slot has no in-app repair path.**
   `SetupDialog` is the only writer of `state["slots"]`, so the user must delete
   the line by hand; no "remove duplicates" affordance is offered.
4. **New strings are `en` + `tr` only** (~30 keys across Phases 0–4). Phase 5
   owns the coverage check.
5. **`Claude Code Review` CI still fails**, as it has since before this work.

---

## Phase 3 — complete

> **Starting the next session?** → [`HANDOFF-PHASE4.md`](HANDOFF-PHASE4.md)
> has a ready-to-paste prompt, what Phase 3 built that the UI does not yet
> consume, and the ten known gaps this work deliberately left behind.

**Suite: 456 tests — 428 pass, 28 known-defect pins, 0 failures.** Both lanes
exit 0 (fast 410 pass / 28 pins, slow 18 pass). The 28 pins are exactly the ones Phase 2 left behind; **every pin this
phase created was closed by this phase**, and the four ST-SCHED-001 pins the
handoff named as the scoreboard turned red and had their markers deleted.

**The Critical is closed at the root, not papered over.** `repaired_conflicts`
— the assert-and-repair pass added as a safety net — measures **0 on every
preset**, meaning nothing ever reaches it. The optimizer stopped producing
invalid schedules rather than learning to clean up after itself.

### Completion criteria

| Criterion | Result |
|---|---|
| Oracle: raw optimizer output has zero hard violations on **all** presets | ✅ zero **optimizer-caused** violations on all six (`tiny` → `pathological`); see the qualification below |
| CP-SAT respects availability across duration and all protection levels | ✅ |
| 1200-class instance completes without `RecursionError` | ✅ 853 placed, no error, stock recursion limit |

**The qualification on criterion 1, stated plainly.** On `large`, `very_large`
and `pathological` the oracle still reports hard violations — 9, 10 and 89. Every
one belongs to a **pinned** class: measured `flexible=0` at all three scales. The
preset generator emits mutually infeasible pins (93 pins on `pathological`), and
DERSİS deliberately commits an infeasible pin rather than clearing it, because
the pin is an instruction the user typed (ST-SCHED-002, Phase 1). Those cells are
now *named* in `summary['infeasible_fixed']` instead of being silent. So the
criterion is met for everything the engine is responsible for; what remains is
the input's, and it is reported.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-SCHED-001](12-findings-register.md#st-sched-001) | 🔴 Critical | The optimizer no longer proposes hard-constraint violations. `small` 18 → 0, `normal` 102 → 0, **with the placement count unchanged** (21 and 76). |
| [ST-ARCH-004](12-findings-register.md) | 🟠 High | One validator. Drag-and-drop, the class editor, the placement sweep and the legacy solvers all reach their verdict through `ConstraintValidator`; `screen_placements()` is the single commit rule. |
| [ST-SCHED-005](12-findings-register.md#st-sched-005) | 🟠 High | CP-SAT models lecturer availability across a class's whole duration, not just its start hour. |
| [ST-SCHED-006](12-findings-register.md) | 🟠 High | All four protection levels honoured — including two the register did not know were broken. |
| [ST-SCHED-007](12-findings-register.md#st-sched-007) | 🟡 Medium | The legacy solver family forwards to the optimized path; it no longer places an unavailable lecturer or moves a locked class. |
| [ST-SCHED-009](12-findings-register.md) | 🟡 Medium | `find_conflicts` is guaranteed non-empty whenever `check_placement` rejects. |
| [ST-SCHED-010](12-findings-register.md) | 🟡 Medium | Occupancy cells are ref-counted, so removing one of two classes claiming a cell no longer frees the survivor's claim. |
| [ST-SCHED-012](12-findings-register.md) | 🟡 Medium | Greedy construction is iterative. 1200 classes complete; depth is heap-bound. |
| [ST-PERF-004](12-findings-register.md) | 🟠 High | The greedy phase has a real stopping condition. It converges instead of burning its budget. |
| [ST-PERF-008](12-findings-register.md) | 🟡 Medium | The greedy phase is wall-clock bounded. 125–291 s against a 5 s budget → 6.4 s. |
| [ST-SCHED-014](12-findings-register.md) | 🟢 Low | `summary['infeasibility']` names the global bottleneck with numbers; the negotiator analyses the schedule being proposed. |
| [ST-SCHED-015](12-findings-register.md) | 🟢 Low | The dead `neighbor_impact` term is gone, from all four places it lived. |

### The one that mattered: ST-SCHED-001's actual root cause

The register attributes it to "the optimizer's internal placement bookkeeping".
That is the symptom. The defect is a **single seam**, in
`ScheduleOptimizer._greedy_construct`.

`solve()` recorded its answer as a *snapshot* (`best_solution`) taken at a leaf,
while continuing to mutate `solution` and the occupancy maps. It has two exits:

* **Full success** — every class placed. It returns `True` and each frame
  returns without running its matching `_remove`, so occupancy still describes
  the answer.
* **Anything else** — a partial best, or the iteration budget running out. Every
  frame falls through to `_remove`, the stack unwinds completely, and occupancy
  empties back to the baseline — while `best_solution` still claims a full set of
  placements.

In the second case the caller was handed a solution and a validator that
disagreed about **every cell in it**. Measured on `small`: 20 placements
returned, **0** of them known to the validator. `_lns_improve` then ran its
entire repair loop against a grid it believed was empty and stacked classes on
top of each other — exactly the 18 room/lecturer/group double-books the oracle
reported. `apply_reschedule` hid the damage by dropping the losers.

This also explains the shape of the bug that nothing had explained before: **why
`tiny` was always clean.** Five classes, 5/5 placed, so the greedy takes its
full-success exit and the desync never happens. The finding's own evidence
("reproduces at `multi_start_runs=1`, 6 distinct-class collision cells") is the
same seam seen from the other end.

The fix is a reconciliation loop of eight lines. Everything else in this phase is
consequence or defence.

### Where the register was not enough

As in Phases 1 and 2, each of these was proved by building the naive version and
watching it fail — or by measuring rather than assuming.

1. **"Add an assertion/repair pass" would have shipped the bug.** A repair pass
   that drops colliding classes produces the *same committed timetable*
   `apply_reschedule` already produced — clean, and short by however many classes
   collided. It converts a silent drop in one place into a silent drop in
   another. The repair pass is here, but as a tripwire: it measures 0 on every
   preset, and a non-zero `summary['repaired_conflicts']` is now defined as an
   engine defect rather than a normal outcome.

2. **ST-SCHED-015's "dead code — always returns 0.0" is right about the value and
   wrong about the consequence.** `_neighbor_impact` did measure 0.0 on all 3307
   calls across `small` and `normal`. But `neighbor_impact_penalty` is also half
   of the user-facing **"minimal disruption" slider**
   (`optimization_goals._GOAL_WEIGHT_MAP`), so this was not dead code — it was a
   *slider running at half strength*. And deleting the key from `DEFAULT_WEIGHTS`
   alone raises `KeyError` on every reschedule with custom goals, because
   `goals_to_weights` accumulates into `{k: 0.0 for k in DEFAULT_WEIGHTS}` with
   no membership guard. Four sites, one commit.

3. **ST-SCHED-006 is worse than "only LOCKED is respected", in two ways the
   register does not mention.**
   * `same_day` was ignored by **greedy construction** too, not just by CP-SAT.
     `RepairStrategy` filtered candidates by the original day; `_greedy_construct`
     did not. So the protection held or not depending on whether LNS happened to
     destroy and repair that class — a coin flip presented as a guarantee.
   * `improve_only` was **broken in both engines**. The gate is
     `candidate_score <= baseline`, but the candidates were scored with
     `PlacementScorer` and the baseline computed with
     `TimetableScorer.placement_score` — different functions on different scales
     (measured: −0.20…0.60 against −3.67…8.34). For **4 of 10** measured classes
     the gate kept **zero** candidates, *including the class's own current
     placement* — so the protection that promises "never worse" could force the
     class to be unplaced entirely. Both sides now use the same scorer, which
     makes "stay where you are" always admissible.

4. **A wall-clock bound sampled every N nodes is not a wall-clock bound.** The
   first version checked the deadline every 512 search nodes, on the reasoning
   that `time.time()` is measurable at 100 000 nodes. It moved a 5 s budget from
   125–291 s to 65–168 s — because the quantity that needs bounding is *seconds
   between two looks at the clock*, and one node calls `generate()` over
   days × slots × rooms and then scores every candidate against the look-ahead
   window. The interval in nodes was bounded; the interval in seconds was not.
   Checking every node costs tens of nanoseconds against a node costing
   microseconds to milliseconds.

5. **"Bound the greedy phase" (ST-PERF-004) is not about the number.** Cutting
   `max_iterations` bounds the time and silently costs placements — the classic
   trap. Measurement first: placements are **identical at every budget from 100
   to 100 000** (`small` 21, `normal` 76, `large` 231 from 500 up), while the
   full pipeline costs 257 s against 175 s on `normal` and 43.8 s against 10.3 s
   on `small`. The budget was buying nothing at all. So the fix is not a smaller
   number but a *stopping condition*: the search ends when it stops improving its
   incumbent, which is a reason rather than a timeout.

6. **Making `find_conflicts` total is the opposite of the usual trap.** Phase 1's
   lesson is that guarding a reader turns a crash into a silent drop. Here the
   silent case came first — `check_placement` said no and `find_conflicts`
   returned `[]`, so the UI refused a drop with nothing to say. Both the
   availability-across-the-block gap and a backstop for any future rule now
   guarantee a non-empty list.

### Behaviour changes worth knowing

- **`apply_reschedule` returns dicts, not names.** Each entry carries `name`,
  `class_uid`, `reason` and `reasons`. `ui/app.py` still discards the value —
  wiring it into the results dialog is Phase 4's "Why unplaced?" panel — but the
  data it needs now exists. A `str` subclass was tried first, to keep every
  existing caller working untouched; it was rejected because a consumer cannot
  tell a rich entry from a bare name without introspection, which is exactly the
  ambiguity the finding is about.
- **The negotiation report describes the proposed schedule, not the pre-solve
  one.** This is the ST-SCHED-014 fix and it changes what the negotiation tab
  says. Two Phase 2 tests pinned the old baseline and were updated; see below.
- **`improve_only` under CP-SAT is frozen in place, deliberately.** CP-SAT scores
  in a different currency from `PlacementScorer`, so "only move somewhere at
  least as good" cannot be stated in that model. Not moving always satisfies the
  promise, so CP-SAT declines to improve such a class rather than risk making it
  worse; the heuristic phase still optimizes it properly, and now correctly
  (point 3 above). The alternative — leaving it free to move, as it was — breaks
  the promise outright.
- **The legacy solver family is now a set of forwarding shims.** ~325 lines of
  divergent constraint logic became unreachable. `_solve_backtrack`,
  `_get_valid_slots` and `_check_placement_fast` are dead; deleting them is
  ST-ARCH-011's job in Phase 6.
- **`find_drop_classroom` returns `None` for a lesson that needs no room**, which
  is `get_room_candidates`' sentinel rather than a failure. `ui/app.py` was
  taught the difference in both places it checked. Before this, a drag committed
  a *physical classroom* onto an online lesson while `apply_reschedule` stored
  `None` for the same lesson — so the same lesson showed a room or not depending
  on how it was placed, and exports disagreed with the timetable.
- **`summary` gained four keys**: `repaired_conflicts`, `repaired_classes`,
  `infeasible_fixed`, `infeasibility`.

### Tests changed rather than added

Five test files were written by agents that never touched `scheduler_app/**`, so
the fail-before/pass-after guarantee holds for everything they pin. Five tests
needed the implementer to change them, and each is a case where landing the fix
made the *test* wrong rather than the code:

1. `test_auto_place_class_never_displaces_a_locked_class` was **unsatisfiable**
   once locked classes stopped being movable: it asserted both "the newcomer was
   placed" and "the locked class did not move", on a board where the only legal
   cell for the newcomer was the locked one. Rebuilt with a *displaceable* class
   in the way, so the displacement pass provably runs and the locked lesson
   provably survives it. (Both the adversarial verifier and the implementer
   reached this independently.)
2. `test_neighbor_impact_loop_body_never_executes` monkeypatched the method the
   fix deletes, so it hard-errored with `AttributeError` the moment the deletion
   landed. Replaced by `test_neighbor_impact_term_stays_deleted`, which pins that
   it stays gone; the pre-deletion measurement is recorded in its docstring.
3. `test_neighbor_impact_penalty_weight_changes_no_score` became **silently
   vacuous** after the deletion: `PlacementScorer.__init__` merges an unknown
   weight key in as an orphan, so swinging it across nine orders of magnitude
   changed nothing by construction. Deleted; `test_scoring_digest_is_unchanged`
   is the durable tripwire and its golden is unchanged across the deletion.
4. `_drop_verdict`, the drag-and-drop harness, states that it mirrors
   `ui/app.py::_execute_drop` "phase for phase" — and hard-coded the pre-fix
   `if room is None: reject`. Updated to mirror the fixed code, which is what its
   own contract requires.
5. **Two Phase 2 tests** (`test_negotiation_result_still_says_what_it_used_to`,
   `test_negotiation_result_survives_apply_unchanged`) compared the negotiation
   report against a pass over the **pre-solve** state — the baseline
   ST-SCHED-014 deliberately moves. Their ST-PERF-007 property is untouched and
   both still assert it; the second now perturbs the live state explicitly to
   test the pinning directly, because "committing changed the answer" is no
   longer a source of contrast now that the snapshot describes the proposal.

One companion test was **added** by the implementer:
`test_a_harmless_edit_leaves_the_lesson_where_it_was`. Without it, the five
`test_editing_a_class_does_not_leave_it_on_a_now_illegal_cell` cases are all
satisfied by an `apply_class_edit` that unplaces the lesson on *every* edit —
their own escape hatch accepts the unplaced branch as a pass. The pair would
have permanently certified a bulk-unplace as correct.

### Two latent bugs found in passing

- **`schedule_optimizer.py` never imported `tr`**, so the `generator is None`
  branch of the unplaced-reason fallback raised `NameError` instead of reporting.
- **`check_placement_explained` corrupted occupancy for an excluded class.** It
  lifts the class's own placement out of the maps and restores it in a `finally`
  — but for a class in `exclude_ids` the lift finds nothing to release while the
  restore really claims the cell, permanently marking a free cell occupied for
  every later check. Reachable now that `screen_placements` excludes every class
  it is about to test.

### What the adversarial verification caught after the code had landed

The five verifier agents ran against a tree that kept moving under them. Four of
their findings were things the implementer had already fixed independently
(including the two fatal ones: an unsatisfiable locked-class test and a test that
`AttributeError`s the moment the deletion it documents lands). Four more were
real and are fixed in a follow-up commit:

1. **The deadline bounded the search but not the return.** When a stop fired,
   `enter()` returned False and the driver then popped one frame at a time while
   `advance()` re-applied and re-removed every untried candidate of every frame
   still on the stack — genuine occupancy work, O(depth x candidates) of it, done
   past the deadline, counted by nothing and consulting no clock. It now unwinds
   directly. (The same shape existed in the original recursive code, so this is
   not a regression — but a clock bound that keeps working after it fires is not
   a clock bound.)
2. **A stop before the first leaf threw the whole partial descent away.**
   `best_solution` is only written at a leaf, so a run capped early returned
   `[None] * n` and the resync then dutifully stripped every placement the search
   had already made. The stop now offers the current `solution` to the incumbent
   first — mid-descent it is a complete, internally consistent partial answer.
3. **Only one of the three `_greedy_construct` call sites had a deadline.** The
   other two are `optimized_auto_place` and `optimized_batch_schedule` — the
   "add a class" and "place batch" buttons — so ST-PERF-008's user-visible
   symptom survived on exactly the interactive paths, where there is no progress
   dialog to explain the wait.
4. **The one test carrying ST-PERF-008 was vacuous.** It passed against a greedy
   phase that returns `[None] * n` and sets `_clock_capped` (measured: 1.35 s,
   PASSED) — its two stated anti-vacuity guards checked the flag, not the search.
   It now asserts the search visited nodes and placed something beyond the
   instance's 24 pinned classes; mutation-tested against that exact stub.

A fifth was a stale scoreboard: `test_bounding_does_not_cost_placements` kept
floors set from pre-fix measurements on the expectation that the fix would move
`raw_placed` DOWN. It did not — it moved `raw_clean` and `committed` UP to meet
`raw_placed`, which did not move — so `normal`'s clean floor of 39 against an
actual 76 tolerated a 49 % regression in proposal cleanliness. Re-based to 72/72
and 20/20.

The verification also found the occupancy module's headline invariant was
**count-blind** (`set(cell)` discards the refcount, and a doubly-claimed cell
still refuses `check_placement`), so the plausible wrong fix — re-adding
`best_solution` without releasing the stale claim, which is idempotent on sets
and permanent on ref-counted cells — would have passed all ten of its tests and
the whole invariants spine. `test_greedy_holds_exactly_one_claim_per_placement_it_returns`
closes that, and is mutation-proven to be the only test in the module that does.

### Known gaps left behind

1. **`changes[]` still omits protected classes** (`schedule_optimizer.py`, the
   `cls_key(cls) in effective_protected_ids` skip). It no longer *matters*,
   because protected classes no longer move — the defect is closed by
   construction rather than by fixing the builder. If a future change lets them
   move again, the move will be invisible to the impact panel. Undo and rollback
   are snapshot-based and were never affected, contrary to the finding text.
2. **`multi_start_time_limit` is not a global bound.** It is applied per phase —
   the greedy deadline is `global_start + limit` but LNS restarts its own clock —
   so a full solve can take roughly twice the number the user was shown. Bounding
   it globally is a Phase 4 UX decision, not a correctness one.
3. **The presets carry no `protection` levels and no pre-placed classes**, which
   is why the `improve_only` currency bug survived three phases of oracle runs.
   `dataset_gen` should grow a protection-bearing preset; that belongs with
   Phase 7's testing work.
4. **`test_drop_accounting_closes_on_a_real_solve` asserts `0 == 0`** now that
   nothing is dropped, and it is among the more expensive setups in the fast
   lane. It is a legitimate future regression guard, but it is paying for
   coverage it no longer provides.
5. **New user-facing strings are `en` + `tr` only** (4 keys this phase, ~19
   across Phases 0–3). The other 20 locales fall back to English via `tr()` —
   never to a raw key — but need a translator. Phase 5 owns the coverage check.
6. **The re-entrancy guard is still only half-covered** (carried from Phase 2).
   `SolverTask.start()` is idempotent and pinned; that `SchedulerApp` disables
   Generate / undo / import while a solve runs is still not, because pinning it
   means driving the real window through a complete solve.
7. **`Claude Code Review` CI fails** on every PR and did so before this work
   started. Unrelated; needs whoever owns that workflow's configuration.

---

## Phase 2 — complete

> [`HANDOFF-PHASE3.md`](HANDOFF-PHASE3.md) was the prompt for the phase above;
> it is kept as a record. Phase 3's own gaps are listed in its section.


**Suite: 339 tests — 307 pass, 32 known-defect pins, 0 failures.** The non-slow
lane was run three times to confirm stability (299 pass / 28 pins each time).

**All six Criticals from the audit are now closed.** ST-PERF-001 was the last.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-PERF-001](12-findings-register.md#st-perf-001) | 🔴 Critical | The solve runs on a worker thread with real progress and a working Cancel. |
| [ST-PERF-002](12-findings-register.md#st-perf-002) | 🟠 High | Autosave is coalesced behind a 1.5 s debounce and skipped entirely when a hash of the payload matches disk. |
| [ST-PERF-003](12-findings-register.md#st-perf-003) | 🟠 High | The warning log separates sticky history from derived findings; derived ones are replaced, not appended. |
| [ST-PERF-005](12-findings-register.md#st-perf-005) | 🟡 Medium | New EGL1 append-only log format; one append is O(1) in bytes written *and* read. Learning is incremental. |
| [ST-PERF-006](12-findings-register.md) | 🟡 Medium | The open-slots panel is skipped when nothing it displays has changed. |
| [ST-PERF-007](12-findings-register.md) | 🟡 Medium | The negotiation pass is lazy, memoised, and pinned to the reschedule-time state. |
| [ST-UI-009](12-findings-register.md) | 🟡 Medium | Re-selecting what is already selected does no work. |

### Where the plans were not enough

As in Phase 1, each of these was **proved by building the wrong version and
watching it fail**, not argued:

1. **A lazy negotiation property saves nothing on its own.** `BulkResultsDialog`
   is constructed after *every* reschedule and built its negotiation tab inside
   `__init__`, so the first read happened immediately and always. Deferring the
   computation moved ~727 ms (250 classes) a few lines later inside the same
   frozen stretch. The tab is now a placeholder populated on first selection.
2. **Cheap autosave fingerprints all pass the tests and all lose data.** Class
   names, the class count, and `state["classes"]` alone each passed the whole
   module — and each silently drops real edits: a drag mutates one class dict in
   place (same count, same names), and a Setup room change touches
   `state["classrooms"]` and nothing else. The fingerprint hashes the whole
   payload.
3. **A grid-shape-only fingerprint freezes the open-slots panel.** Days, slots
   and classrooms are stable for an entire editing session, so the panel would be
   built once and then show occupied slots as free. Occupancy and the selection
   are both in the fingerprint.
4. **Counting log records must not read them.** The incremental learner's
   early-return still read the entire log to find out how many entries there
   were, so a no-op pass cost 1.6 MB on an 800-entry log. Counting now seeks over
   the framing, and a size check gates the pass before even that.

### A crash this surfaced

The full suite began segfaulting in an unrelated module's teardown, inside a
lambda in `app.py`. The deferred settings modal was connected to its timer as
`lambda: QMessageBox.warning(self, ...)`. **PyQt disconnects a bound-method slot
when its QObject is destroyed; a lambda capturing `self` is just a callable,
stays connected, and fires into a half-destroyed window** — an access violation,
not an exception. It had been latent since Phase 1 and only became reachable
once the off-thread solve started pumping the event loop hard.

The plan's proposed fix for it, `QTimer.singleShot(0, self, lambda: ...)`, **does
not compile under PyQt6** — the context-object overload is not exposed. A real
`QTimer` parented to the window is the equivalent that works.

### Behaviour changes worth knowing

- `refresh_grid` used to normalize `state_data` synchronously as a side effect of
  autosaving. That now happens up to 1.5 s later, or on close. Every load path
  still normalizes, so the exposure is an in-session mutation read by something
  else inside the debounce window.
- **⚠ Corrected in Phase 4 — this change is inert on the production path.** The
  raise moved `ScheduleOptimizer`'s own default; every production solve goes
  through `optimized_reschedule_all`, whose signature default is still 120.0 and
  which passes it explicitly. Measured live: the reschedule path uses **120.0**.
  See the Phase 4 section, "A correction to the Phase 2 record".
- `multi_start_time_limit` raised 120 s → 3600 s. That cap existed to bound a
  freeze the user could not escape; now that the solve is cancellable, truncating
  the search is the wrong trade, and a cap that fires costs reproducibility
  outright.
- The feedback log is written in a new EGL1 format. Logs written by older builds
  are converted once, on the next append, and still load either way.

### Known gap, deliberately left

**The re-entrancy guard is only half-covered.** `SolverTask.start()` is
idempotent and that *is* pinned; that `SchedulerApp` disables Generate / undo /
import while a solve runs is **not**, because pinning it means driving the real
window through a complete solve. Two solves sharing one state dict and one
`apply_reschedule` is the most plausible way this change could corrupt a
timetable. Verified by reading, not by test — it deserves hardening.

---

## Phase 1 — complete

**Suite: 261 tests — 229 pass, 32 known-defect pins, 0 failures.** Five of Phase
0's `xfail(strict=True)` pins flipped to passing and their markers were deleted:
ST-DATA-001 (×2), ST-SCHED-002, ST-FUNC-013 (×2). That is the pins doing exactly
the job they exist for.

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-SCHED-013](12-findings-register.md#st-sched-013) | 🟡 Medium | The optimizer is reproducible. Every random draw in the package now comes from one seeded stream, and the LNS phase is bounded by **iteration count instead of the wall clock**. See "the register was not enough" below. |
| [ST-SCHED-003](12-findings-register.md#st-sched-003) | 🟠 High | `filter_class_days` / `filter_class_times` intersect the class's allow-list with the actual grid, so a class allowed only on Saturday is no longer placed on Saturday on a Mon–Fri timetable. |
| [ST-SCHED-004](12-findings-register.md#st-sched-004) | 🟠 High | New total `find_slot_index()`; `slot_index` deliberately still raises. A stale `allowed_times` no longer aborts the reschedule with `ValueError: '20:00' is not in list`. |
| [ST-SCHED-002](12-findings-register.md#st-sched-002) | 🟠 High | `apply_reschedule` validates pins instead of skipping them. An infeasible pin is reported through the rejected list; the pin itself is left alone, because silently clearing it would destroy the instruction the user deliberately typed. |
| [ST-DATA-001](12-findings-register.md#st-data-001) | 🟠 High | `_load_or_create_key` distinguishes an **absent** key file (first run — mint one) from a **damaged** one (raise, leave the bytes untouched). Previously a 10-byte truncation silently minted a new key and orphaned every save the user had ever made. |
| [ST-DATA-003](12-findings-register.md#st-data-003) | 🟠 High | Every stored-placement reader is total: occupancy, conflict detection, analytics, the scorer, `validate_drop`. |
| [ST-DATA-004](12-findings-register.md#st-data-004) | 🟠 High | New core-layer `SchedulingWorkflow.reconcile_placements()`, called from both `SetupDialog` sites and from Excel import, before the repaint. |
| [ST-DATA-014](12-findings-register.md) | 🟢 Low | A corrupt settings container is quarantined to `backups/`, never rebuilt-from-`{}`-then-overwritten. |
| [ST-DATA-005](12-findings-register.md) | 🟡 Medium | `_auto_save` no longer swallows everything: it reports, returns a bool, and never writes a container it failed to read. |
| [ST-DATA-011](12-findings-register.md) | 🟡 Medium | `schedule_new_classes` is all-or-nothing; the four mutate-compute-restore sites got `try/finally`. |
| [ST-DATA-012](12-findings-register.md) | 🟢 Low | New `scheduler_app/single_instance.py`, acquired before the language gate. |
| [ST-FUNC-013](12-findings-register.md) | 🟢 Low | Exports warn about off-grid placements instead of vanishing them; the CSV writes them. |

### Where the register was not enough

Three places where following the recommendation literally would have shipped a
half-fix. Each was **proved by building the half-fix and watching it fail**, not
argued:

1. **ST-SCHED-013 — a seed is necessary but not sufficient.** A tree carrying
   only the seed change still ran 25 LNS iterations on a simulated fast machine
   and 14 on a slow one, from the same seed and the same instance, landing on
   different placements and different scores. The search was bounded by the wall
   clock, so *machine speed was an input to the answer*. The register's Effort
   "M" covers only the seed half.
2. **ST-SCHED-003 — "drop stale constraint values during normalization" is
   wrong.** An **empty** `allowed_days` means "no restriction", so emptying a
   now-impossible allow-list would silently turn "only Saturday" into "any day"
   and place the lesson on Monday looking like a success. Intersection, not
   dropping; an empty intersection leaves the class unplaced with a reason.
3. **ST-DATA-014 — "back up + warn" on *any* read failure is data loss dressed
   up as recovery.** A prototype that quarantined on every exception destroyed a
   perfectly good settings file on a transient `OSError`. Only `EguFileError`
   means "genuinely unreadable"; everything else propagates.

A fourth, smaller one: making the readers total turns a crash into a **silent
drop**, which is worse — the printout looks complete. Hence
`models.find_off_grid_placements()` and the export warnings.

### Deliberate scope calls

- **`slot_index` still raises.** Around forty call sites do `idx + duration`
  arithmetic; returning `None` would trade a loud `ValueError` for an obscure
  `TypeError`, and returning `-1` would be worse still — `-1` is a valid Python
  index, so lessons would land in the last hour of the day. Stored-data readers
  use `find_slot_index` instead.
- **An infeasible pin is reported, not cleared.** The pin is what the user
  typed.
- **`find_off_grid_placements` is not called from `normalize_state_classes`,**
  so it never runs on the `.egu` load path. Unplacing orphans at load would
  discard the user's own placements with no way to see or undo it — the same
  class of bug in a new place.
- **CP-SAT keeps a wall-clock budget.** It gets `random_seed`, but
  `summary['deterministic']` is False whenever it ran, so the app never claims a
  reproducibility it cannot deliver. A deterministic CP-SAT budget needs
  per-scale calibration; Phase 3, with ST-PERF-009.

### Follow-ups this opened

- **The 120 s `multi_start_time_limit` default needs revisiting.** 80 classes now
  reproduce exactly, but the department-scale run was measured at 77 s once and
  105 s on a busier machine — against a 120 s cap. The margin is thin and it is
  contention-sensitive, so a slower or loaded machine hits the emergency cap and
  loses reproducibility (correctly reported as such, but lost). Interacts with [ST-PERF-001](12-findings-register.md#st-perf-001) —
  Phase 2 wants the solve off the UI thread anyway.
- **Constraint lists on the non-day axes are still never pruned.** A class with
  `required_classrooms=["R003"]` after R003 is deleted has zero candidate rooms
  and is permanently unplaceable, with no message anywhere. `reconcile_placements`
  clears *placements*, not constraints, and pruning changes semantics (an empty
  allow-list means "no restriction"), so this needs its own finding and its own
  decision rather than a silent fix here.
- **Reconciling on file-open and on undo is deliberately NOT wired.** `open_file`
  sets `state["lecturers"] = []` for files predating the lecturers feature, so an
  unconditional reconcile-on-open would unplace that user's entire schedule,
  silently.
- **New strings are `en` + `tr` only** (12 keys across Phases 0–1). The other 20
  locales fall back to English via `tr()` — never to a raw key — but need a
  translator. Phase 5 owns the coverage check.

---

## Phase 0 — complete

### Findings closed

| ID | Sev | What changed |
|---|---|---|
| [ST-FUNC-001](12-findings-register.md#st-func-001) | 🔴 Critical | `_import_from_excel` called `_on_state_changed()` / `refresh()`, neither of which exists in the MRO, so every *successful* import crashed **after** mutating state. Now calls `refresh_grid()` / `_update_status()`, and the whole merge is a transaction: any failure — including in the repaint — restores the pre-import state and reports the error. |
| [ST-FUNC-002](12-findings-register.md#st-func-002) | 🔴 Critical | Blank cells arrive from pandas as `NaN`, and `str(NaN)` is the truthy string `'nan'`, so every blank-`joint_class_group` class shared one joint key and all but the first were deleted. All cell reads now go through `_is_blank` / `_cell_text`. The app's own template round-trips **5 rows → 4 classes** (was 2). |
| [ST-FUNC-003](12-findings-register.md#st-func-003) | 🟠 High | One malformed number aborted the entire import with an uncaught `ValueError`. Numeric cells now parse per row: blank takes the documented default, unreadable text in the required `duration` skips that row with an error, unreadable text in the optional `student_count` degrades to 0 with a warning. Room `capacity` got the same treatment. |
| [ST-ARCH-002](12-findings-register.md) | 🟠 High | CI triggered on `master`, a branch this repo has never had, so it had never run. Now `main` + `workflow_dispatch`. |
| [ST-ARCH-001](12-findings-register.md#st-arch-001) | 🔴 Critical | **Partially.** 0 tests → 138 (132 of them in the fast CI job). Depth is Phase 7. |

### The safety net

`pytest.ini` + `tests/` — **138 tests: 101 pass, 37 known-defect pins, 0 failures.**

| Module | Covers |
|---|---|
| `test_scheduler_invariants.py` | the audit's independent hard-constraint oracle vs. the production optimizer |
| `test_storage_roundtrip.py` | encrypted round-trip, 7 corruption modes, key/​container damage |
| `test_import_roundtrip.py` | Excel import at library level, template round-trip |
| `test_export_smoke.py` | xlsx / csv / pdf smoke ×4 modes, parsed back through real parsers |
| `test_import_ui_flow.py` | the real `SchedulerApp` driven headlessly through an import |
| `test_smoke_environment.py` | the harness itself — proves HOME is sandboxed |

CI runs `pytest -m "not slow"` in the **Validate** job and the full oracle
(including the slow presets) in a separate **Scheduling invariants** job.

Conventions, fixtures and the one rule you cannot break are in
[`tests/README.md`](../tests/README.md).

### 37 known-defect pins

Defects scheduled for later phases are pinned with
`@pytest.mark.xfail(strict=True, …)`, so **the suite goes red the moment a fix
lands** — that is the signal to delete the marker. They cover ST-SCHED-001/002,
ST-DATA-001/002/013, ST-FUNC-004/005/006/007/009/010/011/012/013.

Two pins are deliberately **non-strict**: the `normal`-preset ST-SCHED-001 pins.
The optimizer is non-deterministic (ST-SCHED-013) *and* wall-clock-bound, so it
cannot be made reproducible by seeding alone; 1 of 13 measured 80-class runs came
out clean by luck, which would XPASS a strict marker and redden the build at
random. The `small` pins aggregate three independent trials and stay strict
(~0.2 % false-XPASS).

### What this implies for Phase 1

**Do [ST-SCHED-013](12-findings-register.md#st-sched-013) (seed the RNG) early.**
The roadmap ranks it 3/3/3/S, but it is what makes the ST-SCHED-001 pins
deterministic — and therefore what makes the Phase 3 engine work verifiable
rather than statistical. It is a prerequisite, not a nice-to-have.

### Known gaps in this phase

- **CI is green on Ubuntu.** Both jobs pass on PR #7: **Validate** (1 m 01 s,
  including `pytest -m "not slow"` under `QT_QPA_PLATFORM=offscreen` with the apt
  Qt libraries) and **Scheduling invariants** (4 m 10 s, the full oracle
  including the slow presets). ST-ARCH-002 is therefore verified end to end, and
  Phase 0's "CI runs and is green" completion criterion is met.

  *Correction to an earlier entry here.* This file previously recorded that
  "GitHub Actions is not executing anything in this repository", diagnosed from
  PR #7 showing no runs — not even `Claude Code Review`, which subscribes to
  `pull_request` with no branch filter — and from the last run of any kind being
  68 days earlier. That conclusion was **wrong**. The runs were simply late:
  they were created about fifteen minutes after the PR, well after the checks
  that concluded otherwise, and nothing needed to be re-enabled. The lesson is
  narrow and worth keeping: an empty `GET /actions/runs` shortly after a push is
  evidence of queueing, not of a disabled repository, and the two look identical
  for as long as the queue lasts.

  Still true, and still only a symptom: `ci.yml` and `claude.yml` are absent from
  `GET /actions/workflows` until their first run, because that index lists only
  workflows that have executed at least once. `ci.yml` never could — it triggered
  on `master`, a branch this repo has never had.

- **`Claude Code Review` fails**, on this branch and on PR #6 back in June. It is
  unrelated to the roadmap and predates this work; it needs whoever owns that
  workflow's configuration.
- The three new user-facing strings (`errors.invalid_number`,
  `warnings.blank_number_defaulted`, `warnings.invalid_number_defaulted`) exist
  in **en** and **tr** only. The other 20 locales fall back to English via
  `tr()` — never to a raw key, so [ST-UI-011](12-findings-register.md) is not
  reopened — but they need a translator. Phase 5 owns the coverage check.
- The import still pushes nothing onto the undo stack, so a user cannot reverse
  a bad import. Out of scope here: the undo model only covers `state['classes']`
  while an import also replaces lecturers, rooms and years, so a partial undo
  would desync the state. That is [ST-ARCH-012](12-findings-register.md) /
  full-state snapshots, Phase 6.
