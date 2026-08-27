# 15 — Final Assessment

Part of the [DERSİS stress-test audit](00-README.md). Scores each dimension 0–10
with the evidence behind it, the ceiling the current state imposes, and the score
reachable after the [roadmap](14-implementation-roadmap.md). Every claim traces to
a finding in the [register](12-findings-register.md).

Scores answer the brief's framing question — *if DERSİS were deployed
institution-wide and heavily used tomorrow, what would fail?* They reflect
**current, as-shipped** behavior at commit `365b24b`, not potential.

---

## Scorecard

| Dimension | Now | After roadmap | One-line basis |
|---|:--:|:--:|---|
| Functional correctness | **3** | 8 | Flagship import 100% broken; template loses 60% of classes |
| Scheduling reliability | **2** | 8 | Optimizer commits hard-constraint violations; pins unvalidated |
| Performance | **3** | 7 | 25–120 s reschedule on the UI thread; unusable > 250 classes |
| Scalability | **2** | 7 | Super-linear + RecursionError past ~1000 classes |
| Data integrity | **3** | 8 | Silent key/settings wipe; orphan-slot crashes; no locking |
| Error resilience | **4** | 8 | Graceful on empty states; crashes/silent-accepts on forced/stale input |
| UI usability | **5** | 8 | Polished but hides conflicts; counters disagree/go negative |
| UI consistency | **5** | 8 | Two export engines, four validators, dark dialogs in a light app |
| Accessibility | **2** | 6 | Zero a11y API; mouse-only grid; most in-cell text fails WCAG AA |
| Architecture | **3** | 7 | Two 0.00-MI god objects; 19 layer violations; 11 import cycles |
| Maintainability | **3** | 7 | 10.8% typing; 22 silent excepts on the data path |
| Test quality | **0** | 7 | Zero tests; CI wired to a nonexistent branch |
| Deployment readiness | **2** | 7 | Auto-publishes unvetted "latest"; unsigned; data-loss paths |

**Weighted overall: ~2.8 / 10 now → ~7.4 / 10 after the roadmap.**

---

## Per-dimension detail

### Functional correctness — 3/10
- **Evidence.** Excel import crashes on every success and corrupts state first
  ([ST-FUNC-001](12-findings-register.md#st-func-001)); the app's own template
  re-imports 5 classes as 2 ([ST-FUNC-002](12-findings-register.md#st-func-002));
  PDF renders Turkish as boxes ([ST-FUNC-004](12-findings-register.md#st-func-004));
  xlsx/CSV crash on legal names/locales.
- **Ceiling.** The primary data-entry and output paths — import and PDF — are the
  two a real institution depends on, and both fail.
- **After roadmap.** 8 — the fixes are small (Phase 0), the ceiling then becomes
  edge-case polish.

### Scheduling reliability — 2/10
- **Evidence.** The correctness oracle found the production optimizer commits
  hard-constraint violations (room/lecturer/group double-bookings), masked only by
  silently dropping classes; pinned collisions commit unvalidated
  ([ST-SCHED-001](12-findings-register.md#st-sched-001),
  [ST-SCHED-002](12-findings-register.md#st-sched-002)); four divergent validators;
  non-determinism ([ST-SCHED-013](12-findings-register.md#st-sched-013)).
- **Ceiling.** A timetabling tool whose core output can be silently wrong is at the
  floor for its own category, regardless of how sophisticated the engine looks.
- **After roadmap.** 8 — Phase 3 unifies validation and repairs occupancy
  bookkeeping; CP-SAT gains full-duration availability.

### Performance — 3/10 · Scalability — 2/10
- **Evidence.** `t ≈ 0.0135·n^1.77`; 80-class reschedule 121.7 s, 250 ~132 s, 600
  does not finish; greedy budget exhausted at 25 classes; RecursionError past ~1000;
  a single 250-class refresh blocks the UI 2.5–4.7 s; +480 MB leak over 12 refreshes
  ([ST-PERF-001](12-findings-register.md#st-perf-001)…006).
- **Ceiling.** Everything runs on one UI thread with no cancellation; memory is
  fine but time is not.
- **After roadmap.** 7 — off-thread solving + delta autosave + bounded greedy;
  true parallel/CP-SAT scaling beyond that is a longer effort.

### Data integrity — 3/10
- **Evidence.** Truncated `key.bin` silently regenerated → all saves orphaned;
  corrupt settings silently wiped; orphan-slot crashes; no single-instance lock
  ([ST-DATA-001](12-findings-register.md#st-data-001)…014). Roundtrip fidelity
  itself is good (0 DeepDiff on 250 classes) — the failure is in *failure handling*.
- **After roadmap.** 8 — Phase 1 makes corruption loud and recoverable.

### Error resilience — 4/10
- **Evidence.** Genuinely graceful on empty/degenerate states (no divide-by-zero)
  and on naturally-infeasible placements (left unplaced), but crashes or
  silently-accepts on forced/stale/adversarial input, with no input escaping
  ([08](08-error-edge-case-audit.md)). The bimodality is why it's a 4, not lower.
- **After roadmap.** 8 — boundary validation is a Phase 1 cross-cutting theme.

### UI usability — 5/10 · consistency — 5/10
- **Evidence.** The interface is genuinely polished (clean theme, adaptive zoom,
  tooltips, drag validity overlay) — but the grid **hides conflicting lessons**
  ([ST-UI-001](12-findings-register.md#st-ui-001)), counters disagree and go
  negative ([ST-UI-002](12-findings-register.md#st-ui-002)), and consistency is
  undercut by two export engines, four validators, and dark crash dialogs in a
  light app. The polish keeps it at 5 despite the structural defects.
- **After roadmap.** 8 — conflict cells + one vocabulary + consistency pass.

### Accessibility — 2/10
- **Evidence.** Zero accessibility-API usage; the timetable is custom-painted
  QGraphicsItems with no keyboard path and no screen-reader representation; room
  text 1.55–2.14:1, badges 2.3:1, most in-cell text below WCAG AA
  ([ST-UI-004](12-findings-register.md), [ST-UI-005](12-findings-register.md)).
- **Ceiling.** For a public-sector/education tool this is both a usability and a
  compliance gap.
- **After roadmap.** 6 — keyboard navigation + contrast fixes are achievable;
  full AT parity for a custom canvas is a longer road (hence not 8).

### Architecture — 3/10 · Maintainability — 3/10
- **Evidence.** `app.py` (4 961 LOC) and `dialogs.py` (4 451) both score MI 0.00;
  `CPSATScheduler.solve` CC 105; 19 upward layer violations; 11 import cycles;
  10.8% typing; 22 silent excepts on the data path
  ([10](10-code-architecture-audit.md)).
- **After roadmap.** 7 — the recommended work is *extraction seams*, not rewrites;
  the engine is already clean enough to test headlessly today.

### Test quality — 0/10
- **Evidence.** No tests, no config; CI runs none and its trigger targets a branch
  that does not exist ([ST-ARCH-001](12-findings-register.md#st-arch-001),
  [ST-ARCH-002](12-findings-register.md)). Every Critical finding is exactly what a
  minimal suite would catch.
- **After roadmap.** 7 — the headless core is highly testable; Phase 0/7 build the
  suite. This is the single biggest ROI in the plan.

### Deployment readiness — 2/10
- **Evidence.** CI auto-publishes an unvetted non-prerelease "latest" on every push
  to `main`, no checksum, unsigned installer, users-modify ACL, placeholder AppId,
  plus the data-loss paths above ([ST-SEC-001](12-findings-register.md#st-sec-001),
  003, 004, 007).
- **After roadmap.** 7 — tag-gated signed releases + the correctness/data fixes.

---

## Current deployment readiness: **Internal alpha**

DERSİS is **not** a controlled beta and further from production. It presents as a
finished product — polished UI, four languages, a sophisticated hybrid solver,
installers — but the audit shows the **core promise is unmet**: it produces
schedules that can silently contain hard-constraint violations, hides those
conflicts in the one view users trust, breaks its own flagship import, can
silently and permanently lose all saved data, and ships with zero tests behind CI
that never runs. Any of those alone would block a beta; together they place it at
**Internal alpha** — suitable for a developer or a single tolerant pilot user who
knows the sharp edges, not for institution-wide use.

The encouraging half: none of the top problems is a fundamental design dead-end.
The engine is testable headlessly *today*, the domain model is coherent, the fixes
for the Criticals are mostly small, and the architecture needs *seams*, not a
rewrite. With Phases 0–1 alone (a few weeks) DERSİS would cross into a defensible
**controlled beta**; the full roadmap reaches **production candidate**.

---

## The five highest-leverage changes

Ranked by impact per unit effort, drawing across all findings:

1. **Stand up the test scaffold + fix CI, with the scheduler invariant oracle as
   the centerpiece** ([ST-ARCH-001](12-findings-register.md#st-arch-001),
   [ST-ARCH-002](12-findings-register.md)). Everything else becomes verifiable, and
   the oracle already exists ([`tests/schedule_oracle.py`](tests/schedule_oracle.py)).
   Effort L, unblocks the entire roadmap.

2. **Make the optimizer never commit hard-constraint violations** — unify to one
   validator, fix occupancy bookkeeping, validate pins on commit, and stop silently
   dropping classes ([ST-SCHED-001](12-findings-register.md#st-sched-001),
   [ST-SCHED-002](12-findings-register.md#st-sched-002),
   [ST-ARCH-004](12-findings-register.md)). This restores the product's core promise.

3. **Fix the two broken data workflows and the silent data-loss paths** — the
   Excel-import crash + `'nan'` merge, and the `key.bin`/settings self-destruct
   ([ST-FUNC-001](12-findings-register.md#st-func-001),
   [ST-FUNC-002](12-findings-register.md#st-func-002),
   [ST-DATA-001](12-findings-register.md#st-data-001),
   [ST-DATA-014](12-findings-register.md)). Small fixes, catastrophic consequences
   prevented.

4. **Make conflicts visible and move solving off the UI thread** — render
   overlapping lessons as conflicts instead of hiding them, and give reschedule a
   background worker with progress + cancel
   ([ST-UI-001](12-findings-register.md#st-ui-001),
   [ST-PERF-001](12-findings-register.md#st-perf-001)). Turns an untrustworthy,
   freezing tool into a usable one.

5. **Ship a Unicode PDF font + gate releases behind signed, checksummed tags** —
   Turkish output that is actually readable
   ([ST-FUNC-004](12-findings-register.md#st-func-004)), and users no longer
   receiving unvetted "latest" dev builds ([ST-SEC-001](12-findings-register.md#st-sec-001)).
   Low effort, directly user-facing and trust-critical for a Turkish-first product.
