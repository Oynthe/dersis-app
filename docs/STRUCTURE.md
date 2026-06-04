# DERSIS Repository Structure

## Directory Tree

```
dersis/
├── scheduler_gui.py              # Main entry point (GUI launcher)
├── requirements.txt              # Python dependencies
├── verify_deps.py                # Pre-build dependency checker
│
├── scheduler_app/                # Main application package
│   ├── __init__.py               # Package init + backward-compat import shims
│   ├── plans.py                  # Tier/plan configuration, feature flags, pricing
│   │
│   ├── core/                     # Scheduling engine
│   │   ├── __init__.py
│   │   ├── models.py             # Data models (class, state, location types, protection levels)
│   │   ├── constants.py          # Visual/UI constants (colors, dimensions)
│   │   ├── logic.py              # Core scheduling logic, conflict detection, occupancy
│   │   ├── workflow.py           # UI-free orchestration of scheduling operations
│   │   ├── schedule_optimizer.py # Multi-engine optimization pipeline (heuristic + LNS + CP-SAT)
│   │   ├── cpsat_scheduler.py    # Google OR-Tools CP-SAT solver wrapper
│   │   ├── constraint_validator.py   # Hard constraint validation with O(1) lookups
│   │   ├── constraint_propagator.py  # Incremental constraint state tracking
│   │   ├── constraint_negotiator.py  # Constraint relaxation and conflict resolution
│   │   ├── candidate_generator.py    # Valid placement candidate generation
│   │   ├── conflict_graph.py         # Graph-based scheduling relationship analysis
│   │   ├── placement_scorer.py       # Soft-objective scoring for placement ranking
│   │   ├── timetable_scorer.py       # Overall timetable quality evaluation
│   │   ├── parallel_scorer.py        # Multi-process parallel scoring
│   │   ├── schedule_analytics.py     # Post-optimization quality analysis (grades A-F)
│   │   ├── schedule_impact_analyzer.py # Non-invasive change impact assessment
│   │   ├── analytics.py              # Per-entity timetable metrics
│   │   ├── explanation_engine.py      # Human-readable explanations for AI decisions
│   │   ├── lns_strategies.py          # Large Neighborhood Search destroy/repair strategies
│   │   └── optimization_goals.py      # User slider goals → internal scoring weights
│   │
│   ├── ui/                       # PyQt6 GUI layer
│   │   ├── __init__.py
│   │   ├── app.py                # Main window (SchedulerApp), menus, toolbars, event handling
│   │   ├── dashboard.py          # Analytics dashboard (charts, gauges, metrics tabs)
│   │   ├── dialogs.py            # All modal dialogs (setup, add class, place, bulk add, edit)
│   │   ├── widgets.py            # Reusable widgets (toast, multi-select, warning log)
│   │   ├── bug_report.py         # In-app bug/crash report dialogs (email via mailto)
│   │   ├── renderer.py           # QGraphicsView-based timetable grid renderer
│   │   ├── cell_formatter.py     # Cell content assembly for display/export
│   │   ├── badge_formatter.py    # Protection-level badge display (icons + colors)
│   │   ├── day_keys.py           # Weekday key helpers and normalization
│   │   ├── icons.py              # Programmatic icon generation (toolbar, menu, flags)
│   │   ├── translations.py       # Multi-language translation dictionary (22 languages)
│   │   ├── tier_translations.py  # Tier/upgrade dialog translations
│   │   ├── tier_enforcement.py   # Local tier gating (institutional by default — all features unlocked)
│   │   ├── first_run.py          # First-run wizard (language selection)
│   │   └── tutorial.py           # Interactive spotlight tutorial overlay
│   │
│   ├── data_io/                  # Data import/export
│   │   ├── __init__.py           # Public API re-exports
│   │   ├── importer.py           # Excel (.xlsx) import with validation
│   │   ├── exporter.py           # Export to Excel/CSV/PDF
│   │   ├── schema.py             # Localized workbook schema helpers
│   │   └── template.py           # Excel template generator with example data
│   │
│   ├── learning/                 # Machine learning / preference adaptation
│   │   ├── __init__.py
│   │   ├── feedback_logger.py    # Persistent logging of user interactions
│   │   └── preference_learner.py # Online gradient learning for weight adjustment
│   │
│   ├── storage/                  # Encrypted persistence layer
│   │   ├── __init__.py           # Re-exports for backward compatibility
│   │   └── storage.py            # Path management, AES-256-GCM .egu container format
│   │
│   └── assets/                   # Application icons
│       ├── __init__.py
│       ├── app_icon.ico          # Windows icon
│       ├── app_icon.png          # Default icon
│       └── app_icon_{16,32,48,64,128,256}.png  # Multi-resolution icons
│
├── flags/                        # Country flag PNGs for language selection (22 flags)
│
├── docs/                         # Documentation and logo
│   ├── dersis.png                # Application logo
│   └── *.md                      # Analysis and reference documentation
│
├── installer/                    # Windows installer assets
│   ├── LICENSE.txt               # License agreement shown by the installer
│   ├── create_wizard_images.py   # Generate Inno Setup wizard BMPs
│   ├── wizard_image.bmp          # Installer left panel image (164x314)
│   └── wizard_small_image.bmp    # Installer top-right icon (55x55)
│
├── VERSION                       # Single-source version string (plain semver)
├── build_embed.bat               # Embeddable Python build (recommended)
├── build_nuitka.bat              # Nuitka compilation build (advanced)
├── installer.iss                 # Inno Setup installer script
│
├── .github/
│   └── workflows/
│       ├── ci.yml                # Version, build-file, and import-smoke checks
│       ├── build-installer.yml   # Windows build + installer + checksum
│       ├── release.yml           # Build + publish GitHub Release
│       ├── claude.yml            # Claude Code CI workflow
│       └── claude-code-review.yml # Claude Code review workflow
│
├── BUILD.md                      # Build & packaging guide
├── README.md                     # User-facing project overview
└── .gitignore                    # Git ignore rules
```

The repository ships without test files. Continuous integration runs lightweight
version, build-file, and import-smoke checks only (see `.github/workflows/ci.yml`).

## Directory Explanations

### `scheduler_app/core/` — Scheduling Engine
The heart of the application. Contains all scheduling logic, constraint satisfaction, optimization algorithms (heuristic greedy, LNS, CP-SAT via Google OR-Tools), scoring systems, analytics, and explainability. Entirely UI-free — operates on plain Python dicts. ~16,000+ lines.

### `scheduler_app/ui/` — PyQt6 GUI Layer
Complete desktop UI built on PyQt6. Includes the main window, interactive timetable renderer (QGraphicsView), dialogs for all workflows, analytics dashboard, tutorial overlay, and a 22-language translation system. ~36,600 lines (mostly translations).

### `scheduler_app/data_io/` — Import/Export
Handles Excel import (with schema validation and conflict resolution), and export to Excel/CSV/PDF. Includes localized template generation with example data.

### `scheduler_app/learning/` — Preference Learning
Logs user interactions (moves, acceptances, rejections) and uses online gradient descent with momentum to adapt scoring weights to user preferences over time.

### `scheduler_app/storage/` — Encrypted Persistence
Custom `.egu` binary container format using AES-256-GCM encryption with SHA-256 checksums. Manages all file paths under `~/Documents/Dersis/`. Handles migration from legacy formats.

### `flags/` — Country Flag Assets
22 PNG flag images used in the language selection dialog during first run.

### `installer/` — Windows Installer Assets
Inno Setup resources: license file, wizard images, and a script to generate installer graphics from the app logo.

## Entry Point

| Entry Point | Purpose |
|---|---|
| `scheduler_gui.py` | **Primary** — GUI launcher; opens directly into the main window (no login or network) |

## Build Files

| File | Method | Output |
|---|---|---|
| `build_embed.bat` | Embeddable Python (recommended) | `build/Dersis.dist/` with full Python runtime |
| `build_nuitka.bat` | Nuitka compilation (advanced) | `build/Dersis.dist/` with compiled native code |
| `installer.iss` | Inno Setup installer | `Output/Dersis_Setup.exe` |
| `verify_deps.py` | Pre-build dep checker | Exit code 0/1 |
