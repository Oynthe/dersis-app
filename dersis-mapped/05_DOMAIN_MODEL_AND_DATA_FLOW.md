# 05 — Domain Model and Data Flow

The whole scheduling engine operates on **plain Python dictionaries**. There are no ORM models, no dataclasses for the persistent state, no Pydantic schemas. This keeps the data trivially picklable, JSON-serialisable, and snapshotable across process boundaries (e.g. the `ParallelScorerPool`).

The few `@dataclass` declarations that exist (`AutoPlaceResult`, `ScheduleNewResult`, `PlaceBatchResult`, `DropValidation`, `EditClassResult`, `DataValidationReport`, `SchedulerDataset`, `UpdateManifest` etc.) are **return-value containers** between workflow and UI — none of them participate in the persistent state.

## 1. `state` — the canonical schedule dict

Source: `scheduler_app/core/models.py::new_state()` (see file map for line numbers).

```python
state = {
    "days":                 [],   # list[str] — weekday keys in display order. e.g. ["monday","tuesday",…]
    "slots":                [],   # list[str] — time-slot labels in chronological order. e.g. ["08:00","09:00",…]
    "classrooms":           [],   # list[str] — physical classroom names in display order.
    "classroom_capacities": {},   # dict[str → int] — name → integer capacity. 0 means unlimited.
    "lecturers":            [],   # list[str] — lecturer display names.
    "lecturer_availability":{},   # dict[str → availability]. See `new_lecturer_availability()`.
    "years":                {},   # dict[str → list[str]] — year/level name → list of branch names.
                                  #   e.g. {"Year 1": ["CS", "Math"], "Year 2": ["CS"]}
    "classes":              [],   # list[cls] — class dicts. See section 2.
}
```

### Lecturer availability

```python
new_lecturer_availability() = {
    "allowed_days":   [],   # list[str] of day keys; empty == all days OK
    "allowed_hours":  [],   # list[str] of slot labels; empty == all slots OK
    "excluded_days":  [],
    "excluded_hours": [],
}
```

Rule (`lecturer_available_at`): **excluded takes precedence** over allowed; non-empty `allowed_*` means "must be in this set".

## 2. `cls` — the canonical class dict

Source: `scheduler_app/core/models.py::new_class()`.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `class_uid` | str | `uuid4()` | Stable identity that survives copy/serialise. |
| `class_code` | str | `""` | Optional course code shown bold in cells. |
| `name` | str | `""` | Display name. |
| `lecturer` | str | `""` | Lecturer name (must match `state["lecturers"]`). |
| `targets` | list[dict] | `[]` | Each entry: `{"year": "Year 1", "branch": "CS"}`. |
| `duration` | int | `1` | Number of slots. |
| `participants` | int | `0` | Used for capacity check; 0 means "no constraint". |
| `location_type` | str | `"face_to_face"` | One of `face_to_face`, `online`, `lecturer_office`. |
| `joint_session` | bool | `True` | True → all targets in same block. False → sequential blocks per target. |
| `pinned` | bool | `False` | If True, locked to `pinned_*`. |
| `pinned_day` | str\|None | `None` | Required day key. |
| `pinned_time` | str\|None | `None` | Required slot label. |
| `pinned_classroom` | str\|None | `None` | Required room (face-to-face only). |
| `protection` | str | `"none"` | `none` / `soft` / `same_day` / `improve_only` / `locked`. |
| `allowed_days` | list[str] | `[]` | If non-empty → constraint. |
| `allowed_times` | list[str] | `[]` | If non-empty → constraint. |
| `excluded_days` | list[str] | `[]` | Always applies (with precedence). |
| `excluded_times` | list[str] | `[]` | Always applies. |
| `required_classrooms` | list[str] | `[]` | If non-empty → must be one of these. |
| `excluded_classrooms` | list[str] | `[]` | Must NOT be one of these. |
| `placed` | bool | `False` | Solver output: currently placed? |
| `placed_day` | str\|None | `None` | Solver output. |
| `placed_time` | str\|None | `None` | Solver output. |
| `placed_classroom` | str\|None | `None` | Solver output. |

`normalize_class_data(cls)` backfills any missing keys (e.g. after loading older saves) and applies `normalize_class_location_fields()` which clears the classroom-related fields for virtual `location_type`s.

`split_non_joint(cls)` turns a non-joint multi-target class into N independent single-target clones (one per target), each with its own UUID.

## 3. Validation and feasibility

| Function | Where | What it checks |
|----------|-------|----------------|
| `validate_class_fields(cls)` | `models.py` | Field-level: name, lecturer, targets, duration, pinned coherence. Returns list of translated error strings. |
| `slots_fit(state, start_slot, duration)` | `logic.py` | Whether duration fits within remaining slots. |
| `room_fits_class(state, room, cls)` | `models.py` | Whether room capacity ≥ participants. 0 capacity == unlimited. |
| `lecturer_available_at(state, lecturer, day, slot)` | `models.py` | Per-day/slot availability. |
| `find_conflicts(state, cls, day, slot, room)` | `logic.py` | Occupancy conflicts vs existing placed classes (room / lecturer / group / lecturer availability). **Does not** check the class's own constraints. |
| `ConstraintValidator.respects_constraints / check_placement / find_conflicts / check_placement_explained` | `constraint_validator.py` | The authoritative pipeline used by the optimizer. |

## 4. Identity and equality

Classes are identified by `cls_key(cls)` which returns `cls["class_uid"]` (lazily assigning one if missing). This means:
- Two dicts with the same UID are "the same class" — even if other fields drift.
- Deep-copying preserves identity (UID is just a string).
- `set` of class identities is built with `{cls_key(c) for c in classes}`.

## 5. Data flow through the system

```
┌─────────────────────────┐
│  User input from UI     │
│  (dialogs / Excel /     │
│   drag-drop)            │
└────────────┬────────────┘
             │
             ▼  state mutations
┌─────────────────────────┐         ┌─────────────────────────┐
│   state (dict)          │◄────────│  data_io/importer       │
│   ── classes etc.       │         │  data_io/exporter       │
└────────────┬────────────┘         └─────────────────────────┘
             │
             ▼  passed by reference, no copy
┌─────────────────────────────────────────────────────────────┐
│  SchedulingWorkflow                                          │
│  - place_class / auto_place_class / schedule_new_classes     │
│  - reschedule_all / validate_drop / edit_class               │
└────────────┬─────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│  optimized_* in core/logic.py                                │
│  → ScheduleOptimizer (greedy + LNS + optional CP-SAT)        │
│      → CandidateGenerator → ConstraintValidator              │
│      → PlacementScorer (uses ConstraintPropagator + parallel)│
│      → TimetableScorer                                        │
└────────────┬─────────────────────────────────────────────────┘
             │  returns updated (cls, day, slot, room) tuples
             ▼
┌─────────────────────────┐
│  mark_placed / mark_…   │     Each placement decision mutates the
│  on each class dict     │     same `state` in place.
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐         ┌─────────────────────────┐
│  ui/renderer.py paints  │         │  feedback_logger writes │
│  the new positions      │         │  to logs/feedback_log.. │
└─────────────────────────┘         └────────────┬────────────┘
                                                 ▼
                                    ┌─────────────────────────┐
                                    │  preference_learner     │
                                    │  reads log, updates     │
                                    │  weight deltas, writes  │
                                    │  learning/learned_      │
                                    │  weights.egu            │
                                    └─────────────────────────┘
```

## 6. Save / load behaviour

The persistence layer (`storage/storage.py`) treats every saved object identically: serialise as UTF-8 JSON, encrypt via AES-256-GCM, write atomically.

| File | What it holds |
|------|---------------|
| `settings/app_settings.egu` | UI prefs (language, tab, zoom), update prefs, misc. |
| `settings/negotiation_settings.egu` | Constraint-negotiation preferences. |
| `settings/auth_session.egu` | Bearer token, user info, license info, tier, device_hash, heartbeat config, grace_period, min_version, remember flag. |
| `settings/device_identity.egu` | Cached SHA-256 device hash so wmic isn't re-run every launch. |
| `saves/timetable_YYYY_MM_DD_HH_MM_SS.egu` | A `state` snapshot for "File → Save As". |
| `saves/autosave.egu` | The most recent `state` snapshot — restored on next launch if the user accepts. |
| `learning/learned_weights.egu` | `{ "weight_deltas": {...}, "velocity": {...}, "train_count": int }`. |
| `learning/preference_model.egu` | Reserved / future use. |
| `logs/feedback_log.egu` | List of feedback entry dicts (one append per user event). |
| `logs/crash_log.txt` | **Plain text**, append-only, NOT encrypted. |
| `keys/key.bin` | 32-byte raw AES-256 master key (chmod 0o600 on POSIX). |
| `backups/` | Backed-up legacy files after migration. |
| `exports/` | Default user export location (no fixed schema). |

## 7. Custom file format `.egu`

Documented in detail in `09_SETTINGS_LOCALIZATION_AND_PERSISTENCE_MAP.md`. Summary:

```
EGU1  ver(2)  salt(16)  iv(12)  payload_len(4)  AES-256-GCM ciphertext(N)  sha256(32)
```

Per-file AES key = `sha256(master_key || salt)`. Auth tag is the last 16 bytes of the ciphertext (AES-GCM standard). The trailing SHA-256 covers the entire body (header + salt + iv + payload_len + ciphertext) — corruption is caught before decryption is attempted.

Legacy formats accepted transparently on load:
- `UVA1` magic — same layout (was the previous container name).
- Fernet token — old encrypted format; key file `keys/scheduler.key`.
- Plain JSON — predates encryption entirely.

On save, everything always becomes `EGU1`.

## 8. Validation rules in import

`data_io/importer.py` (`load_scheduler_data_from_excel`) produces a `SchedulerDataset`:
- `state` — populated from sheets.
- `report` — `DataValidationReport(errors, warnings)`.
- Raw input lists kept for diagnostics.

Schema validation:
- Required columns must be present (otherwise sheet is skipped and an error is recorded).
- Duplicate IDs → error.
- Unknown extra columns → warning.
- Unknown room references in `allowed_rooms` → warning (and the unknown ones are dropped).
- Teacher/branch references must resolve, otherwise the class row is skipped with an error.

Post-processing:
- `_resolve_joint_groups()` — classes sharing a `joint_class_group` value are merged into one joint session (targets unioned) and the duplicates removed.
- `normalize_state_classes()` — every class normalised against the canonical dict shape.

## 9. Export data flow

`data_io/exporter.py::export_schedule(state, path, format=…)`:
- Wraps `state` in a `FinalSchedule` helper.
- For Excel: writes one sheet per view (Lecturer view, Classroom view, Branch view, Show Everything) with `openpyxl`, using `CellRichText` for colour-coded multi-line cells.
- For CSV: writes a flat grid, one row per slot, columns per day or per classroom (depending on view).
- For PDF: uses `reportlab` to lay out a styled table.

The exporter does **not** mutate `state`. It only reads.
