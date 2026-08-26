# Handoff — DERSİS fixing session

Paste the block below into a **fresh** Claude Code session (run from
`C:\dev\dersis-app`) to begin implementing the audit roadmap. Everything it
references is committed under `stress-test/`.

---

```
You are implementing fixes for DERSİS (C:\dev\dersis-app), an offline PyQt6
school-timetabling desktop app (~49k lines Python, v1.0.0). A full stress-test
audit was completed 2026-08-26 and lives in `stress-test/`. Your job is to work
the roadmap in `stress-test/14-implementation-roadmap.md`, phase by phase, turning
findings into verified fixes. This is now an IMPLEMENTATION session — the "don't
fix yet" rule from the audit no longer applies.

READ FIRST (in order):
1. stress-test/00-README.md          — index + executive summary
2. stress-test/14-implementation-roadmap.md — the phased plan you are executing
3. stress-test/12-findings-register.md — canonical findings with stable IDs + repro
Consult the subsystem docs (01–11) for any finding you touch. The register entry
for each finding has the exact file:line, root cause, and recommendation.

ENVIRONMENT (already set up):
- Python venv: C:\dev\dersis-app\.venv-audit\Scripts\python.exe (all deps incl.
  ortools, PyQt6). Run from repo root with Git Bash or PowerShell.
- MANDATORY SANDBOX for anything importing scheduler_app: set HOME and USERPROFILE
  to a temp dir BEFORE the import — storage binds ~/Documents/Dersis at import time.
- Headless GUI: native platform, construct widgets but DON'T call show(); use
  widget.grab() for screenshots. Pre-seed language: from scheduler_app.translations
  import set_language; set_language("tr").
- Reusable audit assets: stress-test/tests/schedule_oracle.py (hard-constraint
  invariant checker), stress-test/tests/scheduler_benchmark.py (scaling), and
  stress-test/tests/_fixtures/dataset_gen.py (deterministic presets tiny→pathological).

WORK ORDER — start with Phase 0 (do not skip; it is the safety net):
- Fix the CI branch trigger master→main (.github/workflows/ci.yml) so CI runs.
- Stand up pytest + the first regression wave. Promote schedule_oracle.py into a
  real pytest that asserts ZERO committed hard-constraint violations, xfail-pinning
  the currently-known ones so they're tracked, then flip them to pass as Phase 1/3
  fixes land. Add: storage roundtrip+corruption, import round-trip of the generated
  template (must preserve class count), export smoke ×3.
- Fix ST-FUNC-001 (import success handler calls undefined _on_state_changed/refresh
  at app.py:4525-4526 → use refresh_grid/_update_status; wrap import in try/except
  with rollback) and ST-FUNC-002 (blank joint-group 'nan' merge at importer.py:297
  → guard with pd.isna) and ST-FUNC-003 (per-row numeric parsing).

Then Phase 1 (data & correctness), Phase 2 (performance), Phase 3 (engine
hardening), etc. — full sequencing and dependency graph in the roadmap.

GUARDRAILS:
- Every fix needs a regression test that fails before and passes after. The
  roadmap lists the required tests per phase.
- Re-run stress-test/tests/schedule_oracle.py after any scheduler/optimizer change;
  it is the correctness spine.
- Preserve existing useful behavior; the audit calls for extraction seams, not
  rewrites. Don't refactor the god objects (app.py/dialogs.py) until Phase 6 and
  only behind passing tests.
- Keep the 6 Criticals as the north star: ST-FUNC-001, ST-FUNC-002, ST-SCHED-001,
  ST-UI-001, ST-PERF-001, ST-ARCH-001.
- Work on a feature branch, one phase (or one coherent finding group) per PR.

Start by reading the three docs above, confirming the venv works
(.venv-audit/Scripts/python.exe stress-test/tests/schedule_oracle.py runs), then
begin Phase 0.
```

---

## Quick reference — the 6 Criticals

| ID | One-liner | Fix location |
|---|---|---|
| [ST-FUNC-001](12-findings-register.md#st-func-001) | Excel import crashes on every success (undefined methods) after mutating state | `ui/app.py:4525-4526` |
| [ST-FUNC-002](12-findings-register.md#st-func-002) | Blank joint-group cells → `'nan'` merge; template loses 60% of classes | `data_io/importer.py:297` |
| [ST-SCHED-001](12-findings-register.md#st-sched-001) | Optimizer commits hard-constraint violations; losers silently dropped | `core/schedule_optimizer.py`, `core/workflow.py:424` |
| [ST-UI-001](12-findings-register.md#st-ui-001) | Renderer silently hides one of two conflicting lessons | `ui/renderer.py:117-131` |
| [ST-PERF-001](12-findings-register.md#st-perf-001) | Super-linear solver on the UI thread, no cancel | `core/schedule_optimizer.py`, `ui/app.py:2683` |
| [ST-ARCH-001](12-findings-register.md#st-arch-001) | Zero tests; CI wired to nonexistent `master` branch | `.github/workflows/ci.yml`, new `tests/` |

Roadmap: [14-implementation-roadmap.md](14-implementation-roadmap.md) ·
Register: [12-findings-register.md](12-findings-register.md) ·
Top-5 leverage: [15-final-assessment.md](15-final-assessment.md#the-five-highest-leverage-changes)
