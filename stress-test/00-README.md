# DERSİS — Full Stress-Test & Improvement Audit

A complete, evidence-based technical audit of `dersis-app`: architecture map,
functional inventory, adversarial stress testing, performance profiling, data-
integrity analysis, UI/UX review, security notes, a canonical findings register,
and a prioritised implementation roadmap. Open this file first — it is the index.

---

## Purpose

Answer one question with evidence, not opinion:

> If DERSİS were deployed institution-wide and heavily used tomorrow, what would
> fail, slow down, confuse users, create incorrect schedules, corrupt state, or
> make the system hard to maintain?

## Scope

- **Repository:** `C:\dev\dersis-app`, branch `main`, commit `365b24b`, working
  tree clean at audit start. App version **1.0.0**.
- **Application:** DERSİS — an offline PyQt6 desktop app for school/university
  timetabling (Turkish-first, 22 languages, Windows + macOS). ~48 800 LOC Python.
- **Covered:** every subsystem in the [system map](01-system-map.md) — lifecycle,
  domain model, scheduling engine (heuristic + CP-SAT), optimizer stack,
  import/export (Excel/CSV/PDF), persistence & crypto, learning, the full UI,
  localization, tiers, build/CI/release.
- **Method:** `understand → test → measure → diagnose → document → plan`. This is a
  diagnostic baseline; **no production code under `scheduler_app/` was modified**
  (per the brief). The only repo changes are this `stress-test/` tree and one
  `.gitignore` line (`.venv-audit/`).

## Environment

- Python 3.12.10 in `.venv-audit/` (installed for this audit; the machine had no
  Python) with all runtime deps incl. `ortools` (CP-SAT available) and PyQt6.
- All tests sandbox `HOME`/`USERPROFILE` to a temp dir so the real
  `~/Documents/Dersis` is never touched. GUI evidence is captured headlessly via
  `widget.grab()` on the native platform. Full detail:
  [03-test-methodology.md](03-test-methodology.md).

## Tests conducted

- **10 audit workstreams** run as parallel agent workflows: 8 subsystem readers
  (mapping), 7 empirical stress-probers, and 3 static auditors (UX / architecture /
  security).
- **~50 reproducible probe scripts** under [`tests/`](tests/) and
  [`scenarios/`](scenarios/), including a reusable **scheduler correctness oracle**
  ([`tests/schedule_oracle.py`](tests/schedule_oracle.py)) and a **scaling
  benchmark** ([`tests/scheduler_benchmark.py`](tests/scheduler_benchmark.py)).
- **~110 evidence artifacts** under [`evidence/`](evidence/) — 38 UI screenshots,
  the benchmark CSV, oracle JSON, export samples, injection docs.

## Areas not fully tested (with reasons)

- **Packaged builds** (`build_embed.bat`, Nuitka, PyInstaller `.dmg`) — need
  network + long Windows/macOS builds; reviewed **statically**. `NOT TESTABLE`.
- **macOS runtime** — audit ran on Windows; packaging reviewed statically. `N/A`.
- **True multi-user concurrency** — single-user app; approximated with two local
  processes. `N/A`.
- Full per-language visual proofing of all 22 locales — key coverage counted, not
  every string rendered. `PARTIAL`.

Every subsystem's status is tabulated in the
[coverage matrix](01-system-map.md#coverage-matrix). Nothing is left unreviewed.

## Severity system

| Severity | Meaning |
|---|---|
| 🔴 **Critical** | Data corruption, catastrophic loss, unusable scheduling, app-wide failure, serious security exposure, or unsafe to deploy. |
| 🟠 **High** | Major functionality fails or is unreliable for realistic use. |
| 🟡 **Medium** | Noticeable defect or technical weakness with a viable workaround. |
| 🟢 **Low** | Minor defect, polish, maintainability, or low-impact inconsistency. |

Severity reflects **user consequence**, not how hard the bug was to find. Every
finding is also marked **OBSERVED** (demonstrated in this environment) or
**INFERRED** (concluded from code).

---

## Executive summary

DERSİS looks like a finished product — polished UI, four languages, a sophisticated
hybrid solver (greedy + Large-Neighbourhood Search + Google OR-Tools CP-SAT),
encrypted storage, Windows/macOS installers. The audit found that **the core
promise is not met**, across four independent fault lines:

1. **The scheduler can silently produce wrong schedules.** An independent
   invariant oracle showed the production optimizer commits hard-constraint
   violations (room/lecturer/group double-bookings); the commit step's only defense
   is to *silently drop* the colliding classes, and pinned collisions are committed
   entirely unvalidated. → [ST-SCHED-001](12-findings-register.md#st-sched-001),
   [ST-SCHED-002](12-findings-register.md#st-sched-002).
2. **The main view hides those conflicts.** The timetable renderer overwrites one
   of two lessons sharing a cell, with no indicator anywhere — a double-booked
   group looks conflict-free. → [ST-UI-001](12-findings-register.md#st-ui-001).
3. **Flagship data workflows break and lose data.** Excel import crashes on every
   success (after corrupting state), the app's own template re-imports 5 classes as
   2, and a few corrupt bytes in `key.bin` silently orphan **all** saved timetables.
   → [ST-FUNC-001](12-findings-register.md#st-func-001),
   [ST-FUNC-002](12-findings-register.md#st-func-002),
   [ST-DATA-001](12-findings-register.md#st-data-001).
4. **No safety net.** There are **zero automated tests**, and CI is wired to a
   branch (`master`) that does not exist, so nothing runs on any push. Every
   Critical above is exactly what a minimal suite would catch. →
   [ST-ARCH-001](12-findings-register.md#st-arch-001).

Add a **super-linear solver on the UI thread** (25–120 s per reschedule at 80
classes, unusable past ~250, [ST-PERF-001](12-findings-register.md#st-perf-001)), a
**PDF export that renders Turkish letters as boxes** for a Turkish-first product
([ST-FUNC-004](12-findings-register.md#st-func-004)), a **mouse-only grid invisible
to assistive tech** ([ST-UI-004](12-findings-register.md)), and CI that
**auto-publishes unvetted "latest" builds** to real users
([ST-SEC-001](12-findings-register.md#st-sec-001)).

**Total: 93 findings — 6 Critical, 27 High, 43 Medium, 17 Low.**
**Current deployment readiness: Internal alpha.**

The encouraging half: none of this is a design dead-end. The engine is testable
headlessly *today*, the domain model is coherent, most Critical fixes are small,
and the architecture needs *extraction seams*, not a rewrite. Phases 0–1 of the
roadmap (a few weeks) would reach a defensible **controlled beta**. The five
highest-leverage changes are in [15-final-assessment.md](15-final-assessment.md#the-five-highest-leverage-changes).

---

## Document index

| # | Document | What it covers |
|---|---|---|
| 00 | **This file** | Index, scope, executive summary |
| 01 | [System Map](01-system-map.md) | Verified architecture, runtime shape, domain model, coverage matrix |
| 02 | [Functional Inventory](02-functional-inventory.md) | Every feature → entry point, code path, failures, test status |
| 03 | [Test Methodology](03-test-methodology.md) | Environment, sandboxing, headless techniques, evidence discipline |
| 04 | [Functional Stress Test](04-functional-stress-test.md) | Import/export/UI workflows under adversarial input |
| 05 | [Scheduling Engine Stress Test](05-scheduling-engine-stress-test.md) | Correctness oracle, constraints, determinism, scaling cliffs |
| 06 | [Performance Audit](06-performance-audit.md) | Measured frontend/engine/persistence performance |
| 07 | [Data & State Reliability](07-data-state-reliability.md) | Corruption, orphans, recovery, concurrency |
| 08 | [Error & Edge-Case Audit](08-error-edge-case-audit.md) | Boundaries, malformed input, injection, analytics correctness |
| 09 | [UI/UX Audit](09-ui-ux-audit.md) | Screen-by-screen, accessibility, responsiveness, 5 redesign proposals |
| 10 | [Code & Architecture Audit](10-code-architecture-audit.md) | Complexity, coupling, duplication, god objects, typing, tests |
| 11 | [Security & Resilience Notes](11-security-resilience-notes.md) | Crypto, supply chain, release integrity, privacy |
| 12 | [**Findings Register**](12-findings-register.md) | Canonical issue list (all 82) with stable IDs |
| 13 | [Improvement Opportunities](13-improvement-opportunities.md) | Fixes / hardening / perf / UX / architecture / features |
| 14 | [Implementation Roadmap](14-implementation-roadmap.md) | Phased plan (0–7) with prioritisation model |
| 15 | [Final Assessment](15-final-assessment.md) | 0–10 scores, deployment verdict, top-5 changes |

Supporting: [`tests/`](tests/) reproducible probes · [`scenarios/`](scenarios/) ·
[`evidence/`](evidence/) raw artifacts.

**Starting the fix work?** → [`HANDOFF.md`](HANDOFF.md) has a ready-to-paste
prompt for a fresh implementation session, plus a quick reference to the 6
Criticals.

## How to reproduce

```bash
# from repo root, with the audit venv
.venv-audit/Scripts/python.exe stress-test/tests/schedule_oracle.py       # scheduler invariants
.venv-audit/Scripts/python.exe stress-test/tests/scheduler_benchmark.py   # scaling curve → evidence CSV
.venv-audit/Scripts/python.exe stress-test/tests/capture_screens.py       # UI screenshots
```

Each probe sandboxes its own `HOME`/`USERPROFILE`; none touches real user data.
See [03-test-methodology.md](03-test-methodology.md) for the full setup.
