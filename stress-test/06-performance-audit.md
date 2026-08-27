# 06 — Performance Audit

Part of the [DERSİS stress-test audit](00-README.md). Measured, not guessed —
every number below was produced by a probe in [`tests/`](tests/) with
`time.perf_counter` / `tracemalloc` on the audit machine. Findings are `ST-PERF-*`
([register](12-findings-register.md)). Scheduling-solver scaling is detailed in
[05 §5](05-scheduling-engine-stress-test.md#5-scalability--algorithmic-cliffs);
this document covers the whole application's runtime behavior.

**Headline.** The app is not slow because of the dataset being small today — it is
slow by construction. Three compounding costs sit on the **single UI thread**:
(1) a **super-linear solver** with no cancellation, (2) a **full encrypted
state rewrite on every screen refresh**, and (3) a **warning panel that leaks
memory and rebuilds O(n²) HTML**. At a realistic 80-class department the app is
sluggish; at 250 it is painful; at 600 it does not finish.

---

## 1. Frontend / UI thread

### 1.1 The refresh cost (ST-PERF-002)

`refresh_grid` runs after essentially every user action and does three expensive
things: rebuild the whole `QGraphicsScene`, re-run the warnings/negotiation
analysis, and call `_auto_save` (a full decrypt-modify-encrypt-write of the
settings container).

| State | `refresh_grid` | of which `_auto_save` | of which `_refresh_warnings` |
|---|---|---|---|
| normal (80 cls) | 0.65 s | 16.8 ms (74 KB egu) | 485 ms |
| large (250 cls) | 2.5–4.7 s | 33.6 ms (232 KB egu) | 4 480 ms |

So a single click on a 250-class schedule can block the UI for **2.5–4.7
seconds**, and the dominant cost is the warnings pass, not rendering. Every
selection change, drag, add, or move pays this. → [ST-PERF-002](12-findings-register.md#st-perf-002),
[ST-UI-009](12-findings-register.md).

### 1.2 Memory leak under repeated refresh (ST-PERF-003)

Driving `refresh_grid` 12× on the large state
([`probe_autosave_and_refresh_perf.py`](tests/probe_autosave_and_refresh_perf.py)):

| Metric | Start | After 12 refreshes |
|---|---|---|
| refresh time | 2 081 ms | 4 816 ms (**2.3×**) |
| `warning_log._messages` | 138 | 1 656 (**+138 each refresh, never cleared**) |
| process RSS | 662 MB | 1 142 MB (**+480 MB**) |
| settings egu on disk | 231 793 B | 231 793 B (constant) |

The warnings list is appended to (never reset) and the panel rebuilds full HTML
from the entire list each time — so both time and memory grow without bound
across a working session, while the on-disk data does not. In a long editing
session this is a slow-motion memory exhaustion.
→ [ST-PERF-003](12-findings-register.md#st-perf-003).

### 1.3 Widget churn (ST-PERF-006)

The Open-Slots and Unplaced side panels rebuild **every row widget** on each
refresh and each selection change (359 widgets rebuilt on the large state). The
warnings pass dominates refresh at 4.5 s on 250 classes. None of this is
incremental. → [ST-PERF-006](12-findings-register.md).

### 1.4 Bundle / startup

Not a web app, so no JS bundle. Cold construction is healthy: Qt init 0.04 s +
`scheduler_app` import 0.75 s + main-window construct 0.17 s ≈ **1.1 s** headless
(see [`tests/smoke_offscreen_launch.py`](tests/smoke_offscreen_launch.py)). The
20 785-line translation module imports as one dict — acceptable. Startup is
**not** a problem area.

## 2. Backend / engine

### 2.1 Solver scaling (ST-PERF-001)

The production reschedule is **super-linear** (`t ≈ 0.0135·n^1.77`), synchronous
on the UI thread, and uncancellable:

| Classes | Single-restart | Full multi-start (default) |
|---|---|---|
| 25 | 5.8 s | ~7–17 s |
| 80 | 25.4 s | **121.7 s** |
| 250 | ~30 s | **132.6 s** |
| 600 | timeout >55 s | — |
| 1200 | timeout / RecursionError | — |

Two cliffs (greedy 100 000-iteration budget already exhausted at 25 classes; and
recursion depth = flexible-class count → `RecursionError` past ~1000). Full detail
and the CSV in [05 §5](05-scheduling-engine-stress-test.md#5-scalability--algorithmic-cliffs).
→ [ST-PERF-001](12-findings-register.md#st-perf-001), [ST-PERF-004](12-findings-register.md),
[ST-PERF-008](12-findings-register.md), [ST-SCHED-012](12-findings-register.md).

### 2.2 Wrapper overhead (ST-PERF-007)

`SchedulingWorkflow.reschedule` runs a **second** expensive pass —
`negotiate_after_optimization` — whenever any class is unplaced. Measured: the raw
optimizer took 7.6 s on `small` but the full workflow wrapper took **17.7 s**
(+10.1 s), almost entirely the negotiation pass plus analytics. Since most
realistic instances have some unplaced classes, this overhead is the common case,
not the exception. → [ST-PERF-007](12-findings-register.md).

### 2.3 Progress & cancellation

The greedy construction phase emits **no** progress callbacks and has **no** time
limit; progress only appears during LNS (every 10 iters) and CP-SAT polling. There
is no cancel button — the only bound observed was the audit's own hard kill. On a
frozen UI thread the window cannot even be closed cleanly mid-solve.

## 3. Persistence performance

- **Encrypt/decrypt** is cheap and scales linearly with state size: tiny 5-class
  save 2.2 ms / load 19 ms; large 250-class save 8 ms / load 18 ms; egu size
  tracks JSON size (~1.4×). AES-GCM is not a bottleneck — the problem is doing a
  **full** rewrite on every refresh (§1.1), not the crypto itself.
- **Feedback logging is O(n²)** ([ST-PERF-005](12-findings-register.md#st-perf-005)):
  each append rewrites the whole encrypted log. Per-append grew 2.55 ms → 99.9 ms
  from 1 → 2000 entries; **2000 appends cumulatively took 108 s**; a 2000-entry
  log is 905 KB. `PreferenceLearner.learn()` re-reads and retrains on the full log
  every call (17 ms → 78 ms). Over a heavy-use institutional deployment this log
  grows unbounded and every save gets slower.

## 4. Database

Not applicable — DERSİS has no database. Persistence is flat encrypted files
(§3). The "N+1 / indexes / joins" checklist maps to **repeated full-file
read-modify-write**, which is exactly the ST-PERF-002 / ST-PERF-005 pattern:
whole-container rewrites where an append or delta would do.

## 5. Quantified summary

| Area | Metric | Value | Finding |
|---|---|---|---|
| Reschedule (80 cls, default) | wall | 121.7 s | ST-PERF-001 |
| Reschedule scaling | exponent | n^1.77 | ST-PERF-001 |
| Single refresh (250 cls) | wall | 2.5–4.7 s | ST-PERF-002 |
| 12× refresh | RSS growth | +480 MB | ST-PERF-003 |
| Warnings pass (250 cls) | wall | 4 480 ms | ST-PERF-006 |
| Greedy budget exhaustion | onset | 25 classes | ST-PERF-004 |
| Feedback log (2000 appends) | cumulative | 108 s | ST-PERF-005 |
| Solver memory (normal) | peak | 7.8 MiB | (not a bottleneck) |
| Cold startup (headless) | wall | 1.1 s | (healthy) |

**The single highest-leverage performance change** is to move solving off the UI
thread with progress + cancel, and stop rewriting the full encrypted state on
every refresh — those two dwarf everything else and are sequenced in
[roadmap Phase 2](14-implementation-roadmap.md#phase-2--performance-foundations).
