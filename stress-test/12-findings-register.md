# 12 — Findings Register (canonical)

Part of the [DERSİS stress-test audit](00-README.md). This is the single source
of truth for every finding. All other documents reference these IDs. Severity
reflects **user consequence**, not discovery difficulty
([definitions](00-README.md#severity-system)). Confidence and OBSERVED/INFERRED
follow [the methodology](03-test-methodology.md#6-evidence-discipline).

Status is `Open` for all — this audit is diagnosis, not remediation
([§23 of the brief](14-implementation-roadmap.md)).

**Counts:** 6 Critical · 27 High · 43 Medium · 17 Low = 93 findings.

Jump: [Functional](#functional) · [Scheduler](#scheduler) · [Performance](#performance) · [Data](#data) · [UI/UX](#uiux) · [Architecture](#architecture) · [Security](#security)

---

## How to read an entry

Each row of the summary tables links to a detail block below the table (where
one exists). Detail blocks carry: Category · Severity · Confidence · Component ·
Evidence · Reproduction · Root cause · User impact · Technical impact ·
Recommendation · Effort (S ≤½day / M ≤2days / L ≤1wk / XL >1wk) · Dependencies ·
Related · Status.

---

## Functional

Import/export/UI-workflow correctness. Detailed in
[04-functional-stress-test.md](04-functional-stress-test.md).

| ID | Sev | Conf | Title | Evidence |
|---|---|---|---|---|
| ST-FUNC-001 | 🔴 Critical | High | Successful Excel import always crashes — `_on_state_changed()` and `refresh()` are undefined, **after** state is already mutated | OBSERVED · [detail](#st-func-001) |
| ST-FUNC-002 | 🔴 Critical | High | Blank joint-group cells collapse to `'nan'` and silently merge/delete unrelated classes; the app's **own generated template** loses 3 of 5 classes on re-import | OBSERVED · [detail](#st-func-002) |
| ST-FUNC-003 | 🟠 High | High | Blank / non-numeric `duration` or `student_count` cell raises an uncaught `ValueError` that aborts the whole import with no dialog | OBSERVED · [detail](#st-func-003) |
| ST-FUNC-004 | 🟠 High | High | PDF export cannot render Turkish letters (ğ Ğ ş Ş ı İ) — no embedded Unicode font, Helvetica-only → boxes | OBSERVED · [detail](#st-func-004) |
| ST-FUNC-005 | 🟠 High | High | `xlsx` export crashes when any lecturer/room/branch name contains `/ \ : ? * [ ]` (sheet-title chars) | OBSERVED · [detail](#st-func-005) |
| ST-FUNC-006 | 🟡 Medium | High | CSV export leaks internal day keys (`monday`, not `Pazartesi`) and uses OS-locale encoding — crashes with `UnicodeEncodeError` on non-Turkish Windows | OBSERVED |
| ST-FUNC-007 | 🟡 Medium | High | Legacy plain-JSON ASCII saves cannot be loaded (mis-detected as Fernet tokens); only non-ASCII JSON loads — inverse of intent | OBSERVED |
| ST-FUNC-008 | 🟡 Medium | High | Ctrl+C on the Dashboard tab raises `IndexError` | OBSERVED |
| ST-FUNC-009 | 🟡 Medium | High | `required_room_type` is advertised in template + import schema but never consumed by the importer | OBSERVED |
| ST-FUNC-010 | 🟡 Medium | High | Rows with a space in the class ID are silently dropped by the importer | OBSERVED |
| ST-FUNC-011 | 🟢 Low | High | Workbook with zero recognized sheets "imports successfully" (`is_valid=True`, empty result) | OBSERVED |
| ST-FUNC-012 | 🟢 Low | High | No dedup of duplicate `class_code` / classroom / lecturer names — silently accepted | OBSERVED |
| ST-FUNC-013 | 🟢 Low | High | PDF export silently omits classes whose placement is outside the current grid (data loss, no warning) | OBSERVED |

## Scheduler

Scheduling-engine correctness, constraint enforcement, scalability. Detailed in
[05-scheduling-engine-stress-test.md](05-scheduling-engine-stress-test.md).

| ID | Sev | Conf | Title | Evidence |
|---|---|---|---|---|
| ST-SCHED-001 | 🔴 Critical | High | Production optimizer's raw schedule contains **hard-constraint violations** (room / lecturer / group double-bookings) between distinct classes; `apply_reschedule` silently drops the losers | OBSERVED · [detail](#st-sched-001) |
| ST-SCHED-002 | 🟠 High | High | Infeasible / mutually-colliding **pinned** placements are committed with **no validation** — `apply_reschedule` skips pinned classes entirely | OBSERVED · [detail](#st-sched-002) |
| ST-SCHED-003 | 🟠 High | High | **Ghost-day / ghost-slot placements**: `allowed_days`/`allowed_times` are never intersected with the grid, so a class allowed only on Saturday is placed on Saturday even on a Mon–Fri grid | OBSERVED · [detail](#st-sched-003) |
| ST-SCHED-004 | 🟠 High | High | Stale `allowed_times` value not in `state['slots']` causes an uncaught `ValueError` that crashes reschedule | OBSERVED · [detail](#st-sched-004) |
| ST-SCHED-005 | 🟠 High | High | CP-SAT deep mode enforces lecturer availability only at the **start** slot; multi-hour classes are placed across unavailable hours, then silently dropped | OBSERVED · [detail](#st-sched-005) |
| ST-SCHED-006 | 🟠 High | High | CP-SAT deep mode ignores `soft` / `same_day` / `improve_only` protection — only `LOCKED` is respected; protected placements move without appearing in `changes[]` | OBSERVED |
| ST-SCHED-007 | 🟡 Medium | High | Legacy backtracking solver family (`reschedule_all` / `batch_schedule` / `auto_place_class`) ignores lecturer availability entirely and moves `LOCKED` classes | OBSERVED · [detail](#st-sched-007) |
| ST-SCHED-008 | 🟡 Medium | High | Malformed `lecturer_availability` (missing sub-keys) raises `KeyError` on the validation hot path | OBSERVED |
| ST-SCHED-009 | 🟡 Medium | High | `ConstraintValidator.find_conflicts` returns `[]` for a placement `check_placement` rejects — conflict UI can't explain the rejection | OBSERVED |
| ST-SCHED-010 | 🟡 Medium | High | Occupancy maps are ref-count-free sets: temporarily removing one class erases a co-located class's occupancy, corrupting subsequent validity checks | OBSERVED |
| ST-SCHED-011 | 🟡 Medium | High | "Move conflicting class" relaxation suggestions never emit — blockers tallied by `id()` but looked up by `cls_key()` | OBSERVED |
| ST-SCHED-012 | 🟡 Medium | High | Greedy construction recursion depth == flexible-class count; ~1000+ classes raise `RecursionError` (pathological preset of 1200 crashes) | OBSERVED |
| ST-SCHED-013 | 🟡 Medium | High | Optimizer is **non-deterministic** (unseeded global RNG): identical input → different placements and up to ~40% quality spread | OBSERVED · [detail](#st-sched-013) |
| ST-SCHED-014 | 🟢 Low | High | Infeasible (oversubscribed) instances produce misleading explanations that don't name the root global constraint; negotiation mislabels unplaceable classes "ok" | OBSERVED |
| ST-SCHED-015 | 🟢 Low | High | `neighbor_impact_penalty` objective term (weight 4.0) is dead code — `_neighbor_impact` always returns 0.0 | OBSERVED |

## Performance

Detailed in [06-performance-audit.md](06-performance-audit.md).

| ID | Sev | Conf | Title | Evidence |
|---|---|---|---|---|
| ST-PERF-001 | 🔴 Critical | High | Super-linear solve time (~O(n^1.77)); routine reschedule is 25–120 s at 80 classes and effectively unusable beyond ~250 — **on the UI thread, no cancellation** | OBSERVED · [detail](#st-perf-001) |
| ST-PERF-002 | 🟠 High | High | `refresh_grid` does a **full encrypted state rewrite every call**; a single refresh is 0.65 s (80 cls) → 2.5–4.7 s (250 cls) | OBSERVED · [detail](#st-perf-002) |
| ST-PERF-003 | 🟠 High | High | Warning log never cleared + O(n²) HTML rebuild → memory leak: 12 refreshes grew RSS +480 MB and per-refresh time 2.1 s → 4.8 s | OBSERVED · [detail](#st-perf-003) |
| ST-PERF-004 | 🟠 High | High | Greedy backtracking exhausts its 100 000-iteration budget already at 25 classes (moderate density) | OBSERVED |
| ST-PERF-005 | 🟡 Medium | High | Feedback logging rewrites the whole log per append → O(n²); 2000 appends took 108 s; `PreferenceLearner.learn()` re-reads the full log each call | OBSERVED · [detail](#st-perf-005) |
| ST-PERF-006 | 🟡 Medium | High | Open-slots & warnings panels rebuild hundreds of widgets and re-run heavy analysis on every refresh (359 widgets, 4.5 s warnings pass at 250 cls) | OBSERVED |
| ST-PERF-007 | 🟡 Medium | High | `workflow.reschedule` runs a second expensive `negotiate_after_optimization` pass whenever any class is unplaced (+10 s wrapper overhead at 25 cls) | OBSERVED |
| ST-PERF-008 | 🟡 Medium | High | Greedy construction phase is not wall-clock bounded — a single restart overran a 25 s budget to 55 s | OBSERVED |
| ST-PERF-009 | 🟢 Low | High | CP-SAT deep path yields no improvement at normal scale within a 5 s limit (helps only small instances) | OBSERVED |

## Data

State integrity & persistence. Detailed in
[07-data-state-reliability.md](07-data-state-reliability.md).

| ID | Sev | Conf | Title | Evidence |
|---|---|---|---|---|
| ST-DATA-001 | 🟠 High | High | Truncated/short `key.bin` is silently regenerated during a **load**, permanently orphaning all prior encrypted saves | OBSERVED · [detail](#st-data-001) |
| ST-DATA-002 | 🟠 High | High | Corrupt/truncated feedback log is silently swallowed to `[]`, then the next append overwrites it — history destroyed | OBSERVED |
| ST-DATA-003 | 🟠 High | High | Deleting/renaming a **time slot** after placement crashes 8 of 9 downstream operations (analytics, CSV/XLSX export, reschedule, refresh) | OBSERVED · [detail](#st-data-003) |
| ST-DATA-004 | 🟠 High | High | `SetupDialog` OK does not reconcile placed classes with removed days/slots/rooms/lecturers → orphaned placements reachable through normal UI use | OBSERVED · [detail](#st-data-004) |
| ST-DATA-005 | 🟡 Medium | High | `_auto_save` swallows **all** exceptions; a read-only or failing settings path loses data with zero user feedback | OBSERVED |
| ST-DATA-006 | 🟡 Medium | High | Class pinned to a nonexistent slot poisons the whole state (analytics + CSV/XLSX export + reschedule crash) | OBSERVED |
| ST-DATA-007 | 🟡 Medium | High | Force-placed duration overflow (`duration > slots`) crashes `analytics.busiest_slots` / `compute_all_metrics` with `IndexError` | OBSERVED |
| ST-DATA-008 | 🟡 Medium | High | Un-normalized class dict (missing keys) crashes core read/optimize/export paths with `KeyError` at 3+ sites | OBSERVED |
| ST-DATA-009 | 🟡 Medium | High | Dragging one class from the Unplaced panel destroys an unrelated undo-history entry; undo model covers only `state['classes']` | OBSERVED |
| ST-DATA-010 | 🟡 Medium | High | Multi-select drag from the grid moves only the primary class | OBSERVED |
| ST-DATA-011 | 🟡 Medium | High | `schedule_new_classes` leaks a half-added class into state when the optimizer raises (no internal rollback) | OBSERVED |
| ST-DATA-012 | 🟢 Low | High | No single-instance lock — two app instances clobber the shared `app_settings.egu` (last-writer-wins; a full class + language change was lost) | OBSERVED |
| ST-DATA-013 | 🟢 Low | Medium | Persistence roundtrip silently coerces non-string dict keys (`42`→`'42'`) and preserves `NaN`/`Infinity` (invalid-per-spec JSON) | OBSERVED |
| ST-DATA-014 | 🟢 Low | High | Corrupt settings container is silently replaced on load, discarding the entire saved schedule | OBSERVED |

## UI/UX

Detailed in [09-ui-ux-audit.md](09-ui-ux-audit.md). IDs reserved here; the UX
agent's screen-by-screen findings are merged on completion.

| ID | Sev | Conf | Title | Evidence |
|---|---|---|---|---|
| ST-UI-001 | 🔴 Critical | High | **Timetable renderer silently hides one of two conflicting lessons** — two classes in one room/slot render as one, with no conflict indicator anywhere; a double-booked group looks conflict-free | OBSERVED · [detail](#st-ui-001) |
| ST-UI-002 | 🟠 High | High | Placed-count disagreement across dashboard / status bar / results dialog (3 definitions); status bar can show a **negative** unplaced count ("-5 yerleşmemiş") | OBSERVED · [detail](#st-ui-002) |
| ST-UI-003 | 🟠 High | High | Dashboard "room switching" quality bar and room-utilization metrics are **always zero** — code reads a nonexistent `'room'` key | OBSERVED · [detail](#st-ui-003) |
| ST-UI-004 | 🟠 High | High | Timetable grid is **mouse-only and invisible to assistive tech** — zero accessibility API usage, no keyboard navigation, custom-painted text | OBSERVED · [detail](#st-ui-004) |
| ST-UI-005 | 🟠 High | High | Most in-cell text **fails WCAG AA contrast** — room label 1.55–2.14:1, pinned badge 2.3:1, class code 3.4:1 (the room, the most critical field, is least legible) | OBSERVED · [detail](#st-ui-005) |
| ST-UI-006 | 🟠 High | High | **Color is the only encoding** of class grouping (year color) and there is no legend anywhere; online vs face-to-face distinguished only by low-contrast text | OBSERVED |
| ST-UI-007 | 🟡 Medium | High | Warning panel renders user-controlled class/branch names into `QTextEdit.setHtml()` unescaped — markup/UI-spoofing injection | OBSERVED |
| ST-UI-008 | 🟡 Medium | High | CSV export is vulnerable to spreadsheet formula/DDE injection (cells beginning with `=` written verbatim) | OBSERVED |
| ST-UI-009 | 🟡 Medium | High | Every user action (add/move/**select**/drag) triggers a full scene rebuild + encrypted autosave (306–563 ms at 250 cls) | OBSERVED |
| ST-UI-010 | 🟡 Medium | High | Toast notifications appear at the wrong screen position (offset by the window's screen origin — 500 px measured) | OBSERVED |
| ST-UI-011 | 🟡 Medium | High | Raw translation key `labels.targets` shown in the Edit Classes header in **all 22 languages** (also `labels.protection`, `errors.duration_required`) | OBSERVED |
| ST-UI-012 | 🟡 Medium | High | Extreme class names deform the whole grid row (~5× height); sequential cells clip names and show ambiguous branch-only labels | OBSERVED |
| ST-UI-013 | 🟡 Medium | High | Responsive breakdown below ~1400 px: truncated tabs; fixed-width sidebar (~43%) starves the grid to 2.5 day columns at 1000 px | OBSERVED |
| ST-UI-014 | 🟡 Medium | High | Inconsistent destructive-action protection across four delete paths; setup edits are irreversible and unconfirmed | OBSERVED |
| ST-UI-015 | 🟡 Medium | High | `PlaceClassDialog` dead-ends on the most important case (0 valid placements) — no negotiator reasons shown | OBSERVED |
| ST-UI-016 | 🟡 Medium | Medium | 33-step tutorial auto-fires over a modal on first run, obscuring the whole app | OBSERVED |
| ST-UI-017 | 🟢 Low | High | Toolbar dropdown buttons indistinguishable from action buttons (menu caret hidden); Open-Slots rows advertise clickability but do nothing | OBSERVED |
| ST-UI-018 | 🟢 Low | High | Dark-themed bug/crash dialogs in a light-only app; inconsistent button order/roles app-wide | OBSERVED |
| ST-UI-019 | 🟢 Low | High | Warning log: unbounded duplicate spam, no timestamps, redundant phrasing, 120 px cap; thin selection border, no hover state | OBSERVED |
| ST-UI-020 | 🟢 Low | High | Empty state offers no guidance (blank canvas + misleading "Çevrimiçi" filter); language switch hidden behind a flag-only menu entry; terminology drift across screens | OBSERVED |

*Full screen-by-screen writeups and 38 evidence screenshots in [09-ui-ux-audit.md](09-ui-ux-audit.md).*

## Architecture

Detailed in [10-code-architecture-audit.md](10-code-architecture-audit.md).

| ID | Sev | Conf | Title | Evidence |
|---|---|---|---|---|
| ST-ARCH-001 | 🔴 Critical | High | **Zero automated tests** (0% coverage) over a codebase with multiple probe-confirmed correctness holes; CI runs none | OBSERVED · [detail](#st-arch-001) |
| ST-ARCH-002 | 🟠 High | High | CI validation workflow is **dead** — triggers on `master`, the repo's only branch is `main` | OBSERVED |
| ST-ARCH-003 | 🟠 High | High | UI ships a **parallel** Excel/CSV export engine (`app.py._write_excel`, 496 LOC) duplicating and drifting from `data_io/exporter.py` | OBSERVED |
| ST-ARCH-004 | 🟠 High | High | Hard-constraint validation exists in **four divergent implementations**; production drag-drop uses the deprecated weaker one | OBSERVED |
| ST-ARCH-005 | 🟠 High | High | `ui/app.py` is a god object: 4 961 LOC, Maintainability Index 0.00, ~135 methods, 10+ responsibilities | OBSERVED |
| ST-ARCH-006 | 🟠 High | High | Persistence critical path wrapped in silent exception swallowing (22 of 55 broad excepts are single-statement silent) | OBSERVED |
| ST-ARCH-007 | 🟠 High | High | Shared-mutable-state-dict with no ownership boundary: dialogs write live state; normalization runs as an autosave side effect | OBSERVED |
| ST-ARCH-008 | 🟡 Medium | High | Optimizer megafunctions concentrate the hardest logic in unreviewable units (`CPSATScheduler.solve` CC 105/510 LOC; `optimize` CC 84) | OBSERVED |
| ST-ARCH-009 | 🟡 Medium | High | 19 upward layering violations — core/storage/data_io/learning all import `ui` | OBSERVED |
| ST-ARCH-010 | 🟡 Medium | High | 11 module-level import cycles through `core.logic`, held together by 20 function-level deferred imports | OBSERVED |
| ST-ARCH-011 | 🟡 Medium | High | ~30 dead/unreachable symbols including a complete legacy solver family that still carries known constraint holes | OBSERVED |
| ST-ARCH-012 | 🟡 Medium | High | Undo model covers only `state['classes']`; setup/availability edits are irreversible and can desync restored classes | OBSERVED |
| ST-ARCH-013 | 🟡 Medium | High | Type-hint coverage 10.8% overall, ~5–7% in `ui`/`core` where the dict-shaped domain model hides errors | OBSERVED |
| ST-ARCH-014 | 🟢 Low | High | `requirements-dev.txt` pins `pytest` that nothing uses — dev tooling exists on paper only | OBSERVED |

## Security

Detailed in [11-security-resilience-notes.md](11-security-resilience-notes.md).
Threat model is a **local offline desktop app**, so severities are calibrated
accordingly.

| ID | Sev | Conf | Title | Evidence |
|---|---|---|---|---|
| ST-SEC-001 | 🟠 High | High | CI auto-publishes a public non-prerelease `latest` GitHub Release on every push to `main` — users receive unvetted dev builds | OBSERVED · [detail](#st-sec-001) |
| ST-SEC-002 | 🟡 Medium | High | At-rest "encryption" is obfuscation only — the 256-bit master key is stored in plaintext (`keys/key.bin`) beside the ciphertext, contradicting the "encrypted/private" framing | OBSERVED · [detail](#st-sec-002) |
| ST-SEC-003 | 🟡 Medium | High | Installer grants users-modify ACL on `{app}`; an elevated install enables binary planting / local privilege escalation | OBSERVED |
| ST-SEC-004 | 🟡 Medium | High | Unpinned, unverified build-time downloads and an unsigned installer (supply-chain exposure) | OBSERVED |
| ST-SEC-005 | 🟡 Medium | High | "Offline" app shells out to `pip install` over the network from the UI (and it can't work in the frozen build) | OBSERVED |
| ST-SEC-006 | 🟢 Low | High | `download_release.py`: SHA-256 verification is silently optional; auth token forwarded across the CDN redirect | OBSERVED |
| ST-SEC-007 | 🟢 Low | High | Placeholder AppId GUID and unsigned binaries | OBSERVED |
| ST-SEC-008 | 🟢 Low | Medium | Privacy: plaintext crash log + `mailto:` report may leak the Windows username via filesystem paths in tracebacks | INFERRED |

---

# Detail blocks

<a id="st-func-001"></a>
### ST-FUNC-001 — Successful Excel import always crashes (after mutating state)
- **Category** Functional · **Severity** Critical · **Confidence** High · **OBSERVED**
- **Component** `scheduler_app/ui/app.py` `_import_from_excel`
- **Evidence** `hasattr(SchedulerApp, '_on_state_changed')` → `False` and `hasattr(SchedulerApp, 'refresh')` → `False` across the entire 8-class MRO. Driving `_import_from_excel` with a valid template raises `AttributeError` at `app.py:4525` on **every** run. Probe: `tests/dataio/probe_01_import_ui_flow_static.py`, `tests/ui/...`.
- **Reproduction** Import any valid `.xlsx` (including the app's own generated template) via File → Import. Crash is deterministic.
- **Root cause** The success handler calls `self._on_state_changed()` then `self.refresh()` (`app.py:4525-4526`) — methods that were never defined (the real ones are `refresh_grid()` / `_update_status()`). Critically, the imported classes are merged into `self.state_data` at `app.py:4512-4523` **before** the crash, so state is mutated and then the UI throws.
- **User impact** The flagship "import from Excel" workflow is 100% broken; worse, it half-applies the import (state changed, screen not refreshed, exception dialog shown), so the user cannot tell what state they are in.
- **Technical impact** Combined with the global excepthook, the app survives but is left in an inconsistent, unrendered state; a subsequent action auto-saves the half-merged data.
- **Recommendation** Replace with `self.refresh_grid(); self._update_status()`; wrap the whole import in `try/except` that rolls back the merge on failure. Add a regression test that drives `_import_from_excel` end-to-end.
- **Effort** S · **Dependencies** none · **Related** ST-FUNC-002, ST-ARCH-001 · **Status** Open

<a id="st-func-002"></a>
### ST-FUNC-002 — Blank joint-group cells merge/delete unrelated classes
- **Category** Functional · **Severity** Critical · **Confidence** High · **OBSERVED**
- **Component** `scheduler_app/data_io/importer.py:297` (`_resolve_joint_groups` 412-436)
- **Evidence** Generating the official template (`template.generate_excel_template`) and re-importing it yields **2 classes from 5** — C001/C002/C003 all merged into one joint session via the string `'nan'`, `report.is_valid=True`, no warning. Ad-hoc: 3 unrelated classes with blank joint-group → 1 out. Probe: `tests/dataio/probe_02_import_edge_cases.py`, `probe_06_template_roundtrip.py`.
- **Reproduction** Import any workbook where ≥2 classes leave `joint_class_group` blank.
- **Root cause** `jcg = str(row.get('joint_class_group','')).strip()` — pandas reads blank/empty cells as float `NaN`, and `str(NaN) == 'nan'` (truthy), so every blank-group class shares the joint key `'nan'` and is merged; duplicates are removed from `state['classes']`.
- **User impact** Silent, invisible data loss on the primary bulk-entry path. A real institution importing a roster with mostly-independent courses would lose most of them and never be told.
- **Recommendation** `jcg = '' if pd.isna(v) else str(v).strip()`, treat `''`/`'nan'` as "no group". Round-trip test of the generated template asserting class count preserved.
- **Effort** S · **Related** ST-FUNC-001, ST-ARCH-001 · **Status** Open

<a id="st-func-003"></a>
### ST-FUNC-003 — Malformed numeric cell aborts the entire import
- **Severity** High · **OBSERVED** · `importer.py:258-259`
- **Evidence** Blank `duration` → `ValueError: cannot convert float NaN to integer`; `duration='two'` / `student_count='many'` → `invalid literal for int()`. The exception escapes `load_scheduler_data_from_excel` and the caller `_import_from_excel` has **no** `try/except`. Probe: `probe_02_import_edge_cases.py`.
- **Root cause** `int(row.get('duration',1) or 1)` — a present-but-NaN cell returns `NaN` (not the default), `NaN` is truthy so `or 1` doesn't apply, `int(NaN)` raises. No per-row error handling in `_process_classes`.
- **Recommendation** `pd.isna` guard + per-row `try/except → report.add_error(...)`; skip the bad row, keep the import. **Effort** S · **Related** ST-FUNC-001 · **Status** Open

<a id="st-func-004"></a>
### ST-FUNC-004 — PDF export cannot render Turkish letters
- **Severity** High · **OBSERVED** · `data_io/exporter.py:464`
- **Evidence** Exported PDF embeds only `Helvetica`, `Helvetica-Bold`, `ZapfDingbats`; **0** embedded/subset Unicode fonts, no `/FontFile`. Helvetica `stringWidth` for ş/ğ/İ/ı all equal 7.61 (a single substitute glyph) vs 5.56 for `a`. Probe: `probe_04_pdf_turkish.py`.
- **Root cause** reportlab canvas uses a base-14 font with no `TTFont` registration; non-Latin-1 codepoints have no glyph.
- **User impact** For a Turkish-first product, the PDF timetable — the artifact teachers actually print and post — renders every Turkish-specific letter as a box. Names like "Öğretmen", "Çarşamba", "İş" are unreadable.
- **Recommendation** Bundle and register a Unicode TTF (e.g. DejaVu Sans) via `pdfmetrics.registerFont`; use it for all text. **Effort** S · **Related** ST-FUNC-006 · **Status** Open

<a id="st-func-005"></a>
### ST-FUNC-005 — xlsx export crashes on sheet-title-invalid names
- **Severity** High · **OBSERVED** · `data_io/exporter.py:333`
- **Evidence** Names containing `/ \ : ? * [ ]` (legal in a lecturer/room/branch name) crash export with `ValueError` from openpyxl's sheet-title validation. 2/2 such names crashed. Probe: `probe_05_export_crashes.py`.
- **Recommendation** Sanitize sheet titles (strip/replace invalid chars, truncate to 31 chars, dedupe). **Effort** S · **Related** ST-FUNC-004 · **Status** Open

<a id="st-sched-001"></a>
### ST-SCHED-001 — Production optimizer commits hard-constraint violations
- **Category** Scheduler · **Severity** Critical · **Confidence** High · **OBSERVED**
- **Component** `core/schedule_optimizer.py:416-475`, `core/workflow.py:424-436`
- **Evidence** Independent invariant oracle (`tests/schedule_oracle.py`) over the production path `workflow.reschedule + apply_reschedule`:
  - small (25): raw 21 placed, **18 violation-cells** (6 room + 6 lecturer + 6 group double-books), apply dropped 1 → 20 committed clean.
  - normal (80): raw 76 placed, **60 violation-cells** (24 room + 12 lecturer + 24 group), apply dropped 9 → 67 committed clean.
  - Reproduces at `multi_start_runs=1` (deterministic): 6 distinct-class collision cells.
- **Reproduction** `tests/schedule_oracle.py` + `tests/verify_optimizer_conflicts.py`, seed 42.
- **Root cause** The optimizer's internal placement bookkeeping permits two **distinct** flexible classes to occupy the same room/lecturer/target slot; the raw proposed schedule is invalid. `apply_reschedule` re-validates and **silently drops** the losing class (its rejection list is discarded by the UI — see ST-SCHED-005 note), rather than the optimizer never producing the collision.
- **User impact** For small/normal instances the committed schedule ends up clean *only because* classes are dropped (silently unplaced), i.e. the tool quietly refuses to place work it could have placed. When combined with pinned classes (ST-SCHED-002) the committed schedule can retain real double-bookings.
- **Recommendation** Treat a raw schedule containing hard violations as a solver bug: add an assertion/repair pass; surface dropped classes to the user; fix the internal occupancy bookkeeping (relates to ST-SCHED-010). **Effort** L · **Related** ST-SCHED-002, ST-SCHED-005, ST-SCHED-010 · **Status** Open

<a id="st-sched-002"></a>
### ST-SCHED-002 — Colliding pinned placements committed unvalidated
- **Severity** High · **OBSERVED** · `core/workflow.py:425` (`if cls_item['pinned']: continue`)
- **Evidence** Deterministic repro: 4 mutually/infeasibly pinned classes → **6 committed hard-violations** (capacity 1, availability 1, room double-book 2, group clash 2), `apply_reschedule` rejected 0; `ConstraintValidator.check_placement` returns `False` for all 4. On the `large` preset, 7 pinned classes were committed with validator-confirmed violations and 0 rejections. Probe: `tests/pinned_infeasible_probe.py`.
- **Root cause** `apply_reschedule` skips validation for any pinned class, trusting the pin. Two classes pinned to the same room/slot, or a pin that violates capacity/availability, is committed as-is.
- **User impact** A user who manually pins classes can produce a schedule with real, invisible double-bookings that the quality panel reports as clean.
- **Recommendation** Validate pins on commit; flag infeasible pins as conflicts instead of trusting them. **Effort** M · **Related** ST-SCHED-001 · **Status** Open

<a id="st-sched-003"></a>
### ST-SCHED-003 — Ghost-day / ghost-slot placements
- **Severity** High · **OBSERVED** · `core/models.py:356-366`, `constraint_validator.py:77`, `workflow.py:181`
- **Evidence** A class with `allowed_days=['saturday']` on a Mon–Fri grid: `CandidateGenerator` returns 8/8 Saturday candidates, `check_placement` passes, `auto_place` commits `placed_day='saturday'` — a day that is not in `state['days']`. Probe: `tests/ghost_day_and_stale_time_probe.py, tests/legacy_solver_probe.py, tests/validator_integrity_probe.py` Task 1.
- **Root cause** `filter_class_days` (and the times equivalent) apply the class's own allow/exclude lists but never intersect with the actual grid axes.
- **User impact** Classes land on days/slots that don't exist in the timetable; they render off-grid or vanish from views, and downstream analytics/export can crash (ST-DATA-003/006).
- **Recommendation** Intersect `allowed_days`/`allowed_times` with `state['days']`/`state['slots']` at candidate generation. **Effort** S · **Related** ST-SCHED-004, ST-DATA-003 · **Status** Open

<a id="st-sched-004"></a>
### ST-SCHED-004 — Out-of-grid allowed_times crashes reschedule
- **Severity** High · **OBSERVED** · `candidate_generator.py:41`, `logic.py:17-18`
- **Evidence** `allowed_times=['20:00']` (or `'23:00'`) with that value absent from `state['slots']` → `ValueError: '20:00' is not in list` at `logic.py:18`, deterministic. Probe: `tests/ghost_day_and_stale_time_probe.py, tests/legacy_solver_probe.py, tests/validator_integrity_probe.py` Task 2, `tests/error_edge_*`.
- **Root cause** `slot_index` does `state['slots'].index(slot)` with no membership guard; stale constraints (e.g. after a slot is renamed/removed) reference values no longer present.
- **Recommendation** Guard `slot_index`; drop/flag stale constraint values during normalization. **Effort** S · **Related** ST-SCHED-003, ST-DATA-003 · **Status** Open

<a id="st-sched-005"></a>
### ST-SCHED-005 — CP-SAT availability checked only at start slot; losers silently dropped
- **Severity** High · **OBSERVED** · `cpsat_scheduler.py:606-619`, `workflow.py:423-438`, `app.py:2703-2713`
- **Evidence** A duration-3 class with a lecturer available only at the start hour is placed spanning 2 unavailable hours; `ConstraintValidator.check_placement=False`; `apply_reschedule` returns `rejected=['BIG3H']` but `result.placed` had reported it placed, and the UI **discards the rejected list** (`app.py:2703-2713`), so the class silently ends `placed=False`. Probe: `tests/probe_cpsat_protection_semantics.py, tests/probe_cpsat_midblock_availability.py, tests/probe_optimizer_determinism.py`.
- **Recommendation** Model availability across the whole duration in CP-SAT; surface `apply_reschedule`'s rejected list in the UI. **Effort** M · **Related** ST-SCHED-001, ST-SCHED-006 · **Status** Open

<a id="st-sched-007"></a>
### ST-SCHED-007 — Legacy solver family ignores availability and moves LOCKED classes
- **Severity** Medium · **OBSERVED** · `core/logic.py:522-588, 1043`
- **Evidence** A fully-unavailable lecturer: legacy `reschedule_all` / `auto_place_class` / `batch_schedule` all place at monday/09:00 (availability violated), and `reschedule_all` moved a `protection=locked` class from friday/11:00 → monday/09:00. The optimized path leaves it correctly fixed/unplaced. Probe: `tests/ghost_day_and_stale_time_probe.py, tests/legacy_solver_probe.py, tests/validator_integrity_probe.py` Tasks 3-4.
- **Note** These legacy entry points are **dead code** (ST-ARCH-011) — no live caller — but they are exported, importable, and imported by `ui/dialogs.py:28`. They are latent regressions if ever re-wired. **Recommendation** Delete the dead legacy solver family. **Effort** M · **Related** ST-ARCH-004, ST-ARCH-011 · **Status** Open

<a id="st-sched-013"></a>
### ST-SCHED-013 — Non-deterministic optimizer
- **Severity** Medium · **OBSERVED** · `schedule_optimizer.py:530-550`, `lns_strategies.py:555-603`
- **Evidence** 5 identical-input `optimize()` runs → 5 distinct placements, scores `[12.46, 12.10, 8.70, 8.82, 7.34]`, spread 5.12, population stdev 2.03 (~41% best-vs-worst). Placed-count is stable but quality is not. Probe: `tests/probe_cpsat_protection_semantics.py, tests/probe_cpsat_midblock_availability.py, tests/probe_optimizer_determinism.py`, `tests/scheduler_benchmark.py` determinism cell.
- **Root cause** LNS and multi-start use the unseeded global `random`; no seed is threaded through.
- **User impact** Regenerating a schedule gives a different (sometimes markedly worse) result each time; no reproducibility for support or comparison.
- **Recommendation** Thread an optional seed through the optimizer; default to a fixed seed for reproducibility, expose "randomize" explicitly. **Effort** M · **Status** Open

<a id="st-perf-001"></a>
### ST-PERF-001 — Super-linear solve time on the UI thread
- **Category** Performance · **Severity** Critical · **Confidence** High · **OBSERVED**
- **Component** `core/schedule_optimizer.py`, `ui/app.py:2683-2698`
- **Evidence** `tests/scheduler_benchmark.py` → `evidence/scheduler_benchmark.csv`. Single-restart production path (density 0.3): fit `t ≈ 0.0135·n^1.77`. Measured wall seconds: tiny 0.2 s; small(25) 5.8 s; normal(80) 25.4 s; large(250) ~30 s single-restart (full multi-start default: normal 121.7 s, large 132.6 s). Construction-only curve times out (>60 s) at 600 classes. Memory is modest (normal peak 7.8 MiB) — the cliff is **CPU time**, not memory.
- **Root cause** Multi-start × LNS × per-candidate scoring with several O(n)-per-candidate scans (e.g. `PlacementScorer._lecturer_switches_room`), all synchronous on the Qt main thread with `processEvents` pumping and no cancellation.
- **User impact** At a realistic 80-class department the UI freezes for 25–120 s per generate; at institution scale (250+) it is effectively unusable, and the window cannot be cancelled or closed cleanly mid-run.
- **Recommendation** Move solving to a worker (QThread/process) with progress + cancel; bound the greedy phase (ST-PERF-008); reduce per-candidate O(n) scans with incremental occupancy indexes. **Effort** L · **Related** ST-PERF-004, ST-PERF-008, ST-ARCH-005 · **Status** Open

<a id="st-perf-002"></a>
### ST-PERF-002 — Full encrypted state rewrite on every refresh
- **Severity** High · **OBSERVED** · `app.py:1963-1967, 1835-1851`
- **Evidence** `refresh_grid` calls `_auto_save`, which decrypts + re-encrypts + rewrites the whole settings container each time. Single `_auto_save`: 16.8 ms (80 cls / 74 KB egu) → 33.6 ms (250 cls / 232 KB). `refresh_grid` total: 0.65 s (80) → 2.5–4.7 s (250), dominated by the warnings pass (ST-PERF-003/006). Probe: `tests/probe_autosave_and_refresh_perf.py, tests/probe_undo_and_drag_integrity.py, tests/probe_excel_import_and_clipboard_crash.py`.
- **Recommendation** Debounce/defer autosave; save deltas or on explicit action, not every render. **Effort** M · **Related** ST-PERF-003, ST-DATA-005 · **Status** Open

<a id="st-perf-003"></a>
### ST-PERF-003 — Warning-log memory leak + O(n²) HTML rebuild
- **Severity** High · **OBSERVED** · `app.py:2964-3001`, `widgets.py:213-239`
- **Evidence** 12 successive `refresh_grid` on the large state: refresh time 2081 ms → 4816 ms (2.3×); `warning_log._messages` grew 138 → 1656 (+138/refresh, never cleared); process RSS grew 662 MB → 1142 MB (**+480 MB**); the egu on disk stayed constant (231 793 B), confirming the growth is in-memory. Probe: `tests/probe_autosave_and_refresh_perf.py, tests/probe_undo_and_drag_integrity.py, tests/probe_excel_import_and_clipboard_crash.py` rapid-refresh loop.
- **Root cause** The warnings list is appended to (never reset) and the panel rebuilds full HTML from the whole list each refresh.
- **Recommendation** Clear/rebuild warnings from current state each refresh; render incrementally. **Effort** M · **Related** ST-PERF-002, ST-PERF-006 · **Status** Open

<a id="st-perf-005"></a>
### ST-PERF-005 — O(n²) feedback logging
- **Severity** Medium · **OBSERVED** · `learning/preference_learner.py:72`, feedback logger append path
- **Evidence** Per-append time grows linearly (2.55 ms@1 → 99.9 ms@2000); cumulative 2000 appends = **108.4 s**; a 2000-entry log is 905 KB. `PreferenceLearner.learn()` re-reads and retrains on the full log every call (16.9 ms@100 → 78 ms@2000). Probe: `dataio/probe_04_feedback_log_perf_and_corruption.py`.
- **Root cause** Each append rewrites the whole encrypted log; each learn re-reads it.
- **Recommendation** Append-only log format (or cap + rotate); incremental learning. **Effort** M · **Status** Open

<a id="st-data-001"></a>
### ST-DATA-001 — Truncated key.bin silently regenerated → all saves orphaned
- **Category** Data · **Severity** High · **Confidence** High · **OBSERVED**
- **Component** `storage/storage.py:186-202`
- **Evidence** Save a state (loads fine), truncate `key.bin` to 20 bytes, clear the in-process key cache, reload → prior save fails `EguFileError`, and `key.bin` is silently replaced with a new random 32-byte key (old one moved to `backups/`). Every prior `.egu` is now undecryptable, with no prompt. Probe: `probe_03_storage_key_and_formats.py`; independently reproduced by the security agent with a 3-byte truncation.
- **Root cause** `_load_or_create_key` treats any key whose length ≠ 32 as "missing" and regenerates, rather than failing loudly — a partial write / disk corruption of a few bytes is indistinguishable from first run.
- **User impact** A single bad sector or interrupted write in `key.bin` permanently locks the user out of **all** their saved timetables, silently.
- **Recommendation** Distinguish "absent" from "malformed"; on malformed, fail loudly and offer restore from `backups/`; never auto-regenerate over a partially-present key. **Effort** M · **Related** ST-DATA-002, ST-DATA-014, ST-SEC-002 · **Status** Open

<a id="st-data-003"></a>
### ST-DATA-003 — Removing a time slot after placement crashes everything downstream
- **Severity** High · **OBSERVED** · `logic.py:18`, `analytics.py:150`, `dialogs.py:1813`
- **Evidence** On a 20/20-placed state, deleting one time slot: 8 of 9 downstream ops crash (analytics `compute_all_metrics`, CSV & XLSX export, reschedule, UI refresh) with `ValueError`/`IndexError`; only PDF survives, and it **silently drops** the orphaned class. Removing a day / room / lecturer / year instead is tolerated (0 crashes) because those are membership lookups. Probe: `tests/probe_deleted_resources.py, tests/probe_empty_and_boundary.py, tests/probe_recovery_rollback.py`.
- **Root cause** Placed classes retain `placed_time` referencing the deleted slot; `slot_index` and slot-indexed arrays assume membership. This is reachable through normal UI use because `SetupDialog` OK doesn't reconcile placements (ST-DATA-004).
- **Recommendation** On slot/day/resource removal, unplace or re-validate affected classes; guard `slot_index`. **Effort** M · **Related** ST-DATA-004, ST-SCHED-003/004, ST-DATA-006 · **Status** Open

<a id="st-data-004"></a>
### ST-DATA-004 — SetupDialog OK orphans placements
- **Severity** High · **OBSERVED** · `dialogs.py:1813-1821`, `app.py:2828-2838`
- **Evidence** Applying `SetupDialog` after removing a day / slot / room / lecturer leaves placed classes dangling (`placed=True` pointing at the removed axis) — 4/4 removal types produced orphans against the live dialog logic. This is the normal-UI trigger for ST-DATA-003. Probe: `tests/probe_autosave_and_refresh_perf.py, tests/probe_undo_and_drag_integrity.py, tests/probe_excel_import_and_clipboard_crash.py`, `tests/probe_deleted_resources.py, tests/probe_empty_and_boundary.py, tests/probe_recovery_rollback.py`.
- **Recommendation** Reconcile placements on OK (unplace affected classes, warn the user with counts). **Effort** M · **Related** ST-DATA-003 · **Status** Open

<a id="st-ui-001"></a>
### ST-UI-001 — Timetable renderer silently hides conflicting lessons
- **Category** UI · **Severity** Critical · **Confidence** High · **OBSERVED**
- **Component** `scheduler_app/ui/renderer.py:117-131` (filtered occupancy dict overwrite), `200-209` (everything view)
- **Evidence** Two classes hand-placed in the same cell (monday 09:00, R001): the classroom view renders only the last-written one; the other is completely invisible — in both the room view and the student-group view (a double-booked group looks conflict-free). `RendererAdapter` probe: blocks rendered for R001 = `['C0002','C0003']` while class `XX9999` placed at the same slot is absent. The status bar still counts all 3 as placed ("3 yerleşmiş"), so the visible grid contradicts the counters with **no conflict badge, warning, or indicator anywhere**. Evidence: `evidence/ux-stress-conflict-r001.png`, `ux-stress-conflict-group.png`.
- **Reproduction** Hand-place (or import — see ST-FUNC-002, ST-SCHED-002) two classes into the same room/day/slot; open the room or group view.
- **Root cause** The renderer builds an occupancy dict keyed by (day, slot); a second class at the same key **overwrites** the first instead of being detected as a collision.
- **User impact** The core artifact of the whole application — the visible timetable — can silently omit real lessons. A user whose schedule contains any overlap (from a corrupted import, a colliding pin, or the optimizer's own hard-violations) will print and publish a timetable that is missing classes, with nothing on screen to warn them.
- **Recommendation** Detect key collisions in `_default_filtered_blocks`/`everything_blocks`; render conflicting entries stacked (split cell) with a red "ÇAKIŞMA" chip and push one warning-log entry per conflict. **Effort** M · **Related** ST-SCHED-001, ST-SCHED-002, ST-UI-002 · **Status** Open

<a id="st-ui-002"></a>
### ST-UI-002 — Placed-count disagreement; negative unplaced count
- **Category** UI · **Severity** High · **Confidence** High · **OBSERVED**
- **Component** `ui/app.py:1853-1868` (formula at 1861), `core/analytics.py:197-202`
- **Evidence** Dashboard "Yerleşti **56**" and status bar "**52** yerleşmiş + 4 sabitlenmiş + 24 yerleşmemiş" are visible at once (dashboard counts placed OR pinned via `get_placed_classes`; status bar counts only `placed=True`). Worse, the status formula `n_unplaced = total - pinned - placed` **double-subtracts** classes that are both pinned and `placed=True`: on the large dataset the status bar rendered "**-5 yerleşmemiş**" — an impossible number shown to the user. Evidence: `evidence/ux-large-everything.png`, `evidence/screen-4-dashboard.png`.
- **User impact** The completion metric can't be trusted; "how many are placed?" has three answers and one of them is negative.
- **Recommendation** Define one canonical trio in core (`scheduled = placed ∪ effective-pinned`, `unscheduled = rest`); use it in status bar, dashboard, and `BulkResultsDialog`; clamp/assert non-negative. **Effort** S · **Related** ST-UI-001, ST-UI-003 · **Status** Open

<a id="st-ui-003"></a>
### ST-UI-003 — Dashboard room metrics always zero
- **Severity** High · **OBSERVED** · `ui/dashboard.py:441`, `schedule_analytics.py:45-62`, `models.py:116-120`
- **Evidence** On a hand-built 4-class/2-room schedule with a real R1→R2 switch: the dashboard "Oda Değişimi" bar reads **0.0** while the true room-switch penalty is 0.8; `ScheduleAnalytics` room_metrics via the dashboard path report `total_rooms=0, avg_utilization=0.0` vs correct `total_rooms=2, avg_utilization=0.10`. The analytics code reads a `'room'` key that no class dict has (the field is `placed_classroom`). The quality gauge itself is unaffected (room_metrics aren't used in the global score), but the room breakdown bar and room-utilization analytics are silently zero. Probe: `scenarios/probe_dashboard_room_key_bug.py`. Evidence: `evidence/dashboard-room-switching-zero.png`.
- **Recommendation** Read `placed_classroom` / `classroom_of(cls)`; add a test asserting non-zero room metrics on a known schedule. **Effort** S · **Related** ST-UI-002 · **Status** Open

<a id="st-ui-004"></a>
### ST-UI-004 — Timetable grid mouse-only, invisible to assistive tech
- **Severity** High · **OBSERVED** · `ui/renderer.py` (no keyboard/focus code); 0 accessibility-API hits package-wide
- **Evidence** grep confirms zero `keyPressEvent`/`focusPolicy` in `renderer.py` and zero `setAccessibleName`/`QAccessible` usage anywhere in `scheduler_app/`. Lessons are `QGraphicsRectItem`s with custom-painted text: no focus, no arrow-key navigation, no screen-reader representation. Selection, drag-move, context menus and empty-cell interactions all require a mouse; the shortcuts (Delete, Ctrl+E, Ctrl+C) act only on a mouse-established selection.
- **User impact** For a tool aimed at school/public-sector administrators (accessibility obligations), the core surface is unusable without a mouse and entirely silent to screen readers.
- **Recommendation** Add cell-cursor keyboard navigation (arrows move focus, Enter=context menu, F2=edit, cut/paste to move), a visible focus ring, and accessible names — or an equivalent accessible table view. **Effort** L · **Related** ST-UI-005 · **Status** Open

<a id="st-ui-005"></a>
### ST-UI-005 — In-cell text fails WCAG AA contrast
- **Severity** High · **OBSERVED** · `ui/renderer.py:387-419`, `ui/app.py:2954-2957`
- **Evidence** Computed from the exact painted hexes: room/location `#16A34A` on the four common cell backgrounds = **1.55–2.14:1** (AA needs 4.5); "SABİT" pinned badge `#DC2626` 7 pt bold = 2.27–2.45:1; class code `#1D4ED8` 8 pt = 3.40:1; lecturer `#475569` 8 pt = 3.84:1; only the class name passes (7.42:1). Open-slots room label `#9CA3AF` ≈ 2.5:1. The room assignment — critical scheduling info — is the least legible element in the cell.
- **Recommendation** Darken in-cell secondary text; render badges as filled pills with white text; raise the open-slots room label to ≥ 4.5:1. **Effort** S · **Related** ST-UI-004, ST-UI-006 · **Status** Open

<a id="st-arch-001"></a>
### ST-ARCH-001 — Zero automated tests
- **Category** Architecture · **Severity** Critical · **Confidence** High · **OBSERVED**
- **Evidence** No test files, no `tests/` dir, no pytest/tox/pyproject config anywhere outside `.venv-audit`. `requirements-dev.txt` pins `pytest>=7.0` that nothing consumes. CI (`ci.yml`) runs no test runner and its trigger (`branches: [master]`) never fires (ST-ARCH-002). Conceptual coverage: **0%**.
- **User/technical impact** Every Critical/High finding in this register (import crash, `'nan'` merge, optimizer hard-violations, key orphaning) is exactly the class of regression a minimal test suite would have caught. There is no safety net for any change.
- **Recommendation** Stand up pytest; first targets are the headless core (oracle-based scheduler invariants, storage roundtrip/corruption, import round-trip of the generated template, export smoke on all three formats). See [14-implementation-roadmap.md](14-implementation-roadmap.md) Phase 7. **Effort** L · **Related** every finding · **Status** Open

<a id="st-sec-001"></a>
### ST-SEC-001 — CI auto-publishes a public `latest` release on every push to main
- **Category** Security · **Severity** High · **Confidence** High · **OBSERVED**
- **Component** `.github/workflows/build-release.yml:27-31, 159-170`
- **Evidence** The workflow triggers on push to `main` and creates a non-prerelease GitHub Release marked `latest`. Live check: `releases/latest` = `v1.0.0-build.10` (2026-06-19), one asset `Dersis_Setup_v1.0.0.exe` (113.4 MiB), **no `.sha256`**, no macOS asset. The README download button points end-users at `releases/latest`.
- **User impact** Every developer push becomes an unvetted "latest" download for real users; a broken or malicious mid-development commit ships immediately, without a checksum to verify.
- **Recommendation** Gate releases behind version tags only; mark auto-builds as prereleases/artifacts; publish a checksum and sign the installer. **Effort** M · **Related** ST-SEC-004, ST-SEC-006, ST-ARCH-002 · **Status** Open

<a id="st-sec-002"></a>
### ST-SEC-002 — At-rest encryption is obfuscation only
- **Severity** Medium · **OBSERVED** · `storage/storage.py`
- **Evidence** The AES-256-GCM master key is a genuinely random 32 bytes (two installs differ) — but it is stored as a plaintext file `~/Documents/Dersis/keys/key.bin`, in the **same tree** as the ciphertext it protects. Nonces/salts are unique per save (verified: 2 saves → different salt and 12-byte nonce). So the crypto is correct but provides no confidentiality against anyone who can read the folder.
- **User impact** The README frames the app as private/offline; users may assume their `.egu` saves are protected. Against local access (shared PC, backup, cloud-synced Documents) they are not.
- **Recommendation** Either drop the encryption framing (call it integrity-checked storage) or derive the key from a user secret / OS keystore (DPAPI/Keychain). **Effort** L · **Related** ST-DATA-001, ST-SEC-008 · **Status** Open

---

*Remaining Medium/Low findings without a detail block are fully specified in
their subsystem document (linked in each table's section header) and carry the
same fields there. Improvement grouping is in
[13-improvement-opportunities.md](13-improvement-opportunities.md); sequencing in
[14-implementation-roadmap.md](14-implementation-roadmap.md).*
