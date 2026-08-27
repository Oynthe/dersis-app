# 02 — Functional Inventory

Part of the [DERSİS stress-test audit](00-README.md). Every meaningful function
of DERSİS, mapped to its entry point, code path, failure states, test status, and
the findings it produced. Derived from the traced [system map](01-system-map.md).

Legend — **Test status**: ✅ tested · ◐ partially tested · ○ not driven directly.
**Findings** link to the [register](12-findings-register.md).

---

## A. Setup & configuration

| # | Feature | User goal | Entry point → handler | Backend | Failure states found | Test | Findings |
|---|---|---|---|---|---|---|---|
| A1 | Define days | Choose working days | Toolbar Setup → `SetupDialog` tab 0 (`dialogs.py:681`) | `state['days']` | Removing a day orphans placements (tolerated); no undo | ✅ | [ST-DATA-004](12-findings-register.md#st-data-004) |
| A2 | Define time slots | Enter period labels | `SetupDialog` tab 1 — **free-text box** | `state['slots']` | No format/dedup/order validation; `slots.index()` is load-bearing; removing a slot crashes 8/9 downstream ops | ✅ | [ST-DATA-003](12-findings-register.md#st-data-003), [ST-UI-014](12-findings-register.md) |
| A3 | Define classrooms + capacity | Rooms & sizes | `SetupDialog` tab | `classrooms`, `classroom_capacities` | No dedup of room names | ✅ | [ST-FUNC-012](12-findings-register.md) |
| A4 | Define lecturers | Teacher list | `SetupDialog` tab 2 | `lecturers` | No dedup; rename silently drops availability | ✅ | [ST-FUNC-012](12-findings-register.md) |
| A5 | Lecturer availability | Allowed/excluded days & hours | `LecturerConstraintsDialog` (`dialogs.py:1826`) | `lecturer_availability` | Malformed dict → `KeyError` on hot path; allowed/excluded look identical | ✅ | [ST-SCHED-008](12-findings-register.md), [ST-UI-006](12-findings-register.md) |
| A6 | Define years/branches | Student groups | `SetupDialog` tab 3 | `years` | Removing a year orphans placements (tolerated) | ✅ | — |
| A7 | Apply setup | Commit all setup | `SetupDialog._ok` (`dialogs.py:1755`) | overwrites 7 state keys | **No reconciliation of placed classes; no undo** | ✅ | [ST-DATA-004](12-findings-register.md#st-data-004) |

## B. Class management

| # | Feature | User goal | Entry point → handler | Failure states | Test | Findings |
|---|---|---|---|---|---|---|
| B1 | Add single class | Create a course | Toolbar → `AddClassDialog` (`dialogs.py:2064`, CC 46) → `_schedule_new_classes` | Only first validation error shown; editable lecturer accepts unknown names; validation runs before constraint checkboxes read | ✅ | [ST-UI-015](12-findings-register.md), [ST-ARCH-004](12-findings-register.md) |
| B2 | Bulk add | Spreadsheet entry | `BulkAddDialog` (`dialogs.py`) | Needs >1500 px; stale-row capture after deletion; column explosion at scale | ◐ | [ST-UI-012](12-findings-register.md) |
| B3 | Edit classes | Modify/inspect | `EditClassesDialog` | Header shows raw key `labels.targets` (all langs); immediate mutation, no cancel; fallback branch wipes placement | ✅ | [ST-UI-011](12-findings-register.md) |
| B4 | Delete class | Remove a course | `app.py:2592` / `2798` (two paths) | Inconsistent confirmation across delete paths | ✅ | [ST-UI-014](12-findings-register.md) |
| B5 | Import from Excel | Bulk load roster | File → `_import_from_excel` (`app.py:4471`) | **Always crashes on success**; blank joint-group merges/deletes; numeric-cell crash; drops spaced IDs; ignores `required_room_type` | ✅ | [ST-FUNC-001](12-findings-register.md#st-func-001)…003, 009, 010 |
| B6 | Generate template | Get a blank workbook | File → `_generate_excel_template` | Round-trips to **2 classes from 5** ([ST-FUNC-002]) | ✅ | [ST-FUNC-002](12-findings-register.md#st-func-002) |

## C. Scheduling (the engine)

| # | Feature | User goal | Entry point → handler | Failure states | Test | Findings |
|---|---|---|---|---|---|---|
| C1 | Auto-place one class | Place a single class | `workflow.auto_place` → `optimized_auto_place` | Ghost-day placement; no valid-day guard on commit | ✅ | [ST-SCHED-003](12-findings-register.md#st-sched-003) |
| C2 | Schedule new classes | Place added classes | `workflow.schedule_new_classes` | Leaks half-added class on optimizer exception | ✅ | [ST-DATA-011](12-findings-register.md) |
| C3 | Full reschedule (Standard) | Regenerate everything | Ctrl+R → `RescheduleDialog` → `_do_reschedule` → `workflow.reschedule(use_cpsat=False)` | Hard-violations in raw output; super-linear/slow; non-deterministic; UI freezes; negative counts | ✅ | [ST-SCHED-001](12-findings-register.md#st-sched-001), [ST-PERF-001](12-findings-register.md#st-perf-001), [ST-SCHED-013](12-findings-register.md#st-sched-013) |
| C4 | Full reschedule (Deep/CP-SAT) | Best-effort solve | same dialog, `use_cpsat=True` | Availability only at start slot; ignores soft/same_day/improve_only protection; no gain at scale in 5 s | ✅ | [ST-SCHED-005](12-findings-register.md#st-sched-005), [ST-SCHED-006](12-findings-register.md) |
| C5 | Optimization goals | Tune the objective | `RescheduleDialog` goals panel (6 sliders) | Defaults ≠ DEFAULT_WEIGHTS; opening the panel clobbers learned weights | ✅ | [ST-SCHED-013](12-findings-register.md#st-sched-013) note |
| C6 | Conflict negotiation | Explain unplaceable | `ConstraintNegotiator.negotiate_class` | "Move conflicting class" suggestions never emit; misleading global-infeasibility explanation | ◐ | [ST-SCHED-011](12-findings-register.md), [ST-SCHED-014](12-findings-register.md) |
| C7 | Auto-negotiation on refresh | Passive suggestions | `_run_auto_negotiation` (refresh tail) | Heavy analysis every refresh; mutates live constraints w/o try/finally | ✅ | [ST-PERF-007](12-findings-register.md), [ST-PERF-003](12-findings-register.md#st-perf-003) |

## D. Manual timetable editing

| # | Feature | Entry point → handler | Failure states | Test | Findings |
|---|---|---|---|---|---|
| D1 | Drag-move placed class | `LessonItem` → `_start_drag_gfx` (`app.py:3304`) | Multi-select drag moves only primary; validity checks use deprecated weaker validator | ◐ | [ST-DATA-010](12-findings-register.md), [ST-ARCH-004](12-findings-register.md) |
| D2 | Drag-place from Unplaced | `_start_drag_unplaced` (`app.py:3396`, no undo push) | Pops an unrelated undo entry on drop | ✅ | [ST-DATA-009](12-findings-register.md) |
| D3 | Drag-unplace | drop on Unplaced tab (`app.py:604/682`) | — | ◐ | — |
| D4 | Empty-slot add/place | `EmptySlotItem` double-click / context menu | Open-Slots rows show clickable cursor but no handler | ✅ | [ST-UI-017](12-findings-register.md) |
| D5 | Undo / redo | `_push_undo`/`_undo` (`app.py:1767`) | Covers **only** `state['classes']`; setup edits irreversible; history gaps | ✅ | [ST-DATA-009](12-findings-register.md), [ST-ARCH-012](12-findings-register.md) |
| D6 | Pin / protection | class fields | Pins bypass validation on commit | ✅ | [ST-SCHED-002](12-findings-register.md#st-sched-002) |

## E. Views & analytics

| # | Feature | Entry point | Failure states | Test | Findings |
|---|---|---|---|---|---|
| E1 | By-Classroom / Group / Lecturer views | Tabs 0–2 (`renderer.py` `TimetableView`) | **Silently hides conflicting lessons**; contrast failures; color-only encoding | ✅ | [ST-UI-001](12-findings-register.md#st-ui-001), 005, 006 |
| E2 | Show Everything (matrix) | Tab 3 | Same silent-overwrite; no jump-to-year; Ctrl+C ok here | ✅ | [ST-UI-001](12-findings-register.md#st-ui-001) |
| E3 | Dashboard (Kalite Paneli) | Tab 4 | Room metrics always 0; count disagreement; Ctrl+C → IndexError | ✅ | [ST-UI-003](12-findings-register.md#st-ui-003), [ST-FUNC-008](12-findings-register.md) |
| E4 | Open Slots panel | Sidebar page 1 | Rebuilds hundreds of widgets each refresh; false click affordance | ✅ | [ST-PERF-006](12-findings-register.md), [ST-UI-017](12-findings-register.md) |
| E5 | Unplaced panel | Sidebar page 2 | No count badge / sort / bulk-place | ◐ | [ST-UI-020](12-findings-register.md) |
| E6 | Warning log | bottom panel | Unbounded duplicate spam, memory leak, unescaped HTML | ✅ | [ST-PERF-003](12-findings-register.md#st-perf-003), [ST-UI-007](12-findings-register.md) |
| E7 | Zoom (25–300%) | zoom bar / Ctrl+Wheel | — | ◐ | — |

## F. Export & reporting

| # | Feature | Entry point → handler | Failure states | Test | Findings |
|---|---|---|---|---|---|
| F1 | Export PDF | `_export_to_pdf` → `exporter.export_schedule('pdf')` | **Turkish letters → boxes**; aborts on reportlab-tag names; drops off-grid classes | ✅ | [ST-FUNC-004](12-findings-register.md#st-func-004), [ST-FUNC-013](12-findings-register.md) |
| F2 | Export Excel | `_export_to_excel` → `app.py._write_excel` (parallel engine) | Crashes on `/ \ : ? * [ ]` names | ✅ | [ST-FUNC-005](12-findings-register.md#st-func-005), [ST-ARCH-003](12-findings-register.md) |
| F3 | Export CSV | `app.py:2290` inline | Leaks day keys; locale encoding crash; formula injection | ✅ | [ST-FUNC-006](12-findings-register.md), [ST-UI-008](12-findings-register.md) |
| F4 | `data_io/exporter` xlsx/csv | (unreachable from UI) | Dead code; drifted from `_write_excel` | ✅ | [ST-ARCH-003](12-findings-register.md) |

## G. Persistence & session

| # | Feature | Entry point → handler | Failure states | Test | Findings |
|---|---|---|---|---|---|
| G1 | Save / Save As | File → `_do_save` → `storage.save_encrypted` | `.tmp` race; no fsync | ✅ | [ST-DATA-013](12-findings-register.md) |
| G2 | Open | File → `open_file` → `storage.load_encrypted` | ASCII JSON mis-routed to Fernet (fails); corrupt → silent fresh state | ✅ | [ST-FUNC-007](12-findings-register.md), [ST-DATA-014](12-findings-register.md) |
| G3 | Autosave | every `refresh_grid` → `_auto_save` | Full encrypted rewrite each call; swallows all errors | ✅ | [ST-PERF-002](12-findings-register.md#st-perf-002), [ST-DATA-005](12-findings-register.md) |
| G4 | Key management | `storage._load_or_create_key` | Short/corrupt key silently regenerated → all saves orphaned | ✅ | [ST-DATA-001](12-findings-register.md#st-data-001) |
| G5 | Learning persistence | `FeedbackLogger`/`PreferenceLearner` | O(n²) append; corrupt log silently wiped | ✅ | [ST-PERF-005](12-findings-register.md#st-perf-005), [ST-DATA-002](12-findings-register.md) |

## H. Onboarding, help, system

| # | Feature | Entry point | Failure states | Test | Findings |
|---|---|---|---|---|---|
| H1 | First-run language gate | `run_language_gate` | Cancel → permanent English default | ◐ | [ST-UI-020](12-findings-register.md) |
| H2 | Tutorial (33 steps) | `FirstRunController` (QTimer) | Auto-fires over empty dataset; heavy | ✅ | [ST-UI-016](12-findings-register.md) |
| H3 | Language switch | Language menu | Flag-only entry; in-place retranslation list is hand-maintained | ◐ | [ST-UI-020](12-findings-register.md) |
| H4 | Bug / crash report | `bug_report.py` (mailto) | Dark theme in light app; leaks username paths | ✅ | [ST-UI-018](12-findings-register.md), [ST-SEC-008](12-findings-register.md) |
| H5 | Crash recovery | `sys.excepthook` | App continues after arbitrary exceptions | ◐ | — |

## I. Dead / unreachable (exists in code, not reachable via UI)

Confirmed unreachable but shipped ([ST-ARCH-011](12-findings-register.md)):

- **Legacy solver family**: `logic.reschedule_all`, `batch_schedule`,
  `auto_place_class`, `cascade_relocate`, `find_conflicting_classes`,
  `analyze_conflict_graph`, `analyze_constraint_propagation` — superseded by the
  `optimized_*` path; still carry constraint holes
  ([ST-SCHED-007](12-findings-register.md#st-sched-007)).
- **Second export engine**: `data_io/exporter._export_excel` / `_export_csv`
  (only PDF is called) ([ST-ARCH-003](12-findings-register.md)).
- **Tier UI**: `UpgradeDialog`, `FeatureGateWidget`, upgrade button/banner,
  `_open_upgrade_page` — dormant (institutional tier, empty pricing URL).
- **Dialogs**: `WarningsDialog`, `OpenSlotsDialog` (superseded by live panels).
- **Online-build residue**: `SchedulerApp(session=, server_url=)` params,
  `_offline_banner`, `storage.autosave_path()`.
- **Optimizer**: `neighbor_impact_penalty` term (always 0.0,
  [ST-SCHED-015](12-findings-register.md)), several unreferenced scorer/analytics
  methods.

---

The categories above are exercised in detail across the stress-test result
documents: functional workflows in [04](04-functional-stress-test.md), the engine
in [05](05-scheduling-engine-stress-test.md), performance in
[06](06-performance-audit.md), state integrity in [07](07-data-state-reliability.md),
and error/edge behavior in [08](08-error-edge-case-audit.md).
