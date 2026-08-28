# DERSIS — Feature Inventory

All features below are verified from source code. Each entry includes the code location where the feature is implemented.

---

## Core Features

### Timetable Setup
- **Day configuration**: Select active weekdays (Monday-Sunday)
- **Time slot configuration**: Define available hours per day
- **Classroom management**: Add rooms with capacity and type (physical/virtual)
- **Year/branch management**: Define student groups (year + branch pairs)
- **Lecturer management**: Add lecturers with availability constraints
- Code: `scheduler_app/ui/dialogs.py` → `SetupDialog`

### Class Management
- **Add single class**: Full form with course name, code, lecturer, targets, duration, constraints
- **Bulk add via Excel**: Import classes from formatted .xlsx workbooks
- **Edit classes**: Modify properties of existing classes (single or batch)
- **Delete classes**: Remove classes from the schedule
- **Joint classes**: Group classes that must be scheduled at the same time/day
- **Non-joint sequential display**: Classes with same name split for display but schedule independently
- Code: `scheduler_app/ui/dialogs.py` → `AddClassDialog`, `BulkAddDialog`, `EditClassesDialog`
- Code: `scheduler_app/core/models.py` → `split_non_joint()`, `new_class()`

### Manual Placement
- **Drag-and-drop**: Move classes between slots on the timetable grid
- **Real-time validation**: Instant conflict detection on every placement
- **Context menu operations**: Right-click to place, unplace, edit, or delete
- Code: `scheduler_app/ui/renderer.py` → `LessonItem`, `EmptySlotItem`, `TimetableView`
- Code: `scheduler_app/core/workflow.py` → `validate_drop()`

### Conflict Detection
- **Room conflicts**: Two classes in the same room at the same time
- **Lecturer conflicts**: Same lecturer assigned to overlapping classes
- **Student group conflicts**: Overlapping classes sharing target groups
- **Duration overflow**: Class duration exceeding available consecutive slots
- **Capacity violations**: Class size exceeding room capacity
- Code: `scheduler_app/core/constraint_validator.py` → `check_placement()`, `find_conflicts()`
- Code: `scheduler_app/core/constraint_validator.py` → `ConstraintValidator.check_placement()`, `.respects_constraints()`

---

## Advanced Features

### AI Auto-Placement
- **Single class auto-place**: AI selects optimal slot considering all constraints and soft objectives
- **Batch scheduling**: Auto-place multiple unplaced classes in difficulty-first order
- **Full reschedule**: Optimize entire timetable from scratch
- **Lookahead scoring**: Evaluates future impact of each placement decision
- Code: `scheduler_app/core/workflow.py` → `auto_place()`, `schedule_new_classes()`, `reschedule()`
- Code: `scheduler_app/core/placement_scorer.py` → `score_with_lookahead()`

### Optimization Engine
- **Heuristic optimizer**: Greedy placement with difficulty-aware ordering
- **Large Neighborhood Search (LNS)**: 7 destroy strategies + repair with adaptive selection
  - Lecturer gap destroy, student gap destroy, low-score destroy
  - Conflict cluster destroy, day window destroy, random destroy
  - Conflict graph destroy (graph-aware subgraph removal)
- **CP-SAT solver**: Google OR-Tools constraint programming for exact solutions
- **Multi-engine pipeline**: Heuristic → LNS → CP-SAT with seeding between stages
- **Dynamic weight adjustment**: Scoring weights adapt based on optimization progress
- Code: `scheduler_app/core/schedule_optimizer.py`
- Code: `scheduler_app/core/cpsat_scheduler.py` → `CPSATScheduler`
- Code: `scheduler_app/core/lns_strategies.py` → 7 destroy strategy classes + `RepairStrategy`

### Constraint Negotiation
- **Conflict analysis**: When placement fails, explains *why* with categorized reasons
- **Relaxation suggestions**: Proposes specific constraint changes to enable placement
- **Displacement strategies**: Identifies which existing classes could be moved to make room
- **Minimum disruption**: Finds solutions requiring fewest changes
- Code: `scheduler_app/core/constraint_negotiator.py`
- Code: `scheduler_app/core/candidate_generator.py` → `generate_with_conflicts()`, `unplaced_reason()`

### Preference Learning
- **Feedback logging**: Records manual moves, accepted/rejected placements, corrections
- **Online gradient descent**: Learns scoring weight adjustments from user behavior
- **Momentum-based updates**: Prevents oscillation in learned preferences
- **Persistent weights**: Learned preferences saved encrypted, survive app restarts
- Code: `scheduler_app/learning/feedback_logger.py` → `FeedbackLogger`
- Code: `scheduler_app/learning/preference_learner.py` → `PreferenceLearner`

### Explainable AI
- **Placement explanations**: Pros/cons breakdown of why a slot was chosen
- **Rejection explanations**: Categorized conflict reasons (room/lecturer/group/capacity)
- **Optimization explanations**: Quality verdict + metric deltas after reschedule
- **Change explanations**: Why a specific class was moved during optimization
- Code: `scheduler_app/core/explanation_engine.py` → `ExplanationEngine`

### Protection Levels
- **None**: Class can be freely moved
- **Soft protection**: Optimization avoids moving but can if needed
- **Same-day protection**: Can only be moved within the same day
- **Improve-only**: Can only be moved if the new position scores better
- **Locked**: Cannot be moved at all
- **Pinned (immovable)**: Completely fixed, ignored by optimizer
- Code: `scheduler_app/core/models.py` → `PROTECTION_*` constants

### Optimization Goals (User Sliders)
- **Lecturer compactness**: Minimize gaps in lecturer schedules
- **Student compactness**: Minimize idle periods for student groups
- **Room utilization**: Maximize efficient use of classrooms
- **Fairness**: Balance daily teaching loads
- **Minimal disruption**: Prefer keeping classes in current positions
- **Early hour preference**: Favor morning time slots
- 6 presets: balanced, lecturer_priority, student_priority, minimal_change, space_efficient, morning_friendly
- Code: `scheduler_app/core/optimization_goals.py` → `goals_to_weights()`, `PRESETS`

### Change Impact Analysis
- **Non-invasive assessment**: Evaluates impact of setup changes without modifying schedule
- **Impact levels**: NO_RESCHEDULE_NEEDED, RESCHEDULE_RECOMMENDED, RESCHEDULE_REQUIRED
- **Affected entity tracking**: Identifies which lecturers/rooms/groups are affected
- **Violation detection**: Checks if current placements still valid after changes
- Code: `scheduler_app/core/schedule_impact_analyzer.py` → `analyze_impact()`

---

## UI/UX Features

### Timetable Views
- **Per-entity views**: Filter by classroom, lecturer, or student group
- **Matrix view ("Show Everything")**: All classes in a single grid
- **Virtual classroom overlap view**: Special filter for online class overlaps
- Code: `scheduler_app/ui/renderer.py` → `TimetableScene`, `set_filter_mode()`
- Code: `scheduler_app/ui/app.py` → view switching logic

### Interactive Tutorial
- **Spotlight overlay**: Highlights UI elements with dimmed background
- **11 tutorial sections**: Welcome, interface, setup, classes, placement, views, panels, optimization, dashboard, data, shortcuts
- **Step-by-step navigation**: Previous/Next/Skip controls with progress bar
- Code: `scheduler_app/ui/tutorial.py` → `TutorialOverlay`

### First-Run Experience
- **Language selection**: Type-to-search dialog with flag icons for 22 languages
- **Persistent preference**: Language choice saved for future sessions
- Code: `scheduler_app/ui/first_run.py` → `LanguageDialog`, `run_language_gate()`

### Multi-Language UI
- **22 languages supported**: English, Turkish, German, French, Spanish, Chinese, Russian, Brazilian Portuguese, Swedish, Danish, Italian, Dutch, Polish, Hindi, Indonesian, Azerbaijani, Persian, Arabic, South African, Japanese, Korean, Portuguese
- **Full coverage**: All dialogs, tooltips, analytics, export templates, error messages
- Code: `scheduler_app/ui/translations.py` → `TRANSLATIONS` dict (~21,600 lines)

### Visual Indicators
- **Color-coded classes**: Year group colors for visual grouping
- **Protection badges**: Emoji + color badges (lock, shield, arrows, thumbtack)
- **Toast notifications**: Auto-dismissing popups (info, success, warning, error)
- **Warning log panel**: Expandable bottom panel for scheduling warnings
- Code: `scheduler_app/ui/badge_formatter.py`, `scheduler_app/ui/widgets.py`

### Analytics Dashboard
- **Quality gauge**: Circular gauge showing 0-100 score with A-F grade
- **5 analytics tabs**: Rooms, Lecturers, Students, Schedule Load, Schedule Quality
- **Bar charts**: Custom QPainter-rendered horizontal bar charts
- **Metric cards**: Summary statistics for key indicators
- **Actionable insights**: Warnings and improvement suggestions
- Code: `scheduler_app/ui/dashboard.py` → `DashboardWidget`, `QualityGaugeWidget`

---

## Scheduling Engine Capabilities

### Scoring System (14 Parameters)
| Parameter | Default Weight | Purpose |
|---|---|---|
| lecturer_gap | 5.0 | Penalize gaps in lecturer schedules |
| lecturer_cluster | 2.5 | Reward consecutive classes for lecturers |
| student_gap | 2.5 | Penalize student idle periods |
| student_cluster | 1.2 | Reward consecutive classes for students |
| day_overload | 1.5 | Penalize unbalanced daily loads |
| fragmentation | 1.8 | Penalize scattered placements |
| early_slot_bonus | 0.3 | Reward morning slots |
| midday_bonus | 0.2 | Reward midday slots |
| end_of_day_penalty | 0.6 | Penalize late afternoon slots |
| room_switch_penalty | 0.8 | Penalize room changes within a day |
| lookahead_penalty | 3.0 | Impact on future placement options |
| neighbor_impact_penalty | 4.0 | Graph-aware impact on related classes |
| stability_penalty | 2.0 | Penalize moving from current position |
| slot_position | 0.01 | Tiebreaker by slot position |

Code: `scheduler_app/core/placement_scorer.py` → `DEFAULT_WEIGHTS`

### Occupancy-Based Conflict Detection
- O(1) conflict lookups via pre-built occupancy maps
- Three maps: room_occ, lect_occ, group_occ indexed by (day, slot)
- Incremental add/remove for efficient updates
- Code: `scheduler_app/core/constraint_validator.py` → `ConstraintValidator`

### Constraint Propagation
- Lazy-initialized valid placement count cache per class
- Reverse indices by lecturer/group/room for O(affected) invalidation
- Simulation stack for non-invasive lookahead
- Code: `scheduler_app/core/constraint_propagator.py` → `ConstraintPropagator`

### Conflict Graph Analysis
- Adjacency-list graph with typed weighted edges (lecturer, group, room)
- BFS-based subgraph discovery for LNS destroy
- Connected component analysis for independent cluster identification
- Difficulty ranking combining graph degree + constraint tightness
- Code: `scheduler_app/core/conflict_graph.py` → `ConflictGraph`, `ConflictAnalyzer`

### Parallel Scoring
- ProcessPoolExecutor for distributing lookahead scoring across CPU cores
- State/occupancy snapshot serialization for cross-process isolation
- Heuristic threshold for when parallelization is worthwhile
- Code: `scheduler_app/core/parallel_scorer.py` → `ParallelScorerPool`

---

## Import/Export

### Excel Import
- **Structured workbook**: Teachers, Rooms, Branches, Classes sheets
- **Schema validation**: Required/optional column checking with error reporting
- **Duplicate detection**: Warns on duplicate IDs
- **Joint group resolution**: Merges classes with same joint_class_group
- **Localized headers**: Column names in active language
- Code: `scheduler_app/data_io/importer.py` → `load_scheduler_data_from_excel()`

### Excel Template Generation
- **Styled template**: Header formatting, descriptions, example data
- **Localized**: Sheet names and column headers in active language
- **Example data**: Pre-filled rows showing expected format
- Code: `scheduler_app/data_io/template.py` → `generate_excel_template()`

### Export Formats
- **Excel (.xlsx)**: Multi-sheet workbook with color-coded cells, per-entity sheets
- **CSV**: Plain tabular export
- **PDF**: Formatted printable timetable (via reportlab)
- **Virtual room sheets**: Special layout for online/virtual classroom schedules
- Code: `scheduler_app/data_io/exporter.py` → `export_schedule()`

---

## Analytics

### Schedule Quality Scoring
- **Global score**: 0-100 combining compactness, gaps, balance, fragmentation
- **Grade system**: A (90+), B (80+), C (70+), D (60+), F (<60)
- **Per-entity metrics**: Lecturer/group/room-level detailed statistics
- **Before/after comparison**: Delta analysis for optimization runs
- Code: `scheduler_app/core/schedule_analytics.py` → `ScheduleAnalytics`

### Per-Entity Metrics
- **Lecturer**: teaching_days, total_slots, gaps per day, compactness, room_switches, avg_per_day
- **Student group**: idle periods per day, day balance
- **Room**: utilization percentage, underuse detection
- **Schedule**: busiest days/slots, day balance standard deviation
- Code: `scheduler_app/core/analytics.py` → `compute_all_metrics()`

### Insights Generation
- **Warnings**: High gaps, fragmentation, imbalanced days, overloaded lecturers
- **Successes**: Good compactness, balanced loads, efficient room use
- **Localized**: All insights translated to active language
- Code: `scheduler_app/core/schedule_analytics.py` → `_generate_insights()`

---

## Installer / Packaging Features

### Build Methods
- **Embeddable Python** (recommended): Downloads Python 3.11.9 embeddable, bundles all deps, includes source
- **Nuitka compilation** (advanced): Compiles to native C code, hides source, 5-15 min build
- **PyInstaller** (legacy): Standard Python packaging
- Code: `build_embed.bat`, `build_nuitka.bat`, `build.bat`

### Windows Installer
- **Inno Setup**: Professional installer with wizard, license agreement, uninstaller
- **13 installer languages**: English, Turkish, German, French, Spanish, Italian, Dutch, Polish, Portuguese, Russian, Japanese, Korean, Danish
- **Optional VC++ Redistributable**: Bundled for systems without it
- **Branded wizard images**: Custom left panel and icon with gradient branding
- Code: `installer.iss`, `installer/create_wizard_images.py`

### Pre-Build Verification
- **22 required packages**: Checks all direct and transitive dependencies before build
- **Exit code reporting**: 0 = all OK, 1 = missing packages
- Code: `verify_deps.py`

---

## Offline Operation & Storage

### Fully Offline
- **No login or account**: The app launches directly into the main window
- **No network calls**: No license server, heartbeat, update check, or any internet connection — ever
- **All features unlocked locally**: The build runs at the institutional tier by default, so auto-scheduling, optimization, AI explanations, advanced analytics, and every export format are always available
- Code: `scheduler_gui.py` → `main()`, `scheduler_app/plans.py` → `TIER_INSTITUTIONAL`

### In-App Bug Report
- **Report Bug dialog**: Status-bar bug button opens a polished form for manual reports
- **Crash report dialog**: On an unhandled exception, a safe dialog shows the error and offers to report it
- **Email-based**: Composes the report locally (app version, OS, severity, steps, optional traceback) and opens the user's default email client via `mailto:dersis.app@gmail.com`; falls back to copying the text to the clipboard if no mail client is configured
- **Nothing transmitted by the app**: The report is handed to the user's email client; the app never sends anything itself
- Code: `scheduler_app/ui/bug_report.py` → `BugReportDialog`, `CrashReportDialog`, `BugReportButton`

### Encrypted Storage
- **Custom .egu format**: AES-256-GCM + SHA-256 checksum binary container
- **Master key**: 32-byte key stored in `~/Documents/Dersis/keys/key.bin`
- **Legacy migration**: Transparent migration from old `.uva`, Fernet, and plain JSON formats
- Code: `scheduler_app/storage/storage.py`
