# File: `scheduler_app/core/models.py`

## 1. File Role
Defines the canonical shape of the **state** dict and the **class** dict, plus all helpers that read/write these structures: location types, protection levels, validation, normalisation, joint-vs-sequential splitting, lecturer-availability rules, and room-capacity checks.

## 2. Why this file matters
**Critical.** Every other module operates on these structures. Any change here ripples everywhere.

## 3. Imports and Dependencies
- stdlib: `uuid`.
- Internal: `scheduler_app.translations` (`TRANSLATIONS`, `tr`) — used to translate location/protection labels and to alias multilingual labels back to stable keys.

## 4. Main Symbols
| Symbol | Lines | Purpose |
|--------|-------|---------|
| Location-type constants and `LOCATION_TYPES`, `VIRTUAL_LOCATION_TYPES`, `LOCATION_LABELS`, `LOCATION_LABEL_KEYS`, `VIRTUAL_LOCATION_DISPLAY` | 8–34 | Stable identifiers + display labels. |
| `_ROOM_UNSET` | 34 | Sentinel for "no override". |
| `get_location_label(lt)`, `get_location_labels()`, `get_virtual_location_labels()` | 37–50 | Translated label helpers. |
| `parse_location_type_label(value)` | 53–67 | Reverse-lookup using multilingual aliases. |
| `is_virtual_location_type(lt)`, `location_type_of(cls)`, `class_uses_physical_room(cls)`, `needs_physical_room(cls)`, `should_show_physical_classroom(cls)`, `get_special_location_resource(cls_or_lt)`, `get_active_physical_classroom(cls, room_override=_ROOM_UNSET)`, `get_effective_room_resource_for_class(cls, room_override=_ROOM_UNSET)`, `get_display_location_label(cls, room_override=_ROOM_UNSET)`, `display_room(cls)`, `get_classroom_export_labels(classrooms, classes)` | 70–168 | Location/room display helpers. |
| Protection-level constants + `PROTECTION_LEVELS`, `PROTECTION_LABELS`, `PROTECTION_LABEL_KEYS` | 170–199 | Five levels: none / soft / same_day / improve_only / locked. |
| `get_protection_label(level)`, `is_immovable(cls)` | 202–211 | Helpers. |
| `effective_day(cls)`, `effective_time(cls)`, `effective_room(cls)`, `mark_placed(cls, day, slot, room)`, `mark_unplaced(cls)` | 213–243 | Placement state. |
| `is_sequential_class(cls)`, `slot_offset_for_target(cls, target_idx)` | 246–255 | Non-joint sequential math. |
| `validate_class_fields(cls)` | 258–278 | Returns list of translated error strings. |
| `new_state()` | 281–291 | Default schedule state dict. |
| `new_lecturer_availability()` | 294–301 | Default availability dict. |
| `get_lecturer_availability(state, lecturer_name)` | 304–307 | Defaulting accessor. |
| `lecturer_available_at(state, lec_name, day, slot)` | 310–331 | Rule: excluded > allowed. |
| `get_room_capacity(state, room)`, `room_fits_class(state, room, cls)` | 334–353 | Capacity helpers. 0 capacity == unlimited. |
| `filter_class_days(cls, all_days)`, `filter_class_times(cls, all_times)`, `apply_lecturer_availability_filters(state, lecturer, days, times)` | 356–402 | Constraint filters. |
| `get_physical_room_candidates(state, cls, apply_capacity=True)`, `get_room_candidates(state, cls, apply_capacity=True)` | 405–428 | Allowed rooms (or `[None]` for virtual). |
| `new_class()` | 431–457 | Canonical class dict. |
| `cls_key(cls)`, `ensure_class_uid(cls)` | 460–480 | UUID identity. |
| `_EDITABLE_CLASS_FIELDS` | 483–503 | Tuple used by `copy_editable_class_fields`. |
| `normalize_class_location_fields(cls)` | 506–514 | Clears classroom fields for virtual location types. |
| `normalize_class_data(cls)` | 517–540 | Backfills missing keys; UUID safety. |
| `normalize_state_classes(state)` | 543–547 | Normalises every class in-place. |
| `copy_editable_class_fields(dst, src)` | 550–561 | Used by `EditClassDialog`. |
| `split_non_joint(cls)` | 564–601 | Sequentially splits a multi-target non-joint class into N clones. |

## 5. Block-by-block code map
| Lines | Block | Notes |
|-------|-------|-------|
| 1–6 | docstring + import | sets up uuid + translations. |
| 8–34 | location-type constants | three logical kinds. |
| 37–67 | label + reverse-label helpers | multilingual-aware. |
| 70–98 | location classification helpers | predicate functions. |
| 101–167 | room/location resolution helpers | source-of-truth for "which room is this class effectively in?". |
| 170–211 | protection constants + helpers | identity of the five levels. |
| 213–243 | placement state helpers | mutate `placed_*` keys. |
| 246–255 | non-joint sequential helpers | used by renderer + logic. |
| 258–278 | `validate_class_fields` | called by add/edit dialogs. |
| 281–301 | `new_state` + `new_lecturer_availability` | canonical defaults. |
| 304–331 | lecturer availability logic | excluded > allowed. |
| 334–353 | room-capacity helpers | 0 == unlimited. |
| 356–402 | day/time filters + availability filter merge | used by candidate generator. |
| 405–428 | room candidates | physical vs virtual. |
| 431–471 | `new_class`, `cls_key`, `ensure_class_uid` | canonical class shape + identity. |
| 483–561 | normalize/copy helpers | tolerant to legacy data. |
| 564–601 | `split_non_joint` | sequential-class expansion. |

## 6. Runtime Behavior
Pure helpers. Stateless. All functions take/return plain Python types. The only ambient state is the imported `TRANSLATIONS` dict (read-only here).

## 7. Data Flow
Mutates input dicts where documented (`mark_placed`, `mark_unplaced`, `normalize_*`, `copy_editable_class_fields`, `ensure_class_uid`, `cls_key`'s lazy UID assignment).

## 8. UI Flow
Not directly UI; UI dialogs call `validate_class_fields` and the label helpers.

## 9. Error Handling and Edge Cases
- `parse_location_type_label` falls back to face-to-face on unknown text.
- `lecturer_available_at` returns True when no availability rules are defined.
- `room_fits_class`: 0 capacity == unlimited; 0 participants == always fits.
- `cls_key` quietly assigns a UUID — the input dict is mutated.
- `normalize_class_data` assigns a new UUID when `class_uid` is None or missing (migration safety).
- `validate_class_fields` returns translation keys via `tr()` rather than raw strings.

## 10. Integration Points
Imported by `core/logic.py`, `core/constraint_validator.py`, `core/candidate_generator.py`, `core/schedule_optimizer.py`, `data_io/importer.py`, `data_io/exporter.py`, `ui/app.py`, `ui/dialogs.py`, `ui/renderer.py`, `storage/storage.py` (`normalize_state_classes` on load).

## 11. Risks and Maintenance Notes
- Add a new class field → also update `new_class`, `normalize_class_data`, `_EDITABLE_CLASS_FIELDS`, and `copy_editable_class_fields`.
- The "0 capacity == unlimited" convention is **subtle** — preserve it everywhere.
- `cls_key` mutating the input dict can surprise read-only-style code that doesn't expect side effects.
- `split_non_joint` creates new UUIDs for the split clones — those clones become independent classes.

## 12. Mini Summary
Schema and helpers for the two canonical dicts (`state` and `cls`). If you change anything fundamental about how a class or schedule is represented, change it here first.
