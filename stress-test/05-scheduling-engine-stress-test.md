# 05 — Scheduling Engine Stress Test

Part of the [DERSİS stress-test audit](00-README.md). The timetabling engine is
the heart of the product and gets dedicated, adversarial testing here: constraint
correctness, infeasibility handling, determinism, and scalability. Findings are
`ST-SCHED-*` and the performance cliff is `ST-PERF-001`
([register](12-findings-register.md)).

**Method.** All results come from running the **production path**
(`SchedulingWorkflow.reschedule → optimized_reschedule_all → ScheduleOptimizer →
apply_reschedule`) headlessly against the deterministic
[fixture generator](tests/_fixtures/dataset_gen.py), checked by an **independent
invariant oracle** written for this audit
([`tests/schedule_oracle.py`](tests/schedule_oracle.py)) that re-derives every
hard-constraint violation from scratch. Raw data:
[`evidence/scheduler_benchmark.csv`](evidence/scheduler_benchmark.csv),
[`evidence/oracle_tiny_small_normal.json`](evidence/oracle_tiny_small_normal.json),
[`evidence/oracle_large.json`](evidence/oracle_large.json). Long runs were bounded
with hard timeouts (noted where they fired).

---

## 1. The correctness oracle — headline result

The single most important finding of the whole audit: **the production optimizer
produces raw schedules containing hard-constraint violations**, and the commit
step's only defense is to *silently drop* the losing class. When the violations
involve pinned classes, they are committed as-is.

Oracle results on the production `reschedule + apply_reschedule` path (seed 42,
multi-start defaults):

| Preset | Classes | reschedule (s) | Raw placed | **Raw violation-cells** | Dropped at apply | **Committed violations** |
|---|---|---|---|---|---|---|
| tiny | 5 | 1.7 | 5 | 0 | 0 | 0 ✅ |
| small | 25 | 47.1 | 21 | **18** (6 room + 6 lecturer + 6 group) | 1 | 0 (clean only via drops) |
| normal | 80 | 121.7 | 76 | **60** (24 room + 12 lecturer + 24 group) | 9 | 0 (clean only via drops) |
| large | 250 | 132.6 | 232 | 16 | 0 | **16** (3 availability + 7 capacity + 2 room + 4 group) |

Concrete raw-violation samples (from the oracle, verbatim):

```
[room_double_book] Ders 11: room 'R001' shared at friday/12:00 by: Ders 11, Ders 23
[room_double_book] Ders 23: room 'R001' shared at friday/13:00 by: Ders 11, Ders 23
[availability]     Ders 22: lecturer 'Lect-002' not available at monday/14:00
[capacity]         Ders 52: room capacity exceeded
```

**Interpretation.**
- On small/normal instances the *committed* schedule is clean **only because
  `apply_reschedule` re-validates and silently unplaces the colliding classes**
  (small dropped 1, normal dropped 9). The user is never told which classes were
  dropped or why — the rejected list is discarded at `app.py:2713`. So the tool
  quietly refuses to place work it appeared to place.
  → [ST-SCHED-001](12-findings-register.md#st-sched-001).
- On the large instance, **16 hard-violation cells survived into the committed
  schedule with 0 rejections** — every one traceable to a **pinned** class, which
  `apply_reschedule` skips validating entirely
  → [ST-SCHED-002](12-findings-register.md#st-sched-002). A deterministic minimal
  repro ([`tests/pinned_infeasible_probe.py`](tests/pinned_infeasible_probe.py)):
  4 mutually/infeasibly pinned classes → **6 committed hard-violations, 0
  rejected**, `ConstraintValidator.check_placement=False` for all 4.

This is compounded by [ST-UI-001](12-findings-register.md#st-ui-001): the grid
then *hides* one of the two colliding lessons, so a committed double-booking is
invisible on screen too.

## 2. Constraint enforcement correctness

Probes: [`ghost_day_and_stale_time_probe.py`](tests/ghost_day_and_stale_time_probe.py), [`legacy_solver_probe.py`](tests/legacy_solver_probe.py), [`validator_integrity_probe.py`](tests/validator_integrity_probe.py), [`probe_malformed_classes.py`](tests/probe_malformed_classes.py) — the 7 targeted tasks below (see [test index](tests/README.md)).

| # | Constraint behavior | Result | Finding |
|---|---|---|---|
| 1 | `allowed_days=['saturday']` on a Mon–Fri grid | 8/8 candidates off-grid; `auto_place` commits `placed_day='saturday'` (not in `state['days']`) | [ST-SCHED-003](12-findings-register.md#st-sched-003) |
| 2 | `allowed_times=['20:00']` not in `slots` | `ValueError: '20:00' is not in list` at `logic.py:18` — crash | [ST-SCHED-004](12-findings-register.md#st-sched-004) |
| 3 | Fully-unavailable lecturer, legacy solvers | `reschedule_all`/`auto_place_class`/`batch_schedule` all place at monday/09:00 (availability ignored); optimized path correctly leaves unplaced | [ST-SCHED-007](12-findings-register.md#st-sched-007) |
| 4 | `protection=locked` class, legacy `reschedule_all` | moved friday/11:00 → monday/09:00; optimized path keeps it fixed | [ST-SCHED-007](12-findings-register.md#st-sched-007) |
| 5 | dur-2 class, lecturer available 09:00 not 10:00 | `check_placement=False` **but** `find_conflicts=[]` (0 reasons) — conflict UI can't explain | [ST-SCHED-009](12-findings-register.md) |
| 6 | Two classes co-located in R1/mon/09:00, remove one | occupancy set collapses to `{}`; a third class's `check_placement` flips `False→True` — ref-count-free sets corrupt validity | [ST-SCHED-010](12-findings-register.md) |
| 7 | Partial `lecturer_availability` `{'allowed_days':['monday']}` | `KeyError 'excluded_days'` on `lecturer_available_at`, availability filter, and `check_placement` | [ST-SCHED-008](12-findings-register.md) |

The root structural cause is that **hard-constraint validation exists in four
divergent implementations** ([ST-ARCH-004](12-findings-register.md)) and the
CP-SAT model re-encodes the rules independently
([ST-SCHED-005](12-findings-register.md#st-sched-005),
[ST-SCHED-006](12-findings-register.md)) — so every path enforces a slightly
different subset. The drag-drop path even uses the *deprecated* validator that
skips availability.

## 3. CP-SAT "deep" mode

Probes: [`probe_cpsat_protection_semantics.py`](tests/probe_cpsat_protection_semantics.py), [`probe_cpsat_midblock_availability.py`](tests/probe_cpsat_midblock_availability.py).

- **Availability checked only at the start slot** — a duration-3 class with a
  lecturer available only at its start hour is placed spanning 2 unavailable
  hours; `apply_reschedule` then returns `rejected=['BIG3H']`, but `result.placed`
  had reported it placed, and the UI discards the rejected list, so it silently
  ends `placed=False`. → [ST-SCHED-005](12-findings-register.md#st-sched-005).
- **Protection semantics incomplete** — only `LOCKED` is respected; `soft`,
  `same_day`, and `improve_only` classes were all moved (14:00→10:00), and the
  move did **not** appear in `changes[]`, so it is invisible to the impact/undo
  machinery. → [ST-SCHED-006](12-findings-register.md).
- **No benefit at scale** — with a 5 s limit, CP-SAT lifted `small` from 23→25
  placed but on `normal` produced no adopted improvement (`cpsat_used=False`),
  wall 24 s.

## 4. Determinism

Probes: [`probe_optimizer_determinism.py`](tests/probe_optimizer_determinism.py) and
`scheduler_benchmark.py` determinism cells. Five identical-input `optimize()` runs (30 classes,
CP-SAT+parallel disabled): **5 distinct placements**, scores
`[12.46, 12.10, 8.70, 8.82, 7.34]`, spread 5.12, population stdev 2.03 — roughly
**41% quality difference** between best and worst. Placed-count is stable but
quality is not. Cause: LNS and multi-start use the **unseeded global `random`**.
→ [ST-SCHED-013](12-findings-register.md#st-sched-013). Consequence: regenerating
a schedule gives a different, sometimes markedly worse, result with no way to
reproduce a prior good run.

## 5. Scalability & algorithmic cliffs

Full data in [`evidence/scheduler_benchmark.csv`](evidence/scheduler_benchmark.csv).
Single-restart production path, density 0.3 (bounded to keep runs finite):

| Preset | Classes | Wall (s) | Placed | Greedy iters | Budget exhausted? |
|---|---|---|---|---|---|
| tiny | 5 | 0.20 | 5/5 | 6 | no |
| small | 25 | 5.80 | 23/25 | **100 000** | **yes** |
| normal | 80 | 25.4 | 76/80 | 100 000 | yes |
| large | 250 | ~30 (single-restart) | 231/250 | 100 000 | yes |
| very_large | 600 | **TIMEOUT >55 s** | — | — | — |
| pathological | 1200 | **TIMEOUT >60 s / RecursionError** | — | — | — |

With the **full multi-start defaults** the same instances take far longer:
`normal` = **121.7 s**, `large` = **132.6 s** (§1 table).

**Scaling fit:** construction-only `t ≈ 0.0047·n^1.69`; single-restart production
`t ≈ 0.0135·n^1.77`. This is markedly **super-linear**
→ [ST-PERF-001](12-findings-register.md#st-perf-001). Two distinct cliffs:

1. **Greedy budget cliff** — the recursive greedy construction exhausts its
   **100 000-iteration cap already at 25 classes** (moderate density): 5 classes
   use 6 iterations, 25 classes at density ≥0.3 hit 100 000 (a ~4-order-of-
   magnitude jump across one density step). → [ST-PERF-004](12-findings-register.md).
   The greedy phase has **no wall-clock bound** and **emits no progress**, so a
   single restart overran a configured 25 s budget to 55 s
   → [ST-PERF-008](12-findings-register.md).
2. **Recursion cliff** — greedy recursion depth equals the flexible-class count;
   with the interpreter limit at 1000, n=980 succeeds and n≥1100 raises
   `RecursionError`. The pathological preset (1200 classes) crashes.
   → [ST-SCHED-012](12-findings-register.md).

**Memory is not the problem** — peak was 6.9 MiB (small) / 7.8 MiB (normal) via
`tracemalloc`. The cliff is pure CPU time, on the UI thread, with no cancellation.

> Non-monotonic aside: higher constraint density sometimes runs *faster* while
> placing *fewer* classes (e.g. `normal` d0.7 = 24.9 s placing 61/80 vs d0.5 =
> 45.4 s placing 75/80) — the solver gives up sooner when the problem is tighter.

## 6. Infeasibility handling & explanation quality

- **Oversubscribed** (120 classes, 1 lecturer, 1 room, ~240 class-hours vs ~30
  slots): terminates in 33.6 s, places 14/120, and the explanation is a generic
  "relocating flexible classes" for all 106 unplaced — it never names the root
  global constraint (one lecturer / one room). The negotiator even mislabels an
  unplaceable class "ok". → [ST-SCHED-014](12-findings-register.md).
- **Barely feasible** (`normal` at density 0.9): terminates in 5.2 s, places
  52/80, and here the reasons *are* specific ("23 slots occupied", "3 no allowed
  days remain", "2 no room capacity") — so per-class diagnostics work; global
  infeasibility diagnosis does not.

## 7. Dead objective term

`neighbor_impact_penalty` carries weight 4.0 in the objective but
`_neighbor_impact` **always returns 0.0** — 209 calls, 0 non-zero, confirmed inert
(parallel-vs-sequential ranking delta 0.0). A whole scoring dimension the code
advertises is silently disabled. → [ST-SCHED-015](12-findings-register.md).

---

## Summary

The engine is functionally impressive on paper (heuristic multi-start + LNS +
CP-SAT, adaptive strategies, negotiation) but has a **correctness gap at its
core**: it can propose and commit schedules that violate hard constraints, its
commit step hides the fallout by silently dropping classes, and its four parallel
validators disagree. Layered on top are a **super-linear performance cliff on the
UI thread** and **non-determinism**. These are the findings that most directly
answer the brief's question — *"what would create incorrect schedules?"* — and
they dominate [Phase 1](14-implementation-roadmap.md#phase-1--data--correctness)
and [Phase 3](14-implementation-roadmap.md#phase-3--scheduling-engine-hardening)
of the roadmap.

See also: performance depth in [06](06-performance-audit.md); the invisible-
conflict rendering in [09](09-ui-ux-audit.md#timetable-views-by-classroom--group--lecturer);
validator duplication in [10](10-code-architecture-audit.md).
