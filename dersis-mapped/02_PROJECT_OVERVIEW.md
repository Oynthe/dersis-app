# 02 — Project Overview

## What the application appears to do

**DERSİS** is a **fully offline** desktop class-scheduling application targeted at **universities and schools**. It runs entirely on the local machine — there is no login, license server, account page, or update check, and the app makes no network calls of any kind. Its job is to take:

- a set of **classes** (each with a lecturer, one or more student groups, a duration, optional location/day/time/room constraints, joint-vs-sequential mode, and optional pinning/protection),
- a set of **rooms** (with capacities and optional virtual variants: face-to-face, online, lecturer office),
- a set of **lecturers** (with allowed/excluded days and hours),
- a set of **student groups** organised under "years" (e.g. Year 1 / Branch CS),
- and a configurable set of **active weekdays** and **time slots**,

and produce a **conflict-free weekly timetable** that satisfies all hard constraints (lecturer/room/group double-booking, capacity, availability, day/time/room restrictions, duration fitting, joint-vs-sequential rules) while optimising soft objectives such as lecturer schedule compactness, student gaps, day-load balance, fragmentation, room switching, and time-of-day quality.

The schedule can be built **interactively** (drag-and-drop, manual placement, auto-place one class) or **in bulk / globally** (batch placement, full reschedule). The system supports **explainable AI** — every auto-placement comes with a pros/cons breakdown and rejected placements explain which constraint was violated.

## Main user-facing purpose

Replace the **manual, error-prone** process of building a university weekly timetable with a tool that:

1. Enforces all hard constraints automatically.
2. Produces a high-quality schedule from scratch.
3. Lets the planner adjust the result interactively, with real-time conflict detection on every drag.
4. Tells the planner *why* something is impossible and *what relaxations* would unblock it.
5. Exports the final timetable to Excel, CSV, or PDF and saves it locally as an encrypted `.egu` file.

## Application type, framework, runtime, technology stack

| Layer | Technology |
|-------|------------|
| GUI framework | **PyQt6 6.5+** (Fusion style) |
| Runtime | **Python 3.10+** (`python3` interpreter; Windows installer ships embeddable Python or Nuitka-compiled binary) |
| Constraint solver | **Google OR-Tools CP-SAT** (`ortools` package) |
| Excel I/O | **openpyxl 3.1+** (read/write `.xlsx`) and **pandas 2.0+** (DataFrames during import) |
| PDF export | **reportlab 4.0+** |
| Encryption | **cryptography ≥ 41** — AES-256-GCM via `AESGCM` |
| Diffing | **deepdiff ≥ 6.0** (schedule impact analysis) |
| Version comparison | **packaging ≥ 21.0** (PEP 440) |
| Installer (Windows) | **Inno Setup 6.x** (`installer.iss`) |
| Build tooling | **Nuitka 2.0+** (optional) or **embeddable Python** (recommended) |

The runtime targets are documented in `requirements.txt`; the exact pinned versions used in CI are in `requirements-lock.txt`. The `requests` dependency was removed in the offline conversion.

## Functional domains

The repo's modules cluster into **seven functional domains**:

1. **Domain model & state** — `core/models.py` (dict schemas for state, class, lecturer availability; protection levels, location types, validation, normalization, joint-vs-sequential splitting).
2. **Hard-constraint engine** — `core/logic.py`, `core/constraint_validator.py`, `core/candidate_generator.py`, `core/constraint_propagator.py`, `ui/day_keys.py`.
3. **Optimization** — `core/placement_scorer.py`, `core/timetable_scorer.py`, `core/parallel_scorer.py`, `core/conflict_graph.py`, `core/lns_strategies.py`, `core/schedule_optimizer.py`, `core/cpsat_scheduler.py`, `core/optimization_goals.py`.
4. **Explanation, analytics, negotiation** — `core/explanation_engine.py`, `core/analytics.py`, `core/schedule_analytics.py`, `core/schedule_impact_analyzer.py`, `core/constraint_negotiator.py`.
5. **Workflow orchestration (UI-free)** — `core/workflow.py`.
6. **Persistence & encryption** — `storage/storage.py` and its re-export shim.
7. **User interface** — everything under `ui/*.py` plus `plans.py` for tier definitions (all features unlocked locally; see below).

Cross-cutting:
- **Localization** — `ui/translations.py` (22 languages) and `ui/tier_translations.py`.
- **Preference learning** — `learning/feedback_logger.py`, `learning/preference_learner.py` (uses gradient descent + momentum on scoring weights).
- **Import/export** — `data_io/*.py`.

## Top-level invariants worth remembering

- **State is a plain Python dict** with keys `days`, `slots`, `classrooms`, `classroom_capacities`, `lecturers`, `lecturer_availability`, `years`, `classes`. Trivially picklable, deep-copyable, and JSON-serialisable. No SQLAlchemy, no ORMs.
- **A class is also a plain dict** with at least these keys: `class_uid`, `class_code`, `name`, `lecturer`, `targets`, `duration`, `participants`, `location_type`, `joint_session`, `pinned`, `pinned_day`, `pinned_time`, `pinned_classroom`, `protection`, `allowed_days`, `allowed_times`, `excluded_days`, `excluded_times`, `required_classrooms`, `excluded_classrooms`, `placed`, `placed_day`, `placed_time`, `placed_classroom`.
- **Identity** is by `class_uid` (UUID4 string), not Python `id()`. Survives serialisation.
- **Lower score is better** throughout the scoring system.
- **Pinned ≠ Locked**: pinning fixes a class to a specific day/time/room; protection levels (`none`/`soft`/`same_day`/`improve_only`/`locked`) constrain how the optimiser may move it.
- All persistent data lives under `~/Documents/Dersis/` in subfolders: `settings/`, `saves/`, `learning/`, `logs/`, `exports/`, `backups/`, `keys/`.
- File format `.egu` is a **custom binary container**: magic `EGU1`, version `1`, salt, IV, AES-256-GCM ciphertext, SHA-256 trailer. Legacy `UVA1`, Fernet, and plain JSON files are auto-migrated.
- **Tiers are local-only and fully unlocked.** `plans.py` and `ui/tier_enforcement.py` still exist, but the app defaults to the `institutional` tier (all feature flags `True`, unlimited entity limits). The gating helpers (`require_feature` / `require_entity_limit` / `gate_menu_action`) and `UpgradeDialog` remain but always allow and never display; the toolbar upgrade button/banner stay hidden. There is no login, no server enforcement, and no `auth_session.egu` / `device_identity.egu`.

## Things that could be misinterpreted (mark as uncertain)

- The phrase "AI" in the codebase refers to **heuristic + LNS + CP-SAT** optimization with **learned weight deltas** from the gradient learner — there is **no large language model** in the runtime. The "AI explanations" feature produces human-readable text from a structured score breakdown using template rules, not from an LLM.
- The "Multi-department" feature (tier flag `multi_department`) appears in `plans.py` but the actual code that gates department behaviour was not located in this map. Since the app is offline and defaults to the `institutional` tier, the flag is always `True` regardless. Treat the underlying feature behaviour as **uncertain**.
- The `archive_repo_cleanup/` folder exists but was not included in the mapping; it is preserved for recovery. Anything you see referenced in old commit messages but missing in current source is likely there.
