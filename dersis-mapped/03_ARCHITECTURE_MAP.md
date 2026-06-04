# 03 — Architecture Map

## High-level layer cake

```
                       ┌─────────────────────────────────────────┐
                       │ Entry point: scheduler_gui.py           │
                       │   main() → QApplication → language gate →│
                       │   set institutional tier → SchedulerApp │
                       │   window (fully offline, no network)     │
                       └────────────────┬────────────────────────┘
                                        │
                       ┌────────────────▼─────────────────┐
                       │       UI layer (PyQt6)           │
                       │ ui/app.py            ← main window│
                       │ ui/dialogs.py        ← modals     │
                       │ ui/renderer.py       ← timetable  │
                       │ ui/dashboard.py      ← analytics  │
                       │ ui/widgets.py        ← reusable   │
                       │ ui/first_run.py      ← lang gate  │
                       │ ui/bug_report.py     ← mailto rpt │
                       │ ui/translations.py   ← 22 langs   │
                       │ ui/tier_enforcement  ← always-on  │
                       └─────┬───────────────────────────┬─┘
                             │ user actions             │ paint/state
                             ▼                          ▲
                       ┌──────────────────────────────────┐
                       │   Workflow orchestration         │
                       │   core/workflow.py               │
                       │   SchedulingWorkflow class       │
                       │   (UI-free; returns dataclasses) │
                       └─────────────┬────────────────────┘
                                     │
       ┌─────────────────────────────┼─────────────────────────────┐
       ▼                             ▼                             ▼
┌──────────────┐           ┌──────────────────┐         ┌──────────────────┐
│ Domain model │           │ Hard constraints │         │ Optimization     │
│ core/models  │◄──────────┤ constraint_*     │◄────────┤ schedule_optimi… │
│ core/logic   │           │ candidate_gen    │         │ lns_strategies   │
│ core/        │           │ logic.py         │         │ cpsat_scheduler  │
│ constants.py │           └──────────────────┘         │ placement_scorer │
└──────────────┘                                        │ timetable_scorer │
                                                        │ parallel_scorer  │
                                                        │ conflict_graph   │
                                                        │ optimization_…   │
                                                        └────────┬─────────┘
                                                                 │
                                              ┌──────────────────▼────────────┐
                                              │ Explanation / analytics       │
                                              │ explanation_engine.py         │
                                              │ analytics.py                  │
                                              │ schedule_analytics.py         │
                                              │ schedule_impact_analyzer.py   │
                                              │ constraint_negotiator.py      │
                                              └───────────────────────────────┘

Cross-cutting layers (used by everything above):

┌────────────────────────┐  ┌──────────────────────┐
│ Persistence (.egu)     │  │ Learning             │
│ storage/storage.py     │  │ learning/feedback_*  │
│ storage/__init__.py    │  │ learning/preference_*│
│ Paths: ~/Documents/    │  │                      │
│   Dersis/{settings,    │  │ Loops back into      │
│   saves, learning,     │  │ placement_scorer     │
│   logs, exports,       │  │ via weight deltas    │
│   backups, keys}       │  │                      │
└────────────────────────┘  └──────────────────────┘

Adjacent supporting subsystems:

┌─────────────────────────┐  ┌──────────────────────┐
│ Import / Export         │  │ Build / Release      │
│ data_io/importer.py     │  │ build_embed.bat      │
│ data_io/exporter.py     │  │ build_nuitka.bat     │
│ data_io/schema.py       │  │ installer.iss        │
│ data_io/template.py     │  │ .github/workflows/   │
└─────────────────────────┘  └──────────────────────┘
```

## How the layers communicate

1. **UI → Workflow.** The `SchedulerApp` (in `ui/app.py`) creates a `SchedulingWorkflow` instance bound to the current `state` dict. UI actions (menu clicks, drag-drops) call workflow methods such as `auto_place_class`, `schedule_new_classes`, `reschedule_all`, `validate_drop`. The workflow returns plain dataclasses (`AutoPlaceResult`, `ScheduleNewResult`, `PlaceBatchResult`, `DropValidation`, `EditClassResult`). **No Qt imports in `core/workflow.py`.**

2. **Workflow → Optimization.** `workflow.py` re-exports / delegates to `optimized_auto_place`, `optimized_batch_schedule`, `optimized_reschedule_all` (in `core/logic.py`), which in turn instantiate `ScheduleOptimizer` (`core/schedule_optimizer.py`). Optimizer pulls in `ConstraintValidator`, `CandidateGenerator`, `PlacementScorer`, `TimetableScorer`, `ParallelScorerPool`, the destroy/repair strategies in `lns_strategies.py`, the conflict graph in `conflict_graph.py`, the constraint propagator, and optionally the `CPSATScheduler` (only if `ortools` is available).

3. **Hard-constraint enforcement.** `ConstraintValidator` builds three occupancy maps `(day, slot) → {set}` for rooms, lecturers, and student groups. Every placement check is O(1) per slot times the duration. The validator is the **sole authority** for "is this placement legal?" — `respects_constraints` in `logic.py` is deprecated.

4. **Soft scoring.** `PlacementScorer` produces a numerical score for a candidate (lower = better) across 14 weighted components. It can look ahead by simulating the placement and asking the propagator how many valid placements remain for the still-unscheduled classes. The `TimetableScorer` evaluates an entire schedule (used by LNS to compare destroy/repair candidates).

5. **Persistence.** Any save (`File → Save`, autosave, settings, learned weights, feedback log) goes through `storage/storage.py::save_encrypted()`. Each call: JSON-serialise → derive per-file AES key from master key + salt → AES-256-GCM encrypt → checksum → atomic write via `<path>.tmp` + `os.replace`. Load is the inverse, with fallbacks for legacy `UVA1`, Fernet, and plain JSON formats.

6. **Tier enforcement (offline, always-unlocked).** `main()` calls `TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)` before constructing the window, so every feature flag is `True` and all entity limits are unlimited. The gating helpers (`require_feature` / `require_entity_limit` / `gate_menu_action`) and `UpgradeDialog` in `ui/tier_enforcement.py` are retained but always allow and never display; the toolbar upgrade button/banner stay hidden. No network, no login, no server validation.

7. **Bug / crash reporting (local only).** "Report Bug" (`BugReportDialog`) and the crash hook (`CrashReportDialog`, both in `ui/bug_report.py`) compose a plain-text report from local diagnostics (app version, OS, what happened, steps, expected, actual, optional traceback) and hand it to the user's default email client via a `mailto:emre.uygun.elt@gmail.com` link (`QDesktopServices.openUrl`). If no mail client is available, the body is copied to the clipboard and a dialog shows the address. Nothing is transmitted by the app.

8. **Localisation.** Every user-visible string goes through `tr("key.path")` from `ui/translations.py`. The dictionary contains 22 language blocks (`en`, `tr`, `de`, `fr`, `es`, `zh`, `ru`, `ar`, `fa`, `it`, `pt_BR`, `pt_PT`, `nl`, `sv`, `da`, `pl`, `az`, `hi`, `id`, `af`, `ja`, `ko`). `set_language()` updates a module-level global; `is_rtl()` returns whether the language is right-to-left.

## Textual dependency diagram (selected, top-down)

```
scheduler_gui.py
  ├── scheduler_app.app                   (alias of ui.app)
  ├── scheduler_app.storage               (paths, encryption)
  ├── scheduler_app.translations          (alias of ui.translations)
  ├── scheduler_app.first_run             (alias of ui.first_run; language gate)
  ├── scheduler_app.ui.tier_enforcement   (singleton; set to institutional)
  ├── scheduler_app.plans                 (TIER_INSTITUTIONAL)
  └── scheduler_app.ui.bug_report         (crash dialog → mailto)

scheduler_app.ui.app
  ├── scheduler_app.renderer              (TimetableView, scene, lesson items)
  ├── scheduler_app.dashboard             (DashboardWidget)
  ├── scheduler_app.constants             (visual constants)
  ├── scheduler_app.translations
  ├── scheduler_app.models
  ├── scheduler_app.logic
  ├── scheduler_app.workflow              (SchedulingWorkflow)
  ├── scheduler_app.dialogs               (every modal)
  ├── scheduler_app.widgets               (Toast, MultiSelectButton, WarningLogPanel)
  ├── scheduler_app.icons                 (flag icons, painted icons)
  ├── scheduler_app.tutorial              (TutorialOverlay)
  ├── scheduler_app.first_run             (run_language_gate, FirstRunController)
  ├── scheduler_app.data_io               (load_scheduler_data_from_excel, export_schedule)
  ├── scheduler_app.feedback_logger       (FeedbackLogger)
  ├── scheduler_app.preference_learner    (PreferenceLearner)
  └── scheduler_app.storage

scheduler_app.core.schedule_optimizer
  ├── scheduler_app.logic
  ├── scheduler_app.models
  ├── scheduler_app.constraint_validator
  ├── scheduler_app.candidate_generator
  ├── scheduler_app.placement_scorer
  ├── scheduler_app.timetable_scorer
  ├── scheduler_app.lns_strategies
  ├── scheduler_app.conflict_graph
  ├── scheduler_app.constraint_propagator
  ├── scheduler_app.parallel_scorer
  └── scheduler_app.cpsat_scheduler       (optional — only when ortools is importable)

scheduler_app.core.cpsat_scheduler
  └── ortools.sat.python.cp_model         (lazy import; HAS_ORTOOLS guard)
```

## Entry points

| Entry | What it launches |
|-------|------------------|
| `python scheduler_gui.py` | The full desktop GUI (only supported entry point). |
| `python verify_deps.py` | Pre-build dependency import check (exit 0/1). |
| `python installer/create_wizard_images.py` | Generates the Inno Setup wizard BMPs. |
| `iscc installer.iss` | Builds the Inno Setup `.exe` installer (after `build_embed.bat`). |
| `build_embed.bat` | Recommended Windows packaging path (~2 min). |
| `build_nuitka.bat` | Alternative Nuitka compile (5–15 min). |

## Runtime flow at a glance (full detail in `04_ENTRYPOINTS_AND_RUNTIME_FLOW.md`)

```
main()                                              (~95 lines; fully offline, no network)
  ├── multiprocessing.freeze_support()
  ├── sys.excepthook = _global_exception_handler   (writes ~/Documents/Dersis/logs/crash_log.txt + shows CrashReportDialog)
  ├── QApplication(sys.argv); setStyle("Fusion")
  ├── run_language_gate()                          (first-time language picker, local only)
  ├── TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)  (unlock every feature locally)
  ├── SchedulerApp().show()                        (opens directly into the main window)
  └── sys.exit(app.exec())                         (Qt event loop)
```
