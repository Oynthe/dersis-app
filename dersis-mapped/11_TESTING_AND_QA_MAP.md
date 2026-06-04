# 11 — Testing and QA Map

## 1. Current state — no automated test suite

The offline conversion **removed all automated test files**. There is no `pytest` suite in the repository anymore:

- `test_release_audit.py` (root) — removed.
- `test_workflow.py` (root) — removed.
- `tests/` package, including `tests/test_updater.py` — removed.
- `archive_repo_cleanup/` (archived legacy tests) — removed.

`requirements-dev.txt` still lists `pytest`, but it is a convenience dependency only; nothing in the repo is collected by it.

Verification today rests on two pillars: **CI structural checks** and **manual QA**.

## 2. CI checks — `.github/workflows/ci.yml`

CI runs on every push/PR to `master`. It does **not** run tests; it performs fast static/structural validation:

| Check | What it verifies |
|-------|------------------|
| Dependency install | `pip install -r requirements.txt` + `pip check` succeed (no broken/conflicting deps). |
| VERSION semver | `VERSION` exists and matches `^[0-9]+\.[0-9]+\.[0-9]+$`. |
| Runtime version parity | `scheduler_app._version.__version__` equals the `VERSION` file. |
| Tag parity (tag pushes) | On a `v*` tag, the tag equals `VERSION`. |
| Build-file presence | `build_embed.bat`, `installer.iss`, `verify_deps.py`, `scheduler_gui.py` all exist. |
| Import smoke test | `import`s `scheduler_app._version`, `core.models`, `core.workflow`, `plans`, `storage`; fails if any raise. |
| Installer references | `installer.iss` points at `build\Dersis.dist` and uses the `Dersis_Setup_v{#AppVersion}` output name. |

No Qt display is needed — CI never launches `QApplication`.

## 3. Validation scripts (not a test framework)

- `verify_deps.py` — production dependency import-check (returns 0/1). Used by `build_nuitka.bat` and as a manual pre-build gate. It checks direct **and** transitive deps so Nuitka can statically discover everything.

## 4. What is NOT automatically verified

Essentially all behaviour is now verified manually. The highest-value areas to exercise by hand:

| Area | Why it matters |
|------|----------------|
| Scheduler correctness (`core/constraint_validator.py`, `core/schedule_optimizer.py`) | Hard-constraint violations must always be rejected; reschedule must not leave feasible classes unplaced. Heuristic + CP-SAT output is non-deterministic. |
| `SchedulingWorkflow` API (`core/workflow.py`) | Auto-place / schedule-new / batch / reschedule / drop-validation / edit / snapshot-restore are the engine the UI drives. |
| Persistence round-trip (`storage/storage.py`) | `.egu` save/load, checksum-failure detection, legacy-Fernet auto-upgrade. A regression here could lose user data. |
| Import/export (`data_io/*`) | Excel import → export round-trip; PDF/CSV/Excel output. |
| Translation key drift (`ui/translations.py`) | Adding a key in code without adding it to all 22 languages silently falls back to English; placeholder mismatches show literal `{name}`. No parity test exists. |
| PyQt UI rendering / interaction | No Qt-driver tests; covered by the manual release checklist. |
| `preference_learner.py` weight updates | A bad gradient direction would silently drift the learned weights. |

## 5. High-risk untested areas (most important)

1. **`schedule_optimizer.py` LNS loop + multi-start** — pure heuristic; correctness is hard to assert beyond "produces a placement that satisfies hard constraints".
2. **`constraint_negotiator.py` relaxation suggestions** — large surface, no checks.
3. **`preference_learner.py`** — silent weight drift risk.
4. **Translation key parity across 22 languages** — silent English fallback.
5. **`storage.py` legacy Fernet fallback** — touched by old installations; a regression could lose data.

## 6. Suggested future tests (recommendations, not statements of fact)

If a test suite is reintroduced, prioritise:

| Recommendation | Reason |
|----------------|--------|
| Round-trip `.egu` save/load + flipped-bit corruption tests | Verify checksum detection always fires; protect user data. |
| `SchedulingWorkflow` method tests (auto-place, batch, reschedule, drop-validation, edit, snapshot/restore) | These are the core engine entry points. |
| `ConstraintValidator.check_placement` property-based tests | Surface corner cases (zero-duration, empty `state["slots"]`, etc.). |
| Translation key parity test across all 22 languages | Detect missing/extra keys and mismatched format placeholders. |
| Snapshot test on `ScheduleAnalytics.analyze` output | The dashboard depends on its structure. |
| Invariant test of `optimized_reschedule_all` | No unplaced classes when feasible; hard constraints respected; quality ≥ greedy baseline. |

## 7. How verification is run

```bash
# CI-equivalent local checks (no test runner involved):
pip install -r requirements.txt && pip check
python -c "from scheduler_app._version import __version__; print(__version__)"
python verify_deps.py

# Manual smoke run of the app itself:
python scheduler_gui.py
```

CI uses the structural checks in `.github/workflows/ci.yml`. Tests build no in-memory state because there are no tests; ad-hoc verification can use `models.new_state()` / `models.new_class()` in a REPL.

## 8. Manual QA gates

`docs/RELEASE_CHECKLIST.md` lists the manual checks performed before each release. Highlights (paraphrased, adjusted for the offline app):
- Launch a fresh install on Windows; the app opens directly into the main window after the first-run **language gate** (no login, no account, no update prompt).
- Restore an existing autosave.
- Place a class manually; auto-place; reschedule; verify the analytics dashboard.
- Import an Excel template and export to all three formats (Excel/CSV/PDF).
- Trigger the bug-report button and confirm it opens the default mail client (`mailto:dersis.app@gmail.com`); confirm the clipboard fallback when no mail client is configured.
- Verify uninstall removes the program but leaves `~/Documents/Dersis/` user data intact.
