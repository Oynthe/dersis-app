# DERSIS — Architecture & Technical Context

## Architecture Decisions

### 1. Plain Dict State Model (No ORM / No Classes)

The entire scheduling state is represented as a single Python `dict` (called `state`) containing lists and nested dicts. Classes, lecturers, rooms, and time slots are all plain dicts — not dataclass instances or ORM models.

**Why**: This design enables:
- Trivial JSON serialization for save/load/export
- Deep copy for snapshot/restore (atomic operations)
- No schema migration burden when fields change
- Cross-process serialization for parallel scoring

**Trade-off**: No type safety at the model boundary; validation is done explicitly via `validate_class_fields()` and `normalize_class_data()`.

State shape (from `models.py` → `new_state()`):
```python
{
    "days": ["monday", "tuesday", ...],
    "slots": ["09:00", "10:00", ...],
    "classrooms": ["Room A", "Room B", ...],
    "years": {"Year 1": ["CS", "Math"], ...},
    "classes": [
        {
            "uid": "uuid-string",
            "name": "Algorithms",
            "lecturer": "Dr. Smith",
            "targets": [{"year": "Year 1", "branch": "CS"}],
            "duration": 2,
            "pinned": False,
            "placed": False,
            "pinned_day": None, "pinned_time": None, "pinned_classroom": None,
            "placed_day": None, "placed_time": None, "placed_classroom": None,
            "allowed_days": [], "allowed_times": [],
            "protection": 0,
            "location_type": "face_to_face",
            ...
        },
        ...
    ]
}
```

### 2. UI-Free Core (Workflow Layer)

The `SchedulingWorkflow` class (`core/workflow.py`) is the single orchestration point between UI and engine. It:
- Accepts a state dict, performs operations, returns structured result objects
- Never imports PyQt6 or any UI module
- Uses snapshot/restore for atomic rollback on failure

This separation means the scheduling engine can be tested, used from CLI (`scheduler.py`), or driven programmatically without any GUI dependency.

### 3. Backward-Compatible Import Shims

When the codebase was restructured from flat `scheduler_app/models.py` to `scheduler_app/core/models.py`, a `MetaPathFinder` shim was added in `scheduler_app/__init__.py` to transparently redirect old imports. This means:
- `from scheduler_app.models import X` still works (maps to `scheduler_app.core.models`)
- `from scheduler_app.logic import X` still works (maps to `scheduler_app.core.logic`)
- ~20 legacy paths are shimmed

### 4. Encrypted Everything

All persistent data uses the custom `.egu` binary container format with AES-256-GCM encryption. This includes saved schedules, settings, learned preferences, and feedback logs. The rationale is protecting institutional data from tampering. All data stays on the local machine — DERSIS is fully offline and makes no network calls.

---

## How Scheduling Logic Works

### Data Flow: Input → Processing → Output

```
User Input (Setup + Classes)
       │
       ▼
   State Dict
       │
       ├──► ConstraintValidator (builds occupancy maps)
       │         │
       │         ▼
       ├──► CandidateGenerator (enumerate valid placements)
       │         │
       │         ▼
       ├──► PlacementScorer (rank candidates by soft objectives)
       │         │
       │         ▼
       ├──► ScheduleOptimizer (heuristic + LNS + CP-SAT pipeline)
       │         │
       │         ▼
       ├──► ConstraintNegotiator (resolve failures via relaxation)
       │         │
       │         ▼
       ├──► ExplanationEngine (human-readable reasoning)
       │         │
       │         ▼
   Updated State Dict → UI Rendering / Export
```

### Step-by-Step Processing

1. **Occupancy Map Construction** (`ConstraintValidator.__init__`):
   Three dictionaries are built from placed classes:
   - `room_occ[(day, slot)]` → set of class UIDs occupying that room-slot
   - `lect_occ[(day, slot)]` → set of class UIDs for that lecturer-slot
   - `group_occ[(day, slot)]` → set of class UIDs for that group-slot
   
   These enable O(1) conflict lookups instead of O(n) scans.

2. **Candidate Generation** (`CandidateGenerator.generate()`):
   For each unplaced class, enumerate all `(day, slot, room)` triples where:
   - Day is in the class's allowed days (or all days if unconstrained)
   - Slot allows the class duration to fit in consecutive slots
   - Room matches the class's location type requirements
   - No hard constraint violations exist (checked via occupancy maps)

3. **Scoring** (`PlacementScorer.score()`):
   Each candidate is scored on 14 soft objectives (see FEATURES.md). Key metrics:
   - Lecturer compactness: How well the placement clusters the lecturer's day
   - Student compactness: How well it clusters the student group's day
   - Gap minimization: Fewer holes in daily schedules
   - Fragmentation: Avoid scattering classes across many days
   - Neighbor impact: Graph-aware assessment of how this placement affects related classes

4. **Optimization Pipeline** (`ScheduleOptimizer`):
   - **Phase 1 — Heuristic**: Sort unplaced classes by difficulty (most constrained first), place each using best-scoring candidate with lookahead
   - **Phase 2 — LNS iterations**: Repeatedly destroy a subset of placements using adaptive strategy selection, then repair using the heuristic. Accept if timetable score improves.
   - **Phase 3 — CP-SAT** (optional): Build a constraint programming model, seed with heuristic solution, let OR-Tools solver search for provably better solutions

5. **Result Delivery**:
   - Structured result objects (`AutoPlaceResult`, `RescheduleResult`, etc.)
   - Explanation text via `ExplanationEngine`
   - Analytics via `ScheduleAnalytics` (quality score, grade, insights)

---

## Constraints System

### Hard Constraints (Must Never Be Violated)

| Constraint | Check Method | Map Used |
|---|---|---|
| Room conflict | `check_placement()` | `room_occ` |
| Lecturer conflict | `check_placement()` | `lect_occ` |
| Student group conflict | `check_placement()` | `group_occ` |
| Duration overflow | `slots_fit()` | Direct slot list check |
| Room capacity | `respects_constraints()` | Room metadata |
| Allowed days filter | Pre-filtered in `CandidateGenerator` | Class metadata |
| Allowed times filter | Pre-filtered in `CandidateGenerator` | Class metadata |
| Lecturer availability | `apply_lecturer_availability_filters()` | Lecturer metadata |
| Pinned/locked protection | Skipped by optimizer | `protection` field |

### Soft Objectives (Optimized, Not Enforced)

Soft objectives are encoded as scoring weights in `PlacementScorer`. They influence *where* a class is placed among valid options but never cause a valid placement to be rejected.

Priority order (by default weight):
1. **Lecturer gap minimization** (weight 5.0) — Highest priority
2. **Neighbor impact** (weight 4.0) — Don't harm related classes
3. **Lookahead penalty** (weight 3.0) — Don't reduce future options
4. **Lecturer clustering** (weight 2.5) — Consecutive classes preferred
5. **Student gap minimization** (weight 2.5)
6. **Stability** (weight 2.0) — Don't move from current position
7. **Fragmentation** (weight 1.8) — Avoid scattered days
8. **Day overload** (weight 1.5) — Balance daily loads
9. **Student clustering** (weight 1.2)
10. **Room switching** (weight 0.8)
11. Lower: end-of-day penalty, early/midday bonuses, tiebreakers

---

## State Management

### Snapshot/Restore Pattern

Critical operations in `SchedulingWorkflow` use snapshot/restore for atomicity:

```python
snapshot = copy.deepcopy(state['classes'])
try:
    # Perform multi-step operation
    result = optimizer.run(state)
    if not acceptable(result):
        raise Rollback()
except:
    state['classes'] = snapshot  # Atomic rollback
```

This ensures that failed optimization runs, invalid batch placements, or user cancellations leave the state unchanged.

### Occupancy Map Maintenance

The `ConstraintValidator` maintains occupancy maps incrementally:
- `add_placement(cls)`: Updates all three maps when a class is placed
- `remove_placement(cls)`: Removes from all three maps when unplaced
- Maps are rebuilt from scratch only at initialization

### Protection State

Each class has a `protection` field (0-4) and a `pinned` boolean:
- `pinned=True`: Class is completely immovable (set by user)
- `protection=0` (NONE): Free to move
- `protection=1` (SOFT): Optimizer avoids but can move
- `protection=2` (SAME_DAY): Can only move within same day
- `protection=3` (IMPROVE_ONLY): Can only move to better-scoring position
- `protection=4` (LOCKED): Cannot be moved by optimizer

---

## Optimization Logic

### Heuristic Optimizer (Greedy + Lookahead)

1. **Difficulty sorting**: Classes sorted by constraint tightness (fewest valid candidates first)
2. **For each unplaced class**:
   a. Generate all valid candidates via `CandidateGenerator`
   b. Score each with `PlacementScorer.score_with_lookahead()`
   c. Place in highest-scoring slot
   d. Update occupancy maps and propagate constraints
3. **Constraint propagation**: After each placement, invalidate cached valid counts for affected classes (those sharing lecturer, room, or group)

### Large Neighborhood Search (LNS)

LNS iteratively improves an existing schedule:

1. **Destroy phase**: Remove a subset of placed classes using one of 7 strategies:
   - `LecturerGapDestroy`: Target classes contributing to lecturer gaps
   - `StudentGapDestroy`: Target classes causing student idle periods
   - `LowScoreDestroy`: Remove worst-quality placements
   - `ConflictClusterDestroy`: Remove from dense same-day conflict clusters
   - `DayWindowDestroy`: Remove all classes from a problematic day
   - `RandomDestroy`: Random subset for exploration
   - `ConflictGraphDestroy`: Remove tightly connected subgraph from conflict graph

2. **Repair phase**: Re-insert removed classes using difficulty-aware ordering + lookahead scoring

3. **Acceptance**: Accept new schedule if `TimetableScorer.score()` improves (lower = better)

4. **Adaptive strategy selection**: Tracks success rate of each destroy strategy using exponential moving average. Strategies that produce improvements get higher selection probability.

### CP-SAT Solver Integration

The `CPSATScheduler` wraps Google OR-Tools CP-SAT:

1. **Variables**: For each flexible (non-pinned) class: `day_var`, `slot_var`, `room_var`
2. **Hard constraints**: No-overlap intervals for rooms/lecturers/groups, pinned class fixes, duration fitting
3. **Soft objectives** (in objective function): Lecturer compactness, student compactness, gap minimization, day balance
4. **Seeding**: Heuristic solution provided as solver hint for faster convergence
5. **Progressive reporting**: Status callback emits progress during solve

---

## Known Complexity Areas

### 1. `logic.py` (~3,000+ lines)
The largest single module. Contains all low-level scheduling functions: slot arithmetic, occupancy building, conflict detection, placement validation, scoring helpers, and the core `optimized_auto_place()` / `optimized_batch_schedule()` / `optimized_reschedule_all()` entry points. This is the most complex and most performance-critical module.

### 2. `constraint_negotiator.py` (~3,000+ lines)
Complex conflict resolution logic. Analyzes why classes can't be placed, generates relaxation suggestions, and manages displacement negotiations. Contains deep recursive analysis of constraint interactions.

### 3. `schedule_optimizer.py` (~2,000+ lines)
Orchestrates the multi-engine pipeline. Manages state across heuristic, LNS, and CP-SAT phases. Contains adaptive parameter tuning and convergence detection logic.

### 4. `translations.py` (~21,600 lines)
Massive translation dictionary for 22 languages. Not algorithmically complex but dominates file count. Every UI string, error message, and tooltip is stored here.

### 5. `app.py` (~4,990 lines)
The main window class. Handles all menu actions, toolbar actions, state persistence, view switching, file I/O, and event routing. Very large class with many responsibilities.

### 6. `dialogs.py` (~4,450 lines)
All modal dialogs in one file. Contains ~11 dialog classes for setup, class management, placement, bulk operations, and results display.

### 7. Joint Class Handling
Classes with the same `joint_class_group` must be scheduled at the same day/time. The system splits display but maintains constraint linkage. This adds complexity throughout the candidate generation, validation, and optimization code paths.

### 8. Location Type System
Three location types (face-to-face, online, lecturer office) have different room allocation rules. Online classes share a virtual room and can overlap. This creates special-case logic in conflict detection and candidate generation.

---

## Dependency Graph (High-Level)

```
scheduler_gui.py (entry point)
├── scheduler_app.ui.app (SchedulerApp — main window)
│   ├── scheduler_app.core.workflow (SchedulingWorkflow)
│   │   ├── scheduler_app.core.logic (core scheduling functions)
│   │   ├── scheduler_app.core.constraint_validator
│   │   ├── scheduler_app.core.constraint_negotiator
│   │   ├── scheduler_app.core.explanation_engine
│   │   └── scheduler_app.core.models
│   ├── scheduler_app.core.schedule_optimizer
│   │   ├── scheduler_app.core.cpsat_scheduler (OR-Tools)
│   │   ├── scheduler_app.core.lns_strategies
│   │   ├── scheduler_app.core.placement_scorer
│   │   │   ├── scheduler_app.core.conflict_graph
│   │   │   ├── scheduler_app.core.constraint_propagator
│   │   │   └── scheduler_app.core.parallel_scorer
│   │   ├── scheduler_app.core.timetable_scorer
│   │   └── scheduler_app.core.candidate_generator
│   ├── scheduler_app.ui.renderer (timetable rendering)
│   ├── scheduler_app.ui.dashboard (analytics)
│   │   ├── scheduler_app.core.schedule_analytics
│   │   └── scheduler_app.core.analytics
│   ├── scheduler_app.ui.dialogs (all modal dialogs)
│   ├── scheduler_app.data_io (import/export)
│   ├── scheduler_app.learning (preference learner)
│   ├── scheduler_app.ui.bug_report (in-app email bug/crash reports)
│   └── scheduler_app.ui.tier_enforcement
│       └── scheduler_app.plans (tier config — institutional by default, all unlocked)
└── scheduler_app.storage (encrypted file I/O)
```

External dependencies (all runtime deps; no networking libraries):
- **PyQt6**: UI framework
- **ortools**: CP-SAT constraint solver
- **openpyxl/pandas**: Excel I/O
- **reportlab**: PDF generation
- **cryptography**: AES-256-GCM encryption
- **packaging**: Version comparison (About dialog)
- **deepdiff**: Change detection for impact analysis
