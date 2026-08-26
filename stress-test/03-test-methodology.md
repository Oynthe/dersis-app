# 03 — Test Methodology

Part of the [DERSİS stress-test audit](00-README.md). Explains **how** every result
in this audit was obtained, so that any finding can be independently reproduced.

---

## 1. Audit environment

| Item | Value |
|---|---|
| Repository | `C:\dev\dersis-app`, branch `main`, commit `365b24b` ("Merge pull request #6 … macOS packaging"), working tree clean at audit start |
| App version | 1.0.0 (`VERSION` file) |
| OS | Windows 11 Pro 10.0.26200 |
| Python | 3.12.10 (installed user-scope via winget for this audit; the machine had no Python) |
| Virtualenv | `.venv-audit/` at repo root (git-ignored), created from `requirements.txt` |
| Key libraries | PyQt6 6.x, ortools (CP-SAT available: `HAS_ORTOOLS=True`), pandas, openpyxl, reportlab, cryptography, deepdiff |
| Qt platform | Native `windows` platform for rendering evidence; `offscreen` only for logic-only probes |

No production code under `scheduler_app/` was modified by this audit. The only
repository changes are the `stress-test/` tree, one line in `.gitignore`
(`.venv-audit/`), and nothing else.

## 2. Storage sandboxing (mandatory for every test)

`scheduler_app/storage/storage.py:55` binds the data root to
`~/Documents/Dersis` **at import time** with no environment override. Every
probe therefore sets `HOME` and `USERPROFILE` to a fresh temporary directory
*before* importing anything from `scheduler_app`. This was verified to fully
redirect settings, saves, logs, learning data, backups and keys. The real
`~/Documents` was never touched (and contained no Dersis data on this machine).

The absence of a storage-path override is itself recorded as a testability
finding (see [12-findings-register.md](12-findings-register.md)).

## 3. Headless techniques

- **Core engine** — `scheduler_app.core.*` and `data_io.*` are importable without
  a `QApplication`. The production pipeline is exercised via
  `SchedulingWorkflow(state, get_weights).reschedule(...)` / `apply_reschedule(...)`,
  `ScheduleOptimizer(...)`, and `CPSATScheduler(...)` directly.
- **GUI** — the full `SchedulerApp` main window is constructed on the native Qt
  platform but **never shown**; `widget.grab()` renders hidden widgets with real
  fonts. This produces the PNG evidence in `evidence/` without any window
  appearing on screen. (`QT_QPA_PLATFORM=offscreen` works too but renders
  placeholder glyphs — used only for logic probes.)
- **Dialog/modal safety** — dialogs are constructed and grabbed, never `exec()`ed;
  `QFileDialog`/`QMessageBox` are monkeypatched in probes that drive handlers
  which would otherwise block.
- **First-run gates** — the language gate is skipped by pre-seeding
  `set_language("tr")`; the 33-step tutorial is suppressed by patching
  `FirstRunController.start` where clean captures are needed.

## 4. Datasets

All synthetic datasets come from one deterministic generator,
[`tests/_fixtures/dataset_gen.py`](tests/_fixtures/dataset_gen.py)
(`make_state(...)` / `make_preset(...)`, seeded `random.Random`). Scale presets:

| Preset | Classes | Rooms | Lecturers | Grid |
|---|---|---|---|---|
| tiny | 5 | 2 | 3 | 5×8 |
| small | 25 | 4 | 6 | 5×8 |
| normal | 80 | 8 | 15 | 5×8 |
| large | 250 | 16 | 40 | 5×8 |
| very_large | 600 | 30 | 90 | 5×8 |
| pathological | 1200 | 40 | 150 | 5×8 |

A `density` parameter (0–1) layers constraint pressure: lecturer availability
windows, allowed/excluded days & times, required rooms, pins, capacity limits.
Feasibility is deliberately *not* guaranteed at high density.

## 5. Test categories applied

Normal, boundary, invalid/malformed, rapid interaction, state conflict,
persistence, scale, unusual sequence, recovery, concurrency (two-process),
duplicate actions, partial failure, empty state, dirty state — per the audit
specification. Each executed scenario records purpose, input, steps, expected
vs actual, verdict, timing, evidence and severity; consolidated per subsystem
in documents [04](04-functional-stress-test.md)–[08](08-error-edge-case-audit.md).

## 6. Evidence discipline

Every claim is labeled:

- **OBSERVED** — demonstrated by running code in this environment (error text,
  measured timing, screenshot, file artifact), or directly visible in code with
  an unambiguous trace.
- **INFERRED** — a conclusion from reading code that was not (or could not be)
  demonstrated at runtime here.

Measurements use `time.perf_counter()`; memory uses `tracemalloc` where noted.
Long solver runs are bounded with explicit limits/timeouts, and any such
constraint is recorded alongside the measurement. Raw outputs (CSV, PNG, logs)
live in [`evidence/`](evidence/), reproduction scripts in [`tests/`](tests/)
and [`scenarios/`](scenarios/) — each script is standalone and sets up its own
sandbox.

## 7. Severity and identifiers

Severity definitions (Critical / High / Medium / Low) are given in
[00-README.md](00-README.md#severity-system) and reflect user consequence, not
discovery difficulty. Findings carry stable IDs (`ST-FUNC-*`, `ST-PERF-*`,
`ST-SCHED-*`, `ST-DATA-*`, `ST-UI-*`, `ST-ARCH-*`, `ST-SEC-*`) defined in the
canonical [findings register](12-findings-register.md).

## 8. What was NOT tested, and why

- **Real packaged builds** (`build_embed.bat`, Nuitka, PyInstaller/.dmg): they
  require network downloads and long build times; build *logic* was audited
  statically. → NOT TESTABLE here (documented in [10-code-architecture-audit.md](10-code-architecture-audit.md)).
- **True multi-user/institutional concurrency**: the app is single-user by
  design; concurrency testing was limited to multiple local processes.
- **macOS behavior**: audit ran on Windows only; macOS packaging reviewed statically.
- **Real Windows-Store/installer upgrade paths**: static review only.
- Everything else in the system map has an explicit TESTED / PARTIALLY TESTED /
  NOT TESTABLE / NOT APPLICABLE status in [01-system-map.md](01-system-map.md).
