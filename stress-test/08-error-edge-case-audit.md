# 08 — Error, Edge-Case & Adversarial Audit

Part of the [DERSİS stress-test audit](00-README.md). Boundary values, malformed
and adversarial input, degenerate states, and injection. Findings span
`ST-DATA-*`, `ST-UI-*`, `ST-FUNC-*`, `ST-SEC-*`
([register](12-findings-register.md)). Primary probes:
[`probe_deleted_resources.py`](tests/probe_deleted_resources.py),
[`probe_empty_and_boundary.py`](tests/probe_empty_and_boundary.py),
[`probe_recovery_rollback.py`](tests/probe_recovery_rollback.py), plus the analytics
probes under [`scenarios/`](scenarios/) (see [test index](tests/README.md)).

---

## 1. Empty & degenerate states — mostly safe

Good news first. On a fresh `new_state()` (0 days/slots/classes):
`analytics.compute_all_metrics`, `workflow.reschedule`, and **all three exports**
(csv/xlsx/pdf) succeed with **no divide-by-zero** — the `max(..., 1)` and
`total_capacity == 0` guards hold. Single-day / single-slot / single-room /
single-class instances also behave. Degenerate states are the one area DERSİS
handles gracefully.

## 2. Boundary values

| Case | Behavior | Verdict |
|---|---|---|
| `participants` > all room capacities | Class correctly left **unplaced** | ✅ prevents |
| 0-capacity room + 999 participants | Placed (0 = unlimited, as documented) | ✅ by design |
| `duration > slots` (non-forced) | Left **unplaced** | ✅ prevents |
| `duration > slots` **force-placed** | `analytics.busiest_slots`/`compute_all_metrics` → `IndexError` | ❌ [ST-DATA-007](12-findings-register.md) |
| Class pinned to nonexistent slot | analytics + CSV/XLSX + reschedule crash | ❌ [ST-DATA-006](12-findings-register.md) |
| Class pinned to nonexistent day (valid slot) | 0 crashes | ✅ tolerated |
| `allowed_times=['23:00']` off-grid | reschedule crashes (`ValueError`) | ❌ [ST-SCHED-004](12-findings-register.md#st-sched-004) |
| `allowed_days=['saturday']` off-grid | placed on saturday (ghost day) | ❌ [ST-SCHED-003](12-findings-register.md#st-sched-003) |

The pattern: the engine **prevents** infeasible placements it discovers naturally,
but **crashes** or **silently accepts** whenever a *forced* value (pin, stale
constraint) bypasses the normal candidate-generation guards.

## 3. Malformed input

- **Missing class keys** → `KeyError` at 3 core sites
  ([ST-DATA-008](12-findings-register.md), [07 §4](07-data-state-reliability.md#4-malformed-class-dicts)).
- **`targets=[]`, targets referencing unknown year/branch, unknown lecturer**:
  accepted silently — no referential-integrity check against `state['years']` or
  `state['lecturers']`.
- **Partial `lecturer_availability`** (missing sub-keys) → `KeyError` on the
  validation hot path ([ST-SCHED-008](12-findings-register.md)).
- **Malformed import cells**: numeric `ValueError` aborts the import
  ([ST-FUNC-003](12-findings-register.md); [04 §1](04-functional-stress-test.md#1-excel-import--the-flagship-workflow-is-broken-end-to-end)).

## 4. Adversarial / injection

Three real injection vectors, all confirmed by probe. None permits code execution
(this is a local Qt app, not a browser), but all are integrity/spoofing defects.

### 4.1 Warning-panel HTML injection (ST-UI-007)

`WarningLog.log` interpolates user-controlled class/branch/year names straight
into HTML and calls `QTextEdit.setHtml()` with no escaping (`widgets.py:232-236`).
Probe: 3/4 markup payloads parsed as **live elements** — a class named
`Room<img src="http://evil/x.png">A` produced a real `img` node in the document
(`toHtml()` contains `img`, `toPlainText()` shows the U+FFFC object char); `<b>`
rendered bold. Evidence
[`evidence/warning_injection_doc.html`](evidence/warning_injection_doc.html).
Impact: UI spoofing/defacement of the diagnostics panel, possible layout DoS. Qt
rich text does not run JavaScript and a plain `QTextEdit` doesn't fetch remote
`<img>` URLs by default, so no RCE/egress — but names should be `html.escape()`d.

### 4.2 CSV formula/DDE injection (ST-UI-008)

A class code / name beginning with `=` is written verbatim into the CSV
(`=cmd|'/c calc'!A1` round-tripped unneutralized into
[`evidence/adv.csv`](evidence/adv.csv)). Opening the export in Excel could execute
the formula. Fix: prefix risky cells with `'` or reject leading `= + - @`.

### 4.3 PDF markup abort (ST-DATA / exporter)

reportlab treats `<b>`, `<br>` etc. as inline markup; a class/branch/lecturer name
containing a reportlab tag (`Lab <B>`, `Intro<br>Part2`, `<script>…`) **aborts the
entire PDF build** with `ValueError` (`exporter.py:423`) — one bad cell blocks
exporting the whole timetable (no per-cell isolation). Benign angle brackets
(`Math < Physics`, `C++ > C`, `R&D Seminar`) export fine. Evidence:
`evidence/malformed_*.pdf`, `evidence/benign_*.pdf`.

## 5. Unicode & extreme content

- **Turkish characters** import correctly in cell values but **fail in PDF export**
  ([ST-FUNC-004](12-findings-register.md#st-func-004)) and can crash **CSV export**
  under a non-Turkish locale ([ST-FUNC-006](12-findings-register.md)).
- **Very long names** (200+ chars) are accepted and **deform the grid** — one
  223-char name inflates its whole row to ~5× height across all days
  ([ST-UI-012](12-findings-register.md); `evidence/ux-stress-longname-200char.png`).
- **Names with `/ \ : ? * [ ]`** crash xlsx export
  ([ST-FUNC-005](12-findings-register.md#st-func-005)).

## 6. Analytics correctness under edge conditions

Probe [`scenarios/probe_dashboard_room_key_bug.py`](scenarios/probe_dashboard_room_key_bug.py):

- **Dashboard room metrics are always zero** ([ST-UI-003](12-findings-register.md#st-ui-003)):
  on a hand-built 4-class/2-room schedule with a real R1→R2 switch, the dashboard
  "Oda Değişimi" bar reads 0.0 (true value 0.8) and room utilization reads 0.0
  (true 0.10) because the analytics code reads a nonexistent `'room'` key. The
  global quality *gauge* is unaffected (it doesn't use room_metrics), but the room
  breakdown and utilization numbers a user sees are silently wrong. Verified
  against a hand recomputation. Evidence
  [`evidence/dashboard-room-switching-zero.png`](evidence/dashboard-room-switching-zero.png).
- **Impact analyzer is correct with deepdiff present** (installed): no-change →
  NO_RESCHEDULE_NEEDED; participants 0→40 → RECOMMENDED; allowed_days break →
  REQUIRED. But its **deepdiff-missing fallback silently under-detects** soft
  impacts (participants/protection/joint_session changes report
  `changed_fields=['unknown']`, level NO_RESCHEDULE_NEEDED) — a latent regression
  if deepdiff is ever absent.
- **Scorer serialization is faithful** (REFUTED a suspected bug):
  `TimetableScorer` and the parallel-worker `PlacementScorer` path agree exactly
  (max divergence 0.0 over 20 candidates) — the parallel and sequential objectives
  differ *by design* (different objectives), not due to a serialization bug.
- **Dead but functional**: `ScheduleAnalytics.compare` (no callers) computes an
  **inverted** `score_improvement` sign; `explain_change` surfaces a raw
  translation key. Low impact (unreachable) but latent if wired up.
- `slot_index` `ValueError` on an out-of-grid placed slot propagates through
  `compute_all_metrics`/`analyze_schedule` — the analytics face of
  [ST-DATA-003](12-findings-register.md#st-data-003).

---

## Conclusion

DERSİS's error handling is **bimodal**: genuinely graceful on empty/degenerate
inputs and on placements it discovers are infeasible, but **crash-prone or
silently-wrong** whenever a forced value, stale constraint, or adversarial string
bypasses the natural guards. The consistent absence of input escaping (HTML, CSV,
PDF markup) and of membership guards (`slot_index`, availability keys) means the
same class of bug recurs across subsystems — which is why the roadmap treats
"validate at the boundary" as a cross-cutting Phase 1 theme rather than a pile of
point fixes. See [07](07-data-state-reliability.md) for the state-integrity view
and [11](11-security-resilience-notes.md) for the injection findings' security
framing.
