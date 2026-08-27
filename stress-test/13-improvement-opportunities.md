# 13 — Improvement Opportunities

Part of the [DERSİS stress-test audit](00-README.md). The
[findings register](12-findings-register.md) says what is *wrong*; this document
groups the work by *kind* (fixes / hardening / performance / UX / architecture /
feature) so trade-offs are visible. Sequencing is in the
[roadmap](14-implementation-roadmap.md).

Every item below traces to at least one finding ID. Nothing here is speculative
"best practice" — each solves an observed DERSİS problem. Effort: S ≤ ½day ·
M ≤ 2days · L ≤ 1wk · XL > 1wk.

---

## Fixes — currently broken or unreliable

These are defects a realistic user will hit. Highest value per unit effort.

| Fix | Findings | Effort | Why now |
|---|---|---|---|
| Repair Excel-import success handler (`refresh_grid`/`_update_status`) + wrap in try/except with rollback | [ST-FUNC-001](12-findings-register.md#st-func-001) | S | The flagship import is 100% broken and corrupts state before crashing |
| Guard blank joint-group cells (`pd.isna`) | [ST-FUNC-002](12-findings-register.md#st-func-002) | S | The app's own template loses 3/5 classes silently |
| Per-row numeric parsing with error reporting | [ST-FUNC-003](12-findings-register.md) | S | One bad cell aborts the whole import |
| Register a Unicode TTF for PDF export | [ST-FUNC-004](12-findings-register.md#st-func-004) | S | Turkish letters → boxes in the printed timetable |
| Sanitize xlsx sheet titles | [ST-FUNC-005](12-findings-register.md#st-func-005) | S | Legal names crash export |
| CSV: UTF-8 BOM encoding + localized day names + formula-injection guard | [ST-FUNC-006](12-findings-register.md), [ST-UI-008](12-findings-register.md) | S | Crashes on non-TR Windows; leaks keys; injectable |
| Fix Ctrl+C on Dashboard tab | [ST-FUNC-008](12-findings-register.md) | S | IndexError crash |
| Read `placed_classroom` in room analytics | [ST-UI-003](12-findings-register.md#st-ui-003) | S | Dashboard room metrics always zero |
| One canonical placement-count function; clamp non-negative | [ST-UI-002](12-findings-register.md#st-ui-002) | S | Counters disagree; "-5 yerleşmemiş" shown |
| Render conflicting lessons stacked + "ÇAKIŞMA" chip | [ST-UI-001](12-findings-register.md#st-ui-001) | M | The grid hides real double-bookings |
| Add missing translation keys + CI key-coverage check | [ST-UI-011](12-findings-register.md) | S | Raw `labels.targets` visible in all 22 languages |
| Escape names in warning-panel HTML | [ST-UI-007](12-findings-register.md) | S | Markup injection / spoofing |
| Fix CI branch trigger `master`→`main` | [ST-ARCH-002](12-findings-register.md) | S | CI never runs at all |

## Hardening — works, but fails under realistic stress

| Hardening | Findings | Effort |
|---|---|---|
| Distinguish "absent" vs "malformed" `key.bin`; fail loudly + offer backup restore; never auto-regenerate | [ST-DATA-001](12-findings-register.md#st-data-001) | M |
| Stop rebuilding settings from `{}` on corrupt load; back up the bad file, warn, don't overwrite | [ST-DATA-014](12-findings-register.md), [ST-DATA-005](12-findings-register.md) | M |
| Surface (not swallow) `_auto_save` failures | [ST-DATA-005](12-findings-register.md) | S |
| Append-only / rotated feedback log; don't wipe on corrupt read | [ST-DATA-002](12-findings-register.md), [ST-PERF-005](12-findings-register.md#st-perf-005) | M |
| Reconcile placements on `SetupDialog` OK (unplace affected, warn with counts) | [ST-DATA-004](12-findings-register.md#st-data-004) | M |
| Guard `slot_index`; drop/flag stale constraints during normalization | [ST-DATA-003](12-findings-register.md#st-data-003), [ST-SCHED-004](12-findings-register.md#st-sched-004) | M |
| Validate pins on commit; flag infeasible pins as conflicts | [ST-SCHED-002](12-findings-register.md#st-sched-002) | M |
| Single-instance guard (`QLockFile`) that focuses the existing window | [ST-DATA-012](12-findings-register.md) | S |
| Internal rollback in `schedule_new_classes` | [ST-DATA-011](12-findings-register.md) | S |
| try/finally around negotiation impact estimators that mutate live constraints | [ST-PERF-007](12-findings-register.md), arch | S |
| Delete the runtime `pip install` fallbacks; fail with a clear message | [ST-SEC-005](12-findings-register.md) | S |

## Performance improvements

| Improvement | Findings | Effort |
|---|---|---|
| Move solving to a worker thread/process with progress + **cancel** | [ST-PERF-001](12-findings-register.md#st-perf-001) | L |
| Bound the greedy construction phase in wall-clock; emit progress | [ST-PERF-004](12-findings-register.md), [ST-PERF-008](12-findings-register.md) | M |
| Convert greedy recursion to iteration (kill the ~1000-class RecursionError) | [ST-SCHED-012](12-findings-register.md) | M |
| Debounce/defer autosave; save deltas, not full container each refresh | [ST-PERF-002](12-findings-register.md#st-perf-002) | M |
| Rebuild warnings from current state each refresh (fix leak + O(n²) HTML) | [ST-PERF-003](12-findings-register.md#st-perf-003), [ST-PERF-006](12-findings-register.md) | M |
| Incremental occupancy indexes to cut per-candidate O(n) scans | [ST-PERF-001](12-findings-register.md#st-perf-001) | L |
| Skip side-panel/negotiation recompute on pure selection changes | [ST-UI-009](12-findings-register.md), [ST-PERF-007](12-findings-register.md) | M |
| Seed the RNG (also a correctness/reproducibility win) | [ST-SCHED-013](12-findings-register.md#st-sched-013) | S |

## UX improvements

Grouped from the [UI/UX audit](09-ui-ux-audit.md); the five structured proposals
P1–P5 live there. Highest-leverage:

| Improvement | Findings | Effort |
|---|---|---|
| **P1** Conflict-aware cells (split cell + chip + warning) | [ST-UI-001](12-findings-register.md#st-ui-001) | M |
| **P2** One placement vocabulary across status bar/dashboard/results | [ST-UI-002](12-findings-register.md#st-ui-002) | S |
| **P3** Year-color legend + redundant encodings + contrast fixes | [ST-UI-005](12-findings-register.md), [ST-UI-006](12-findings-register.md) | M |
| **P4** Keyboard grid navigation + accessible names | [ST-UI-004](12-findings-register.md) | L |
| **P5** Responsive shell (proportional splitter, collapsible sidebar, icon tabs) | [ST-UI-013](12-findings-register.md) | M |
| Time slots: structured entry with format validation instead of free text | [ST-UI-014](12-findings-register.md) | M |
| Show all form errors + inline field markers; QFormLayout alignment | [ST-UI-015](12-findings-register.md) | M |
| PlaceClass: show negotiator reasons on the 0-options case | [ST-UI-015](12-findings-register.md) | M |
| Warning log: de-dup with counts, timestamps, click-to-navigate | [ST-UI-019](12-findings-register.md) | S |
| Light-theme the bug/crash dialogs; standardize button order | [ST-UI-018](12-findings-register.md) | S |
| Empty-state guidance (two CTAs); shorten/seed the 33-step tutorial | [ST-UI-016](12-findings-register.md), [ST-UI-020](12-findings-register.md) | M |

## Architecture improvements

Extraction seams and guardrails — **not** rewrites. Each is independently
shippable (see [10](10-code-architecture-audit.md)).

| Improvement | Findings | Effort |
|---|---|---|
| Unify to one hard-constraint validator; delete the other three | [ST-ARCH-004](12-findings-register.md), [ST-SCHED-007](12-findings-register.md#st-sched-007) | L |
| Unify export: promote `_write_excel` into `data_io/exporter`, delete the dead engine, give `export_schedule` its `mode` param | [ST-ARCH-003](12-findings-register.md) | M |
| Extract `SessionStore` (persistence + undo) out of `app.py` → makes the silent-wipe path unit-testable | [ST-ARCH-005](12-findings-register.md), [ST-ARCH-006](12-findings-register.md) | L |
| Move `translations`/`day_keys`/formatters to a leaf package; kill the core→ui layering violations & import cycles | [ST-ARCH-009](12-findings-register.md), [ST-ARCH-010](12-findings-register.md) | M |
| Delete the dead legacy solver family and other unreachable symbols | [ST-ARCH-011](12-findings-register.md), [ST-SCHED-007](12-findings-register.md#st-sched-007) | M |
| `TypedDict` for state/class dicts; annotate ~30 seam functions; mypy on `core/` | [ST-ARCH-013](12-findings-register.md) | M |
| Full-state undo snapshots (not classes-only) | [ST-ARCH-012](12-findings-register.md) | M |

## Feature improvements

Only extensions that solve an identified user problem — **no bloat**.

- **"Why can't this be placed?" panel** — the negotiator already computes
  per-class reasons; surface them at the point of failure (PlaceClass 0-options
  case, and the oversubscribed-instance global reason). Solves the observed
  dead-end in [ST-SCHED-014](12-findings-register.md) / [ST-UI-015](12-findings-register.md).
  Effort M.
- **"Dropped classes" report after reschedule** — expose
  `apply_reschedule`'s currently-discarded `rejected` list so users learn which
  classes the solver silently unplaced ([ST-SCHED-001](12-findings-register.md#st-sched-001),
  [ST-SCHED-005](12-findings-register.md#st-sched-005)). Effort S.
- **Import "replace vs merge" choice + preview** — the import currently always
  appends via a broken path; a preview + explicit mode prevents silent
  duplication and the `'nan'` merge surprise ([ST-FUNC-002](12-findings-register.md#st-func-002)).
  Effort M.
- **Deterministic + "randomize" toggle** — once the RNG is seeded, let users
  reproduce a good run or deliberately re-roll ([ST-SCHED-013](12-findings-register.md#st-sched-013)).
  Effort S.

Explicitly **not** recommended: new solver algorithms, cloud/sync features, a
plugin system, theming — none addresses an observed user problem and all would add
surface area to an app that first needs its existing surface made correct.
